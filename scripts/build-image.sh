#!/usr/bin/env bash
# scripts/build-image.sh — produce a blazen_os SD image as .img or .qcow2.
#
# The image is built by appending a `stage-blazen/` to upstream pi-gen.
# This script is a thin orchestrator over Docker + pi-gen. See
# docs/03-SOFTWARE-STACK.md for the pipeline overview.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD_DIR="$REPO_ROOT/build"
PI_GEN_SHA="${BLAZEN_PI_GEN_SHA:-2026-04-13-raspios-trixie-arm64}"   # pinned to latest stable trixie release tag
PI_GEN_REPO="https://github.com/RPi-Distro/pi-gen.git"

FORMAT=raw
OUT=""
DEV_IMAGE="${BLAZEN_DEV_IMAGE:-0}"
STAGE_ONLY=0

usage() {
  cat <<USAGE
Usage: $0 --format <raw|qcow2> --out <path> [--dev]

Builds the blazen_os SD image. Requires docker on the host.

Options:
  --format       raw | qcow2 (default: raw)
  --out          output path (.img or .qcow2)
  --dev          DEV flavour: in addition to the default (login 'blazen'
                 user + SSH on), bakes a dev SSH key and a serial-console
                 password so the M1 boot test can SSH in. Release images
                 (the default) are SSH-on but pubkey-only/fail-closed — no
                 key or password ships. See docs/06-SSH-BOOTSTRAP.md §6.
  --stage-only   stop after stage_payload (no Docker): ~1-minute dry-run to
                 inspect build/pi-gen/stage-blazen/.../blazen-staging/
  --pi-gen-sha   override BLAZEN_PI_GEN_SHA env (defaults to the pinned tag)

Env:
  BLAZEN_DEV_IMAGE=1        same as --dev
  BLAZEN_DEV_SSH_PUBKEY     path to a public key to bake into the dev image
                            (default: build/dev-ssh/id_ed25519.pub, generated)
  BLAZEN_BAKE_MODELS=1      bake models/ + wheelhouse/ (full offline stack)
  BLAZEN_BAKE_MODEL_DIRS    space-separated models/ subdirs to bake
                            (default: "asr embeddings llm tts wake" —
                            asr-remote, paul's 2.9 GB large-v3, is excluded)
  BLAZEN_HOSTNAME           target hostname (default: blazen)
USAGE
}

while [ $# -gt 0 ]; do
  case "$1" in
    --format) FORMAT="$2"; shift 2 ;;
    --out)    OUT="$2";    shift 2 ;;
    --dev)    DEV_IMAGE=1; shift 1 ;;
    --stage-only) STAGE_ONLY=1; shift 1 ;;
    --pi-gen-sha) PI_GEN_SHA="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 1 ;;
  esac
done

[ -z "$OUT" ] && { usage; exit 1; }
case "$FORMAT" in raw|qcow2) ;; *) usage; exit 1 ;; esac

log()  { printf '\033[1;34m[build-image]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[build-image]\033[0m %s\n' "$*" >&2; }

ensure_dev_ssh_key() {
  # Resolve (or generate) the public key to bake into the dev image.
  # Prints the chosen .pub path on stdout. Keys live under build/ which
  # is gitignored, so a private key never lands in the repo.
  local pub="${BLAZEN_DEV_SSH_PUBKEY:-}"
  if [ -n "$pub" ]; then
    [ -f "$pub" ] || { warn "BLAZEN_DEV_SSH_PUBKEY=$pub not found"; return 1; }
    echo "$pub"; return 0
  fi
  local dir="$BUILD_DIR/dev-ssh"
  mkdir -p "$dir"
  if [ ! -f "$dir/id_ed25519" ]; then
    # Diagnostics go to stderr: stdout is the captured return value.
    log "Generating dev SSH keypair at $dir/id_ed25519 (gitignored)" >&2
    ssh-keygen -t ed25519 -N '' -C 'blazen-dev' -f "$dir/id_ed25519" >/dev/null
  fi
  echo "$dir/id_ed25519.pub"
}

ensure_pi_gen() {
  mkdir -p "$BUILD_DIR"
  if [ ! -d "$BUILD_DIR/pi-gen" ]; then
    log "Cloning pi-gen (branch/tag: $PI_GEN_SHA)"
    # --depth 1 needs an explicit ref or default branch; we pass the tag.
    git clone --depth 1 --branch "$PI_GEN_SHA" "$PI_GEN_REPO" "$BUILD_DIR/pi-gen" \
      || git clone "$PI_GEN_REPO" "$BUILD_DIR/pi-gen"
  fi
  ( cd "$BUILD_DIR/pi-gen" && git fetch --all --tags && git checkout "$PI_GEN_SHA" )
}

inject_stage() {
  log "Injecting stage-blazen/ into pi-gen"
  rm -rf "$BUILD_DIR/pi-gen/stage-blazen"
  cp -R "$REPO_ROOT/rpi5/stage-blazen" "$BUILD_DIR/pi-gen/stage-blazen"
  # SSH is on by default in both flavours (docs/06-SSH-BOOTSTRAP.md): enable
  # the openssh-server unit at the pi-gen level too. The chroot script makes
  # 'blazen' a login user either way; release is pubkey-only/fail-closed
  # (no baked key or password), dev additionally bakes the dev key + password.
  local enable_ssh=1
  cat > "$BUILD_DIR/pi-gen/config" <<EOF
IMG_NAME=${BLAZEN_IMAGE_NAME:-blazen_os}
TARGET_HOSTNAME=${BLAZEN_HOSTNAME:-blazen}
ENABLE_SSH=${enable_ssh}
RELEASE=trixie
PI_GEN_RELEASE='trixie'
STAGE_LIST='stage0 stage1 stage2 stage-blazen'
EOF
}

run_pi_gen() {
  log "Running pi-gen (this takes a while)"
  # pi-gen's build-docker.sh can exit non-zero during container teardown even
  # after the image was built successfully (it prints "Done!" then trips on a
  # cleanup step). Under `set -e` that abort happens AT the subshell — before
  # `|| rc=$?` runs — so bracket the call with an explicit `set +e`/`set -e`
  # (the reliable form). Only fail if pi-gen did NOT finish — the authoritative
  # signal is the "Build finished" line pi-gen writes to deploy/build.log.
  set +e
  ( cd "$BUILD_DIR/pi-gen" && CLEAN=1 ./build-docker.sh )
  local rc=$?
  set -e
  if [ "$rc" -ne 0 ]; then
    if grep -q "Build finished" "$BUILD_DIR/pi-gen/deploy/build.log" 2>/dev/null; then
      warn "build-docker.sh exited $rc after 'Build finished' (cleanup quirk); continuing to post_convert"
    else
      echo "pi-gen failed (rc=$rc): no 'Build finished' in deploy/build.log" >&2
      exit "$rc"
    fi
  fi
  return 0
}

post_convert() {
  local deploy="$BUILD_DIR/pi-gen/deploy"
  local img archive
  # pi-gen's canonical output is a compressed .zip (or .img.xz); a raw
  # .img from a PREVIOUS build can linger in deploy/ and must not shadow
  # this run's artefact. So prefer the freshest archive: drop stale raw
  # images, then extract the newest archive. Fall back to a raw .img only
  # when no archive exists at all.
  archive=$(ls -t "$deploy"/*.zip "$deploy"/*.img.xz 2>/dev/null | head -n1)
  if [ -n "$archive" ]; then
    log "Extracting freshest archive: $archive"
    rm -f "$deploy"/*.img
    case "$archive" in
      *.zip)    unzip -o "$archive" -d "$deploy" >/dev/null ;;
      *.img.xz) xz -d -k -f "$archive" ;;
    esac
  fi
  img=$(ls -t "$deploy"/*.img 2>/dev/null | grep -v '\.xz$' | head -n1)
  [ -z "$img" ] && { echo "No image artefact (.img/.zip/.img.xz) in $deploy"; exit 1; }
  mkdir -p "$(dirname "$OUT")"
  case "$FORMAT" in
    raw)   cp -f "$img" "$OUT" ;;
    qcow2) qemu-img convert -f raw -O qcow2 "$img" "$OUT" ;;
  esac
  log "Wrote $OUT ($(du -h "$OUT" | cut -f1))"
}

stage_payload() {
  # Bundle the Python sources, cross-compiled Rust binaries, and YAML
  # configs into 00-install/files/var/lib/blazen-staging/* so pi-gen's
  # SUBSTAGE rsync drops them into the rootfs at /var/lib/blazen-staging/
  # where 01-run-chroot.sh reads them.
  #
  # IMPORTANT: pi-gen rsyncs files/ at the SUBSTAGE level only. Putting
  # files at stage-blazen/files/ (stage level) does NOTHING. Both the
  # payload AND the systemd units have to live under
  # stage-blazen/00-install/files/.
  #
  # We use /var/lib/blazen-staging/ (not /tmp/) because pi-gen's chroot
  # mounts a tmpfs on /tmp inside the chroot, wiping our payload.
  local stage_root="$1"          # $BUILD_DIR/pi-gen/stage-blazen
  local out="$stage_root/00-install/files/var/lib/blazen-staging"
  # Cleanup any leftovers from older layouts.
  rm -rf "$stage_root/00-install/files/tmp" \
         "$stage_root/files" \
         "$out"
  mkdir -p "$out/blazen-src" "$out/blazen-rust" "$out/blazen-configs/intents" \
           "$out/blazen-configs/vm" "$out/blazen-configs/ontology" \
           "$out/blazen-configs/prompts" "$out/blazen-configs/site"
  cp -R "$REPO_ROOT/rpi5/src/blazend"       "$out/blazen-src/"
  cp -R "$REPO_ROOT/configs/"*.yaml         "$out/blazen-configs/"
  cp -R "$REPO_ROOT/configs/intents/"*.yaml "$out/blazen-configs/intents/"
  cp -R "$REPO_ROOT/configs/vm/"*.yaml      "$out/blazen-configs/vm/"
  cp -R "$REPO_ROOT/configs/ontology/"*.json "$out/blazen-configs/ontology/"
  cp -R "$REPO_ROOT/configs/prompts/"*.json  "$out/blazen-configs/prompts/"
  # Site-DIVERGENT mesh.yaml for the appliance: strip paul's llm resource
  # (Decision 2026-08-09 — the Pi never routes to paul's Ollama). The chroot
  # installs this to /etc/blazen/mesh.yaml; canonical source stays configs/.
  "$REPO_ROOT/.venv/bin/python" - "$REPO_ROOT/configs/mesh.yaml" \
      "$out/blazen-configs/site/mesh.yaml" <<'PY'
import sys
import yaml
src, dst = sys.argv[1], sys.argv[2]
with open(src) as f:
    mesh = yaml.safe_load(f)
mesh["nodes"]["paul"]["resources"].pop("llm", None)
with open(dst, "w") as f:
    f.write("# GENERATED site variant of configs/mesh.yaml for the Pi appliance.\n"
            "# Decision 2026-08-09: paul's llm resource removed — Jessica never\n"
            "# routes commands off-node. Canonical source: configs/mesh.yaml.\n")
    yaml.safe_dump(mesh, f, sort_keys=False, allow_unicode=True)
PY
  # Local pure-python domain wheels — ALWAYS staged (tiny, arch-independent).
  # jessica-linux carries the `jessica` console script the fabric-snapshot and
  # pull-catalog units ExecStart; the rest are its domain deps.
  rm -rf "$out/blazen-local-wheels"
  "$REPO_ROOT/.venv/bin/pip" wheel --no-deps -q -w "$out/blazen-local-wheels" \
    "$REPO_ROOT/linux/agent" \
    "$REPO_ROOT/domains/audiobook-catalog" \
    "$REPO_ROOT/domains/mesh-registry" \
    "$REPO_ROOT/domains/mesh-llm" \
    "$REPO_ROOT/domains/context-sync"
  # Rust binaries: prefer the cross-compiled aarch64 release artefacts.
  # Appliance units build in the rpi5/ project workspace; the shared-core
  # blazend-fabric builds in the repo-root domains/ workspace.
  local app_rust="$REPO_ROOT/rpi5/crates/target/aarch64-unknown-linux-gnu/release"
  local core_rust="$REPO_ROOT/domains/target/aarch64-unknown-linux-gnu/release"
  if [ ! -d "$app_rust" ]; then
    warn "Appliance Rust aarch64 artefacts not found at $app_rust; run 'make rust-aarch64' first"
    return 1
  fi
  for bin in blazend-audio-in blazend-audio-out blazend-wake blazend-nlu blazend-tts blazend-health blazend-player; do
    install -m 0755 "$app_rust/$bin" "$out/blazen-rust/$bin"
  done
  install -m 0755 "$core_rust/blazend-fabric" "$out/blazen-rust/blazend-fabric"

  # Full offline stack: bake model weights + a pre-built aarch64 ML wheelhouse
  # into the image so first boot needs no network. Opt-in via BLAZEN_BAKE_MODELS=1
  # (default off keeps the lean <1 GB lazy-download image). Inputs come from the
  # host repo: models/ (via `make models`) and wheelhouse/ (built on a native
  # aarch64 host with `pip wheel "rpi5[runtime]" -w wheelhouse`).
  if [ "${BLAZEN_BAKE_MODELS:-0}" = "1" ]; then
    # Allowlist, not `cp -R models/*`: the host models/ tree also carries
    # paul-only weights (asr-remote = 2.9 GB large-v3) that must never ship.
    local model_dirs="${BLAZEN_BAKE_MODEL_DIRS:-asr embeddings llm tts wake}"
    local d
    mkdir -p "$out/blazen-models"
    for d in $model_dirs; do
      if [ ! -d "$REPO_ROOT/models/$d" ]; then
        warn "model dir missing: models/$d — run 'make models' first"
        return 1
      fi
      cp -R "$REPO_ROOT/models/$d" "$out/blazen-models/$d"
    done
    log "baking model weights into image ($model_dirs): $(du -sh "$out/blazen-models" | cut -f1)"
    if [ -d "$REPO_ROOT/wheelhouse" ] && [ -n "$(ls -A "$REPO_ROOT/wheelhouse" 2>/dev/null)" ]; then
      log "baking ML wheelhouse into image: $(du -sh "$REPO_ROOT/wheelhouse" | cut -f1)"
      mkdir -p "$out/blazen-wheels"
      cp -R "$REPO_ROOT/wheelhouse/"* "$out/blazen-wheels/"
    else
      warn "BLAZEN_BAKE_MODELS=1 but $REPO_ROOT/wheelhouse missing — ML deps will lazy-install on first boot"
    fi
  fi

  # Dev flavour: drop the marker + SSH key that 01-run-chroot.sh keys off.
  # These live only in the staging payload and are deleted by the chroot
  # script (rm -rf $STAGE), so they never ship in the rootfs.
  if [ "$DEV_IMAGE" = "1" ]; then
    local pub; pub="$(ensure_dev_ssh_key)" || return 1
    touch "$out/DEV_IMAGE"
    cp "$pub" "$out/dev_authorized_keys"
    log "DEV image: baked SSH key $pub (login user 'blazen', ssh enabled)"
  fi

  log "payload staged at $out"
}

main() {
  ensure_pi_gen
  inject_stage
  stage_payload "$BUILD_DIR/pi-gen/stage-blazen"
  if [ "$STAGE_ONLY" = "1" ]; then
    log "stage-only: payload at $BUILD_DIR/pi-gen/stage-blazen/00-install/files/var/lib/blazen-staging (no image built)"
    exit 0
  fi
  run_pi_gen
  post_convert
}

main "$@"

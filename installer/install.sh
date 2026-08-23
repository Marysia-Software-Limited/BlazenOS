#!/usr/bin/env bash
# Jessica common Linux installer — Arch / Debian / Fedora (macOS seam stubbed).
#
# Installs the FULL voice pipeline (audio-in → wake/ASR → NLU → brain → TTS →
# audio-out, orchestrator, health) natively on this machine.
#
#   --mode desktop    (default) systemd USER units, XDG home layout, no root
#                     beyond package installs; audio profile auto-detected at
#                     every start (Jabra SPEAK 410 preferred, ALSA fallback).
#   --mode appliance  system units + blazen service user under /usr/lib/blazen
#                     — the distro-neutral subset of the Pi image recipe.
#   --profile cuda    GPU models: faster-whisper large-v3-turbo on CUDA +
#                     Ollama Bielik 11B (needs the NVIDIA driver; falls back
#                     to cpu with a warning when nvidia-smi is absent).
#   --profile cpu     the appliance model set (whisper-small int8, Bielik 1.5B).
#   --dry-run         print every action, touch nothing.
#   --yes             fully non-interactive (QEMU installer testing).
#   --uninstall       remove everything recorded in the install manifest.
#
# Idempotent: re-running converges. See docs/20-LINUX-INSTALLER.md.
set -euo pipefail

INSTALLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$INSTALLER_DIR/.." && pwd)"
# shellcheck source=lib/common.sh
. "$INSTALLER_DIR/lib/common.sh"

MODE=desktop PROFILE=cuda DRY_RUN=0 ASSUME_YES="" SKIP_PACKAGES=0
UNINSTALL=0 WITH_XTTS=0 NODE_NAME="$(hostname 2>/dev/null || uname -n)"
while [ $# -gt 0 ]; do
  case "$1" in
    --mode) MODE="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --node) NODE_NAME="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --yes) ASSUME_YES=1; shift ;;
    --skip-packages) SKIP_PACKAGES=1; shift ;;
    --with-xtts) WITH_XTTS=1; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) die "unknown flag: $1" ;;
  esac
done
[ "$MODE" = desktop ] || [ "$MODE" = appliance ] || die "--mode desktop|appliance"
[ "$PROFILE" = cpu ] || [ "$PROFILE" = cuda ] || die "--profile cpu|cuda"

# ---- path model (THE platform abstraction) ---------------------------------
# shellcheck disable=SC2034  # consumed by render_template/pkg_install in lib/
if [ "$MODE" = desktop ]; then
  LIB_DIR="$HOME/.local/share/blazen"
  BIN_DIR="$LIB_DIR/bin"
  VENV="$LIB_DIR/venv"
  STATE_DIR="$LIB_DIR"
  MODELS_DIR="$STATE_DIR/models"
  RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/blazen-$(id -u)}/blazen"
  DEFAULTS_DIR="$LIB_DIR/defaults"
  SITE_DIR="$HOME/.config/blazen"
  UNIT_DIR="$HOME/.config/systemd/user"
  SYSCTL=(systemctl --user)
  SERVICE_USER_BLOCK=""
  CPU_PINNING=""
  HARDENING_BLOCK="NoNewPrivileges=yes"
  TARGET_WANTEDBY="default.target"
  SYSTEMCTL_ENV="Environment=BLAZEN_SYSTEMCTL=systemctl --user"
  AUDIO_GROUP=""
else
  LIB_DIR="/usr/lib/blazen"
  BIN_DIR="$LIB_DIR/bin"
  VENV="$LIB_DIR/.venv"
  STATE_DIR="/var/lib/blazen"
  MODELS_DIR="$STATE_DIR/models"
  RUNTIME_DIR="/run/blazen"
  DEFAULTS_DIR="/usr/share/blazen/defaults"
  SITE_DIR="/etc/blazen"
  UNIT_DIR="/etc/systemd/system"
  SYSCTL=(sudo systemctl)
  SERVICE_USER_BLOCK="User=blazen\nGroup=blazen"
  CPU_PINNING=""
  HARDENING_BLOCK="NoNewPrivileges=yes\nProtectSystem=strict\nReadWritePaths=$RUNTIME_DIR $STATE_DIR\nProtectHome=yes"
  TARGET_WANTEDBY="multi-user.target"
  SYSTEMCTL_ENV="Environment=BLAZEN_SYSTEMCTL=sudo -n systemctl"
  AUDIO_GROUP="SupplementaryGroups=audio"
fi
CONFIG_ROOT="$DEFAULTS_DIR:$SITE_DIR"
ENV_FILE="$SITE_DIR/blazen.env"
AUDIO_ENV_FILE="$RUNTIME_DIR/audio.env"
MANIFEST="${BLAZEN_MANIFEST:-$LIB_DIR/install-manifest}"

# ---- uninstall --------------------------------------------------------------
if [ "$UNINSTALL" = 1 ]; then
  [ -f "$MANIFEST" ] || die "no install manifest at $MANIFEST"
  "${SYSCTL[@]}" disable --now blazend.target blazend-pull-catalog.timer 2>/dev/null || true
  while IFS= read -r p; do run rm -f "$p"; done < "$MANIFEST"
  run rm -f "$MANIFEST"
  "${SYSCTL[@]}" daemon-reload || true
  log "uninstalled (data/models under $STATE_DIR left in place; remove by hand)"
  exit 0
fi

log "mode=$MODE profile=$PROFILE node=$NODE_NAME repo=$REPO_ROOT"
detect_distro

# ---- 1. system packages -----------------------------------------------------
if [ "$SKIP_PACKAGES" != 1 ]; then
  pkg_install "${PKGS_BASE[@]}"
  [ "$MODE" = desktop ] && pkg_install "${PKGS_AUDIO[@]}"
  pkg_install_rust
fi

# ---- 2. GPU preflight -------------------------------------------------------
if [ "$PROFILE" = cuda ] && ! command -v nvidia-smi >/dev/null 2>&1; then
  warn "nvidia-smi not found — downgrading to --profile cpu"
  PROFILE=cpu
fi

# ---- 3. Rust binaries -------------------------------------------------------
BINS=(blazend-audio-in blazend-audio-out blazend-wake blazend-nlu blazend-tts
      blazend-health blazend-player)
run bash -c "cd '$REPO_ROOT/rpi5/crates' && cargo build --release ${BINS[*]/#/-p }"
run bash -c "cd '$REPO_ROOT/domains' && cargo build --release -p blazend-fabric"
run mkdir -p "$BIN_DIR"
for b in "${BINS[@]}"; do
  run install -C -m 0755 "$REPO_ROOT/rpi5/crates/target/release/$b" "$BIN_DIR/$b"
  manifest_add "$BIN_DIR/$b"
done
run install -C -m 0755 "$REPO_ROOT/domains/target/release/blazend-fabric" "$BIN_DIR/blazend-fabric"
manifest_add "$BIN_DIR/blazend-fabric"
run install -C -m 0755 "$INSTALLER_DIR/bin/blazen-audio-env" "$BIN_DIR/blazen-audio-env"
manifest_add "$BIN_DIR/blazen-audio-env"

# ---- 4. Python venv + packages ---------------------------------------------
if [ ! -x "$VENV/bin/python" ]; then run python3 -m venv "$VENV"; fi
run "$VENV/bin/pip" install --upgrade pip wheel
run "$VENV/bin/pip" install "pyyaml>=6.0" "pydantic>=2.9" "numpy>=1.26" "jsonschema>=4.21"
run "$VENV/bin/pip" install faster-whisper onnxruntime piper-tts sounddevice soundfile tokenizers
[ "$PROFILE" = cpu ] && run "$VENV/bin/pip" install llama-cpp-python
[ "$PROFILE" = cuda ] && run "$VENV/bin/pip" install nvidia-cublas-cu12 nvidia-cudnn-cu12
run "$VENV/bin/pip" install \
  "$REPO_ROOT/domains/audiobook-catalog" "$REPO_ROOT/domains/mesh-registry" \
  "$REPO_ROOT/domains/mesh-llm" "$REPO_ROOT/domains/context-sync" \
  "$REPO_ROOT/linux/agent"
run rsync -a --delete "$REPO_ROOT/rpi5/src/blazend/" "$LIB_DIR/blazend/"

# ---- 5. configs -------------------------------------------------------------
run mkdir -p "$DEFAULTS_DIR" "$SITE_DIR/intents" "$SITE_DIR/ontology" \
  "$SITE_DIR/prompts" "$SITE_DIR/overrides"
run bash -c "cp '$REPO_ROOT/configs/'*.yaml '$DEFAULTS_DIR/'"
run bash -c "cp '$REPO_ROOT/configs/intents/'*.yaml '$SITE_DIR/intents/'"
run bash -c "cp '$REPO_ROOT/configs/ontology/'*.json '$SITE_DIR/ontology/' 2>/dev/null || true"
run bash -c "cp '$REPO_ROOT/configs/prompts/'*.json '$SITE_DIR/prompts/' 2>/dev/null || true"
render_template "$INSTALLER_DIR/templates/site/mesh.yaml.in" "$SITE_DIR/mesh.yaml"
run install -C -m 0644 "$INSTALLER_DIR/templates/alsa/jabra-asound.conf" "$SITE_DIR/jabra-asound.conf"
manifest_add "$SITE_DIR/jabra-asound.conf"
if [ "$PROFILE" = cuda ]; then
  run install -C -m 0644 "$INSTALLER_DIR/templates/site/asr-cuda.yaml" "$SITE_DIR/asr.yaml"
  run install -C -m 0644 "$INSTALLER_DIR/templates/site/llm-cuda.yaml" "$SITE_DIR/llm.yaml"
  manifest_add "$SITE_DIR/asr.yaml"; manifest_add "$SITE_DIR/llm.yaml"
fi
if [ ! -f "$SITE_DIR/secrets.env" ] && [ "$DRY_RUN" != 1 ]; then
  printf '# Cloud opt-in keys (OPENAI_API_KEY=..., GEMINI_API_KEY=...).\n# Never committed.\n' > "$SITE_DIR/secrets.env"
  chmod 600 "$SITE_DIR/secrets.env"
fi
# blazen.env: computed once at install (ORT dylib discovery + CUDA lib path).
if [ "$DRY_RUN" != 1 ]; then
  {
    printf 'BLAZEN_CONFIG_ROOT=%s\n' "$CONFIG_ROOT"
    ort="$(find "$VENV" -name 'libonnxruntime.so.1*' 2>/dev/null | head -1 || true)"
    [ -n "$ort" ] && printf 'ORT_DYLIB_PATH=%s\n' "$ort"
    if [ "$PROFILE" = cuda ]; then
      cudnn="$(find "$VENV" -path '*nvidia/cudnn/lib' -type d 2>/dev/null | head -1 || true)"
      cublas="$(find "$VENV" -path '*nvidia/cublas/lib' -type d 2>/dev/null | head -1 || true)"
      [ -n "$cudnn" ] && printf 'LD_LIBRARY_PATH=%s:%s\n' "$cudnn" "$cublas"
    fi
    # Jessica's rich voice off the local GPU XTTS server (scripts/blazen-xtts.service).
    if [ "$WITH_XTTS" = 1 ]; then printf 'BLAZEN_TTS_XTTS_URL=http://127.0.0.1:8091/synthesize\n'; fi
  } > "$ENV_FILE"
  manifest_add "$ENV_FILE"
else
  log "[dry-run] would write $ENV_FILE"
fi

# ---- 6. models --------------------------------------------------------------
run mkdir -p "$STATE_DIR"/{data,voice-cache,wake-negatives,voice-training,music,semantic-index,audiobooks,fabric,media} "$MODELS_DIR"
model_cfgs=(--config "$REPO_ROOT/configs/tts.yaml" --config "$REPO_ROOT/configs/wake-word.yaml" --config "$REPO_ROOT/configs/embeddings.yaml" --config "$REPO_ROOT/configs/asr.yaml")
[ "$PROFILE" = cpu ] && model_cfgs+=(--config "$REPO_ROOT/configs/llm.yaml")
run "$VENV/bin/python" "$REPO_ROOT/scripts/install_models.py" "${model_cfgs[@]}"
run bash -c "rsync -a '$REPO_ROOT/models/' '$MODELS_DIR/'"
# install_models.py only fetches direct URLs; the CUDA ASR model comes from
# HuggingFace (CT2 conversion) and is pulled here when the profile needs it.
if [ "$PROFILE" = cuda ] && [ ! -f "$MODELS_DIR/asr/large-v3-turbo/model.bin" ]; then
  run "$VENV/bin/python" -c "from huggingface_hub import snapshot_download; snapshot_download('mobiuslabsgmbh/faster-whisper-large-v3-turbo', local_dir='$MODELS_DIR/asr/large-v3-turbo')"
fi

# ---- 7. Ollama (cuda profile) ----------------------------------------------
if [ "$PROFILE" = cuda ]; then
  pkg_install_ollama
  run sudo systemctl enable --now ollama 2>/dev/null || warn "enable ollama manually"
  run ollama pull SpeakLeash/bielik-11b-v2.3-instruct:Q8_0 || warn "ollama pull failed — pull the model manually"
fi

# ---- 8. audio (desktop) -----------------------------------------------------
if [ "$MODE" = desktop ]; then
  run install -C -m 0644 -D "$INSTALLER_DIR/templates/wireplumber/51-blazen-jabra.conf" \
    "$HOME/.config/wireplumber/wireplumber.conf.d/51-blazen-jabra.conf"
  manifest_add "$HOME/.config/wireplumber/wireplumber.conf.d/51-blazen-jabra.conf"
  run systemctl --user restart wireplumber 2>/dev/null || true
fi

# ---- 9. systemd units -------------------------------------------------------
run mkdir -p "$UNIT_DIR"
for t in "$INSTALLER_DIR"/templates/units/*.in; do
  unit="$(basename "${t%.in}")"
  render_template "$t" "$UNIT_DIR/$unit"
done
run bash -c "${SYSCTL[*]} daemon-reload || true"
if [ "$MODE" = desktop ]; then
  run bash -c "loginctl enable-linger '${USER:-$(id -un)}' || true"
fi
run "${SYSCTL[@]}" enable blazend.target blazend-env.service \
  blazend-audio-in blazend-wake blazend-asr blazend-nlu blazend-brain \
  blazend-tts blazend-health blazend-orchestrator blazend-fabric \
  blazend-fabric-snapshot.service blazend-pull-catalog.timer

log "installed. Start with: ${SYSCTL[*]} start blazend.target"
log "then say: dżesika … (Jabra preferred; plugging one in + restart re-detects)"

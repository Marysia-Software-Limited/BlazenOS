# 06 — SSH bootstrap and break-glass policy

`blazen_os` is voice-first. SSH exists **only** for situations the voice
path can't handle: first boot before the user has trained the system to
their voice, recovery when the voice pipeline is broken, advanced sysadmin.

## 1. First-boot flow (no monitor, no keyboard)

The image's `boot` partition is FAT32 and read by the user on a normal
laptop **once** before the first boot. The build pipeline drops the
following template files into it:

```
/boot/blazen-firstboot/
├── wpa_supplicant.conf       # WiFi credentials (or use an Ethernet cable)
├── userconf.txt              # pi-style "<user>:<bcrypt password hash>"
├── ssh                       # empty file — enables SSH for first boot only
├── authorized_keys           # ssh-ed25519 pubkey (paste yours here)
└── blazen-bootstrap.yaml     # initial system config (hostname, locale, ...)
```

On first boot, `blazend-bootstrap.service`:

1. Reads `/boot/blazen-firstboot/`.
2. Creates the user account, installs the SSH key.
3. Connects to WiFi (or waits for Ethernet).
4. Computes a one-time pairing code and announces it via TTS:
   *"Your pairing code is four-seven-two-nine. Say 'hey blazen, my code is
   four-seven-two-nine' when you are ready."*
5. Once paired, deletes `/boot/blazen-firstboot/` (so the SD card can't be
   yanked and impersonated).
6. Disables SSH again unless `keep_ssh_on: true` is set in the bootstrap
   YAML or the user later says `"hey blazen, enable ssh"`.

If the user has no laptop to prepare the boot files, an **AP-mode
fallback** is available: a fresh image with no `wpa_supplicant.conf`
brings up a `blazen-setup` open WiFi network with a captive-portal
config page. This is disabled by default and toggleable via
`configs/system.yaml: setup_ap.enabled`.

## 2. The voice-vs-SSH split

| Action                                     | Voice-mutable? | SSH-only? |
|--------------------------------------------|:--------------:|:---------:|
| Change volume                              | ✓              | ✓         |
| Switch wake word                           | ✓              | ✓         |
| Switch TTS voice                           | ✓              | ✓         |
| Switch language                            | ✓              | ✓         |
| Switch ASR model size                      | ✓              | ✓         |
| Switch LLM model                           | ✓              | ✓         |
| Change Wi-Fi network                       | ✓ (with confirm) | ✓     |
| Reboot                                     | ✓ (with confirm) | ✓     |
| Shutdown                                   | ✓ (with confirm) | ✓     |
| Re-record wake-word samples                | ✓              | ✓         |
| Install a new tool / plugin                | partial — install voice can request, SSH confirms | ✓ |
| Update the OS image                        |                | ✓         |
| Edit firewall rules                        |                | ✓         |
| Wipe conversation memory                   | ✓              | ✓         |
| Disable telemetry                          | ✓              | ✓         |
| Enable cloud LLM (opt-in)                  | ✓ (loud confirm) | ✓     |
| Factory reset                              | ✓ (two confirms) | ✓     |

The complete list lives in `configs/voice-policy.yaml`. The schema is
documented in [`07-CONFIGURATION.md`](07-CONFIGURATION.md).

## 3. SSH lifecycle states

```
                   "enable ssh"            "disable ssh"
   ┌──────────┐  ───────────────▶  ┌──────────┐
   │ SSH off  │                    │ SSH on   │
   └──────────┘  ◀───────────────  └──────────┘
        ▲                              │
        │   (voice path broken)        │  (idle 30 min)
        │                              │
        │   ┌──────────────────┐       │
        └───│ Recovery mode ON │◀──────┘  triggered by `blazend-health`
            └──────────────────┘
```

- **SSH off** is the steady state in release builds.
- **SSH on** is the steady state in dev builds.
- **Recovery mode** is automatic: triggered by `blazend-health` when any
  of these is true for >60 s:
  - audio-out has been silent through 3 consecutive scheduled checks;
  - audio-in delivered no frames through 3 consecutive scheduled checks;
  - brain failed to produce a token within 3 consecutive utterances;
  - the orchestrator crashed and didn't restart cleanly.

When recovery mode triggers, the LED goes red, a long beep plays, and
SSH is enabled on port 22 with a 30-minute window. After that window the
system attempts a clean restart back to normal mode.

## 4. SSH usage from a developer machine

```bash
# Find the device on the LAN (avahi/mdns)
ssh blazen@blazen.local

# Or by static IP
ssh blazen@192.168.1.42

# Re-run the bootstrap voice pairing if you forget the code
sudo systemctl restart blazend-bootstrap.service
journalctl -u blazend-bootstrap -f
```

A small set of admin scripts live in `/usr/lib/blazen/admin/`:

| Script                          | Purpose                          |
|---------------------------------|----------------------------------|
| `blazen-status`                 | Print every component's state.   |
| `blazen-models list/switch`     | Inspect or change loaded models. |
| `blazen-logs <component>`       | Tail journal for one component.  |
| `blazen-soak start/stop`        | Run/stop a 24h soak test.        |
| `blazen-factory-reset`          | Wipe `/var/lib/blazen/state/`.   |
| `blazen-export-state`           | Dump conversation/intent stats.  |
| `blazen-test-record N`          | Capture an N-second mic sample to `/tmp/`. |

All scripts are idempotent and safe to re-run.

## 5. Why SSH is not "just disable it forever"

A truly voice-first device still needs a hatch for the day:

- The user's voice is lost (laryngitis, throat surgery — yes, this matters).
- A model update accidentally breaks ASR for a particular accent.
- The wake-word model needs retraining and the user can't pair.

Without SSH the only recovery is reflashing the SD card. SSH is the
cheaper failure mode and we keep it small and audited.

## 6. Dev vs release images

The break-glass model above is the **release** contract. During bring-up
(M1–M7) there is no working voice path yet, so the image we boot in QEMU
and flash for hardware bring-up needs a standing login. We express this
as two image **flavours** produced from the same `stage-blazen/` overlay:

| Flavour | Built by | `blazen` account | SSH at boot | Use |
|---------|----------|------------------|-------------|-----|
| **release** (default) | `make pi-image` | system, `nologin` | disabled | shipped images; daily surface is voice-only |
| **dev** | `make vm-image`, `make pi-image-dev` | login: home + `/bin/bash` + passwordless `sudo` | enabled | QEMU boot test, on-hardware bring-up, CI |

The flavour is selected by `scripts/build-image.sh --dev` (or
`BLAZEN_DEV_IMAGE=1`). Concretely the dev build:

- sets `ENABLE_SSH=1` in the pi-gen config;
- drops a `DEV_IMAGE` marker + the dev public key into the staging
  payload, which `stage-blazen/00-install/01-run-chroot.sh` keys off to
  create the login `blazen` user, install
  `~blazen/.ssh/authorized_keys`, set the serial-console fallback
  password `blazen:blazen`, and `systemctl enable ssh`;
- bakes a key from `BLAZEN_DEV_SSH_PUBKEY` if set, otherwise generates
  one at `build/dev-ssh/id_ed25519` (gitignored — a private key never
  enters the repo).

The marker and key live only in `/var/lib/blazen-staging/`, which the
chroot script deletes (`rm -rf "$STAGE"`) at the end of the build, so
neither ships in the rootfs of either flavour.

> A dev image is **not** a relaxed release image — it is a different
> artefact. Release images never carry the dev key, the dev password, or
> a login `blazen`. The two are produced by different `make` targets so
> the locked-down default can never be reached by forgetting a flag.

# 06 — SSH access and bootstrap policy

`blazen_os` is voice-first, but SSH is a **standing administration channel**
that is **on by default**. It is **pubkey-only** and ships with **no
credential** — the operator provisions their own key, so an un-provisioned
device admits nobody (fail-closed). SSH complements the voice surface for
first boot, recovery when the voice pipeline is broken, and advanced sysadmin.

> **Decision (2026-06-14):** SSH is **on by default on every image**,
> including release builds — overturning the prior "break-glass / off by
> default" contract, at the maintainer's direction. The posture is
> **operator-key, fail-closed**: `sshd` is always enabled and pubkey-only
> (`ssh.allow_only_pubkey: true`); the shipped release image sets **no
> password and bakes no key**, so it is reachable on port 22 but authenticates
> no one until the operator drops a pubkey via
> `/boot/blazen-firstboot/authorized_keys` at flash time. We never ship a
> default key or password (that would be a shared-secret vulnerability across
> all devices). The `enable_ssh` / `disable_ssh` voice intents are retained
> (loud confirm), so SSH can still be turned off by voice.

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
2. Installs the operator's `authorized_keys` to `~blazen/.ssh/` (SSH is
   already enabled in the image — this is what makes it reachable).
3. Connects to WiFi (or waits for Ethernet).
4. Computes a one-time pairing code and announces it via TTS:
   *"Your pairing code is four-seven-two-nine. Say 'Jessica, my code is
   four-seven-two-nine' when you are ready."*
5. Once paired, deletes `/boot/blazen-firstboot/` (so the SD card can't be
   yanked and impersonated).

SSH stays on after first boot. `keep_ssh_on` defaults `true`; a user who
wants it off can say `"Jessica, disable ssh"` (loud confirm).

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
                   "disable ssh"            "enable ssh"
   ┌──────────┐  ───────────────▶  ┌──────────┐
   │ SSH on   │                    │ SSH off  │
   │ (default)│  ◀───────────────  └──────────┘
   └──────────┘
        │
        │   (voice path broken)
        ▼
   ┌──────────────────┐
   │ Recovery mode ON │◀── triggered by `blazend-health`
   └──────────────────┘
```

- **SSH on** is the steady state in **both** flavours (release and dev).
  It is only ever off if the user explicitly says `"disable ssh"`.
- **Recovery mode** is automatic: triggered by `blazend-health` when any
  of these is true for >60 s:
  - audio-out has been silent through 3 consecutive scheduled checks;
  - audio-in delivered no frames through 3 consecutive scheduled checks;
  - brain failed to produce a token within 3 consecutive utterances;
  - the orchestrator crashed and didn't restart cleanly.

When recovery mode triggers, the LED goes red and a long beep plays. SSH is
already up, so recovery's job is to **signal** (LED/beep) and, if the user
had previously disabled SSH, re-enable it — it does not need to open the
daily channel. After the recovery window the system attempts a clean restart
back to normal mode.

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

## 5. Why pubkey-only and no shipped credential

SSH is on by default because a voice-first device still needs a hatch for
the day the voice path can't serve:

- The user's voice is lost (laryngitis, throat surgery — yes, this matters).
- A model update accidentally breaks ASR for a particular accent.
- The wake-word model needs retraining and the user can't pair.

What keeps "on by default" from being an exposure is the **fail-closed**
auth model: `allow_only_pubkey: true` (no password auth) and **no key baked
into the image**. A freshly flashed device is reachable on port 22 but
authenticates nobody until the operator provisions their own pubkey. We
never ship a default key or password — that would be one shared secret
across every device in the field. The daemon stays small and audited.

## 6. Dev vs release images

Both flavours now ship `blazen` as a **login** user with **SSH enabled** —
the difference is purely the **shipped credential**. They are produced from
the same `rpi5/stage-blazen/` overlay:

| Flavour | Built by | `blazen` account | SSH at boot | Shipped credential |
|---------|----------|------------------|-------------|--------------------|
| **release** (default) | `make pi-image` | login: home + `/bin/bash` + NOPASSWD `sudo`, **password locked** | enabled | **none** — pubkey-only; operator key via firstboot |
| **dev** | `make vm-image`, `make pi-image-dev` | login: home + `/bin/bash` + NOPASSWD `sudo` | enabled | dev pubkey baked + serial password `blazen:blazen` |

The flavour is selected by `scripts/build-image.sh --dev` (or
`BLAZEN_DEV_IMAGE=1`). Both builds set `ENABLE_SSH=1` in the pi-gen config
and `systemctl enable ssh`. The release chroot path locks the `blazen`
password (`passwd --lock`) so only an operator-provisioned key authenticates.
The **dev** build additionally:

- drops a `DEV_IMAGE` marker + the dev public key into the staging payload,
  which `rpi5/stage-blazen/00-install/01-run-chroot.sh` keys off to install
  `~blazen/.ssh/authorized_keys` and set the serial-console fallback
  password `blazen:blazen`;
- bakes a key from `BLAZEN_DEV_SSH_PUBKEY` if set, otherwise generates
  one at `build/dev-ssh/id_ed25519` (gitignored — a private key never
  enters the repo).

The marker and key live only in `/var/lib/blazen-staging/`, which the
chroot script deletes (`rm -rf "$STAGE"`) at the end of the build, so the
dev key/password never ship in a release rootfs.

> A dev image is **not** a relaxed release image — it is a different
> artefact. Release images never carry the dev key or the dev password.
> The two are produced by different `make` targets so the dev credential
> can never reach a shipped image by forgetting a flag.

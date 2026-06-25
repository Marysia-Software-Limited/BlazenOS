#!/bin/bash
# blazen-hat-mixer.sh — set up the ReSpeaker 2-Mics (WM8960) mixer at boot.
#
# The WM8960 powers up with the capture boosts and the DAC→output-mixer routing
# OFF, so out of the box the mic records silence and TTS plays into the void.
# This applies the known-good capture + playback routing explicitly (more
# robust than a captured asound.state, and the HAT isn't present in the pi-gen
# build chroot so we can't snapshot one there). Fail-soft: every control is
# best-effort; a missing control never fails the boot.
set -u
CARD="${BLAZEN_HAT_CARD:-wm8960soundcard}"
A() { amixer -c "$CARD" sset "$@" >/dev/null 2>&1 || true; }

# --- Capture (2 mics on LINPUT1/RINPUT1) ---
# The mic path is MIC → LINPUT1 → input PGA (the 'Capture' volume, up to +30 dB)
# → **Input PGA Boost → input boost mixer → ADC**. That PGA-output-to-ADC hop is
# the `Left/Right Input Mixer Boost` switch, and it powers up **OFF** — so out of
# the box only the weak *direct* `… Input Boost Mixer LINPUTn` tap (≤ +6 dB)
# reaches the ADC and the mics read ~100× too quiet (speech buried under the ADC
# noise floor). Enabling it is what makes the ReSpeaker actually hear voice.
A 'Left Input Mixer Boost' on
A 'Right Input Mixer Boost' on
# PGA gain: +30 dB (max). Near-field speech reads ~18k RMS, well above the
# codec's ~4-6k resting floor — the separation the VAD thresholds in
# blazend-audio-in.service depend on. (Very loud speech can clip to full-scale;
# faster-whisper tolerates that fine, and the gain is what buys SNR over the
# WM8960's gain-independent noise floor.) Re-tune the VAD floors together with
# this value if you change it.
A 'Capture' 100% cap
A 'Left Input Boost Mixer LINPUT1' 3
A 'Right Input Boost Mixer RINPUT1' 3
A 'Left Boost Mixer LINPUT1' unmute
A 'Right Boost Mixer RINPUT1' unmute
A 'ADC PCM' 100%

# --- Playback (DAC → output mixer → speaker + headphone) ---
A 'Left Output Mixer PCM' on
A 'Right Output Mixer PCM' on
A 'Playback' 100%
A 'Speaker' 100%
A 'Speaker AC' 100%
A 'Speaker DC' 100%
A 'Headphone' 100%

exit 0

"""The orchestrator: connects to every peer socket, tracks state, reacts.

M1 scope:
  - Open a publisher socket (`orchestrator.sock`).
  - Subscribe to wake, audio-in, asr, brain, tts, audio-out, health.
  - For every received envelope, merge a small summary into state.json.
  - Re-publish a `system.event` `ready` after first heartbeat from health.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import os
import random
import re
import struct
import subprocess
import wave
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from blazend.config import load as load_config
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.audiobook_progress import (
    AudiobookProgress,
)
from blazend.domains.ai_orchestrator.adapters.rpi5.dispatch import IntentDispatcher, SettingsStore
from blazend.domains.systems.adapters.rpi5.led import PipelineLeds
from blazend.domains.systems.adapters.rpi5.led_hw import open_status_led
from blazend.domains.systems.adapters.rpi5.recovery import for_level as recovery_for_level
from blazend.domains.systems.adapters.rpi5.state import StateWriter
from blazend.domains.voice_output.adapters.rpi5.radio_control import RadioControl
from blazend.events import Envelope, system_event
from blazend.i18n import msg, table
from blazend.ipc import Publisher, Subscriber, runtime_dir

log = logging.getLogger("blazend.domains.systems.adapters.rpi5.orchestrator")

# How the orchestrator starts/stops blazend-audio-out for the half-duplex
# hand-off. Appliance: root systemctl via the sudoers rule. Desktop user-scope
# installs set BLAZEN_SYSTEMCTL="systemctl --user" — no sudoers needed.
_SYSTEMCTL: list[str] = (
    os.environ.get("BLAZEN_SYSTEMCTL", "sudo -n systemctl").split()
)


# "Jak mam się do ciebie zwracać?" (the voice-training finale): strip a spoken
# lead-in so both a bare answer ("Błazen") and a phrased one ("mów mi Błazen")
# yield the address form. Mirrors the engine's _NAME_SET phrases, plus
# bare-answer acceptance the engine's intent path doesn't need.
_ADDRESS_LEADIN = re.compile(
    r"^(?:m(?:ó|o)w\s+(?:do\s+mnie|mi)|nazywaj\s+mnie|zwracaj\s+si(?:ę|e)\s+do\s+mnie|"
    r"jestem|mam\s+na\s+imi(?:ę|e)|nazywam\s+si(?:ę|e)|per|call\s+me|i\s*'?a?m|"
    r"my\s+name\s+is|address\s+me\s+as)\s+",
    re.IGNORECASE,
)


def _extract_address(text: str) -> str:
    """Spoken answer → the address to store ("Mów mi Błazen." → "Błazen").

    At most four words survive — an address is short, and a rambling answer is
    more likely a mis-capture than a name. Empty string means "couldn't tell"
    (the caller retries or keeps the previous profile name)."""
    body = text.strip().strip(" ,.?!„”\"")
    # Lead-ins stack ("zwracaj się do mnie per pan doktor") — strip until fixed.
    while (stripped := _ADDRESS_LEADIN.sub("", body).strip(" ,.?!„”\"")) != body:
        body = stripped
    # Whisper's stock silence hallucinations must never become the owner's
    # address (live 2026-09-08 15:56: an empty name window transcribed to
    # "Dziękuje za uwagę" and Jessica started addressing the user with it).
    folded = body.lower().replace("ę", "e").replace("ó", "o").replace("ł", "l")
    if any(p in folded for p in (
            "dziekuje za", "dzieki za", "napisy", "amara", "zapraszam",
            "do zobaczenia", "thanks for watching", "subscribe")):
        return ""
    words = body.split()
    if not words or len(words) > 4:
        return ""
    name = " ".join(words)
    return name[:1].upper() + name[1:]


def _ambient_target_pct(
    rms: float, current: int, *, quiet_rms: float, loud_rms: float,
    min_pct: int, max_pct: int, step_pct: int,
) -> int:
    """Map an ambient-noise RMS to a media volume target (linear between the
    quiet/loud anchors), moving at most ``step_pct`` per cycle (no pumping)
    and ignoring nudges under 5 points. Pure — unit-tested without audio."""
    span = max(1.0, float(loud_rms - quiet_rms))
    frac = min(1.0, max(0.0, (rms - quiet_rms) / span))
    target = min_pct + frac * (max_pct - min_pct)
    delta = max(float(-step_pct), min(float(step_pct), target - current))
    proposed = int(round(current + delta))
    return proposed if abs(proposed - current) >= 5 else current


def _state_root() -> Path:
    """Persistent state root: the parent of BLAZEN_DATA_DIR (…/data) when set —
    desktop installs point it under XDG — else the appliance /var/lib/blazen."""
    d = os.environ.get("BLAZEN_DATA_DIR")
    return Path(d).parent if d else Path("/var/lib/blazen")


# Jabra SPEAK 410 output-volume control (voice volume commands + radio ducking).
# Env-overridable for non-appliance installs (2026-08-23); Pi defaults unchanged.
_JABRA_CARD = os.environ.get("BLAZEN_MIXER_CARD", "USB")   # ALSA card id
_JABRA_MIXER = os.environ.get("BLAZEN_MIXER_CONTROL", "PCM")  # playback control
# Duck HARD (near-mute) while listening over a playing stream: the Jabra is a
# speakerphone whose DSP gates the mic when it plays loud, so at 8% commands still
# came through at only ~45-180 RMS (often dropped). ~2% un-gates the mic for a
# clean capture; the stream keeps running (not stopped), so it restores instantly.
_DUCK_PCT = 2             # Jabra output % while listening over a playing stream
_DUCK_WINDOW_S = 7.0      # restore volume if no command follows the wake (covers the ~5 s ASR window)
_DUCK_MIN_VOLUME_PCT = 15  # only duck when playback is above this — quiet music doesn't gate the mic
# Startup output volume. The appliance default stays LOW (30 — less
# speaker→mic echo on the Pi's hardware control); desktop installs override
# via BLAZEN_VOLUME_PCT — on a softvol the percentage maps linearly onto a
# dB range, so 30% there is near-silence (2026-08-29, paul runs 80).
_DEFAULT_VOLUME_PCT = int(os.environ.get("BLAZEN_VOLUME_PCT", "30"))
# Min gap between "Słucham?" prompts, so the over-firing wake model can't turn an
# empty-capture cue into a chant on repeated false wakes.
_LISTENING_CUE_COOLDOWN_S = 12.0
# Same guard for the "Nie zrozumiałam" cue: a false-wake storm captures ambient
# noise → whisper-empty (asr.no_text) → without this, one cue per false wake turns
# the storm into a chant (and churns audio-out up/down each time). A genuine single
# mis-hearing still gets one cue; a burst gets one, not ten.
_NOT_UNDERSTOOD_CUE_COOLDOWN_S = 8.0
# Thinking cue ("Chwileczkę."): spoken when the answer will take a while — the
# brain says it's engaging the LLM (system.event kind=thinking), or a fast-path
# tool reply is long enough that its XTTS render leaves seconds of dead air. A
# blind user needs to hear that Jessica HEARD them and is working. One cue per
# question: cooldown collapses duplicates.
_WORKING_CUE_COOLDOWN_S = 6.0
_WORKING_CUE_MIN_CHARS = 120  # tool replies longer than this get the cue first
# Pre-roll before publishing a supervisor-originated spoken reply: audio-out is DOWN
# while idle (half-duplex) and takes ~1 s to start ALSA + subscribe to tts.frame. A
# cache-rendered reply synthesises in ~90 ms — so without this wait the TTS frame is
# published into a ring no one is reading yet: short replies VANISH, long ones start
# CLIPPED. We bring audio-out up and let it subscribe first. Only paid when audio-out
# was down (idle); rapid follow-ups (already up) skip the wait.
_SPEAKER_WARMUP_S = 1.5
# After handling one wake, ignore further wake.detected for this long. Breaks the
# beep→mic→wake feedback loop and stops a false-wake burst from firing a beep +
# opening a capture window every couple of seconds. One real "dżesika" → one beep
# → one listen window; rapid repeats within the refractory are dropped.
_WAKE_REFRACTORY_S = 4.0
# Wake-less STOP sentinel (2026-09-08): while a stream plays (or a long reply
# is being spoken) the Jabra DSP attenuates the mic and "Dżesika, stop!" can
# score below even the wake near-miss band — the user was left unable to stop
# a radio by voice. So any short VAD utterance heard DURING her own activity
# arms one ASR stop check (strictly matched there; everything not a stop
# phrase is discarded). Rate-limited so speech-y radio leak-through can't keep
# whisper busy. Escape hatch: BLAZEN_STOP_SENTINEL=0.
_STOPCHECK_MIN_GAP_S = 5.0
# Min gap between foreign-voice blips ("plumknięcie", 2026-09-08) — a chatty
# radio/TV must not turn the not-obeying signal into a metronome.
_FOREIGN_CUE_COOLDOWN_S = 8.0
# Confirmation dialog for UNCERTAIN actions (2026-09-08): whisper confidence
# below asr.yaml `confirm_conf` on an actionable intent makes Jessica repeat
# her understanding and ask, instead of acting on a possible mishearing.
# The pending action survives this long; then it silently lapses.
_CONFIRM_TTL_S = 30.0
# Repeat-to-learn (2026-09-21, user request): a household voice the models fit
# poorly (Błazen says "dżesika" differently than the trained samples) fails
# REPEATEDLY — jessica-ts journal shows 2-4 attempts per command. After this
# many consecutive failed understandings (asr.no_text / brain not_understood)
# Jessica stops repeating the generic cue and EXPLICITLY asks for the wake
# word + command again, arming a label window: the successful retry lands in
# learning/events.jsonl as `repeat_after_prompt` with its audio clip — wake
# positives + lexicon material for the next retrain.
_LEARN_REPEAT_AFTER = 2       # consecutive misses before the explicit request
_LEARN_REPEAT_HORIZON_S = 120.0  # misses further apart than this don't chain
_LEARN_REPEAT_WINDOW_S = 60.0    # how long the retry keeps its training label
# Confirmation vocabulary (folded, lowercase). Sync note: emotional overlaps
# the stop-sentinel phrases on purpose — "zamknij się" both aborts a pending
# action AND is a strong detection-error label.
_CONFIRM_YES = frozenset({
    "tak", "potwierdzam", "dobrze", "zgadza sie", "zgadzasie", "no tak",
    "tak tak", "dokladnie", "wlasnie tak", "yes", "correct", "right", "ok",
})
_CONFIRM_NO = frozenset({
    "nie", "nie potwierdzam", "anuluj", "zle", "nie tak", "wycofaj",
    "no", "wrong", "cancel", "nope",
})
_CONFIRM_EMO = (
    "zamknij sie", "spierdalaj", "odczep sie", "daj spokoj", "cicho badz",
    "odwal sie", "shut up", "piss off", "leave me alone",
)


def classify_confirmation(text: str) -> str:
    """Classify the user's reaction to "Zrozumiałam: «…» — potwierdzasz?"
    → ``confirmed`` | ``denied`` | ``emotional`` | ``other``. Pure and folded;
    ``other`` covers both unrelated babble (→ unconfirmed) and a corrected
    command (the caller distinguishes by whether an intent matched)."""
    import re as _re
    import unicodedata

    folded = "".join(c for c in unicodedata.normalize("NFKD", text.lower())
                     if not unicodedata.combining(c)).replace("ł", "l")
    folded = _re.sub(r"[^\w\s]", " ", folded).strip()
    folded = _re.sub(r"\s+", " ", folded)
    if any(p in folded for p in _CONFIRM_EMO):
        return "emotional"
    if folded in _CONFIRM_YES:
        return "confirmed"
    if folded in _CONFIRM_NO:
        return "denied"
    toks = folded.split()
    if toks and len(toks) <= 3 and toks[0] in ("tak", "yes"):
        return "confirmed"
    if toks and len(toks) <= 3 and toks[0] in ("nie", "no"):
        return "denied"
    return "other"
_STOPCHECK_MIN_MS = 250.0
_STOPCHECK_MAX_MS = 4000.0
_STOP_SENTINEL = os.environ.get("BLAZEN_STOP_SENTINEL", "1") != "0"
# How long a guided dialog (voice training / memo dictation) may wait for its
# capture before the orchestrator declares it abandoned and re-enables wake
# activation. Must exceed the longest capture (memo max_s 60) + prompts.
_DIALOG_TIMEOUT_S = 90.0
# Conversation follow-up: how long after a spoken chat reply the wake-less
# answer window stays armed (capture max 12 s + margin).
_FOLLOWUP_TIMEOUT_S = 25.0
# When the LLM reply explicitly AWAITS an answer (question / invitation), the
# user is composing one — keep the window open noticeably longer (2026-09-08).
_FOLLOWUP_HOT_TIMEOUT_S = 40.0

DEFAULT_PEERS: tuple[str, ...] = (
    "audio-in",
    "wake",
    "asr",
    "nlu",
    "brain",
    "tts",
    "audio-out",
    "health",
)

# Wake earcon: the instant "dżesika → beep → speak" cue for the blind-first UX.
# A short two-note rising chime played straight to ALSA (not via blazend-audio-out,
# which is slow to start and down while idle) so it's pre-verbal and adds no latency.
_BEEP_RATE_HZ = 22050          # Jabra playback rate (matches audio.yaml output)
_BEEP_DEVICE = os.environ.get(
    "BLAZEN_BEEP_DEVICE", "plughw:CARD=USB,DEV=0")  # Jabra ALSA device (aplay -D)


_TRACK_NO_PREFIX = re.compile(r"^\s*\d{1,3}\s*[-.)]?\s+")


def _track_label(path: str) -> str:
    """Spoken name of a queued file: the stem minus the "03 " track prefix."""
    return _TRACK_NO_PREFIX.sub("", Path(path).stem).replace("_", " ").strip()


def _queue_label(book: dict[str, Any], i: int) -> str:
    """Spoken name of queue position ``i``: the payload's label (the music
    index's repaired title / a memo's title) when present, else the filename
    stem — so mojibake rips and "vn-3.wav" are never read aloud when the
    index knows better."""
    labels = book.get("labels") or []
    if 0 <= i < len(labels) and str(labels[i]).strip():
        return str(labels[i]).strip()
    return _track_label(str(book["chapters"][i]))


# Processing heartbeat: how long the ticker may run before giving up (a reply,
# cue or error normally cancels it much earlier).
_HB_MAX_S = 60.0


def _make_processing_tick_wav(rate: int = _BEEP_RATE_HZ) -> bytes:
    """A single soft low tick (~60 ms, 440 Hz) — quieter and lower than the
    wake chime so "still working" never sounds like "speak now"."""
    n = int(rate * 0.06)
    fade = max(1, int(0.008 * rate))
    frames = bytearray()
    for i in range(n):
        env = min(1.0, i / fade, (n - i) / fade)
        frames += struct.pack("<h", int(0.22 * env * 32767 * math.sin(2 * math.pi * 440.0 * i / rate)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _make_foreign_blip_wav(rate: int = _BEEP_RATE_HZ) -> bytes:
    """The foreign-voice "plumknięcie": ONE low falling note (330→262 Hz,
    ~140 ms, quiet) — unmistakably different from the rising wake chime.
    "I heard a voice, it is not my owner, I am not acting" (2026-09-08)."""
    notes = (330.0, 262.0)
    note_s = 0.07
    fade = max(1, int(0.008 * rate))
    frames = bytearray()
    for freq in notes:
        n = int(rate * note_s)
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)
            frames += struct.pack(
                "<h", int(0.18 * env * 32767 * math.sin(2 * math.pi * freq * i / rate)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def _make_wake_beep_wav(rate: int = _BEEP_RATE_HZ) -> bytes:
    """A short, gentle rising two-note chime as WAV bytes (mono i16).

    ~150 ms total (660 Hz → 990 Hz), each note faded in/out ~8 ms to avoid clicks,
    at low amplitude so it's a soft acknowledgement, not a startle. Pure/deterministic
    so it's unit-testable and identical every boot; fed to ``aplay`` on stdin."""
    notes = (660.0, 990.0)
    note_s = 0.075
    fade = max(1, int(0.008 * rate))
    frames = bytearray()
    for freq in notes:
        n = int(rate * note_s)
        for i in range(n):
            env = min(1.0, i / fade, (n - i) / fade)
            frames += struct.pack("<h", int(0.35 * env * 32767 * math.sin(2 * math.pi * freq * i / rate)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


class Orchestrator:
    """The supervisor process. One per system."""

    def __init__(
        self,
        peers: Iterable[str] = DEFAULT_PEERS,
        runtime_dir_: Path | None = None,
        dispatcher: IntentDispatcher | None = None,
    ):
        self._runtime_dir = runtime_dir_ or runtime_dir()
        self._peers = tuple(peers)
        self._state = StateWriter(self._runtime_dir / "state.json")
        self._led = PipelineLeds(self._runtime_dir / "led.json")
        self._hwled = open_status_led()  # APA102 on the HAT, or a no-op without SPI
        self._publisher = Publisher(self._runtime_dir / "orchestrator.sock")
        self._dispatcher = dispatcher if dispatcher is not None else self._build_dispatcher()
        self._default_lang = self._load_default_lang()
        self._greeting = self._load_greeting()
        self._require_wake, self._wake_window_s = self._load_wake_gating()
        self._awake_until = 0.0  # loop-clock deadline; speech acts only before it
        self._radio = RadioControl()  # internet-radio playback
        # Local music/audiobook playback KILL SWITCH. Its in-play controls (stop /
        # next / volume) are being fixed; while BLAZEN_MUSIC_ENABLED=0 the fast
        # path speaks a notice instead of starting an unstoppable local track.
        # Radio (internet streams) is unaffected. Default: enabled.
        self._music_enabled = os.environ.get("BLAZEN_MUSIC_ENABLED", "1") != "0"
        # Last played source (radio url / music path) + its name, so "kontynuj"
        # can restore it after a stop.
        self._last_source = ""
        self._last_source_name = ""
        # Now-playing AUDIOBOOK state: {slug, chapters:[...], index, name} (None for
        # music/radio). The player writes its live position to `_position_file`; the
        # orchestrator reads it to remember/resume and to auto-advance chapters.
        self._book: dict[str, Any] | None = None
        # Last book that was playing, kept across a stop so "kontynuj"/"czytaj dalej"
        # can resume it at the saved chapter+offset. Reset to None whenever a
        # non-book source (radio/music) plays, so resume tracks the most-recent kind.
        self._last_book: dict[str, Any] | None = None
        self._position_file = self._runtime_dir / "player-position"
        self._book_stopping = False  # user stop (vs natural EOF) — suppresses auto-advance
        self._book_progress = AudiobookProgress()  # sole writer of progress.json
        # Attention check: pause + "Czy słuchasz?" after a spell of listening; resume
        # on any reply, stay stopped on silence (so a sleeping listener keeps place).
        att = self._load_attention()
        self._attention_enabled, self._attention_interval_s, self._attention_window_s = att
        # Audible state cues for the blind-first UX (audio.yaml earcons + phrases.yaml).
        self._hb_interval_s = 5.0  # default; _load_earcons may override
        self._earcons, self._cues = self._load_earcons()
        # Wake chime: pre-rendered once, played straight to the Jabra on wake.detected.
        self._beep_wav = _make_wake_beep_wav()
        self._foreign_blip_wav = _make_foreign_blip_wav()
        self._foreign_cue_at = 0.0
        # Speaker policy (speaker-id.yaml, 2026-09-08): enforcement flag +
        # the commands any voice may run. Soft: unreadable config = trust all.
        try:
            _sp = load_config("speaker-id").data
        except Exception:  # noqa: BLE001
            _sp = {}
        self._speaker_enforce = bool(_sp.get("enforce", False))
        self._speaker_allow_any = frozenset(
            str(x) for x in _sp.get("allow_any_voice", []) or [])
        # Uncertain-action confirmation (2026-09-08): pending deferred intent
        # + the confidence bar an action must clear to run unconfirmed.
        self._confirm_pending: dict[str, Any] | None = None
        try:
            self._confirm_conf = float(
                load_config("asr").get("confirm_conf", 0.7))
        except Exception:  # noqa: BLE001
            self._confirm_conf = 0.7
        # Processing heartbeat ("still working" tick): armed on wake, cancelled
        # by whatever answers first (reply / cue / playback / memo result).
        self._tick_wav = _make_processing_tick_wav()
        self._hb_task: asyncio.Task[None] | None = None
        # Memo dictation dialog state: None | "title" | "content".
        self._memo_stage: str | None = None
        # Voice-training dialog: 0 = off, else the sample round in progress
        # (1..5). Shares the memo capture window machinery.
        self._voice_train_round = 0
        self._voice_train_retries = 0  # consecutive empty captures (cap 2)
        # Final voice-training step (2026-08-27): after the wake samples she
        # asks how to address the owner; "name" while that answer is awaited.
        self._voice_train_stage: str | None = None
        # Conversation mode (2026-08-30): after a spoken LLM reply a wake-less
        # follow-up window opens (chime → followup-capture marker); while it
        # is armed, wake events are suppressed like in guided dialogs — the
        # user's speech is captured anyway, and a "stop" in it still acts.
        self._followup_until = 0.0
        self._followup_task: asyncio.Task[None] | None = None
        self._ambient_rms: float | None = None  # secondary-mic room level
        try:
            self._followup_enabled = bool(
                load_config("wake-word").get("conversation.followup", True))
        except Exception:  # noqa: BLE001
            self._followup_enabled = True
        # Guided-dialog deadline: while voice training / memo dictation waits
        # for a capture, wake activation is SUPPRESSED (saying "dżesika" as a
        # training sample must not beep + open a command window mid-dialog).
        # The suppression is DERIVED from the dialog state, so it lifts by
        # itself on completion, error, or abort — and this deadline is the
        # backstop: if the capture never comes back (ASR died mid-window),
        # the stale dialog state is dropped and normal listening resumes.
        self._dialog_until = 0.0
        self._memo_title = ""
        self._memo_lang = "pl"
        # "dżesika stop" mid-processing: the brain may already be generating —
        # drop its late reply instead of speaking a cancelled answer.
        self._drop_next_reply = False
        self._beep_device = self._load_beep_device()
        self._listening_cue_at = 0.0  # loop time of the last "Słucham?" (cooldown)
        self._stopcheck_at = 0.0  # loop time of the last wake-less stop check
        self._not_understood_cue_at = 0.0  # loop time of the last "Nie zrozumiałam" (cooldown)
        self._miss_count = 0          # consecutive failed understandings (repeat-to-learn)
        self._last_miss_at = 0.0      # loop time of the last miss (chains within horizon)
        self._repeat_armed_until = 0.0  # label the next understood command as a training repeat
        self._working_cue_at = 0.0  # loop time of the last "Chwileczkę." (cooldown)
        self._wake_handled_at = -1e9  # loop time of the last acted-on wake (refractory)
        self._book_activity_at = 0.0     # loop time the attention interval counts from
        self._awaiting_attention = False
        self._attention_deadline = 0.0
        self._book_paused_for_attention = False
        self._stop = asyncio.Event()
        self._subscribers: list[tuple[str, Subscriber]] = []
        # Half-duplex speaker: the Jabra is a speakerphone whose echo-cancellation
        # ducks its OWN mic while blazend-audio-out holds the output stream open —
        # so the mic can't hear a command while audio-out is up. The supervisor
        # owns audio-out: DOWN by default (mic un-ducked, listening), UP only while
        # a reply is speaking. `_speak_until` is a loop-clock deadline bumped on
        # each spoken reply; `speaker-busy` (set by audio-out during playback)
        # extends it. A reconciler applies the desired state.
        self._speaker_busy = self._runtime_dir / "speaker-busy"
        self._speak_until = 0.0
        self._audio_out_up: bool | None = None  # last-applied state (None = unknown)
        # Jabra output volume (0-100 %). Voice commands (głośniej / ciszej /
        # "ustaw głośność na N") mutate audio.volume → applied to the speaker here.
        # Radio "duck & verify": a wake while a stream plays may just be the
        # stream echoing into the mic, so we DUCK the output (not stop), listen,
        # and restore unless a real radio command follows.
        self._volume_pct = _DEFAULT_VOLUME_PCT
        self._ducked = False
        self._duck_task: asyncio.Task[None] | None = None

    @staticmethod
    def _load_attention() -> tuple[bool, float, float]:
        """Audiobook attention-check settings (enabled, interval_s, window_s)."""
        try:
            a = load_config("audiobooks").data.get("attention", {}) or {}
            return (bool(a.get("enabled", True)),
                    float(a.get("interval_min", 20)) * 60.0,
                    float(a.get("window_s", 20)))
        except Exception:  # noqa: BLE001
            return True, 1200.0, 20.0

    def _load_earcons(self) -> tuple[dict[str, bool], dict[str, str]]:
        """Audible state cues: which are on (audio.yaml `earcons`) + the spoken
        cue text for this node's language (phrases.yaml `cues`)."""
        try:
            audio = load_config("audio")
            earcons = {
                "wake_chime": bool(audio.get("earcons.wake_chime", True)),
                "error_tone": bool(audio.get("earcons.error_tone", True)),
                "thinking": bool(audio.get("earcons.thinking", True)),
                "processing_tick": bool(audio.get("earcons.processing_tick", True)),
            }
            self._hb_interval_s = float(audio.get("earcons.processing_tick_s", 5.0))
        except Exception:  # noqa: BLE001
            earcons = {"wake_chime": True, "error_tone": True, "thinking": True,
                       "processing_tick": True}
            self._hb_interval_s = 5.0
        lang = self._default_lang or "pl"
        fallback = {"not_understood": "Nie zrozumiałam. Powiedz proszę jeszcze raz.",
                    "listening": "Słucham?",
                    "working": "Chwileczkę, już to sprawdzam.",
                    "searching": "Poczekaj chwileczkę, szukam wiadomości.",
                    "still_searching": "Chwileczkę, ciągle szukam.",
                    "learn_repeat": ("Powiedz słowo dżesika i swoją komendę "
                                     "ponownie, abym mogła się nauczyć.")}
        try:
            phrases = load_config("phrases")
            cues = {k: str(phrases.get(f"cues.{k}.{lang}", v)) for k, v in fallback.items()}
        except Exception:  # noqa: BLE001
            cues = fallback
        return earcons, cues

    @staticmethod
    def _load_beep_device() -> str:
        """Concrete ALSA device for the wake chime (aplay -D). The Jabra is one
        physical device for mic + speaker, so reuse audio.yaml's ``input.device``
        (a real ``plughw:CARD=...`` string); fall back to the Jabra default."""
        try:
            dev = str(load_config("audio").get("input.device", "") or "")
            return dev if dev.startswith("plughw:") else _BEEP_DEVICE
        except Exception:  # noqa: BLE001
            return _BEEP_DEVICE

    def _wake_chime_armed(self) -> bool:
        """Whether to sound the wake chime: enabled in audio.yaml AND the Jabra is
        free (no radio/music stream holding the output device)."""
        return bool(self._earcons.get("wake_chime")) and not self._radio.playing

    async def _play_beep(self, wav: bytes, *, tail_s: float = 0.3) -> None:
        """Play a pre-rendered cue straight to the Jabra via ``aplay`` and wait.

        Bypasses blazend-audio-out (down while idle, ~1-2 s to start) so the cue is
        instant. While it plays we hold a ``cue`` marker (+ a short tail) so
        overlapping beeps can coordinate. It must NOT be the ``speaking`` marker: the
        ASR drops any wake fired while ``speaking`` exists, and the wake chime plays
        in reaction to the very wake the ASR is about to serve — marking it as
        self-speech made the ASR ignore every wake (deaf pipeline, 2026-08-09).
        ``speaking`` stays reserved for real TTS replies, whose echo genuinely
        re-fires the wake. A failure is swallowed — a missing cue must never break
        the voice path."""
        marker = self._runtime_dir / "cue"
        had_marker = marker.exists()
        try:
            if not had_marker:
                marker.touch()
        except OSError:
            pass
        try:
            proc = await asyncio.create_subprocess_exec(
                "aplay", "-q", "-D", self._beep_device, "-t", "wav", "-",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.communicate(wav)
        except (OSError, asyncio.CancelledError) as exc:
            log.debug("beep skipped: %s", exc)
        finally:
            if not had_marker:
                await asyncio.sleep(tail_s)  # let the tail decay before listening
                try:
                    marker.unlink(missing_ok=True)
                except OSError:
                    pass

    async def _play_wake_beep(self) -> None:
        """The wake chime: "I heard „dżesika” — speak now"."""
        await self._play_beep(self._beep_wav)

    # -- processing heartbeat: a soft tick every N s while Jessica works ----
    def _start_heartbeat(self) -> None:
        """(Re)arm the working ticker after a wake. It stays silent for the
        capture window, then ticks every `_hb_interval_s` until something
        audible happens (reply/cue/playback) — the blind-first "still with
        you" signal for slow ASR + LLM turns."""
        if not self._earcons.get("processing_tick"):
            return
        self._cancel_heartbeat()
        self._hb_task = asyncio.ensure_future(self._heartbeat_loop())

    def _cancel_heartbeat(self) -> None:
        if self._hb_task is not None and not self._hb_task.done():
            self._hb_task.cancel()
        self._hb_task = None

    @property
    def _processing(self) -> bool:
        """True while the working ticker is armed (wake heard, no reply yet)."""
        return self._hb_task is not None and not self._hb_task.done()

    async def _heartbeat_loop(self) -> None:
        try:
            for _ in range(int(_HB_MAX_S / self._hb_interval_s)):
                await asyncio.sleep(self._hb_interval_s)
                # Never tick over content or speech — the sound means
                # "working in silence", not "interrupting".
                if self._radio.playing or (self._runtime_dir / "speaking").exists() \
                        or self._speaker_busy.exists():
                    continue
                await self._play_beep(self._tick_wav, tail_s=0.15)
        except asyncio.CancelledError:
            return

    @staticmethod
    def _load_wake_gating() -> tuple[bool, float]:
        """Whether commands require a recent wake, and the window length."""
        try:
            cfg = load_config("wake-word").data
            return bool(cfg.get("require_wake", True)), float(cfg.get("conversation_window_s", 20))
        except Exception:  # noqa: BLE001
            return True, 20.0

    def _awake(self) -> bool:
        """True if Jessica is within the conversation window (or gating off)."""
        return not self._require_wake or asyncio.get_running_loop().time() < self._awake_until

    @staticmethod
    def _load_default_lang() -> str:
        """System default reply language (Polish-first); falls back to pl."""
        try:
            return str(load_config("system").data.get("languages", {}).get("default", "pl"))
        except Exception:  # noqa: BLE001
            return "pl"

    @staticmethod
    def _load_greeting() -> tuple[bool, float, str, str]:
        """Startup self-introduction (Polish-first). Spoken once when the
        pipeline comes up so a screenless user hears the system is alive.
        Configurable via system.yaml: startup_greeting.{enabled,delay_s,pl,en}."""
        pl = msg("greeting.startup", "pl")
        en = msg("greeting.startup", "en")
        enabled, delay = True, 5.0
        try:
            g = load_config("system").data.get("startup_greeting", {}) or {}
            enabled = bool(g.get("enabled", True))
            delay = float(g.get("delay_s", 5.0))
            pl = str(g.get("pl", pl))
            en = str(g.get("en", en))
        except Exception:  # noqa: BLE001
            pass
        return enabled, delay, pl, en

    async def _announce_greeting(self) -> None:
        """Speak the startup self-introduction once, after a short grace so the
        TTS + audio-out peers have subscribed to this socket."""
        enabled, delay, pl, en = self._greeting
        if not enabled:
            return
        # Quiet hours: an operator-written ISO timestamp ("keep her silent
        # until 10:00" at a client site, 2026-08-22) — while now < it, start-up
        # stays mute. The scheduled introduction removes the marker; a
        # malformed marker never mutes forever.
        quiet = _state_root() / "quiet-until"
        try:
            if quiet.exists() and datetime.now() < datetime.fromisoformat(
                    quiet.read_text().strip()):
                log.info("startup greeting suppressed (quiet until %s)",
                         quiet.read_text().strip())
                return
        except (OSError, ValueError):
            pass
        await asyncio.sleep(delay)
        lang = self._default_lang
        text = en if lang == "en" else pl
        # Personal introduction: {name} → ", <owner>" from the stored profile
        # ("Błazen"), or nothing while no name is set.
        name = ""
        try:
            from blazend.domains.context.adapters.rpi5.memory import MemoryStore
            entry = MemoryStore()._db.profile.get("name") or {}
            name = str(entry.get("value", "") or "")
        except Exception:  # noqa: BLE001 — a broken store must not mute the greeting
            pass
        text = text.replace("{name}", f", {name}" if name else "")
        log.info("speaking startup greeting (%s)", lang)
        self._mark_speaking(len(text))  # bring audio-out up before TTS plays
        await asyncio.sleep(2.5)  # let the reconciler start audio-out
        await self._publisher.publish(
            Envelope(
                topic="brain.reply",
                source="blazend-orchestrator",
                data={
                    "language": lang,
                    "text": text,
                    "chunk": text,
                    "final_": True,
                    "action": "system.greeting",
                },
            )
        )

    def _recovery_lang(self) -> str:
        """Effective language for a fault cue: a voice pin, else the default."""
        pinned = self._dispatcher.pinned_language() if self._dispatcher else None
        return pinned or self._default_lang

    def _build_dispatcher(self) -> IntentDispatcher | None:
        """Load intents + voice-policy for fast-path command dispatch.

        Returns ``None`` (commands just observed) if the configs aren't
        reachable — e.g. a bare test runtime with no ``BLAZEN_CONFIG_ROOT``.
        """
        try:
            intents = load_config("intents/system").data
            policy = load_config("voice-policy").data
            if not intents.get("intents"):
                return None
            settings = SettingsStore(self._runtime_dir / "settings.json")
            return IntentDispatcher(intents, policy, settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("intent dispatcher disabled (%s)", exc)
            return None

    def _mark_speaking(self, text_len: int = 0) -> None:
        """Keep audio-out up until TTS starts producing frames — after which
        `speaker-busy` (set by audio-out while its queue is non-empty) holds it for
        the whole playback. The window scales with reply length: synthesising a long
        or novel reply via XTTS takes several seconds (more when paul's XTTS is busy),
        and a fixed 6 s dropped audio-out before a long reply (book menu, news digest)
        finished synthesising, so it played to a closed device."""
        window = 6.0 + text_len * 0.04
        self._speak_until = asyncio.get_running_loop().time() + min(window, 30.0)

    async def _prepare_speaker(self, text_len: int = 0) -> None:
        """Bring audio-out UP and let it subscribe to tts.frame BEFORE the caller
        publishes a supervisor-originated reply, closing the publish-before-subscribe
        race that made short (cache-rendered) replies vanish and long ones start
        clipped. Marks the speak window either way. The warm-up wait is only paid when
        audio-out was actually down (idle start); when it's already up — a rapid
        follow-up in the same conversation — we return immediately. Skipped while a
        stream owns the Jabra (the spoken path won't seize the device from playback).
        Only use this on paths where the SUPERVISOR publishes the reply; a brain/LLM
        reply is already in flight to TTS when we see it (and is slow enough to synth
        that audio-out is up by the time its frame lands)."""
        self._mark_speaking(text_len)
        if self._audio_out_up is True or self._radio.playing:
            return
        await self._apply_audio_out(True)
        await asyncio.sleep(_SPEAKER_WARMUP_S)

    async def _apply_audio_out(self, up: bool) -> None:
        """Start/stop blazend-audio-out to match desired state (only on change)."""
        if up == self._audio_out_up:
            return
        self._audio_out_up = up
        # Mark self-speech: audio-out is UP only for Jessica's own TTS (radio keeps
        # it DOWN). The ASR skips wakes fired during this window so Jessica's voice
        # echoing into the Jabra mic can't drive an endless self-reply loop.
        try:
            marker = self._runtime_dir / "speaking"
            if up:
                marker.touch()
            else:
                marker.unlink(missing_ok=True)
        except OSError:
            pass
        action = "start" if up else "stop"
        try:
            await asyncio.to_thread(
                subprocess.run,
                [*_SYSTEMCTL, action, "blazend-audio-out"],
                check=False, timeout=10,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            log.info("audio-out %s (half-duplex: %s)", action,
                     "speaking" if up else "listening")
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("systemctl %s blazend-audio-out failed: %s", action, exc)
            self._audio_out_up = None  # unknown — retry next tick

    async def _speaker_manager(self) -> None:
        """Reconcile audio-out: UP while a reply speaks (and no radio stream holds
        the Jabra), DOWN otherwise so the mic is un-ducked for the next command."""
        await self._apply_audio_out(False)  # boot idle: listening, mic un-ducked
        while not self._stop.is_set():
            speaking = self._speaker_busy.exists() or \
                asyncio.get_running_loop().time() < self._speak_until
            desired = speaking and not self._radio.playing
            await self._apply_audio_out(desired)
            await asyncio.sleep(0.3)

    async def run(self) -> None:
        """Bind sockets, connect to peers, react until interrupted."""
        await self._publisher.bind()
        await self._state.update({"v": 1, "ready": False, "units": {}})
        self._led.write()  # initial state (idle: listening)
        self._hwled.set_pixels(self._led.leds)
        log.info("orchestrator bound at %s", self._publisher._socket_path)  # noqa: SLF001

        # Apply the startup output volume to the Jabra so the stored audio.volume,
        # the voice volume commands, and the hardware all agree from boot.
        await self._set_volume(self._volume_pct)
        # Clear a stale self-speech marker (a crash mid-reply would otherwise leave
        # the ASR ignoring every wake — deaf until the file is removed).
        (self._runtime_dir / "speaking").unlink(missing_ok=True)
        (self._runtime_dir / "cue").unlink(missing_ok=True)

        # Own audio-out for half-duplex (keeps the Jabra mic un-ducked while idle).
        asyncio.create_task(self._speaker_manager())
        asyncio.create_task(self._book_watcher())  # auto-advance audiobook chapters
        asyncio.create_task(self._ambient_monitor())  # secondary-mic room sensor

        # Connect to every peer (idempotent; missing peers are retried lazily).
        for name in self._peers:
            asyncio.create_task(self._peer_loop(name))

        # Speak a one-time self-introduction once the pipeline is up.
        asyncio.create_task(self._announce_greeting())

        await self._stop.wait()
        self._hwled.close()  # blank the LEDs + release SPI
        await self._publisher.close()
        for _, sub in self._subscribers:
            await sub.close()

    async def shutdown(self) -> None:
        """Signal :meth:`run` to return."""
        self._stop.set()

    async def _peer_loop(self, name: str) -> None:
        socket_path = self._runtime_dir / f"{name}.sock"
        backoff = 1.0
        while not self._stop.is_set():
            if not socket_path.exists():
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 5.0)
                continue
            try:
                sub = Subscriber(socket_path)
                await sub.connect()
                self._subscribers.append((name, sub))
                log.info("connected to %s", name)
                backoff = 1.0
                await self._consume(name, sub)
            except (FileNotFoundError, ConnectionRefusedError, OSError) as exc:
                log.debug("%s not ready (%s); retrying", name, exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 1.5, 5.0)

    async def _consume(self, name: str, subscriber: Subscriber) -> None:
        async for envelope in subscriber:
            await self._on_envelope(name, envelope)

    async def _on_envelope(self, peer: str, env: Envelope) -> None:
        patch: dict[str, Any] = {
            "units": {peer: {"last_topic": env.topic, "last_ts_ms": env.ts_ms}},
        }
        # Audiobook attention: a wake or command means the listener is present —
        # reset the attention interval, and if we're mid-check, resume the book.
        if env.topic in ("wake.detected", "nlu.intent") and self._book is not None:
            self._book_activity_at = asyncio.get_running_loop().time()
            if self._awaiting_attention:
                await self._attention_present()
        if env.topic == "system.event" and env.data.get("kind") == "heartbeat":
            patch["ready"] = True
            patch["units"][peer]["status"] = "running"
        # Thinking cue: the brain is about to block on the LLM for this question —
        # say "Chwileczkę." now so the wait is announced, not dead air.
        if env.topic == "system.event" and env.data.get("kind") == "thinking":
            await self._speak_working_cue()
        # A finished voice-memo dictation from the ASR — store + confirm.
        if env.topic == "system.event" and env.data.get("kind") == "memo_recorded":
            await self._on_memo_recorded(env.data)
        # Dictation opened but nothing was said — tell the (blind) user how to
        # retry instead of leaving dead air after "Nagrywam…".
        if env.topic == "error" and env.data.get("code") == "asr.memo_empty":
            if self._voice_train_round:
                # A silent voice-training round: retry the SAME round (max 2
                # consecutive empties) so one missed sample doesn't abandon the
                # calibration — but a silent room doesn't loop forever either.
                self._voice_train_retries += 1
                if self._voice_train_retries > 2:
                    self._voice_train_round = 0
                    self._voice_train_retries = 0
                    await self._speak(msg("train.abort", self._memo_lang or "pl"),
                                      self._memo_lang or "pl")
                    return
                await self._speak(msg("train.retry_sample", self._memo_lang or "pl"),
                                  self._memo_lang or "pl")
                await self._wait_speech_done()
                self._open_memo_window()
                return
            if self._voice_train_stage == "name":
                # Silence on "jak mam się do ciebie zwracać?" — same bounded
                # retry as the sample rounds, then keep the stored address.
                self._voice_train_retries += 1
                lang = self._memo_lang or "pl"
                if self._voice_train_retries > 2:
                    self._voice_train_stage = None
                    self._voice_train_retries = 0
                    await self._speak(msg("train.name_empty_keep", lang), lang)
                    return
                await self._speak(msg("train.name_empty_retry", lang), lang)
                await self._wait_speech_done()
                self._open_memo_window()
                return
            self._memo_stage = None  # abandon the title/content dialog
            self._memo_title = ""
            await self._speak(msg("memo.retry_empty", self._default_lang))
        if env.topic == "vad.end":
            self._maybe_stopcheck(env)
        if env.topic == "wake.detected":
            now = asyncio.get_running_loop().time()
            # Refractory: ignore wakes that arrive too soon after the last handled
            # one. The wake model over-fires on ambient sound and Jessica's own beep
            # can echo back into the mic; without this, a burst fires a beep + opens
            # a capture window every couple of seconds ("random beeps", commands
            # drowned out). One wake is handled, then a short deaf window.
            if now - self._wake_handled_at < _WAKE_REFRACTORY_S:
                return
            # A guided dialog owns the mic: during voice training the user is
            # ASKED to say "dżesika", and during dictation any wake-ish sound
            # must not beep + open a command window over the capture. Ignore
            # the wake outright; the suppression lifts automatically when the
            # dialog completes, errors out, or times out (_dialog_active).
            if self._dialog_active():
                log.info("wake ignored: guided dialog in progress")
                return
            self._wake_handled_at = now
            self._awake_until = now + self._wake_window_s
            patch["wake_word"] = {
                "last_fired": env.data.get("model"),
                "last_language": env.data.get("language"),
                "last_score": env.data.get("score"),
            }
            patch["awake"] = True
            # STRICT FLOW: wake → SOUND → listen. Play the chime and WAIT for it to
            # finish (it marks self-speech so its echo can't re-fire wake), THEN open
            # the capture window — so the beep never bleeds into the command and the
            # mic starts listening only after the ack. Gated by earcons.wake_chime +
            # a free Jabra (a playing stream's duck is the feedback there).
            if self._wake_chime_armed():
                await self._play_wake_beep()
            # Open one listen window for blazend-audio-in (mirrors the HAT
            # button's activate marker). The wake word otherwise only lights the
            # LED while the mic stays DEAF, so the command spoken after "dżesika"
            # is never captured and ASR/NLU never run. blazend-audio-in consumes
            # this marker to start one capture window.
            try:
                (self._runtime_dir / "activate").touch()
            except OSError as exc:
                log.warning("could not write activate marker: %s", exc)
            # If a stream is playing LOUD, DUCK it (don't stop) so the command
            # after "dżesika" is heard over it — then arm an auto-restore. Quiet
            # playback (<= _DUCK_MIN_VOLUME_PCT) doesn't gate the mic, so skip the
            # duck: no point dropping already-quiet music. A real radio_stop/play
            # acts on it; a false wake just restores the volume.
            if self._radio.playing and self._volume_pct > _DUCK_MIN_VOLUME_PCT:
                await self._duck_on()
                self._arm_duck_restore()
            if self._radio.playing:
                # Guarantee the low ASR floor for the command that follows: while a
                # stream plays the Jabra DSP attenuates the mic (voice lands at
                # ~50-180 RMS), so the post-wake capture must use min_capture_rms_playing.
                # The ASR keys that on speaker-busy; (re)assert it here so a context
                # command ("ciszej"/"stop"/"zagraj inny") over the stream isn't
                # dropped as a false wake by the idle floor.
                try:
                    self._speaker_busy.touch()
                except OSError as exc:
                    log.warning("could not assert speaker-busy marker: %s", exc)
            # Audible state: from here Jessica is WORKING (capture → whisper →
            # route). Tick softly every few seconds until something answers.
            self._start_heartbeat()
        # The fast-path router answers promptly (or its playback IS the answer),
        # and a miss hands over to the brain — which re-arms the ticker via its
        # `thinking` cue only when it will actually engage. Either way this
        # leg of the wait is over; without this a false wake would tick for a
        # minute into an empty room.
        if env.topic in ("nlu.intent", "nlu.miss"):
            stop_like = env.topic == "nlu.intent" and str(env.data.get("intent", "")) in (
                "radio_stop", "stop_talking")
            if stop_like and self._processing and not self._radio.playing:
                # "Dżesika, stop" while she works: kill the wait sounds and drop
                # the answer the brain may still be generating.
                self._cancel_heartbeat()
                self._drop_next_reply = True
                await self._speak(msg("misc.cancel", self._default_lang))
                patch["last_command"] = {"intent": env.data.get("intent"),
                                         "result": "cancelled"}
                await self._state.update(patch)
                await self._publisher.publish(system_event(
                    source="blazend-orchestrator", kind="observed", detail=env.topic))
                return
            self._cancel_heartbeat()
        if env.topic == "system.event" and env.data.get("kind") == "thinking":
            # The brain is about to block on the LLM — the long wait starts NOW.
            self._start_heartbeat()
        if env.topic == "nlu.intent" and self._dispatcher is not None:
            if self._foreign_voice_block(str(env.data.get("intent", "")), env.data):
                self._learn_event("foreign", str(env.data.get("transcript", "")),
                                  env.data)
                await self._notify_foreign_voice()
                await self._state.update(patch)
                return
            if self._confirm_pending is not None:
                # A confirmation is pending — this utterance answers it
                # (yes/no/emotional) or corrects it (a different command,
                # which then falls through and runs normally).
                if await self._resolve_confirmation(env):
                    await self._state.update(patch)
                    return
            if self._awake():
                # A recognised command ends any miss streak; when it answers
                # the repeat-to-learn request it becomes a labelled example.
                self._note_understood(str(env.data.get("transcript", "")),
                                      env.data)
            if not self._awake():
                # Heard a command but not addressed ("Hej Jessico" not said) —
                # acknowledge in state, but stay silent.
                log.info("asleep — ignoring %s", env.data.get("intent"))
                patch["awake"] = False
            elif str(env.data.get("intent", "")) in (
                    "music_now_playing", "music_shuffle", "voice_memo_record",
                    "voice_learn"):
                # Intents the orchestrator answers ITSELF, asynchronously — they
                # pause/resume the exclusive player or open a capture window,
                # which a sync dispatcher reply cannot do. Conversation stays open.
                self._awake_until = asyncio.get_running_loop().time() + self._wake_window_s
                lang = str(env.data.get("language", "") or "pl")
                intent_name = str(env.data.get("intent"))
                if intent_name == "music_now_playing":
                    await self._answer_now_playing(lang)
                elif intent_name == "music_shuffle":
                    await self._shuffle_queue(lang)
                elif intent_name == "voice_learn":
                    await self._start_voice_training(lang)
                else:
                    await self._start_memo_capture(lang)
                patch["last_command"] = {
                    "intent": env.data.get("intent"), "result": env.data.get("intent")}
            else:
                if await self._maybe_ask_confirmation(env):
                    patch["last_command"] = {
                        "intent": env.data.get("intent"),
                        "result": "confirm_pending",
                    }
                    await self._state.update(patch)
                    return
                if str(env.data.get("intent", "")) in ("news_brief", "sport_brief"):
                    # Live web research (GPT web_search) blocks for tens of
                    # seconds. Narrate the wait (user request 2026-08-21):
                    # announce the search up front, then a periodic "still
                    # searching" — silence here reads as "no reaction". The
                    # dispatch runs off-loop so wake events, state and the
                    # half-duplex machinery stay alive meanwhile.
                    if not self._radio.playing:
                        await self._speak(self._cues["searching"])
                    task = asyncio.ensure_future(
                        asyncio.to_thread(self._dispatch_intent, env))
                    for _ in range(8):  # research transport caps at 150 s
                        try:
                            reply = await asyncio.wait_for(
                                asyncio.shield(task), timeout=25.0)
                            break
                        except TimeoutError:
                            if not self._radio.playing:
                                await self._speak(self._cues["still_searching"])
                    else:
                        reply = await task
                else:
                    reply = self._dispatch_intent(env)
                # Acting on a command keeps the conversation open for follow-ups.
                self._awake_until = asyncio.get_running_loop().time() + self._wake_window_s
                if reply is not None:
                    action = str(reply.data.get("action", ""))
                    # A volume command already hit the mixer; while the player holds
                    # the Jabra PCM, DON'T speak the confirmation — bringing audio-out
                    # up would seize the device and kill the stream. The loudness
                    # change is its own feedback. (Fixes "głośniej silences the book".)
                    if reply.data.get("volume_only") and self._radio.playing:
                        pass
                    else:
                        # Playback actions ARE the feedback (the stream/track starting
                        # or stopping), so don't also speak the confirmation over the
                        # Jabra it needs — a spoken reply brings audio-out UP and grabs
                        # the single output PCM out from under the player (silent death).
                        # Non-playback replies (volume, time, …) are spoken via TTS.
                        if action not in ("radio_play", "radio_stop", "music_play", "music_stop"):
                            text_len = len(str(reply.data.get("text", "")))
                            # Long tool reply (book menu, news digest): its XTTS
                            # render takes seconds — announce the wait first.
                            if text_len >= _WORKING_CUE_MIN_CHARS:
                                await self._speak_working_cue()
                            # Warm audio-out (subscribe to tts.frame) BEFORE publishing,
                            # or a cache-rendered confirmation races ahead of the speaker
                            # and is lost (short reply vanishes / long one clips).
                            await self._prepare_speaker(text_len)
                            await self._publisher.publish(reply)
                        # Execute the reply's action INLINE — a fast-path reply is never
                        # received back as an incoming brain.reply, so play/stop the
                        # stream (or raise audio-out for a spoken reply) here.
                        await self._act_on_reply(reply.data)
                    patch["last_command"] = {
                        "intent": env.data.get("intent"),
                        "result": reply.data.get("action"),
                    }
                # Keep the language pin authoritative in state (scenario 09).
                patch["languages"] = {"pinned": self._dispatcher.pinned_language()}
        # Radio + spoken replies from the brain (LLM). The fast-path dispatcher
        # (nlu.intent, above) calls the SAME _act_on_reply, since its reply is
        # published to TTS but never loops back here as an incoming brain.reply.
        if env.topic == "brain.reply":
            self._cancel_heartbeat()  # the answer arrived — the wait is over
            if self._drop_next_reply:
                # "Dżesika, stop" cancelled this turn while the LLM was still
                # generating — swallow the late answer instead of speaking it.
                # A stop that interrupts an answer is ALSO a negative training
                # stimulus (2026-09-21): the answer was unwanted.
                self._drop_next_reply = False
                self._learn_event("negative_reaction", "stop", env.data)
                log.info("dropping cancelled brain reply: %r",
                         str(env.data.get("text", ""))[:60])
            elif (str(env.data.get("action", "")) == "reminder"
                  and (env.data.get("payload") or {}).get("fired")
                  and self._radio.playing):
                # An alarm/reminder fired while a stream owns the Jabra: tts's
                # direct copy raced the player and was lost (audio-out can't
                # start while the player holds the PCM). Alarms outrank
                # playback — stop the stream and re-speak through the managed
                # path so the budzik is actually HEARD (alarms 2026-08-21).
                await asyncio.to_thread(self._radio.stop)
                await asyncio.sleep(0.5)
                await self._speak(str(env.data.get("text", "")),
                                  str(env.data.get("language", "pl")))
                log.info("reminder fired over playback — stream stopped, re-spoken")
            else:
                await self._act_on_reply(env.data)
                # Conversation mode (2026-08-30): after an LLM chat reply, arm
                # a wake-less follow-up window so the user can answer in one
                # breath. Runs as its own task — waiting for the speech to
                # finish must not stall this envelope loop.
                if (self._followup_enabled and str(env.data.get("action")) == "chat"
                        and env.data.get("text") and not self._radio.playing):
                    if self._followup_task is not None and not self._followup_task.done():
                        self._followup_task.cancel()
                    # HOT follow-up (2026-09-08): when the LLM reply awaits an
                    # answer, listen longer and more sensitively.
                    self._followup_task = asyncio.create_task(self._arm_followup(
                        hot=bool(env.data.get("awaits_reply"))))
                # Closed command list (2026-09-08): the brain says it did not
                # understand — speak the cue with the same rate limit as the
                # asr.no_text path, then stop; no LLM guessing.
                if str(env.data.get("action")) == "negative_reaction":
                    # Emotional pushback (2026-09-21): go quiet NOW — close
                    # the follow-up window so the conversation ends, and keep
                    # the utterance as a negative training stimulus tied to
                    # whatever was just said/done.
                    self._followup_until = 0.0
                    if (self._followup_task is not None
                            and not self._followup_task.done()):
                        self._followup_task.cancel()
                    self._learn_event(
                        "negative_reaction",
                        str((env.data.get("payload") or {}).get("transcript", "")),
                        env.data)
                    log.info("negative reaction — conversation closed quietly")
                if str(env.data.get("action")) == "not_understood":
                    # Learning label: an owner utterance the closed list did
                    # not match (the clip text rides along via peek).
                    self._learn_event("not_understood", "", env.data)
                    if (self._earcons.get("error_tone")
                            and not self._radio.playing
                            and not await self._register_miss()):
                        now = asyncio.get_running_loop().time()
                        if (now - self._not_understood_cue_at
                                >= _NOT_UNDERSTOOD_CUE_COOLDOWN_S):
                            self._not_understood_cue_at = now
                            await self._speak(self._cues["not_understood"])
                elif (env.data.get("text")
                      and str(env.data.get("action")) not in ("wake", "asleep",
                                                              "error")):
                    # A real answer (chat/tool) — the utterance was understood;
                    # a bare wake ack ("Tak? Słucham.") doesn't count.
                    self._note_understood(
                        str(env.data.get("transcript", "")), env.data)
        if env.topic == "nlu.miss" and self._confirm_pending is not None:
            # Free text while a confirmation is pending: yes/no/emotional or
            # unrelated babble (= no confirmation → cancel). The brain is
            # gated by the confirm-pending marker, so no double reply.
            await self._resolve_confirmation(env)
        if env.topic == "error" and env.data.get("code") == "asr.followup_empty":
            # Silence after the reply — the exchange is over. Close quietly:
            # nagging "Słucham?" after every answer would be noise.
            self._followup_until = 0.0
            if self._confirm_pending is not None:
                stored = self._clear_confirmation() or {}
                self._learn_event(
                    "unconfirmed",
                    str((stored.get("data") or {}).get("transcript", "")),
                    stored.get("data") or {})
                log.info("confirm: silence — cancelled")
            log.info("follow-up window closed (silence)")
        # State cue: heard a sound after "dżesika" but no intelligible words. Tell
        # the (blind) user rather than going silent — but only when we were awake
        # (a wake fired, a command was expected) and nothing is playing over the
        # Jabra. Gated by audio.yaml earcons.error_tone.
        if env.topic == "error" and str(env.data.get("code", "")).startswith("asr."):
            self._cancel_heartbeat()  # the turn ended in an error cue, not a reply
        if (env.topic == "error" and env.data.get("code") == "asr.no_text"
                and self._earcons.get("error_tone") and self._awake()
                and not self._radio.playing
                # Only a REAL-SPEECH miss (whisper heard words, confidence too
                # low) chains toward the repeat-to-learn request; hallucination
                # and empty-window drops are false-wake noise — asking an empty
                # room to "say dżesika again" would be the old cue chant back.
                and not (env.data.get("reason") == "low_conf"
                         and await self._register_miss())):
            now = asyncio.get_running_loop().time()
            if now - self._not_understood_cue_at >= _NOT_UNDERSTOOD_CUE_COOLDOWN_S:
                self._not_understood_cue_at = now
                await self._speak(self._cues["not_understood"])
        # State cue: wake fired but the capture window came back empty — nothing was
        # said (or too quiet/far). Prompt "Słucham?" so a blind user knows Jessica is
        # still waiting, instead of dead air. Rate-limited (the wake model over-fires,
        # so an empty window is common); same gating as the not-understood cue.
        if (env.topic == "error" and env.data.get("code") == "asr.no_speech"
                and self._earcons.get("error_tone") and self._awake()
                and not self._radio.playing):
            now = asyncio.get_running_loop().time()
            if now - self._listening_cue_at >= _LISTENING_CUE_COOLDOWN_S:
                self._listening_cue_at = now
                await self._speak(self._cues["listening"])
        if env.topic == "health.status":
            # Mirror the level into state on every verdict (the orchestrator is
            # the dominant state writer); recovery details only on a fault.
            patch["health"] = {
                "level": env.data.get("level", "ok"),
                "unit": env.data.get("unit", "system"),
                "action": env.data.get("action", "none"),
            }
            recovery = await self._announce_recovery(env)
            if recovery is not None:
                patch["recovery"] = recovery
        if self._led.observe(env.topic, env.data):
            self._led.write()
            self._hwled.set_pixels(self._led.leds)  # paint the 3 phase LEDs
            patch["led"] = self._led.leds
        await self._state.update(patch)
        await self._publisher.publish(
            system_event(source="blazend-orchestrator", kind="observed", detail=env.topic)
        )

    def _amixer(self, level: str) -> None:
        """Set the Jabra playback volume (level like '27%'). Best-effort.
        An empty mixer card (desktop default-ALSA profile) skips entirely."""
        if not _JABRA_CARD:
            return
        try:
            subprocess.run(
                ["amixer", "-c", _JABRA_CARD, "set", _JABRA_MIXER, level],
                capture_output=True, timeout=4, check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("amixer set %s failed: %s", level, exc)

    async def _set_volume(self, pct: int) -> None:
        """Apply an output volume (0-100 %) to the Jabra speaker."""
        await asyncio.to_thread(self._amixer, f"{max(0, min(100, int(pct)))}%")

    async def _duck_on(self) -> None:
        """Lower the Jabra so a command is heard over a playing stream."""
        self._ducked = True
        await self._set_volume(_DUCK_PCT)
        log.info("duck → %d%% (listening over playback)", _DUCK_PCT)

    async def _duck_off(self) -> None:
        """Restore the Jabra to the user's volume after a duck."""
        self._ducked = False
        await self._set_volume(self._volume_pct)
        log.info("duck restored → %d%%", self._volume_pct)

    def _arm_duck_restore(self) -> None:
        """(Re)arm the timer that un-ducks the stream if no command follows."""
        if self._duck_task is not None and not self._duck_task.done():
            self._duck_task.cancel()
        self._duck_task = asyncio.ensure_future(self._duck_restore_later())

    def _cancel_duck_restore(self) -> None:
        if self._duck_task is not None and not self._duck_task.done():
            self._duck_task.cancel()
        self._duck_task = None

    async def _duck_restore_later(self) -> None:
        try:
            await asyncio.sleep(_DUCK_WINDOW_S)
        except asyncio.CancelledError:
            return
        # No radio command followed the wake → treat it as a false (echo) wake
        # and bring the volume back up. Restore even when nothing plays anymore:
        # a stream that died/ended inside the window used to strand the Jabra
        # at _DUCK_PCT — "Jessica ściszyła się do minimum" (2026-09-18).
        if self._ducked:
            await self._duck_off()

    def _foreign_voice_block(self, intent: str, data: dict[str, Any]) -> bool:
        """Speaker policy (2026-09-08): True when this intent must be DROPPED
        because the voice is not the owner's. Only with `speaker-id.yaml
        enforce: true`; utterances without speaker fields are trusted (no
        model/profile — pre-feature behaviour); the allow_any_voice list
        (stop, volume…) is never blocked. In log-only mode the would-block
        case is logged so the threshold can be proven from the journal."""
        owner = data.get("speaker_owner")
        if owner is not False or intent in self._speaker_allow_any:
            return False
        sim = data.get("speaker_sim")
        if not self._speaker_enforce:
            log.info("speaker: foreign voice (sim=%s) — would drop %r "
                     "(enforce off)", sim, intent)
            return False
        log.info("speaker: foreign voice (sim=%s) — dropping %r", sim, intent)
        return True

    async def _notify_foreign_voice(self) -> None:
        """The soft "plumknięcie" — a quiet low blip telling the room a voice
        was heard but not obeyed (user request 2026-09-08). Rate-limited so a
        chatty radio can't turn it into a metronome; gated by audio.yaml
        earcons.foreign_voice."""
        if not self._earcons.get("foreign_voice", True):
            return
        now = asyncio.get_running_loop().time()
        if now - self._foreign_cue_at < _FOREIGN_CUE_COOLDOWN_S:
            return
        self._foreign_cue_at = now
        await self._play_beep(self._foreign_blip_wav)

    # -- uncertain-action confirmation + learning labels (2026-09-08) -------
    def _learn_event(self, outcome: str, text: str, data: dict[str, Any],
                     corrected: str | None = None) -> None:
        """Append one label to ``<state>/learning/events.jsonl`` and keep a
        copy of the utterance audio when the ASR clip is still fresh. Every
        confirmation outcome, foreign-voice discard and not-understood miss
        is a training example (wake negatives, lexicon corrections, speaker
        profile). Best-effort — learning must never break the voice path."""
        try:
            import shutil

            from blazend.domains.context.adapters.rpi5.memory import MemoryStore

            root = _state_root() / "learning"
            clips = root / "clips"
            clips.mkdir(parents=True, exist_ok=True)
            clip_path = ""
            peek = MemoryStore().peek_last_clip()
            if peek and peek.get("path"):
                src = Path(str(peek["path"]))
                if src.exists():
                    dest = clips / src.name
                    if not dest.exists():
                        shutil.copy2(src, dest)
                    clip_path = str(dest)
                    for stale in sorted(clips.glob("clip-*.wav"))[:-200]:
                        stale.unlink(missing_ok=True)
            event = {
                "ts": datetime.now(UTC).isoformat(),
                "outcome": outcome,
                "text": text,
                "intent": data.get("intent", ""),
                "confidence": data.get("confidence"),
                "speaker_sim": data.get("speaker_sim"),
                "corrected": corrected,
                "clip": clip_path,
            }
            with (root / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception as exc:  # noqa: BLE001
            log.debug("learning event write failed: %s", exc)

    def _confirmable(self, intent: str) -> bool:
        """Intents the confirmation dialog applies to: real actions — not the
        any-voice safety set (stop/volume act instantly), not the
        orchestrator's own dialog openers, not no-op acknowledgements."""
        return intent not in self._speaker_allow_any and intent not in (
            "wake_ack", "cancel_pending", "apply_change",
            "music_now_playing", "music_shuffle", "voice_memo_record",
            "voice_learn", "go_to_sleep", "wake_up")

    async def _maybe_ask_confirmation(self, env: Envelope) -> bool:
        """True when the intent was NOT executed but deferred: whisper is not
        sure enough (confidence < asr.yaml confirm_conf), so Jessica repeats
        her understanding and asks. The user's reply resolves it in
        :meth:`_resolve_confirmation` (wake-less, via the follow-up window)."""
        conf = env.data.get("confidence")
        intent = str(env.data.get("intent", ""))
        if (conf is None or float(conf) >= self._confirm_conf
                or not self._confirmable(intent)):
            return False
        lang = str(env.data.get("language", "") or "pl")
        transcript = str(env.data.get("transcript", ""))
        try:  # echo the COMMAND, not "Dżesika, …" (2026-09-11 polish)
            from blazend.domains.ai_orchestrator.adapters.rpi5.assistant import wake

            transcript = wake.strip_wake(transcript) or transcript
        except Exception:  # noqa: BLE001
            pass
        loop_now = asyncio.get_running_loop().time()
        self._confirm_pending = {
            "data": dict(env.data), "lang": lang,
            "until": loop_now + _CONFIRM_TTL_S,
        }
        try:  # gate the brain: this turn belongs to the confirmation dialog
            (self._runtime_dir / "confirm-pending").touch()
        except OSError:
            pass
        log.info("confirm: %r (conf=%.2f < %.2f) — asking", transcript,
                 float(conf), self._confirm_conf)
        await self._speak(msg("confirm.echo_ask", lang, text=transcript), lang)
        if self._followup_task is not None and not self._followup_task.done():
            self._followup_task.cancel()
        self._followup_task = asyncio.create_task(self._arm_followup())
        return True

    def _clear_confirmation(self) -> dict[str, Any] | None:
        pending = self._confirm_pending
        self._confirm_pending = None
        (self._runtime_dir / "confirm-pending").unlink(missing_ok=True)
        return pending

    async def _resolve_confirmation(self, env: Envelope) -> bool:
        """Route the utterance that arrived while a confirmation is pending.
        Returns True when consumed; False = it is a CORRECTED command (or the
        pending lapsed) and the caller processes the envelope normally."""
        pending = self._confirm_pending
        assert pending is not None
        stored = pending["data"]
        lang = pending["lang"]
        text = str(env.data.get("transcript", ""))
        if asyncio.get_running_loop().time() > pending["until"]:
            self._clear_confirmation()
            self._learn_event("unconfirmed", str(stored.get("transcript", "")),
                              stored)
            return False  # stale — treat the new utterance as its own turn
        cls = classify_confirmation(text)
        intent = str(env.data.get("intent", "")) if env.topic == "nlu.intent" else ""
        if intent == "wake_ack":
            cls = "confirmed"  # a bare "tak" fast-paths as the ack intent
        elif intent == "cancel_pending":
            cls = "denied"
        elif intent == "stop_talking":
            cls = "denied"  # "stop" while she asks = abort the pending action
        elif intent and cls == "other":
            # A different real command: detection error WITH correction — run
            # the new command instead ("Dżesika, <komenda>" re-dictation).
            self._clear_confirmation()
            self._learn_event("corrected", str(stored.get("transcript", "")),
                              stored, corrected=text)
            log.info("confirm: corrected %r → %r",
                     stored.get("transcript"), text)
            return False
        if cls == "confirmed":
            self._clear_confirmation()
            self._learn_event("confirmed", str(stored.get("transcript", "")),
                              stored)
            log.info("confirm: confirmed — executing %r", stored.get("intent"))
            env2 = Envelope(topic="nlu.intent", source="blazend-orchestrator",
                            data={**stored, "confidence": 1.0})
            await self._on_envelope("nlu", env2)
            return True
        if cls == "denied":
            self._clear_confirmation()
            self._learn_event("denied", str(stored.get("transcript", "")), stored)
            await self._speak(msg("confirm.cancelled", lang), lang)
            return True
        if cls == "emotional":
            self._clear_confirmation()
            self._learn_event("emotional", str(stored.get("transcript", "")),
                              stored)
            await self._speak(msg("confirm.sorry", lang), lang)
            return True
        # Unrelated babble (nlu.miss, no intent): no confirmation = error.
        self._clear_confirmation()
        self._learn_event("unconfirmed", str(stored.get("transcript", "")), stored)
        log.info("confirm: no confirmation (%r) — cancelled silently", text[:40])
        return True

    async def _register_miss(self) -> bool:
        """Count one failed understanding; True when the streak reached
        ``_LEARN_REPEAT_AFTER`` and the explicit repeat-to-learn request was
        spoken (in place of the generic "Nie zrozumiałam" cue). The request
        bypasses the cue cooldown on purpose — it must be heard clearly."""
        now = asyncio.get_running_loop().time()
        if now - self._last_miss_at > _LEARN_REPEAT_HORIZON_S:
            self._miss_count = 0
        self._last_miss_at = now
        self._miss_count += 1
        if self._miss_count < _LEARN_REPEAT_AFTER:
            return False
        self._miss_count = 0
        self._repeat_armed_until = now + _LEARN_REPEAT_WINDOW_S
        # Keep listening: the user was told to speak again — the awake window
        # must outlive the prompt, not expire mid-retry.
        self._awake_until = max(self._awake_until, now + self._wake_window_s)
        log.info("repeat-to-learn: asking for wake word + command again")
        await self._speak(self._cues["learn_repeat"])
        return True

    def _note_understood(self, transcript: str, data: dict[str, Any]) -> None:
        """A command was understood: end the miss streak, and when it answers
        the repeat-to-learn request, label it as a training example."""
        self._miss_count = 0
        if asyncio.get_running_loop().time() < self._repeat_armed_until:
            self._repeat_armed_until = 0.0
            self._learn_event("repeat_after_prompt", transcript, data)
            log.info("repeat-to-learn: labelled retry %r", transcript[:60])

    def _maybe_stopcheck(self, env: Envelope) -> None:
        """Arm one wake-less ASR stop check for a short utterance heard while
        Jessica herself is making sound (stream playing / long spoken reply) —
        see _STOPCHECK_MIN_GAP_S. The strict phrase match lives in the ASR; a
        non-stop utterance is discarded there, so arming is cheap and safe."""
        if not _STOP_SENTINEL:
            return
        if not (self._radio.playing or (self._runtime_dir / "speaking").exists()):
            return
        if self._dialog_active():
            return  # memo/training/follow-up owns the mic
        dur = float(env.data.get("duration_ms", 0.0))
        if not _STOPCHECK_MIN_MS <= dur <= _STOPCHECK_MAX_MS:
            return  # a stop order is a short burst, not babble or a click
        now = asyncio.get_running_loop().time()
        if now - self._stopcheck_at < _STOPCHECK_MIN_GAP_S:
            return
        self._stopcheck_at = now
        try:
            (self._runtime_dir / "stopcheck-capture").touch()
            log.info("stop sentinel armed (vad %d ms during activity)", int(dur))
        except OSError as exc:
            log.warning("could not arm stop check: %s", exc)

    async def _act_on_reply(self, data: dict[str, Any]) -> None:
        """Execute a reply's action: play/stop the internet-radio stream, or bring
        audio-out up for a spoken reply. Barge-in stays wake-gated (see the
        wake.detected handler) — on the Jabra speakerphone the stream's own audio
        loops into the mic, so a vad.start barge-in would stop the radio it just
        started. `_radio.*` is a subprocess → run off the event loop.

        Called for BOTH brain-produced brain.reply events and fast-path dispatcher
        replies (which are published to TTS but never loop back as brain.reply)."""
        action = str(data.get("action", ""))
        # radio_play (stream url) and music_play (local file path) both hand a
        # single source to the exclusive player; the only difference is the source.
        if action in ("radio_play", "music_play"):
            payload = data.get("payload", {}) or {}
            source = str(payload.get("url") or payload.get("path", ""))
            # Remember the last source so "kontynuj" can restore it after a stop.
            self._last_source = source
            self._last_source_name = str(payload.get("name", ""))
            # Audiobook or album queue? Track it so we can auto-advance on each
            # part's natural EOF (via the player's position-file). An album
            # (`is_playlist`) rides the same chapters engine as a book, minus the
            # book-only bits: no progress slug, no attention check, music DSP.
            start_seconds, position_file = 0.0, ""
            if payload.get("is_audiobook") or payload.get("is_playlist"):
                self._book = {
                    "slug": str(payload.get("slug", "")),
                    "chapters": list(payload.get("chapters", [])),
                    "labels": list(payload.get("labels", [])),
                    "index": int(payload.get("chapter", 0)),
                    "name": self._last_source_name,
                    "kind": "album" if payload.get("is_playlist") else "book",
                }
                self._last_book = self._book  # remember for "kontynuj" after a stop
                self._book_stopping = False
                self._book_paused_for_attention = False
                self._book_activity_at = asyncio.get_running_loop().time()  # attention clock
                start_seconds = float(payload.get("start_seconds", 0.0))
                position_file = str(self._position_file)
                self._position_file.unlink(missing_ok=True)  # stale-guard
            else:
                self._book = None  # music/radio → not a book
                self._last_book = None  # resume should target this radio/music, not a stale book
            self._cancel_duck_restore()  # a real play command supersedes a duck
            # Free the Jabra output for playback; the reconciler keeps audio-out
            # down while it plays.
            self._speak_until = 0.0
            await self._apply_audio_out(False)
            await asyncio.sleep(0.3)  # let the device release
            await asyncio.to_thread(self._radio.play, source, self._last_source_name,
                                    position_file=position_file, start_seconds=start_seconds,
                                    speech=bool(payload.get("is_audiobook")))
            await self._duck_off()  # play at the user's volume
            # Suppress the VAD while it plays so the Jabra hearing its own output
            # can't trigger a barge-in ("dżesika" still interrupts via wake).
            self._speaker_busy.touch()
        elif action in ("radio_stop", "music_stop"):
            self._book_stopping = True  # a user stop → the watcher must not auto-advance
            self._save_book_progress()  # remember where we were before killing the player
            self._book = None
            self._cancel_duck_restore()
            self._ducked = False
            await asyncio.to_thread(self._radio.stop)
            self._speaker_busy.unlink(missing_ok=True)
            await self._set_volume(self._volume_pct)  # ready for next stream
        elif data.get("text"):
            # A spoken reply (LLM chat, or a non-radio command confirmation) —
            # bring audio-out up long enough to synthesise + play it.
            self._mark_speaking(len(str(data.get("text", ""))))

    # -- audiobook engine ----------------------------------------------
    def _read_position(self) -> tuple[float, bool]:
        """Read ``{seconds, done}`` the player writes to the position-file."""
        try:
            d = json.loads(self._position_file.read_text(encoding="utf-8"))
            return float(d.get("seconds", 0.0)), bool(d.get("done", False))
        except (OSError, ValueError):
            return 0.0, False

    def _save_book_progress(self) -> None:
        """Persist the current book's chapter + offset so 'czytaj dalej' resumes here."""
        if not self._book or not self._book.get("slug"):
            return
        offset, _done = self._read_position()
        self._book_progress.save(
            self._book["slug"], chapter=int(self._book["index"]), offset_s=offset,
            title=str(self._book.get("name", "")),
            updated=datetime.now(UTC).isoformat(timespec="seconds"))

    async def _speak_working_cue(self) -> None:
        """Speak "Chwileczkę." once per question when the real answer will take a
        while (LLM generation / long XTTS render), so the user hears Jessica is
        working instead of dead air. Gated by `audio.yaml earcons.thinking`, muted
        while a stream owns the Jabra, cooldown-collapsed."""
        if not self._earcons.get("thinking") or self._radio.playing:
            return
        now = asyncio.get_running_loop().time()
        if now - self._working_cue_at < _WORKING_CUE_COOLDOWN_S:
            return
        self._working_cue_at = now
        await self._speak(self._cues["working"])

    async def _speak(self, text: str, lang: str = "pl") -> None:
        """Say something proactively (attention prompt / 'finished the book')."""
        await self._prepare_speaker(len(text))  # audio-out up + subscribed before the frame
        await self._publisher.publish(Envelope(
            topic="brain.reply", source="blazend-orchestrator",
            data={"language": lang, "text": text, "chunk": text, "final_": True,
                  "action": "system.notice"}))

    async def _play_book_chapter(self, book: dict[str, Any], index: int,
                                 start_seconds: float = 0.0) -> None:
        """Play chapter ``index`` of ``book`` (auto-advance / chapter nav / resume)."""
        book["index"] = index
        self._book = book
        self._last_book = book  # keep resumable across a later stop
        self._book_stopping = False
        self._book_paused_for_attention = False
        self._book_activity_at = asyncio.get_running_loop().time()
        self._position_file.unlink(missing_ok=True)
        self._last_source = str(book["chapters"][index])
        self._last_source_name = str(book.get("name", ""))
        await self._apply_audio_out(False)
        await asyncio.sleep(0.3)
        await asyncio.to_thread(self._radio.play, self._last_source, self._last_source_name,
                                position_file=str(self._position_file), start_seconds=start_seconds,
                                speech=book.get("kind", "book") != "album")  # book = spoken-word DSP
        self._speaker_busy.touch()
        log.info("%s part %d/%d — %s", book.get("kind", "book"), index + 1,
                 len(book["chapters"]), self._last_source_name)

    async def _attention_present(self) -> None:
        """The listener replied to 'Czy słuchasz?' → resume the paused book."""
        self._awaiting_attention = False
        self._book_activity_at = asyncio.get_running_loop().time()
        if self._book_paused_for_attention and self._book is not None:
            self._book_paused_for_attention = False
            prog = self._book_progress.get(str(self._book.get("slug", ""))) or {}
            await self._play_book_chapter(self._book, int(self._book["index"]),
                                          start_seconds=float(prog.get("offset_s", 0.0)))

    async def _book_watcher(self) -> None:
        """Drive a playing audiobook: auto-advance chapters on natural EOF, and run
        the attention check (pause + ask; resume on reply, stay stopped on silence)."""
        while not self._stop.is_set():
            await asyncio.sleep(1.0)
            book = self._book
            if book is None:
                continue
            now = asyncio.get_running_loop().time()
            if self._awaiting_attention:
                if now >= self._attention_deadline:  # no reply → asleep → stay stopped
                    self._awaiting_attention = False
                    self._book_paused_for_attention = False
                    self._book = None  # position already saved at the pause
                    await self._speak(msg("book.stopped_attention", self._default_lang))
                continue
            if self._book_paused_for_attention:
                continue
            if self._radio.playing:
                # Attention checks are a BOOK feature (losing your place in a
                # story matters); interrupting an album with "Czy słuchasz?" is
                # just annoying — music plays through.
                if (self._attention_enabled and book.get("kind", "book") != "album"
                        and self._book_activity_at
                        and now - self._book_activity_at >= self._attention_interval_s):
                    self._save_book_progress()          # remember before pausing
                    self._book_paused_for_attention = True
                    self._awaiting_attention = True
                    self._attention_deadline = now + self._attention_window_s
                    self._book_stopping = True           # suppress auto-advance while paused
                    await asyncio.to_thread(self._radio.stop)
                    self._speaker_busy.unlink(missing_ok=True)  # mic un-gated for the reply
                    await self._speak(msg("book.attention_check", self._default_lang))
                    (self._runtime_dir / "activate").touch()  # open a listen window
                    self._awake_until = now + self._attention_window_s  # accept the reply
                continue
            if self._book_stopping:
                continue
            # player exited on its OWN (chapter EOF) → auto-advance / finish
            _offset, done = self._read_position()
            nxt = int(book["index"]) + 1
            if done and nxt < len(book["chapters"]):
                await self._play_book_chapter(book, nxt)
            else:
                if done:  # ran off the end of the last chapter/track → finished
                    self._last_book = None  # finished → nothing to resume
                    if book.get("kind", "book") == "album":
                        await self._speak(msg("album.finished", self._default_lang))
                    else:
                        self._book_progress.clear(str(book.get("slug", "")))
                        await self._speak(msg("book.finished", self._default_lang))
                self._book = None

    def _describe_playback(self, lang: str) -> str:
        """One spoken sentence about what's on the speaker right now."""
        if not self._radio.playing:
            return msg("playback.nothing", lang)
        book = self._book
        if book is not None:
            idx, n = int(book["index"]) + 1, len(book["chapters"])
            if book.get("kind") == "album":
                track = _queue_label(book, int(book["index"]))
                return msg("playback.album_track", lang,
                           track=track, album=book["name"], idx=idx, n=n)
            return msg("playback.book_chapter", lang,
                       book=book["name"], idx=idx, n=n)
        name = str(self._last_source_name or "")
        if name:
            return msg("playback.source", lang, name=name)
        return msg("playback.radio", lang)

    async def _wait_speech_done(self) -> None:
        """Wait until the just-published spoken reply has actually played out:
        first for TTS frames to reach audio-out (speaker-busy appears), then for
        the queue to drain. Bounded, so a lost frame can't wedge playback."""
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        while loop.time() - t0 < 10.0 and not self._speaker_busy.exists():
            await asyncio.sleep(0.2)
        while loop.time() - t0 < 30.0 and self._speaker_busy.exists():
            await asyncio.sleep(0.3)
        self._speak_until = 0.0  # answer done — release the speak window early

    async def _speak_over_playback(self, text: str, lang: str = "pl") -> None:
        """Speak even while the exclusive player holds the Jabra: audio-out
        stays down during a stream, so a plain reply would vanish. Pause the
        stream at its offset, say it, resume right where it left off (the queue
        engine's position-file makes the resume mid-track). Idle → just speak."""
        if not self._radio.playing:
            await self._speak(text, lang)
            return
        book = self._book
        offset, _done = self._read_position()
        self._book_stopping = True  # the watcher must not read this stop as EOF
        await asyncio.to_thread(self._radio.stop)
        self._speaker_busy.unlink(missing_ok=True)
        await self._speak(text, lang)
        await self._wait_speech_done()
        if book is not None:  # album/book → resume mid-track via the queue engine
            await self._play_book_chapter(book, int(book["index"]), start_seconds=offset)
        elif self._last_source:  # live stream / single track → restart it
            await self._act_on_reply({"action": "music_play", "payload": {
                "path": self._last_source, "name": self._last_source_name}})

    async def _answer_now_playing(self, lang: str) -> None:
        """"Co teraz gra?" — say what's on, then give the speaker back."""
        await self._speak_over_playback(self._describe_playback(lang), lang)

    _VOICE_TRAIN_ROUNDS = 5
    _VOICE_TRAIN_DIR = _state_root() / "voice-training"

    async def _start_voice_training(self, lang: str) -> None:
        """"Naucz się mojego głosu" (2026-08-22 — recalibration for a new
        owner): a guided dialog that captures five wake-word samples into
        `/var/lib/blazen/voice-training/`. Training itself runs off-device
        (train-wake.py on the dev rig, samples pulled over the tailnet); this
        collects the on-device half and thanks the owner. Reuses the memo
        capture window; a playing stream is stopped first."""
        if self._radio.playing:
            await self._act_on_reply({"action": "music_stop"})
        self._voice_train_round = 1
        self._voice_train_stage = None  # a stale name stage never survives a restart
        self._voice_train_retries = 0
        self._memo_lang = lang
        await self._speak(msg("train.start", lang), lang)
        await self._wait_speech_done()
        self._open_memo_window()

    async def _on_voice_sample(self, data: dict[str, Any]) -> None:
        """One voice-training capture came back: keep the wav, narrate the
        round count, and either open the next window or thank and finish."""
        path = str(data.get("audio_path", ""))
        transcript = str(data.get("transcript", ""))
        lang = str(data.get("language", "") or self._memo_lang or "pl")
        n = self._voice_train_round
        self._voice_train_retries = 0  # a real capture resets the empty streak
        try:
            self._VOICE_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
            if path:
                Path(path).rename(
                    self._VOICE_TRAIN_DIR / f"wake-{datetime.now():%Y%m%d-%H%M%S}-{n}.wav")
        except OSError as exc:
            log.warning("voice-training sample %d not saved: %s", n, exc)
        log.info("voice-training sample %d/%d (%r)", n, self._VOICE_TRAIN_ROUNDS,
                 transcript[:40])
        if n >= self._VOICE_TRAIN_ROUNDS:
            self._voice_train_round = 0
            # Learning the voice includes learning how to address its owner
            # (user request 2026-08-27): one more captured answer, stored in
            # the profile ({name} in the greeting; never hardcoded).
            self._voice_train_stage = "name"
            await self._speak(msg("train.thanks_ask_address", lang), lang)
            await self._wait_speech_done()
            self._open_memo_window()
            return
        self._voice_train_round = n + 1
        left = self._VOICE_TRAIN_ROUNDS - n
        await self._speak(
            msg("train.next_round", lang,
                left=table("train_rounds_left", left, lang, default=str(left))),
            lang)
        await self._wait_speech_done()
        self._open_memo_window()

    async def _on_voice_train_name(self, data: dict[str, Any]) -> None:
        """The answer to "jak mam się do ciebie zwracać?" — the voice-training
        finale (2026-08-27). Stores the address in the profile (the {name} the
        greeting and replies use — never hardcoded) and keeps the wav with the
        other training samples: it is the owner saying their own name, prime
        voice-ID material."""
        transcript = str(data.get("transcript", ""))
        lang = str(data.get("language", "") or self._memo_lang or "pl")
        path = str(data.get("audio_path", ""))
        try:
            if path:
                self._VOICE_TRAIN_DIR.mkdir(parents=True, exist_ok=True)
                Path(path).rename(
                    self._VOICE_TRAIN_DIR / f"name-{datetime.now():%Y%m%d-%H%M%S}.wav")
        except OSError as exc:
            log.warning("voice-training name sample not saved: %s", exc)
        name = _extract_address(transcript)
        if not name:
            # One retry, then finish politely keeping the stored address.
            self._voice_train_retries += 1
            if self._voice_train_retries > 1:
                self._voice_train_stage = None
                self._voice_train_retries = 0
                await self._speak(msg("train.name_keep", lang), lang)
                return
            await self._speak(msg("train.name_retry", lang), lang)
            await self._wait_speech_done()
            self._open_memo_window()
            return
        self._voice_train_stage = None
        self._voice_train_retries = 0
        from blazend.domains.context.adapters.rpi5.memory import MemoryStore
        try:
            MemoryStore().set_profile("name", name, now=datetime.now())
        except OSError as exc:
            log.warning("voice-training: profile name store failed: %s", exc)
        log.info("voice-training: owner address stored (%r)", name)
        # The training set just grew — tell the ASR to (re)build the owner's
        # speaker-ID profile from it (speaker.py enrollment, 2026-09-08).
        try:
            (self._runtime_dir / "speaker-enroll").touch()
        except OSError as exc:
            log.warning("could not arm speaker enrollment: %s", exc)
        await self._speak(msg("train.name_saved", lang, name=name), lang)

    async def _start_memo_capture(self, lang: str) -> None:
        """"Nagraj notatkę" — a two-step Polish dialog (user request
        2026-08-04): ask for the TITLE, capture it, ask for the CONTENT,
        capture that; the memo stores the content wav + transcript under the
        spoken title. A playing stream is stopped first: dictation needs both
        the speaker (prompts + confirmation) and an un-ducked mic. Each capture
        comes back as ``system.event kind=memo_recorded`` and
        :meth:`_on_memo_recorded` advances the dialog."""
        if self._radio.playing:
            await self._act_on_reply({"action": "music_stop"})
        self._memo_stage = "title"
        self._memo_title = ""
        self._memo_lang = lang
        await self._speak(msg("memo.ask_title", lang), lang)
        await self._wait_speech_done()
        self._open_memo_window()

    async def _arm_followup(self, *, hot: bool = False) -> None:
        """Open the conversation follow-up window once the spoken reply has
        actually played out: soft chime ("I'm still listening"), then the
        followup-capture marker for the ASR. Blind-first: the chime is the
        only cue, so it reuses the familiar wake chime.

        ``hot`` (2026-09-08): the LLM reply ends in a question / invitation —
        it AWAITS the user. Listen longer (the user is composing an answer)
        and tell the ASR to capture more sensitively (hot marker: lower
        confidence floor, longer lead-in)."""
        try:
            await self._wait_speech_done()
            if self._radio.playing:
                return  # a stream took the speaker meanwhile — no follow-up
            if self._wake_chime_armed():
                await self._play_wake_beep()
            now = asyncio.get_running_loop().time()
            self._followup_until = now + (
                _FOLLOWUP_HOT_TIMEOUT_S if hot else _FOLLOWUP_TIMEOUT_S)
            self._awake_until = max(self._awake_until, now + self._wake_window_s)
            marker = "followup-capture-hot" if hot else "followup-capture"
            try:
                (self._runtime_dir / marker).touch()
            except OSError as exc:
                log.warning("could not open follow-up window: %s", exc)
                self._followup_until = 0.0
        except asyncio.CancelledError:
            pass

    async def _ambient_monitor(self) -> None:
        """Secondary-mic room sensor (2026-08-31, paul: the built-in ALC269VC
        dedicated to Jessica). Config-gated by ``audio.yaml ambient_mic.device``
        — absent/empty (the Pi has no such mic) the task exits immediately.
        Measures the room ONLY while the speaker is idle and no dialog is
        pending (never her own voice/stream), keeps ``ambient.rms`` in
        state.json, and optionally nudges the media volume toward the room
        level (auto_volume; applied between playbacks, never mid-stream)."""
        try:
            cfg = load_config("audio").data.get("ambient_mic", {}) or {}
        except Exception:  # noqa: BLE001
            return
        dev = str(cfg.get("device", "") or "")
        if not dev:
            return
        interval = float(cfg.get("interval_s", 30))
        sample_s = float(cfg.get("sample_s", 2))
        av = cfg.get("auto_volume", {}) or {}
        log.info("ambient monitor on %s (every %.0fs)", dev, interval)
        while not self._stop.is_set():
            await asyncio.sleep(interval)
            if (self._speaker_busy.exists()
                    or (self._runtime_dir / "speaking").exists()
                    or self._radio.playing or self._dialog_active()):
                continue  # her own audio would poison the room measurement
            rms = await asyncio.to_thread(self._measure_ambient, dev, sample_s)
            if rms is None:
                continue
            self._ambient_rms = rms
            await self._state.update({"ambient": {"rms": round(rms, 1)}})
            if bool(av.get("enabled", False)) and not self._radio.playing:
                new = _ambient_target_pct(
                    rms, self._volume_pct,
                    quiet_rms=float(av.get("quiet_rms", 40)),
                    loud_rms=float(av.get("loud_rms", 800)),
                    min_pct=int(av.get("min_pct", 40)),
                    max_pct=int(av.get("max_pct", 100)),
                    step_pct=int(av.get("step_pct", 10)))
                if new != self._volume_pct:
                    self._volume_pct = new
                    await asyncio.to_thread(self._amixer, f"{new}%")
                    log.info("ambient auto-volume → %d%% (room rms %.0f)", new, rms)

    @staticmethod
    def _measure_ambient(device: str, seconds: float) -> float | None:
        """One room sample: record ``seconds`` of mono 16 kHz from ``device``
        and return the MEDIAN of 200 ms block RMS values — the median rides
        out keyboard clicks and other transients that wreck a plain RMS."""
        import numpy as np

        try:
            out = subprocess.run(
                ["arecord", "-q", "-D", device, "-f", "S16_LE", "-r", "16000",
                 "-c", "1", "-d", str(max(1, int(seconds))), "-t", "raw", "-"],
                capture_output=True, timeout=seconds + 5, check=True)
        except (OSError, subprocess.SubprocessError):
            return None
        pcm = np.frombuffer(out.stdout, dtype=np.int16).astype(np.float64)
        if len(pcm) < 3200:
            return None
        block = 3200  # 200 ms @ 16 kHz
        blocks = [pcm[i:i + block] for i in range(0, len(pcm) - block + 1, block)]
        rms = sorted(float(np.sqrt((b ** 2).mean())) for b in blocks)
        return rms[len(rms) // 2]

    def _dialog_active(self) -> bool:
        """True while a guided dialog (voice training rounds, the address
        question, memo title/content dictation) OR a conversation follow-up
        window is awaiting a capture — wake activation is suppressed for its
        duration. Past ``_dialog_until`` the dialog is declared abandoned
        (ASR never answered): its state is dropped so Jessica goes back to
        normal listening instead of staying deaf to her own name."""
        if asyncio.get_running_loop().time() < self._followup_until:
            return True
        if not (self._voice_train_round or self._voice_train_stage
                or self._memo_stage is not None):
            return False
        if asyncio.get_running_loop().time() > self._dialog_until:
            log.warning("guided dialog timed out — re-enabling wake activation")
            self._voice_train_round = 0
            self._voice_train_stage = None
            self._voice_train_retries = 0
            self._memo_stage = None
            self._memo_title = ""
            return False
        return True

    def _open_memo_window(self) -> None:
        # Every (re)opened capture window extends the guided-dialog deadline —
        # multi-round training keeps the suppression alive round to round.
        self._dialog_until = asyncio.get_running_loop().time() + _DIALOG_TIMEOUT_S
        try:
            (self._runtime_dir / "memo-capture").touch()
        except OSError as exc:
            log.warning("could not start memo capture: %s", exc)
            self._memo_stage = None

    async def _on_memo_recorded(self, data: dict[str, Any]) -> None:
        """Advance the memo dialog: a TITLE capture asks for the content next;
        a CONTENT capture stores the memo (audio + transcript + title) and
        confirms audibly with the title — the blind-first proof the right
        thing was recorded. Embedding into the semantic index happens lazily
        in the brain (mtime-triggered backfill)."""
        if self._voice_train_round:
            # A voice-training capture, not a memo — route to the trainer.
            await self._on_voice_sample(data)
            return
        if self._voice_train_stage == "name":
            await self._on_voice_train_name(data)
            return
        path = str(data.get("audio_path", ""))
        transcript = str(data.get("transcript", ""))
        lang = str(data.get("language", "") or self._memo_lang or "pl")
        if self._memo_stage == "title":
            # The title is text-only — its wav was a means to an end.
            try:
                Path(path).unlink(missing_ok=True)
            except OSError:
                pass
            self._memo_title = " ".join(transcript.split()[:8]).strip(" ,.?!")
            self._memo_stage = "content"
            prompt = msg("memo.ask_content" if self._memo_title
                         else "memo.ask_content_no_title", lang)
            await self._speak(prompt, lang)
            await self._wait_speech_done()
            self._open_memo_window()
            return
        # Content stage (or a stray memo event): store + confirm.
        from blazend.domains.context.adapters.rpi5.memory import MemoryStore
        title = self._memo_title
        self._memo_stage = None
        self._memo_title = ""
        try:
            MemoryStore().add_voice_note(
                path, now=datetime.now(),
                duration_s=float(data.get("duration_s", 0.0) or 0.0),
                transcript=transcript, title=title)
        except OSError as exc:
            log.warning("voice memo store failed: %s", exc)
            return
        spoken_label = title or " ".join(transcript.split()[:6])
        if spoken_label:
            confirmation = msg("memo.saved", lang, label=spoken_label)
        else:
            confirmation = msg("memo.saved_no_words", lang)
        await self._speak(confirmation, lang)

    async def _shuffle_queue(self, lang: str) -> None:
        """"Przetasuj" — reshuffle the REST of the playing queue; the current
        track finishes normally (it moves to slot 0 of the reordered queue).
        Confirmation names the upcoming track — audible proof the shuffle
        happened, which a silent reorder wouldn't give a blind user."""
        book = self._book
        if not self._radio.playing or book is None or book.get("kind") != "album":
            await self._speak_over_playback(msg("shuffle.none", lang), lang)
            return
        chapters = list(book["chapters"])
        idx = int(book["index"])
        # Labels ride along: shuffle (path, label) PAIRS so position i keeps
        # naming the file at position i after the reorder.
        labels = list(book.get("labels") or [])
        labels += [""] * (len(chapters) - len(labels))
        pairs = [pl for i, pl in enumerate(zip(chapters, labels, strict=True)) if i != idx]
        random.shuffle(pairs)
        book["chapters"] = [chapters[idx], *[p for p, _ in pairs]]
        book["labels"] = [labels[idx], *[label for _, label in pairs]]
        book["index"] = 0
        if pairs:
            confirmation = msg("shuffle.done_next", lang, next=_queue_label(book, 1))
        else:
            confirmation = msg("shuffle.done", lang)
        await self._speak_over_playback(confirmation, lang)

    def _album_nav(self, delta: int) -> Envelope:
        """Jump ±1 track in the now-playing album queue (or say why we can't).
        Mirrors _chapter_nav; the returned music_play envelope flows through
        _act_on_reply and rebuilds the queue state at the new index."""
        def spoken(msg: str) -> Envelope:
            return Envelope(topic="brain.reply", source="blazend-orchestrator",
                            data={"language": "pl", "text": msg, "chunk": msg,
                                  "final_": True, "action": "command.track"})
        book = self._book
        assert book is not None  # caller gates on an active album queue
        chapters = list(book["chapters"])
        idx = int(book["index"]) + delta
        if idx < 0:
            return spoken(msg("nav.first_track", "pl"))
        if idx >= len(chapters):
            return spoken(msg("nav.last_track", "pl"))
        return Envelope(topic="brain.reply", source="blazend-orchestrator",
                        data={"action": "music_play", "payload": {
                            "path": chapters[idx], "name": str(book["name"]),
                            "is_playlist": True, "chapters": chapters, "chapter": idx,
                            "labels": list(book.get("labels") or [])}})

    def _chapter_nav(self, delta: int) -> Envelope:
        """Jump ±1 chapter in the now-playing book (or say why we can't)."""
        def spoken(msg: str) -> Envelope:
            return Envelope(topic="brain.reply", source="blazend-orchestrator",
                            data={"language": "pl", "text": msg, "chunk": msg,
                                  "final_": True, "action": "command.chapter"})
        if self._book is None or self._book.get("kind", "book") == "album":
            return spoken(msg("nav.no_book", "pl"))
        chapters = list(self._book["chapters"])
        idx = int(self._book["index"]) + delta
        if idx < 0:
            return spoken(msg("nav.first_chapter", "pl"))
        if idx >= len(chapters):
            return spoken(msg("nav.last_chapter", "pl"))
        return Envelope(topic="brain.reply", source="blazend-orchestrator",
                        data={"action": "music_play", "payload": {
                            "path": chapters[idx], "name": str(self._book["name"]),
                            "is_audiobook": True, "slug": str(self._book["slug"]),
                            "chapters": chapters, "chapter": idx, "start_seconds": 0.0}})

    def _dispatch_intent(self, env: Envelope) -> Envelope | None:
        """Act on a fast-path `nlu.intent`; return the spoken reply (if any)."""
        if self._dispatcher is None:
            return None
        intent_name = str(env.data.get("intent", ""))
        # Media transport (the dispatcher noops these — the orchestrator owns the
        # exclusive player). "kontynuj"/media_resume restores the last playback
        # (radio stream or music track — the source flows through here, so this is
        # the one place that knows both; the player has no seek, so it restarts
        # from the top). media_pause/"wstrzymaj" stops the current playback.
        if intent_name == "media_resume":
            # A book resumes at its saved chapter + offset (re-arms auto-advance /
            # attention via _act_on_reply); radio/music restarts from the top (the
            # player has no seek for a live stream).
            book = self._last_book
            if book:
                prog = self._book_progress.get(str(book.get("slug", ""))) or {}
                idx = int(prog.get("chapter", book.get("index", 0)))
                chapters = list(book.get("chapters", []))
                if 0 <= idx < len(chapters):
                    is_album = book.get("kind") == "album"  # resume keeps the queue's kind
                    return Envelope(
                        topic="brain.reply", source="blazend-orchestrator",
                        data={"action": "music_play", "payload": {
                            "path": chapters[idx], "name": str(book.get("name", "")),
                            "is_audiobook": not is_album, "is_playlist": is_album,
                            "slug": str(book.get("slug", "")),
                            "chapters": chapters, "chapter": idx,
                            "labels": list(book.get("labels") or []),
                            "start_seconds": float(prog.get("offset_s", 0.0))}},
                    )
            if self._last_source:
                return Envelope(
                    topic="brain.reply", source="blazend-orchestrator",
                    data={"action": "music_play",
                          "payload": {"path": self._last_source, "name": self._last_source_name}},
                )
            return Envelope(
                topic="brain.reply", source="blazend-orchestrator",
                data={"language": "pl", "text": "Nie mam czego wznowić.",
                      "chunk": "Nie mam czego wznowić.", "final_": True, "action": "command.resume"},
            )
        if intent_name == "media_pause" and self._radio.playing:
            return Envelope(
                topic="brain.reply", source="blazend-orchestrator",
                data={"action": "music_stop"},
            )
        # Chapter navigation ("następny/poprzedni rozdział") acts on the
        # now-playing book (the orchestrator owns that state).
        if intent_name in ("chapter_next", "chapter_prev"):
            return self._chapter_nav(1 if intent_name == "chapter_next" else -1)
        # While an ALBUM queue plays, "następny/poprzedni" steps the queue in
        # order — the dispatcher's history/random walk would abandon the album.
        if (intent_name in ("music_next", "music_prev") and self._book is not None
                and self._book.get("kind") == "album"):
            return self._album_nav(1 if intent_name == "music_next" else -1)
        result = self._dispatcher.dispatch(
            intent_name,
            env.data.get("params", {}),
            env.data.get("language", "pl"),
        )
        # Stateful context: a bare "stop"/"przestań" normally just interrupts TTS
        # (stop_talking → tts_interrupt), but while a stream plays it means STOP THE
        # STREAM — the current activity. Redirect to music_stop so "stop" over the
        # radio/music actually stops it. See stateful-command-context.
        if self._radio.playing and (result.signal == "tts_interrupt" or intent_name == "stop_talking"):
            return Envelope(
                topic="brain.reply", source="blazend-orchestrator",
                data={"action": "music_stop"},
            )
        if result.signal in ("reboot", "shutdown"):
            # In the VM / dev we never actually power off; on the device a
            # power unit consumes this. Log it loudly.
            log.warning("system command requested: %s (not executed in dev)", result.signal)
        # Volume commands (głośniej / ciszej / "ustaw głośność na N") mutate the
        # audio.volume setting; push the new level to the Jabra speaker so it's
        # actually audible (the dispatcher only updates the stored value).
        if result.data.get("key") == "audio.volume":
            try:
                self._volume_pct = max(0, min(100, int(result.data.get("value") or 0)))
                # Apply immediately, whichever the state. A volume command spoken
                # over a playing stream ends the wake-duck too, so the radio jumps
                # to the new level right away instead of waiting for the restore.
                self._cancel_duck_restore()
                self._ducked = False
                self._amixer(f"{self._volume_pct}%")
                log.info("volume → %d%%", self._volume_pct)
            except (TypeError, ValueError):
                pass
        if not result.speak:
            return None
        data: dict[str, Any] = {
            "language": result.language,
            "text": result.speak,
            "chunk": result.speak,
            "final_": True,
            "action": f"command.{result.action}",
        }
        # A volume change already took effect on the Jabra mixer above; flag it so
        # the caller can skip the spoken confirmation while the player holds the PCM
        # (speaking would seize audio-out and kill the stream — see _on_envelope).
        if result.data.get("key") == "audio.volume":
            data["volume_only"] = True
        # Radio tools carry the resolved stream in result.data ({tool,url,name}).
        # Surface them as the radio_play/radio_stop actions (+payload) the radio
        # reconciler acts on — otherwise a fast-path "włącz trójkę" match only
        # SPEAKS the confirmation and never starts the stream. A radio_offer (no
        # station resolved → no url) intentionally stays a plain spoken reply.
        tool = result.data.get("tool")
        if tool == "radio.play" and result.data.get("url"):
            data["action"] = "radio_play"
            data["payload"] = {
                "url": str(result.data["url"]),
                "name": str(result.data.get("name", "")),
            }
        # context.play_memos / context.play_found are the voice-memo queues —
        # they ride the same music_play action (is_playlist payload). Omitting
        # them here silently dropped the queue: "Odtwarzam 2 nagrania" spoke,
        # nothing played (first live memo playback, 2026-08-09 — the third
        # payload-drop at this exact seam; see the 2026-07-27 note below).
        elif tool in ("music.play", "music.next", "music.prev", "audiobook.play",
                      "context.play_memos", "context.play_found") and result.data.get("path"):
            if not self._music_enabled:
                # Local music/audiobook playback is temporarily disabled (its
                # in-play controls — stop / next / volume — are being fixed). Speak
                # a notice instead of starting an unstoppable track. Radio is
                # unaffected. Re-enable by unsetting BLAZEN_MUSIC_ENABLED=0.
                msg = "Odtwarzacz muzyki jest chwilowo wyłączony, naprawiam sterowanie."
                data["text"] = data["chunk"] = msg
                data["action"] = "command.music_disabled"
            else:
                data["action"] = "music_play"  # local file → same exclusive player
                data["payload"] = {
                    "path": str(result.data["path"]),
                    "name": str(result.data.get("name", "")),
                }
                # Audiobooks carry the whole book so the orchestrator can auto-advance
                # chapters, remember the position, and resume.
                if result.data.get("is_audiobook"):
                    data["payload"].update({
                        "is_audiobook": True,
                        "slug": str(result.data.get("slug", "")),
                        "chapters": list(result.data.get("chapters", [])),
                        "chapter": int(result.data.get("chapter", 0)),
                        "start_seconds": float(result.data.get("start_seconds", 0.0)),
                    })
                # Album/artist queues ride the same engine — dropping these keys
                # here silently downgraded every queue to a single track (live
                # 2026-07-27: "następny" replayed track 1 forever, no auto-advance).
                elif result.data.get("is_playlist"):
                    data["payload"].update({
                        "is_playlist": True,
                        "chapters": list(result.data.get("chapters", [])),
                        "chapter": int(result.data.get("chapter", 0)),
                        "labels": list(result.data.get("labels", [])),
                    })
        elif tool == "radio.stop":
            data["action"] = "radio_stop"
        return Envelope(
            topic="brain.reply",
            source="blazend-orchestrator",
            data=data,
        )

    async def _announce_recovery(self, env: Envelope) -> dict[str, Any] | None:
        """Speak a recovery cue for a `health.status` verdict; return the
        recovery state for `state.json`. ``None`` for the healthy (`ok`) case."""
        level = env.data.get("level", "ok")
        if level == "ok":
            return None
        lang = self._recovery_lang()
        ann = recovery_for_level(level, lang)
        if ann.speak:
            await self._prepare_speaker(len(ann.speak))  # audio-out up + subscribed before the frame
            await self._publisher.publish(Envelope(
                topic="brain.reply",
                source="blazend-orchestrator",
                data={
                    "language": lang,
                    "text": ann.speak,
                    "chunk": ann.speak,
                    "final_": True,
                    "action": f"recovery.{level}",
                },
            ))
        return {
            "level": level,
            "unit": env.data.get("unit", "system"),
            "action": env.data.get("action", "none"),
            "recovery_image": ann.recovery_image,
        }


__all__ = ["Orchestrator"]

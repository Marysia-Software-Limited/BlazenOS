"""The orchestrator: connects to every peer socket, tracks state, reacts.

M1 scope:
  - Open a publisher socket (`orchestrator.sock`).
  - Subscribe to wake, audio-in, asr, brain, tts, audio-out, health.
  - For every received envelope, merge a small summary into state.json.
  - Re-publish a `system.event` `ready` after first heartbeat from health.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from blazend.config import load as load_config
from blazend.domains.ai_orchestrator.adapters.rpi5.dispatch import IntentDispatcher, SettingsStore
from blazend.domains.systems.adapters.rpi5.led import PipelineLeds
from blazend.domains.systems.adapters.rpi5.led_hw import open_status_led
from blazend.domains.systems.adapters.rpi5.recovery import for_level as recovery_for_level
from blazend.domains.systems.adapters.rpi5.state import StateWriter
from blazend.domains.voice_output.adapters.rpi5.radio_control import RadioControl
from blazend.events import Envelope, system_event
from blazend.ipc import Publisher, Subscriber, runtime_dir

log = logging.getLogger("blazend.domains.systems.adapters.rpi5.orchestrator")

# Jabra SPEAK 410 output-volume control (voice volume commands + radio ducking).
_JABRA_CARD = "USB"       # ALSA card id of the Jabra speakerphone
_JABRA_MIXER = "PCM"      # its playback volume control
# Duck HARD (near-mute) while listening over a playing stream: the Jabra is a
# speakerphone whose DSP gates the mic when it plays loud, so at 8% commands still
# came through at only ~45-180 RMS (often dropped). ~2% un-gates the mic for a
# clean capture; the stream keeps running (not stopped), so it restores instantly.
_DUCK_PCT = 2             # Jabra output % while listening over a playing stream
_DUCK_WINDOW_S = 7.0      # restore volume if no command follows the wake (covers the ~5 s ASR window)
_DUCK_MIN_VOLUME_PCT = 15  # only duck when playback is above this — quiet music doesn't gate the mic
_DEFAULT_VOLUME_PCT = 30  # startup output volume (kept low: less speaker→mic echo)

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
        pl = "Cześć, tu Jessica. Jestem gotowa do pomocy."
        en = "Hi, I'm Jessica. I'm ready to help."
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
        await asyncio.sleep(delay)
        lang = self._default_lang
        text = en if lang == "en" else pl
        log.info("speaking startup greeting (%s)", lang)
        self._mark_speaking()  # bring audio-out up before TTS plays
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

    def _mark_speaking(self) -> None:
        """Bump the speak deadline so the reconciler brings audio-out up in time
        for a reply (covers audio-out start ~1-2s + TTS synth); `speaker-busy`
        then holds it up for the duration of playback."""
        self._speak_until = asyncio.get_running_loop().time() + 6.0

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
                ["sudo", "-n", "systemctl", action, "blazend-audio-out"],
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

        # Own audio-out for half-duplex (keeps the Jabra mic un-ducked while idle).
        asyncio.create_task(self._speaker_manager())

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
        if env.topic == "system.event" and env.data.get("kind") == "heartbeat":
            patch["ready"] = True
            patch["units"][peer]["status"] = "running"
        if env.topic == "wake.detected":
            self._awake_until = asyncio.get_running_loop().time() + self._wake_window_s
            patch["wake_word"] = {
                "last_fired": env.data.get("model"),
                "last_language": env.data.get("language"),
                "last_score": env.data.get("score"),
            }
            patch["awake"] = True
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
        if env.topic == "nlu.intent" and self._dispatcher is not None:
            if not self._awake():
                # Heard a command but not addressed ("Hej Jessico" not said) —
                # acknowledge in state, but stay silent.
                log.info("asleep — ignoring %s", env.data.get("intent"))
                patch["awake"] = False
            else:
                reply = self._dispatch_intent(env)
                # Acting on a command keeps the conversation open for follow-ups.
                self._awake_until = asyncio.get_running_loop().time() + self._wake_window_s
                if reply is not None:
                    action = str(reply.data.get("action", ""))
                    # Playback actions ARE the feedback (the stream/track starting
                    # or stopping), so don't also speak the confirmation over the
                    # Jabra it needs — a spoken reply brings audio-out UP and grabs
                    # the single output PCM out from under the player (silent death).
                    # Non-playback replies (volume, time, …) are spoken via TTS.
                    if action not in ("radio_play", "radio_stop", "music_play", "music_stop"):
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
            await self._act_on_reply(env.data)
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
        """Set the Jabra playback volume (level like '27%'). Best-effort."""
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

    async def _duck_off(self) -> None:
        """Restore the Jabra to the user's volume after a duck."""
        self._ducked = False
        await self._set_volume(self._volume_pct)

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
        # and bring the volume back up.
        if self._radio.playing and self._ducked:
            await self._duck_off()

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
            self._cancel_duck_restore()  # a real play command supersedes a duck
            # Free the Jabra output for playback; the reconciler keeps audio-out
            # down while it plays.
            self._speak_until = 0.0
            await self._apply_audio_out(False)
            await asyncio.sleep(0.3)  # let the device release
            await asyncio.to_thread(self._radio.play, source, str(payload.get("name", "")))
            await self._duck_off()  # play at the user's volume
            # Suppress the VAD while it plays so the Jabra hearing its own output
            # can't trigger a barge-in ("dżesika" still interrupts via wake).
            self._speaker_busy.touch()
        elif action in ("radio_stop", "music_stop"):
            self._cancel_duck_restore()
            self._ducked = False
            await asyncio.to_thread(self._radio.stop)
            self._speaker_busy.unlink(missing_ok=True)
            await self._set_volume(self._volume_pct)  # ready for next stream
        elif data.get("text"):
            # A spoken reply (LLM chat, or a non-radio command confirmation) —
            # bring audio-out up to play it.
            self._mark_speaking()

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
                self._volume_pct = max(0, min(100, int(result.data.get("value"))))
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
        elif tool in ("music.play", "music.next", "music.prev", "audiobook.play") and result.data.get("path"):
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
            self._mark_speaking()  # spoken cue → audio-out up
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

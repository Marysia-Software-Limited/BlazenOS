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
            # If a stream is playing, pause it so the command spoken after
            # "dżesika" is heard cleanly, and clear speaker-busy so the mic's VAD
            # is no longer suppressed.
            if self._radio.playing:
                await asyncio.to_thread(self._radio.stop)
                self._speaker_busy.unlink(missing_ok=True)
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
                    self._mark_speaking()  # spoken reply → audio-out up
                    await self._publisher.publish(reply)
                    patch["last_command"] = {
                        "intent": env.data.get("intent"),
                        "result": reply.data.get("action"),
                    }
                # Keep the language pin authoritative in state (scenario 09).
                patch["languages"] = {"pinned": self._dispatcher.pinned_language()}
        # Radio: act on the brain's decision. Barge-in is wake-gated (see the
        # wake.detected handler) rather than triggered by raw vad.start — on the
        # Jabra speakerphone the stream's own audio loops back into the mic, so a
        # vad.start barge-in would immediately stop the radio it just started.
        # `_radio.*` block (subprocess) → run off the event loop.
        if env.topic == "brain.reply":
            action = str(env.data.get("action", ""))
            if action == "radio_play":
                payload = env.data.get("payload", {}) or {}
                # Free the Jabra output for the stream, then play. The reconciler
                # keeps audio-out down while the stream is playing.
                self._speak_until = 0.0
                await self._apply_audio_out(False)
                await asyncio.sleep(0.3)  # let the device release
                await asyncio.to_thread(
                    self._radio.play, str(payload.get("url", "")), str(payload.get("name", ""))
                )
                # Suppress the VAD while the stream plays so the Jabra hearing its
                # own output can't trigger a barge-in. Wake reads the raw ring, so
                # "dżesika" still interrupts (handled above).
                self._speaker_busy.touch()
            elif action == "radio_stop":
                await asyncio.to_thread(self._radio.stop)
                self._speaker_busy.unlink(missing_ok=True)
            elif env.data.get("text"):
                # A spoken reply from the brain (LLM) — bring audio-out up to play it.
                self._mark_speaking()
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

    def _dispatch_intent(self, env: Envelope) -> Envelope | None:
        """Act on a fast-path `nlu.intent`; return the spoken reply (if any)."""
        if self._dispatcher is None:
            return None
        result = self._dispatcher.dispatch(
            env.data.get("intent", ""),
            env.data.get("params", {}),
            env.data.get("language", "pl"),
        )
        if result.signal in ("reboot", "shutdown"):
            # In the VM / dev we never actually power off; on the device a
            # power unit consumes this. Log it loudly.
            log.warning("system command requested: %s (not executed in dev)", result.signal)
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

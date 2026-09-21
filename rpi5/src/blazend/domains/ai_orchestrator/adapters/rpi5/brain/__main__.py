"""Entrypoint: `python -m blazend.domains.ai_orchestrator.adapters.rpi5.brain`.

The brain is the conversational half of the assistant. It subscribes to
`nlu.miss` (utterances the fast-path router did **not** match — so command
intents go to the dispatcher, not here, and there is no double-reply), runs
:class:`blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine.Assistant` (name/memory/reminders +
Gemini-backed Polish chat and news), and publishes `brain.reply` for
`blazend-tts` to speak. Due reminders are fired on a timer. `--mock` keeps
the old canned-reply behaviour for smoke tests.

The transcript only exists after the wake word fired upstream, so the engine
runs ``always_awake=True`` here.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path

from blazend.config import load
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.context_wiring import (
    notes_context_wiring,
)
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import (
    Assistant,
    Reply,
    _is_stop_order,
)
from blazend.domains.ai_orchestrator.core.registry import build_model_router
from blazend.events import Envelope, system_event
from blazend.ipc import Publisher, Subscriber, runtime_dir

log = logging.getLogger("blazend.domains.ai_orchestrator.adapters.rpi5.brain")


def _reply_envelope(reply: Reply) -> Envelope:
    from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import (
        llm_awaits_reply,
    )

    return Envelope(
        topic="brain.reply",
        source="blazend-brain",
        data={
            "language": reply.language,
            "text": reply.text,
            "chunk": reply.text,
            "final_": True,
            "action": reply.action,
            # Carry the action payload (e.g. radio stream url + name) so the
            # orchestrator can actually execute it, not just speak the reply.
            "payload": reply.data,
            # Conversation steering (2026-09-08): does this chat reply WAIT
            # for the user? The orchestrator arms the hot (more sensitive)
            # follow-up window when it does.
            "awaits_reply": (reply.action == "chat"
                             and llm_awaits_reply(reply.text)),
        },
    )


async def _connect_nlu(rt: Path) -> Subscriber:
    """Connect to the NLU publisher (nlu.miss / nlu.intent), retrying."""
    return await _connect_sock(rt / "nlu.sock")


async def _connect_sock(sock: Path) -> Subscriber:
    """Connect to a publisher socket, retrying until it binds."""
    while True:
        if sock.exists():
            try:
                sub = Subscriber(sock)
                await sub.connect()
                return sub
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                pass
        await asyncio.sleep(0.2)


async def _watch_wake(engine: Assistant, rt: Path, stop: asyncio.Event) -> None:
    """Feed REAL ``wake.detected`` events into the engine's awake window.

    Text-only wake detection misses every wake whisper mangles ("dżesika" →
    "Jeszcze karp…"), which made the engine answer its sleep notice right
    after the user said the wake word (2026-08-29). Reconnects when
    blazend-wake restarts — same recover-by-reconnect as the NLU
    subscription."""
    while not stop.is_set():
        sub = await _connect_sock(rt / "wake.sock")
        log.info("brain subscribed to wake.detected")
        while not stop.is_set():
            env = await sub.next()
            if env is None:  # wake restarted — reconnect
                break
            if env.topic == "wake.detected":
                engine.notify_wake(datetime.now())
        await sub.close()


async def _reminder_ticker(engine: Assistant, pub: Publisher, stop: asyncio.Event) -> None:
    while not stop.is_set():
        for reply in engine.due_reminders(datetime.now()):
            log.info("reminder fired: %s", reply.text)
            await pub.publish(_reply_envelope(reply))
        try:
            await asyncio.wait_for(stop.wait(), timeout=1.0)
        except TimeoutError:
            pass


async def serve(
    engine: Assistant, *, runtime_dir_: Path | None = None, stop: asyncio.Event | None = None
) -> None:
    """Run the brain: asr.final → engine → brain.reply, plus reminder ticks."""
    rt = runtime_dir_ or runtime_dir()
    stop = stop or asyncio.Event()
    pub = Publisher(rt / "brain.sock")
    await pub.bind()
    await pub.publish(system_event(source="blazend-brain", kind="ready"))
    log.info("brain online at %s", rt / "brain.sock")

    ticker = asyncio.create_task(_reminder_ticker(engine, pub, stop))
    wake_watch = asyncio.create_task(_watch_wake(engine, rt, stop))
    try:
        speaker_enforce = bool(load("speaker-id").get("enforce", False))
    except Exception:  # noqa: BLE001 — unreadable config = trust every voice
        speaker_enforce = False
    sub = await _connect_nlu(rt)
    log.info("brain subscribed to nlu.miss")
    try:
        while not stop.is_set():
            # Race the next ASR frame against a stop signal so shutdown is
            # prompt even while we're blocked waiting for the next utterance.
            next_task = asyncio.ensure_future(sub.next())
            stop_task = asyncio.ensure_future(stop.wait())
            done, pending = await asyncio.wait(
                {next_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for t in pending:
                t.cancel()
            if stop.is_set():
                break
            env = next_task.result()
            if env is None:
                # NLU restarted (its socket rebound) → this subscription hit
                # EOF. RECONNECT instead of exiting: a clean exit here left
                # the brain dead after a routine blazend-asr→nlu restart —
                # exit 0 means Restart=on-failure never revives it, and the
                # pipeline loses every non-fast-path reply (2026-08-28).
                await sub.close()
                sub = await _connect_nlu(rt)
                log.info("brain re-subscribed to nlu.miss (nlu restarted)")
                continue
            if env.topic == "nlu.miss":
                text = env.data.get("transcript", "")
                # A pending uncertain-action confirmation owns this turn: the
                # orchestrator classifies the answer (yes/no/emotional/babble)
                # and the brain must not ALSO route it (2026-09-08).
                if (rt / "confirm-pending").exists():
                    log.info("confirm pending — leaving %r to the orchestrator",
                             text[:40])
                    continue
                # Speaker policy (2026-09-08): with enforcement on, a voice
                # that is NOT the owner never reaches the engine/LLM — except
                # a bare stop order, which works for anyone. Missing speaker
                # fields = cannot tell → trust (pre-feature behaviour).
                if (speaker_enforce and env.data.get("speaker_owner") is False
                        and not _is_stop_order(text)):
                    log.info("speaker: foreign voice (sim=%s) — ignoring %r",
                             env.data.get("speaker_sim"), text[:60])
                    continue
                # Thinking cue: routing may block on the LLM for many seconds; tell
                # the orchestrator FIRST so it can speak "Chwileczkę." instead of
                # dead air (blind-first). Only when the engine will actually engage
                # (awake, real command) — never before an "asleep" brush-off.
                if engine.engages(text):
                    await pub.publish(
                        system_event(source="blazend-brain", kind="thinking")
                    )
                reply = engine.route(text, now=datetime.now())
                # not_understood carries no text but the orchestrator speaks
                # its cue (closed command list, 2026-09-08) — publish it too.
                # negative_reaction is silent by design, yet the orchestrator
                # must see it to close the conversation and write the label.
                if reply.text or reply.action in ("not_understood",
                                                  "negative_reaction"):
                    log.info("reply (%s/%s): %s", reply.language, reply.action, reply.text)
                    await pub.publish(_reply_envelope(reply))
    finally:
        stop.set()
        ticker.cancel()
        wake_watch.cancel()
        await sub.close()
        await pub.close()


async def _mock_loop(pub: Publisher) -> None:
    replies = [("pl", "Jasne, robi się."), ("en", "Sure, on it.")]
    idx = 0
    while True:
        await asyncio.sleep(30)
        lang, text = replies[idx % len(replies)]
        idx += 1
        await pub.publish(
            Envelope(
                topic="brain.reply",
                source="blazend-brain",
                data={"language": lang, "text": text, "chunk": text, "final_": True},
            )
        )
        log.info("emit brain.reply %s %r", lang, text)


async def run(mock: bool) -> None:
    """Bind the brain socket and serve (real engine) or emit canned replies."""
    if mock:
        pub = Publisher(runtime_dir() / "brain.sock")
        await pub.bind()
        await pub.publish(system_event(source="blazend-brain", kind="ready"))
        log.info("brain online (mock) at %s", pub._socket_path)  # noqa: SLF001
        await _mock_loop(pub)
        return
    # Wake gating: when require_wake is set, the engine only answers utterances
    # that contain the wake word ("Jessica" / "Hej Jessico") and stays awake for
    # follow-ups (engine.route → wake.is_wake). always_awake disables that.
    try:
        _wake_cfg = load("wake-word")
        require_wake = bool(_wake_cfg.get("require_wake", True))
        window_s = float(_wake_cfg.get("conversation_window_s", 20))
        history_turns = int(_wake_cfg.get("conversation.history_turns", 6))
    except Exception:  # noqa: BLE001
        require_wake, window_s, history_turns = True, 20.0, 6
    # Task-based model router: COMMAND→Bielik 1.5B, RECOMMEND→Bielik 4.5B,
    # OPEN_QA→gpt-5.5, all preferring the LAN Ollama 11B when reachable. It owns
    # backend selection + graceful fallback; the engine degrades to Gemini/canned
    # if the router (or every backend) is unavailable.
    router = build_model_router()
    # Semantic memory recall (shared wiring with the legacy runner): without
    # this the live brain silently ran lexical-only recall — saved notes never
    # reached the LLM prompts.
    wiring = notes_context_wiring()
    await serve(Assistant(
        always_awake=not require_wake, wake_window_s=window_s,
        history_turns=history_turns, router=router,
        embedder=wiring.embedder,
        notes_top_k=wiring.top_k, notes_min_score=wiring.min_score,
        notes_rel_margin=wiring.rel_margin, notes_max_chars=wiring.max_chars,
        notes_include_voice=wiring.include_voice,
        notes_share_with_cloud=wiring.share_with_cloud,
    ))


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(prog="blazend-brain")
    parser.add_argument("--mock", action="store_true", help="emit canned replies (no engine)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    asyncio.run(run(args.mock))


if __name__ == "__main__":
    main()

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
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.engine import Assistant, Reply
from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import OpenAiClient
from blazend.domains.ai_orchestrator.core.registry import select_chat_llm
from blazend.events import Envelope, system_event
from blazend.ipc import Publisher, Subscriber, runtime_dir

log = logging.getLogger("blazend.domains.ai_orchestrator.adapters.rpi5.brain")


def _reply_envelope(reply: Reply) -> Envelope:
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
        },
    )


async def _connect_nlu(rt: Path) -> Subscriber:
    """Connect to the NLU publisher (nlu.miss / nlu.intent), retrying."""
    sock = rt / "nlu.sock"
    while True:
        if sock.exists():
            try:
                sub = Subscriber(sock)
                await sub.connect()
                return sub
            except (FileNotFoundError, ConnectionRefusedError, OSError):
                pass
        await asyncio.sleep(0.2)


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
                break
            if env.topic == "nlu.miss":
                text = env.data.get("transcript", "")
                reply = engine.route(text, now=datetime.now())
                if reply.text:
                    log.info("reply (%s/%s): %s", reply.language, reply.action, reply.text)
                    await pub.publish(_reply_envelope(reply))
    finally:
        stop.set()
        ticker.cancel()
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
        require_wake = bool(load("wake-word").get("require_wake", True))
    except Exception:  # noqa: BLE001
        require_wake = True
    # LLM for freeform chat. The ai-orchestrator backend registry picks the
    # primary backend (reachable LAN Ollama via BLAZEN_LLM_OLLAMA_URL, else the
    # on-device Bielik), or None to degrade to the engine's cloud fallback.
    llm = select_chat_llm()
    # Cloud second layer (OpenAI) behind the local model; activates if the key
    # is set. Local stays first, so normal operation is on-device.
    await serve(Assistant(always_awake=not require_wake, llm=llm, openai=OpenAiClient()))


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(prog="blazend-brain")
    parser.add_argument("--mock", action="store_true", help="emit canned replies (no engine)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    asyncio.run(run(args.mock))


if __name__ == "__main__":
    main()

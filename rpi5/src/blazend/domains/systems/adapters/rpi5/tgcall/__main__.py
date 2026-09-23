"""blazend-tgcall — Telegram account bridge daemon (see package docstring)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np
from pyrogram import Client, filters, raw
from pyrogram.handlers import RawUpdateHandler
from pyrogram.types import Message
from pytgcalls import PyTgCalls
from pytgcalls import filters as call_filters
from pytgcalls.types import (
    AudioQuality,
    ChatUpdate,
    Device,
    ExternalMedia,
    Frame,
    MediaStream,
    RecordStream,
    StreamFrames,
    Update,
)

from blazend.config import load
from blazend.domains.systems.adapters.rpi5.telegram.__main__ import (
    _announce,
    _decode_ogg,
)
from blazend.domains.voice_input.adapters.rpi5.audio import RingReader
from blazend.domains.voice_input.adapters.rpi5.intercom import _open_player
from blazend.ipc import runtime_dir

log = logging.getLogger("blazend.tgcall")

_ANSWER_TIMEOUT_S = 30.0
_MIC_CHUNK_MS = 20
# Call audio format (both directions): 48 kHz stereo s16le — AudioQuality.HIGH.
_CALL_RATE = 48_000
_CALL_CH = 2


class Account:
    """One logged-in Telegram account bridged into the room."""

    def __init__(self, cfg: dict[str, Any], max_call_s: float) -> None:
        self.name = str(cfg.get("name", "acc"))
        self.label = str(cfg.get("label", self.name))
        self.session_dir = Path(str(cfg.get("session_dir", ""))).expanduser()
        self.allowed = {int(u) for u in (cfg.get("allowed_callers") or [])}
        self.speak_messages = bool(cfg.get("speak_messages", True))
        self.max_call_s = max_call_s
        self.client = Client(
            "jessica",
            api_id=int(os.environ.get("BLAZEN_TELEGRAM_API_ID", "0")) or None,
            api_hash=os.environ.get("BLAZEN_TELEGRAM_API_HASH") or None,
            workdir=str(self.session_dir))
        self.calls = PyTgCalls(self.client)
        self.active_chat: int | None = None
        self.player: Any = None
        self._mic_task: asyncio.Task[None] | None = None

    # -- messages ----------------------------------------------------------
    async def on_message(self, _client: Client, m: Message) -> None:
        if not self.speak_messages or m.outgoing:
            return
        sender = (m.from_user.first_name if m.from_user else None) or "ktoś"
        if m.voice:
            try:
                ogg = await m.download(in_memory=True)
                wav = _decode_ogg(bytes(ogg.getbuffer()))
            except Exception as exc:  # noqa: BLE001
                log.warning("%s: voice decode failed: %s", self.name, exc)
                return
            _announce(f"Wiadomość głosowa na Telegramie, {self.label}, "
                      f"od: {sender}.")
            await asyncio.sleep(5)
            await self._play_wav(wav)
        elif m.text:
            _announce(f"Telegram, {self.label}, od: {sender}. {m.text[:500]}")

    async def _play_wav(self, wav: bytes) -> None:
        marker = runtime_dir() / "speaking"
        player = await asyncio.to_thread(_open_player,
                                         ["aplay", "-q", "-D", "jabra_out"])
        marker.touch()
        try:
            await asyncio.to_thread(self._feed, player, wav)
        finally:
            marker.unlink(missing_ok=True)
            player.terminate()

    @staticmethod
    def _feed(player: Any, data: bytes) -> None:
        try:
            player.stdin.write(data)
            player.stdin.close()
            player.wait(timeout=180)
        except (BrokenPipeError, OSError, ValueError):
            pass

    # -- calls -------------------------------------------------------------
    async def on_raw(self, client: Client, update: Any,
                     _users: Any, _chats: Any) -> None:
        """Confirm RINGING to the caller the moment a call request lands.
        py-tgcalls never sends phone.receivedCall, so the caller's phone gave
        up almost immediately ("od razu nieodebrane", live test 2026-09-23) —
        with the confirmation it keeps ringing for its full window while the
        room answers by voice."""
        if (isinstance(update, raw.types.UpdatePhoneCall)
                and isinstance(update.phone_call,
                               raw.types.PhoneCallRequested)):
            pc = update.phone_call
            try:
                await client.invoke(raw.functions.phone.ReceivedCall(
                    peer=raw.types.InputPhoneCall(
                        id=pc.id, access_hash=pc.access_hash)))
                log.info("%s: ringing confirmed to caller (call %s)",
                         self.name, pc.id)
            except Exception as exc:  # noqa: BLE001 — best-effort courtesy
                log.debug("%s: receivedCall failed: %s", self.name, exc)

    async def on_chat_update(self, _c: PyTgCalls, u: Update) -> None:
        if not isinstance(u, ChatUpdate):
            return
        if u.status & ChatUpdate.Status.INCOMING_CALL:
            await self._on_ring(int(u.chat_id))
        elif u.status & ChatUpdate.Status.DISCARDED_CALL:
            await self._hangup(reason="rozmówca się rozłączył")

    async def _on_ring(self, chat_id: int) -> None:
        if self.allowed and chat_id not in self.allowed:
            log.warning("%s: rejecting call from non-allowlisted %s",
                        self.name, chat_id)
            return  # let it ring out — strangers learn nothing
        if self.active_chat is not None:
            log.info("%s: busy — ignoring call from %s", self.name, chat_id)
            return
        try:
            user = await self.client.get_users(chat_id)
            caller = user.first_name or str(chat_id)
        except Exception:  # noqa: BLE001
            caller = str(chat_id)
        log.info("%s: incoming Telegram call from %s (%s)",
                 self.name, caller, chat_id)
        verdict = await self._voice_answer(f"{caller}, przez Telegram")
        if verdict != "accept":
            log.info("%s: call %s → %s", self.name, caller, verdict)
            return
        await self._accept(chat_id)

    async def _voice_answer(self, from_label: str) -> str:
        """Ring through the orchestrator's call spool; the household answers
        by voice ("odbierz"/"odrzuć") — same flow as a node↔node call."""
        spool = runtime_dir() / "call"
        if not spool.is_dir():
            return "nospool"
        call_id = uuid.uuid4().hex[:10]
        tmp = spool / f"ring-{call_id}.json.tmp"
        tmp.write_text(json.dumps({"id": call_id, "from": from_label},
                                  ensure_ascii=False), encoding="utf-8")
        tmp.rename(spool / f"ring-{call_id}.json")
        answer = spool / f"answer-{call_id}.json"
        deadline = time.monotonic() + _ANSWER_TIMEOUT_S + 10
        try:
            while time.monotonic() < deadline:
                if answer.is_file():
                    return str(json.loads(
                        answer.read_text(encoding="utf-8")).get("answer", ""))
                await asyncio.sleep(0.5)
        finally:
            answer.unlink(missing_ok=True)
            (spool / f"ring-{call_id}.json").unlink(missing_ok=True)
        return "timeout"

    async def _accept(self, chat_id: int) -> None:
        self.active_chat = chat_id
        (runtime_dir() / "speaking").touch()
        self.player = await asyncio.to_thread(_open_player, [
            "aplay", "-q", "-D", "jabra_out", "-f", "S16_LE",
            "-r", str(_CALL_RATE), "-c", str(_CALL_CH), "-t", "raw"])
        await self.calls.play(
            chat_id, MediaStream(ExternalMedia.AUDIO,
                                 audio_parameters=AudioQuality.HIGH))
        await self.calls.record(chat_id, RecordStream(audio=True))
        self._mic_task = asyncio.create_task(self._pump_mic(chat_id))
        asyncio.get_running_loop().call_later(
            self.max_call_s, lambda: asyncio.ensure_future(
                self._hangup(reason="limit czasu")))
        log.info("%s: call %s connected (cap %.0fs)",
                 self.name, chat_id, self.max_call_s)

    async def _pump_mic(self, chat_id: int) -> None:
        """Mic ring (16 kHz mono) → 48 kHz stereo raw frames into the call."""
        ring = RingReader(runtime_dir() / "audio-ring.shm")
        step = max(1, int(ring.sample_rate * _MIC_CHUNK_MS / 1000))
        pos = ring.write_pos
        factor = _CALL_RATE // ring.sample_rate
        try:
            while self.active_chat == chat_id:
                new = ring.write_pos
                if new - pos >= step:
                    pcm = ring.read_range(pos, new)
                    pos = new
                    up = np.repeat(pcm, factor)          # 16k → 48k
                    stereo = np.repeat(up, _CALL_CH)     # mono → interleaved
                    await self.calls.send_frame(
                        chat_id, Device.MICROPHONE,
                        stereo.astype("<i2").tobytes(), Frame.Info())
                else:
                    await asyncio.sleep(_MIC_CHUNK_MS / 2000)
        except Exception as exc:  # noqa: BLE001 — hang-ups race the pump
            log.debug("%s: mic pump ended: %s", self.name, exc)
        finally:
            ring.close()

    async def on_frames(self, _c: PyTgCalls, update: Update) -> None:
        if not isinstance(update, StreamFrames) or self.player is None:
            return
        if update.chat_id != self.active_chat:
            return
        for frame in update.frames:
            try:
                self.player.stdin.write(frame.frame)
                self.player.stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                await self._hangup(reason="głośnik padł")
                return

    async def _hangup(self, *, reason: str) -> None:
        chat, self.active_chat = self.active_chat, None
        if chat is None:
            return
        log.info("%s: hanging up (%s)", self.name, reason)
        if self._mic_task is not None:
            self._mic_task.cancel()
        try:
            await self.calls.leave_call(chat)
        except Exception:  # noqa: BLE001
            pass
        if self.player is not None:
            self.player.terminate()
            self.player = None
        (runtime_dir() / "speaking").unlink(missing_ok=True)
        _announce("Rozmowa telegramowa zakończona.")

    async def run(self) -> None:
        self.client.on_message(filters.private & filters.incoming)(
            self.on_message)
        self.client.add_handler(RawUpdateHandler(self.on_raw), group=-99)
        self.calls.on_update(call_filters.chat_update(
            ChatUpdate.Status.INCOMING_CALL
            | ChatUpdate.Status.DISCARDED_CALL))(self.on_chat_update)
        self.calls.on_update(call_filters.stream_frame(
            devices=Device.SPEAKER))(self.on_frames)
        await self.calls.start()
        me = await self.client.get_me()
        log.info("account %s online as %s (+%s), callers allowlist: %d",
                 self.name, me.first_name, me.phone_number, len(self.allowed))


async def amain() -> int:
    try:
        cfg = load("telegram-call").data
    except Exception:  # noqa: BLE001
        cfg = {}
    if not bool(cfg.get("enabled", False)):
        log.info("tgcall disabled (telegram-call.yaml enabled: false) — idle")
        while True:
            await asyncio.sleep(3600)
    max_call_s = float(cfg.get("max_call_s", 300))
    accounts = [Account(a, max_call_s) for a in (cfg.get("accounts") or [])
                if a.get("session_dir")]
    if not accounts:
        raise SystemExit("no accounts configured in telegram-call.yaml")
    for acc in accounts:
        await acc.run()
    await asyncio.Event().wait()  # run forever; handlers do the work
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(message)s")
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())

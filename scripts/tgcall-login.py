#!/usr/bin/env python3
"""One-time MTProto login for the Jessica Telegram ACCOUNT (tgcall bridge).

Two-step, so the SMS/app code can be relayed out-of-band:

    tgcall-login.py --phone +48123456789     # sends the code, stores the hash
    tgcall-login.py --code 12345             # completes sign-in, saves session

Credentials come from the environment (BLAZEN_TELEGRAM_API_ID/API_HASH —
source secrets.env first). The session file lands next to --session (default
~/.local/share/blazen/tgcall/jessica.session) with 0600 — it IS full access
to the account; it never leaves the node.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from pyrogram import Client  # kurigram fork — stock pyrogram is dead on 3.14

STATE = Path.home() / ".local/share/blazen/tgcall/login-state.json"


def _client(workdir: Path) -> Client:
    api_id = int(os.environ["BLAZEN_TELEGRAM_API_ID"])
    api_hash = os.environ["BLAZEN_TELEGRAM_API_HASH"]
    workdir.mkdir(parents=True, exist_ok=True)
    os.chmod(workdir, 0o700)
    return Client("jessica", api_id=api_id, api_hash=api_hash,
                  workdir=str(workdir))


async def send_code(phone: str, workdir: Path) -> int:
    client = _client(workdir)
    await client.connect()
    sent = await client.send_code(phone)
    STATE.write_text(json.dumps(
        {"phone": phone, "hash": sent.phone_code_hash}), encoding="utf-8")
    STATE.chmod(0o600)
    await client.disconnect()
    print(f"code sent to {phone} — finish with: tgcall-login.py --code <kod>")
    return 0


async def sign_in(code: str, workdir: Path) -> int:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    client = _client(workdir)
    await client.connect()
    user = await client.sign_in(state["phone"], state["hash"], code)
    me = getattr(user, "first_name", None) or "?"
    await client.disconnect()
    STATE.unlink(missing_ok=True)
    print(f"signed in as {me} — session saved in {client.workdir}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--phone", help="step 1: send the login code to this number")
    ap.add_argument("--code", help="step 2: the received code")
    ap.add_argument("--session-dir",
                    default=str(Path.home() / ".local/share/blazen/tgcall"))
    args = ap.parse_args()
    workdir = Path(args.session_dir)
    if args.phone:
        return asyncio.run(send_code(args.phone, workdir))
    if args.code:
        return asyncio.run(sign_in(args.code, workdir))
    ap.error("need --phone (step 1) or --code (step 2)")
    return 2


if __name__ == "__main__":
    sys.exit(main())

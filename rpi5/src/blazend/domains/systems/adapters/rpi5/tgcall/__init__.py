"""Telegram CALL bridge — MTProto accounts answered by voice (2026-09-23).

Where the bot bridge (``..telegram``) handles messages, this daemon logs in
as real Telegram ACCOUNTS (kurigram sessions; the bot API cannot touch
calls) and gives each one ears and a mouth in the room:

- an **incoming private call** from an allowlisted Telegram user rings
  through the orchestrator's ``<runtime>/call/`` spool — Jessica announces
  "Połączenie przez Telegram od: X" and the household answers by VOICE
  ("odbierz"/"odrzuć"), exactly like a node↔node intercom ring;
- on accept the call goes full duplex on raw frames: the mic ring →
  ``send_frame`` (16 kHz mono upsampled to 48 kHz), incoming ``StreamFrames``
  → ``aplay jabra_out`` under the ``speaking`` marker;
- an **incoming private message** (text/voice note) is spoken/played like
  the bot bridge does — so one node can watch several accounts (user's
  +44/+48, later Błazen's) with per-account allowlists.

Sessions live under ``tgcall/<account>/`` (0700) — a session file IS the
account; it never leaves the node. Runs in the dedicated tgcall venv
(kurigram + py-tgcalls; stock pyrogram is dead on Python 3.14).
"""

# 18 — Jessica prototype (working assistant)

A runnable, text-drivable version of the assistant that works **on the dev
host** (no Pi, no audio device, no local model required). It is the
end-to-end logic behind the four prototype goals; the voice path
(`blazend-wake` → ASR → `blazend-nlu` → brain → TTS) drops in on top later.

Code: [`rpi5/src/blazend/assistant/`](../rpi5/src/blazend/assistant/).

## Run it

```bash
make demo
# or, non-interactive:
python -m blazend.assistant --once "Hej Jessico, przypomnij mi o praniu za 5 sekund"
```

You type what you'd *say*; Jessica prints what she'd *speak*. Memory persists
to `$BLAZEN_DATA_DIR/memory.json` (default `vm-runs/jessica-data/`).

## The four capabilities

| Goal | How | Real offline? |
|------|-----|:-------------:|
| **Reacts to her name** | `wake.py` detects "Jessica" / "Jess" / "hej Jessico" (same names as the `jessica_*` wake models). She stays asleep until addressed. | ✅ |
| **Converses in Polish** (and EN) | `engine.py` detects the language and replies in it (Polish-first). Freeform chat is answered by Gemini. | chat needs key |
| **Checks news / sites** | `gemini.py` calls Gemini with **Google Search grounding** ("co w wiadomościach?", "sprawdź stronę…"). | needs key |
| **Remembers + reminds** | `memory.py` persists notes + reminders (fabric-shaped facts); `timeparse.py` parses "o 15:00" / "za 10 minut" / "jutro o 9"; due reminders fire on a timer. | ✅ |

Examples (Polish):

```
Hej Jessico, zapamiętaj że kod do bramy to 4729
Jessica, przypomnij mi o praniu za 5 sekund
Jessica, co w wiadomościach?
co pamiętasz?
jakie mam przypomnienia?
```

## Gemini (your account)

News/web lookups and freeform conversation use **your Gemini account**.
Set the key (e.g. in `.env` or the shell):

```bash
export GEMINI_API_KEY=...        # https://aistudio.google.com/apikey
export GEMINI_MODEL=gemini-2.0-flash   # optional
```

Without the key, memory / reminders / name-reaction / command parsing all
still work; only the cloud-backed chat + news politely ask you to set it. No
SDK dependency — the adapter uses `urllib` and an injectable transport, so
tests run fully offline and deterministically.

## Where it fits

`engine.Assistant` is **pure and synchronous** — the same object drives the
REPL **and** the IPC `blazend-brain` unit, which consumes `asr.final` and
publishes `brain.reply` (and fires due reminders on a timer) for
`blazend-tts` to speak. So `make dev` brings up the real conversational
brain, not a mock. Fast-path system commands (volume, "stop talking") are
handled in Rust by
[`blazend-nlu`](14-RUST-PYTHON-SPLIT.md) over the shared `jessica-core`;
this engine owns the conversational + memory + cloud side.

Tests: [`rpi5/tests/unit/test_assistant.py`](../rpi5/tests/unit/test_assistant.py)
(name EN/PL, time parsing, notes/reminders persistence + firing, and engine
routing with a fake Gemini + injected clock).

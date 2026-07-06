# macos/docs — rachel node docs

Read in order:

1. [`00-CONTEXT.md`](00-CONTEXT.md) — **start here.** The constellation, the vision
   (shared LLM mesh + shared context), naming to confirm, and what already exists
   that rachel reuses.
2. [`01-ARCHITECTURE.md`](01-ARCHITECTURE.md) — the rachel stack (Swift shell + agent
   core + MLX/Metal ML + fabric context sync); host-language decision.
3. [`03-LLM-MESH.md`](03-LLM-MESH.md) — the distributed **DSPy LLM mesh** across
   jessica · rachel · paul (the headline feature).
4. [`02-BUILD-INSTRUCTIONS.md`](02-BUILD-INSTRUCTIONS.md) — phased, testable build
   steps for the macOS Claude session.
5. [`04-CALIBRE-TTS.md`](04-CALIBRE-TTS.md) — **rachel owns this**: turn the user's
   Calibre ebooks into Polish audiobooks (Azure now, ElevenLabs later) for the
   shared library. Moved here from the Pi's audiobook plan.

Surface rules: [`../AGENTS.md`](../AGENTS.md), [`../CLAUDE.md`](../CLAUDE.md).
Shared core + contract: [`../../docs/17-MOBILE-MONOREPO.md`](../../docs/17-MOBILE-MONOREPO.md).

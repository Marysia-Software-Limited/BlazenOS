"""blazen_os Python sources.

Component layout:
    blazend.config         — shared YAML loader (used by every Python unit).
    blazend.events         — IPC envelope + topic constants (mirrors crates/blazend-ipc).
    blazend.ipc            — async Unix-socket client/server.
    blazend.domains.systems.adapters.rpi5.state          — /run/blazen/state.json writer.
    blazend.domains.systems.adapters.rpi5.orchestrator   — pipeline supervisor (Python unit).
    blazend.domains.voice_input.adapters.rpi5.asr            — speech recognition (Python unit).
    blazend.domains.ai_orchestrator.adapters.rpi5.brain          — LLM engine selector (Python unit).
    blazend.domains.systems.adapters.rpi5.bootstrap      — first-boot pairing (Python unit).

Each unit's entrypoint is `python -m blazend.<unit>`.
See docs/14-RUST-PYTHON-SPLIT.md for the language boundary.
"""

__version__ = "0.0.1"

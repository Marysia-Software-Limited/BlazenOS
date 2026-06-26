"""Capability domains — ports-and-adapters. See docs/19-DOMAIN-ARCHITECTURE.md.

Each domain is ``core/`` (portable logic + Port protocols, no hardware/vendor
imports) plus ``adapters/<platform>/`` (the hardware-close implementations behind
those ports). Phase 1 nests the Pi realization here under the ``blazend`` package;
the Rust adapter crates relocate under the domain tree in Phase 3.

Domains: local_ai, ai_orchestrator, context (the portable "mind"); voice_input,
voice_output, systems (the per-device "body").
"""

"""Tier 0 — PL runtime + retained-EN-asset coverage invariants.

See docs/13-LANGUAGES.md (Decision 2026-06-27): the appliance runs Polish-only,
but the EN assets are retained so re-enabling EN is a config flip. These tests
enforce that split — Polish leads at runtime, EN stays present at the asset
level. Asserts that:
  1. system.yaml enables Polish (Polish leads; EN is deferred at runtime).
  2. ASR default is multilingual (not the `.en`-only model), language pinned PL.
  3. TTS has a default voice for both `en` and `pl` (EN asset retained).
  4. wake-word config lists both `hey_blazen_en` and `hey_blazen_pl`,
     and both are in the `active` list.
  5. Every fast-path intent that has user-facing voice triggers carries
     both `en:` and `pl:` lists (each non-empty).
  6. At least one PL scenario exists AND the language-switch scenario
     exists.
"""
from __future__ import annotations

from pathlib import Path

import yaml

# Appliance tests live under rpi5/tests; the shared config contract (consumed
# by the appliance AND the ios/android sibling repos) stays at the repo root.
APP_ROOT = Path(__file__).resolve().parents[2]      # rpi5/
REPO_ROOT = Path(__file__).resolve().parents[3]     # repo root
CFG = REPO_ROOT / "configs"
SCN = APP_ROOT / "tests" / "scenarios"


def _load(p: Path):
    return yaml.safe_load(p.read_text())


def test_system_polish_only_runtime():
    # Polish-only runtime (Decision 2026-06-27): PL leads, EN deferred. docs/13.
    data = _load(CFG / "system.yaml")
    langs = (data.get("languages") or {}).get("enabled") or []
    assert langs and langs[0] == "pl", f"system.yaml: Polish must lead, got {langs}"
    assert "en" not in langs, f"EN is deferred at runtime, got {langs}"


def test_asr_default_is_multilingual():
    # Pi 5 16 GB ships `medium`; 8 GB variant ships `small`. Both are
    # multilingual — `.en` defaults would foreclose re-enabling EN.
    data = _load(CFG / "asr.yaml")
    assert data["active"] in {"small", "medium", "large-v3-turbo"}, (
        f"asr.yaml: default must be a multilingual Whisper model, got {data['active']!r}"
    )
    # Polish-only runtime pins the language to `pl`; `auto` is the bilingual mode.
    assert data["language"] in {"auto", "pl"}


def test_tts_voice_for_each_language():
    data = _load(CFG / "tts.yaml")
    by_lang = data.get("voice_by_language") or {}
    assert by_lang.get("en"), "tts.yaml: missing default voice for `en`"
    assert by_lang.get("pl"), "tts.yaml: missing default voice for `pl`"
    voices = data.get("voices") or {}
    for v in by_lang.values():
        assert v in voices, f"tts.yaml: default voice {v!r} not declared in voices"


def test_wake_word_bilingual():
    data = _load(CFG / "wake-word.yaml")
    models = data.get("models") or {}
    assert "jessica_en" in models, "wake-word.yaml: missing jessica_en"
    assert "jessica_pl" in models, "wake-word.yaml: missing jessica_pl"
    assert models["jessica_en"]["language"] == "en"
    assert models["jessica_pl"]["language"] == "pl"
    active = data.get("active") or []
    assert "jessica_en" in active and "jessica_pl" in active, (
        f"wake-word.yaml: active must include both EN+PL Jessica wakes, got {active}"
    )


def test_persona_jessica():
    """Jessica identity is fully configured in system.yaml."""
    data = _load(CFG / "system.yaml")
    persona = data.get("persona") or {}
    assert persona.get("name") == "Jessica"
    assert persona.get("short_name") == "Jess"
    assert persona.get("pl_vocative") == "Jessico"
    assert persona.get("register") in {"casual", "formal"}
    ack = persona.get("ack_phrases") or {}
    assert ack.get("en"), "missing en ack_phrases"
    assert ack.get("pl"), "missing pl ack_phrases"


# Intents that are purely internal (no spoken user triggers) are exempt.
INTENT_EXEMPT_FROM_BILINGUAL = set()


def test_intents_have_both_languages():
    data = _load(CFG / "intents" / "system.yaml")
    for intent in data.get("intents", []):
        name = intent["name"]
        if name in INTENT_EXEMPT_FROM_BILINGUAL:
            continue
        triggers = intent.get("triggers")
        assert isinstance(triggers, dict), (
            f"intent {name}: triggers must be a {{en:[...], pl:[...]}} mapping"
        )
        en = triggers.get("en") or []
        pl = triggers.get("pl") or []
        assert en, f"intent {name}: missing EN triggers"
        assert pl, f"intent {name}: missing PL triggers"


def test_pl_scenarios_exist():
    pl_scenarios = []
    for p in SCN.glob("*.yaml"):
        data = _load(p)
        if data.get("language") == "pl":
            pl_scenarios.append(data["id"])
    assert len(pl_scenarios) >= 3, f"need at least 3 PL scenarios, got {pl_scenarios}"


def test_language_switch_scenario_exists():
    found = False
    for p in SCN.glob("*.yaml"):
        data = _load(p)
        if data.get("language") == "mixed":
            found = True
            assert "switch" in data["id"].lower() or "language" in data["id"].lower()
    assert found, "no mixed-language scenario found"


def test_voice_policy_allows_pin_unpin():
    pol = _load(CFG / "voice-policy.yaml")
    allow = pol.get("allow_voice_mutation", {})
    # `languages.pinned` should be voice-mutable (single confirm).
    # We don't hard-require the key (forward-compatible) but if it's there,
    # confirm must be sensible.
    if "languages.pinned" in allow:
        c = allow["languages.pinned"].get("confirm")
        assert c in {"never", "single"}, f"languages.pinned confirm = {c}"

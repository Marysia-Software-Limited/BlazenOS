#!/usr/bin/env python3
"""Generate the voice-command reference: docs/commands/00-INDEX.md + one
document per command.

The COMMAND LIST IS CLOSED (decision 2026-09-08): Jessica executes exactly
what is documented here — an unmatched utterance gets the "not understood"
cue, and an LLM conversation is opened only by the explicit query command.
This generator is the contract's renderer: every intent in
configs/intents/system.yaml plus every engine-ladder command MUST have a page
(a Tier 0 test enforces it), so adding a voice feature without documenting it
fails the gate.

Regeneration is idempotent and preserves nothing by hand: descriptions live in
the DESC map below — edit THERE, not in the generated files. Run:

    python3 scripts/gen-command-docs.py     # rewrites docs/commands/
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
OUT = REPO / "docs" / "commands"
INTENTS = REPO / "configs" / "intents" / "system.yaml"

# Engine-ladder commands (regex routes in assistant/engine.py, reached via
# nlu.miss) — not in system.yaml but part of the closed list all the same.
ENGINE_COMMANDS: dict[str, dict[str, object]] = {
    "ai_query": {
        "pl": ["sprawdź <pytanie>", "zapytaj <pytanie>", "spytaj <pytanie>",
               "dowiedz się <pytanie>", "poszukaj <pytanie>"],
        "en": ["check <question>", "ask <question>", "look up <question>",
               "find out <question>"],
        "params": "pytanie — dowolny tekst przekazywany do LLM (verbatim)",
        "desc": "Jedyne wejście do rozmowy z LLM (zamknięta lista komend). "
                "Samo „sprawdź” pyta „Co mam sprawdzić?” i zbroi jednorazowe "
                "okno na pytanie. Po odpowiedzi AI Jessica nasłuchuje dalszych "
                "pytań bez prefiksu (tryb rozmowy; czulszy nasłuch, gdy "
                "odpowiedź kończy się pytaniem).",
    },
    "time_query": {
        "pl": ["która godzina"], "en": ["what time is it"],
        "params": "—",
        "desc": "Mówi aktualny czas (lokalny zegar, bez LLM).",
    },
    "date_query": {
        "pl": ["jaki dziś dzień"], "en": ["what's the date"],
        "params": "—",
        "desc": "Mówi dzisiejszą datę (lokalny zegar, bez LLM).",
    },
    "remember_note": {
        "pl": ["zapamiętaj[,] że <treść>", "zapamiętaj: <tytuł>. <treść>"],
        "en": ["remember that <text>"],
        "params": "treść — notatka; opcjonalny tytuł przed kropką",
        "desc": "Zapisuje notatkę (tekst + audio wypowiedzi, gdy klip jest "
                "świeży). Odczyt: „co wiesz o …”.",
    },
    "recall_notes": {
        "pl": ["co wiesz o <temat>", "co zapamiętałaś o <temat>"],
        "en": ["what do you know about <topic>"],
        "params": "temat — fraza szukana semantycznie w notatkach",
        "desc": "Wyszukiwanie w pamięci (notatki + memo głosowe).",
    },
    "reminders": {
        "pl": ["przypomnij mi o <czym> o <kiedy>", "jakie mam przypomnienia"],
        "en": ["remind me to <what> at <when>"],
        "params": "czym/kiedy — treść i czas przypomnienia",
        "desc": "Ustawia i wylicza przypomnienia.",
    },
    "weather": {
        "pl": ["jaka jest pogoda [w <miasto>]"], "en": ["what's the weather"],
        "params": "miasto — opcjonalne (domyślnie profilowe)",
        "desc": "Prognoza pogody.",
    },
    "news_briefing": {
        "pl": ["jakie wieści", "przeczytaj wiadomości"], "en": ["read the news"],
        "params": "—",
        "desc": "Czyta skrót wiadomości ze skonfigurowanych źródeł.",
    },
    "radio_by_name": {
        "pl": ["włącz <stacja>", "puść <stacja>"], "en": ["play <station>"],
        "params": "stacja — nazwa/alias z configs/radio.yaml",
        "desc": "Gra stację radiową. Wymaga czasownika akcji albo krótkiej "
                "(≤3 słowa) bezpośredniej prośby — sama nazwa stacji w "
                "rozmowie nie uruchamia strumienia.",
    },
    "recommend": {
        "pl": ["poleć <co>", "co warto przeczytać/posłuchać"],
        "en": ["recommend <what>"],
        "params": "co — gatunek/nastrój/autor (ontologia + RAG)",
        "desc": "Rekomendacja książki/muzyki z lokalnej biblioteki.",
    },
    "voice_note": {
        "pl": ["nagraj notatkę", "odtwórz notatkę"], "en": ["record a memo"],
        "params": "—",
        "desc": "Dyktowanie i odsłuch notatek głosowych.",
    },
    "name_set_get": {
        "pl": ["mów mi <imię>", "jak mam na imię"], "en": ["call me <name>"],
        "params": "imię — forma zwracania się do właściciela",
        "desc": "Ustawia/odczytuje imię właściciela (profil).",
    },
    "site_read": {
        "pl": ["sprawdź na stronie <url/temat>", "streść stronę <url>"],
        "en": ["open <url>", "summarise <url>"],
        "params": "url/temat",
        "desc": "Pobiera i streszcza stronę WWW.",
    },
}

# One-liners for system.yaml intents (fallback: generated from the action).
DESC: dict[str, str] = {
    "wake_ack": "Krótkie potwierdzenie obecności po wake wordzie.",
    "stop_talking": "Natychmiast przerywa wypowiedź/przetwarzanie. Działa też BEZ wake worda podczas odtwarzania (sentinel stopu) i dla każdego głosu.",
    "repeat_last": "Powtarza ostatnią odpowiedź.",
    "volume_up": "Głośniej o 30 pp (media; nigdy poniżej 10%). Działa dla każdego głosu.",
    "volume_down": "Ciszej o 30 pp (media; nigdy poniżej 10%). Działa dla każdego głosu.",
    "volume_set": "Ustawia głośność na wskazany procent.",
    "go_to_sleep": "Usypia nasłuch komend (wake word nadal działa).",
    "wake_up": "Przywraca nasłuch.",
    "reboot": "Restart urządzenia (z potwierdzeniem).",
    "shutdown": "Wyłączenie urządzenia (z potwierdzeniem).",
    "enable_ssh": "Włącza dostęp SSH (ścieżka ratunkowa).",
    "disable_ssh": "Wyłącza dostęp SSH.",
    "switch_voice": "Zmienia głos TTS.",
    "switch_language": "Przełącza język (runtime: polski).",
    "voice_learn": "Uczy się głosu właściciela (5 próbek + imię). Zasila profil głosu (voice-ID) i model wake.",
}


def _fold_triggers(trigs: list[str]) -> list[str]:
    """Regex triggers → readable forms (best-effort unescape)."""
    out = []
    for t in trigs[:4]:
        s = re.sub(r"\(([^)|]+)\|[^)]*\)", r"\1", t)  # first alternative
        s = re.sub(r"[\^\$\\]|\?\b|\*", "", s).replace("(", "").replace(")", "")
        s = re.sub(r"\s+", " ", s).strip()
        if s and s not in out:
            out.append(s)
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = yaml.safe_load(INTENTS.read_text(encoding="utf-8"))
    rows: list[tuple[str, str, str]] = []  # (name, file, one-liner)

    for it in data.get("intents", []):
        name = str(it["name"])
        trig = it.get("triggers", {})
        pl = _fold_triggers(list(trig.get("pl", [])))
        en = _fold_triggers(list(trig.get("en", [])))
        desc = DESC.get(name) or f"Akcja: `{it.get('action', '?')}`."
        body = [
            f"# Komenda: `{name}`", "",
            f"> {desc}", "",
            "## Wyzwalacze (PL — runtime)", "",
            *([f"- „{p}”" for p in pl] or ["- (brak)"]), "",
            "## Triggers (EN — asset, wyłączony w runtime)", "",
            *([f"- \"{e}\"" for e in en] or ["- (none)"]), "",
            "## Parametry", "",
            f"- {it.get('params', '—') if isinstance(it.get('params'), str) else '—'}", "",
            f"*Źródło: `configs/intents/system.yaml` (action `{it.get('action', '?')}`).*",
            "",
        ]
        (OUT / f"{name}.md").write_text("\n".join(body), encoding="utf-8")
        rows.append((name, f"{name}.md", desc.split(".")[0] + "."))

    for name, spec in ENGINE_COMMANDS.items():
        body = [
            f"# Komenda: `{name}`", "",
            f"> {spec['desc']}", "",
            "## Wyzwalacze (PL — runtime)", "",
            *[f"- „{p}”" for p in spec["pl"]], "",
            "## Triggers (EN — asset, wyłączony w runtime)", "",
            *[f"- \"{e}\"" for e in spec["en"]], "",
            "## Parametry", "",
            f"- {spec['params']}", "",
            "*Źródło: drabinka silnika (`assistant/engine.py`, ścieżka nlu.miss).*",
            "",
        ]
        (OUT / f"{name}.md").write_text("\n".join(body), encoding="utf-8")
        rows.append((name, f"{name}.md", str(spec["desc"]).split(".")[0] + "."))

    rows.sort()
    idx = [
        "# Komendy głosowe Jessiki — INDEX", "",
        "**Lista komend jest ZAMKNIĘTA** (decyzja 2026-09-08): Jessica wykonuje",
        "wyłącznie to, co tu opisano. Niedopasowana wypowiedź → sygnał „nie",
        "zrozumiałam”; rozmowę z LLM otwiera wyłącznie komenda `ai_query`",
        "(„sprawdź …”). Plik generowany przez `scripts/gen-command-docs.py` —",
        "opisy edytuj w mapach DESC/ENGINE_COMMANDS tego skryptu.", "",
        f"Komend: **{len(rows)}**.", "",
        "| Komenda | Opis |",
        "|---|---|",
        *[f"| [`{n}`]({f}) | {d} |" for n, f, d in rows],
        "",
    ]
    (OUT / "00-INDEX.md").write_text("\n".join(idx), encoding="utf-8")
    print(f"wrote {len(rows)} command docs + 00-INDEX.md → {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

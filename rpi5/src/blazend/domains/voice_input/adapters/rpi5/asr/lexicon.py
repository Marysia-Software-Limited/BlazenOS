"""Domain lexicon: ASR vocabulary biasing + conservative post-ASR correction.

Jessica's commands name a small, known world — intents, radio stations, music
tracks/artists/albums, audiobook titles/authors. Whisper doesn't know that and
happily transcribes "Dżesika, stop!" over a playing radio as "Jeszcze karp!
Top!" (observed 2026-08-27, conf 0.39). This module aggregates that known
vocabulary from the catalogs already in the tree and offers two uses:

1. ``hotwords()`` — a short, priority-ordered vocabulary string passed to
   faster-whisper's ``hotwords=`` decoder bias (``asr.yaml lexicon.hotwords``;
   OFF by default — the appliance's far-field mic echoed prompt words back,
   see the ``initial_prompt`` note in ``configs/asr.yaml``).
2. ``correct(text)`` — fuzzy span repair of a finished transcript against the
   lexicon (``lexicon.correct``, ON by default): sliding token n-grams are
   compared (folded + stemmed, shared ``audiobook_catalog.text_norm``) against
   lexicon phrases with stdlib ``difflib``; a span is replaced by the canonical
   phrase only when it is CLOSE but not already an exact match. Every entity
   resolver downstream (radio/music/books) matches exact stems only, so one
   ASR slip means a dead command — this layer is the missing forgiveness.

Sources are best-effort: a missing catalog file must never break the voice
path. Pure stdlib + PyYAML (via the config loader) — no new dependencies, the
CPU/appliance contract is untouched.
"""

from __future__ import annotations

import difflib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from audiobook_catalog.text_norm import _stem_phrase

from blazend.config import load

log = logging.getLogger("blazend.domains.voice_input.adapters.rpi5.asr.lexicon")

# Tier order = correction priority: a garbled short command should repair to a
# command word before it repairs to a similarly-spelled song title.
_TIER_WAKE = 0
_TIER_COMMANDS = 1
_TIER_STATIONS = 2
_TIER_BOOKS = 3
_TIER_MUSIC = 4

# Wake forms lead every tier ("dżesika" is the single most misheard word).
_WAKE_FORMS = ("Dżesika", "Jessica")

# Common words that must NEVER be "repaired" into an entity. Live incident
# 2026-08-29 15:04: ambient speech "…to jednak zwiększa…" had "jednak"
# corrected to the station "Jedynka" (ratio 0.83 — clears any sane cutoff)
# and, arriving inside a false-wake window, started the radio unasked.
# Fuzzy repair is for near-miss ENTITY words; everyday function words are
# never a misheard entity. Folded+stemmed forms, single tokens only.
_STOPWORDS = frozenset(
    _stem_phrase(w).strip() for w in (
        # pl — frequent function/filler words near entity names
        "jednak", "jutro", "teraz", "dobra", "dobrze", "jeszcze", "tylko",
        "wiesz", "jest", "słuchaj", "mamy", "chcesz", "może", "moze",
        "trzeba", "chyba", "zaraz", "wcale", "przecież", "właśnie", "całkiem",
        "dzisiaj", "wczoraj", "bardzo", "trochę", "gdzie", "kiedy", "czemu",
        # en counterparts
        "just", "still", "today", "tomorrow", "really", "maybe", "where",
        "which", "there",
    )
)

# Regex-trigger expansion: only patterns built from plain words + alternation
# groups are expanded; anything with other regex syntax is skipped (regexes
# stay the matcher's job — the lexicon only wants example phrases).
_SAFE_TRIGGER = re.compile(r"^[^\\\[\]{}*+.]*$")
_GROUP = re.compile(r"\(([^()]*)\)(\?)?")
_MAX_EXPANSIONS = 40

_DEFAULT_MUSIC_INDEX = "/var/lib/blazen/music-index.json"
_DEFAULT_AUDIOBOOKS = "/var/lib/blazen/audiobooks/catalog.json"
_DEFAULT_ONTOLOGY = "/etc/blazen/ontology/books.json"


def _split_top_alts(pattern: str) -> list[str]:
    """Split a trigger on TOP-LEVEL ``|`` (outside any parens). One regexy
    alternative must not sink its plain siblings: ``"wiadomo(ś|s)\\w*|wie(ś|s)ci"``
    keeps "wieści" in the lexicon even though the first branch is skipped —
    live 2026-09-11: "jakie wieści" had NO lexicon entry (the whole pattern
    was rejected for the one ``\\w*``), so "jakie wyjści" was never repaired
    and the news command kept coming back not-understood."""
    parts: list[str] = []
    cur: list[str] = []
    depth = 0
    for ch in pattern:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p for p in (s.strip() for s in parts) if p]


def _expand_trigger(pattern: str) -> list[str]:
    """Deterministically expand a simple alternation regex into phrases.

    ``"g(ł|l)o(ś|s)niej"`` → ``["głośniej", "glośniej", …]`` (diacritic variant
    first, so dedup-by-stem keeps it as the canonical spelling);
    ``"^(stop|cicho)$"`` → ``["stop", "cicho"]``. Top-level alternatives are
    split first and screened independently. Patterns using regex syntax
    beyond ``( | ) ? ^ $`` are skipped. Expansion is capped — diacritic groups
    multiply fast but collapse again under the folded key.
    """
    pattern = pattern.strip().removeprefix("^").removesuffix("$")
    alts = _split_top_alts(pattern)
    if len(alts) > 1:
        merged: list[str] = []
        for alt in alts:
            for phrase in _expand_trigger(alt):
                if phrase not in merged:
                    merged.append(phrase)
        return merged[:_MAX_EXPANSIONS]
    if not _SAFE_TRIGGER.match(pattern):
        return []
    variants = [pattern]
    while True:
        m = _GROUP.search(variants[0])
        if m is None:
            break
        nxt: list[str] = []
        alts = m.group(1).split("|")
        if m.group(2):  # optional group: the "absent" variant too
            alts = [*alts, ""]
        for v in variants:
            g = _GROUP.search(v)
            if g is None:
                nxt.append(v)
                continue
            for alt in alts:
                nxt.append(v[: g.start()] + alt + v[g.end() :])
        variants = nxt[:_MAX_EXPANSIONS]
    out: list[str] = []
    for v in variants:
        phrase = " ".join(v.split())
        # Metacharacters surviving expansion (e.g. a bare `,?` outside any
        # group) mean the pattern was regex-ier than it looked — drop it.
        if phrase and not re.search(r"[?()|^$]", phrase) and phrase not in out:
            out.append(phrase)
    return out


@dataclass(frozen=True)
class _Phrase:
    canonical: str  # spelling to put back into the transcript
    key: str        # folded + stemmed match key (no padding)
    tier: int


@dataclass
class Lexicon:
    """Aggregated domain vocabulary, grouped by priority tier."""

    tiers: dict[int, dict[str, str]] = field(default_factory=dict)
    # {tier: {key: canonical}} — first writer wins (diacritic spellings lead).

    def _add(self, tier: int, canonical: str) -> None:
        key = _stem_phrase(canonical).strip()
        if len(key) < 3:
            return  # too short to match anything safely
        bucket = self.tiers.setdefault(tier, {})
        bucket.setdefault(key, canonical.strip())

    # ----- building -------------------------------------------------------

    @classmethod
    def build(cls) -> Lexicon:
        """Aggregate every available vocabulary source (each best-effort)."""
        lex = cls()
        for form in _WAKE_FORMS:
            lex._add(_TIER_WAKE, form)
        lex._load_intents()
        lex._load_radio()
        lex._load_news()
        lex._load_books()
        lex._load_music()
        lex._load_learned()
        return lex

    def _load_learned(self) -> None:
        """Learned corrections (2026-09-08, phase 4 of the learning loop):
        phrases the user CONFIRMED or dictated as corrections, written by
        scripts/learn-sync.py to ``<state>/learning/lexicon-boost.yaml``.
        They join the command tier so the fuzzy repair snaps future misreads
        to what the user actually says. Best-effort like every source."""
        try:
            import yaml

            root = (Path(os.environ["BLAZEN_DATA_DIR"]).parent
                    if os.environ.get("BLAZEN_DATA_DIR")
                    else Path("/var/lib/blazen"))
            path = root / "learning" / "lexicon-boost.yaml"
            if not path.is_file():
                return
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for phrase in data.get("phrases", []) or []:
                self._add(_TIER_COMMANDS, str(phrase))
        except Exception:  # noqa: BLE001 — lexicon must never break ASR
            return

    def _load_intents(self) -> None:
        try:
            intents = load("intents/system").get("intents", []) or []
        except Exception:  # noqa: BLE001 — lexicon must never break ASR
            return
        for intent in intents:
            triggers = (intent or {}).get("triggers", {}) or {}
            for lang in ("pl", "en"):  # Polish first; EN assets retained
                for pattern in triggers.get(lang, []) or []:
                    for phrase in _expand_trigger(str(pattern)):
                        self._add(_TIER_COMMANDS, phrase)

    def _load_radio(self) -> None:
        try:
            stations = load("radio").get("stations", []) or []
        except Exception:  # noqa: BLE001
            return
        # Names first, aliases second: hotwords() takes the bucket in insertion
        # order, so every station's proper name gets in before any alias list.
        for st in stations:
            if name := st.get("name"):
                self._add(_TIER_STATIONS, str(name))
        for st in stations:
            for alias in st.get("aliases", []) or []:
                self._add(_TIER_STATIONS, str(alias))

    def _load_news(self) -> None:
        try:
            sources = load("news").get("sources", []) or []
        except Exception:  # noqa: BLE001
            return
        for src in sources:
            if name := (src or {}).get("name"):
                self._add(_TIER_STATIONS, str(name))

    def _load_books(self) -> None:
        catalog = Path(os.environ.get("BLAZEN_AUDIOBOOKS_CATALOG", _DEFAULT_AUDIOBOOKS))
        for book in _read_json(catalog).get("books", []) or []:
            for text in ((book or {}).get("title"), (book or {}).get("author")):
                if text:
                    self._add(_TIER_BOOKS, str(text))
        ontology = Path(os.environ.get("BLAZEN_ONTOLOGY", _DEFAULT_ONTOLOGY))
        for author in _read_json(ontology).get("author_nationality", {}) or {}:
            self._add(_TIER_BOOKS, str(author))

    def _load_music(self) -> None:
        index = Path(os.environ.get("BLAZEN_MUSIC_INDEX", _DEFAULT_MUSIC_INDEX))
        for track in _read_json(index).get("tracks", []) or []:
            for fld in ("artist", "album", "title"):
                if text := (track or {}).get(fld):
                    self._add(_TIER_MUSIC, str(text))

    # ----- use ------------------------------------------------------------

    def hotwords(self, max_chars: int = 120) -> str:
        """Priority-ordered vocabulary head for whisper's ``hotwords=`` bias.

        Wake forms and station names carry the most correction value per
        character; commands are already short function words whisper knows, so
        stations outrank them here (order ≠ the correction tiers).
        """
        seen: set[str] = set()
        picked: list[str] = []
        length = 0
        for tier in (_TIER_WAKE, _TIER_STATIONS, _TIER_COMMANDS, _TIER_BOOKS, _TIER_MUSIC):
            for canonical in self.tiers.get(tier, {}).values():
                low = canonical.lower()
                if low in seen:
                    continue
                cost = len(canonical) + (2 if picked else 0)
                if length + cost > max_chars:
                    break
                seen.add(low)
                picked.append(canonical)
                length += cost
        return ", ".join(picked)

    def correct(self, text: str, *, fuzzy_ratio: float = 0.78) -> tuple[str, list[str]]:
        """Repair near-miss entity/command spans in a transcript.

        Slides 4→1-token windows over ``text``; a window whose folded+stemmed
        form is CLOSE to a lexicon key (``difflib`` ratio ≥ ``fuzzy_ratio``)
        but not identical is replaced by that phrase's canonical spelling.
        Longest spans win; replacements never overlap; exact matches are left
        alone (the downstream resolvers already handle them). Returns the
        repaired text plus ``["orig → canonical", …]`` for logging.
        """
        tokens = list(re.finditer(r"\w+", text, re.UNICODE))
        if not tokens:
            return text, []
        replacements: list[tuple[int, int, str, str]] = []  # (start, end, orig, canon)
        taken: list[tuple[int, int]] = []
        # Protect EXACT phrase occurrences first: a span the lexicon already
        # knows verbatim is something whisper got right, so no window
        # overlapping it may be "repaired". Live bug 2026-10-10: a correct
        # "zadzwoń do błazna" was rewritten to "zadzwoń dzwoń błazen" (and
        # stopped matching call_peer) because the inner "do błazna" window
        # fuzzy-matched "dzwoń błazen" — the terse-form expansion of the SAME
        # intent's other trigger. Longest spans claim first, same as repairs.
        for size in (4, 3, 2, 1):
            for i in range(len(tokens) - size + 1):
                start, end = tokens[i].start(), tokens[i + size - 1].end()
                if any(start < e and s < end for s, e in taken):
                    continue
                window = text[start:end]
                if size > 1 and re.search(r"[,.!?;:]", window):
                    continue
                key = _stem_phrase(window).strip()
                if len(key) >= 3 and any(key in b for b in self.tiers.values()):
                    taken.append((start, end))
        for size in (4, 3, 2, 1):
            for i in range(len(tokens) - size + 1):
                start, end = tokens[i].start(), tokens[i + size - 1].end()
                if any(start < e and s < end for s, e in taken):
                    continue
                window = text[start:end]
                # Entities don't span sentence punctuation — a window crossing
                # a comma glues the wake-name onto the command ("Jessica, włącz")
                # and invites a false repair of the command word.
                if size > 1 and re.search(r"[,.!?;:]", window):
                    continue
                key = _stem_phrase(window).strip()
                if len(key) < 3:
                    continue
                # Never repair common words into an entity ("jednak" is not a
                # misheard "Jedynka"; "to wiesz" is not "co wiesz" — live
                # incidents 2026-08-29/30). Applies to a bare stopword AND to
                # any window built ONLY of stopwords.
                window_keys = [_stem_phrase(t).strip()
                               for t in re.findall(r"\w+", window)]
                if window_keys and all(k in _STOPWORDS for k in window_keys):
                    continue
                match = self._closest(key, fuzzy_ratio)
                if match is None:
                    continue
                # A repair may SHRINK or respell, never GROW: replacing a
                # window with a longer phrase invents words the user did not
                # say ("jak jest" must not become "jaka jest data" —
                # found 2026-09-11 while widening the trigger expansion).
                if len(match.split()) > size:
                    continue
                replacements.append((start, end, window, match))
                taken.append((start, end))
        if not replacements:
            return text, []
        corrections = [f"{orig!r} → {canon!r}" for _, _, orig, canon in replacements]
        out = text
        for start, end, _, canon in sorted(replacements, reverse=True):
            out = out[:start] + canon + out[end:]
        return out, corrections

    def _closest(self, key: str, fuzzy_ratio: float) -> str | None:
        """Best fuzzy (non-exact) canonical for ``key``, tier priority first."""
        if any(key in bucket for bucket in self.tiers.values()):
            return None  # already exact somewhere — resolvers handle it, don't touch
        for tier in sorted(self.tiers):
            bucket = self.tiers[tier]
            hits = difflib.get_close_matches(key, bucket.keys(), n=1, cutoff=fuzzy_ratio)
            if hits:
                return bucket[hits[0]]
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


_CACHED: Lexicon | None = None


def get_lexicon() -> Lexicon:
    """Process-wide lexicon, built once. Catalogs change rarely (an install or
    an index rebuild) and the ASR service restarts with the pipeline, so a
    simple build-once cache is enough — no mtime polling on the hot path."""
    global _CACHED  # noqa: PLW0603 — module-level cache, single-threaded asyncio
    if _CACHED is None:
        _CACHED = Lexicon.build()
        sizes = {t: len(b) for t, b in sorted(_CACHED.tiers.items())}
        log.info("lexicon built: %s phrases by tier %s",
                 sum(sizes.values()), sizes)
    return _CACHED


__all__ = ["Lexicon", "get_lexicon"]

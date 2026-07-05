#!/usr/bin/env python3
"""Build the book ontology — the metadata/knowledge layer of Jessica's RAG.

The semantic index answers by MEANING, but spoken requests also carry structured
signals — a genre ("coś przygodowego"), an epoch/era ("klasyka", "romantyczne")
or a nationality ("francuska klasyka"). This script distils the Wolne Lektury
``catalog.json`` (genre + epoch per book) plus a curated author→nationality table
into ``configs/ontology/books.json``, which ``assistant/recommend.py`` uses to
EXPAND the query before retrieval and to RERANK candidates by structured match.

The taxonomy is derived from the catalogue's own genre/epoch strings, so it stays
in sync with what is actually on the card. Bilingual (PL leads, EN retained).

Usage:
  scripts/build-ontology.py --catalog /var/lib/blazen/audiobooks/catalog.json \
      --out configs/ontology/books.json
"""
from __future__ import annotations

import argparse
import json
import sys
import unicodedata
from pathlib import Path

_PL = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")


def fold(text: str) -> str:
    """Accent-insensitive lowercase key (matches recommend.py / radio.py folding)."""
    return unicodedata.normalize("NFKC", text).translate(_PL).lower().strip()


# User phrasing (PL + EN, folded on load) → catalogue GENRE substrings (folded).
# Substring match so "powiesc dla dzieci i mlodziezy" is caught by "powiesc".
_GENRE_SYNONYMS: dict[str, list[str]] = {
    "poezja": ["wiersz", "sonet", "piesn", "fraszka", "tren", "ballada", "poemat"],
    "poetry": ["wiersz", "sonet", "piesn", "fraszka", "tren", "ballada", "poemat"],
    "wiersz": ["wiersz", "sonet", "poemat"],
    "wiersze": ["wiersz", "sonet", "poemat"],
    "bajka": ["bajka", "przypowiesc"],
    "bajki": ["bajka", "przypowiesc"],
    "fable": ["bajka", "przypowiesc"],
    "basn": ["basn", "legenda"],
    "basnie": ["basn", "legenda"],
    "fairy tale": ["basn", "legenda"],
    "powiesc": ["powiesc"],
    "novel": ["powiesc"],
    "opowiadanie": ["opowiadanie", "nowela"],
    "opowiadania": ["opowiadanie", "nowela"],
    "nowela": ["nowela", "opowiadanie"],
    "short story": ["opowiadanie", "nowela"],
    "dla dzieci": ["basn", "bajka", "dla dzieci"],
    "children": ["basn", "bajka", "dla dzieci"],
    "przygodowe": ["przygodow"],
    "przygodowa": ["przygodow"],
    "adventure": ["przygodow"],
    "obyczajowe": ["obyczajow"],
    "obyczajowa": ["obyczajow"],
    "dramat": ["tragedia", "komedia", "dramat"],
    "teatr": ["tragedia", "komedia", "dramat"],
    "drama": ["tragedia", "komedia", "dramat"],
}

# User phrasing → catalogue EPOCH values (folded, substring).
_EPOCH_SYNONYMS: dict[str, list[str]] = {
    "klasyka": ["starozytnosc", "renesans", "barok", "oswiecenie"],
    "klasyczne": ["starozytnosc", "renesans", "barok", "oswiecenie"],
    "classic": ["starozytnosc", "renesans", "barok", "oswiecenie"],
    "classical": ["starozytnosc", "renesans", "barok", "oswiecenie"],
    "antyczne": ["starozytnosc"],
    "starozytne": ["starozytnosc"],
    "antiquity": ["starozytnosc"],
    "romantyczne": ["romantyzm"],
    "romantyzm": ["romantyzm"],
    "romantic": ["romantyzm"],
    "wspolczesne": ["wspolczesnosc", "dwudziestolecie"],
    "nowoczesne": ["wspolczesnosc", "dwudziestolecie"],
    "contemporary": ["wspolczesnosc", "dwudziestolecie"],
    "oswiecenie": ["oswiecenie"],
    "enlightenment": ["oswiecenie"],
    "renesans": ["renesans"],
    "renaissance": ["renesans"],
    "sredniowiecze": ["sredniowiecze"],
    "medieval": ["sredniowiecze"],
    "pozytywizm": ["pozytywizm"],
    "modernizm": ["modernizm"],
    "mloda polska": ["modernizm"],
    "barok": ["barok"],
    "baroque": ["barok"],
}

# Curated author → nationality (folded surname/full-name substrings → nationality
# tag). Only the well-known classics need covering; the LLM reasoning layer knows
# the rest. Keyed by a folded substring of the author name as it appears in WL.
_AUTHOR_NATIONALITY: dict[str, str] = {
    "verne": "francuski", "dumas": "francuski", "balzac": "francuski",
    "baudelaire": "francuski", "flaubert": "francuski", "maupassant": "francuski",
    "hugo": "francuski", "moliere": "francuski", "voltaire": "francuski",
    "stendhal": "francuski", "zola": "francuski", "rimbaud": "francuski",
    "dickens": "angielski", "shakespeare": "angielski", "wilde": "angielski",
    "kipling": "angielski", "defoe": "angielski", "swift": "angielski",
    "conrad": "angielski", "carroll": "angielski", "shelley": "angielski",
    "poe": "amerykanski", "twain": "amerykanski", "london": "amerykanski",
    "alcott": "amerykanski", "whitman": "amerykanski",
    "dostojewski": "rosyjski", "tolstoj": "rosyjski", "czechow": "rosyjski",
    "gogol": "rosyjski", "turgieniew": "rosyjski", "puszkin": "rosyjski",
    "andersen": "dunski",
    "grimm": "niemiecki", "goethe": "niemiecki", "hoffmann": "niemiecki",
    "kafka": "niemiecki", "nietzsche": "niemiecki", "schiller": "niemiecki",
    "homer": "grecki", "sofokles": "grecki", "arystofanes": "grecki",
    "platon": "grecki", "arystoteles": "grecki",
    "horacy": "rzymski", "owidiusz": "rzymski", "wergiliusz": "rzymski",
    "cervantes": "hiszpanski",
    "dante": "wloski", "boccaccio": "wloski",
    "ibsen": "norweski",
    "conan doyle": "angielski", "stevenson": "angielski",
    # Polish — the bulk of the Wolne Lektury catalogue.
    "krasicki": "polski", "lesmian": "polski", "kochanowski": "polski",
    "mickiewicz": "polski", "slowacki": "polski", "norwid": "polski",
    "sienkiewicz": "polski", "boleslaw prus": "polski", "zeromski": "polski",
    "orzeszkowa": "polski", "konopnicka": "polski", "reymont": "polski",
    "tuwim": "polski", "zelenski": "polski", "baczynski": "polski",
    "borowski": "polski", "schulz": "polski", "jasienski": "polski",
    "grabinski": "polski", "zapolska": "polski", "morsztyn": "polski",
    "nalkowska": "polski", "fredro": "polski", "kraszewski": "polski",
    "galczynski": "polski", "staff": "polski", "asnyk": "polski",
    "wyspianski": "polski", "przybos": "polski", "oppman": "polski",
    "kornhauser": "polski", "beresewicz": "polski", "fraczek": "polski",
    "braun": "polski", "biedrzycki": "polski",
    # Others present in the catalogue.
    "safona": "grecki", "ajschylos": "grecki",
    "szewczenko": "ukrainski", "ukrainka": "ukrainski",
}

# User phrasing → nationality tag (folded).
_NATIONALITY_SYNONYMS: dict[str, str] = {
    "francuska": "francuski", "francuskie": "francuski", "french": "francuski",
    "angielska": "angielski", "angielskie": "angielski", "english": "angielski",
    "brytyjska": "angielski", "british": "angielski",
    "rosyjska": "rosyjski", "rosyjskie": "rosyjski", "russian": "rosyjski",
    "amerykanska": "amerykanski", "american": "amerykanski",
    "niemiecka": "niemiecki", "german": "niemiecki",
    "polska": "polski", "polskie": "polski", "polish": "polski",
    "grecka": "grecki", "greek": "grecki",
    "wloska": "wloski", "italian": "wloski",
    "hiszpanska": "hiszpanski", "spanish": "hiszpanski",
    "dunska": "dunski", "danish": "dunski",
    "ukrainska": "ukrainski", "ukrainskie": "ukrainski", "ukrainian": "ukrainski",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", type=Path, default=Path("/var/lib/blazen/audiobooks/catalog.json"))
    ap.add_argument("--out", type=Path, default=Path("configs/ontology/books.json"))
    args = ap.parse_args()

    try:
        books = json.loads(args.catalog.read_text(encoding="utf-8")).get("books", [])
    except (OSError, ValueError) as e:
        print(f"cannot read catalog {args.catalog}: {e}", file=sys.stderr)
        return 1

    genres = sorted({str(b.get("genre", "")).strip() for b in books if b.get("genre")})
    epochs = sorted({str(b.get("epoch", "")).strip() for b in books if b.get("epoch")})
    # Only keep author→nationality entries for authors actually on the card, and
    # attach the nationality by matching a curated substring against each author.
    authors: dict[str, str] = {}
    for b in books:
        who = str(b.get("author", "")).strip()
        if not who or who in authors:
            continue
        folded = fold(who)
        for key, nat in _AUTHOR_NATIONALITY.items():
            if key in folded:
                authors[who] = nat
                break

    out = {
        "version": 1,
        "generated_from": str(args.catalog),
        "genres": genres,
        "epochs": epochs,
        "genre_synonyms": _GENRE_SYNONYMS,
        "epoch_synonyms": _EPOCH_SYNONYMS,
        "nationality_synonyms": _NATIONALITY_SYNONYMS,
        "author_nationality": authors,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ontology: {len(genres)} genres, {len(epochs)} epochs, "
          f"{len(authors)} authors tagged → {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

# Komenda: `list_audiobooks`

> Akcja: `tool_call`.

## Wyzwalacze (PL — runtime)

- „jakie masz dostępne|są książkw|audiobookw|lekturw”
- „co maszesz do przeczytać|poczytać|słuchać”
- „lista książek|audiobookw|jakie książki”

## Triggers (EN — asset, wyłączony w runtime)

- "what books do you have"
- "list the books|what can you read"

## Odpowiedź

Wymienia **wszystkie** dostępne audiobooki po kolei (w porządku katalogu), np.
„Mam 28 audiobooków. Po kolei: 20 000 mil podmorskiej żeglugi, Abuzei i Tair, …
Powiedz „przeczytaj” i tytuł.” Zwykłe „stop” przerywa czytanie listy.
Fraza: `book.list` w `configs/phrases.yaml` (od 2026-10-12; wcześniej 3 przykłady).

## Parametry

- —

*Źródło: `configs/intents/system.yaml` (action `tool_call`).*

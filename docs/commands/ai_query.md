# Komenda: `ai_query`

> Jedyne wejście do rozmowy z LLM (zamknięta lista komend). Samo „sprawdź” pyta „Co mam sprawdzić?” i zbroi jednorazowe okno na pytanie. Po odpowiedzi AI Jessica nasłuchuje dalszych pytań bez prefiksu (tryb rozmowy; czulszy nasłuch, gdy odpowiedź kończy się pytaniem).

## Wyzwalacze (PL — runtime)

- „sprawdź <pytanie>”
- „zapytaj <pytanie>”
- „spytaj <pytanie>”
- „dowiedz się <pytanie>”
- „poszukaj <pytanie>”

## Triggers (EN — asset, wyłączony w runtime)

- "check <question>"
- "ask <question>"
- "look up <question>"
- "find out <question>"

## Parametry

- pytanie — dowolny tekst przekazywany do LLM (verbatim)

*Źródło: drabinka silnika (`assistant/engine.py`, ścieżka nlu.miss).*

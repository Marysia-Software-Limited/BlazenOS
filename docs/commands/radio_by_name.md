# Komenda: `radio_by_name`

> Gra stację radiową. Wymaga czasownika akcji albo krótkiej (≤3 słowa) bezpośredniej prośby — sama nazwa stacji w rozmowie nie uruchamia strumienia.

## Wyzwalacze (PL — runtime)

- „włącz <stacja>”
- „puść <stacja>”

## Triggers (EN — asset, wyłączony w runtime)

- "play <station>"

## Parametry

- stacja — nazwa/alias z configs/radio.yaml

*Źródło: drabinka silnika (`assistant/engine.py`, ścieżka nlu.miss).*

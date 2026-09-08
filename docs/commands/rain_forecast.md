# Komenda: `rain_forecast`

> Akcja: `tool_call`.

## Wyzwalacze (PL — runtime)

- „?:czy ??:będzie ?bpadw.b?:ws+P<place>[a-ząćęłńóśźż'’- ]+?s”
- „?:czy ?P<when>jutro.bpadw”
- „bpadw.P<when>jutro”
- „kiedy będzie ?bpadw”

## Triggers (EN — asset, wyłączony w runtime)

- "rain.bins+P<place>[a-z'’- ]+?s"
- "P<when>tomorrow.brainb"
- "brainb.P<when>tomorrow"
- "when.brainb"

## Parametry

- —

*Źródło: `configs/intents/system.yaml` (action `tool_call`).*

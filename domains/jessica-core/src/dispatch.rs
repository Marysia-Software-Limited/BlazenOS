//! Portable intent dispatch.
//!
//! Maps a matched command intent (name + slot params) to the action that
//! satisfies it. Tool-backed commands (weather, news, web, radio, recall)
//! become a [`ToolCall`] the mind sends to the Python tool server over the
//! `tool.request` seam; everything else is left to the mind (chat) or future
//! in-core actions. This is the **device-independent dispatch** — the same
//! routing mobile would use. Tool *execution* is ML/API glue (Python on the
//! Pi); only this routing is Rust. See `docs/14-RUST-PYTHON-SPLIT.md` §1.

use std::collections::HashMap;

use serde_json::{json, Value};

/// A call to a Python tool: a `tool.request` payload-in-waiting.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ToolCall {
    /// Tool name (e.g. `"weather.query"`).
    pub tool: String,
    /// Tool-specific arguments (the `tool.request` `args` object).
    pub args: Value,
}

impl ToolCall {
    fn new(tool: &str, args: Value) -> Self {
        Self {
            tool: tool.to_string(),
            args,
        }
    }
}

/// The dispatch outcome for an intent.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Dispatch {
    /// Run a Python tool over the bus.
    Tool(ToolCall),
    /// Not a tool-backed command — the mind handles it (or it's unknown).
    None,
}

/// Route a matched command `intent` (+ its captured `params`) to an action.
///
/// Only the tool-backed commands are mapped here; config mutations, clock,
/// language, and the in-core memory writes are dispatched elsewhere.
pub fn dispatch(intent: &str, params: &HashMap<String, String>) -> Dispatch {
    let arg = |key: &str| params.get(key).cloned();
    match intent {
        "weather_query" => Dispatch::Tool(ToolCall::new(
            "weather.query",
            json!({ "place": arg("place") }),
        )),
        "news_brief" => Dispatch::Tool(ToolCall::new("news.brief", json!({}))),
        "remember_note" => Dispatch::Tool(ToolCall::new(
            "context.remember",
            json!({ "text": arg("text").unwrap_or_default() }),
        )),
        "set_name" => Dispatch::Tool(ToolCall::new(
            "context.set_name",
            json!({ "name": arg("name").unwrap_or_default() }),
        )),
        "web_lookup" => Dispatch::Tool(ToolCall::new(
            "web.lookup",
            json!({ "query": arg("query").unwrap_or_default() }),
        )),
        "radio_play" => Dispatch::Tool(ToolCall::new(
            "radio.play",
            json!({ "query": arg("query").unwrap_or_default() }),
        )),
        "radio_stop" => Dispatch::Tool(ToolCall::new("radio.stop", json!({}))),
        "list_notes" => Dispatch::Tool(ToolCall::new("context.recall", json!({}))),
        "list_reminders" => Dispatch::Tool(ToolCall::new("context.recall_reminders", json!({}))),
        _ => Dispatch::None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params(pairs: &[(&str, &str)]) -> HashMap<String, String> {
        pairs
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect()
    }

    #[test]
    fn weather_carries_place_slot() {
        let d = dispatch("weather_query", &params(&[("place", "Gdańsk")]));
        assert_eq!(
            d,
            Dispatch::Tool(ToolCall::new("weather.query", json!({"place": "Gdańsk"})))
        );
    }

    #[test]
    fn weather_without_place_is_null() {
        let d = dispatch("weather_query", &params(&[]));
        match d {
            Dispatch::Tool(tc) => {
                assert_eq!(tc.tool, "weather.query");
                assert!(tc.args["place"].is_null());
            }
            _ => panic!("expected tool"),
        }
    }

    #[test]
    fn radio_play_defaults_query_empty() {
        let d = dispatch("radio_play", &params(&[]));
        assert_eq!(
            d,
            Dispatch::Tool(ToolCall::new("radio.play", json!({"query": ""})))
        );
    }

    #[test]
    fn news_and_recall_take_no_args() {
        assert_eq!(
            dispatch("news_brief", &params(&[])),
            Dispatch::Tool(ToolCall::new("news.brief", json!({})))
        );
        assert_eq!(
            dispatch("list_notes", &params(&[])),
            Dispatch::Tool(ToolCall::new("context.recall", json!({})))
        );
        assert_eq!(
            dispatch("list_reminders", &params(&[])),
            Dispatch::Tool(ToolCall::new("context.recall_reminders", json!({})))
        );
    }

    #[test]
    fn memory_writes_carry_their_slots() {
        assert_eq!(
            dispatch("remember_note", &params(&[("text", "kup mleko")])),
            Dispatch::Tool(ToolCall::new(
                "context.remember",
                json!({"text": "kup mleko"})
            ))
        );
        assert_eq!(
            dispatch("set_name", &params(&[("name", "Paweł")])),
            Dispatch::Tool(ToolCall::new("context.set_name", json!({"name": "Paweł"})))
        );
    }

    #[test]
    fn unknown_intent_is_none() {
        assert_eq!(dispatch("volume_up", &params(&[])), Dispatch::None);
        assert_eq!(dispatch("chat", &params(&[])), Dispatch::None);
    }
}

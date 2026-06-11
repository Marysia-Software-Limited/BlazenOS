//! Fast-path intent router.
//!
//! Reads the shared `configs/intents/system.yaml` and matches a
//! spoken transcript against language-tagged regex triggers. Mirrors
//! the Dart `IntentRouter` in `rachel/lib/intents/router.dart`. The
//! conformance test cross-runs both implementations against the same
//! corpus.

use std::collections::HashMap;

use regex::Regex;
use serde::{Deserialize, Serialize};

use crate::{CoreError, Result};

/// Match outcome.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct IntentMatch {
    /// Canonical intent name from the YAML (e.g. `"volume_up"`).
    pub name: String,
    /// Which language pack matched (`"en"` / `"pl"`).
    pub language: String,
    /// Action verb (`"mutate"`, `"tool_call"`, `"noop"`, ...).
    pub action: String,
    /// Tool name when `action == "tool_call"`.
    pub tool: Option<String>,
    /// Captured named groups from the regex.
    pub params: HashMap<String, String>,
    /// Confirm level (`"never"` / `"single"` / `"loud"` / `"double_loud"`).
    pub confirm: String,
}

/// One compiled intent.
struct CompiledIntent {
    name: String,
    triggers: HashMap<String, Vec<Regex>>,
    action: String,
    tool: Option<String>,
    confirm: String,
}

/// Fast-path matcher.
pub struct IntentRouter {
    intents: Vec<CompiledIntent>,
}

#[derive(Debug, Deserialize)]
struct RawDoc {
    intents: Vec<RawIntent>,
}

#[derive(Debug, Deserialize)]
struct RawIntent {
    name: String,
    #[serde(default)]
    triggers: HashMap<String, Vec<String>>,
    action: String,
    #[serde(default)]
    tool: Option<String>,
    #[serde(default)]
    confirm: Option<String>,
}

impl IntentRouter {
    /// Load + compile intents from a YAML string. The format matches
    /// `blazen_os/configs/intents/system.yaml`.
    pub fn from_yaml(yaml: &str) -> Result<Self> {
        let raw: RawDoc = serde_yaml::from_str(yaml)?;
        let mut intents = Vec::with_capacity(raw.intents.len());
        for intent in raw.intents {
            let mut triggers = HashMap::new();
            for (lang, patterns) in intent.triggers {
                let mut compiled = Vec::with_capacity(patterns.len());
                for pat in patterns {
                    let pat = python_to_rust_regex(&pat);
                    compiled.push(Regex::new(&format!("(?i){pat}"))?);
                }
                triggers.insert(lang, compiled);
            }
            intents.push(CompiledIntent {
                name: intent.name,
                triggers,
                action: intent.action,
                tool: intent.tool,
                confirm: intent.confirm.unwrap_or_else(|| "never".into()),
            });
        }
        Ok(Self { intents })
    }

    /// Number of compiled intents.
    pub fn len(&self) -> usize {
        self.intents.len()
    }

    /// True iff no intents are loaded.
    pub fn is_empty(&self) -> bool {
        self.intents.is_empty()
    }

    /// Match a transcript to the first applicable intent. Tries
    /// `language` first, then the other.
    pub fn match_intent(&self, transcript: &str, language: &str) -> Option<IntentMatch> {
        let t = transcript.trim();
        let langs = lang_order(language);
        for intent in &self.intents {
            for lang in &langs {
                let Some(list) = intent.triggers.get(*lang) else { continue };
                for re in list {
                    if let Some(captures) = re.captures(t) {
                        let mut params = HashMap::new();
                        for name in re.capture_names().flatten() {
                            if let Some(val) = captures.name(name) {
                                params.insert(name.to_string(), val.as_str().to_string());
                            }
                        }
                        return Some(IntentMatch {
                            name: intent.name.clone(),
                            language: lang.to_string(),
                            action: intent.action.clone(),
                            tool: intent.tool.clone(),
                            confirm: intent.confirm.clone(),
                            params,
                        });
                    }
                }
            }
        }
        None
    }
}

fn lang_order(detected: &str) -> Vec<&'static str> {
    let mut order = Vec::with_capacity(2);
    let detected = if detected == "pl" || detected == "en" {
        detected
    } else {
        "pl"
    };
    order.push(if detected == "pl" { "pl" } else { "en" });
    order.push(if detected == "pl" { "en" } else { "pl" });
    order
}

/// Python `(?P<name>...)` → Rust `(?P<name>...)` (Rust's regex crate
/// accepts both — but the shared YAML uses the Python form). No-op
/// in practice today; kept as a guard against future drift.
fn python_to_rust_regex(p: &str) -> String {
    p.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    const YAML: &str = r#"
version: 1
intents:
  - name: volume_up
    triggers:
      en:
        - "(volume|louder) (up|higher)?"
        - "louder"
      pl:
        - "g(ł|l)o(ś|s)niej"
        - "podg(ł|l)o(ś|s)nij"
    action: mutate

  - name: volume_set
    triggers:
      en:
        - "set (the )?volume (to )?(?P<value>\\d{1,3})( percent)?"
      pl:
        - "ustaw (g(ł|l)o(ś|s)no(ś|s)(ć|c) na )?(?P<value>\\d{1,3})( procent)?"
    action: mutate

  - name: fabric_pair
    triggers:
      en:
        - "(jess|jessica),? pair (a )?new device"
      pl:
        - "(jess|jessico),? sparuj nowe urz(ą|a)dzenie"
    action: tool_call
    tool: fabric.start_pairing
"#;

    #[test]
    fn matches_pl_volume_up() {
        let r = IntentRouter::from_yaml(YAML).unwrap();
        let m = r.match_intent("głośniej", "pl").unwrap();
        assert_eq!(m.name, "volume_up");
        assert_eq!(m.language, "pl");
    }

    #[test]
    fn matches_en_volume_up() {
        let r = IntentRouter::from_yaml(YAML).unwrap();
        let m = r.match_intent("louder", "en").unwrap();
        assert_eq!(m.name, "volume_up");
        assert_eq!(m.language, "en");
    }

    #[test]
    fn captures_value_group() {
        let r = IntentRouter::from_yaml(YAML).unwrap();
        let m = r.match_intent("ustaw głośność na 70 procent", "pl").unwrap();
        assert_eq!(m.name, "volume_set");
        assert_eq!(m.params.get("value").map(String::as_str), Some("70"));
    }

    #[test]
    fn matches_fabric_pair_pl() {
        let r = IntentRouter::from_yaml(YAML).unwrap();
        let m = r.match_intent("Jessico, sparuj nowe urządzenie", "pl").unwrap();
        assert_eq!(m.name, "fabric_pair");
        assert_eq!(m.action, "tool_call");
        assert_eq!(m.tool.as_deref(), Some("fabric.start_pairing"));
    }

    #[test]
    fn returns_none_for_garbage() {
        let r = IntentRouter::from_yaml(YAML).unwrap();
        assert!(r.match_intent("zupełnie losowe zdanie", "pl").is_none());
    }

    #[test]
    fn falls_back_to_other_language() {
        let r = IntentRouter::from_yaml(YAML).unwrap();
        // Trigger is EN-only but utterance says louder; lang param is
        // pl — the matcher should still find it via the fall-back.
        let m = r.match_intent("louder", "pl").unwrap();
        assert_eq!(m.name, "volume_up");
        assert_eq!(m.language, "en");
    }
}

//! Portable ai-orchestrator routing types.
//!
//! Where a conversation turn gets answered. The Pi resolves this in two
//! seams today: `registry.select_chat_llm` picks the **primary** chat
//! backend (a reachable LAN Ollama box when `BLAZEN_LLM_OLLAMA_URL` is
//! set, else the on-device Bielik), and the engine then **escalates**
//! through cloud fallbacks (OpenAI, then Gemini, then a canned
//! "needs a model/key" terminal). This module folds both into one
//! portable [`RoutePlan`]: an ordered list of [`Backend`]s where the
//! first available one answers.
//!
//! The core carries only the *policy* — the order and the first-available
//! rule. Whether a given backend is reachable is an adapter concern
//! (`LocalLlm.available`, `OllamaLlm.available`, an API key being set),
//! injected as the `available` predicate. See
//! `docs/19-DOMAIN-ARCHITECTURE.md`.

use serde::{Deserialize, Serialize};

/// A chat backend a turn can be routed to.
///
/// Named by the tag the Pi engine records in `Reply.data["engine"]`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub enum Backend {
    /// On-device embedded model (Bielik via llama.cpp). Offline, private.
    #[serde(rename = "local")]
    Local,
    /// A reachable LAN box (the dev Ollama GPU). Off-device but on the
    /// private network — the registry prefers it as the primary when set.
    #[serde(rename = "ollama")]
    Ollama,
    /// OpenAI cloud.
    #[serde(rename = "openai")]
    OpenAi,
    /// Gemini cloud.
    #[serde(rename = "gemini")]
    Gemini,
}

impl Backend {
    /// True for backends that send the turn off the local network.
    ///
    /// Routing to these is opt-in only — the appliance makes no outbound
    /// cloud calls during normal operation (see `CLAUDE.md` §2).
    pub fn is_cloud(self) -> bool {
        matches!(self, Backend::OpenAi | Backend::Gemini)
    }

    /// The `data["engine"]` tag the Pi engine records for this backend.
    ///
    /// Note both [`Backend::Local`] and [`Backend::Ollama`] surface as
    /// `"local"` there: the registry resolves one of them into the
    /// engine's single `self.llm` slot, and the engine tags whatever it
    /// holds `"local"`. The cloud tiers keep distinct tags.
    pub fn engine_tag(self) -> &'static str {
        match self {
            Backend::Local | Backend::Ollama => "local",
            Backend::OpenAi => "openai",
            Backend::Gemini => "gemini",
        }
    }

    /// The canonical wire name — distinct per backend, matching the serde form
    /// and the `brain.request` schema's `backend` enum.
    pub fn as_str(self) -> &'static str {
        match self {
            Backend::Local => "local",
            Backend::Ollama => "ollama",
            Backend::OpenAi => "openai",
            Backend::Gemini => "gemini",
        }
    }
}

/// An ordered chat-escalation policy: try each backend in turn; the first
/// one the `available` predicate accepts answers the turn.
///
/// The default order composes the Pi's two routing seams —
/// `registry.select_chat_llm` ahead of the engine's cloud fallback — into
/// a single list.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RoutePlan {
    order: Vec<Backend>,
}

impl RoutePlan {
    /// A plan with an explicit escalation order.
    pub fn new(order: Vec<Backend>) -> Self {
        Self { order }
    }

    /// The canonical chat order: LAN Ollama → on-device Bielik → OpenAI →
    /// Gemini. Mirrors `registry.select_chat_llm` (Ollama preferred as the
    /// primary, else `LocalLlm`) followed by the engine's cloud fallback.
    pub fn default_chat() -> Self {
        Self::new(vec![
            Backend::Ollama,
            Backend::Local,
            Backend::OpenAi,
            Backend::Gemini,
        ])
    }

    /// The escalation order, primary first.
    pub fn order(&self) -> &[Backend] {
        &self.order
    }

    /// The first backend `available` accepts, or `None` when none is
    /// reachable — the latter maps to the engine's canned
    /// "needs a model/key" terminal reply.
    pub fn select(&self, available: impl Fn(Backend) -> bool) -> Option<Backend> {
        self.order.iter().copied().find(|&b| available(b))
    }

    /// Like [`select`](Self::select) but never routes off the local
    /// network: cloud backends are skipped even when reachable. Enforces
    /// the "no outbound cloud during normal operation" guarantee
    /// (`CLAUDE.md` §2) — cloud routing is opt-in via [`select`](Self::select).
    pub fn select_local_only(&self, available: impl Fn(Backend) -> bool) -> Option<Backend> {
        self.select(|b| !b.is_cloud() && available(b))
    }
}

impl Default for RoutePlan {
    fn default() -> Self {
        Self::default_chat()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn default_order_is_lan_device_then_cloud() {
        assert_eq!(
            RoutePlan::default_chat().order(),
            [
                Backend::Ollama,
                Backend::Local,
                Backend::OpenAi,
                Backend::Gemini
            ]
        );
    }

    #[test]
    fn select_prefers_the_lan_box_when_up() {
        let plan = RoutePlan::default_chat();
        // Everything reachable → the LAN Ollama box wins (dev GPU primary).
        assert_eq!(plan.select(|_| true), Some(Backend::Ollama));
    }

    #[test]
    fn select_falls_through_to_on_device_then_cloud() {
        let plan = RoutePlan::default_chat();
        // Ollama unset/unreachable → on-device Bielik.
        assert_eq!(plan.select(|b| b != Backend::Ollama), Some(Backend::Local));
        // No local path at all → OpenAI is the first cloud tier.
        assert_eq!(plan.select(Backend::is_cloud), Some(Backend::OpenAi));
        // Only Gemini configured.
        assert_eq!(plan.select(|b| b == Backend::Gemini), Some(Backend::Gemini));
        // Nothing reachable → the canned "needs a key" terminal.
        assert_eq!(plan.select(|_| false), None);
    }

    #[test]
    fn local_only_never_routes_to_cloud() {
        let plan = RoutePlan::default_chat();
        // Even with every cloud key set, a local-only turn stays on-network.
        assert_eq!(plan.select_local_only(Backend::is_cloud), None);
        assert_eq!(
            plan.select_local_only(|b| b == Backend::Local || b.is_cloud()),
            Some(Backend::Local)
        );
    }

    #[test]
    fn is_cloud_and_engine_tag() {
        assert!(!Backend::Local.is_cloud());
        assert!(!Backend::Ollama.is_cloud());
        assert!(Backend::OpenAi.is_cloud());
        assert!(Backend::Gemini.is_cloud());
        // Ollama and the on-device model share the "local" engine tag.
        assert_eq!(Backend::Ollama.engine_tag(), "local");
        assert_eq!(Backend::Local.engine_tag(), "local");
        assert_eq!(Backend::OpenAi.engine_tag(), "openai");
        assert_eq!(Backend::Gemini.engine_tag(), "gemini");
    }

    #[test]
    fn backend_serde_matches_engine_tags() {
        // The cloud tiers serialize exactly as their engine tags.
        assert_eq!(
            serde_json::to_string(&Backend::OpenAi).unwrap(),
            "\"openai\""
        );
        assert_eq!(
            serde_json::to_string(&Backend::Gemini).unwrap(),
            "\"gemini\""
        );
        let b: Backend = serde_json::from_str("\"ollama\"").unwrap();
        assert_eq!(b, Backend::Ollama);
    }

    #[test]
    fn plan_roundtrips() {
        let plan = RoutePlan::new(vec![Backend::Local, Backend::Gemini]);
        let s = serde_json::to_string(&plan).unwrap();
        let back: RoutePlan = serde_json::from_str(&s).unwrap();
        assert_eq!(plan, back);
    }
}

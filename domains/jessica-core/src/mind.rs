//! Portable conversation-mind turn logic.
//!
//! The **device-independent half of the conversation**: given a transcript and
//! the user's context, decide *what to ask the model* — assemble the system
//! prompt (persona + the user's name + relevant notes) and choose which backend
//! to route to. Running the model is ML glue (Python on the Pi, the OS engines
//! on mobile); the mind only produces the [`BrainRequest`]. This mirrors the
//! Pi's Python `Assistant` turn assembly (`engine.py`) and is shared with mobile
//! via `jessica-ffi`. See `docs/14-RUST-PYTHON-SPLIT.md` §1 (mind vs ML-glue)
//! and `docs/19-DOMAIN-ARCHITECTURE.md` Phase 4.

use crate::context::MemoryStore;
use crate::routing::{Backend, RoutePlan};

/// The default Jessica persona (Polish-first), ported verbatim from the Pi
/// engine's `PERSONA` so the spoken character is identical across platforms.
pub const DEFAULT_PERSONA: &str = "Jesteś Jessica — głosowa asystentka osobista \
dla osób niewidomych i słabowidzących, działająca na Raspberry Pi 5. Odpowiadaj \
w języku użytkownika (polski lub angielski); domyślnie po polsku. Mów krótko — \
jedno lub dwa zdania, chyba że poproszono o szczegóły. Bądź konkretna i uczciwa; \
jeśli czegoś nie wiesz, powiedz to wprost.";

/// A planned chat turn — everything the ML-glue inference server needs.
///
/// Maps 1:1 onto the `brain.request` IPC event; the Pi adapter
/// (`blazend-mind`) does that translation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BrainRequest {
    /// Correlates the eventual reply. Injected by the caller (the core has no
    /// clock or RNG).
    pub request_id: String,
    /// Language tag (`"pl"` / `"en"`).
    pub language: String,
    /// The user's transcript to answer.
    pub prompt: String,
    /// The assembled system / persona + context prompt.
    pub system: String,
    /// The chosen backend, or `None` when none is reachable (→ the glue's
    /// "needs a model/key" terminal reply).
    pub backend: Option<Backend>,
}

/// The portable conversation mind.
pub struct Mind {
    persona: String,
    notes_top_k: usize,
    notes_max_chars: usize,
}

impl Default for Mind {
    fn default() -> Self {
        // Defaults mirror engine.py (`notes_top_k=4`, `notes_max_chars=1200`).
        Self {
            persona: DEFAULT_PERSONA.to_string(),
            notes_top_k: 4,
            notes_max_chars: 1200,
        }
    }
}

impl Mind {
    /// A mind with the default Jessica persona.
    pub fn new() -> Self {
        Self::default()
    }

    /// A mind with a custom persona (everything else default).
    pub fn with_persona(persona: impl Into<String>) -> Self {
        Self {
            persona: persona.into(),
            ..Self::default()
        }
    }

    /// Assemble the system prompt for a turn: persona, then the user's name
    /// (profile key `"name"`, if set), then notes relevant to `query`.
    ///
    /// Mirrors `engine.py`'s name line + `_notes_context`. Note recall here is
    /// **lexical** (via [`MemoryStore::recall`]); the Pi swaps in semantic
    /// recall by pre-fetching hits from the embed service — see the module docs.
    pub fn system_prompt(&self, lang: &str, store: &impl MemoryStore, query: &str) -> String {
        let mut system = self.persona.clone();
        if let Some(name) = store.get_profile("name").filter(|n| !n.is_empty()) {
            system.push_str(&t(
                lang,
                format!(" Użytkownik ma na imię {name}."),
                format!(" The user's name is {name}."),
            ));
        }
        system.push_str(&self.notes_context(lang, store, query));
        system
    }

    fn notes_context(&self, lang: &str, store: &impl MemoryStore, query: &str) -> String {
        let hits = store.recall(Some(query));
        if hits.is_empty() {
            return String::new();
        }
        let header = t(
            lang,
            " Zapisane notatki użytkownika (wykorzystaj, jeśli pomocne):".to_string(),
            " The user's saved notes (use them if relevant):".to_string(),
        );
        let mut budget = self.notes_max_chars as isize;
        let mut parts = String::new();
        for n in hits.iter().take(self.notes_top_k) {
            let piece = if n.title.is_empty() {
                format!(" {}", n.text)
            } else {
                format!(" [{}] {}", n.title, n.text)
            };
            if piece.len() as isize > budget {
                break;
            }
            budget -= piece.len() as isize;
            parts.push_str(&piece);
        }
        if parts.is_empty() {
            String::new()
        } else {
            format!("{header}{parts}")
        }
    }

    /// Plan a chat turn: pick the first backend `available` accepts (in `plan`
    /// order), build the [`BrainRequest`] with the assembled system prompt.
    /// `request_id` is injected by the caller.
    pub fn plan_chat(
        &self,
        request_id: impl Into<String>,
        transcript: &str,
        lang: &str,
        store: &impl MemoryStore,
        plan: &RoutePlan,
        available: impl Fn(Backend) -> bool,
    ) -> BrainRequest {
        BrainRequest {
            request_id: request_id.into(),
            language: lang.to_string(),
            prompt: transcript.trim().to_string(),
            system: self.system_prompt(lang, store, transcript),
            backend: plan.select(available),
        }
    }
}

/// Pick the Polish or English variant by language tag (mirrors engine.py `_t`).
fn t(lang: &str, pl: String, en: String) -> String {
    if lang == "pl" {
        pl
    } else {
        en
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::context::InMemoryStore;

    #[test]
    fn system_prompt_is_persona_when_context_empty() {
        let mind = Mind::new();
        let store = InMemoryStore::new();
        let s = mind.system_prompt("pl", &store, "która godzina");
        assert_eq!(s, DEFAULT_PERSONA); // no name, no matching notes
    }

    #[test]
    fn system_prompt_injects_name_pl_and_en() {
        let mind = Mind::new();
        let mut store = InMemoryStore::new();
        store.set_profile("name", "Paweł", "2026-06-29T10:00:00");
        let pl = mind.system_prompt("pl", &store, "x");
        assert!(pl.contains("Użytkownik ma na imię Paweł."));
        let en = mind.system_prompt("en", &store, "x");
        assert!(en.contains("The user's name is Paweł."));
    }

    #[test]
    fn system_prompt_injects_matching_notes() {
        let mind = Mind::new();
        let mut store = InMemoryStore::new();
        store.add_note("kup mleko i chleb", "zakupy", "2026-06-29T10:00:00");
        // Lexical recall: the query contains the note text.
        let s = mind.system_prompt("pl", &store, "kup mleko i chleb");
        assert!(s.contains("Zapisane notatki użytkownika"));
        assert!(s.contains("[zakupy] kup mleko i chleb"));
        // An unrelated query injects nothing.
        let none = mind.system_prompt("pl", &store, "jaka jest pogoda");
        assert_eq!(none, DEFAULT_PERSONA);
    }

    #[test]
    fn plan_chat_builds_request_and_routes() {
        let mind = Mind::new();
        let store = InMemoryStore::new();
        let plan = RoutePlan::default_chat();
        let req = mind.plan_chat("req-1", "  cześć  ", "pl", &store, &plan, |_| true);
        assert_eq!(req.request_id, "req-1");
        assert_eq!(req.language, "pl");
        assert_eq!(req.prompt, "cześć"); // trimmed
        assert_eq!(req.system, DEFAULT_PERSONA);
        assert_eq!(req.backend, Some(Backend::Ollama)); // first available in default order
    }

    #[test]
    fn plan_chat_backend_none_when_nothing_available() {
        let mind = Mind::new();
        let store = InMemoryStore::new();
        let plan = RoutePlan::default_chat();
        let req = mind.plan_chat("r", "hi", "en", &store, &plan, |_| false);
        assert_eq!(req.backend, None);
    }
}

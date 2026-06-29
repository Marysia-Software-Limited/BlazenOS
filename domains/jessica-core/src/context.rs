//! Portable context-domain mind types.
//!
//! The device-independent memory model — notes, reminders, profile —
//! and the [`MemoryStore`] port the conversation engine depends on.
//! This is the Rust mirror of the Python `MemoryStorePort`
//! (`blazend.domains.context.core.ports`) and the data model in
//! `…/context/adapters/rpi5/memory.py`. Because the mind is
//! device-independent, every platform reasons over these same types;
//! they are what the future `blazend-fabric` `sync_fact` path
//! replicates to make many devices act as **one personality**.
//!
//! Timestamps are passed in as RFC 3339 strings (matching Python's
//! `datetime.isoformat()`) so the core carries no clock dependency —
//! the caller/adapter injects time, exactly as the Python store takes a
//! `now` argument. See `docs/19-DOMAIN-ARCHITECTURE.md` and
//! `docs/16-SYNC-PROTOCOL.md`.

use std::collections::HashMap;

use serde::{Deserialize, Serialize};

/// A remembered term/fact.
///
/// `title` is an optional short label for longer dictated notes; when
/// empty the note is a single untitled blob and `text` holds the whole
/// thing. Mirrors the Python `Note` dataclass field-for-field so the
/// JSON log stays interchangeable.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Note {
    /// Stable id (`note-N`).
    pub id: String,
    /// Note body.
    pub text: String,
    /// ISO-8601 creation timestamp.
    pub created: String,
    /// Optional short label (empty for untitled notes).
    #[serde(default)]
    pub title: String,
    /// Fabric fact tag.
    #[serde(default = "note_kind")]
    pub kind: String,
}

fn note_kind() -> String {
    "note_created".to_string()
}

/// How a [`Reminder`] should surface. Covers alarms and events too.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum ReminderCategory {
    /// A plain time-bound reminder.
    #[default]
    Reminder,
    /// An alarm.
    Alarm,
    /// A calendar-style event.
    Event,
}

/// A time-bound reminder (also covers alarms and events via `category`).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Reminder {
    /// Stable id (`rem-N`).
    pub id: String,
    /// Reminder body.
    pub text: String,
    /// ISO-8601 due timestamp.
    pub due: String,
    /// ISO-8601 creation timestamp.
    pub created: String,
    /// Whether the reminder has already fired.
    #[serde(default)]
    pub fired: bool,
    /// Reminder / alarm / event.
    #[serde(default)]
    pub category: ReminderCategory,
    /// Fabric fact tag.
    #[serde(default = "reminder_kind")]
    pub kind: String,
}

fn reminder_kind() -> String {
    "reminder_created".to_string()
}

/// The persistence surface the conversation engine depends on.
///
/// A narrow subset of the concrete store — structural so a richer
/// adapter (the rpi5 JSON store, a future cross-device fabric store)
/// can carry more (embeddings, voice notes) while satisfying this port.
/// Mirrors the Python `MemoryStorePort` Protocol.
pub trait MemoryStore {
    /// Record a note and return it.
    fn add_note(&mut self, text: &str, title: &str, now: &str) -> Note;
    /// All notes, oldest first.
    fn notes(&self) -> Vec<Note>;
    /// All notes, or those whose text/title contain `query` (case-insensitive).
    fn recall(&self, query: Option<&str>) -> Vec<Note>;
    /// Store a user fact (last write wins).
    fn set_profile(&mut self, key: &str, value: &str, now: &str);
    /// Read a stored user fact.
    fn get_profile(&self, key: &str) -> Option<String>;
    /// Record a reminder and return it.
    fn add_reminder(
        &mut self,
        text: &str,
        due: &str,
        now: &str,
        category: ReminderCategory,
    ) -> Reminder;
    /// Reminders that have not yet fired.
    fn pending(&self) -> Vec<Reminder>;
}

/// A profile fact: a value plus when it was set.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
struct ProfileEntry {
    value: String,
    set: String,
    #[serde(default = "profile_kind")]
    kind: String,
}

fn profile_kind() -> String {
    "profile_set".to_string()
}

/// Portable in-memory reference store.
///
/// The pure-Rust implementation of [`MemoryStore`] that mobile cores
/// reuse directly; the Pi's JSON-file store is a sibling adapter behind
/// the same port. Ids are sequential (`note-1`, `rem-1`, …) to match the
/// Python store's `_next_id`.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct InMemoryStore {
    notes: Vec<Note>,
    reminders: Vec<Reminder>,
    profile: HashMap<String, ProfileEntry>,
    seq: u64,
}

impl InMemoryStore {
    /// An empty store.
    pub fn new() -> Self {
        Self::default()
    }

    /// Load from a Python-compatible `memory.json`.
    ///
    /// The keys `notes` / `reminders` / `profile` / `seq` line up
    /// field-for-field with the Pi's `MemoryStore` JSON; extra keys (e.g.
    /// `voice_notes`) are ignored. A missing file yields an empty store; a
    /// malformed file is an error. This is the read side of the "one
    /// personality" memory — the same bytes the Python store persists.
    pub fn load_json(path: &std::path::Path) -> std::io::Result<Self> {
        match std::fs::read_to_string(path) {
            Ok(s) => serde_json::from_str(&s)
                .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Self::new()),
            Err(e) => Err(e),
        }
    }

    fn next_id(&mut self, prefix: &str) -> String {
        self.seq += 1;
        format!("{prefix}-{}", self.seq)
    }

    /// Return and mark fired every pending reminder due at or before `now`.
    ///
    /// Inherent (not part of the port) — mirrors the concrete store's
    /// `due`. Comparison is lexical on RFC 3339 strings, which is a
    /// correct chronological order for zero-padded same-offset stamps.
    pub fn due(&mut self, now: &str) -> Vec<Reminder> {
        let mut fired = Vec::new();
        for r in &mut self.reminders {
            if !r.fired && r.due.as_str() <= now {
                r.fired = true;
                fired.push(r.clone());
            }
        }
        fired
    }
}

impl MemoryStore for InMemoryStore {
    fn add_note(&mut self, text: &str, title: &str, now: &str) -> Note {
        let note = Note {
            id: self.next_id("note"),
            text: text.trim().to_string(),
            created: now.to_string(),
            title: title.trim().to_string(),
            kind: note_kind(),
        };
        self.notes.push(note.clone());
        note
    }

    fn notes(&self) -> Vec<Note> {
        self.notes.clone()
    }

    fn recall(&self, query: Option<&str>) -> Vec<Note> {
        let Some(q) = query.filter(|s| !s.is_empty()) else {
            return self.notes.clone();
        };
        let q = q.to_lowercase();
        self.notes
            .iter()
            .filter(|n| n.text.to_lowercase().contains(&q) || n.title.to_lowercase().contains(&q))
            .cloned()
            .collect()
    }

    fn set_profile(&mut self, key: &str, value: &str, now: &str) {
        self.profile.insert(
            key.to_string(),
            ProfileEntry {
                value: value.trim().to_string(),
                set: now.to_string(),
                kind: profile_kind(),
            },
        );
    }

    fn get_profile(&self, key: &str) -> Option<String> {
        self.profile.get(key).map(|e| e.value.clone())
    }

    fn add_reminder(
        &mut self,
        text: &str,
        due: &str,
        now: &str,
        category: ReminderCategory,
    ) -> Reminder {
        let rem = Reminder {
            id: self.next_id("rem"),
            text: text.trim().to_string(),
            due: due.to_string(),
            created: now.to_string(),
            fired: false,
            category,
            kind: reminder_kind(),
        };
        self.reminders.push(rem.clone());
        rem
    }

    fn pending(&self) -> Vec<Reminder> {
        self.reminders
            .iter()
            .filter(|r| !r.fired)
            .cloned()
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn add_and_list_notes() {
        let mut s = InMemoryStore::new();
        let n = s.add_note("  kup mleko  ", "zakupy", "2026-06-28T10:00:00");
        assert_eq!(n.id, "note-1");
        assert_eq!(n.text, "kup mleko"); // trimmed
        assert_eq!(n.title, "zakupy");
        assert_eq!(n.kind, "note_created");
        assert_eq!(s.notes().len(), 1);
    }

    #[test]
    fn recall_filters_case_insensitively_on_text_and_title() {
        let mut s = InMemoryStore::new();
        s.add_note("Kup Mleko", "", "2026-06-28T10:00:00");
        s.add_note("oddać książkę", "Biblioteka", "2026-06-28T10:01:00");
        assert_eq!(s.recall(None).len(), 2);
        assert_eq!(s.recall(Some("")).len(), 2); // empty query = all
        assert_eq!(s.recall(Some("mleko")).len(), 1);
        assert_eq!(s.recall(Some("biblioteka")).len(), 1); // matches on title
        assert!(s.recall(Some("nieobecne")).is_empty());
    }

    #[test]
    fn profile_last_write_wins() {
        let mut s = InMemoryStore::new();
        assert_eq!(s.get_profile("name"), None);
        s.set_profile("name", "  Beret ", "2026-06-28T10:00:00");
        assert_eq!(s.get_profile("name").as_deref(), Some("Beret")); // trimmed
        s.set_profile("name", "Paweł", "2026-06-28T11:00:00");
        assert_eq!(s.get_profile("name").as_deref(), Some("Paweł"));
    }

    #[test]
    fn reminders_pending_and_due() {
        let mut s = InMemoryStore::new();
        let r = s.add_reminder(
            "lekarstwo",
            "2026-06-28T12:00:00",
            "2026-06-28T08:00:00",
            ReminderCategory::Alarm,
        );
        assert_eq!(r.id, "rem-1");
        assert_eq!(r.category, ReminderCategory::Alarm);
        assert_eq!(s.pending().len(), 1);

        // Not yet due.
        assert!(s.due("2026-06-28T11:59:00").is_empty());
        assert_eq!(s.pending().len(), 1);

        // Due now — fires once, then no longer pending.
        let fired = s.due("2026-06-28T12:00:00");
        assert_eq!(fired.len(), 1);
        assert!(fired[0].fired);
        assert!(s.pending().is_empty());
        assert!(s.due("2026-06-28T13:00:00").is_empty()); // already fired
    }

    #[test]
    fn note_json_is_interchangeable_with_python() {
        // The Python store writes flat dataclass dicts; round-trip a
        // representative record to guard the wire shape.
        let json = r#"{"id":"note-3","text":"kup mleko","created":"2026-06-28T10:00:00","title":"","kind":"note_created"}"#;
        let n: Note = serde_json::from_str(json).unwrap();
        assert_eq!(n.id, "note-3");
        let back = serde_json::to_string(&n).unwrap();
        assert!(back.contains("\"kind\":\"note_created\""));
    }

    #[test]
    fn loads_python_memory_json_shape() {
        // A representative on-device memory.json, including a `voice_notes`
        // key the Rust store doesn't model (must be ignored, not rejected).
        let json = r#"{
          "notes": [
            {"id":"note-1","text":"kup mleko","created":"2026-06-28T10:00:00","title":"zakupy","kind":"note_created"}
          ],
          "reminders": [
            {"id":"rem-1","text":"lek","due":"2026-06-29T12:00:00","created":"2026-06-29T08:00:00","fired":false,"category":"alarm","kind":"reminder_created"}
          ],
          "voice_notes": [
            {"id":"vn-1","audio_path":"/x.wav","created":"2026-06-28T10:00:00","duration_s":1.0,"transcript":"","kind":"voice_note_created"}
          ],
          "profile": {"name": {"value":"Paweł","set":"2026-06-28T09:00:00","kind":"profile_set"}},
          "seq": 3
        }"#;
        let s: InMemoryStore = serde_json::from_str(json).unwrap();
        assert_eq!(s.notes().len(), 1);
        assert_eq!(s.notes()[0].title, "zakupy");
        assert_eq!(s.pending().len(), 1);
        assert_eq!(s.get_profile("name").as_deref(), Some("Paweł"));
        // seq carried, so a fresh add continues the sequence (note-4).
        let mut s = s;
        assert_eq!(s.add_note("x", "", "2026-06-29T10:00:00").id, "note-4");
    }

    #[test]
    fn load_json_missing_file_is_empty() {
        let p = std::path::Path::new("/nonexistent/blazen/memory.json");
        let s = InMemoryStore::load_json(p).unwrap();
        assert!(s.notes().is_empty());
    }

    #[test]
    fn reminder_category_defaults_when_absent() {
        // Older records may predate the explicit category tag.
        let json = r#"{"id":"rem-1","text":"x","due":"2026-06-28T12:00:00","created":"2026-06-28T08:00:00"}"#;
        let r: Reminder = serde_json::from_str(json).unwrap();
        assert_eq!(r.category, ReminderCategory::Reminder);
        assert!(!r.fired);
    }
}

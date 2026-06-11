//! Sync log — append-only CRDT-friendly fact store.
//!
//! M0 in-memory implementation. M2 will back this with sqlite via
//! `rusqlite` and add disk persistence + signature verification.

use std::collections::HashMap;

use crate::fact::{Fact, FactType};

/// Outcome of merging a remote fact.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SyncMergeOutcome {
    /// The fact was new and accepted into the log.
    Accepted,
    /// We already had this fact_id; remote payload was identical.
    DuplicateNoOp,
    /// We had the same fact_id with different payload; resolved by
    /// last-writer-wins (later `ts_ms` wins).
    ResolvedNewer,
    /// We had the same fact_id with different payload; remote was
    /// older so we kept the local one.
    ResolvedKeepLocal,
}

/// Append-only fact store with CRDT-friendly conflict resolution.
#[derive(Debug, Default)]
pub struct SyncLog {
    facts: HashMap<String, Fact>,
}

impl SyncLog {
    /// Empty log.
    pub fn new() -> Self {
        Self::default()
    }

    /// Number of unique facts.
    pub fn len(&self) -> usize {
        self.facts.len()
    }

    /// True iff no facts have been stored.
    pub fn is_empty(&self) -> bool {
        self.facts.is_empty()
    }

    /// Append a local fact. Returns the inserted fact's id.
    pub fn append(&mut self, fact: Fact) -> String {
        let id = fact.fact_id.clone();
        self.facts.insert(id.clone(), fact);
        id
    }

    /// Merge a fact arriving from a peer. Returns the merge outcome.
    pub fn merge(&mut self, incoming: Fact) -> SyncMergeOutcome {
        let id = incoming.fact_id.clone();
        match self.facts.get(&id) {
            None => {
                self.facts.insert(id, incoming);
                SyncMergeOutcome::Accepted
            }
            Some(existing) if existing.payload == incoming.payload => {
                SyncMergeOutcome::DuplicateNoOp
            }
            Some(existing) if incoming.ts_ms > existing.ts_ms => {
                self.facts.insert(id, incoming);
                SyncMergeOutcome::ResolvedNewer
            }
            Some(_) => SyncMergeOutcome::ResolvedKeepLocal,
        }
    }

    /// Iterate facts of a given type.
    pub fn facts_of_type(&self, kind: FactType) -> impl Iterator<Item = &Fact> {
        self.facts.values().filter(move |f| f.fact_type == kind)
    }

    /// Retrieve a fact by id.
    pub fn get(&self, fact_id: &str) -> Option<&Fact> {
        self.facts.get(fact_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::fact::{Fact, FactType};
    use serde_json::json;

    fn note_fact(id: &str, body: &str, ts_ms: u64, origin: &str) -> Fact {
        Fact {
            v: 1,
            fact_id: id.into(),
            fact_type: FactType::NoteCreated,
            ts_ms,
            origin: origin.into(),
            lamport: vec![],
            payload: json!({"note_id": id, "body": body, "lang": "pl"}),
            signature: String::new(),
        }
    }

    #[test]
    fn empty_log() {
        let l = SyncLog::new();
        assert!(l.is_empty());
        assert_eq!(l.len(), 0);
    }

    #[test]
    fn append_and_get() {
        let mut l = SyncLog::new();
        l.append(note_fact("n-1", "kup mleko", 1000, "pi"));
        assert_eq!(l.len(), 1);
        let f = l.get("n-1").unwrap();
        assert_eq!(f.origin, "pi");
    }

    #[test]
    fn merge_new_fact() {
        let mut l = SyncLog::new();
        let outcome = l.merge(note_fact("n-1", "kup mleko", 1000, "phone"));
        assert_eq!(outcome, SyncMergeOutcome::Accepted);
        assert_eq!(l.len(), 1);
    }

    #[test]
    fn merge_duplicate_no_op() {
        let mut l = SyncLog::new();
        l.append(note_fact("n-1", "kup mleko", 1000, "pi"));
        let outcome = l.merge(note_fact("n-1", "kup mleko", 1000, "pi"));
        assert_eq!(outcome, SyncMergeOutcome::DuplicateNoOp);
        assert_eq!(l.len(), 1);
    }

    #[test]
    fn merge_resolves_newer() {
        let mut l = SyncLog::new();
        l.append(note_fact("n-1", "kup mleko", 1000, "pi"));
        let outcome = l.merge(note_fact("n-1", "kup mleko i chleb", 2000, "phone"));
        assert_eq!(outcome, SyncMergeOutcome::ResolvedNewer);
        let f = l.get("n-1").unwrap();
        assert_eq!(f.payload["body"], "kup mleko i chleb");
    }

    #[test]
    fn merge_keeps_local_when_older() {
        let mut l = SyncLog::new();
        l.append(note_fact("n-1", "kup mleko nowe", 5000, "pi"));
        let outcome = l.merge(note_fact("n-1", "kup mleko stare", 1000, "phone"));
        assert_eq!(outcome, SyncMergeOutcome::ResolvedKeepLocal);
        let f = l.get("n-1").unwrap();
        assert_eq!(f.payload["body"], "kup mleko nowe");
    }

    #[test]
    fn facts_of_type_filter() {
        let mut l = SyncLog::new();
        l.append(note_fact("n-1", "a", 1000, "pi"));
        l.append(note_fact("n-2", "b", 2000, "pi"));
        let cnt = l.facts_of_type(FactType::NoteCreated).count();
        assert_eq!(cnt, 2);
        let no_reminders = l.facts_of_type(FactType::ReminderCreated).count();
        assert_eq!(no_reminders, 0);
    }
}

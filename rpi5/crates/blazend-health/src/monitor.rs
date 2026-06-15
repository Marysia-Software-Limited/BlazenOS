//! Recovery monitor — the watchdog's decision logic.
//!
//! Pure(ish) state machine: fed once per health tick with the set of units
//! that produced a heartbeat in the last interval (plus an optional critical
//! fault), it tracks consecutive misses per unit and returns a [`Verdict`]
//! (level + offending unit + recommended action). The live socket-watching
//! that produces the `seen` set is an M8 hook (needs the booted multi-unit
//! system); this logic is unit-tested in isolation here.
//!
//! Thresholds mirror `configs/system.yaml: voice_recovery_thresholds`.

use std::collections::{HashMap, HashSet};

/// Severity of the current health verdict.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Level {
    /// Everything alive.
    Ok,
    /// One non-essential unit went quiet — restart it, keep serving.
    Degraded,
    /// The voice path can't function (mic starved) — enter recovery mode.
    Recovery,
    /// Unrecoverable in place (e.g. corrupt model) — reboot into recovery image.
    Critical,
}

impl Level {
    /// Wire string (matches the `health.status` schema `level` enum).
    pub fn as_str(self) -> &'static str {
        match self {
            Level::Ok => "ok",
            Level::Degraded => "degraded",
            Level::Recovery => "recovery",
            Level::Critical => "critical",
        }
    }

    /// LED colour for this level (matches `blazend/led.py`).
    pub fn led(self) -> &'static str {
        match self {
            Level::Ok => "green",
            Level::Degraded => "yellow",
            Level::Recovery | Level::Critical => "red",
        }
    }
}

/// Recommended action (matches the `health.status` schema `action` enum).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Action {
    None,
    Restart,
    RecoveryMode,
    RecoveryImage,
}

impl Action {
    pub fn as_str(self) -> &'static str {
        match self {
            Action::None => "none",
            Action::Restart => "restart",
            Action::RecoveryMode => "recovery_mode",
            Action::RecoveryImage => "recovery_image",
        }
    }
}

/// One watchdog verdict.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    pub level: Level,
    /// Unit the verdict is about, or `"system"`.
    pub unit: String,
    pub action: Action,
}

/// Consecutive-miss thresholds (from `voice_recovery_thresholds`).
#[derive(Debug, Clone, Copy)]
pub struct Thresholds {
    pub audio_in_silent: u32,
    pub audio_out_silent: u32,
    pub brain_no_token: u32,
    pub recovery_cooldown_s: u64,
}

impl Default for Thresholds {
    fn default() -> Self {
        // Mirrors configs/system.yaml: voice_recovery_thresholds.
        Self { audio_in_silent: 3, audio_out_silent: 3, brain_no_token: 3, recovery_cooldown_s: 60 }
    }
}

/// Tracks per-unit liveness and derives the recovery level.
pub struct RecoveryMonitor {
    th: Thresholds,
    units: Vec<String>,
    misses: HashMap<String, u32>,
    /// When recovery was last entered (for the cooldown hold).
    recovery_since_s: Option<u64>,
}

impl RecoveryMonitor {
    /// `units` is the set of peers to watch (e.g. the seven `blazend-*` units).
    pub fn new(th: Thresholds, units: Vec<String>) -> Self {
        let misses = units.iter().map(|u| (u.clone(), 0)).collect();
        Self { th, units, misses, recovery_since_s: None }
    }

    fn threshold_for(&self, unit: &str) -> u32 {
        match unit {
            "blazend-audio-in" => self.th.audio_in_silent,
            "blazend-audio-out" => self.th.audio_out_silent,
            "blazend-brain" => self.th.brain_no_token,
            _ => self.th.audio_in_silent, // sensible default for the rest
        }
    }

    /// Advance one health tick.
    ///
    /// * `seen` — units that heartbeated since the last tick.
    /// * `critical` — `Some(unit)` if a critical fault is active (e.g. a
    ///   corrupt model file reported via an `error` event).
    /// * `now_s` — monotonic seconds, used for the recovery cooldown.
    pub fn tick(&mut self, seen: &HashSet<String>, critical: Option<&str>, now_s: u64) -> Verdict {
        for unit in &self.units {
            let m = self.misses.entry(unit.clone()).or_insert(0);
            if seen.contains(unit) {
                *m = 0;
            } else {
                *m = m.saturating_add(1);
            }
        }

        // 1. Critical wins outright — reboot into the recovery image.
        if let Some(unit) = critical {
            self.recovery_since_s = None;
            return Verdict { level: Level::Critical, unit: unit.to_string(), action: Action::RecoveryImage };
        }

        // 2. Mic starvation → recovery mode (the voice path is dead).
        let audio_in_dead = self.misses.get("blazend-audio-in").copied().unwrap_or(0)
            >= self.th.audio_in_silent;
        if audio_in_dead {
            self.recovery_since_s.get_or_insert(now_s);
            return Verdict { level: Level::Recovery, unit: "blazend-audio-in".into(), action: Action::RecoveryMode };
        }

        // 3. Hold recovery through the cooldown even if the mic came back, so we
        //    don't flap the LED/announcement on a brief blip.
        if let Some(since) = self.recovery_since_s {
            if now_s.saturating_sub(since) < self.th.recovery_cooldown_s {
                return Verdict { level: Level::Recovery, unit: "blazend-audio-in".into(), action: Action::RecoveryMode };
            }
            self.recovery_since_s = None;
        }

        // 4. Any other unit quiet past its threshold → degraded, restart it.
        for unit in &self.units {
            if self.misses.get(unit).copied().unwrap_or(0) >= self.threshold_for(unit) {
                return Verdict { level: Level::Degraded, unit: unit.clone(), action: Action::Restart };
            }
        }

        Verdict { level: Level::Ok, unit: "system".into(), action: Action::None }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn units() -> Vec<String> {
        ["blazend-audio-in", "blazend-brain", "blazend-tts", "blazend-audio-out"]
            .iter().map(|s| s.to_string()).collect()
    }

    fn all_seen() -> HashSet<String> {
        units().into_iter().collect()
    }

    fn seen_except(missing: &str) -> HashSet<String> {
        units().into_iter().filter(|u| u != missing).collect()
    }

    #[test]
    fn all_alive_is_ok() {
        let mut m = RecoveryMonitor::new(Thresholds::default(), units());
        let v = m.tick(&all_seen(), None, 0);
        assert_eq!(v.level, Level::Ok);
        assert_eq!(v.action, Action::None);
    }

    #[test]
    fn brain_silent_past_threshold_is_degraded_restart() {
        let mut m = RecoveryMonitor::new(Thresholds::default(), units());
        // 2 misses: still ok (threshold is 3).
        assert_eq!(m.tick(&seen_except("blazend-brain"), None, 0).level, Level::Ok);
        assert_eq!(m.tick(&seen_except("blazend-brain"), None, 5).level, Level::Ok);
        let v = m.tick(&seen_except("blazend-brain"), None, 10);
        assert_eq!(v.level, Level::Degraded);
        assert_eq!(v.unit, "blazend-brain");
        assert_eq!(v.action, Action::Restart);
    }

    #[test]
    fn brain_recovers_after_heartbeat() {
        let mut m = RecoveryMonitor::new(Thresholds::default(), units());
        for t in 0..3 { m.tick(&seen_except("blazend-brain"), None, t * 5); }
        assert_eq!(m.tick(&seen_except("blazend-brain"), None, 15).level, Level::Degraded);
        // A heartbeat resets the counter → back to ok.
        assert_eq!(m.tick(&all_seen(), None, 20).level, Level::Ok);
    }

    #[test]
    fn mic_starvation_is_recovery() {
        let mut m = RecoveryMonitor::new(Thresholds::default(), units());
        for t in 0..2 { m.tick(&seen_except("blazend-audio-in"), None, t * 5); }
        let v = m.tick(&seen_except("blazend-audio-in"), None, 10);
        assert_eq!(v.level, Level::Recovery);
        assert_eq!(v.unit, "blazend-audio-in");
        assert_eq!(v.action, Action::RecoveryMode);
    }

    #[test]
    fn recovery_holds_through_cooldown_then_clears() {
        let mut m = RecoveryMonitor::new(Thresholds::default(), units());
        for t in 0..3 { m.tick(&seen_except("blazend-audio-in"), None, t * 5); }
        assert_eq!(m.tick(&seen_except("blazend-audio-in"), None, 15).level, Level::Recovery);
        // Mic returns, but within the 60 s cooldown we still report recovery.
        assert_eq!(m.tick(&all_seen(), None, 30).level, Level::Recovery);
        // After the cooldown elapses with the mic healthy → ok.
        assert_eq!(m.tick(&all_seen(), None, 95).level, Level::Ok);
    }

    #[test]
    fn critical_fault_beats_everything() {
        let mut m = RecoveryMonitor::new(Thresholds::default(), units());
        let v = m.tick(&all_seen(), Some("llm.model"), 0);
        assert_eq!(v.level, Level::Critical);
        assert_eq!(v.action, Action::RecoveryImage);
        assert_eq!(v.level.led(), "red");
    }

    #[test]
    fn level_led_mapping() {
        assert_eq!(Level::Ok.led(), "green");
        assert_eq!(Level::Degraded.led(), "yellow");
        assert_eq!(Level::Recovery.led(), "red");
        assert_eq!(Level::Critical.led(), "red");
    }
}

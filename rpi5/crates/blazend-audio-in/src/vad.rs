//! Energy-based voice-activity detection over fixed-size mono i16 frames.
//!
//! Deliberately simple (RMS thresholds + hangover) — it runs in the capture
//! hot loop and only needs to bracket utterances so the ASR knows which slice
//! of the ring to transcribe. `Start` fires only after `min_speech` of energy
//! (so transient clicks don't open an utterance); `End` fires after
//! `hangover` of trailing silence and always pairs with a prior `Start`.
//!
//! **Self-calibrating:** rather than a single hand-tuned threshold (capture
//! gain varies per HAT/mixer/room), the VAD tracks the **ambient noise floor**
//! from quiet idle frames and scales the thresholds off it:
//! `effective_open = max(open_rms, noise_floor * open_mult)`. The absolute
//! `open_rms`/`close_rms` act as floors (a safety net so a near-silent room
//! doesn't make the VAD hair-trigger); the multipliers add headroom above
//! whatever ambient actually is. See `configs/audio.yaml` `input.vad:`.

/// EWMA weight for the ambient-noise tracker (per idle frame; ~1 s settle).
const NOISE_ALPHA: f32 = 0.05;

/// A VAD transition, mapped 1:1 onto the `vad.start` / `vad.end` IPC events.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VadEvent {
    /// Speech began.
    Start,
    /// Speech ended; `duration_ms` excludes the trailing hangover silence.
    End { duration_ms: u32 },
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum State {
    Idle,
    Speaking,
}

/// RMS-threshold VAD with an adaptive noise floor. Feed one frame at a time
/// with [`EnergyVad::push_frame`].
pub struct EnergyVad {
    open_rms: f32,
    close_rms: f32,
    open_mult: f32,
    close_mult: f32,
    hangover_frames: u32,
    min_speech_frames: u32,
    frame_ms: u32,
    state: State,
    run: u32,
    speech_frames: u32,
    noise_floor: f32,
}

impl EnergyVad {
    /// `*_rms` are absolute floor thresholds (linear i16 RMS); `*_mult` scale
    /// the learned ambient floor above them. `*_ms` are wall-clock windows
    /// quantised to whole `frame_ms` frames (min 1).
    pub fn new(
        open_rms: f32,
        close_rms: f32,
        open_mult: f32,
        close_mult: f32,
        hangover_ms: u32,
        min_speech_ms: u32,
        frame_ms: u32,
    ) -> Self {
        let frame_ms = frame_ms.max(1);
        Self {
            open_rms,
            close_rms,
            open_mult,
            close_mult,
            hangover_frames: (hangover_ms / frame_ms).max(1),
            min_speech_frames: (min_speech_ms / frame_ms).max(1),
            frame_ms,
            state: State::Idle,
            run: 0,
            speech_frames: 0,
            noise_floor: 0.0,
        }
    }

    /// Current learned ambient noise floor (linear i16 RMS) — for logging.
    pub fn noise_floor(&self) -> f32 {
        self.noise_floor
    }

    fn effective_open(&self) -> f32 {
        self.open_rms.max(self.noise_floor * self.open_mult)
    }

    fn effective_close(&self) -> f32 {
        self.close_rms.max(self.noise_floor * self.close_mult)
    }

    /// Feed one mono frame; returns a transition if the state changed.
    pub fn push_frame(&mut self, frame: &[i16]) -> Option<VadEvent> {
        let rms = rms_i16(frame);
        match self.state {
            State::Idle => {
                if rms > self.effective_open() {
                    self.run += 1;
                    if self.run >= self.min_speech_frames {
                        self.state = State::Speaking;
                        self.speech_frames = self.run;
                        self.run = 0;
                        return Some(VadEvent::Start);
                    }
                } else {
                    self.run = 0;
                    // Learn ambient only from quiet idle frames (never speech).
                    self.noise_floor += (rms - self.noise_floor) * NOISE_ALPHA;
                }
                None
            }
            State::Speaking => {
                self.speech_frames += 1;
                if rms < self.effective_close() {
                    self.run += 1;
                    if self.run >= self.hangover_frames {
                        let voiced = self.speech_frames.saturating_sub(self.hangover_frames);
                        let duration_ms = voiced.max(1) * self.frame_ms;
                        self.state = State::Idle;
                        self.run = 0;
                        self.speech_frames = 0;
                        return Some(VadEvent::End { duration_ms });
                    }
                } else {
                    self.run = 0;
                }
                None
            }
        }
    }
}

/// Linear RMS of a mono i16 frame.
pub fn rms_i16(frame: &[i16]) -> f32 {
    if frame.is_empty() {
        return 0.0;
    }
    let sum: f64 = frame.iter().map(|&s| (s as f64) * (s as f64)).sum();
    (sum / frame.len() as f64).sqrt() as f32
}

#[cfg(test)]
mod tests {
    use super::*;

    fn frame(amp: i16) -> Vec<i16> {
        vec![amp; 320] // 20 ms @ 16 kHz
    }

    // Defaults mirror configs/audio.yaml: floors 1800/1100, mults 2.5/1.6.
    fn vad() -> EnergyVad {
        EnergyVad::new(1800.0, 1100.0, 2.5, 1.6, 300, 120, 20)
    }

    #[test]
    fn silence_never_opens() {
        let mut v = vad();
        for _ in 0..50 {
            assert_eq!(v.push_frame(&frame(0)), None);
        }
    }

    #[test]
    fn transient_blip_does_not_open() {
        // One loud frame, then silence — below the 120 ms (6-frame) min.
        let mut v = vad();
        assert_eq!(v.push_frame(&frame(8000)), None);
        for _ in 0..5 {
            assert_eq!(v.push_frame(&frame(0)), None);
        }
    }

    #[test]
    fn speech_then_silence_brackets_an_utterance() {
        let mut v = vad();
        let min_speech_frames = 6; // 120 / 20
        let mut started = false;
        for _ in 0..min_speech_frames {
            if v.push_frame(&frame(6000)) == Some(VadEvent::Start) {
                started = true;
            }
        }
        assert!(started, "Start should fire once min_speech is reached");
        for _ in 0..10 {
            assert_eq!(v.push_frame(&frame(6000)), None);
        }
        let mut ended = None;
        for _ in 0..15 {
            if let Some(ev) = v.push_frame(&frame(0)) {
                ended = Some(ev);
            }
        }
        match ended {
            Some(VadEvent::End { duration_ms }) => assert!(duration_ms > 0),
            other => panic!("expected End, got {other:?}"),
        }
    }

    #[test]
    fn adapts_threshold_to_ambient_noise() {
        // Low absolute floor so the adaptive part dominates.
        let mut v = EnergyVad::new(1000.0, 600.0, 2.5, 1.6, 300, 120, 20);
        // Settle the noise floor to ~1000 of room noise (no Start — it's below
        // effective_open the whole time).
        for _ in 0..120 {
            assert_eq!(v.push_frame(&frame(1000)), None);
        }
        assert!(
            v.noise_floor() > 800.0,
            "floor learned ambient: {}",
            v.noise_floor()
        );
        // A 2000-RMS blip would clear the *fixed* 1000 floor, but not the
        // adapted threshold (~1000 * 2.5) — so it must NOT open.
        let mut opened = false;
        for _ in 0..10 {
            if v.push_frame(&frame(2000)) == Some(VadEvent::Start) {
                opened = true;
            }
        }
        assert!(!opened, "2000 RMS must not open against ~1000 ambient");
        // Genuine speech well above ambient does open.
        let mut started = false;
        for _ in 0..10 {
            if v.push_frame(&frame(5000)) == Some(VadEvent::Start) {
                started = true;
            }
        }
        assert!(started, "5000 RMS should open above ~1000 ambient");
    }
}

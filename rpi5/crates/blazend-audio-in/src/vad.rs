//! Energy-based voice-activity detection over fixed-size mono i16 frames.
//!
//! Deliberately simple (RMS thresholds + hangover) — it runs in the capture
//! hot loop and only needs to bracket utterances so the ASR knows which slice
//! of the ring to transcribe. `Start` fires only after `min_speech` of energy
//! (so transient clicks don't open an utterance); `End` fires after
//! `hangover` of trailing silence and always pairs with a prior `Start`.
//! Thresholds are config-driven (see `configs/audio.yaml` `vad:`), defaulting
//! to the levels measured on the ReSpeaker HAT (ambient RMS ~750).

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

/// RMS-threshold VAD. Feed one frame at a time with [`EnergyVad::push_frame`].
pub struct EnergyVad {
    open_rms: f32,
    close_rms: f32,
    hangover_frames: u32,
    min_speech_frames: u32,
    frame_ms: u32,
    state: State,
    run: u32,
    speech_frames: u32,
}

impl EnergyVad {
    /// `*_rms` are linear i16 RMS thresholds; `*_ms` are wall-clock windows
    /// quantised to whole `frame_ms` frames (min 1).
    pub fn new(
        open_rms: f32,
        close_rms: f32,
        hangover_ms: u32,
        min_speech_ms: u32,
        frame_ms: u32,
    ) -> Self {
        let frame_ms = frame_ms.max(1);
        Self {
            open_rms,
            close_rms,
            hangover_frames: (hangover_ms / frame_ms).max(1),
            min_speech_frames: (min_speech_ms / frame_ms).max(1),
            frame_ms,
            state: State::Idle,
            run: 0,
            speech_frames: 0,
        }
    }

    /// Feed one mono frame; returns a transition if the state changed.
    pub fn push_frame(&mut self, frame: &[i16]) -> Option<VadEvent> {
        let rms = rms_i16(frame);
        match self.state {
            State::Idle => {
                if rms >= self.open_rms {
                    self.run += 1;
                    if self.run >= self.min_speech_frames {
                        self.state = State::Speaking;
                        self.speech_frames = self.run;
                        self.run = 0;
                        return Some(VadEvent::Start);
                    }
                } else {
                    self.run = 0;
                }
                None
            }
            State::Speaking => {
                self.speech_frames += 1;
                if rms < self.close_rms {
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

    #[test]
    fn silence_never_opens() {
        let mut vad = EnergyVad::new(1800.0, 1100.0, 300, 120, 20);
        for _ in 0..50 {
            assert_eq!(vad.push_frame(&frame(0)), None);
        }
    }

    #[test]
    fn transient_blip_does_not_open() {
        // One loud frame, then silence — below the 120 ms (6-frame) min.
        let mut vad = EnergyVad::new(1800.0, 1100.0, 300, 120, 20);
        assert_eq!(vad.push_frame(&frame(8000)), None);
        for _ in 0..5 {
            assert_eq!(vad.push_frame(&frame(0)), None);
        }
    }

    #[test]
    fn speech_then_silence_brackets_an_utterance() {
        let mut vad = EnergyVad::new(1800.0, 1100.0, 300, 120, 20);
        let min_speech_frames = 6; // 120 / 20
        let mut started = false;
        for _ in 0..min_speech_frames {
            if vad.push_frame(&frame(6000)) == Some(VadEvent::Start) {
                started = true;
            }
        }
        assert!(started, "Start should fire once min_speech is reached");
        // keep talking
        for _ in 0..10 {
            assert_eq!(vad.push_frame(&frame(6000)), None);
        }
        // trailing silence: End after 300 ms (15 frames)
        let mut ended = None;
        for _ in 0..15 {
            if let Some(ev) = vad.push_frame(&frame(0)) {
                ended = Some(ev);
            }
        }
        match ended {
            Some(VadEvent::End { duration_ms }) => assert!(duration_ms > 0),
            other => panic!("expected End, got {other:?}"),
        }
    }
}

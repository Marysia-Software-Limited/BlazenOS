//! Output-side dynamics: an always-on loudness **leveler** (measures the player's
//! own output and slews a gain toward a target RMS, so a quiet audiobook rises to
//! the same loudness as radio), an optional speech **compressor** (intelligibility
//! for books/podcasts — never music), and a brick-wall **limiter**. Operates in
//! place on interleaved `i16` chunks, holding envelope + gain state across chunks.
//!
//! Ported verbatim from the Pi appliance's `blazend-player` so the Mac path holds
//! the identical loudness/intelligibility behaviour (domains for common code).

/// Decibels (full-scale) → linear amplitude ratio.
pub fn db_to_lin(db: f32) -> f32 {
    10f32.powf(db / 20.0)
}

pub(crate) const I16_SCALE: f32 = 32768.0;

fn apply_gain(samples: &mut [i16], gain: f32) {
    if (gain - 1.0).abs() < f32::EPSILON {
        return;
    }
    for s in samples.iter_mut() {
        *s = (*s as f32 * gain).clamp(i16::MIN as f32, i16::MAX as f32) as i16;
    }
}

/// Static config for the output dynamics chain, resolved from dB values once.
#[derive(Clone, Copy, Debug)]
pub struct DynamicsCfg {
    pub pre_gain: f32,   // manual gain, applied first
    pub level: bool,     // always-on loudness leveler (AGC)
    pub target_rms: f32, // linear target (from target_db)
    pub max_boost: f32,  // linear ceiling on AGC gain (from max_boost_db)
    pub max_cut: f32,    // linear floor on AGC gain
    pub compress: bool,  // speech compressor (books/podcasts)
    pub comp_threshold: f32,
    pub comp_ratio: f32,
    pub comp_makeup: f32,
    pub limit_ceil: f32, // linear brick-wall ceiling (from limit_db)
}

/// Public, dB-valued config that mirrors the `blazend-player` CLI flags.
#[derive(Clone, Copy, Debug)]
pub struct DynamicsConfig {
    pub pre_gain: f32,
    pub level: bool,
    pub target_db: f32,
    pub max_boost_db: f32,
    pub compress: bool,
    pub comp_threshold_db: f32,
    pub comp_ratio: f32,
    pub comp_makeup_db: f32,
    pub limit_db: f32,
}

impl DynamicsConfig {
    /// Defaults identical to the `blazend-player` CLI defaults.
    pub fn defaults() -> Self {
        DynamicsConfig {
            pre_gain: 1.0,
            level: true,
            target_db: -16.0,
            max_boost_db: 20.0,
            compress: false,
            comp_threshold_db: -24.0,
            comp_ratio: 3.0,
            comp_makeup_db: 3.0,
            limit_db: -1.0,
        }
    }

    /// No leveling/compression — plain pass-through (unit gain).
    pub fn off() -> Self {
        DynamicsConfig {
            level: false,
            compress: false,
            ..Self::defaults()
        }
    }

    pub(crate) fn resolve(&self) -> DynamicsCfg {
        DynamicsCfg {
            pre_gain: self.pre_gain,
            level: self.level,
            target_rms: db_to_lin(self.target_db),
            max_boost: db_to_lin(self.max_boost_db),
            max_cut: db_to_lin(-12.0), // never attenuate a hot source by more than 12 dB
            compress: self.compress,
            comp_threshold: db_to_lin(self.comp_threshold_db),
            comp_ratio: self.comp_ratio.max(1.0),
            comp_makeup: db_to_lin(self.comp_makeup_db),
            limit_ceil: db_to_lin(self.limit_db),
        }
    }
}

impl DynamicsCfg {
    /// Nothing to do but the manual gain → fall back to the plain `apply_gain`.
    fn is_noop(&self) -> bool {
        !self.level && !self.compress
    }
}

/// Stateful dynamics processor. See module docs.
pub struct Dynamics {
    cfg: DynamicsCfg,
    a_rms: f32,
    a_up: f32,
    a_down: f32,
    a_att: f32,
    a_rel: f32,
    gate: f32,
    rms_sq: f32,
    level_gain: f32,
    desired_gain: f32,
    refresh: u32,
    comp_env: f32,
    meter_peak: f32,
}

impl Dynamics {
    pub fn new(cfg: DynamicsCfg, rate: u32, channels: usize) -> Self {
        let fs = (rate.max(1) as f32) * (channels.max(1) as f32);
        let coef = |tau: f32| 1.0 - (-1.0 / (tau * fs)).exp();
        Dynamics {
            cfg,
            a_rms: coef(0.4),
            a_up: coef(1.0),
            a_down: coef(0.15),
            a_att: coef(0.005),
            a_rel: coef(0.120),
            gate: db_to_lin(-50.0),
            rms_sq: cfg.target_rms * cfg.target_rms,
            level_gain: 1.0,
            desired_gain: 1.0,
            refresh: 0,
            comp_env: 0.0,
            meter_peak: 0.0,
        }
    }

    /// Process one interleaved chunk in place: pre-gain → leveler → compressor → limiter.
    pub fn process(&mut self, samples: &mut [i16]) {
        if self.cfg.is_noop() {
            apply_gain(samples, self.cfg.pre_gain);
            for s in samples.iter() {
                let a = (*s as f32 / I16_SCALE).abs();
                self.meter_peak = self.meter_peak.max(a);
            }
            return;
        }
        for s in samples.iter_mut() {
            let mut x = (*s as f32 / I16_SCALE) * self.cfg.pre_gain;
            if self.cfg.level {
                self.rms_sq += self.a_rms * (x * x - self.rms_sq);
                if self.refresh == 0 {
                    let rms = self.rms_sq.sqrt();
                    self.desired_gain = if rms > self.gate {
                        (self.cfg.target_rms / rms).clamp(self.cfg.max_cut, self.cfg.max_boost)
                    } else {
                        self.level_gain
                    };
                    self.refresh = 64;
                }
                self.refresh -= 1;
                let a = if self.desired_gain < self.level_gain {
                    self.a_down
                } else {
                    self.a_up
                };
                self.level_gain += a * (self.desired_gain - self.level_gain);
                x *= self.level_gain;
            }
            if self.cfg.compress {
                let mag = x.abs();
                let a = if mag > self.comp_env {
                    self.a_att
                } else {
                    self.a_rel
                };
                self.comp_env += a * (mag - self.comp_env);
                if self.comp_env > self.cfg.comp_threshold {
                    let over = self.comp_env / self.cfg.comp_threshold;
                    x *= over.powf(1.0 / self.cfg.comp_ratio - 1.0);
                }
                x *= self.cfg.comp_makeup;
            }
            x = x.clamp(-self.cfg.limit_ceil, self.cfg.limit_ceil);
            self.meter_peak = self.meter_peak.max(x.abs());
            *s = (x * I16_SCALE).clamp(i16::MIN as f32, i16::MAX as f32) as i16;
        }
    }

    /// Output peak since the last call, in dBFS (drains the meter).
    pub fn meter_dbfs(&mut self) -> f32 {
        let p = self.meter_peak.max(1e-6);
        self.meter_peak = 0.0;
        20.0 * p.log10()
    }

    /// Current leveler gain in dB (0 dB = unity).
    pub fn gain_db(&self) -> f32 {
        20.0 * self.level_gain.max(1e-6).log10()
    }
}

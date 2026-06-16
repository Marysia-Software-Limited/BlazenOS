//! openWakeWord inference pipeline (3-stage ONNX via `ort`), replicating
//! `openwakeword.utils.AudioFeatures` exactly so it matches the Python-trained
//! `models/wake/jessica.onnx`:
//!
//! audio i16 → **melspectrogram.onnx** → squeeze to `(frames, 32)` → transform
//! `x/10 + 2` → sliding 76-frame windows (step 8) → **embedding_model.onnx** →
//! `(W, 96)` → each 16-embedding window → **jessica.onnx** → score. The unit
//! takes the max score over the windows in the rolling buffer.

use std::path::Path;

use anyhow::{anyhow, Result};
use ndarray::{s, Array2, Array4, Axis};
use ort::session::Session;
use ort::value::Tensor;

const MEL_WINDOW: usize = 76;
const MEL_STEP: usize = 8;
const MEL_BINS: usize = 32;
const EMB_DIM: usize = 96;
const CLF_WINDOW: usize = 16;

pub struct WakeModel {
    melspec: Session,
    embedding: Session,
    classifier: Session,
}

impl WakeModel {
    pub fn load(melspec: &Path, embedding: &Path, classifier: &Path) -> Result<Self> {
        Ok(Self {
            melspec: Session::builder()?.commit_from_file(melspec)?,
            embedding: Session::builder()?.commit_from_file(embedding)?,
            classifier: Session::builder()?.commit_from_file(classifier)?,
        })
    }

    /// `audio` (mono 16 kHz i16) → mel spectrogram `(frames, 32)`, with the
    /// openWakeWord `x/10 + 2` transform applied.
    fn melspectrogram(&mut self, audio: &[i16]) -> Result<Array2<f32>> {
        let samples: Vec<f32> = audio.iter().map(|&s| s as f32).collect();
        let input = Array2::from_shape_vec((1, samples.len()), samples)?;
        let out = self
            .melspec
            .run(ort::inputs!["input" => Tensor::from_array(input)?])?;
        let (_, data) = out[0].try_extract_tensor::<f32>()?;
        let frames = data.len() / MEL_BINS;
        let mut spec = Array2::from_shape_vec((frames, MEL_BINS), data.to_vec())?;
        spec.mapv_inplace(|v| v / 10.0 + 2.0);
        Ok(spec)
    }

    /// Mel `(frames, 32)` → embeddings `(W, 96)` via 76-frame windows (step 8).
    fn embeddings(&mut self, spec: &Array2<f32>) -> Result<Array2<f32>> {
        let frames = spec.shape()[0];
        if frames < MEL_WINDOW {
            return Ok(Array2::zeros((0, EMB_DIM)));
        }
        let mut flat: Vec<f32> = Vec::new();
        let mut count = 0;
        let mut i = 0;
        while i + MEL_WINDOW <= frames {
            for f in i..i + MEL_WINDOW {
                for c in 0..MEL_BINS {
                    flat.push(spec[[f, c]]);
                }
            }
            count += 1;
            i += MEL_STEP;
        }
        let batch = Array4::from_shape_vec((count, MEL_WINDOW, MEL_BINS, 1), flat)?;
        let out = self
            .embedding
            .run(ort::inputs!["input_1" => Tensor::from_array(batch)?])?;
        let (_, data) = out[0].try_extract_tensor::<f32>()?;
        Array2::from_shape_vec((count, EMB_DIM), data.to_vec()).map_err(|e| anyhow!(e))
    }

    /// Max wake score over the 16-embedding windows in `audio`.
    pub fn score(&mut self, audio: &[i16]) -> Result<f32> {
        let spec = self.melspectrogram(audio)?;
        let emb = self.embeddings(&spec)?;
        let w = emb.shape()[0];
        if w < CLF_WINDOW {
            return Ok(0.0);
        }
        let mut best = 0.0f32;
        for j in 0..=(w - CLF_WINDOW) {
            let window = emb
                .slice(s![j..j + CLF_WINDOW, ..])
                .to_owned()
                .insert_axis(Axis(0));
            let out = self
                .classifier
                .run(ort::inputs!["x" => Tensor::from_array(window)?])?;
            let (_, data) = out[0].try_extract_tensor::<f32>()?;
            best = best.max(data[0]);
        }
        Ok(best)
    }
}

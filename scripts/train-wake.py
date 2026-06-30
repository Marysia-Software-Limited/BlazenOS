#!/usr/bin/env python3
"""Train the "Jessica" wake-word model in openWakeWord format.

openWakeWord's runtime is a 3-stage ONNX pipeline: audio → melspectrogram →
speech embedding (96-dim) → a small classifier over a 16-frame embedding window.
The first two stages ship with the `openwakeword` package and are reused
verbatim by the Rust `blazend-wake` unit; this script trains only the final
**classifier head** (`models/wake/jessica.onnx`, input (1,16,96) → score).

The wake word is the **bare single word "dżesika"** (Polish phonetic for
"Jessica") — the form the user actually speaks (see docs/13-LANGUAGES.md and the
2026-06-30 decision). "Hej Dżesika" / "Hey Jessica" remain as positives for
robustness, but the bare word is the lead.

Training data is twofold:
  * **Synthetic** — Piper-synthesised "dżesika" (+ command tails) in the voices
    Jessica speaks with, with gain/noise/length augmentation.
  * **Real** — the operator's own recorded "dżesika" utterances captured on the
    appliance mic (default `~/wake-samples/dzesika/`, override --real-dir). These
    are the decisive signal: they teach the classifier the real speaker + mic +
    room. A held-out slice of them is reported separately as the real-world FRR.
Run with no real samples present and it falls back to synthetic-only (and says
so). Negatives are non-wake Polish/English phrases + rhyming names + silence.

Usage: .venv-train/bin/python scripts/train-wake.py [--epochs 250] [--real-dir DIR]
"""

from __future__ import annotations

import argparse
import glob
import subprocess
import wave
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
PIPER = REPO / ".venv-train/bin/piper"
VOICE_PL = REPO / "models/tts/pl_PL-gosia-medium.onnx"
VOICE_EN = REPO / "models/tts/en_US-lessac-medium.onnx"
OUT = REPO / "models/wake/jessica.onnx"
REAL_DIR = Path.home() / "wake-samples" / "dzesika"  # real "dżesika" recordings
SR = 16_000

# Wake WORD is the bare "dżesika" (Polish phonetic for Jessica) — the form the
# user speaks. "Hej Dżesika" / "Hey Jessica" are kept as extra positives for
# robustness, but the single word leads. Several spellings nudge Piper toward the
# real pronunciation /d͡ʐɛˈɕika/.
WAKE = [(VOICE_PL, ["Dżesika", "Dżesiko", "Dziesika", "Hej Dżesika",
                    "Jessica", "Jessico"]),
        (VOICE_EN, ["Jessica", "Hey Jessica", "Jessika"])]
# Commands appended after the wake word (so positives also learn "wake + speech",
# incl. the radio command the user tests with).
COMMANDS = ["", "", "włącz trójkę", "puść trójkę", "która godzina", "włącz muzykę"]
# Hard negatives: rhyming Polish names and other speech — all must NOT wake.
# (Bare "Jessica/Dżesika" is now a POSITIVE, so it is no longer listed here.)
NEGATIVE = [
    (VOICE_PL, ["Marysia", "Krysia", "Kasia", "Basia", "Misia", "Jagoda",
                "dzień dobry", "która godzina", "dziękuję bardzo",
                "jaka pogoda", "włącz muzykę", "telefon do mamy", "kalendarz na jutro",
                "przeczytaj wiadomości", "dobranoc", "proszę bardzo",
                "asystent głosowy", "włącz komputer", "jak się masz", "co słychać",
                "do widzenia", "włącz światło", "wyłącz światło", "ustaw alarm",
                "opowiedz mi coś", "ile to kosztuje", "gdzie jesteś", "hej Marysia",
                "hej Kasia", "hej mamo"]),
    (VOICE_EN, ["Melissa", "Jessie", "good morning", "what time is it",
                "thank you very much", "the weather today", "play some music",
                "call my mother", "read the news", "good night", "yes please",
                "assistant", "turn on the computer", "hello there", "how are you",
                "see you later", "turn on the light", "set an alarm",
                "tell me a story", "hey Melissa", "hey mom"]),
]
WIN, EMB_HOP = 16, 1280  # classifier window (frames), samples per embedding step


def piper_synth(voice: Path, text: str, length_scale: float) -> np.ndarray:
    raw = subprocess.run(
        [str(PIPER), "-m", str(voice), "--output-raw", "--length-scale", str(length_scale)],
        input=text.encode(), capture_output=True, check=True,
    ).stdout
    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    # Piper medium is 22050 Hz → resample to 16 kHz (linear).
    n = int(len(pcm) * SR / 22050)
    if n > 1:
        pcm = np.interp(np.linspace(0, len(pcm), n, endpoint=False),
                        np.arange(len(pcm)), pcm).astype(np.float32)
    return pcm


def place(pcm: np.ndarray, lead: int = 4800, total: int = 48_000) -> np.ndarray:
    """Place audio after a short lead-in in a 3 s buffer (so the wake word sits
    near the start, where the first streaming windows cover it)."""
    out = np.zeros(total, dtype=np.float32)
    end = min(total, lead + len(pcm))
    out[lead:end] = pcm[: end - lead]
    return out


def augment(clip: np.ndarray, gain: float, noise: float) -> np.ndarray:
    rng = np.random.RandomState(int(abs(gain * 1000 + noise * 1e6)) % 2**31)
    a = clip * gain + rng.normal(0, noise * 3000, size=clip.shape).astype(np.float32)
    return np.clip(a, -32768, 32767)


def all_windows(af, clip: np.ndarray) -> list[np.ndarray]:
    """Every sliding 16-frame (16,96) window of a clip — as streaming sees it."""
    emb = af._get_embeddings(clip.astype(np.int16), window_size=76, step_size=8)  # noqa: SLF001
    return [emb[i : i + WIN].astype(np.float32) for i in range(emb.shape[0] - WIN + 1)]


def load_wav(path: Path) -> np.ndarray:
    """Read a mono 16 kHz S16_LE wav as float32 PCM (resample if needed)."""
    with wave.open(str(path), "rb") as w:
        pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
        sr = w.getframerate()
    if sr != SR and len(pcm) > 1:
        n = int(len(pcm) * SR / sr)
        pcm = np.interp(np.linspace(0, len(pcm), n, endpoint=False),
                        np.arange(len(pcm)), pcm).astype(np.float32)
    return pcm


def real_windows(af, paths: list[Path]) -> list[list[np.ndarray]]:
    """Per-file positive windows from real recordings. Each utterance is placed at
    a few lead offsets (timing jitter) and gain/noise-augmented; the first windows
    — which cover the word — are kept. Returned grouped by file so a whole
    utterance can be held out for validation (no window leaks across the split)."""
    per_file = []
    for p in paths:
        pcm = load_wav(p)
        wins: list[np.ndarray] = []
        for lead in (3200, 4800, 6400):  # 200/300/400 ms — robustness to alignment
            base = place(pcm, lead=lead)
            for g, nz in ((1.0, 0.0), (0.85, 0.015), (1.2, 0.02)):
                wins.extend(all_windows(af, augment(base, g, nz))[:4])
        if wins:
            per_file.append(wins)
    return per_file


def build_dataset(af, real_dir: Path):
    pos, neg = [], []
    # Synthetic positives: "<wake> [command]" with the wake near the start → the
    # first few windows (which cover the wake + following speech) are positive.
    for voice, phrases in WAKE:
        for wake in phrases:
            for cmd in COMMANDS:
                text = wake if not cmd else f"{wake}, {cmd}"
                for ls in (0.9, 1.05):
                    base = place(piper_synth(voice, text, ls))
                    for g, nz in ((1.0, 0.0), (0.8, 0.02), (1.2, 0.03)):
                        wins = all_windows(af, augment(base, g, nz))
                        pos.extend(wins[:3])  # first 3 windows cover the wake
    # Negatives: every window of non-wake speech (incl. similar names).
    for voice, phrases in NEGATIVE:
        for text in phrases:
            for ls in (0.9, 1.1):
                base = place(piper_synth(voice, text, ls))
                for g, nz in ((1.0, 0.0), (1.1, 0.02)):
                    neg.extend(all_windows(af, augment(base, g, nz)))
    # Silence / noise negatives.
    rs = np.random.RandomState(0)
    for _ in range(30):
        neg.extend(all_windows(af, rs.normal(0, rs.uniform(30, 900), 48_000).astype(np.float32)))
    # Real positives — the operator's own "dżesika", grouped per utterance.
    real_paths = sorted(Path(p) for p in glob.glob(str(real_dir / "*.wav"))
                        if "_raw" not in Path(p).name)
    real = real_windows(af, real_paths) if real_paths else []
    return np.array(pos), np.array(neg), real


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=250)
    ap.add_argument("--real-dir", type=Path, default=REAL_DIR,
                    help="dir of real 'dżesika' wavs (positives); synthetic-only if absent")
    ap.add_argument("--real-oversample", type=int, default=4,
                    help="replicate real-positive windows N× so they aren't drowned by synth")
    args = ap.parse_args()

    import torch
    from openwakeword.utils import AudioFeatures

    torch.manual_seed(0)  # reproducible model (stable sha)
    np.random.seed(0)
    af = AudioFeatures()
    print("synthesising + embedding training clips (Piper)…", flush=True)
    pos, neg, real = build_dataset(af, args.real_dir)
    if real:
        # Split real utterances 80/20 BY FILE so no windows of a held-out
        # utterance leak into training — this is the honest real-world metric.
        rsr = np.random.RandomState(2)
        order = rsr.permutation(len(real))
        vcut = max(1, int(0.2 * len(real)))
        val_files = [real[i] for i in order[:vcut]]
        tr_files = [real[i] for i in order[vcut:]]
        real_tr = [w for f in tr_files for w in f]
        real_va = np.array([w for f in val_files for w in f], dtype=np.float32)
        print(f"  real 'dżesika': {len(real)} utterances "
              f"({len(tr_files)} train / {len(val_files)} val), "
              f"{len(real_tr)} train windows ×{args.real_oversample} oversample, "
              f"{len(real_va)} val windows", flush=True)
        real_tr = (real_tr * args.real_oversample) if real_tr else []
    else:
        print("  NO real samples found — synthetic-only (quality bounded; "
              f"expected dir: {args.real_dir})", flush=True)
        real_tr, real_va = [], np.empty((0, WIN, 96), dtype=np.float32)
    print(f"  synth positives={len(pos)}  negatives={len(neg)}  "
          f"real train positives={len(real_tr)}", flush=True)

    # Training set: synth positives + real-train positives + negatives.
    pos_all = np.concatenate([pos, np.array(real_tr, dtype=np.float32)]) if real_tr else pos
    X = np.concatenate([pos_all, neg]).astype(np.float32)  # (N,16,96)
    y = np.concatenate([np.ones(len(pos_all)), np.zeros(len(neg))]).astype(np.float32)
    npos = len(pos_all)
    # Hold out 20% for synthetic validation.
    rs = np.random.RandomState(1)
    idx = rs.permutation(len(X))
    X, y = X[idx], y[idx]
    cut = int(0.8 * len(X))
    Xtr, ytr, Xva, yva = X[:cut], y[:cut], X[cut:], y[cut:]

    class Net(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.net = torch.nn.Sequential(
                torch.nn.Flatten(),
                torch.nn.Linear(16 * 96, 64), torch.nn.ReLU(),
                torch.nn.Linear(64, 32), torch.nn.ReLU(),
                torch.nn.Linear(32, 1), torch.nn.Sigmoid(),
            )

        def forward(self, x):  # type: ignore[no-untyped-def]
            return self.net(x)

    net = Net()
    opt = torch.optim.Adam(net.parameters(), lr=1e-3, weight_decay=1e-4)
    xt, yt = torch.from_numpy(Xtr), torch.from_numpy(ytr).unsqueeze(1)
    # Class weight: positives rarer.
    w = torch.where(yt > 0.5, len(neg) / max(1, npos), 1.0)
    for _ep in range(args.epochs):
        opt.zero_grad()
        out = net(xt)
        loss = torch.nn.functional.binary_cross_entropy(out, yt, weight=w)
        loss.backward()
        opt.step()
    # Validate.
    with torch.no_grad():
        pv = net(torch.from_numpy(Xva)).numpy().ravel()
    pos_scores = pv[yva > 0.5]
    neg_scores = pv[yva < 0.5]
    thr = 0.5
    far = float((neg_scores > thr).mean()) if len(neg_scores) else 0.0
    frr = float((pos_scores < thr).mean()) if len(pos_scores) else 1.0
    print(f"VALIDATION (synth) @thr={thr}: pos_mean={pos_scores.mean():.2f} "
          f"neg_mean={neg_scores.mean():.2f} false_accept={far:.0%} "
          f"false_reject={frr:.0%}", flush=True)
    # Real-world validation — held-out utterances of the operator's own "dżesika".
    # This is the metric that matters: does the model fire on the real voice/mic?
    if len(real_va):
        with torch.no_grad():
            rv = net(torch.from_numpy(real_va)).numpy().ravel()
        # Per-utterance the runtime takes the MAX window score, so report both the
        # per-window rate and the realistic per-utterance fire rate.
        win_fire = float((rv > thr).mean())
        # Reconstruct utterance grouping for a per-utterance max.
        fired, off = 0, 0
        for f in val_files:
            seg = rv[off:off + len(f)]
            off += len(f)
            if len(seg) and seg.max() > thr:
                fired += 1
        print(f"VALIDATION (REAL dżesika) @thr={thr}: "
              f"window_mean={rv.mean():.2f} window_fire={win_fire:.0%} "
              f"utterance_fire={fired}/{len(val_files)}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # Stage the openWakeWord front-end ONNX next to the classifier so the Rust
    # wake unit (blazend-wake) has the full melspec→embedding→classifier chain.
    import shutil

    import openwakeword

    res = Path(openwakeword.__file__).parent / "resources" / "models"
    for fname in ("melspectrogram.onnx", "embedding_model.onnx"):
        shutil.copy(res / fname, OUT.parent / fname)

    net.eval()
    dummy = torch.zeros(1, 16, 96)
    torch.onnx.export(net, dummy, str(OUT), input_names=["x"], output_names=["score"],
                      opset_version=13, dynamo=False)
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

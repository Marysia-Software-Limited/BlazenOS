#!/usr/bin/env python3
"""Train the "Jessica" wake-word model in openWakeWord format.

openWakeWord's runtime is a 3-stage ONNX pipeline: audio → melspectrogram →
speech embedding (96-dim) → a small classifier over a 16-frame embedding window.
The first two stages ship with the `openwakeword` package and are reused
verbatim by the Rust `blazend-wake` unit; this script trains only the final
**classifier head** (`models/wake/jessica.onnx`, input (1,16,96) → score).

Training data is synthesised with Piper (the same voices Jessica speaks with):
positives are "Hej Jessico" / "Jessica" / …; negatives are other Polish/English
phrases (hard negatives, same voices) + silence. Synthetic-only, so quality is
bounded — validate before trusting it (see the report this prints).

Usage: .venv/bin/python scripts/train-wake.py [--epochs 200]
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
PIPER = REPO / ".venv/bin/piper"
VOICE_PL = REPO / "models/tts/pl_PL-gosia-medium.onnx"
VOICE_EN = REPO / "models/tts/en_US-lessac-medium.onnx"
OUT = REPO / "models/wake/jessica.onnx"
SR = 16_000

# Wake PHRASE is "Hej Jessico" / "Hey Jessica" (the distinctive two-word form —
# bare "Jessica" rhymes with Polish names and is intentionally NOT the wake).
WAKE = [(VOICE_PL, ["Hej Jessico", "Hej Jessika", "Hej Dżesika"]),
        (VOICE_EN, ["Hey Jessica", "Hey Jessika"])]
# Commands appended after the wake word (so positives learn "wake + speech").
COMMANDS = ["", "", "która godzina", "włącz muzykę", "jaka pogoda", "what time is it"]
# Hard negatives: bare "Jessica" (no hej), rhyming names, and other speech —
# all must NOT wake.
NEGATIVE = [
    (VOICE_PL, ["Jessica", "Jessico", "Jessika", "Marysia", "Krysia", "Kasia",
                "Basia", "Misia", "dzień dobry", "która godzina", "dziękuję bardzo",
                "jaka pogoda", "włącz muzykę", "telefon do mamy", "kalendarz na jutro",
                "przeczytaj wiadomości", "dobranoc", "proszę bardzo",
                "asystent głosowy", "włącz komputer", "jak się masz", "co słychać",
                "do widzenia", "włącz światło", "wyłącz światło", "ustaw alarm",
                "opowiedz mi coś", "ile to kosztuje", "gdzie jesteś", "hej Marysia",
                "hej Kasia", "hej mamo"]),
    (VOICE_EN, ["Jessica", "Jessika", "Melissa", "good morning", "what time is it",
                "thank you very much", "the weather today", "play some music",
                "call my mother", "read the news", "good night", "yes please",
                "assistant", "turn on the computer", "hello there", "how are you",
                "see you later", "turn on the light", "set an alarm",
                "Jessica's house", "tell me a story", "hey Melissa", "hey mom"]),
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


def build_dataset(af):
    pos, neg = [], []
    # Positives: "<wake> [command]" with the wake near the start → the first few
    # windows (which cover the wake + following speech) are positive.
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
    return np.array(pos), np.array(neg)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=250)
    args = ap.parse_args()

    import torch
    from openwakeword.utils import AudioFeatures

    torch.manual_seed(0)  # reproducible model (stable sha)
    np.random.seed(0)
    af = AudioFeatures()
    print("synthesising + embedding training clips (Piper)…", flush=True)
    pos, neg = build_dataset(af)
    print(f"  positives={len(pos)}  negatives={len(neg)}", flush=True)

    X = np.concatenate([pos, neg]).astype(np.float32)  # (N,16,96)
    y = np.concatenate([np.ones(len(pos)), np.zeros(len(neg))]).astype(np.float32)
    # Hold out 20% for validation.
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
    w = torch.where(yt > 0.5, len(neg) / max(1, len(pos)), 1.0)
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
    print(f"VALIDATION @thr={thr}: pos_mean={pos_scores.mean():.2f} neg_mean={neg_scores.mean():.2f} "
          f"false_accept={far:.0%} false_reject={frr:.0%}", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    net.eval()
    dummy = torch.zeros(1, 16, 96)
    torch.onnx.export(net, dummy, str(OUT), input_names=["x"], output_names=["score"],
                      opset_version=13, dynamo=False)
    print(f"wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

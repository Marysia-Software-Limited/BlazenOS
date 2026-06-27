"""Experimental few-shot wake-word features (openWakeWord ONNX).

Preserved reference for the WM8960 wake-word investigation — see
docs/findings/wake-word-wm8960.md. Not wired into the running stack: on the
WM8960 HAT mic the audio doesn't carry the wake word well enough to discriminate
it from background (every method scored ~0 separation). Revisit on a better mic.
"""

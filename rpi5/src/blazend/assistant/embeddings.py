"""On-device text embeddings — Jessica's semantic note recall.

Wraps an ONNX sentence-embedding model (default ``multilingual-e5-small``,
Polish + English) via ``onnxruntime`` + a ``tokenizers`` fast tokenizer. Used
by :class:`blazend.assistant.engine.Assistant` to retrieve the user's stored
notes that are relevant to a question and inject them into the LLM prompt.

Both heavy deps are imported lazily (only when a real backend is built), so
``make test-fast`` runs with neither installed and no model load — exactly like
:mod:`blazend.assistant.localllm`. Tests inject a fake via the ``EmbedderLike``
Protocol. If the model files or the runtime deps are absent the embedder reports
:attr:`available` ``False`` and the engine degrades to lexical note recall — the
CPU path is the contract, embeddings are a strict-improvement path.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from blazend.config import Config, load


class EmbedderError(RuntimeError):
    """Raised when an embedding cannot be produced."""


@runtime_checkable
class EmbedderLike(Protocol):
    """Seam the engine depends on, so tests inject a deterministic fake."""

    @property
    def available(self) -> bool: ...
    def embed(self, texts: list[str], *, kind: str = ...) -> list[list[float]]: ...


def models_root() -> Path:
    """On-disk model tree. Mirrors :func:`blazend.assistant.localllm.models_root`."""
    env = os.environ.get("BLAZEN_MODELS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[4] / "models"


def _deps_importable() -> bool:
    return (
        importlib.util.find_spec("onnxruntime") is not None
        and importlib.util.find_spec("tokenizers") is not None
    )


class _OnnxBackend:
    """Real backend: tokenizer + ONNX encoder, mean-pooled + L2-normalised."""

    def __init__(self, model_file: Path, tokenizer_file: Path, *, max_seq: int) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._tok = Tokenizer.from_file(str(tokenizer_file))
        self._tok.enable_truncation(max_length=max_seq)
        self._sess = ort.InferenceSession(
            str(model_file), providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self._sess.get_inputs()}

    def encode(self, texts: list[str]) -> list[list[float]]:
        encs = self._tok.encode_batch(texts)
        max_len = max((len(e.ids) for e in encs), default=1)
        ids = np.zeros((len(encs), max_len), dtype=np.int64)
        mask = np.zeros((len(encs), max_len), dtype=np.int64)
        for row, e in enumerate(encs):
            n = len(e.ids)
            ids[row, :n] = e.ids
            mask[row, :n] = e.attention_mask
        feeds: dict[str, Any] = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        feeds = {k: v for k, v in feeds.items() if k in self._input_names}
        last_hidden = self._sess.run(None, feeds)[0]  # (B, T, H)
        m = mask[:, :, None].astype(np.float32)
        summed = (last_hidden * m).sum(axis=1)
        counts = np.clip(m.sum(axis=1), 1e-9, None)
        pooled = summed / counts
        norms = np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-9, None)
        out = (pooled / norms).astype(np.float32).tolist()
        return [[float(x) for x in row] for row in out]


class Embedder:
    """On-device embedder. Pass ``backend`` in tests to skip the model load."""

    def __init__(
        self,
        *,
        backend: _OnnxBackend | None = None,
        config_name: str = "embeddings",
        config: Config | None = None,
    ) -> None:
        cfg = config or load(config_name)
        self.name = str(cfg.get("active_model", ""))
        models = cfg.get("models", {}) or {}
        entry = models.get(self.name, {}) if isinstance(models, dict) else {}
        self._max_seq = int(entry.get("max_seq", 256)) if isinstance(entry, dict) else 256
        prefixes = cfg.get("e5_prefixes", {}) or {}
        self._prefix = {
            "query": str(prefixes.get("query", "")),
            "passage": str(prefixes.get("passage", "")),
        }
        self._model_file, self._tokenizer_file = self._resolve_files(entry)
        self._backend = backend

    def _resolve_files(self, entry: Any) -> tuple[Path | None, Path | None]:
        if not isinstance(entry, dict) or not self.name:
            return None, None
        root = models_root() / "embeddings" / self.name
        model_file = tokenizer_file = None
        for f in entry.get("files", []) or []:
            fname = str(f.get("file", ""))
            if fname.endswith(".onnx"):
                model_file = root / fname
            elif "tokenizer" in fname:
                tokenizer_file = root / fname
        return model_file, tokenizer_file

    @property
    def available(self) -> bool:
        if self._backend is not None:
            return True
        if not self._model_file or not self._tokenizer_file:
            return False
        if not (self._model_file.exists() and self._tokenizer_file.exists()):
            return False
        return _deps_importable()

    def _ensure_backend(self) -> _OnnxBackend:
        if self._backend is None:
            if not (self._model_file and self._tokenizer_file):
                raise EmbedderError("no active embedding model configured")
            if not (self._model_file.exists() and self._tokenizer_file.exists()):
                raise EmbedderError(f"embedding model not found under {self._model_file}")
            self._backend = _OnnxBackend(
                self._model_file, self._tokenizer_file, max_seq=self._max_seq
            )
        return self._backend

    def embed(self, texts: list[str], *, kind: str = "passage") -> list[list[float]]:
        """Embed ``texts``. ``kind`` is ``"query"`` or ``"passage"`` (e5 asymmetric)."""
        if not texts:
            return []
        prefix = self._prefix.get(kind, "")
        prepared = [prefix + t for t in texts]
        try:
            return self._ensure_backend().encode(prepared)
        except EmbedderError:
            raise
        except Exception as e:  # onnxruntime / tokenizers runtime failure
            raise EmbedderError(f"embedding failed: {e}") from e

"""Local LLM adapter — Jessica's on-device brain (llama.cpp / GGUF; default Bielik).

The on-device counterpart to :class:`blazend.assistant.gemini.GeminiClient`:
same public surface (:attr:`available`, :meth:`chat`) so the engine can prefer
a local model for freeform conversation and keep Gemini only for web-grounded
lookups. No cloud — the CPU llama.cpp path is always-available per
``configs/llm.yaml`` and ``docs/12-ML-ACCELERATOR.md``.

``llama_cpp`` is imported lazily (only when a real backend is built) so unit
tests and ``make test-fast`` run with no binding and no 2 GB model load. The
backend is a :class:`LlmBackend` ``Protocol`` so tests inject a fake — exactly
like :mod:`blazend.asr.engine`.
"""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Protocol

from blazend.config import Config, load


class LlmError(RuntimeError):
    """Raised when a local LLM generation cannot be performed."""


class Llm(Protocol):
    """The chat surface the engine needs — satisfied by :class:`LocalLlm` and by
    :class:`blazend.assistant.ollama.OllamaLlm` (the dev LAN-GPU backend)."""

    @property
    def available(self) -> bool: ...

    def chat(self, user: str, *, system: str | None = None) -> str: ...

    def chat_stream(self, user: str, *, system: str | None = None) -> Iterator[str]: ...


def models_root() -> Path:
    """On-disk model tree. ``BLAZEN_MODELS_DIR`` overrides; else ``<repo>/models``.

    Mirrors ``scripts/install_models.py`` (``MODELS_DIR = REPO_ROOT/models``) so
    installed and loaded paths agree.
    """
    env = os.environ.get("BLAZEN_MODELS_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[4] / "models"


def resolve_model_path(cfg: Config) -> Path | None:
    """Resolve the active CPU GGUF from ``llm.yaml``, or ``None`` if unset.

    The model *name* contains dots (``qwen2.5-…``) so we index the ``models``
    mapping directly rather than via :meth:`Config.get`'s dotted walk.
    """
    active = cfg.get("active_model")
    if not active:
        return None
    active = str(active)
    models = cfg.get("models", {}) or {}
    entry = models.get(active, {}) if isinstance(models, dict) else {}
    cpu = entry.get("cpu", {}) if isinstance(entry, dict) else {}
    file = cpu.get("file", f"{active}.gguf") if isinstance(cpu, dict) else f"{active}.gguf"
    return models_root() / "llm" / active / str(file)


def _llama_importable() -> bool:
    return importlib.util.find_spec("llama_cpp") is not None


class LlmBackend(Protocol):
    """Minimal seam over llama.cpp so tests can inject a fake.

    ``generate_stream`` is optional — :meth:`LocalLlm.chat_stream` falls back to
    a single :meth:`generate` chunk for backends that don't implement it.
    """

    def generate(self, *, system: str, user: str) -> str: ...


class _LlamaCppBackend:
    """Real backend. Imports llama-cpp-python lazily (CPU, GGUF chat template)."""

    def __init__(
        self,
        model_path: Path,
        *,
        n_ctx: int,
        n_threads: int,
        n_batch: int,
        use_mmap: bool,
        use_mlock: bool,
        temperature: float,
        top_p: float,
        top_k: int,
        repeat_penalty: float,
        seed: int,
        max_tokens: int,
    ) -> None:
        from llama_cpp import Llama

        # Typed as Any: llama-cpp-python ships partial stubs; keep the untyped
        # surface from leaking into strict-mypy callers.
        self._llm: Any = Llama(
            model_path=str(model_path),
            n_ctx=n_ctx,
            n_threads=n_threads,
            n_batch=n_batch,
            use_mmap=use_mmap,
            use_mlock=use_mlock,
            n_gpu_layers=0,
            seed=seed,
            verbose=False,
        )
        self._temperature = temperature
        self._top_p = top_p
        self._top_k = top_k
        self._repeat_penalty = repeat_penalty
        self._seed = seed
        self._max_tokens = max_tokens

    def _messages(self, system: str, user: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    def generate(self, *, system: str, user: str) -> str:
        # create_chat_completion applies the model's own chat template from the
        # GGUF metadata (e.g. Bielik or Qwen2.5 ChatML) — no manual formatting.
        out = self._llm.create_chat_completion(
            messages=self._messages(system, user),
            temperature=self._temperature,
            top_p=self._top_p,
            top_k=self._top_k,
            repeat_penalty=self._repeat_penalty,
            seed=self._seed,
            max_tokens=self._max_tokens,
        )
        return str(out["choices"][0]["message"]["content"]).strip()

    def generate_stream(self, *, system: str, user: str) -> Iterator[str]:
        """Yield reply token text as llama.cpp produces it (``stream=True``)."""
        stream = self._llm.create_chat_completion(
            messages=self._messages(system, user),
            temperature=self._temperature,
            top_p=self._top_p,
            top_k=self._top_k,
            repeat_penalty=self._repeat_penalty,
            seed=self._seed,
            max_tokens=self._max_tokens,
            stream=True,
        )
        for chunk in stream:
            piece = chunk["choices"][0].get("delta", {}).get("content")
            if piece:
                yield str(piece)


class LocalLlm:
    """On-device conversational client. Pass ``backend`` in tests."""

    def __init__(
        self,
        *,
        backend: LlmBackend | None = None,
        config_name: str = "llm",
        system_prompt: str | None = None,
    ) -> None:
        cfg = load(config_name)
        self._cfg = cfg
        self._model_path = resolve_model_path(cfg)
        self.system_prompt = system_prompt or str(cfg.get("system_prompt", ""))
        self._backend = backend
        # Sampling / runtime, defaulting to the llm.yaml values.
        self._temperature = float(cfg.get("temperature", 0.4))
        self._top_p = float(cfg.get("top_p", 0.9))
        self._top_k = int(cfg.get("top_k", 40))
        self._repeat_penalty = float(cfg.get("repeat_penalty", 1.1))
        self._seed = int(cfg.get("seed", 0))
        self._max_tokens = int(cfg.get("max_tokens_per_reply", 256))
        self._n_ctx = int(cfg.get("n_ctx", 4096))
        self._n_threads = int(cfg.get("cpu.threads", os.cpu_count() or 4))
        self._n_batch = int(cfg.get("cpu.n_batch", 256))
        self._use_mmap = bool(cfg.get("cpu.use_mmap", True))
        self._use_mlock = bool(cfg.get("cpu.use_mlock", False))

    @property
    def available(self) -> bool:
        """True when a chat can actually be served (binding + model present)."""
        if self._backend is not None:
            return True
        if self._model_path is None or not self._model_path.exists():
            return False
        return _llama_importable()

    def _ensure_backend(self) -> LlmBackend:
        if self._backend is None:
            if self._model_path is None:
                raise LlmError("no active_model configured for the local LLM")
            if not self._model_path.exists():
                raise LlmError(f"local LLM model not found: {self._model_path}")
            self._backend = _LlamaCppBackend(
                self._model_path,
                n_ctx=self._n_ctx,
                n_threads=self._n_threads,
                n_batch=self._n_batch,
                use_mmap=self._use_mmap,
                use_mlock=self._use_mlock,
                temperature=self._temperature,
                top_p=self._top_p,
                top_k=self._top_k,
                repeat_penalty=self._repeat_penalty,
                seed=self._seed,
                max_tokens=self._max_tokens,
            )
        return self._backend

    def chat(self, user: str, *, system: str | None = None) -> str:
        """Freeform reply to ``user`` (the model replies in the user's language)."""
        return self._ensure_backend().generate(
            system=system or self.system_prompt, user=user
        )

    def chat_stream(self, user: str, *, system: str | None = None) -> Iterator[str]:
        """Stream the reply as token-text chunks (for sentence-sliced TTS).

        Falls back to a single chunk (the whole reply) for backends without a
        ``generate_stream`` — e.g. the deterministic fakes used in tests.
        """
        backend = self._ensure_backend()
        sys = system or self.system_prompt
        stream = getattr(backend, "generate_stream", None)
        if stream is None:
            yield backend.generate(system=sys, user=user)
            return
        yield from stream(system=sys, user=user)

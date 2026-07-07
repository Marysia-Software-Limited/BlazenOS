"""Task-based LLM routing — the brain's model manager.

Jessica has four brains at different cost/quality points; each request should go
to the cheapest capable one and fall back gracefully:

* ``bielik-1.5b`` — on-device, fast (~9.6 tok/s): quick command-like replies.
* ``bielik-4.5b`` — on-device, richer Polish: book/music recommendations and the
  metadata/ontology/semantic reasoning of the multi-layer RAG.
* ``ollama-11b`` — remote Bielik 11B on the LAN GPU (paul): preferred for BOTH of
  the above whenever it is reachable (zero local RAM, GPU-fast).
* ``gpt-5.5``   — OpenAI, only when a key is present: open questions / web
  research (deep weather, advanced science, newest news).

The order per task is data-driven from ``llm.yaml`` ``routing:``. ``route(task)``
yields the *available* backends for that task in order, so the caller keeps the
existing try / fall-to-next pattern. All backends satisfy the
:class:`blazend.domains.local_ai.adapters.rpi5.localllm.Llm` protocol
(``available`` / ``chat`` / ``chat_stream``).

RAM: on the 8 GB Pi only one large local Bielik fits alongside Whisper + Piper,
so ``single_local_model`` evicts the other local model before a different one is
used. The Ollama reachability probe (a 3 s network call) is cached for a short
TTL so routing costs nothing per turn when the box is up.
"""
from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Iterator
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from blazend.config import load
from blazend.domains.local_ai.adapters.rpi5.localllm import LocalLlm

if TYPE_CHECKING:
    from blazend.domains.local_ai.adapters.rpi5.localllm import Llm

log = logging.getLogger("blazend.domains.ai_orchestrator.model_router")

_LOCAL = ("bielik-1.5b", "bielik-4.5b")

# Fallback order if llm.yaml carries no routing block.
_DEFAULT_TASKS = {
    "command": ["ollama-11b", "bielik-1.5b"],
    "recommend": ["ollama-11b", "bielik-4.5b"],
    "open_qa": ["gpt-5.5", "ollama-11b", "bielik-4.5b"],
}


class Task(StrEnum):
    """What kind of thinking a request needs — selects the backend order."""

    COMMAND = "command"
    RECOMMEND = "recommend"
    OPEN_QA = "open_qa"


class _LocalSlot:
    """Wraps a local :class:`LocalLlm` so that using it first evicts the *other*
    local model (single-resident constraint on the 8 GB Pi). Transparent: it
    forwards the ``Llm`` protocol and reports the wrapped model's availability."""

    def __init__(self, name: str, llm: Llm, router: ModelRouter) -> None:
        self._name = name
        self._llm = llm
        self._router = router

    @property
    def available(self) -> bool:
        return bool(self._llm.available)

    def chat(self, user: str, *, system: str | None = None) -> str:
        self._router._activate_local(self._name)
        return self._llm.chat(user, system=system)

    def chat_stream(self, user: str, *, system: str | None = None) -> Iterator[str]:
        self._router._activate_local(self._name)
        yield from self._llm.chat_stream(user, system=system)


class ModelRouter:
    """Resolve the ordered list of available backends for a task."""

    def __init__(
        self,
        cfg: object | None = None,
        *,
        ollama: Llm | None = None,
        openai: Llm | None = None,
        local_factory: Callable[[str], Llm] | None = None,
        clock: Callable[[], float] = time.monotonic,
        mesh: Any = None,
    ) -> None:
        conf = cfg if cfg is not None else load("llm")
        routing = (conf.get("routing", {}) or {}) if hasattr(conf, "get") else {}
        self._tasks: dict[str, list[str]] = routing.get("tasks", {}) or _DEFAULT_TASKS
        self._backend_cfg: dict[str, dict[str, Any]] = routing.get("backends", {}) or {}
        self._single_local = bool(routing.get("single_local_model", True))
        self._probe_ttl = float(routing.get("ollama_probe_ttl_s", 30))
        self._clock = clock
        # The mesh registry supplies the *where* (a network backend's URL); llm.yaml
        # keeps the *policy* (task→backend order). Lazily loaded so tests/offline
        # construction don't require it. Injected backends (below) bypass resolution.
        self._mesh = mesh
        # Injected backends win (tests); otherwise build real ones lazily.
        self._built: dict[str, Llm | None] = {}
        if ollama is not None:
            self._built["ollama-11b"] = ollama
        if openai is not None:
            self._built["gpt-5.5"] = openai
        self._local_factory = local_factory or (
            lambda model: LocalLlm(model_name=model)
        )
        self._active_local: str | None = None
        self._ollama_cache: tuple[float, bool] = (-1e9, False)

    # -- backend construction (lazy, cached) -------------------------------
    def _model_name(self, name: str) -> str:
        entry = self._backend_cfg.get(name, {}) if isinstance(self._backend_cfg, dict) else {}
        return str(entry.get("model", "")) if isinstance(entry, dict) else ""

    def _mesh_url(self, category: str, name: str) -> str | None:
        """A network backend's endpoint from the mesh registry (lazy-loaded), or
        None to fall back to the env-configured default. Only the URL is taken —
        the served model tag stays the backend's own (OllamaLlm default). Auto-load
        uses only a DEPLOYED registry ($BLAZEN_MESH or /etc/blazen/mesh.yaml), never
        the dev repo copy — so unit tests stay hermetic. An injected mesh always
        wins (tests / the linux surface pass one explicitly)."""
        if self._mesh is None:
            if not (os.environ.get("BLAZEN_MESH") or os.path.exists("/etc/blazen/mesh.yaml")):
                return None
            try:
                from mesh_registry import Mesh  # noqa: PLC0415

                self._mesh = Mesh.load()
            except Exception:  # noqa: BLE001 — no mesh → env fallback
                return None
        try:
            res = self._mesh.resource(category, name)
        except Exception:  # noqa: BLE001
            return None
        return res.url if (res and res.url) else None

    def _build(self, name: str) -> Llm | None:
        if name in self._built:
            return self._built[name]
        try:
            if name == "ollama-11b":
                # Lazy imports break an import cycle (assistant/__init__ → engine
                # → model_router) — these adapters live under the assistant pkg.
                from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.ollama import (  # noqa: PLC0415
                    OllamaLlm,
                )

                # URL from the mesh registry (the "where"); env is the fallback.
                url = self._mesh_url("llm", name)
                inst: Llm = OllamaLlm(url=url) if url else OllamaLlm()
            elif name == "gpt-5.5":
                from blazend.domains.ai_orchestrator.adapters.rpi5.assistant.openai import (  # noqa: PLC0415
                    OpenAiClient,
                )

                inst = OpenAiClient()
            elif name in _LOCAL:
                inst = self._local_factory(self._model_name(name))
            else:
                log.warning("unknown backend %r in routing config", name)
                inst = None  # type: ignore[assignment]
        except Exception as exc:  # noqa: BLE001 — a build failure just drops this backend
            log.warning("backend %s unavailable to build: %s", name, exc)
            inst = None  # type: ignore[assignment]
        self._built[name] = inst
        return inst

    # -- availability ------------------------------------------------------
    def _available(self, name: str, inst: Llm) -> bool:
        if name == "ollama-11b":
            checked_at, ok = self._ollama_cache
            now = self._clock()
            if now - checked_at < self._probe_ttl:
                return ok
            ok = bool(inst.available)  # 3 s network probe
            self._ollama_cache = (now, ok)
            return ok
        return bool(inst.available)

    # -- single-resident local eviction ------------------------------------
    def _activate_local(self, name: str) -> None:
        if not self._single_local or self._active_local == name:
            self._active_local = name
            return
        prev = self._active_local
        if prev is not None and prev in self._built:
            evict = getattr(self._built[prev], "close", None)
            if callable(evict):
                log.info("evicting local model %s to load %s (single-resident)", prev, name)
                try:
                    evict()
                except Exception:  # noqa: BLE001
                    pass
        self._active_local = name

    # -- public ------------------------------------------------------------
    def route(self, task: Task | str) -> Iterator[tuple[str, Llm]]:
        """Yield ``(name, backend)`` for the available backends of ``task`` in
        configured order, so the caller can try each and log which answered."""
        key = task.value if isinstance(task, Task) else str(task)
        for name in self._tasks.get(key, []):
            inst = self._build(name)
            if inst is None or not self._available(name, inst):
                continue
            yield name, (_LocalSlot(name, inst, self) if name in _LOCAL else inst)

    def first(self, task: Task | str) -> tuple[str, Llm] | None:
        """The first available (name, backend) for ``task``, or ``None``."""
        key = task.value if isinstance(task, Task) else str(task)
        for name in self._tasks.get(key, []):
            inst = self._build(name)
            if inst is not None and self._available(name, inst):
                return name, (_LocalSlot(name, inst, self) if name in _LOCAL else inst)
        return None


__all__ = ["ModelRouter", "Task"]

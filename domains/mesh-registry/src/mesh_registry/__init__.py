"""Shared reader for the Jessica constellation registry (``configs/mesh.yaml``).

Every node loads the same registry to discover peers and the resources each
advertises (LLM / ASR / TTS endpoints). Device-independent, so it lives under
``domains/`` and is imported by the Pi agent and the rachel macOS agent alike.
"""

from mesh_registry.registry import Mesh, Resource

__all__ = ["Mesh", "Resource"]

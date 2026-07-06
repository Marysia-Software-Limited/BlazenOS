"""Shared audiobook catalog domain lib — model, resolver, progress store.

Device-independent audiobook logic used by every node (the ``jessica`` Pi
appliance and the ``rachel`` macOS agent). Lives under ``domains/`` so common
code has one source of truth instead of being copied into each platform adapter.
"""

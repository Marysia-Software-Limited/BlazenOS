"""Re-export shim — implementation moved to the shared domains lib.

The audiobook directory/resolver now lives in ``domains/audiobook-catalog``
(``audiobook_catalog.directory``) so every node — the ``jessica`` Pi appliance
and the ``rachel`` macOS agent — shares one implementation (domains for common
code). This shim keeps the existing import path working unchanged.
"""
from __future__ import annotations

from audiobook_catalog.directory import AudiobookDirectory, Book

__all__ = ["AudiobookDirectory", "Book"]

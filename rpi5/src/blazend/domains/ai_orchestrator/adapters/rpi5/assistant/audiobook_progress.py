"""Re-export shim — implementation moved to the shared domains lib.

Per-book listening progress now lives in ``domains/audiobook-catalog``
(``audiobook_catalog.progress``) so every node shares one implementation
(domains for common code). This shim keeps the existing import path working.
"""
from __future__ import annotations

from audiobook_catalog.progress import AudiobookProgress

__all__ = ["AudiobookProgress"]

"""Serialize Content repository mutations across package operations."""

from __future__ import annotations

import threading
from contextlib import contextmanager
from collections.abc import Iterator


class RepositoryBusyError(RuntimeError):
    """Another request is changing the Content repository."""


_MUTATION_LOCK = threading.Lock()


@contextmanager
def mutation() -> Iterator[None]:
    """Hold the process-wide Content mutation lock without waiting."""
    if not _MUTATION_LOCK.acquire(blocking=False):
        raise RepositoryBusyError(
            "repository is busy; try again after the current operation finishes"
        )
    try:
        yield
    finally:
        _MUTATION_LOCK.release()

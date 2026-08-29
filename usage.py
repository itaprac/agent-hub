"""Compatibility alias for :mod:`agenthub.usage`."""

from __future__ import annotations

import sys

from agenthub import usage as _implementation

sys.modules[__name__] = _implementation

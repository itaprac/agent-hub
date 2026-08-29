#!/usr/bin/env python3
"""Compatibility entry point for :mod:`agenthub.webapp`."""

from __future__ import annotations

import sys

from agenthub import webapp as _implementation
from agenthub.webapp import *  # noqa: F403

if __name__ == "__main__":
    raise SystemExit(_implementation.main())

sys.modules[__name__] = _implementation

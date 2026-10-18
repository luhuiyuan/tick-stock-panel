"""Small runtime compatibility helpers for the vendored OpenTDX source."""
from __future__ import annotations

try:
    from opentdx.compat import override
except ImportError:  # Python 3.11
    def override(function):
        return function

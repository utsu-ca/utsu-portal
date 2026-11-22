"""Compatibility alias for Senate preferences."""
import sys

from senate import preferences

sys.modules[__name__] = preferences

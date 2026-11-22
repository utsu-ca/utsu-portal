"""Compatibility alias for Senate seating."""
import sys

from senate import seating

sys.modules[__name__] = seating

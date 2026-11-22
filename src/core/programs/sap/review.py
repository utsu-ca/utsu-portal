"""Compatibility alias for SABP review."""
import sys

from sabp import review

sys.modules[__name__] = review

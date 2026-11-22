"""Compatibility alias for SABP terms."""
import sys

from sabp import terms

sys.modules[__name__] = terms

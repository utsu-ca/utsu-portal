"""Compatibility alias for SABP banking."""
import sys

from sabp import banking

sys.modules[__name__] = banking

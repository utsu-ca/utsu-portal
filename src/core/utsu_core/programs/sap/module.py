"""Compatibility alias for the SABP portal module."""
import sys

from sabp import module

sys.modules[__name__] = module

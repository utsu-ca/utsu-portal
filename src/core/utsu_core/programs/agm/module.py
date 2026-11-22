"""Compatibility alias for the AGM portal module."""
import sys

from agm import module

sys.modules[__name__] = module

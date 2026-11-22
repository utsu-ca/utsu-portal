"""Compatibility alias for the Senate portal module."""
import sys

from senate import module

sys.modules[__name__] = module

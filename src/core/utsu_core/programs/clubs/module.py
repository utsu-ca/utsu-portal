"""Compatibility alias for Club workflows."""
import sys

from clubs import module

sys.modules[__name__] = module

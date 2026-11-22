"""Compatibility alias for SABP applications."""
import sys

from sabp import applications

sys.modules[__name__] = applications

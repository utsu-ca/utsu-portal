"""Compatibility alias for SABP weighting."""
import sys

from sabp import wfni

sys.modules[__name__] = wfni

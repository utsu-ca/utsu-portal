"""Compatibility alias for SABP EFT generation."""
import sys

from sabp import eft

sys.modules[__name__] = eft

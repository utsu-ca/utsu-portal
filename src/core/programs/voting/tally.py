"""Compatibility alias for vote tallying."""
import sys

from voting import tally

sys.modules[__name__] = tally

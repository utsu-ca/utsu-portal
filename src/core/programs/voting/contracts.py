"""Compatibility alias for voting contracts."""
import sys

from voting import contracts

sys.modules[__name__] = contracts

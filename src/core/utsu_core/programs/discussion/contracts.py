"""Compatibility alias for discussion contracts."""
import sys

from chat import contracts

sys.modules[__name__] = contracts

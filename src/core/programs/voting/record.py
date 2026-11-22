"""Compatibility alias for voting records."""
import sys

from voting import record

sys.modules[__name__] = record

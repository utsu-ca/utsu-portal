"""Compatibility alias for SABP banking exports."""
import sys

from sabp import export_banking

sys.modules[__name__] = export_banking

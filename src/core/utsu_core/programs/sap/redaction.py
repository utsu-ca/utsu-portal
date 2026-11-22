"""Compatibility alias for SABP redaction."""
import sys

from sabp import redaction

sys.modules[__name__] = redaction

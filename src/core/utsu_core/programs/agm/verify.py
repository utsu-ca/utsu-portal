"""Compatibility alias for AGM verification."""
import sys

from agm import verify

sys.modules[__name__] = verify

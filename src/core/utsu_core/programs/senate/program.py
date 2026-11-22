"""Compatibility alias for the Senate batch program."""
import sys

from senate import program

sys.modules[__name__] = program

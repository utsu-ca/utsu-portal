"""Compatibility alias for the AGM batch program."""
import sys

from agm import program

sys.modules[__name__] = program

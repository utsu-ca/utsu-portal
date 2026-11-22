"""Compatibility alias for the voting plug-in."""
import sys

from voting import plugin

sys.modules[__name__] = plugin

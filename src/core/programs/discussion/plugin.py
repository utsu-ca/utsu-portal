"""Compatibility alias for the discussion plug-in."""
import sys

from chat import plugin

sys.modules[__name__] = plugin

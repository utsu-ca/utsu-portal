"""Compatibility alias for AGM models."""
import sys

from agm import models

sys.modules[__name__] = models

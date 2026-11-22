"""Compatibility alias for SABP document merging."""
import sys

from sabp import merge_docx

sys.modules[__name__] = merge_docx

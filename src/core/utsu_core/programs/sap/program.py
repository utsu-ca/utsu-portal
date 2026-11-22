"""Compatibility alias for the SABP batch program."""
import sys

from sabp import program

if __name__ == "__main__":
    import doctest

    doctest.testmod(program)
else:
    sys.modules[__name__] = program

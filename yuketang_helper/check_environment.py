"""Exit codes for the Windows launcher. No third party imports are required."""
import importlib.util
from importlib.metadata import version
import sys

if sys.version_info < (3, 10):
    sys.exit(2)
try:
    import tkinter
except ImportError:
    sys.exit(2)
if not importlib.util.find_spec("playwright") or version("playwright") != "1.60.0":
    sys.exit(1)
if not importlib.util.find_spec("PIL"):
    sys.exit(1)

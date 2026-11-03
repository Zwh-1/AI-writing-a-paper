"""Compatibility import; business ownership moved to research_api."""
import sys
from . import research_api as _implementation
sys.modules[__name__] = _implementation

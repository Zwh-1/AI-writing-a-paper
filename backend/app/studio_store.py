"""Compatibility import; business ownership moved to research_records."""
import sys
from . import research_records as _implementation
sys.modules[__name__] = _implementation

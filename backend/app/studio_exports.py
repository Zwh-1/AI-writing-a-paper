"""Compatibility import; business ownership moved to research_delivery."""
import sys
from . import research_delivery as _implementation
sys.modules[__name__] = _implementation

"""Compatibility import; business ownership moved to parsing_service."""
import sys
from . import parsing_service as _implementation
sys.modules[__name__] = _implementation

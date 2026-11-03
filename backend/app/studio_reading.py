"""Compatibility import; business ownership moved to reading_service."""
import sys
from . import reading_service as _implementation
sys.modules[__name__] = _implementation

"""Compatibility import; business ownership moved to assistant_service."""
import sys
from . import assistant_service as _implementation
sys.modules[__name__] = _implementation

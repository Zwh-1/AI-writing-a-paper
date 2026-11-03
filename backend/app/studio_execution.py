"""Compatibility import; business ownership moved to execution_service."""
import sys
from . import execution_service as _implementation
sys.modules[__name__] = _implementation

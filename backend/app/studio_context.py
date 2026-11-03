"""Compatibility import; business ownership moved to assistant_context."""
import sys
from . import assistant_context as _implementation
sys.modules[__name__] = _implementation

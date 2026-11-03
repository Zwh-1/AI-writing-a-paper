"""Compatibility import; business ownership moved to knowledge_metadata."""
import sys
from . import knowledge_metadata as _implementation
sys.modules[__name__] = _implementation

"""Compatibility import; business ownership moved to literature_connectors."""
import sys
from . import literature_connectors as _implementation
sys.modules[__name__] = _implementation

"""Shared, versioned academic prose guidance; never a detector or fact source."""
from .config import ROOT


VERSION = 'academic-expression.v1'


def guidance():
    return (ROOT / 'prompts' / 'academic-expression.v1.txt').read_text(encoding='utf-8').strip()


def review_guidance():
    return (ROOT / 'prompts' / 'academic-expression-review.v1.txt').read_text(encoding='utf-8').strip()

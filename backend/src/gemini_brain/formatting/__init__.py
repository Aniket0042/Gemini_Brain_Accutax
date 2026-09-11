"""formatting — markdown normalization, deterministic zero-row generators, and table renderers."""
from .markdown import normalize_markdown
from .empty_answer import subject_for, build_empty_answer

__all__ = [
    "normalize_markdown",
    "subject_for",
    "build_empty_answer",
]

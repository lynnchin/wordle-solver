"""Wordle API client and solver."""

__version__ = "0.1.0"

from app.config import Settings, get_settings
from app.models import GuessMode, GuessResponse, GuessResult, ResultKind

__all__ = [
    "Settings",
    "get_settings",
    "GuessMode",
    "GuessResponse",
    "GuessResult",
    "ResultKind",
    "__version__",
]

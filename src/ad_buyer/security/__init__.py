# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Security helpers for the buyer agent.

Currently exposes the prompt sanitizer used to neutralize
counterparty-controlled text before it enters buyer LLM prompts.
"""

from .prompt_sanitizer import (
    DEFAULT_MAX_LENGTH,
    sanitize_untrusted_text,
    wrap_untrusted_text,
)

__all__ = [
    "DEFAULT_MAX_LENGTH",
    "sanitize_untrusted_text",
    "wrap_untrusted_text",
]

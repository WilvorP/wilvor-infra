"""Provider-owned errors for malformed model/provider responses.

This module does not classify AWS transport failures. Raw injected-client
exceptions may propagate to the specialist boundary.
"""


class ModelProviderError(Exception):
    """Base provider-owned error. Message is a stable code, not a payload."""


class ModelProviderMalformedDecisionError(ModelProviderError):
    """Raised when a provider response cannot be mapped to one ModelDecision."""


class ModelProviderContextLengthError(ModelProviderError):
    """Raised when the provider reports a context-window overflow."""


__all__ = [
    "ModelProviderContextLengthError",
    "ModelProviderError",
    "ModelProviderMalformedDecisionError",
]

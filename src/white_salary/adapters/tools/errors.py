"""Structured tool failures used by the registry execution boundary."""


class ToolKnownFailure(RuntimeError):
    """The operation is known not to have completed."""


class ToolOutcomeUnknown(RuntimeError):
    """The external side effect may have completed; automatic retry is unsafe."""

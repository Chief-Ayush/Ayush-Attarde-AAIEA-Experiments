"""Exceptions mapped to the lab-manual failure categories (the MCP servers turn them into [CATEGORY] errors)."""


class InputError(ValueError):
    """Bad or missing input: missing file, malformed CSV, missing field, invalid argument, malformed configuration."""


class SecurityError(PermissionError):
    """Path traversal, unauthorized action, tampering attempt."""


class ToolFailure(RuntimeError):
    """A tool ran but could not do its job (for example the notification gateway refused the message)."""

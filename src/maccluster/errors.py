"""CLI and domain errors with exit codes."""

from __future__ import annotations

from typing import Any


class CliError(Exception):
    """Raised by commands/services; mapped to process exit codes by main."""

    def __init__(
        self,
        message: str,
        *,
        exit_code: int = 1,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.details = details

    def __str__(self) -> str:
        return self.message


class ConfigError(CliError):
    """Invalid or missing configuration (typically exit 2)."""

    def __init__(self, message: str, *, details: Any | None = None) -> None:
        super().__init__(message, exit_code=2, details=details)


class PrivilegeError(CliError):
    """Missing admin/sudo rights for a mutate operation."""

    def __init__(self, message: str | None = None, *, details: Any | None = None) -> None:
        msg = message or "admin/sudo required to modify network interfaces"
        super().__init__(msg, exit_code=1, details=details)


class PlatformError(CliError):
    """Unsupported platform for the requested operation."""

    def __init__(self, message: str, *, exit_code: int = 2, details: Any | None = None) -> None:
        super().__init__(message, exit_code=exit_code, details=details)


class DegradedError(CliError):
    """Operation partially succeeded; cluster not fully healthy."""

    def __init__(self, message: str, *, details: Any | None = None) -> None:
        super().__init__(message, exit_code=3, details=details)

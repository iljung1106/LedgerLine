from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True, slots=True)
class Diagnostic:
    severity: Severity
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class LedgerLineError(Exception):
    """Base exception carrying stable diagnostics for CLI and MCP adapters."""

    def __init__(self, message: str, diagnostics: list[Diagnostic] | None = None) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or []


class ValidationError(LedgerLineError):
    pass


class CapabilityError(LedgerLineError):
    pass

from __future__ import annotations


class AurError(RuntimeError):
    """Base error for the isolated AUR subsystem."""

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.detail = detail


class AurUnavailable(AurError):
    """A required local AUR capability is unavailable."""


class AurNetworkError(AurError):
    """The aurweb endpoint could not be reached reliably."""


class AurRpcError(AurError):
    """aurweb returned a valid RPC error response."""


class AurInvalidResponse(AurError):
    """aurweb returned malformed or unexpected data."""


class AurPackageNotFound(AurError):
    """An exact package name was not present in AUR."""


class AurVersionError(AurError):
    """Arch version comparison could not be completed."""


class AurHelperUnavailable(AurUnavailable):
    """yay is unavailable or failed its capability probe."""


class AurBuildToolsUnavailable(AurUnavailable):
    """Required AUR build tooling is incomplete."""


class AurPackageAlreadyInstalled(AurError):
    """The requested package name is already installed locally."""


class AurPackageNotInstalled(AurError):
    """The requested package is no longer installed locally."""


class AurPackageNotAur(AurError):
    """The installed package is no longer classified as foreign/AUR."""


class AurTransactionBusy(AurError):
    """Another package transaction currently owns the package database/workflow."""


class AurTransactionCancelled(AurError):
    """The user cancelled or interrupted the interactive AUR transaction."""


class AurTransactionFailed(AurError):
    """The terminal AUR transaction completed unsuccessfully."""


class AurSystemUpdateRequired(AurError):
    """A full official system update must happen before AUR mutation."""

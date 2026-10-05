"""Isolated read-only AUR subsystem introduced by App Store Stage 7.1."""

from .availability import AurAvailabilityProbe, AurCapabilityReport
from .errors import (
    AurError,
    AurInvalidResponse,
    AurNetworkError,
    AurBuildToolsUnavailable,
    AurHelperUnavailable,
    AurPackageAlreadyInstalled,
    AurPackageNotFound,
    AurRpcError,
    AurSystemUpdateRequired,
    AurTransactionBusy,
    AurTransactionCancelled,
    AurTransactionFailed,
    AurUnavailable,
    AurVersionError,
)
from .models import AurInstalledSnapshot, AurPackage, ForeignPackage
from .rpc import AurRpcClient
from .service import AurService

__all__ = [
    "AurAvailabilityProbe",
    "AurCapabilityReport",
    "AurBuildToolsUnavailable",
    "AurError",
    "AurHelperUnavailable",
    "AurInstalledSnapshot",
    "AurInvalidResponse",
    "AurNetworkError",
    "AurPackage",
    "AurPackageAlreadyInstalled",
    "AurPackageNotFound",
    "AurRpcClient",
    "AurRpcError",
    "AurService",
    "AurSystemUpdateRequired",
    "AurTransactionBusy",
    "AurTransactionCancelled",
    "AurTransactionFailed",
    "AurUnavailable",
    "AurVersionError",
    "ForeignPackage",
]

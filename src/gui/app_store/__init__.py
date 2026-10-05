"""Isolated Qt presentation layer for the Arch Manager application store."""

from .details import ApplicationDetailsDialog
from .page import AppStorePage

__all__ = ["AppStorePage", "ApplicationDetailsDialog"]

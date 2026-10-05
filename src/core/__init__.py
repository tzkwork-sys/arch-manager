"""Read-only system information backend for Arch Manager."""

from .dashboard import DashboardSummary, collect_dashboard_summary

__all__ = ["DashboardSummary", "collect_dashboard_summary"]

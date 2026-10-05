from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime

from .maintenance import MaintenanceSummary, collect_maintenance
from .restore_points import RestorePointsSummary, collect_restore_points
from .system_info import SystemInfo, collect_system_info
from .update_state import get_update_state_service
from .updates import UpdatesSummary


@dataclass(frozen=True)
class DashboardSummary:
    checked_at: datetime
    system: SystemInfo
    updates: UpdatesSummary
    restore_points: RestorePointsSummary
    maintenance: MaintenanceSummary

    @property
    def needs_attention(self) -> bool:
        """A concrete problem was found, not merely an unavailable read-only check."""
        return self.system.needs_attention

    @property
    def has_incomplete_checks(self) -> bool:
        """Important read-only checks could not provide a complete answer."""
        services_unknown = (
            self.system.failed_system_services is None
            or self.system.failed_user_services is None
        )
        system_unknown = self.system.has_incomplete_checks
        maintenance_unknown = any(
            value is None
            for value in (
                self.maintenance.reclaimable_cache_bytes,
                self.maintenance.orphan_packages,
                self.maintenance.journal_usage_bytes,
                self.maintenance.config_attention_files,
            )
        )
        restore_unknown = self.restore_points.count is None
        return (
            self.updates.partial
            or restore_unknown
            or services_unknown
            or system_unknown
            or maintenance_unknown
        )

    @property
    def has_review_items(self) -> bool:
        """Non-critical findings worth showing without calling the system broken."""
        updates_available = self.updates.total is not None and self.updates.total > 0
        no_restore_points = self.restore_points.count == 0
        orphan_packages = (self.maintenance.orphan_packages or 0) > 0
        config_files = (self.maintenance.config_attention_files or 0) > 0
        service_advisories = self.system.has_service_advisories
        system_warnings = self.system.has_warnings
        return (
            updates_available
            or no_restore_points
            or orphan_packages
            or config_files
            or service_advisories
            or system_warnings
        )

    @property
    def overall_status(self) -> str:
        if self.needs_attention:
            return "Требуется внимание"
        if self.has_incomplete_checks or self.has_review_items:
            return "Есть что проверить"
        return "Всё в порядке"


def collect_dashboard_summary() -> DashboardSummary:
    # Each collector is read-only. Running them concurrently keeps the GUI responsive
    # while update checks may need the network.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="arch-manager-stage2") as pool:
        system_future = pool.submit(collect_system_info)
        updates_future = pool.submit(lambda: get_update_state_service().refresh().summary)
        points_future = pool.submit(collect_restore_points)
        maintenance_future = pool.submit(collect_maintenance)
        return DashboardSummary(
            checked_at=datetime.now().astimezone(),
            system=system_future.result(),
            updates=updates_future.result(),
            restore_points=points_future.result(),
            maintenance=maintenance_future.result(),
        )

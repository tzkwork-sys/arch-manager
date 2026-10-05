from __future__ import annotations

import argparse

from .catalog import AppCatalogService, CatalogLoadResult


def format_catalog_diagnostics(result: CatalogLoadResult, *, examples: int = 5) -> str:
    stats = result.stats
    lines = [
        "Arch Manager — App Store Stage 1 diagnostics",
        f"cache: {'hit' if result.cache_hit else 'miss/rebuilt'}",
        f"AppStream metadata files: {stats.metadata_files}",
        f"AppStream components found: {stats.components_found}",
        f"desktop application components: {stats.desktop_components_found}",
        f"applications linked to official packages: {stats.official_packages_linked}",
        f"applications in final catalog: {stats.applications_built}",
        f"installed catalog applications: {stats.installed_applications}",
        f"skipped incomplete desktop components: {stats.skipped_components}",
        f"duplicate app IDs removed: {stats.duplicate_app_ids}",
    ]
    if stats.read_errors:
        lines.append("metadata read errors:")
        lines.extend(f"  - {error}" for error in stats.read_errors)
    sample = result.applications[: max(0, examples)]
    if sample:
        lines.append("examples:")
        for application in sample:
            state = "installed" if application.installed else "available"
            lines.append(
                f"  - {application.name} [{application.package_name}] "
                f"{application.repository or '?'} {application.available_version or '?'} ({state})"
            )
    elif stats.metadata_files == 0:
        lines.append("hint: install archlinux-appstream-data to provide /usr/share/swcatalog/xml/*.xml.gz")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the read-only Arch Manager application catalog")
    parser.add_argument("--no-cache", action="store_true", help="rebuild instead of reading the catalog cache")
    parser.add_argument("--examples", type=int, default=5, help="number of example applications to print")
    args = parser.parse_args(argv)
    result = AppCatalogService().load_catalog(use_cache=not args.no_cache)
    print(format_catalog_diagnostics(result, examples=args.examples))
    return 0 if result.stats.applications_built > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())

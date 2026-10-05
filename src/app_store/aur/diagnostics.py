from __future__ import annotations

import argparse

from .availability import AurCapabilityReport
from .service import AurService


def format_capabilities(report: AurCapabilityReport) -> str:
    status = "ready" if report.supported else "not ready"
    lines = [
        "Arch Manager — App Store AUR Stage 7.1 diagnostics",
        f"AUR support: {status}",
        f"running as root: {'yes' if report.running_as_root else 'no'}",
        f"yay: {report.yay_version or ('available' if report.yay_available else 'missing')}",
        f"git: {'available' if report.git_available else 'missing'}",
        f"makepkg: {'available' if report.makepkg_available else 'missing'}",
        f"vercmp: {'available' if report.vercmp_available else 'missing'}",
        f"pacman: {'available' if report.pacman_available else 'missing'}",
        "base-devel: " + (
            "complete" if report.base_devel_complete is True
            else "incomplete" if report.base_devel_complete is False
            else "unknown"
        ),
    ]
    if report.missing_base_devel:
        lines.append("missing base-devel packages: " + ", ".join(report.missing_base_devel))
    if report.issues:
        lines.append("issues: " + ", ".join(report.issues))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect read-only Arch Manager AUR capabilities")
    parser.add_argument("--search", help="optionally test an AUR search (read-only network request)")
    args = parser.parse_args(argv)
    service = AurService()
    report = service.capabilities()
    print(format_capabilities(report))
    if args.search:
        try:
            packages = service.search(args.search, use_cache=False)
        except Exception as exc:
            print(f"AUR search: failed: {exc}")
            return 3
        print(f"AUR search: {len(packages)} result(s)")
        for package in packages[:5]:
            print(f"  - {package.name} {package.version}")
    return 0 if report.supported else 2


if __name__ == "__main__":
    raise SystemExit(main())

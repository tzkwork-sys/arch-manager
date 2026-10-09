# Changelog

## Arch package 0.7.0beta1-1

- Pacman-managed installation, desktop launcher and ordinary privileged helpers.
- Reproducible PKGBUILD/.SRCINFO and a separate build bundle with SHA-256 checks.
- Package-owned Polkit rules for active local sessions, without hard-coded usernames.
- Isolated launcher and explicit `--check`; optional per-user Snapper read access.
- Recovery is not installed or enabled by the base package.
- Actual clean-system installation and system transactions remain unvalidated.

## 0.7.0-beta.1 — Unreleased

- First public release candidate of the Python/Qt Arch Manager GUI.
- Unified official/AUR updates, application catalog and installed package views.
- Snapper restore points, maintenance, diagnostics and experimental Recovery.
- Updated application navigation and reduced redundant sudo authorization.
- Release installation instructions, GPLv3 license and automated checks.
- Package-operation timeout no longer forcibly terminates a running helper.

This candidate still requires clean-system and Recovery validation before publication.

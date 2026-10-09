# Release candidate 0.7.0-beta.1

License: GPL-3.0-only. This is a candidate, not a published or certified release.

## Deferred system validation

The owner has deferred stage 4 because a separate Arch test installation is not
available. Clean-system installation, actual package changes, interrupted helper
updates, local rollback and physical USB/UEFI boot have **not** been validated
for this candidate. If released, it must remain a **prerelease beta**, explicitly
disclosing these gaps. This is not approval for a stable release or for relying
on Recovery on a production machine without an independent backup.

A draft release does not change repository visibility or publish the candidate.

Before publication:

- Run all tests, Python compilation, Bash syntax checks and desktop validation.
- Install from a clean source archive on a separate Arch Linux x86_64 machine.
- Check installation, upgrade, removal and optional helper permissions.
- Check offline startup, canceled authentication and concurrent operations.
- Exercise a slow package transaction: elapsed timeout must not kill pacman or
  release the application's transaction coordinator before the helper exits.
- Test interrupted Recovery helper updates and repair from retained backups.
- Test local/USB Recovery and actual rollback only on disposable disks/VMs with
  independent backups. Unit/source-contract tests do not prove recovery safety.
- Inspect the entire Git history for secrets/private data before making it public.
- Configure protected main, required CI and contribution/review policy on GitHub.

Current installation is source-based, not an Arch package. Dependency versions
tested during development: Python 3.14, PySide6 6.12 and pytest 9.1. Compatibility
with other versions requires CI and installation testing. GPL applies to this
project; external dependencies retain their own licenses. Confirm provenance of
included code/assets before publication.

Known boundaries: directory replacement preserves backups but is not a single
atomic transaction spanning helpers, policies and sudoers. Forced power loss or
SIGKILL can require manual repair. A package helper that hangs indefinitely keeps
the GUI's package coordinator occupied; elapsed time alone is not safe cancellation.

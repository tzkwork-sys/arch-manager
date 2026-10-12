# Flatpak / Flathub in Arch Manager

Arch Manager continues to use pacman for Official and yay for AUR. Flatpak is a third, independent source, identified by application ID and installation scope (system/user). Search uses the configured Flathub remote; installed entries are obtained from Flatpak itself. An official and Flatpak build of the same application remain separate entries.

## Supported workflows

- Installed Flatpak apps appear under All / Installed / Flatpak, including system-installed apps from KDE Discover.
- Search from the store finds Flathub applications (enter at least two characters). An existing Flathub remote and Flatpak package are required.
- App details offer install/remove/update/launch via Flatpak. Commands are run asynchronously, with no shell or direct root escalation. Flatpak handles its own polkit authorization for system installs.
- Shared update discovery combines Official, AUR, Flatpak and displays separate source filters. Users can select individual Flatpak updates. When mixed updates are selected, the existing full Arch upgrade runs first; Flatpak updates run only after its successful completion. Flatpak-only selection updates without invoking pacman.
- User and system installation scopes are kept separate for removal and updates.

## Notes / constraints

- Flatpak search returns the CLI's basic metadata; Flathub category and screenshot metadata are not available through that interface and are not fabricated. Installed application desktop file metadata is not rewritten.
- KDE/Flatpak must have a functioning system Polkit agent for system-wide install/remove/update. If authentication fails, the operation reports the error.
- This patch does **not** configure or install Flatpak or Flathub automatically, change pacman/yay helpers, or push commits.
- After applying, check the Installed/Flatpak filter with `io.github.mhogomchungu.media-downloader`, then try a search and a safe GUI install/remove test. Recheck the Updates view and ensure Official/AUR counts remain correct.

# Flatpak / Flathub in Arch Manager

Arch Manager continues to use pacman for Official and yay for AUR. Flatpak is a third, independent source, identified by application ID and installation scope (system/user). Search uses the configured Flathub remote; installed entries are obtained from Flatpak itself. An official and Flatpak build of the same application remain separate entries.

## Supported workflows

- Installed Flatpak apps appear under All / Installed / Flatpak, including system-installed apps from KDE Discover.
- Search from the store finds Flathub applications (enter at least two characters). An existing Flathub remote and Flatpak package are required.
- App details offer install/remove/update/launch via Flatpak. Commands are run asynchronously, with no shell or direct root escalation. Flatpak handles its own polkit authorization for system installs.
- Shared update discovery combines Official, AUR, Flatpak and displays separate source filters. Users can select individual Flatpak updates. When mixed updates are selected, the existing full Arch upgrade runs first; Flatpak updates run only after its successful completion. Flatpak-only selection updates without invoking pacman.
- User and system installation scopes are kept separate for removal and updates.

## Card metadata and trustworthy installed counts

- Cards show a short description, repository, available/installed version and installation scope; their tooltip shows the complete Flatpak application ID.
- For icons the store checks exported local hicolor icons and Flatpak's locally cached Flathub AppStream icons. There are no invented images and no remote image requests from the card renderer. If no matching icon exists it falls back to the desktop theme.
- The Installed/Flatpak view checks both `flatpak list --app --system` and `flatpak list --app --user`. Only a successful read from *both* can justify a zero count. A failed read is shown separately, not misreported as zero.
- Check the actual installation state independently with `flatpak list --app --columns=application,installation,version`.

## Notes / constraints

- Flatpak search returns the CLI's basic metadata; Flathub category and screenshot metadata are not available through that interface and are not fabricated. Installed application desktop file metadata is not rewritten.
- KDE/Flatpak must have a functioning system Polkit agent for system-wide install/remove/update. If authentication fails, the operation reports the error.
- This patch does **not** configure or install Flatpak or Flathub automatically, change pacman/yay helpers, or push commits.
- After applying, check the Installed/Flatpak filter with `io.github.mhogomchungu.media-downloader`, then try a search and a safe GUI install/remove test. Recheck the Updates view and ensure Official/AUR counts remain correct.

## Rich AppStream details (Flathub)

The existing Official application details UI is reused for Flatpak: a scrollable
window, reusable screenshot tiles and fullscreen screenshot viewer, HTML-safe
long descriptions, license/publisher details, and verified external links. Flatpak
operations continue through their own Flatpak QProcess code, **never** pacman.

When a Flatpak details window is *shown*, the app ID is queried from
`https://flathub.org/api/v2/appstream/<app-id>`. The catalog and card search do
not request AppStream details. The response is bounded to 512 KiB, validated to
match the requested app ID, sanitized and cached for 24 hours (up to 120
entries). A stale cache can be reused if offline. Screenshot and icon URLs must
point to HTTPS Flathub-hosted media; screenshot fetching reuses the official
MediaCache with its limits and 96 MiB disk cache. Only up to eight screenshots
are requested, and only after the details view opens. Missing optional fields or
network failures leave the original Flatpak metadata and install/remove actions
available. No new package dependency, Polkit rule or system helper is required.

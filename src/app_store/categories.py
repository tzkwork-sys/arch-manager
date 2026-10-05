from __future__ import annotations

from collections.abc import Iterable


CATEGORY_LABELS_RU: dict[str, str] = {
    "internet": "Интернет",
    "office": "Офис",
    "graphics": "Графика",
    "photo-video": "Фото и видео",
    "audio": "Аудио",
    "development": "Разработка",
    "education": "Образование",
    "games": "Игры",
    "system-tools": "Система и инструменты",
    "science": "Наука",
    "communication": "Связь",
    "other": "Прочее",
}

_ORDER = tuple(CATEGORY_LABELS_RU)

_COMMUNICATION = {
    "Chat",
    "Email",
    "InstantMessaging",
    "IRCClient",
    "Telephony",
    "VideoConference",
}
_INTERNET = {
    "Network",
    "WebBrowser",
    "FileTransfer",
    "P2P",
    "RemoteAccess",
    "News",
}
_OFFICE = {
    "Office",
    "Calendar",
    "ContactManagement",
    "Database",
    "Dictionary",
    "Finance",
    "FlowChart",
    "Presentation",
    "ProjectManagement",
    "Spreadsheet",
    "WordProcessor",
}
_GRAPHICS = {
    "Graphics",
    "2DGraphics",
    "3DGraphics",
    "RasterGraphics",
    "VectorGraphics",
    "Publishing",
    "Viewer",
}
_PHOTO_VIDEO = {
    "Photography",
    "Video",
    "VideoEditing",
    "Recorder",
    "TV",
}
_AUDIO = {
    "Audio",
    "AudioVideo",
    "AudioVideoEditing",
    "Midi",
    "Mixer",
    "Music",
    "Player",
}
_DEVELOPMENT = {
    "Development",
    "Building",
    "Debugger",
    "GUIDesigner",
    "IDE",
    "Profiling",
    "RevisionControl",
    "Translation",
    "WebDevelopment",
}
_EDUCATION = {
    "Education",
    "Art",
    "Languages",
    "Literature",
}
_GAMES = {
    "Game",
    "ActionGame",
    "AdventureGame",
    "ArcadeGame",
    "BoardGame",
    "BlocksGame",
    "CardGame",
    "KidsGame",
    "LogicGame",
    "RolePlaying",
    "Simulation",
    "SportsGame",
    "StrategyGame",
}
_SYSTEM = {
    "System",
    "Settings",
    "Utility",
    "ConsoleOnly",
    "FileManager",
    "Filesystem",
    "Monitor",
    "Security",
    "TerminalEmulator",
}
_SCIENCE = {
    "Science",
    "Astronomy",
    "Biology",
    "Chemistry",
    "ComputerScience",
    "DataVisualization",
    "Economy",
    "Electricity",
    "Engineering",
    "Geography",
    "Geology",
    "Geoscience",
    "Math",
    "MedicalSoftware",
    "NumericalAnalysis",
    "Physics",
    "Robotics",
}


def normalize_categories(categories: Iterable[str]) -> tuple[str, ...]:
    """Map AppStream/Freedesktop categories to a small stable catalog taxonomy."""

    raw = {item.strip() for item in categories if item and item.strip()}
    matched: set[str] = set()
    if raw & _COMMUNICATION:
        matched.add("communication")
    if raw & _INTERNET:
        matched.add("internet")
    if raw & _OFFICE:
        matched.add("office")
    if raw & _GRAPHICS:
        matched.add("graphics")
    if raw & _PHOTO_VIDEO:
        matched.add("photo-video")
    if raw & _AUDIO:
        matched.add("audio")
    if raw & _DEVELOPMENT:
        matched.add("development")
    if raw & _EDUCATION:
        matched.add("education")
    if raw & _GAMES:
        matched.add("games")
    if raw & _SYSTEM:
        matched.add("system-tools")
    if raw & _SCIENCE:
        matched.add("science")
    if not matched:
        matched.add("other")
    return tuple(category for category in _ORDER if category in matched)

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
AUR = ROOT / "src" / "app_store" / "aur"


def test_stage71_is_isolated_from_official_store_sources():
    assert AUR.is_dir()
    official_init = (ROOT / "src" / "app_store" / "__init__.py").read_text(encoding="utf-8")
    assert "aur" not in official_init.lower()


def test_stage71_contains_no_mutating_package_commands_or_shell_execution():
    sources = "\n".join(path.read_text(encoding="utf-8") for path in AUR.glob("*.py"))
    forbidden = (
        "shell=True",
        "pacman -S",
        "pacman -R",
        "yay --aur -S",
        "yay -Rns",
        "yay -Sua",
        "--noconfirm",
        "--skipchecksums",
        "--skippgpcheck",
        "makepkg -",
    )
    for token in forbidden:
        assert token not in sources


def test_stage71_does_not_touch_official_helper_contract():
    helper = (ROOT / "src" / "privileged" / "manage_applications.sh").read_text(encoding="utf-8")
    assert "yay" not in helper
    assert "AUR" not in helper

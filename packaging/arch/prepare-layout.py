#!/usr/bin/env python3
"""Adapt a verified upstream source tree to pacman-owned system paths."""
from pathlib import Path
import sys


def prepare(root: Path) -> None:
    # Restrict substitutions to host helpers. ISO-internal /usr/local paths
    # must not be rewritten; the base package never installs the Recovery engine.
    files = list((root / "src/core").glob("*.py"))
    files += list((root / "scripts").glob("run-*.sh"))
    files += list((root / "packaging/polkit").glob("*.policy"))
    for path in files:
        text = path.read_text()
        text = text.replace("/usr/local/libexec/arch-manager/", "/usr/lib/arch-manager/")
        path.write_text(text)
    diagnostics = root / "src/privileged/read_system_diagnostics.py"
    text = diagnostics.read_text()
    assert text.startswith("#!/usr/bin/python\n")
    diagnostics.write_text(text.replace("#!/usr/bin/python\n", "#!/usr/bin/python -I\n", 1))


if __name__ == "__main__":
    prepare(Path(sys.argv[1]).resolve())

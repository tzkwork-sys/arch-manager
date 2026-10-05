from __future__ import annotations

from dataclasses import dataclass
import os
import shutil
import subprocess
from typing import Sequence


@dataclass(frozen=True)
class CommandResult:
    command: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    available: bool = True
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.available and not self.timed_out and self.returncode == 0


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def run_command(
    args: Sequence[str],
    *,
    timeout: float = 30.0,
    accepted_returncodes: set[int] | None = None,
) -> CommandResult:
    """Run a read-only helper command without a shell or privilege escalation."""
    command = tuple(str(part) for part in args)
    if not command:
        raise ValueError("command must not be empty")

    if not command_exists(command[0]):
        return CommandResult(command, 127, "", f"{command[0]} not found", available=False)

    env = os.environ.copy()
    # Stable English output makes parsers independent of the desktop locale.
    env["LC_ALL"] = "C"
    env["LANG"] = "C"

    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        return CommandResult(command, 124, stdout, stderr or "timeout", timed_out=True)
    except OSError as exc:
        return CommandResult(command, 126, "", str(exc), available=False)

    result = CommandResult(command, completed.returncode, completed.stdout, completed.stderr)
    if accepted_returncodes is not None and completed.returncode in accepted_returncodes:
        return result
    return result

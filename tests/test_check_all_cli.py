#!/usr/bin/env python3
"""Offline CLI checks for scripts/check_all.sh (non-recursive).

Only validates POSIX sh syntax and immediate exit-code-2 rejection of invalid or
extra arguments (with a timeout and an assertion that no check step ran). Never
invokes `check_all.sh --offline` from inside the test suite to avoid recursion.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECK_ALL = ROOT / "scripts" / "check_all.sh"
TIMEOUT_SECONDS = 10


def main() -> int:
    syntax = subprocess.run(
        ["sh", "-n", str(CHECK_ALL)],
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    assert syntax.returncode == 0, f"sh -n failed: {syntax.stderr}"

    for bad_args in (
        ["--bogus"],
        ["--offline", "extra"],
        ["--offline", "--offline"],
        ["--help"],
        ["offline"],
    ):
        proc = subprocess.run(
            ["sh", str(CHECK_ALL), *bad_args],
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
        assert proc.returncode == 2, (bad_args, proc.returncode, proc.stdout, proc.stderr)
        assert "Usage:" in proc.stderr, (bad_args, proc.stderr)
        assert "==> " not in proc.stdout, (bad_args, proc.stdout)

    print("check_all.sh CLI syntax and argument rejection tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

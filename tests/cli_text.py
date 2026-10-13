"""Shared by CLI tests that match on `--help` text.

On GitHub Actions Rich forces colour even when output is captured, so the text carries ANSI escape codes
(bold, dim, the box-drawing borders) between the words a regex looks for. Strip them first; the assertion
itself stays exactly as strict as before. Reproduce locally with `GITHUB_ACTIONS=true pytest`."""

import re

_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    """`text` without ANSI CSI escape sequences (colours, bold/dim, cursor codes)."""
    return _ANSI.sub("", text)

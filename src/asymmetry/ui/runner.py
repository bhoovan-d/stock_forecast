"""Entry point for a command launched by the panel.

Rich makes two decisions when its output is a pipe that are wrong here: it drops colour,
and on Windows it falls back to the legacy console renderer, which replaces the
box-drawing glyphs with ``+---+``. The panel's log pane *is* a terminal — it parses ANSI
and renders the glyphs — so the console is rebuilt with those decisions forced before the
CLI's own console is used.

Run as ``python -m asymmetry.ui.runner <command> [options]``.
"""

from __future__ import annotations

import os
import sys

DEFAULT_WIDTH = 150


def main() -> None:
    from rich.console import Console

    from asymmetry import cli

    width = int(os.environ.get("ASYMMETRY_UI_WIDTH", DEFAULT_WIDTH))
    # 16-colour rather than truecolor on purpose: the panel maps the eight ANSI colours
    # onto its own tokens, so green/red stay legible in both the light and dark theme
    # instead of arriving as terminal RGB tuned for one of them.
    cli.console = Console(
        force_terminal=True,
        color_system="standard",
        legacy_windows=False,
        width=width,
        soft_wrap=False,
    )
    cli.app(prog_name="asymmetry")


if __name__ == "__main__":
    sys.exit(main())

"""The panel's command registry, argument building and file boundaries.

The registry is the only thing standing between a browser POST and a subprocess, so what
it refuses matters as much as what it builds.
"""

from __future__ import annotations

import pytest

from asymmetry.ui import commands as registry
from asymmetry.ui.server import _safe_child


# ── the registry mirrors the CLI ──────────────────────────────────────────────


def test_every_registered_command_exists_in_the_cli():
    """A typo in the registry would only surface as a failed run, so check it here."""
    import typer.main

    from asymmetry.cli import app

    click_app = typer.main.get_command(app)
    for command in registry.COMMANDS:
        node = click_app
        for part in command.argv:
            assert part in node.commands, f"{command.id}: no CLI command `{part}`"
            node = node.commands[part]


def test_registered_options_exist_on_their_command():
    import typer.main

    from asymmetry.cli import app

    click_app = typer.main.get_command(app)
    for command in registry.COMMANDS:
        node = click_app
        for part in command.argv:
            node = node.commands[part]
        known = {opt for param in node.params for opt in getattr(param, "opts", [])}
        known |= {opt for param in node.params for opt in getattr(param, "secondary_opts", [])}
        for field in command.fields:
            for flag in (field.flag, field.off_flag):
                if flag:
                    assert flag in known, f"{command.id}: `{flag}` is not an option"


# ── argv construction ─────────────────────────────────────────────────────────


def test_toggle_emits_its_negative_form():
    argv = registry.build_argv(registry.BY_ID["brief"], {"refresh": False, "html": True})
    assert "--no-refresh" in argv and "--refresh" not in argv
    assert "--html" in argv


def test_flag_is_omitted_when_off():
    argv = registry.build_argv(registry.BY_ID["scan"], {"no_catalyst": False, "top": 5})
    assert "--no-catalyst" not in argv
    assert argv[:1] == ["scan"] and "--top" in argv and "5" in argv


def test_multi_repeats_its_flag():
    argv = registry.build_argv(
        registry.BY_ID["v3"], {"setup": ["reclaim", "continuation"], "per_day": 2}
    )
    assert argv.count("--setup") == 2
    assert "reclaim" in argv and "continuation" in argv


def test_blank_optional_value_is_dropped():
    argv = registry.build_argv(registry.BY_ID["regime"], {"on": ""})
    assert argv == ["regime"]


def test_positionals_lead_the_option_list():
    argv = registry.build_argv(
        registry.BY_ID["journal-log"],
        {"symbol": "reliance", "action": "taken", "price": "1330", "qty": "40", "note": "half"},
    )
    assert argv[:4] == ["journal", "log", "RELIANCE", "taken"]
    assert argv[4:] == ["--price", "1330.0", "--qty", "40", "--note", "half"]


# ── what the registry refuses ────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("command", "values", "fragment"),
    [
        ("journal-log", {"symbol": "A; rm -rf /", "action": "taken"}, "Symbol may only"),
        ("journal-log", {"symbol": "--help", "action": "taken"}, "Symbol may only"),
        ("journal-log", {"symbol": "", "action": "taken"}, "required"),
        ("journal-log", {"symbol": "TCS", "action": "deleted"}, "not a valid choice"),
        ("regime", {"on": "yesterday"}, "must look like"),
        ("backfill", {"days": "all"}, "whole number"),
        ("v3", {"setup": ["breakout"]}, "not a valid choice"),
    ],
)
def test_bad_values_are_refused(command, values, fragment):
    with pytest.raises(ValueError, match=fragment):
        registry.build_argv(registry.BY_ID[command], values)


def test_note_is_length_capped_but_otherwise_free():
    argv = registry.build_argv(
        registry.BY_ID["journal-log"],
        {"symbol": "TCS", "action": "skipped", "note": "x" * 900},
    )
    assert len(argv[argv.index("--note") + 1]) == 500


# ── file serving stays inside its directory ──────────────────────────────────


def test_safe_child_refuses_paths_that_climb_out(tmp_path):
    (tmp_path / "inside.md").write_text("ok", encoding="utf-8")
    secret = tmp_path.parent / "outside.env"
    secret.write_text("secret", encoding="utf-8")

    assert _safe_child(tmp_path, "inside.md") is not None
    assert _safe_child(tmp_path, "../outside.env") is None
    assert _safe_child(tmp_path, "..%2Foutside.env") is None
    assert _safe_child(tmp_path, "missing.md") is None

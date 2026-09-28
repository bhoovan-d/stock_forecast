"""Fail CI when a clean-room track reaches legacy or cross-track implementation code."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRACKS = {
    "fno_momentum": ROOT / "systems" / "fno_momentum" / "src" / "fno_momentum",
    "nifty500_source_news": (
        ROOT / "systems" / "nifty500_source_news" / "src" / "nifty500_source_news"
    ),
}
LEGACY_IMPORT = "asymmetry"
FORBIDDEN_PATH_TEXT = (
    "src/asymmetry",
    "src\\asymmetry",
    "../data",
    "..\\data",
    "data/asymmetry.db",
    "data\\asymmetry.db",
    "v3_watch.jsonl",
    "v3_probability.json",
    "trident_failures.jsonl",
)


def imports(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def check() -> list[str]:
    failures: list[str] = []
    for track, directory in TRACKS.items():
        other_tracks = set(TRACKS) - {track}
        for path in directory.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
            for name in imports(tree):
                root = name.split(".", 1)[0]
                if root == LEGACY_IMPORT:
                    failures.append(f"{path}: imports legacy package {name}")
                if root in other_tracks:
                    failures.append(f"{path}: imports isolated track {name}")
            for token in FORBIDDEN_PATH_TEXT:
                if token in text:
                    failures.append(f"{path}: references forbidden legacy path {token}")
        pyproject = directory.parents[1] / "pyproject.toml"
        if LEGACY_IMPORT in pyproject.read_text(encoding="utf-8").lower():
            failures.append(f"{pyproject}: declares a legacy implementation dependency")
    return failures


if __name__ == "__main__":
    errors = check()
    if errors:
        print("\n".join(errors))
        raise SystemExit(1)
    print("clean-room boundaries verified")

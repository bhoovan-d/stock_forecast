"""Run the approved FnO collector as the scheduled task's direct process."""

from pathlib import Path
import runpy
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "systems" / "fno_momentum" / "src"))
runpy.run_module("fno_momentum.full_underlying_collection", run_name="__main__")

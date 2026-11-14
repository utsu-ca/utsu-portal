import ast
import sqlite3
from pathlib import Path

import pytest

from utsu_core.spec import ProgramSpec
from utsu_core.programs.agm.program import SPEC as AGM_SPEC
from utsu_core.programs.sap.program import SPEC as SAP_SPEC
from utsu_core.programs.senate.program import SPEC as SENATE_SPEC

SRC = Path(__file__).resolve().parents[1] / "src"
SPECS = [SENATE_SPEC, SAP_SPEC, AGM_SPEC]


@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.slug)
def test_a_spec_describes_its_table(spec: ProgramSpec):
    conn = sqlite3.connect(":memory:")
    conn.executescript(spec.schema_text)
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (spec.table,)).fetchone()
    if spec.insert:  # what a rebuild saves must fit the table
        row = {c: None for c in ("uuid", "student_uuid", "uoft_uuid", "verification_level", "match_method", "reason",
                                 "submission_ts", "meeting_preference", "contact_ok", "flags", "delegated")}
        conn.execute(spec.insert, {**row, "status": "unverified"})


def imported_packages(package: str, recursive: bool = True) -> set[str]:
    found = set()
    paths = (SRC / package).rglob("*.py") if recursive else (SRC / package).glob("*.py")
    for path in paths:
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
            elif isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
    return found


@pytest.mark.parametrize("program", ["senate", "sap", "agm"])
def test_a_program_depends_on_the_core_and_not_on_the_other_programs(program):
    others = {"senate", "sap", "agm"} - {program}
    for module in imported_packages(f"utsu_core/programs/{program}"):
        assert not any(module.startswith(f"utsu_core.programs.{other}") for other in others), module
        assert not module.startswith("utsu_core.mail"), module


def test_shared_processing_engine_does_not_import_programs():
    for module in imported_packages("utsu_core", recursive=False):
        assert not module.startswith(("utsu_core.programs", "utsu_core.mail")), module

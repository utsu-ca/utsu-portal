# 05 Migration and removal of the batch tools

This is a proposed future migration, not a description of completed work. All current functionality is packaged under
`utsu_core`. The four program command entry points have been removed; their underlying batch processing logic is
retained for parity and migration work. Portal replacements are not yet complete.

## 1. What is removed, and when

Removal is the last phase (P6). Nothing is deleted until its replacement passes the ported tests.

| Remove | Replaced by | Condition |
|---|---|---|
| Shared batch processing modules in `src/utsu_core/` | `utsu_core/services/` | P1 done, tests ported |
| Batch processors in `src/utsu_core/programs/{senate,sap,agm}`, `runner.py` | Live program modules | P2-P4 done |
| `src/utsu_core/mail/` | `services/notices.py` + `Mailer` | P1 done |
| `src/utsu_core/std/` | `services/text.py`, `services/exports.py` | P1 done |
| `[project.scripts]` (`utsu-senate`, `utsu-sap`, `utsu-agm`, `utsu-all`) | none | Removed |
| `testing/generate_fake_*.py` | Keep as test fixtures; add portal-compatible seeding and parity tests as needed | Ongoing |
| `data/*.csv` | seats/weights become stored data; conversion tables are dropped | P2-P4 |
| Batch-tool content in `README.md` and `wiki/` (Command-Line-Reference, Input-and-Output-Files, Operations-Runbook, Full-Exports-and-Integrity, ...) | portal docs | P6 |
| `IMPLEMENTATION-COMPLETE.txt`; move `SAP Reimagined Rubric & Procedures Draft.pdf` to `docs/reference/` | n/a | P6 |

Kept: `utsu_core` core, `deploy/`, `infra/`, `Dockerfile*`, `docker-compose*.yml`, `nginx.conf`, observability
files, `LICENSE`.

## 2. Packaging

- Make the portal dependencies the base dependencies in `pyproject.toml` (`fastapi`, `uvicorn`, `pyyaml`,
  `cryptography`, `segno`, `redis`, `google-re2`); keep `dev`, `firestore`, `sql`, `observability`, `filesvc`,
  `authsvc` extras. Rename the project `utsu-portal`.
- `[tool.setuptools.packages.find] include = ["utsu_core*"]`; package data adds the email templates.
- Move `templates/email` into the package so it ships with the wheel.

## 3. Data migration (one-off)

`python -m utsu_core.migrate <sqlite-db> --program {senate,sap,agm}`

- Roster: `uoft_data` -> `roster` (re-keyed with the portal pepper).
- Senate/AGM: not migrated; they are computed from submissions on request.
- SAP: `student_aid`, ratings and decisions are **kept state**. Applications migrate into `sap_applications`; each
  rating becomes a `reviews[]` entry (reviewer from the sheet, `submitted_at`, no lease); decisions into
  `sap_decisions`. Batches are dropped, but the old record id (`B012-01`) is kept as `legacy_record_id` so returned
  sheets still resolve; new applications get random `review_id`s. A dry run prints counts and mismatches; the
  importer refuses to overwrite existing portal records.
- Historical form exports can be imported as submissions through the forms store with a `legacy` marker.

## 4. Order of work for the first PRs

1. `services/` skeleton + text/exports/selection ported with tests (smallest, unblocks all).
2. Roster + alerts.
3. AGM move (mostly renames; lowest risk).
4. Senate.
5. SAP in slices: intake+WFNI, queue (claim/lease/assign), ratings, decisions, banking/EFT.
6. UI pages, then the removal PR and docs rewrite.

Parity must be checked directly against the retained processing logic and fixtures (same inputs should give identical
applications, flags, batches and, for a fixed seed, seats). The old program commands are no longer available.

## 5. Acceptance

- `python -m pytest` passes with the moved tests.
- Parity run: for a fixed fixture, the portal's computed views equal the CLI's CSV output (Senate/AGM; SAP scores,
  flags and suggested amounts; batches no longer apply).
- No Senate or AGM derived state is written to the store (test asserts the store is unchanged after list requests).
- No imports of the retired top-level packages remain; no program module imports another.
- A test shows reviewer endpoints never return names, emails or student numbers.

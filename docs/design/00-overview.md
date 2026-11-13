# UTSU Portal: conversion overview and plan

Status: proposed. Audience: maintainers. Companion documents: [01 Shared services](01-shared-services.md),
[02 Senate](02-senate.md), [03 SAP](03-sap.md), [04 AGM](04-agm.md), [05 Migration and removal](05-migration.md).

## 1. Goal

The repository today is two systems:

1. **Batch tools** (`utsu_core`, `utsu_programs/{senate,sap,agm}`, `utsu_mail`, `utsu_std`): CSV-export-in, CSV/DOCX-out
   programs driven by a CLI and a SQLite file (`utsu-senate`, `utsu-sap`, `utsu-agm`, `utsu-all`).
2. **The portal** (`utsu_portal`): a FastAPI application with accounts, ID verification, forms, clubs, SSO, audit,
   and a module system (`Module.router(ctx)`).

The goal is a repository that is **only the UTSU Portal**. Each program (Senate, SAP, AGM) becomes a **portal
module** that works on live data in the portal store, replacing the "export a Google Form, run a CLI, hand around
files" workflow. The batch CLI, the `uoft_data` SQLite database and the Google-Forms-export reading are retired once
their behaviour is reproduced (see 05).

Non-goals: changing UTSU policy (seat rules, rubric, WFNI weights, eligibility), rewriting the account/verification
core, adding a front-end framework (same-origin vanilla JS and the CSP model stay).

## 2. Current state and gap analysis

| Capability | Batch tools today | Portal today | Gap |
|---|---|---|---|
| Student roster (`uoft_data`) import and matching | `utsu_core.importing_data`, `verification` (exact + fuzzy match, levels) | None. Identity is OTP email + face/ID + admin approval | Roster service (01) |
| Typed uuids, one binding application per person | `selection.py`, `pipeline.Run` | Per-account submissions only | Selection service (01) |
| Integrity alerts (duplicates, mismatches, non-binding) | `integrity.py` | None | Alerts service (01) |
| Form definitions | Google Form exports + lookup tables | YAML forms for all three programs (`forms/*.yaml`) | Program-specific fields (WFNI, banking, seat preferences) must be checked against the CSV mapping |
| Mail-merge, templates | `utsu_mail` | `Mailer` (OTP only), `SMTPMailer` | Notice service (01) |
| Senate verification + seating | `senate/program.py`, `seating.py` | Form only | Senate module (02) |
| SAP batches, WFNI, review, decisions, EFT | `sap/*` | Form only | SAP module (03) |
| AGM verification, QR, check-in, Zoom SSO | `agm/program.py` | `agm.py`, `agm_verify.py`, `sso.py` done | Eligibility from roster, reporting (04) |
| Clubs | None | `clubs.py` complete | None |
| Roles | None | `roles.py`, `ROLES` in `context.py` | New roles per program (01) |
| Audit, encryption, rate limit, metrics | None | Present | Reuse; add events per module |

## 3. Target architecture

Modules are plain portal modules (`name`, `router(ctx)`), one Python package per program, registered in
`create_app`'s module tuple. A program module **depends on the shared services and on the portal core, never on
another program** (same rule the batch code has).

```
src/utsu_portal/
  app.py, context.py, store.py, ...         existing core (unchanged)
  services/                                  shared, program-neutral (see 01)
    roster.py        student roster import + matching (port of utsu_core.verification/importing_data)
    selection.py     binding-application selection, typed uuids
    alerts.py        integrity alerts
    notices.py       templated notices via Mailer (port of utsu_mail)
    exports.py       CSV/DOCX/EFT export helpers, spreadsheet-injection-safe (port of utsu_std.csv_safe)
    text.py          cleaning and parsing helpers (port of utsu_std.utils/parsing_utils)
  programs/
    senate/          module.py, seating.py, models.py
    sap/             module.py, wfni.py, queue.py, review.py, decisions.py, banking.py, eft.py, models.py
    agm/             module.py (moves modules/agm.py, agm_verify.py), eligibility.py
  modules/                                   existing non-program modules (auth, forms, admin, clubs, ...)
  forms/                                     bundled YAML definitions
```

Each program package exposes a single `MODULE` object. `app.py` assembles `CORE_MODULES + PROGRAM_MODULES`;
deployments that run only some programs can pass a subset (the microservice `REMOTE_MODULES` pattern exists).

### Principles

- **Store, not SQLite.** Facts live in `Store` collections (`roster`, `sap_applications`, `sap_decisions`,
  `sap_banking`, `sap_weights`, `senate_seats`, `senate_published`, `agm_checkins`). Every state change is audited
  (`audit.py`) and uses the existing optimistic `update`/lock helpers.
- **Forms are the intake.** Submissions of `senate_application`, `student_aid_bursary` and `agm_rsvp` forms are the
  source of truth.
- **Stateless compute (serverless-safe).** Workers share no memory and there is no reliable cache, so derived
  state is never stored. Senate and AGM **compute on request** (admin or the authorized user) from submissions +
  roster; only facts (submissions, roster, check-ins, published snapshots) are stored. SAP is the exception that
  stores *committee work* (reviews, decisions) as facts on the application, and still **derives** stage, scores and
  selection on read. Concurrency uses optimistic locks, never in-process state; there is no background reconcile.
  Any index added for speed must be rebuildable and never authoritative.
- **Client modes.** Role views are "modes" in the one client (see 03 section 4c).
- **SAP is a review queue**, not batches: reviewers claim applications (or an admin assigns one), each application
  carries its own reviewers (see 03).
- **Pure logic stays pure.** Algorithms (seating assignment, WFNI, batch assignment, suggested amounts, EFT, review
  merging) are ported as dependency-free functions with their existing tests. Only thin routers touch the store.
- **Anonymity for reviewers.** SAP reviewers only ever receive an anonymized projection (record id, redacted text),
  enforced at the API layer by role, not by file hygiene.
- **Roles per program.** New roles `senate_admin`, `sap_admin`, `sap_reviewer`, `agm_admin` (check-in staff). `admin`
  keeps access to everything. Added to `ROLES` in `context.py` and administered by the existing roles module.
- **Least data.** Banking details and student numbers are encrypted at rest (`crypto.EncryptingStore`) and only
  served to `sap_admin` through the audited banking export endpoint.

## 4. Delivery plan

| Phase | Deliverable | Depends on | Done when |
|---|---|---|---|
| P0 | This design set reviewed | none | Maintainers accept 00-05 |
| P1 | `services/` (roster, selection, alerts, text, exports, notices) with ported tests | P0 | Tests from `test_lookup`, `test_identity`, `test_unicode`, `test_full_export`, `test_mail`, `test_hardening` pass on the services |
| P2 | `programs/agm` (move existing modules, add roster eligibility, reports) | P1 | `test_agm*`, `test_portal` AGM cases pass |
| P3 | `programs/senate` (verification, seating, results) | P1 | Senate and seating tests pass |
| P4 | `programs/sap` (intake, WFNI, batches, review, decisions, banking, EFT) | P1 | `test_sap`, `test_wfni`, `test_eft`, `test_export_banking`, `test_merge_docx`, `test_completed`, `test_stages` pass |
| P5 | Admin UI pages per program (`static/`) | P2-P4 | Role-gated pages for each program |
| P6 | Removal of batch tools and docs rewrite (05) | P2-P5 | `utsu_core`, `utsu_programs`, `utsu_mail`, `utsu_std`, CLI scripts gone; wiki rewritten |

P2, P3 and P4 are independent after P1 and can proceed in parallel.

## 5. Risks and open questions

1. **Roster source.** Without `uoft_data` the portal cannot tell "verified student". Decision needed: keep importing
   the university roster file (admin upload, replace semantics, as `--reimport_uoft_data`) or rely only on face/ID
   admin approval. This design assumes the upload is kept (01 section 2).
2. **Form-to-column parity.** The YAML forms were written for the portal; the CSV `conversion_table_*.csv` mapped the
   Google Forms. Each program doc lists the fields required; diff against the YAML before P2-P4.
3. **Historic data.** Existing SQLite databases (batches, ratings, decisions) need a one-off importer (05).
4. **Multi-worker safety.** Queue claims, decisions and check-ins use optimistic locks on a single document.
5. **Request cost.** On-demand Senate/AGM lists recompute per request; they are paginated and streamed. Measure at
   real volume before adding any rebuildable index.

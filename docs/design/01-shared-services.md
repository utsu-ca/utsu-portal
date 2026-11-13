# 01 Shared services (`utsu_portal/services`)

Program-neutral code used by Senate, SAP and AGM. Ported from `utsu_core`, `utsu_std` and `utsu_mail`.

## 1. Module map

| New module | Ported from | Public API (sketch) |
|---|---|---|
| `services/roster.py` | `utsu_core/importing_data.py`, `verification.py`, `schema/uoft_data.sql` | `import_roster(rows, replace=False) -> ImportReport`; `match(person) -> Match` |
| `services/selection.py` | `utsu_core/selection.py` | `person_key`, `submission_rank`, `select_current(rows, key, rank)` |
| `services/alerts.py` | `utsu_core/integrity.py` | `duplicate_verified(rows, cols)`, `raise_alert(kind, severity, refs)`, `list_alerts(program)` |
| `services/notices.py` | `utsu_mail/{templates,notices,campaign,recipients,transport}.py` | `render(template, ctx)`, `send_notice(program, kind, record)` |
| `services/exports.py` | `utsu_std/csv_safe.py`, DOCX helpers in `sap/program.py` | `csv_response(rows, columns)`, `docx_sheet(...)` |
| `services/text.py` | `utsu_std/utils.py`, `parsing_utils.py` | `clean`, `normalize_email`, `parse_student_number`, `parse_amount` |
| `services/spec.py` | `utsu_core/spec.py`, `pipeline.py` | `ProgramSpec` + `reconcile(spec, submissions)` used by every program |

`ProgramSpec` survives as the declarative contract (slug, form id, identity columns, key/rank, veto, annotate,
sticky). `pipeline.Run` becomes the pure `select()`: submissions + roster in, applications + alerts out, with no
SQLite, no CSV and no writes.

## 2. Roster and matching

- Roster is stored as lookup buckets (`roster_name`, `roster_last`) plus `roster_meta` (see `services/roster.py`).
- `POST /api/admin/roster` (role `admin`): upload CSV, validated **before** replacing (a bad file leaves the old
  roster). `replace` defaults to true.
- **Central account check** (`services/account_check.py`): every upload runs a whole-DB pass over all accounts and
  stores `roster_checks/{uuid}` = {rostered, reason, checked_at}. Accounts activated later are checked on approval.
  Matching is exact name (narrowed by the account's last-4 student number); fuzzy matches do not count because the
  account already carries its legal name.
- Program pages (AGM first) require `ctx.require_rostered()`: a `verified` account that is `rostered`. Programs read
  this result; they do not match names or check email domains (accounts cannot exist without an OTP-proven
  utoronto.ca address).
- Matching for non-account people (SAP applicants, Senate) stays the pure `match_people` function; verification is
  sticky only for SAP.
- Matching stays a pure function, so tests port unchanged (`test_lookup`, `test_identity`, `test_fuzzy`,
  `test_unicode`).

## 3. Selection (replaces "full export" and reconcile)

There is no reconcile job and no stored selection state. `select(spec, submissions, roster)` is a **pure function**
called inside a request (list, per-person view, notice preview). Rules carried over:

1. one binding application per person (per term for SAP): newest verified wins; a newer unverified one is
   `non_binding` + warning; losers are `superseded`;
2. an edit is a newer submission (the portal already versions submissions);
3. SAP applications carry committee work, so they are stored facts (see 03); Senate and AGM are never stored;
4. `person_lookup(spec, person)` evaluates one person cheaply (their submissions + one roster lookup) for
   per-user endpoints.

## 3a. Alerts

Computed on request, not stored: `GET /api/admin/{program}/alerts` runs `select` plus the integrity checks. Kinds:
`duplicate_verified` (ERROR), `roster_mismatch`, `overlapping_terms`, `non_binding`, `not_considered` (WARNING).
Acknowledgements (who saw what) are the only stored part, as audit events. Replaces `alerts_<slug>.csv`.

## 4. Notices

Templates under `templates/email/` stay (`*_verified`, `*_unverified`, `*_more_info`, `*_rejection`). The campaign
safety workflow (dry run, then confirm, sent-log to prevent repeats) is kept: `POST .../notices/preview` returns
the rendered list; `POST .../notices/send` needs a `preview_hash` from the preview and records each send in
`notice_log`, so a second call sends nothing twice. Transport is the existing `Mailer`/`SMTPMailer`.

## 5. Exports

All CSV output passes `csv_safe` (formula-injection neutralisation). Endpoints stream `text/csv`; every export is
audited with actor, program and row count.

## 6. Roles and permissions

Add to `ROLES`: `senate_admin`, `sap_admin`, `sap_reviewer`, `agm_admin`. A module's admin dependency is
`ctx.require_role("<program>_admin", "admin")`. SAP reviewer endpoints use `require_role("sap_reviewer")`; any
reviewer may claim from the queue, and edits are limited to their own ratings.

## 7. Testing

Existing tests move with the code. Services are tested without the HTTP layer; routers by `TestClient` (as
`tests/test_portal.py` does) with the `memory` store and `OutboxMailer`.

# 01 Shared services (`core/services`)

Program-neutral code used by Senate, SAP and AGM. Ported from `core`, `core.std` and `core.mail`.
Sections below include proposed behavior; see [README](../../README.md) for what is currently implemented.

## 1. Module map

| New module | Ported from | Public API (sketch) |
|---|---|---|
| `services/roster.py` | `core/importing_data.py`, `verification.py`, `schema/uoft_data.sql` | `import_roster(rows, replace=False) -> ImportReport`; `match(person) -> Match` |
| `services/selection.py` | `core/selection.py` | `person_key`, `submission_rank`, `select_current(rows, key, rank)` |
| `services/alerts.py` | `core/integrity.py` | `duplicate_verified(rows, cols)`, `raise_alert(kind, severity, refs)`, `list_alerts(program)` |
| `services/notices.py` | `core/mail/{templates,notices,campaign,recipients,transport}.py` | `render(template, ctx)`, `send_notice(program, kind, record)` |
| `services/exports.py` | `core/std/csv_safe.py`, DOCX helpers in `sap/program.py` | `csv_response(rows, columns)`, `docx_sheet(...)` |
| `services/text.py` | `core/std/utils.py`, `parsing_utils.py` | `clean`, `normalize_email`, `parse_student_number`, `parse_amount` |
| `services/spec.py` | `core/spec.py`, `pipeline.py` | `ProgramSpec` + `reconcile(spec, submissions)` used by every program |

`ProgramSpec` survives as the declarative contract (slug, form id, identity columns, key/rank, veto, annotate,
sticky). `pipeline.Run` becomes the pure `select()`: submissions + roster in, applications + alerts out, with no
SQLite, no CSV and no writes.

## 2. Roster and matching

- Roster is stored as lookup buckets (`roster_name`, `roster_last`) plus `roster_meta` (see `services/roster.py`).
- `POST /api/admin/roster` (role `admin`): upload CSV, validated **before** replacing (a bad file leaves the old
  roster). `replace` defaults to true.
- **Membership session** (Toronto time): Sep 1-Dec 31 Fall, Jan 1-Apr 30 Winter, May 1-Aug 31 Summer, with the calendar
  year (e.g. `Fall 2026`); not the September-August academic year. A roster upload belongs to the session it is
  imported in (`roster_meta.session`).
- **Membership is RBAC.** Two roles are derived from the roster check on every request and can never be assigned:
  `live_member` (on the roster now, whichever session it is for) and `rostered_member` (on the roster of the current
  session). Each program declares what it needs by adding its own permissions to the manifest `permissions` and mapping
  them to one of these roles in `role_permissions`: AGM RSVP, voting and discussion use `live_member`; Senate and SABP
  applications use `rostered_member` (checked when applying; `SubmissionPolicy.keep_membership` stores the membership held
  on the submission so the application stays eligible if the applicant later leaves the roster). `admin` does not
  cover these permissions: an administrator must be a member to hold them. Non-members keep a normal account and can
  join or form clubs; club ballots and boards use club membership checked live, not these roles. A plug-in can also
  declare its own derived role (`derived_roles` + `register_derived_role`), e.g. AGM `credentialed` (approved online ID
  verification, permission `utsu:agm:online:attend`).
- **Admin tools.** `GET /api/admin/members/lookup?email=|student_number=` finds an account (audited). `PUT` /
  `DELETE /api/admin/members/{uid}/honorary` grants or removes honorary membership for the current session; it feeds
  both derived roles, also through the auth service in a split deployment.
- **Club roles are scoped, never global.** A plug-in declares `scoped_roles`, held in one scope (a club ID), and
  checks `has_permission(account, PERM, scope=club_id)`; a scoped permission asked without a scope is an error, and a
  scoped role is never stored on an account or covered by `admin`. Clubs declare `club_owner`, `club_treasurer`,
  `club_executive` (any club status) and `club_officer`, `club_member` (approved clubs only). Ordinary members and
  join requests live in `club_members` / `club_requests`, apart from the officers in the club record, so members get
  no access to the ledger or receipts. The opt-in `club_governance` plug-in gives each club a scoped voting electorate
  and discussion audience (`club/<club id>`), with an optional per-club "UTSU members only" rule for its ballots.
- **Central account check** (`services/account_check.py`): every upload runs a whole-DB pass over all accounts and
  stores `roster_checks/{uuid}` = {rostered, membership_session, reason, checked_at}. Accounts activated later are checked on approval.
  Matching is exact name narrowed by the four roster-masked digits before the final student-number digit; these are
  distinct from the last four digits used for recovery. Fuzzy matches do not count because the account already
  carries its legal name. In split deployments the auth service owns the roster and computes checks for the portal.
- Program pages (AGM first) require `ctx.require_rostered()`: a `verified` account that is `rostered`. Programs read
  this result; they do not match names or check email domains. Accounts use an OTP-proven email address, which is not
  restricted to a particular domain.
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

## 3a. No alerts

There is no alerts service, endpoint or file. A problem is prevented where the data is entered, or it surfaces on
failure and the person is asked to re-enter the field (for example, student status or banking details that cannot be
verified). `duplicate_verified` remains an internal check that is logged.

## 4. Notices

Implemented: program administrators can preview and explicitly send one generic status notice at a time through the
web client. A keyed preview binds the recipient and application revision; a successful or uncertain attempt cannot
be automatically repeated for that same preview. An SMTP error or process interruption requires manual delivery
investigation. Recipient and submission data are not transactionally frozen across email delivery.

The following bulk-campaign workflow remains proposed:

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
`tests/core/test_portal.py` does) with the `memory` store and `OutboxMailer`.

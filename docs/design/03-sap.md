# 03 Student Aid Bursary (SAP/SABP) module (`utsu_portal/programs/sap`)

Ports `utsu_programs/sap` (`wfni.py`, `merge_docx.py`, `export_banking.py`, `eft.py`, the scoring and decision logic
of `program.py`). **Re-architected from batches to a review queue.** See also [00](00-overview.md) section 3
(stateless compute) for the rules this module follows.

## 1. Purpose

Verify applicants, score need (WFNI), let any authorized reviewer pull applications from a queue (or an admin
request one for a named reviewer), collect two ratings per application, record committee decisions, and produce the
private banking export and EFT file.

## 2. What changes from the batch design

| Batch tools | Queue |
|---|---|
| Applications placed in batches of 50, sheets handed out per reviewer | No batches. Each application carries its own reviewers |
| `--reviewers` / `--issue_batches` assigns work up front | Reviewer **claims** the next application, or an admin **assigns** one to a reviewer |
| Sheets (CSV/DOCX) returned and merged | Reviewer submits a rating on the application in the portal; DOCX import is optional |
| `stage` written by CLI runs | `stage` is **derived** from the stored facts whenever read |
| Record id `B012-01` | Random opaque `review_id` per application (anonymous, not positional) |

## 3. Data (facts only; nothing here is a cache)

| Collection | Key | Contents |
|---|---|---|
| `sap_applications` | typed uuid | student_uuid, terms, stream (`LC`/`AP`), requested amount, emergency, verification result and method (**sticky**, stored when verified), `review_id`, `required_reviews` (default 2), `reviews[]`, `wfni_inputs` |
| `sap_decisions` | application uuid | decision, awarded amount, by, at, history |
| `sap_banking` | application uuid | institution, transit, account numbers; **encrypted**, separate from the application |
| `sap_weights` | version | WFNI weights (replaces `data/wfni_weights.csv`), sha256, by, at |

`reviews[]` entries: `{reviewer, claimed_at, lease_until, assigned_by (null if self-claimed), level, comment,
submitted_at}`. Every write is a single optimistic-lock update of the application document (the same
`update(prev)` pattern as `modules/forms.py`), so two reviewers racing for the last slot cannot both win.

**Derived on read (never stored):** WFNI score and flags (from `wfni_inputs` + current or pinned weights), selection
status (`binding` / `superseded` / `non_binding` / `not_considered`), `stage`, merged level, suggested amount.

Stage derivation: `decided` if a decision exists; else `reconciled` if >= `required_reviews` submitted and the
levels are within 1; `discuss` if they are 2+ apart; `in_review` if any claim or rating exists; else `scored`
(verified) or empty (unverified). `COMPLETE_STAGE = decided` remains the single definition of "complete".

## 4. Queue rules (requirements from UTSU, 2026-11-13)

**Browse.** Reviewers browse a paginated list of open applications (anonymised) and can claim a specific one.
- Order: `emergency` applications first; then by application time, oldest first. Only the **hour** of the
  submission is shown (privacy); the full timestamp never leaves the server.
- Applications with fewer than `min_reviews` (default **2**, adjustable) reviews rank ahead of fully reviewed ones.
- **Sanity-check sampling:** an application that already **meets the minimum** (>= `min_reviews` submitted ratings, so it would otherwise
  not need another) is offered for one more review
  with probability `extra_review_rate` (default **1%**, adjustable in settings). The draw is deterministic per
  (application, reviewer, day) so pagination is stable and no server state is needed. Emergencies bypass sampling
  (always listed first, whatever their review count).
- **Suggested for you:** above the main list, ~5 suggestions (`suggestions`, default 5) chosen to encourage
  diversity of reviewers: applications with the fewest reviews, excluding ones this reviewer has reviewed, and
  preferring applications whose existing reviewers are least like this reviewer (fewest prior shared
  co-reviews; ties broken by the deterministic draw). Then the main list.
- **Urgency/timing:** a claim may carry `urgency` (`normal` | `soon`) and a due time; admins see these.

**Claiming.** `claim` adds `{reviewer, claimed_at, lease_until}` under an optimistic lock; a reviewer cannot hold
two reviews of one application. Claims lapse after `PORTAL_SAP_LEASE_HOURS` (default 48) unless rated (checked
at read time, no sweeper). Reviewers can release. Admins can assign a named reviewer.

**Safety.** Conflict of interest (own roster record, or a self-flagged `conflict`) blocks a claim. Reviewers never
see names, emails, student numbers or banking data. Claims, releases, ratings and edits are audited.
Ratings 0-5 (fractional via `parse_score`), editable until a decision. Ratings 2+ apart -> `discuss`; an admin
may raise `required_reviews` for that application.

## 4a. Banking details and re-verification

- Banking details are stored encrypted in `sap_banking` and are **available until an admin destroys them**,
  from the admin's per-application view (`DELETE /api/admin/sap/applications/{id}/banking`, confirm step,
  audited, irreversible; the application keeps a `banking_destroyed_at` fact). No automatic retention timer.
- **Re-verification:** a button on the application (`POST /api/admin/sap/applications/{id}/banking/reverify`)
  emails the applicant a single-use, expiring link to re-enter and confirm their banking details. The new
  details are stored as a pending version; an admin compares and accepts, which replaces the active details
  (history kept as audit events, not plaintext). Status shown on the view: `on_file`, `reverify_requested`,
  `reverify_received`, `destroyed`.

## 4b. Admin per-application view

`GET /api/admin/sap/applications/{id}` is **fully unblinded**: identity, roster match, flags, WFNI inputs and
score, all reviews with reviewer names, decision history, banking status and (for sap_admin) details, alerts,
and the actions: assign reviewer, record decision, request banking re-verification, destroy banking.

## 4c. Client modes

The portal client exposes each role's views as **modes** (a mode switcher in the header, shown only for roles the
account holds; the server enforces roles independently). SAP: **Applicant**, **Reviewer** (suggested + browse),
**Admin** (list + unblinded application view). Senate: **Public** (this year's Senate), **Admin**. AGM:
**Attendee**, **Check-in**, **Admin**. A mode is a route (`#/sap/reviewer`) plus a role check, not stored state.

## 5. Behaviour carried over unchanged

- Stream maxima, flags, WFNI (`wfni.py`), suggested amount (`fit_exponential`, `curve_amount`, `rubric_amount`,
  `suggested_amount`), `redact`, `parse_amount`, overlapping-terms alert, completed-final rule (a decided
  application makes other applications for that term `not_considered`; evaluated when read or when a new
  application is submitted).
- Decisions: optional amount (`0` allowed), re-recording replaces with a warning and keeps history, a decision before
  reconciliation is accepted with a warning, unverified applications are rejected.
- Banking export / EFT: admin-only, audited, produced on request from stored data; EFT config from settings/secrets.

## 6. Intake

On form submission the SAP module (a) validates and stores the application with `review_id`, (b) verifies against
the roster once and stores the result (sticky; a later mismatch raises `roster_mismatch` only when an admin asks for
the integrity report). No background reconcile exists. Edits are newer submissions; the older record stays and is
`superseded` when derived.

## 7. API

| Method and path | Role | Purpose |
|---|---|---|
| `POST /api/sap/queue/claim` | sap_reviewer | claim the next application |
| `GET /api/sap/queue?cursor=` | sap_reviewer | `{suggested[5], items[]}`, emergencies first, hour-only times |
| `POST /api/sap/queue/{review_id}/claim`, `/release` | sap_reviewer | request or release a specific one |
| `GET /api/sap/review/mine` | sap_reviewer | my claims and ratings |
| `PUT /api/sap/review/{review_id}` | sap_reviewer | submit or edit my rating |
| `POST /api/admin/sap/applications/{id}/assign` | sap_admin | add a named reviewer |
| `GET /api/admin/sap/applications` | sap_admin | list with derived stage, flags, merged level |
| `GET /api/admin/sap/applications/{id}` | sap_admin | full record with reviews |
| `GET/PUT /api/admin/sap/weights` | sap_admin | WFNI weights |
| `POST /api/admin/sap/decisions` | sap_admin | record decisions (JSON or CSV) |
| `GET /api/admin/sap/review-report[.csv]` | sap_admin | merged ratings and suggested amounts (computed) |
| `GET /api/admin/sap/banking[.csv]`, `/eft` | sap_admin | banking export, EFT file |
| `POST .../applications/{id}/banking/reverify`, `DELETE .../banking` | sap_admin | request re-verification; destroy details |
| `GET /api/admin/sap/integrity` | sap_admin | alerts computed on request |
| `GET /api/me/sap` | verified | applicant status (stage hidden until decided) |

## 8. Left to implement

- [ ] `sap/models.py`: `build_application`, `parse_amount`, `sap_uuid`, `last_four`, flags; `review_id` generation.
- [ ] `sap/wfni.py` moved as is; weights versioning endpoint.
- [ ] `sap/queue.py` (browse, suggestions, sampling, urgency): eligibility, priority, claim/release/assign with optimistic lock, leases, conflict rule.
- [ ] `sap/review.py`: rating submit/edit, anonymised projection, derived stage and merged level.
- [ ] `sap/decisions.py`: stage rules, history.
- [ ] `sap/banking.py` + `eft.py`: encrypted storage, export, audit.
- [ ] Optional `merge_docx` import as a rating source.
- [ ] Form parity (banking, WFNI questions, terms) vs `conversion_table_sap.csv`; version bump.
- [ ] Client modes: Applicant, Reviewer, Admin (`static/sap.html|js`).
- [ ] Banking re-verification link flow and destroy action.
- [ ] Tests: concurrent claim (last slot goes to one reviewer), lease expiry, no duplicate reviewer, anonymity (no PII
      in any reviewer response), derived-stage table, plus the moved `test_sap`, `test_wfni`, `test_eft`,
      `test_export_banking`, `test_merge_docx`, `test_completed`, `test_stages`.

## 9. Open questions

- Should the `min_reviews` default differ for small instant-approval requests?

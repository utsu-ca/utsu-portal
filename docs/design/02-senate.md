# 02 Senate module (`utsu_portal/programs/senate`)

Ports `utsu_programs/senate` (`program.py`, `seating.py`). **Stateless: everything is computed when an authorized
user asks.** See [00](00-overview.md) section 3.

## 1. Purpose

Verify Senate applicants against the roster and assign verified applicants to Senate seats by ranked preference,
seats table and a seeded tie-break.

## 2. Stored facts vs computed views

| Kind | Item |
|---|---|
| Stored | form submissions (existing `submissions` collection); roster; `senate_seats` (the seats table, a configuration an admin uploads); `senate_published` (an explicit, immutable snapshot an admin chooses to publish: seed, inputs hash, assignments) |
| Computed per request | application records (from submissions), verification, binding/superseded selection, alerts, seating result |

No application, verification or seating cache is written. Serverless workers share nothing, so every request
rebuilds what it needs from the store.

## 3. Behaviour

1. **Applications view.** `GET /api/admin/senate/applications` loads Senate submissions, runs `build_row`, matches
   each against the roster (`services.roster.match`), applies `selection` (one binding per person, newest verified;
   verification is recomputed each time, not sticky, because nothing is stored to stick to), and returns a page.
   Pagination is by submission id so a request does bounded work.
2. **Seating.** `POST /api/admin/senate/seating {seed}` computes `assign_seats` in the request and returns the result
   plus `inputs_hash` (hash of binding applications, seats and seed). Same inputs + seed = same result. Nothing is
   stored.
3. **Publish.** `POST .../seating/publish {seed, inputs_hash}` recomputes, rejects if the hash differs from what the
   admin reviewed, and stores the immutable snapshot. This is the only write, and it is a decision record, not a
   cache. A later publish supersedes by version.
4. **Applicant status.** `GET /api/me/senate` evaluates just that person (one submission query + one roster lookup).
5. **Notices.** Preview computes recipients now; send needs the preview hash (see 01).
6. **Seats.** `PUT /api/admin/senate/seats` replaces the table, validated by `load_seats` rules.

## 3a. Public "this year's Senate" page

The latest `senate_published` snapshot is served publicly at `/senate` (mode **Public**), listing seats and
the seated members (name and seat only, no student numbers or emails; members who opted out are shown as
"seat filled"). Because seating is deterministic, the page also shows the seed and inputs hash so the result
is independently checkable. Until something is published the page says so. Cacheable by the snapshot version.

## 4. API

| Method and path | Role |
|---|---|
| `GET /api/admin/senate/applications?status=&cursor=` | senate_admin |
| `GET /api/admin/senate/alerts` (computed) | senate_admin |
| `PUT /api/admin/senate/seats` | senate_admin |
| `POST /api/admin/senate/seating` | senate_admin |
| `POST /api/admin/senate/seating/publish` | senate_admin |
| `GET /api/admin/senate/results[/{version}][.csv]` | senate_admin |
| `GET /api/senate/current` (**public**, no auth) | anyone |
| `GET /api/me/senate` | verified |

## 5. Left to implement

- [ ] `senate/models.py`: `build_row`, `read_preferences`, `application_uuid`.
- [ ] `senate/seating.py`: port with in-memory inputs; `inputs_hash`.
- [ ] `services.selection` and `roster.match` wired over submissions; pagination.
- [ ] Router, seats upload, publish snapshot.
- [ ] Form parity vs `conversion_table_senate.csv`; version bump if needed.
- [ ] Public page `static/senate-public.html|js` and `GET /api/senate/current`.
- [ ] Admin page `static/senate.html|js`.
- [ ] Tests: determinism for a fixed seed, hash mismatch on publish rejected, roles, moved seating tests.

## 6. Open questions

- Do seated members consent to being listed publicly? Proposed: a form checkbox, default to listing.
- Can an applicant hold more than one seat? (Current code: one.)
- Performance: a few thousand submissions should compute in one request; if not, add a precomputed index as an
  explicit, rebuildable optimisation, never a source of truth.

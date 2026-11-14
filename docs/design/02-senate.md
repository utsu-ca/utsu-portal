# 02 Senate module (`utsu_core/programs/senate`)

Ports `utsu_core/programs/senate` (`program.py`, `seating.py`). **Stateless: everything is computed when an authorized
user asks.** See [00](00-overview.md) section 3.

Status: the portal exposes application views using current account and roster checks, configurable seats, deterministic
seating previews and versioned published results. Form version 2 collects ranked preferences; older submissions must
be updated before they can be seated. The unified client exposes public results and basic administration for
applications, seat configuration, previews and publication. The alerts view identifies ineligible applications and
missing or invalid seating preferences; historical batch alerts and bulk notices remain unimplemented.
The latest published results can be downloaded by a Senate administrator as an audited private CSV; it includes
names even when they are not public.

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

1. **Applications view.** `GET /api/admin/senate/applications` loads the current submission for each account and
   reads its account and central roster-check status. Pagination is by submission id; the current store implementation
   loads the complete list before paging.
2. **Seating.** `POST /api/admin/senate/seating {seed}` computes `assign_seats` in the request and returns the result
   plus `inputs_hash` (hash of binding applications, seats and seed). Same inputs + seed = same result. Nothing is
   stored.
3. **Publish.** `POST .../seating/publish {seed, inputs_hash}` recomputes, rejects if the hash differs from what the
   admin reviewed, and stores the snapshot as a versioned decision record. The store cannot atomically lock all
   submissions and roster records during publication: the snapshot reflects the inputs read by that computation,
   not a transactional freeze of subsequent edits. A later publish supersedes by version.
4. **Applicant status.** `GET /api/me/senate` evaluates just that person (one submission query + one roster lookup).
5. **Notices.** Preview computes recipients now; send needs the preview hash (see 01).
6. **Seats.** `PUT /api/admin/senate/seats` replaces the table with validated constituency, division and count entries.
   The seat counts must be explicitly configured; the example CSV is not applied automatically.

## 3a. Public "this year's Senate" page

The latest `senate_published` snapshot is served publicly at `/senate` (mode **Public**), listing seats and
the seated members (name and seat only, no student numbers or emails; members who opted out are shown as
"seat filled"). Because seating is deterministic, the page also shows the seed and inputs hash so the result
is independently checkable. Until something is published the API returns null.

## 4. API

| Method and path | Role |
|---|---|
| `GET /api/admin/senate/applications?status=&cursor=` | senate_admin |
| `GET /api/admin/senate/alerts` (computed) | senate_admin |
| `PUT /api/admin/senate/seats` | senate_admin |
| `GET /api/admin/senate/seats` | senate_admin |
| `POST /api/admin/senate/seating` | senate_admin |
| `POST /api/admin/senate/seating/publish` | senate_admin |
| `GET /api/admin/senate/results[/{version}][.csv]` | senate_admin |
| `GET /api/senate/current` (**public**, no auth) | anyone |
| `GET /api/me/senate` | verified |

## 5. Left to implement

- [ ] `senate/models.py`: `build_row`, `read_preferences`, `application_uuid`.
- [x] Reuse in-memory `assign_seats` and hash the inputs.
- [ ] `services.selection` and `roster.match` wired over submissions; pagination.
- [x] Application, seat configuration, seating preview and publish routes.
- [ ] Complete form parity with the legacy application and validate migrated applications.
- [ ] Public page `static/senate-public.html|js` and `GET /api/senate/current`.
- [ ] Admin page `static/senate.html|js`.
- [ ] Tests: determinism for a fixed seed, hash mismatch on publish rejected, roles, moved seating tests.

## 6. Open questions

- Seated members must explicitly consent to be listed publicly; otherwise the API displays "seat filled".
- Can an applicant hold more than one seat? (Current code: one.)
- Performance: a few thousand submissions should compute in one request; if not, add a precomputed index as an
  explicit, rebuildable optimisation, never a source of truth.

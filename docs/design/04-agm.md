# 04 AGM module (`utsu_core/programs/agm`)

Ports `utsu_core/programs/agm/program.py` and consolidates the portal's existing AGM code (`programs/agm/module.py`,
`programs/agm/verify.py`, SSO integration in `modules/sso.py`, `static/checkin.*`). **Stateless: eligibility is
computed when it is needed.** See [00](00-overview.md) section 3.

## 1. Purpose

Register voters (in person or online), decide eligibility from the roster, issue QR invitations, verify online
attendees' identity, provide Zoom SSO to eligible voters, and record check-in.

## 2. Already implemented (keep, move)

Invitation panel, QR, Zoom join, check-in and attendance (`programs/agm/module.py`); online ID verification and 12 h grant
(`programs/agm/verify.py`); OIDC provider with eligibility callback (`modules/sso.py`); check-in screen. The routers now live
in `programs/agm/` without changing their behavior; covered by `test_agm`, `test_agm_review`, `test_agm_sso`.

## 3. Stored facts vs computed views

| Kind | Item |
|---|---|
| Stored | RSVP form submissions; roster; `agm_checkins` (time, scanner, duplicate count); `agm_grants` (existing 12 h online-verification grants) |
| Computed per request | RSVP record (`build_rsvp`), flags (`review_flags`), attendance type, binding RSVP per person, voter lists, duplicate alerts |

There is no `agm_rsvps` collection. Eligibility is the central `roster_checks` result (see 01 section 2), refreshed
on every roster upload; the AGM has no email-domain veto and no matching of its own. Every AGM endpoint requires
`require_rostered`.

## 4. Where eligibility is evaluated

- **Per person, O(1):** invitation/QR, Zoom join, the SSO authorize eligibility callback and check-in each load that
  person's newest RSVP submission and read the account's `roster_checks` result. Check-in is the authoritative moment: it evaluates
  eligibility, then writes the `agm_checkins` fact under optimistic lock.
- **Lists, on admin request:** `GET /api/admin/agm/rsvps` returns a paginated registration list.
  `GET /api/admin/agm/alerts` identifies ineligible registrations and online attendees without a current
  identity-verification grant. `GET /api/admin/agm/rsvps.csv` exports registrations to an audited, spreadsheet-safe
  private CSV. Historical batch duplicate alerts are not implemented.
- **Notices:** preview computes recipients now; send requires the preview hash.
- **Standalone verification:** `POST /api/admin/agm/verify` checks a person against the roster with no RSVP.

## 5. API additions

| Method and path | Role |
|---|---|
| `GET /api/admin/agm/rsvps[.csv]?status=&cursor=` | agm_admin |
| `GET /api/admin/agm/alerts` | agm_admin |
| `POST /api/admin/agm/verify` | agm_admin |
| `POST /api/admin/agm/notices/{preview,send}` | agm_admin |
| existing `/api/agm/*`, `/api/admin/agm/checkin`, `/attendance` | agm_admin (was admin), routes unchanged |

## 6. Left to implement

- [x] Move AGM routers into `programs/agm` and update `app.py`; assets remain in the shared static directory.
- [ ] Complete the pure RSVP model port and integrate it with portal submissions.
- [ ] `eligibility.evaluate(person_or_rsvp)` used by invitation, Zoom, SSO callback, check-in and the list.
- [ ] Voter list CSV, alerts and notices. A paginated JSON registration list and standalone roster check are available
  to `agm_admin` through the API and basic admin screen; the existing `checkin` role remains limited to check-in and attendance.
- [ ] Limit the door-staff response to name and pronouns.
- [ ] Tests: port `test_agm`; roster change flips eligibility on the next request; check-in rejects ineligible; roles.

## 7. Open questions

- Is online ID verification required in addition to roster eligibility? Proposed: both (right to vote vs. identity).
- Verify proxy holders against the roster too?

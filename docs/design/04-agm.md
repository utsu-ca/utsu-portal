# 04 AGM module (`utsu_portal/programs/agm`)

Ports `utsu_programs/agm/program.py` and consolidates the portal's existing AGM code (`modules/agm.py`,
`modules/agm_verify.py`, SSO integration in `modules/sso.py`, `static/checkin.*`). **Stateless: eligibility is
computed when it is needed.** See [00](00-overview.md) section 3.

## 1. Purpose

Register voters (in person or online), decide eligibility from the roster, issue QR invitations, verify online
attendees' identity, provide Zoom SSO to eligible voters, and record check-in.

## 2. Already implemented (keep, move)

Invitation panel, QR, Zoom join, check-in and attendance (`modules/agm.py`); online ID verification and 12 h grant
(`modules/agm_verify.py`); OIDC provider with eligibility callback (`modules/sso.py`); check-in screen. They move
into `programs/agm/` unchanged; covered by `test_agm`, `test_agm_review`, `test_agm_sso`.

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
- **Lists, on admin request:** `GET /api/admin/agm/rsvps[.csv]` streams the voter list (verified, unverified, needs
  review) with a cursor; `GET /api/admin/agm/alerts` computes duplicates.
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

- [ ] Create `programs/agm`; move modules and check-in assets; update imports and `app.py`.
- [ ] Port `build_rsvp`, `agm_uuid`, `review_flags`, `annotate`, `is_in_person`, `attendance`, `real_need`.
- [ ] `eligibility.evaluate(person_or_rsvp)` used by invitation, Zoom, SSO callback, check-in and the list.
- [ ] Voter list, alerts, notices, standalone verify.
- [ ] `agm_admin` role for door staff (check-in only; name and pronouns only).
- [ ] Tests: port `test_agm`; roster change flips eligibility on the next request; check-in rejects ineligible; roles.

## 7. Open questions

- Is online ID verification required in addition to roster eligibility? Proposed: both (right to vote vs. identity).
- Verify proxy holders against the roster too?

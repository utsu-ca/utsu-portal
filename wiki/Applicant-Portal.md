# Applicant portal (`utsu_core`)

FastAPI, database-agnostic form application. Install with `pip install -e .` and run:

```
PORTAL_SECRET=<long random, stable> PORTAL_ADMIN_EMAILS=alice@example.com uvicorn --factory utsu_core:create_app
```

`PORTAL_STORE` selects the backend: `memory` (default), `sqlite:<path>` or `firestore` (`.[firestore]`). Add a backend by
implementing `utsu_core.store.Store` (get/put/delete/list/create). `PORTAL_SECRET` peppers the username and student-id
hashes, so it must stay stable.

## Identity and accounts
- `uuid` = HMAC(secret, username) (v5-style); the full student number becomes a secondary uuid, stored only as a
  uniqueness index (`student_ids`). The **user record** holds only `uuid, email, preferred_name, first_name, last_name,
  student_number` (last 4 digits). Account status/role live in a separate `accounts` collection. The four digits
  before the final digit used by the masked roster are stored separately and encrypted. Existing accounts must
  confirm the original number on the Account screen; matching is denied until they do so. Numbers shorter than
  nine digits cannot be matched to this roster format and require administrator assistance.
- Passwordless: username, then an emailed 6-digit OTP (hashed, expiring, attempt-limited) gives a signed bearer token.
- Activation: email OTP, then a live face + ID capture (browser camera, server-issued challenge phrase), current
  division codes and legal names, then **manual admin approval**. Only `verified` accounts can submit forms. Images are
  deleted as soon as a decision is made. Accounts whose OTP-verified email is listed in `PORTAL_ADMIN_EMAILS` are admins and skip review (usernames are never trusted). Re-sent OTPs have a cooldown, a per-window cap and a carried-over guess budget; recovery endpoints are limited per username and answer uniformly. Form `pattern`s are restricted to a backtracking-safe regex subset.
- Verified applicants can add or replace a separate **backup email** on the Account screen. The portal verifies both
  the primary and proposed backup inboxes before saving it. When locked out, use **Account recovery** with your
  username and backup address to receive a one-time sign-in code. This does not change the primary address or grant
  staff permissions. Google Workspace and accounts with staff or administrative roles cannot use backup sign-in.
- The legacy username + last four student digits option still sends a code to the primary address. A primary email
  change requires a code sent to the new address plus an administrator-approved face and ID request; legal-name
  changes also require review. The portal ID stays the same when either email address changes.
- Note: a camera capture plus challenge phrase is not cryptographic proof of liveness; admins make the final decision.
- In a split deployment the authentication service owns the roster. Program eligibility and standalone AGM roster
  checks query its service-token-protected internal API; roster details are not copied into the portal store.
  The ingress routes roster upload and student-number confirmation to the authentication service. If the service
  cannot be reached, roster-dependent operations fail closed. Roster imports and individual reads are not a
  cross-document transaction, so administrators should avoid running seating or reviews during roster replacement.
- Administrators upload roster CSVs in **Admin: roster**. Replacement is opt-in and requires confirmation.

## Forms
YAML definitions (see `src/utsu_core/forms/` for the Senate, SABP and AGM examples) are validated strictly (unknown
keys rejected, `show_if` conditionals, types `text textarea email number date select radio multiselect checkbox info`).
Each definition has an integer `version` and a content hash. Published versions are immutable: saving or publishing a
changed definition under an already-published version is a `409` ("bump the version"). Bundled forms are checked the
same way at startup. Submissions record the version and hash and are rejected if stale.

- Public `GET /api/forms/{id}/static` returns the compiled JSON (ETag = hash); the browser rebuilds, renders and
  validates the form itself (`web/src/lib/forms.js`) and only posts the data. The server re-validates and sanitises
  (NFC, control/bidi characters stripped, length caps, unknown fields rejected).
- Admin: draft/publish/unpublish, YAML editor with live preview and a field builder (`/api/admin/forms/convert`
  converts YAML and schema JSON), optimistic `base_rev` concurrency, and realtime draft sync over SSE
  (`/api/admin/forms/{id}/events`, an in-process bus; replace `EventBus` for multi-worker deployments).
- Applicants see a status page (`/api/me/submissions`); admins set statuses (`submitted`, `under_review`,
  `needs_info`, `accepted`, `rejected`).

## Extending
A module is an object with `name` and `router(ctx)`; pass it via `create_app(extra_modules=(...))`. `ctx` provides
the store, settings, mailer (`utsu_core.mailer.Mailer`, override `send` for real mail), event bus and auth
dependencies (`require_user`, `require_verified`, `require_admin`).

An optional `configure(ctx)` hook runs for every module before any router is assembled. Register shared services
and program-owned form policies there; router order must not decide whether a dependency is available. The shared
bootstrap is separate from the portal, auth and optional mail composition roots.

Use `services.submissions.register_submission_policy` with a `SubmissionPolicy` for program-specific submission
rules. AGM and Senate register roster requirements; SABP owns its application rollover and review-lock checks.
`save(previous, candidate)` and `validate_status_change(current)` run inside the atomic submission update and must
be side-effect free because optimistic retries may call them again. Register each form's policy only once.

## Clubs (`programs/clubs/module.py`)
- **Registration:** a verified user registers a club (`POST /api/clubs`, unique name) and becomes its owner, who can add
  `treasurer`/`executive` members. Admins approve via `POST /api/admin/clubs/{id}/decision`; it needs
  `club_registration_approvals` (default 2) **distinct** admins, and any rejection is final. Admins who belong to a club
  cannot approve its items.
- **Finances:** an integer-cents ledger. Admins add grants; owners/treasurers record income (auto-approved) and expenses.
  Expenses need at least one receipt and admin approval: 1 approver, or 2 at/above `expense_large_cents` (default $500).
  An expense cannot be approved beyond the club balance.
- **Receipts:** base64 jpeg/png/webp/pdf uploads (size-capped), de-duplicated per club by SHA-256, each usable once.
- **Tax:** each entry stores net/tax/gross using a rate in basis points (default 1300 = Ontario HST; 0 for exempt) and
  inclusive/exclusive entry. `GET /api/clubs/{id}/summary` reports balance, tax paid (input tax credit eligible), tax
  collected, net tax position and spend by category, counting approved entries only.
- The approval mechanism is the reusable `record_decision` helper (N distinct approvers, no self-approval).

## AGM: SSO provider, online ID verifier, invitation panel and check-in
- **SSO provider** (`modules/sso.py`, part of the core): OpenID Connect authorization-code flow with RS256 ID tokens
  (`/sso/.well-known/openid-configuration`, `/sso/jwks`, `/sso/authorize`, `/sso/token`, `/sso/userinfo`), optional PKCE,
  single-use 60 s codes, exact redirect-URI matching and pairwise subjects. Only name and email are released. Modules
  register relying parties via `ctx.services["sso"].register_client(...)` with an eligibility callback. Set
  `PORTAL_SSO_KEY` (PEM) so keys survive restarts, and `PORTAL_BASE_URL` as the issuer.
- **Online ID verifier** (`programs/agm/verify.py`, separate from account activation): an `agm_rsvp` registrant who chose
  *Online* submits a fresh live face and student ID photo. An admin or staff member with `agm_id_reviewer` opens
  **Admin mode → AGM → Online ID review queue** and compares the photos. They can approve Zoom sign-in or ask the
  attendee to resubmit with a reason. The queue is ordered by submission time, and the attendee page shows their
  position and checks again after the server's `wait_after` interval (8–60 seconds based on position and queue size).
  Approval unlocks Zoom SSO for 12 hours by default. Photos are purged after the decision. Automatic approval is disabled.
- **Invitation panel** (`programs/agm/module.py`, "AGM" in the portal): in-person registrants see a QR code of their
  registration UUID; online registrants verify, then "Log in to Zoom" returns the Zoom link and SSO start URL
  (`PORTAL_ZOOM_URL`, `PORTAL_ZOOM_SSO_URL`, `PORTAL_ZOOM_CLIENT_SECRET`, `PORTAL_ZOOM_REDIRECT_URIS`).
- **Check-in screen** (`/static/checkin.html`, admins only): scan the QR (camera where `BarcodeDetector` exists, or a
  keyboard-wedge scanner / paste) to show the attendee's name, pronouns (an optional `pronouns` field on the RSVP form,
  v2, since the user record does not store them) and a "Checked in" message; repeat scans are flagged as duplicates.

## Form patterns (RE2) and styling (Tailwind)

- A field's `pattern` is compiled with **RE2** (`google-re2`, linear time, no backreferences/lookarounds) when a form is saved and when a submission is checked. Because the browser re-runs the pattern with JavaScript's backtracking `RegExp`, patterns must also be JS-safe: only plain `(?:` groups (no `(?P<n>)`, `(?i)` flags), **no quantified groups** (`(a|b)+`, `(ab)*`), repeat counts ≤ 256, ≤ 200 characters. Use character classes and bounded repeats instead, e.g. `[A-Z]{2}\d{4}`. Existing forms with non-conforming patterns are rejected on their next save.
- **Layout.** The main sidebar is fixed to the right (hamburger collapses it to a rail on desktop; on mobile the top-right button opens it full screen). Each program has a **public page** (overview, eligibility, how to apply, key dates; the Senate page also shows published results). Signing in adds buttons on that page and under it in the menu for what the member can do.
- **Edit public pages.** Portal administrators can open **Admin mode → Administration → Page content**, select Home or an existing program page, edit the title, blurb and sections, then preview and publish. Use one paragraph or list item per line. Program titles and blurbs also update the home cards. Changes are audited and appear when visitors next load the portal; no rebuild is needed after the editor is deployed. If someone publishes first, keep a copy of your draft text and reload before reapplying your edits. Existing copy is the default until edited. This editor changes plain text, not Svelte templates, forms, eligibility rules, or the Git repository's wiki files.
- **Icons and the section sidecar.** Each main category has an icon (Noto Emoji, monochrome so it takes the menu colour; a 4 KB subset of the font in `web/src/assets`, SIL OFL, built into the static files because the CSP allows only same-origin fonts). When the sidebar is collapsed to a rail only the icons show, the category being read is highlighted, and choosing a group icon opens the menu. On screens at least 80rem wide a second sidecar to the right of the content, "In this section", lists the parts of the current program page and the other pages and actions of its section.
- **Admin mode.** Staff (anyone with a role) get an "Enter admin mode" button at the bottom of the sidebar. Only in that mode do staff tools appear (SABP review and admin, finance portal, Senate seating, club reviews, form editor, identity checks, roles, roster), and the page switches to the admin colours. It is a client-side convenience: the server still checks the role on every request.
- **Branding and theme.** `theme.json` and the logos live in `src/utsu_core/branding/` and are served at `/branding`, uncached, so edits show on the next page load without rebuilding. To use your own, point `PORTAL_BRANDING_DIR` at a folder with the same layout. `theme.json` sets `name`, `logo`, `favicon`, `colors.user` (`accent`, `accentHover`, `chrome`, `page`), `colors.admin` (`accent`, `accentHover`, `chrome`) and `copyright` (`holder`, `startYear`). Colours must be hex values and files bare names; anything else is ignored. Every page ends with a copyright line built from it.
- **Loading.** Every view is a separate lazily loaded chunk with a hashed file name, so a program's code is downloaded when it is first opened and cached independently of the others.
- **Feature ownership.** Program views, components, state and tests live under `web/src/features/{agm,clubs,sabp,senate}/`.
  Authentication/session, navigation, layout, branding and generic form rendering remain shared. SABP owns banking
  status and loads it independently rather than delaying session startup; emailed re-verification links and the
  action-needed prompt retain their existing workflow.
- The front end is a Svelte 5 app in `web/` (Vite, Tailwind CSS v4, Vitest). It is built into `src/utsu_core/static` and the build is **committed**, so `pip install` and the Docker image need no Node. Pages: `index.html` (the portal), `checkin.html`, `finance.html` (the financial controller's banking portal) and `sso.html`. After changing anything under `web/`, run `npm install` once, then `npm test` and `npm run build` in `web/`, and commit the result. The CSP forbids CDNs, inline scripts and inline styles; the build emits only same-origin module scripts and a stylesheet. Components: `.card`, `button.primary|secondary|danger`, `.err`, `.ok`, `.badge`, `.row`, `.banner-ok|warn` (check-in result), `.banner-action` (action needed), `.banner-admin`.
- The front end stays same-origin with the API, which keeps the token/CSP/SSO model simple.

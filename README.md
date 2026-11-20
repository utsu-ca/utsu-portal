# UTSU core

`utsu_core` is the backend and web client for UTSU applications, including AGM registration, Senate applications and
the Student Aid Bursary Program (SABP). The former `utsu_portal`, `utsu_programs`, `utsu_std` and `utsu_mail`
packages have moved under `utsu_core`. Update external imports accordingly.

**Migration is in progress.** The web workflows do not yet replace all historical processing. In particular,
SABP committee review is experimental and disabled by default; bulk historical application migration and payment
reconciliation/EFT export are not implemented. Do not rely on the current system for a production aid cycle.
The old program and mail command-line interfaces have been removed. The [wiki](wiki/Home.md) contains historical
batch documentation and may describe commands and workflows that no longer exist.

## Local development

Follow the [Docker development guide](docs/development/docker.md) to start the portal, Svelte hot reload,
PostgreSQL, Redis, and observability together. This is the supported dev server; the standalone persistent dev
launcher is retired. Python 3.12 or newer is required for Python tests outside Docker.

Registration and sign-in use email codes. Configure `PORTAL_SMTP_URL` as an `smtp://` or `smtps://` URL and
`PORTAL_MAIL_FROM` as the sender to deliver mail; without SMTP, the default mailer logs only recipient and subject,
not the code. For local testing, `testing/mail_sink.py` catches mail without sending it (see below). An
administrator account is created only after the email listed in `PORTAL_ADMIN_EMAILS` is verified.
Secrets can also be supplied via the corresponding `_FILE` environment variable.
Registration also requires `PORTAL_TURNSTILE_SITE_KEY` and `PORTAL_TURNSTILE_SECRET_KEY` for Cloudflare Turnstile,
unless the explicitly enabled local-development admin is used without keys. Set `PORTAL_BASE_URL` to the hostname
registered for the Turnstile widget.

## Current workflows

- **AGM:** verified, rostered accounts can RSVP, access invitations and check in; online participation requires
  separate identity verification. AGM administrators can view registrations and eligibility,
  download a private registration CSV including accessibility and proxy details, and check a person against
  the roster.
- **Senate:** administrators configure seats, inspect applications, run seeded previews and publish the reviewed
  result. Applicants must provide all seating preferences, including explicit "Not interested" choices. Public
  results show names only when the applicant consented. Administrators can see eligibility and
  download the latest published results (including non-public names) as a private CSV.
  Seat configuration is not populated automatically.
- **SABP:** applicants can submit applications and encrypted banking details. Administrators can configure WFNI
  weights, view and export current applications, and manage banking details, including re-verification. The
  **finance portal** (`/static/finance.html`, role `sap_finance`) is a separate, banking-only surface for the
  financial controller: it lists banking records and, when details fail validation, one click ("Banking failed
  validation") emails the applicant a single-use link to the banking information verification page, where they
  re-enter their details and upload a PDF void cheque (3 MB maximum, checked server-side, encrypted at rest). The
  controller reviews the new details and the cheque (every view is audited) and accepts them. The private
  CSV excludes hardship statements and banking details; it is not a payment export. The review queue requires
  `PORTAL_SAP_REVIEW_ENABLED=1`; it is experimental and not a complete committee process. Administrators must
  review and confirm redacted hardship statements. Client-side static redaction and server identifier checks cannot
  guarantee anonymity. Selected living-costs emergency subsidies are prioritized; missing subsidy selections have
  unknown emergency status and need manual follow-up. Disagreements can be resolved with a recorded reason by an
  administrator who is neither the applicant nor a reviewer; this locks the ratings. New review records are encrypted
  at rest; existing plaintext review records are encrypted on their next update. Destroying active banking data does
  not erase backups or database history. A decided application is retained when a new application for disjoint terms
  is submitted; terms covered by a decision cannot be reused. Administrators can manually import a verified
  completed historical decision, terms and ratings into an existing portal application after checking the account
  and source. Overlaps are refused and source references are deduplicated within that account only. Dates are stored
  at midnight UTC because the historical time is unknown; this is not a bulk database import or payment record. Only one application can
  be in progress at a time, and banking information is shared by the account rather than versioned per application.
- **Discussions and voting (opt-in):** enable with `PORTAL_ENABLED_PLUGINS` (they are not in the default profile).
  Members on the roster read and start topics on discussion boards, with live replies. Elections officials run
  yes/no motions, single-choice, approval, ranked-choice and multi-seat STV ballots. Each ballot is sealed,
  recorded (open) or secret. Voters get a receipt they can check against the published, optionally anchored
  record. See [Discussions and Voting](wiki/Discussions-and-Voting.md).

Roster matching uses the four digits immediately before the final digit, as in the historical masked roster.
The account's last four digits remain separate for recovery. Existing accounts must confirm their registered
student number on the Account screen before they can be roster-matched; the matching digits are encrypted at rest.
Accounts whose numbers are shorter than nine digits cannot currently be matched to this roster format and need
administrator assistance. Historical records still cannot be imported automatically.

In the split-auth deployment, roster upload and student-number confirmation are handled by the authentication service;
the portal queries it for current roster checks and fails closed if it is unavailable. Administrators can upload a
roster from **Admin: roster**. Avoid roster replacement while running seating or reviews: roster updates and program
decisions are not cross-service transactions.

Program administrators can preview and explicitly send a generic status notice to an applicant's verified email.
The notice does not identify the program or contain decision or financial details. A reviewed notice is sent at most once for the same
application revision; failed or interrupted delivery remains unresolved and must be investigated before any new
notice. SMTP must be configured. This is individual messaging, not the historical bulk campaign workflow.

## Code and development

- `src/utsu_core/app.py` assembles the portal; `bootstrap.py` constructs shared application infrastructure without
  importing program routers. A module's optional `configure(ctx)` runs before any module's routers are assembled.
- `src/utsu_core/programs/` contains the AGM, Senate, SABP and clubs modules, plus the opt-in `discussion` and
  `voting` typed plug-ins. Historical CSV/SQLite processors remain
  available for compatibility but are not loaded by live application assembly.
- `src/utsu_core/modules/` and `src/utsu_core/services/` contain shared web modules and services.
- `src/utsu_core/programs/{agm,senate,sap}/forms/` contains owner-contributed form definitions;
  `src/utsu_core/static/` contains the built web client.
- `src/utsu_core/mail/transport.py` owns SMTP delivery for both historical tooling and the live `Mailer` interface.
  Bundled reply-based correspondence lives in `src/utsu_core/mail/templates/historical/` and loads from installed
  package resources, not the working directory. Live generic notices keep their separate content and confirmation flow.
- `web/src/features/` groups AGM, clubs, discussion, SABP, Senate and voting code and tests. Session, layout,
  navigation, generic forms and the live thread panel remain shared; banking status belongs to SABP and does not block
  general session startup.
- `testing/generate_fake_*.py` generates synthetic historical-form and roster data for testing. Run
  `python testing/generate_fake_data.py` or use `--help` for generator options. Generated exports are not direct
  portal-import files; some required portal fields have no historical equivalent.
- `testing/mail_sink.py` is a local mail catcher (standard library only). The Docker dev stack starts it
  automatically. When running it separately with `python testing/mail_sink.py`, point SMTP at `smtp://127.0.0.1:1025` and read sign-in codes and notices at
  `http://127.0.0.1:8025` (JSON at `/messages?to=ADDR`). `--reject REGEX` bounces matching recipients with 550 to
  exercise failure paths; `--help` lists the other options.
- `PORTAL_DEV_ADMIN=1` seeds a verified account for each enabled role at `<role>@dev-local.test` (for example,
  `admin@dev-local.test` or `forms_editor@dev-local.test`). It also pre-registers three verified applicants with no
  staff roles: `applicant+1@dev-local.test`, `applicant+2@dev-local.test`, and `applicant+3@dev-local.test`.
  The legacy `devadmin@dev-local.test` login still works. Sign-in codes go to the mail sink if `PORTAL_SMTP_URL` is set,
  otherwise the whole message is printed to the server log. The server refuses to start with it unless
  `PORTAL_BASE_URL` is a localhost address, and refuses it with the Firestore store or split auth. While the
  development marker exists in the store, every page turns red and the service is renamed "ACTIVE DEV LOCAL, DO NOT USE",
  even after the setting is removed, so a copied development database is easy to spot.
- [Architecture implementation plan](docs/design/06-architecture-implementation.md) tracks the approved
  reorganization, acceptance criteria, verification, and remaining work. Earlier
  [design documents](docs/design/00-overview.md) are historical proposals, not the current implementation.

Run tests with `python -m pytest` (the `pythonpath` and test folder are set in `pyproject.toml`). Some integration
tests additionally require Node.js. Install only the extras needed for the deployment; see `pyproject.toml` for
database, authentication and observability extras.

The generated-user integration test creates synthetic accounts in an isolated in-memory app, verifies them, and
exercises AGM registration, pagination, check-in and export. It uses 24 users by default; for a larger repeatable run:

```sh
UTSU_FAKE_USER_COUNT=210 python -m pytest tests/test_generated_user_load.py -q --durations=1
```

The count can be set from 1 to 2000. This sequential, in-process correctness test disables authentication
rate-limiting and uses an in-memory mailer; it is not a concurrency, network, production-database, or capacity
benchmark. It never writes users to a deployment or sends email. Other generated historical-form tests cover
selected Senate and SABP processing; the generated CSVs are not general-purpose portal imports.

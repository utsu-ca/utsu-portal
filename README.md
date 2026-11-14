# UTSU core

`utsu_core` is the backend and web client for UTSU applications, including AGM registration, Senate applications and
the Student Aid Bursary Program (SABP). The former `utsu_portal`, `utsu_programs`, `utsu_std` and `utsu_mail`
packages have moved under `utsu_core`. Update external imports accordingly.

**Migration is in progress.** The web workflows do not yet replace all historical processing. In particular,
SABP committee review is experimental and disabled by default; bulk historical application migration and payment
reconciliation/EFT export are not implemented. Do not rely on the current system for a production aid cycle.
The old program and mail command-line interfaces have been removed. The [wiki](wiki/Home.md) contains historical
batch documentation and may describe commands and workflows that no longer exist.

## Local setup

Python 3.12 or newer is required. From the project root:

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export PORTAL_SECRET="$(openssl rand -hex 32)"
export PORTAL_STORE="sqlite:portal.sqlite"
export PORTAL_ADMIN_EMAILS="admin@example.com"
export PORTAL_HSTS=0
uvicorn utsu_core:create_app --factory --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000`. Keep `PORTAL_SECRET` stable across restarts: it is used for identity hashes and,
unless `PORTAL_DATA_KEY` is configured, derives the encryption key. The default store is in-memory and loses data
when the process stops. The example disables HSTS only for local HTTP; production requires HTTPS and appropriate
secrets, backups, database and deployment configuration.

Registration and sign-in use email codes. Configure `PORTAL_SMTP_URL` as an `smtp://` or `smtps://` URL and
`PORTAL_MAIL_FROM` as the sender to deliver mail; without SMTP, the default mailer logs only recipient and subject,
not the code. An administrator account is created only after the email listed in `PORTAL_ADMIN_EMAILS` is verified.
Secrets can also be supplied via the corresponding `_FILE` environment variable.

## Current workflows

- **AGM:** verified, rostered accounts can RSVP, access invitations and check in; online participation requires
  separate identity verification. AGM administrators can view registrations, eligibility and online-verification
  alerts, download a private registration CSV including accessibility and proxy details, and check a person against
  the roster. Historical batch alerts are not implemented.
- **Senate:** administrators configure seats, inspect applications, run seeded previews and publish the reviewed
  result. Applicants must provide all seating preferences, including explicit "Not interested" choices. Public
  results show names only when the applicant consented. Administrators can see eligibility and preference alerts
  and download the latest published results (including non-public names) as a private CSV.
  Seat configuration is not populated automatically; historical batch alerts are not implemented.
- **SABP:** applicants can submit applications and encrypted banking details. Administrators can configure WFNI
  weights, view and export current applications, and manage banking details, including re-verification. The private
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

- `src/utsu_core/programs/` contains the AGM, Senate and SABP modules and retained processing logic.
- `src/utsu_core/modules/` and `src/utsu_core/services/` contain shared web modules and services.
- `src/utsu_core/forms/` contains bundled form definitions; `src/utsu_core/static/` contains the web client.
- `testing/generate_fake_*.py` generates synthetic historical-form and roster data for testing. Run
  `python testing/generate_fake_data.py` or use `--help` for generator options. Generated exports are not direct
  portal-import files; some required portal fields have no historical equivalent.
- [Design documents](docs/design/00-overview.md) describe the target architecture and outstanding work; proposed
  functionality should not be read as implemented.

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

# UTSU core

The unified UTSU web application and its program modules live under `utsu_core`. See the repository
[README](../README.md) for setup, current functionality and known limitations, and
[Applicant Portal](Applicant-Portal.md) for portal architecture.

**Migration is not complete.** The former program, mail and export command-line interfaces have been removed.
Other pages in this wiki document historical batch processing and may reference commands that no longer exist;
they are not production operating instructions.

Historical references:

- [Senate and Seating](Senate-and-Seating.md)
- [Student Aid Bursary Program](Student-Aid-Bursary-Program.md)
- [AGM RSVPs](AGM-RSVPs.md)
- [Identity and Verification](Identity-and-Verification.md)
- [Full Exports and Integrity](Full-Exports-and-Integrity.md)

The [design documents](../docs/design/00-overview.md) describe the proposed migration and outstanding work.
The synthetic data generators remain in `testing/` for testing; their CSVs are not portal import files.

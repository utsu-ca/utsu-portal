# Operations Runbook

> Historical reference only. Do not follow this runbook for the web application: the batch commands have been removed.
> See [README](../README.md) for current capabilities and limitations.

A checklist for running a real cycle. Replace paths as needed; all commands run from any directory once the package is
[installed editable](Getting-Started#install).

## 0. Before anything

- [ ] Work only on a trusted machine. Everything personal stays under `secrets/` (gitignored).
- [ ] `data/seats_senate.csv` has the **real** seat counts (the shipped file is a placeholder).
- [ ] Form column headings still match `data/conversion_table_*.csv`.
- [ ] `data/wfni_weights.csv` matches the current rubric (SABP).
- [ ] Roster CSV validated: it has `First Name`, `Last Name`, `Truncated Student Number`, `Faculty`, `Division`, and no
      identical duplicate rows (see the caution in [Input and Output Files](Input-and-Output-Files#roster-import-behaviour)).

## 1. Import the roster and verify applications


Then read the log and these files in `secrets/output/`:

- [ ] `*_run.json`: do the counts look right (rows read, verified, unverified)? Check the log for `INTEGRITY` errors.
- [ ] `unverified.csv` / `sap_unverified.csv` / `agm_unverified.csv`: why (`reason` column)? A student corrects a typo by
      editing or re-submitting the form (Google Forms gives it a new timestamp), not in the program.

## 2. Update as responses arrive

Export the **full** form again and re-run the same processing. Every application in
the export is verified again and the newest verified one per student is binding. See
[Full Exports and Integrity](Full-Exports-and-Integrity).

If the roster itself changed: reimport it. A student who was verified stays verified (the mismatch is
logged and counted in `kept_verified_despite_roster`), and unverified ones can now become verified.

## 3. Handle problems

| Situation | Do this |
|---|---|
| Student replied to a "more information" email | They edit or re-submit the form; export the full form again and re-run |
| `kept_verified_despite_roster` > 0 | A verified application no longer matches the roster. Check the roster change was intended |
| `missing_from_export` > 0 (SABP) | A verified application's row left the export (deleted, or edited into a new row). It is kept; check it |
| `non_binding` count | An unverified submission used a verified student's email. Check it is not impersonation |
| `INTEGRITY` in the log | Two verified applications for one student (per term): investigate in the database |
| Wrong run | Restore the matching file from `<db folder>/backups/` |

## 4. Senate

- [ ] After the final run, `seats.csv`, `unseated.csv`, `seating_run.json` (note the **seed**) are produced.
- [ ] Review `Unfilled seat(s)` and `Delegate ... not drawn` warnings.
- [ ] Email results with `senate_verified` / `senate_rejection` ([Emailing](Emailing)).

## 5. SABP

1. Run with reviewers (or run without and hand the sheets out yourself, then record the batches as issued).
2. Send each reviewer **only** their folder `sap_batches/by_reviewer/<name>/`. They fill in `need_level` (0-5) and `notes`
   in the `.csv` and return it to the same place.
3. Merging reviews, then resolve `discuss` and `incomplete` rows in
   `sap_merged_ratings.csv`.
4. Add `decision` (and `awarded_amount`) to that file or a new one, then
   recording decisions from a decisions CSV.
5. Decided applications are complete and final for their term.

## 6. AGM

1. Run with the AGM form export. Hand out `agm_list_all.csv` (or `agm_list_in_person.csv`) to the door team and
   `agm_accommodations.csv` (no names) to the organizers.
2. Check `agm_unverified.csv` for RSVPs whose `reason` says a name is shared by more than one student, and fix those rows
   with a student number or division.

## 7. Emails

Always preview first, then send. See [Emailing](Emailing#safety-workflow).


## 8. After the cycle

- [ ] Archive the database, backups and `secrets/output/` per your retention policy, then delete working copies.
- [ ] Do not commit anything under `secrets/`.

## Quick troubleshooting

| Symptom | Likely cause |
|---|---|
| `Missing required names in columns: {...}` | A form heading changed: update the left column in the lookup table |
| `The export needs at least one of these columns to identify people` | The export has no name or student-number column |
| Everything is `unverified` | The roster table is empty or the wrong file was imported; check `SELECT COUNT(*) FROM uoft_data` |
| `Seats file not found, skipping seating` | seats file path wrong |
| `FileNotFoundError` for the Senate file on an SABP-only run | Run SABP processing only |
| Reviewer sheet warning "no longer matches batch" | The batch changed after issue; the reviewer's sheet is left untouched on purpose |


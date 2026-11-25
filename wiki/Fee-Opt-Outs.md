# Fee opt-outs and refunds

The core `fees` module (`src/core/modules/fees.py`) tracks optional fees that members may opt out of, and refunds of
fees already paid.

## Windows
Opt-outs open once for each session, in Toronto time:

| Window | Default | Covers |
| --- | --- | --- |
| `fall` | September | Fall-Winter session (`fall:<year>`) |
| `summer` | May | Summer session (`summer:<year>`) |

Admins change the opening month and length (in months) per window; the two windows may not overlap and each must fit
inside one calendar year.

## Roles
- `fees_admin`: manage fees, windows, expected headcounts and levy groups; also sees every levy group.
- `fees_finance`: work the refund queue and read payout banking details (each read is audited).
- `levy_group`: view the groups they are a member of (membership is set by an admin on the levy group).

## Flow
1. Admin adds fees (amount per session, refundable or not, optional levy group).
2. While a window is open a rostered member opts out at **Profile > Fee opt-outs**. Ticking "already paid" requests a
   refund, which needs banking information in the shared financial profile (`financial_profiles`).
3. Finance reviews **Fee refunds**, reads the banking details, pays outside the portal and marks the request refunded
   (or denies it with a note). Members may withdraw an opt-out until the window closes or it is decided.
4. Levy groups see opt-outs, and per fee: gross levy (headcount x amount), confirmed net, and predicted net (pending
   refunds assumed granted). Headcount is set per session by an admin, else taken from the imported roster.

Amounts are snapshotted onto each opt-out, so later price changes do not rewrite past sessions.

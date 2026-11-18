# Discussions and Voting

Discussions and voting are optional plug-ins. They are not part of the default profile. To enable them, list them in
the program allowlist together with every program you already run (core modules are always on and are not listed):

```bash
PORTAL_ENABLED_PLUGINS=agm,agm_verify,senate,sap,clubs,discussion,voting
```

`voting` requires `discussion`. The menu shows **Discussions** only when the plug-in runs and the account is
verified. **Ballots and polls** appears under it for elections officials when voting also runs.

| Role | Grants |
|---|---|
| `discussion_moderator` | create, archive and reopen boards; lock and pin topics; remove posts with a reason; read moderator-only boards |
| `elections_admin` | create, open, close and cancel ballots; view the voter roll of recorded ballots; break the seal on sealed ballots |

Moderator and official tools appear only in admin mode.

## Live chat

Topic replies use the core messaging service, which any plug-in can use for a thread attached to one of its
records.

- Messages stream live over server-sent events. The browser sends the normal `Authorization` header, never a
  token in the URL. Reconnecting reconciles stored posts, including late writes and redactions, without duplicates.
- Access is checked on every request and every streamed batch. Someone who loses access sees "You can no longer
  read this thread" in place of the form.
- Posts are append-only. Staff can remove a post; the reason goes to the audit log and the post shows as
  "Removed by a moderator".
- Core supports internal notes on thread types that enable them; they are visible only to thread staff, including
  in the stream. Discussion topics do not enable internal notes: use a moderator-only board for private discussion.
- Message bodies are encrypted at rest. The event bus (in-process, or Redis across replicas) carries only a
  thread and sequence number, never text.
- There are no direct messages, typing indicators, presence or attachments. Applicant <-> staff threads on
  program records (for example SABP applications) are planned; they need only a thread type and a panel in each
  program.

## Boards and topics

- A board is readable by members on the roster (`members`) or by moderators only (`staff`).
- Any verified, rostered member can start a topic on an open board; the opening post is the first message.
- Moderators can lock a topic (no more replies), pin it, or archive a board (read-only).
- Boards created by another plug-in through the discussion service cannot be archived from this screen.
- Someone who is not on the roster is told to check the student number on their account instead of seeing an
  empty page.

## Ballots

Methods:

| Method | Voter chooses | Result |
|---|---|---|
| Yes / no motion | yes, no or abstain | carried or defeated by a simple majority or two-thirds of yes+no votes |
| Single choice | one option | the most votes wins; ties are reported, not broken at random |
| Approval | up to N options | the most approvals win |
| Ranked choice | a ranking | instant runoff for one seat, with every round shown |
| STV | a ranking | multi-seat single transferable vote (Droop quota, exact fractional transfers), with every round shown |

An optional quorum (a minimum number of votes) applies to every method. A ballot moves from draft to open to
closed (or cancelled). Opening and closing happen at the scheduled times or when an official chooses. Counting
starts on the next read at least 10 seconds after closing, allowing in-flight writes to finish. Use **Refresh ballot**
to retrieve the latest status and count without losing a one-time receipt. A ballot
created with a board gets a discussion topic, and the ballot appears above its replies.

### Vote privacy

The official chooses the privacy level when creating a ballot (there is no default), and it cannot change
afterwards. The voting page shows voters the notice for that level before they vote.

| Level | What is stored | Who can see how someone voted |
|---|---|---|
| Sealed | the voter, in an encrypted field | nobody, until an elections official breaks the seal |
| Recorded (open) | the voter, in an encrypted field | elections officials, at any time; for polls such as dietary needs |
| Secret | no link to the voter | nobody, ever; the seal cannot be broken |

Breaking the seal on a sealed ballot requires a reason of at least 10 characters. The opening, the official's
name and the reason are audited and listed on the ballot for every voter to see. Viewing the roll of a recorded
ballot is also audited.

### Receipts and the published record

- Each voter gets a receipt: a hash of their vote with a random salt, which does not reveal the choice. For
  sealed and recorded ballots the portal keeps it and shows it again. For a secret ballot it is shown once, with a
  prompt to save it.
- When the ballot is counted, the portal publishes a record containing the Merkle root of all receipts, the vote
  count, the options and the full result, plus a digest of that record.
- **Check the record** on the ballot recomputes the root and digest in the browser and confirms the voter's
  receipt is included. This checks the published record's consistency, not the external chain independently.
  Compare the digest with the transaction calldata to verify the external anchor.
- If anchoring is configured, the digest is anchored to an external chain and the ballot shows the anchor's status
  (pending, submitted or confirmed with its block). See the anchoring settings in [Deployment](Deployment.md).

### Limits

- Votes are final.
- Receipts prove a vote was counted, not what it was; this is not an end-to-end verifiable election system.
- For secret ballots, an operator with live database access could still attempt to correlate votes with voters by
  insertion order or timing.
- In split deployments, a ballot's anchor advances when the ballot is read; a ballot nobody opens after closing
  stays pending until someone does.
- Large threads are reconciled in full on the server; this is not yet a high-volume chat service.

Other plug-ins can create and run their own ballots and register their own electorates through the provided
`VotingService` (see [plan 08](../docs/design/08-messaging-discussion-voting-plan.md)). AGM motions are a planned
integration.

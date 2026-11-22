# Live chat, discussion and voting implementation plan

Status: implemented (uncommitted). Builds on [the plug-in architecture plan](07-plugin-architecture-plan.md)
(stages 0–3 complete). This pulls the core of stage 9 (record messaging) forward because the discussion and voting
plug-ins depend on it; the remaining stage 4–8 work is unchanged. §8 records where the build differs from this plan.

User-confirmed decisions:

- Build the core live-chat engine and use it for discussion topics. Applicant <-> staff panels on program records
  (for example SABP applications) come later on the same engine.
- Support yes/no/abstain motions, single choice, approval, ranked choice (one seat) and multi-seat STV.
- Ballot setup asks how much visibility is required, fixed before the ballot opens:
  - `sealed` (the default) keeps an encrypted link from each vote to its voter. Results show totals only. Elections
    admins can "break the glass" at any time with a recorded reason, and every opening is shown on the ballot.
  - `open` is a recorded vote, for polls such as dietary needs where organizers need to know who chose what.
  - `secret` is true secrecy, an opt-in level. No voter link is stored, so nobody can unseal it.
- The voting page always describes who can see what for that ballot's visibility level.
- When a ballot closes, the portal anchors one digest to an external chain. The digest covers every vote's salted
  receipt hash and the result. Voters receive a receipt and can check that it is included.
- A native EVM (Ethereum-compatible JSON-RPC) anchor connector is added alongside the existing OpenTimestamps and
  HTTP-gateway providers.
- Any verified, rostered member may start a topic in a member board; moderators lock, pin and redact.
- Discussion and voting are opt-in: they are not part of the default full-portal profile and must be listed in
  `PORTAL_ENABLED_PLUGINS`.
- AGM motions are a later integration. This round proves cross-plug-in invocation with a test consumer.

## 0. Starting point (before this plan)

Nothing user-visible existed:

- `core.contracts` has a placeholder `MessagingService` (`list_messages`, `post_message`) and
  `RegistrationContext.register_thread_type`, but the host rejects thread registrations and the `messaging`
  capability (`CapabilityUnavailable` / "requires unavailable core services").
- No collections, routes, streams or UI for messages exist.
- The only server-sent-events (SSE) stream is the staff forms editor, which authenticates with the session token
  in the query string. Plan 07 forbids copying that to member-facing streams.
- Plan 07 §10 excluded member-to-member chat. This plan narrows that exclusion: discussion topics are records owned
  by the discussion plug-in, so their threads stay record-linked. Direct messages, presence/typing indicators and
  WebSockets remain out of scope.

## 1. Shape of the solution

```text
core:  messaging service (threads, messages, read markers, SSE)  +  provided-service host mechanism
         ^ thread type "discussion-topic"                              ^ provides "discussion" / "voting"
plug-in "discussion": boards, topics, moderation, topic-kind extension point
         ^ topic kind "voting-ballot"  (voting depends on discussion)
plug-in "voting": ballots, eligibility, secret voting, tallies; VotingService for other plug-ins
         ^ electorates + ballots created by consumer plug-ins (e.g. a future AGM motions integration)
```

- **Live chat is a core service.** Any plug-in registers a thread type with an authorizer; core owns storage,
  limits, audit, redaction and the live stream. Discussion topics are the first consumer; applicant <-> staff
  panels on program records reuse the same component later.
- **Discussion and voting are optional typed plug-ins**, the first built-in plug-ins on the typed
  (`interface: "plugin"`) runtime. They are not core: core stays limited to shared platform services.
- **Plug-ins invoke each other only through provided services.** A provider declares `provides`; a consumer lists
  the provider in `dependencies` and receives a consumer-scoped client. Consumers import only the provider's
  implementation-free `contracts` module, never its implementation.

## 2. Core messaging (live chat)

### Contracts (replace the stage-1 placeholders; nothing implemented them)

```python
@dataclass(frozen=True)
class ThreadAccess:
    read: bool            # may read participant-visible messages
    post: bool            # may post participant-visible messages
    staff: bool = False   # may read/post internal notes and redact

@dataclass(frozen=True)
class ThreadType:
    authorize: Callable[[Mapping[str, Any], str], ThreadAccess | None]   # (account, record_id)
    internal_notes: bool = False
    max_body: int = 4000

class MessagingService(Protocol):        # owner-scoped: a plug-in can only use its own thread types
    def post(self, thread_type, record_id, account, body, internal=False) -> dict: ...
    def messages(self, thread_type, record_id, account, after=0, limit=100) -> list[dict]: ...
    def redact(self, thread_type, record_id, seq, account, reason) -> dict: ...
    def mark_read(self, thread_type, record_id, account, seq) -> int: ...
```

`authorize` returning `None`, a missing/disabled owner, or an unregistered type all deny (fail closed).
Authorization is re-evaluated on every request and every streamed batch.

### Storage (additive core collections)

| Collection | Key | Contents |
|---|---|---|
| `message_threads` | `{type}:{record_id}` | owner, type, record ID, `seq` counter, created/last-activity times |
| `messages` | `{type}:{record_id}:{seq:010d}` | thread key, seq, author UUID, `internal`, `body` (encrypted), time, redaction marker |
| `message_reads` | `{type}:{record_id}:{uuid}` | highest seq read (CAS, monotonic) |

- Posting allocates `seq` with a CAS update of the thread, then creates the message. A failed create leaves a
  harmless gap; cursors are "after seq", never "count".
- `messages.body` joins the retained protection catalogue. Redaction blanks the body and records who/when; the
  reason goes to the audit log, not the message.
- Append-only: no edit or author delete. Posts and redactions are audited without message text.
- Limits: body length per type (default 4000 characters, control characters stripped), and per-account rate
  limits through the existing limiter (overall and per thread).

### API (core routes; any registered type)

| Method/path | Purpose |
|---|---|
| `GET /api/threads/{type}/{record}` | permissions, size limit, last sequence and read marker |
| `GET /api/threads/{type}/{record}/messages?after=N` | visible messages after a cursor (paginated list) |
| `POST /api/threads/{type}/{record}/messages` | post (`internal` only for staff on types with internal notes) |
| `POST /api/threads/{type}/{record}/read` | advance read marker |
| `POST /api/threads/{type}/{record}/messages/{seq}/redact` | staff redaction with reason |
| `GET /api/threads/{type}/{record}/stream?after=N` | SSE live updates |

### Live updates

- Stream authentication uses the normal `Authorization` header with fetch-based streaming. This resolves the
  stage-1 open choice: no session token ever appears in a stream URL.
- Posting publishes only `{thread, seq}` on the event bus — never message text — so Redis carries no content.
- Each connection reconciles the complete authorized backlog, including rows at or before the supplied cursor.
  A `sync` event lists visible sequence numbers; clients discard rows no longer visible and upsert message rows.
  Each hint or keepalive tick reconciles the store again and sends only new or redacted rows, plus `sync` when
  visibility changes. This recovers late lower-sequence writes and missed redaction hints. Internal notes are
  filtered at the server. Stored messages stay authoritative with either the in-process bus or Redis.
- Streams end after a bounded lifetime so the client reconnects and is re-authorized.

### Frontend

A core fetch-SSE reader (`src/core/frontend/src/lib/stream.js`) and a core `ThreadPanel.svelte`: message log
(`role="log"`), inline post errors, staff internal-note toggle and redact control, reconnect with backoff.
No toasts, pop-ups or modal alerts.

## 3. Provided services (host mechanism)

- Manifest gains `provides: [...]`; a service name has exactly one provider in a composition.
- `RegistrationContext.provide(name, factory)` — the host calls `factory(provider_services)` after the provider's
  `configure`; the result exposes `for_consumer(consumer_id)`.
- `RegistrationContext.require(name)` — available during `configure` only when the provider is a declared
  dependency; returns a consumer-scoped client so a consumer can register its contributions (for example an
  electorate) before registries freeze.
- `PluginServices.provided` — the same consumer-scoped clients at request time.
- Provided objects may implement `freeze()`; the host calls it with the core registries.
- Disabling a provider is a dependency error for its consumers, not a silent no-op.

## 4. Discussion plug-in (`discussion`)

Manifest: typed plug-in; services `auth`, `audit`, `store`, `messaging`, `now`; collections `discussion_boards`,
`discussion_topics`; role `discussion_moderator`; provides `discussion`; thread type `discussion-topic`.

| Concept | Behavior |
|---|---|
| Board | Slug ID, title, description, audience `members` (verified and rostered) or `staff` (moderators); created/archived by moderators. |
| Topic | Random ID, board, title, kind (`discussion` or an extension kind), author, pinned/locked, optional extension reference. The opening post is message 1 of the topic's thread. |
| Replies | Core messages in thread type `discussion-topic`; live updates come from the core stream. |
| Moderation | Moderators (and `admin`) are thread staff: lock/pin topics and redact posts, all audited. Locked topics refuse member posts. |

API: `GET/POST /api/discussion/boards`, `GET/POST /api/discussion/boards/{board}/topics`,
`GET /api/discussion/topics/{topic}`, `POST /api/discussion/topics/{topic}/moderate`.

`DiscussionService` (provided): configure-time `register_topic_kind(kind)`; runtime `ensure_board(...)`,
`create_topic(...)`, `topic(...)`, `set_locked(...)`. Topic kinds are namespaced to the registering consumer. A
topic whose kind owner is disabled still reads as a plain discussion; its extension panel is not offered.

UI: board list, board topic list with a new-topic form, topic view with the core thread panel and a moderation bar.
Navigation adds a "Discussions" section (Noto icon) for verified accounts.

## 5. Voting plug-in (`voting`)

Manifest: typed plug-in; depends on `discussion`; services `auth`, `audit`, `store`, `integrity`, `now`, `provided`;
collections `ballots`, `ballot_receipts`, `ballot_votes`; role `elections_admin`; provides `voting`; topic kind
`voting-ballot` (panel `ballot`); protection `ballot_votes.salt`, `.selection` and `.voter`; `ballot_votes` is
declared unlinkable.

### Ballot model

- Fields: title, description, method, options, seats/max choices, threshold, optional quorum, electorate,
  status, opening/closing times, the discussion topic, owner (the creating plug-in) and owner reference.
- Lifecycle: `draft` -> `open` -> `closed` (or `cancelled`). Options, method and electorate are immutable once
  open. Opening/closing is explicit or by the stored times, evaluated on request — no background worker.
- Methods: yes/no/abstain motions (simple majority or two-thirds of yes+no, abstentions
  excluded), single choice (plurality), approval (up to N), ranked choice (instant runoff, single seat) and
  multi-seat STV (Droop quota, weighted inclusive Gregory surplus transfers in exact fractions; with one seat this
  is instant runoff).
  Ties are reported as ties for staff resolution; nothing is decided by randomness.
- Electorates: built-in `members` (verified and rostered). Other plug-ins register electorates with an
  eligibility callback. A ballot whose electorate owner is disabled refuses votes (fail closed).

### Visibility levels

| Level | Stored with the vote | Who can see who voted what | Voting-page notice |
|---|---|---|---|
| `open` | voter ID | elections admins and the owning plug-in's admins, at any time | "Recorded vote" |
| `sealed` (default) | voter ID in an encrypted field | nobody until an elections admin breaks the glass with a reason | "Sealed ballot" plus every opening |
| `secret` | nothing | nobody, ever | "Secret ballot" |

Consumers choose the level when they create a ballot, and the elections admin form asks for it. Breaking the glass
(`POST .../unseal`) needs `elections_admin` and a reason of at least 10 characters. It is audited as
`ballot.unsealed`, adds a dated entry to the ballot's public `unsealed` list, and returns the roll.

### Casting a vote

1. Check the ballot is open and the account is eligible.
2. `create` a receipt keyed by a purpose-scoped HMAC of (ballot, voter). Create-if-absent makes double voting
   impossible, including under concurrency and across replicas. The receipt holds no selection.
3. `create` the vote under a random ID with the ballot ID, the encrypted selection, a random salt, its leaf hash
   `sha256("utsu-vote-v1" | ballot | vote id | selection | salt)` and, unless the ballot is secret, the voter.
   If this write fails, the receipt is removed so the voter can retry.
4. Return the leaf hash to the voter as their receipt. It does not reveal the selection without the stored salt.

Votes are final. The DB-access audit (`PORTAL_AUDIT_DB_ACCESS=1`) would otherwise log each vote ID against the
voter, which would bypass the audited break-glass path. To prevent that, the typed store gains an *unlinkable*
collection declaration: writes to those collections record operation counts without document IDs.

Residual risk, documented: for `secret` ballots, an operator with live database access, the server secret and
write-order metadata could still attempt timing correlation. This is not an end-to-end verifiable election system.

### Results and anchoring

- Turnout is visible while open; choices are never shown before close.
- On first read after closing, votes are tallied once and stored on the ballot, and `ballot.tallied` is audited.
  The stored record has:
  - the Merkle root of the sorted leaf hashes and the vote count;
  - the full method/options/result;
  - one canonical digest over all of those.
- Ranked and STV ballots record every round.
- The digest is anchored through the core anchor service. `GET .../record` returns the leaves, root, digest and
  anchor status/reference, so anyone holding a receipt can recompute the anchored digest.
- Results are visible to anyone who can read the ballot.

## 5a. Anchoring for plug-ins and the EVM connector

- Plug-ins currently cannot anchor anything; only nightly audit checkpoints are anchored. Add a core `anchor`
  plug-in service: `submit(purpose, digest_hex, meta) -> anchor_id` and `status(anchor_id)`.
  - Anchors live in a core `anchors` collection.
  - Network providers submit on a background thread, so requests never wait on a chain.
  - A pending or submitted anchor is retried or confirmed when its status is read, at most once a minute, so the
    portal heals itself without needing the auth-service cron.
- Add a new `evm` provider (`PORTAL_ANCHOR=evm`):
  - It sends a zero-value EIP-1559 transaction from the anchoring wallet to itself. The calldata is
    `"UTSU" || digest`, so no contract is needed.
  - Configuration:
    - `PORTAL_ANCHOR_URL` is the https JSON-RPC endpoint;
    - `PORTAL_ANCHOR_EVM_KEY` is the wallet key, a secret setting (set `PORTAL_ANCHOR_EVM_KEY_FILE` instead);
    - `PORTAL_ANCHOR_CHAIN_ID` is checked against `eth_chainId`;
    - `PORTAL_ANCHOR_CONFIRMATIONS` defaults to 12.
  - `confirm()` waits for a successful receipt and the configured confirmations. `verify()` re-reads the
    transaction and checks the sender, recipient and calldata.
  - Signing comes from the optional `[evm]` extra. Nonce races between replicas fail and are retried.
- Audit maintenance also calls `confirm()` on submitted checkpoints, so EVM checkpoints reach `confirmed`.

### `VotingService` (provided)

Configure-time: `register_electorate(id, Electorate(description, is_eligible))`.
Runtime: `create_ballot(BallotSpec, actor)`, `open_ballot`, `close_ballot`, `cancel_ballot`, `ballot`,
`results`. A consumer manages only its own ballots and electorates; IDs are namespaced by consumer.

UI: ballot panel inside the topic view (vote form per method, turnout, results and rounds) and an elections
administration view (create, open, close and cancel ballots; choose board and electorate).

## 6. Tests and validation

- Messaging: unknown/disabled type and denied authorizer; internal-note isolation in list and stream; staff-only
  redaction with audit; append-only; limits; concurrent seq allocation; stream backlog and live delivery from a
  cursor; no message text on the bus.
- Provided services: undeclared dependency, duplicate provider, consumer scoping, disabled provider.
- Discussion: audience and roster checks, moderation permissions, locked topics, topic-kind fallback.
- Voting: each counting method including instant-runoff rounds and ties; thresholds and quorum; double vote under
  concurrency; ineligible voter; window enforcement; results hidden until close; receipt/vote unlinkability,
  including with DB-access audit enabled; consumer plug-in electorate and ballot scoping.
- Frontend: stream parser and navigation tests, then `npm test`, `npm run build` and the full backend suite.

## 7. Outside this plan

WebSockets, presence/typing indicators, direct messages, message attachments (later, through the file service),
notification/email fan-out for new posts or ballots (stage 8), weighted or proxy voting, and fully
end-to-end verifiable voting. Receipts prove inclusion, not content. No general data migration; existing SABP approval data is untouched.

## 8. As built

### State of live chat

Live chat is a working core service. Any plug-in that registers a thread type gets the routes, the
header-authenticated SSE stream, read markers, staff redaction and internal notes, and can use the shared
`ThreadPanel.svelte`. Discussion topics are the only thread type registered so far. Applicant <-> staff panels on
program records (SABP, clubs) are the next consumers. They need a thread type and an authorizer in each plug-in,
plus a `ThreadPanel` on the record page. Nothing in core has to change.

### Differences from the plan

- The ballot topic kind is `voting-ballot`, because topic kinds are namespaced to the registering plug-in. Its
  panel is `ballot`. The frontend maps panel names to lazily loaded components in `src/core/frontend/src/lib/panels.js`, so the
  discussion views never import the voting plug-in.
- `DiscussionService` also provides `boards()` and `can_read()`. Voting uses `can_read()` so that anyone who
  can read a ballot's topic can also read the ballot.
- A topic's author keeps read access to their own topic after a board audience or roster change.
- Counting waits 10 seconds after the close time, so votes admitted just before closing are written first. While
  waiting, the ballot shows as `counting`. A vote that lands after the record is sealed, or on a cancelled ballot,
  is rolled back and the voter is told.
- For `open` and `sealed` ballots, receipts store the leaf hash, so a voter can see their receipt again. A
  `secret` ballot's receipt holds only the ballot ID. Its leaf is shown once, and the page tells the voter to
  save it.
- Visibility is required, with no default, in the elections admin form and the HTTP API. `BallotSpec` (the
  provided service) still defaults to `sealed`.
- Breaking the glass on an `open` ballot needs no reason (the roll view is still audited). Only `sealed` ballots
  require a reason of at least 10 characters and add an entry to the public `unsealed` list.
- Records can be checked in the browser. `src/core/frontend/src/features/voting/lib/record.js` recomputes the Merkle root
  and digest from `GET .../record` with Web Crypto, then confirms the voter's receipt is one of the leaves.
- A refresh control retrieves the latest ballot, count and anchor status without losing a secret ballot's
  one-time receipt. Browser verification checks record consistency; checking the external chain is a separate step.
- Read-marker and plug-in-discovery failures are shown inline with retry controls; failed browser verification
  is handled by the same inline action status as API errors.
- Streams reconcile the entire authorized thread on reconnect, not just sequences after the cursor. Sequence
  allocation and message persistence are separate writes; a cursor alone can miss a late write or old redaction.
- `GET /api/plugins` lists the enabled plug-ins. The menu shows Discussions, and the elections tool, only where
  they run.
- `ROLES` and the role catalogue only list roles from plug-ins in the default profile. Opt-in plug-in roles
  (`discussion_moderator`, `elections_admin`) appear when those plug-ins are enabled.
- Plug-in services get a late-bound `now`, so tests that move the app clock also move plug-in clocks.

### Residual risks

- A `secret` ballot stores no voter link. Even so, an operator with live database access could still try to
  correlate votes with receipts by insertion order or write timing. Treat `secret` as a strong privacy measure,
  not a cryptographic guarantee.
- In split (multi-service) deployments, plug-in anchors are submitted and confirmed when their status is read.
  A ballot nobody opens after closing stays `pending` until someone reads it.
- Receipts prove that a vote was counted, not what it was. This is not an end-to-end verifiable election system.
- Stream reconciliation reads a whole thread per hint/keepalive and retains sequence/redaction state for that
  connection. Measure large-thread performance before scaling to high-volume chat; there is no indexed change log yet.

### Verification

- Backend: **516 tests passed**; the 14 deprecation warnings are unrelated to this feature.
- Frontend: **95 tests passed** in 20 files; production build succeeded and the generated static files are updated.
- Browser smoke test against an isolated in-memory full-profile app: live replies from another member, posting
  without duplicates, voting, closing/counting, receipt/root/digest verification, and reason-gated unsealing with
  public disclosure all passed. Desktop category selection and the sidecar work; the mobile ballot has no horizontal
  overflow.
- EVM signing/submission/confirmation use simulated JSON-RPC tests. No live-chain transaction was sent; the browser
  smoke test used the local development anchor.
- `git diff --check` passed. No commits or data migrations were made.

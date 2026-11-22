import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import BallotPanel from "../../../../../../voting/frontend/components/BallotPanel.svelte";
import ElectionsAdmin from "../../../../../../voting/frontend/views/ElectionsAdmin.svelte";
import { session } from "@utsu/portal";

vi.mock("@utsu/portal", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
import { api } from "@utsu/portal";

const options = [{ id: "ada", label: "Ada" }, { id: "bo", label: "Bo" }, { id: "cy", label: "Cy" }];
const ballot = (extra = {}) => ({
  id: "b1", title: "Board election", description: "", method: "stv", options, seats: 1, max_choices: 0, threshold: "majority",
  quorum: 0, electorate: "members", electorate_label: "Members", visibility: "sealed", status: "open", owner: "voting",
  notice: "Sealed ballot. Nobody can see how you voted unless an official breaks the seal.", unsealed: [], counting: false,
  results: null, record: null, topic: null, eligible: true, voted: false, receipt: null, turnout: 3, manage: false, can_unseal: false,
  ...extra,
});
const posts = path => api.mock.calls.filter(([p, opts]) => p === path && opts?.method === "POST");

afterEach(() => { cleanup(); api.mockReset(); vi.restoreAllMocks(); session.adminMode = false; });

describe("voting on a ballot", () => {
  it("states how private the vote is, then submits the ranking in the order chosen", async () => {
    let voted = false;
    api.mockImplementation(async (path, opts) => {
      if (path === "/voting/ballots/b1/vote") { voted = true; return { receipt: "c".repeat(64), kept: true }; }
      if (path === "/voting/ballots/b1") return voted ? ballot({ voted: true, receipt: "c".repeat(64) }) : ballot();
      throw new Error(`unexpected ${opts?.method || "GET"} ${path}`);
    });
    render(BallotPanel, { id: "b1" });
    expect(await screen.findByText(/Sealed ballot\. Nobody can see/)).toBeInTheDocument();
    const cast = screen.getByRole("button", { name: "Cast my vote" });
    expect(cast).toBeDisabled();

    for (const name of ["Ada", "Cy", "Bo"]) await fireEvent.click(screen.getByRole("button", { name: `Rank ${name}` }));
    await fireEvent.click(screen.getByRole("button", { name: "Move Bo up" }));
    await fireEvent.click(screen.getByRole("button", { name: "Unrank Ada" }));
    await fireEvent.click(cast);

    expect(await screen.findByText("You have voted.")).toBeInTheDocument();
    expect(posts("/voting/ballots/b1/vote")[0][1].body).toEqual({ selection: ["bo", "cy"] });
    expect(screen.getByText("c".repeat(64))).toBeInTheDocument();
  });

  it("shows a secret ballot's receipt once, with a prompt to save it", async () => {
    let voted = false;
    api.mockImplementation(async path => {
      if (path === "/voting/ballots/b1/vote") { voted = true; return { receipt: "d".repeat(64), kept: false }; }
      return ballot({ method: "single", visibility: "secret", notice: "Secret ballot.", voted });
    });
    render(BallotPanel, { id: "b1" });
    await fireEvent.click(await screen.findByLabelText("Bo"));
    await fireEvent.click(screen.getByRole("button", { name: "Cast my vote" }));
    expect(await screen.findByText("Save your receipt now.")).toBeInTheDocument();
    expect(screen.getByText("d".repeat(64))).toBeInTheDocument();
    expect(posts("/voting/ballots/b1/vote")[0][1].body).toEqual({ selection: ["bo"] });
    expect(screen.queryByText(/receipt was shown once/)).not.toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Refresh ballot" }));
    expect(screen.getByText("d".repeat(64))).toBeInTheDocument();
  });

  it("tells someone outside the electorate how to fix their roster status instead of offering the form", async () => {
    api.mockResolvedValue(ballot({ eligible: false }));
    render(BallotPanel, { id: "b1" });
    expect(await screen.findByText(/not in this ballot's electorate/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check my account" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Cast my vote" })).not.toBeInTheDocument();
  });
});

describe("results and the published record", () => {
  // The same reference record as record.test.js, computed by the server's algorithm.
  const leaves = [
    "f8c90bd1c19992ec3bcc8ab892f54df949e013bfda0b9ce91aea2e20046b3f90",
    "c6478d3e8b1e7bc86c5cc881527bf1ce07d5320b82b02253c92f29bb6d78627f",
    "7bf64816b1826adf1577cd1df945496f1f5e755867023369e2250eca30f3ac83",
  ];
  const committed = {
    v: "utsu-ballot-record-v1", ballot: "b1", title: "Café \"motion\"\n2", count: 3,
    root: "48ab6a3543ede9e5e2d1f9484b36878c88a7e895d0026c080f47235cee477d75",
    result: { quota: "15/7", rounds: [{ tally: { b: "1", a: "2" } }], quorum_met: true, tied: [] },
    closed_at: 1700000000, options: [{ id: "a", label: "Ä" }],
  };
  const results = {
    quota: "15/7", valid: 14, ballots: 14, quorum_met: true, elected: ["ada"], tied: [], outcome: "decided",
    rounds: [
      { tally: { ada: "5", bo: "4", cy: "5" }, exhausted: "0", excluded: "bo" },
      { tally: { ada: "15/2", cy: "13/2" }, exhausted: "0", elected: ["ada"] },
    ],
  };

  it("shows the rounds and confirms the voter's receipt against a record recomputed in the browser", async () => {
    api.mockImplementation(async path => {
      if (path === "/voting/ballots/b1/record") return { committed, digest: "7f4409184eb87c6699412e2eaf3566108868214900bc7fd8fe3170769f7424ef", leaves, anchor: null };
      return ballot({
        status: "closed", voted: true, receipt: leaves[1], results,
        record: { digest: "7f44", root: committed.root, count: 3, anchor: { status: "confirmed", block: 9, tx: "0xabc" } },
      });
    });
    render(BallotPanel, { id: "b1" });
    expect(await screen.findByText("Elected: Ada")).toBeInTheDocument();
    expect(screen.getByText("Excluded Bo")).toBeInTheDocument();
    expect(screen.getByText("7.50")).toBeInTheDocument();
    expect(screen.getByText(/Anchored and confirmed in block 9/)).toBeInTheDocument();

    await fireEvent.click(screen.getByRole("button", { name: "Check the record" }));
    expect(await screen.findByText(/Your receipt is in the published record/)).toBeInTheDocument();
    expect(screen.getByText(/Merkle root matches/)).toBeInTheDocument();
    expect(screen.getByText(/digest matches the published record/)).toBeInTheDocument();
  });

  it("reports browser verification failures inline", async () => {
    api.mockImplementation(async path => path.endsWith("/record")
      ? { committed, leaves, digest: "bad" }
      : ballot({ status: "closed", record: { count: 3, digest: "bad", anchor: null } }));
    vi.spyOn(crypto.subtle, "digest").mockRejectedValue(new Error("Browser verification failed."));
    render(BallotPanel, { id: "b1" });
    await fireEvent.click(await screen.findByRole("button", { name: "Check the record" }));
    expect(await screen.findByText("Browser verification failed.")).toBeInTheDocument();
  });

  it("refreshes a pending count without navigating away", async () => {
    api.mockResolvedValueOnce(ballot({ status: "closed", counting: true }))
      .mockResolvedValue(ballot({ status: "closed", results }));
    render(BallotPanel, { id: "b1" });
    expect(await screen.findByText(/Voting has closed/)).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Refresh ballot" }));
    expect(await screen.findByText("Elected: Ada")).toBeInTheDocument();
    expect(screen.queryByText(/Voting has closed/)).not.toBeInTheDocument();
  });
});

describe("elections officials", () => {
  it("break the seal only with a reason, then see the roll", async () => {
    session.adminMode = true;
    api.mockImplementation(async (path, opts) => {
      if (path === "/voting/ballots/b1/unseal") return { roll: [{ voter: "u1", name: "Ada L", choices: ["Ada", "Cy"], receipt: "e".repeat(64) }] };
      if (path === "/voting/ballots/b1") return ballot({ status: "closed", counting: true, manage: true, can_unseal: true });
      throw new Error(`unexpected ${opts?.method || "GET"} ${path}`);
    });
    render(BallotPanel, { id: "b1" });
    await fireEvent.click(await screen.findByRole("button", { name: "Break the seal…" }));
    const confirm = screen.getByRole("button", { name: "Break the seal" });
    const reason = screen.getByLabelText(/Reason for breaking the seal/);
    await fireEvent.input(reason, { target: { value: "too short" } });
    expect(confirm).toBeDisabled();
    await fireEvent.input(reason, { target: { value: "Appeal 2026-04 ordered a recount" } });
    await fireEvent.click(confirm);
    expect(await screen.findByText("1. Ada, 2. Cy")).toBeInTheDocument();
    expect(posts("/voting/ballots/b1/unseal")[0][1].body).toEqual({ reason: "Appeal 2026-04 ordered a recount" });
  });

  it("see a recorded poll's roll without a reason, and get no unseal control on a secret ballot", async () => {
    session.adminMode = true;
    let visibility = "open";
    api.mockImplementation(async path => {
      if (path === "/voting/ballots/b1/unseal") return { roll: [] };
      return ballot({ method: "single", visibility, manage: visibility === "secret", can_unseal: visibility !== "secret" });
    });
    render(BallotPanel, { id: "b1" });
    await fireEvent.click(await screen.findByRole("button", { name: "Show voter roll" }));
    expect(await screen.findByText("No votes yet.")).toBeInTheDocument();
    expect(posts("/voting/ballots/b1/unseal")[0][1].body).toEqual({ reason: "" });

    cleanup();
    visibility = "secret";
    render(BallotPanel, { id: "b1" });
    expect(await screen.findByText(/No link between voters and votes is stored/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /seal|roll/ })).not.toBeInTheDocument();
  });

  it("must choose the vote privacy before a ballot is created", async () => {
    api.mockImplementation(async (path, opts) => {
      if (path === "/voting/setup") return {
        methods: ["yes_no", "single", "approval", "ranked", "stv"], thresholds: ["majority", "two_thirds"],
        electorates: [{ id: "members", description: "Verified members on the membership roster" }],
        boards: [{ id: "general", title: "General" }],
        visibilities: ["sealed", "open", "secret"].map(id => ({ id, notice: `${id} notice` })),
      };
      if (path === "/voting/ballots" && opts?.method === "POST") return ballot({ status: "draft" });
      if (path === "/voting/ballots") return { ballots: [] };
      throw new Error(`unexpected ${path}`);
    });
    render(ElectionsAdmin);
    const form = await screen.findByRole("form", { name: "New ballot" });
    await fireEvent.change(screen.getByLabelText("How votes are counted"), { target: { value: "yes_no" } });
    expect(screen.queryByLabelText(/Options or candidates/)).not.toBeInTheDocument();
    expect(screen.getByLabelText("Needed to carry")).toBeInTheDocument();

    await fireEvent.input(screen.getByLabelText("Title"), { target: { value: "Dinner" } });
    await fireEvent.change(screen.getByLabelText("How votes are counted"), { target: { value: "single" } });
    await fireEvent.input(screen.getByLabelText(/Options or candidates/), { target: { value: "Vegan\n\nHalal\n" } });
    await fireEvent.submit(form);
    expect(await screen.findByText("Choose how private the votes are.")).toBeInTheDocument();
    expect(posts("/voting/ballots")).toHaveLength(0);

    await fireEvent.click(screen.getByRole("radio", { name: /Recorded/ }));
    await fireEvent.change(screen.getByLabelText("Discussion topic"), { target: { value: "general" } });
    const opens = "2099-05-01T12:30";
    const closes = "2099-05-02T12:30";
    await fireEvent.input(screen.getByLabelText(/Opens automatically/), { target: { value: closes } });
    await fireEvent.input(screen.getByLabelText(/Closes automatically/), { target: { value: opens } });
    await fireEvent.submit(form);
    expect(await screen.findByText(/closing time must be in the future and after/)).toBeInTheDocument();
    expect(posts("/voting/ballots")).toHaveLength(0);
    await fireEvent.input(screen.getByLabelText(/Opens automatically/), { target: { value: opens } });
    await fireEvent.input(screen.getByLabelText(/Closes automatically/), { target: { value: closes } });
    await fireEvent.submit(form);
    await screen.findByText(/Ballot created as a draft/);
    expect(posts("/voting/ballots")[0][1].body).toMatchObject({
      title: "Dinner", method: "single", options: ["Vegan", "Halal"], visibility: "open", board: "general",
      opens_at: new Date(opens).getTime() / 1000, closes_at: new Date(closes).getTime() / 1000,
    });
    await waitFor(() => expect(screen.getByLabelText("Title")).toHaveValue(""));
  });
});

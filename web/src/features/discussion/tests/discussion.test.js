import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import ThreadPanel from "../../../components/ThreadPanel.svelte";
import BoardView from "../views/BoardView.svelte";
import DiscussionsView from "../views/DiscussionsView.svelte";
import TopicView from "../views/TopicView.svelte";
import { nav } from "../../../lib/nav.svelte.js";
import { session } from "../../../lib/session.svelte.js";

vi.mock("../../../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
vi.mock("../../../lib/stream.js", () => ({ openStream: vi.fn() }));
import { api } from "../../../lib/api.js";
import { openStream } from "../../../lib/stream.js";

const row = (seq, extra = {}) => ({ seq, author_name: "Ada", mine: false, internal: false, body: `Post ${seq}`, redacted: false, at: 1700000000, ...extra });
const posts = path => api.mock.calls.filter(([p, opts]) => p === path && opts?.method === "POST");

// Captures the stream callbacks so a test can play the server's side of the connection.
function stream() {
  const handle = { close: vi.fn() };
  openStream.mockImplementation((path, callbacks) => Object.assign(handle, { path, ...callbacks }));
  return handle;
}

afterEach(() => { cleanup(); api.mockReset(); openStream.mockReset(); session.adminMode = false; });

describe("live thread", () => {
  const thread = "/threads/discussion-topic/t1";

  it("lets a viewer hide and restore author names, including on incoming posts", async () => {
    const live = stream();
    api.mockImplementation(async path => {
      if (path === thread) return { post: true, staff: false, internal_notes: false, max_body: 4000, last_seq: 0, read: 0 };
      if (path === `${thread}/read`) return {};
      throw new Error(`unexpected ${path}`);
    });
    render(ThreadPanel, { type: "discussion-topic", id: "t1" });
    await vi.waitFor(() => expect(live.onEvent).toBeTypeOf("function"));
    live.onEvent("message", row(1));
    expect(await screen.findByText("Ada")).toBeInTheDocument();
    await fireEvent.click(screen.getByLabelText("Mask author names on this screen"));
    expect(screen.queryByText("Ada")).not.toBeInTheDocument();
    live.onEvent("message", row(2, { author_name: "Bob" }));
    await vi.waitFor(() => expect(screen.getAllByText("Hidden author")).toHaveLength(2));
    expect(screen.queryByText("Bob")).not.toBeInTheDocument();
    await fireEvent.click(screen.getByLabelText("Mask author names on this screen"));
    expect(screen.getByText("Ada")).toBeInTheDocument();
    expect(screen.getByText("Bob")).toBeInTheDocument();
  });

  it("streams posts in order, posts immediately and marks what was seen as read", async () => {
    const live = stream();
    api.mockImplementation(async (path, opts) => {
      if (path === thread) return { post: true, staff: false, internal_notes: false, max_body: 4000, last_seq: 2, read: 0 };
      if (path === `${thread}/messages`) return row(3, { mine: true, body: opts.body.body, author_name: "Me" });
      if (path === `${thread}/read`) return {};
      throw new Error(`unexpected ${path}`);
    });
    render(ThreadPanel, { type: "discussion-topic", id: "t1", title: "Replies" });
    expect(await screen.findByText("Connecting…")).toBeInTheDocument();
    await vi.waitFor(() => expect(live.onEvent).toBeTypeOf("function"));
    expect(live.path).toBe(`${thread}/stream`);

    live.onEvent("message", row(2));
    live.onEvent("message", row(1));
    live.onStatus("live");
    expect(await screen.findByText(/^Live/)).toBeInTheDocument();
    expect(screen.getAllByText(/^Post \d$/).map(node => node.textContent)).toEqual(["Post 1", "Post 2"]);
    expect(posts(`${thread}/read`).at(-1)[1].body).toEqual({ seq: 2 });

    await fireEvent.input(screen.getByLabelText("Your post"), { target: { value: "Agreed" } });
    await fireEvent.click(screen.getByRole("button", { name: "Post" }));
    expect(await screen.findByText("Agreed")).toBeInTheDocument();
    expect(posts(`${thread}/messages`)[0][1].body).toEqual({ body: "Agreed", internal: false, name_choice: "real" });
    expect(screen.getByLabelText("Your post")).toHaveValue("");

    live.onEvent("message", row(3, { mine: true, body: "Agreed", author_name: "Me" }));
    expect(await screen.findAllByText("Agreed")).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Remove post" })).not.toBeInTheDocument();
  });

  it("lets staff remove a post only with a reason, and replaces a redacted post as it streams", async () => {
    const live = stream();
    api.mockImplementation(async (path, opts) => {
      if (path === thread) return { post: true, staff: true, internal_notes: true, max_body: 4000, last_seq: 1, read: 1 };
      if (path === `${thread}/messages/1/redact`) return row(1, { body: "", redacted: true });
      throw new Error(`unexpected ${opts?.method || "GET"} ${path}`);
    });
    render(ThreadPanel, { type: "discussion-topic", id: "t1" });
    await vi.waitFor(() => expect(live.onEvent).toBeTypeOf("function"));
    live.onEvent("message", row(1));
    await fireEvent.click(await screen.findByRole("button", { name: "Remove post" }));
    const remove = screen.getByRole("button", { name: "Remove" });
    expect(remove).toBeDisabled();
    await fireEvent.input(screen.getByLabelText("Reason for removing this post"), { target: { value: "Personal details" } });
    await fireEvent.click(remove);
    expect(await screen.findByText("Removed by a moderator.")).toBeInTheDocument();
    expect(posts(`${thread}/messages/1/redact`)[0][1].body).toEqual({ reason: "Personal details" });
    expect(screen.getByLabelText(/Internal note/)).toBeInTheDocument();
  });

  it("explains a lost permission instead of leaving a dead form", async () => {
    const live = stream();
    api.mockResolvedValue({ post: true, staff: false, internal_notes: false, max_body: 4000, last_seq: 0, read: 0 });
    render(ThreadPanel, { type: "discussion-topic", id: "t1" });
    await vi.waitFor(() => expect(live.onStatus).toBeTypeOf("function"));
    live.onEvent("message", row(1));
    expect(await screen.findByText("Post 1")).toBeInTheDocument();
    live.onStatus("denied");
    expect(await screen.findByText(/You can no longer read this thread/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Your post")).not.toBeInTheDocument();
    expect(screen.queryByText("Post 1")).not.toBeInTheDocument();
  });

  it("removes posts no longer visible when the server reconciles the thread", async () => {
    const live = stream();
    api.mockResolvedValue({ post: true, staff: true, internal_notes: true, max_body: 4000, read: 2 });
    render(ThreadPanel, { type: "discussion-topic", id: "t1" });
    await vi.waitFor(() => expect(live.onEvent).toBeTypeOf("function"));
    live.onEvent("message", row(1, { internal: true }));
    live.onEvent("message", row(2));
    expect(await screen.findByText("Post 1")).toBeInTheDocument();
    live.onEvent("sync", { seqs: [2] });
    await vi.waitFor(() => expect(screen.queryByText("Post 1")).not.toBeInTheDocument());
    expect(screen.getByText("Post 2")).toBeInTheDocument();
  });

  it("closes the stream when the panel goes away", async () => {
    const live = stream();
    api.mockResolvedValue({ post: false, staff: false, internal_notes: false, max_body: 4000, last_seq: 0, read: 0 });
    render(ThreadPanel, { type: "discussion-topic", id: "t1" });
    expect(await screen.findByText("Posting is closed for you in this thread.")).toBeInTheDocument();
    await vi.waitFor(() => expect(openStream).toHaveBeenCalled());
    cleanup();
    expect(live.close).toHaveBeenCalled();
  });

  it("shows a failed read marker inline and retries without losing the posts", async () => {
    const live = stream();
    let attempts = 0;
    api.mockImplementation(async path => {
      if (path === thread) return { post: true, staff: false, internal_notes: false, max_body: 4000, read: 0 };
      if (path === `${thread}/read`) {
        if (++attempts === 1) throw new Error("Read marker could not be saved.");
        return { read: 1 };
      }
      throw new Error(`unexpected ${path}`);
    });
    render(ThreadPanel, { type: "discussion-topic", id: "t1" });
    await vi.waitFor(() => expect(live.onEvent).toBeTypeOf("function"));
    live.onEvent("message", row(1));
    live.onStatus("live");
    expect(await screen.findByText("Read marker could not be saved.")).toBeInTheDocument();
    expect(screen.getByText("Post 1")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Retry marking as read" }));
    await vi.waitFor(() => expect(screen.queryByText("Read marker could not be saved.")).not.toBeInTheDocument());
    expect(posts(`${thread}/read`).map(([, opts]) => opts.body)).toEqual([{ seq: 1 }, { seq: 1 }]);
  });
});

describe("boards and topics", () => {
  it("lists boards and opens one", async () => {
    api.mockResolvedValue({ boards: [{ id: "general", title: "General", description: "", audience: "members", archived: false, owner: null }], moderator: false, member: true });
    render(DiscussionsView);
    await fireEvent.click(await screen.findByRole("button", { name: "General" }));
    expect(nav.name).toBe("discussionBoard");
    expect(nav.props).toEqual({ id: "general" });
    expect(screen.queryByRole("form", { name: "New board" })).not.toBeInTheDocument();
  });

  it("sends someone off the roster to check their account rather than showing an empty page", async () => {
    api.mockResolvedValue({ boards: [], moderator: false, member: false });
    render(DiscussionsView);
    await fireEvent.click(await screen.findByRole("button", { name: "Check my account" }));
    expect(nav.name).toBe("account");
  });

  it("checks a new board's short name before it is sent", async () => {
    session.adminMode = true;
    api.mockImplementation(async (path, opts) => (opts?.method === "POST" ? {} : { boards: [], moderator: true, member: true }));
    render(DiscussionsView);
    await fireEvent.input(await screen.findByLabelText("Title"), { target: { value: "AGM" } });
    await fireEvent.input(screen.getByLabelText("Short name for links"), { target: { value: "AGM 2026" } });
    expect(screen.getByText(/Use only lower-case letters/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create board" })).toBeDisabled();
    await fireEvent.input(screen.getByLabelText("Short name for links"), { target: { value: "agm-2026" } });
    await fireEvent.click(screen.getByRole("button", { name: "Create board" }));
    expect(await screen.findByText("Board created.")).toBeInTheDocument();
    expect(posts("/discussion/boards")[0][1].body).toEqual({ id: "agm-2026", title: "AGM", description: "", audience: "members" });
  });

  it("starts a topic and opens it", async () => {
    api.mockImplementation(async (path, opts) => {
      if (opts?.method === "POST") return { id: "t9", title: opts.body.title };
      return { board: { id: "general", title: "General", archived: false }, topics: [], moderator: false, can_post: true };
    });
    render(BoardView, { id: "general" });
    expect(await screen.findByText("No topics yet.")).toBeInTheDocument();
    await fireEvent.input(screen.getByLabelText("Title"), { target: { value: "Dietary needs" } });
    await fireEvent.input(screen.getByLabelText("Opening post"), { target: { value: "What should we order?" } });
    await fireEvent.click(screen.getByRole("button", { name: "Start topic" }));
    await vi.waitFor(() => expect(nav.name).toBe("discussionTopic"));
    expect(nav.props).toEqual({ id: "t9" });
    expect(posts("/discussion/boards/general/topics")[0][1].body).toEqual({ title: "Dietary needs", body: "What should we order?", name_choice: "real" });
  });

  it("shows the owning plug-in's panel above a ballot topic's replies", async () => {
    stream();
    api.mockImplementation(async path => {
      if (path === "/discussion/topics/t1") return {
        topic: { id: "t1", board: "general", title: "Board election", kind: "voting-ballot", kind_label: "Ballot", panel: "ballot", ref: "b1",
          author_name: "Elections", pinned: true, locked: false, created_at: 1700000000, last_at: 1700000000, posts: 0 },
        board: { id: "general", title: "General" }, moderator: false, can_post: true,
      };
      if (path === "/voting/ballots/b1") return {
        id: "b1", title: "Board election", description: "", method: "single", options: [{ id: "a", label: "Ada" }], seats: 1, max_choices: 0,
        threshold: "majority", quorum: 0, electorate: "members", electorate_label: "Members", visibility: "open", status: "open",
        owner: "voting", notice: "Recorded vote.", unsealed: [], counting: false, results: null, record: null, topic: "t1",
        eligible: true, voted: false, receipt: null, turnout: 0, manage: false, can_unseal: false,
      };
      if (path === "/threads/discussion-topic/t1") return { post: true, staff: false, internal_notes: false, max_body: 4000, last_seq: 0, read: 0 };
      throw new Error(`unexpected ${path}`);
    });
    render(TopicView, { id: "t1" });
    expect(await screen.findByText("Recorded vote.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Cast my vote" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Discuss this ballot" })).not.toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Replies" })).toBeInTheDocument();
    expect(screen.getByText(/Started by Elections/)).toBeInTheDocument();
    await fireEvent.click(screen.getByLabelText("Mask author names on this screen"));
    expect(screen.getByText(/Started by Hidden author/)).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Back to General" }));
    expect(nav.name).toBe("discussionBoard");
  });
});

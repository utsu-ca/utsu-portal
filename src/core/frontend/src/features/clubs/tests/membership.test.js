import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import ClubMembershipView from "../../../../../../clubs/frontend/views/ClubMembershipView.svelte";
import ClubMembersView from "../../../../../../clubs/frontend/views/ClubMembersView.svelte";
import { session } from "@utsu/portal";

const { apiMock, apiAllPagesMock } = vi.hoisted(() => ({ apiMock: vi.fn(), apiAllPagesMock: vi.fn() }));
vi.mock("@utsu/portal", async importOriginal => ({
  ...(await importOriginal()), api: apiMock, apiAllPages: apiAllPagesMock,
}));
import { api, apiAllPages } from "@utsu/portal";

beforeEach(() => {
  session.me = { user: { uuid: "ada" } };
  apiAllPages.mockImplementation(async path => (await api(path)).items);
});
afterEach(() => { cleanup(); api.mockReset(); apiAllPages.mockReset(); session.me = null; vi.restoreAllMocks(); });

it("lets a person ask to join a club they are not in and leave one they are in", async () => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
  api.mockImplementation(async (path, options) => {
    if (path === "/clubs/joined") return { items: [{ id: "c2", name: "Go Club", category: "games", source: "request" }] };
    if (path === "/clubs/directory") return { items: [
      { id: "c1", name: "Chess Club", description: "Weekly chess", category: "games" },
      { id: "c2", name: "Go Club", description: "Weekly go", category: "games" }] };
    return { status: "ok" };
  });
  render(ClubMembershipView);
  await fireEvent.input(await screen.findByLabelText("Message to the club (optional)"), { target: { value: "hello" } });
  await fireEvent.click(screen.getByRole("button", { name: "Ask to join Chess Club" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/clubs/c1/join-requests", { method: "POST", body: { message: "hello" } }));
  expect(await screen.findByText("Your request to join Chess Club was sent. An officer will review it.")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Ask to join Go Club" })).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole("button", { name: "Leave" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/clubs/c2/leave", { method: "POST" }));
});

it("lets an officer approve a request, find a person and grant honorary membership", async () => {
  let relation = "none";
  api.mockImplementation(async (path, options) => {
    if (path === "/clubs/mine") return { items: [{ id: "c1", name: "Chess Club", members: { ada: "owner" } }, { id: "c9", name: "Other", members: { ada: "bogus" } }] };
    if (path === "/clubs/c1/members") return { items: [{ uuid: "ada", name: "Ada L.", role: "owner", source: "officer" }] };
    if (path === "/clubs/c1/join-requests") return { items: [{ uuid: "bob", name: "Bob L.", message: "hi", at: 1 }] };
    if (path.startsWith("/clubs/c1/people/lookup")) return { uuid: "bob", name: "Bob L.", verified: true, relation };
    if (options?.method === "PUT") { relation = "honorary"; return { status: "granted" }; }
    return { status: "approved" };
  });
  render(ClubMembersView);
  expect(await screen.findByText("Bob L.")).toBeInTheDocument();
  expect(screen.queryByText("Other")).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole("button", { name: "Approve" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/clubs/c1/join-requests/bob/decision", { method: "POST", body: { approve: true } }));

  await fireEvent.input(screen.getByLabelText("Email address"), { target: { value: "bob@example.test" } });
  await fireEvent.click(screen.getByRole("button", { name: "Look up" }));
  expect(await screen.findByText(/relation to this club: none/)).toBeInTheDocument();
  expect(api).toHaveBeenCalledWith("/clubs/c1/people/lookup?email=bob%40example.test");
  await fireEvent.input(screen.getByLabelText("Reason for honorary membership"), { target: { value: "Founder" } });
  await fireEvent.click(screen.getByRole("button", { name: "Grant honorary membership" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/clubs/c1/members/bob/honorary", { method: "PUT", body: { reason: "Founder" } }));
  expect(await screen.findByText(/relation to this club: honorary/)).toBeInTheDocument();
});

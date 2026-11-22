import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import ClubRegistrationView from "../../../../../../clubs/frontend/views/ClubRegistrationView.svelte";
import { session } from "@utsu/portal";

const { apiMock, apiAllPagesMock } = vi.hoisted(() => ({ apiMock: vi.fn(), apiAllPagesMock: vi.fn() }));
vi.mock("@utsu/portal", async importOriginal => ({
  ...(await importOriginal()), api: apiMock, apiAllPages: apiAllPagesMock,
}));
import { api, apiAllPages } from "@utsu/portal";

beforeEach(() => {
  session.me = { user: { uuid: "bob" } };
  apiAllPages.mockImplementation(async (path, collection = "items") => {
    const page = await api(path);
    const items = Array.isArray(page?.[collection]) ? page[collection] : [];
    return collection === "items" ? items : { ...page, [collection]: items, next_cursor: null };
  });
});
afterEach(() => { cleanup(); api.mockReset(); apiAllPages.mockReset(); session.me = null; });

it.each([["Accept", true, "Invitation accepted."], ["Decline", false, "Invitation declined."]])(
  "lets the invited person %s a club invitation", async (label, approve, message) => {
    let pending = true;
    api.mockImplementation(async (path, opts) => {
      if (opts?.method === "POST") { pending = false; return { status: approve ? "accepted" : "declined" }; }
      if (path === "/clubs/invitations") return {
        items: pending ? [{ id: "i1", club_id: "c1", club_name: "Chess Club", role: "treasurer" }] : [],
        next_cursor: null,
      };
      return { items: [], next_cursor: null };
    });
    render(ClubRegistrationView);
    expect(await screen.findByText("Chess Club invited you to be treasurer.")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: label }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/clubs/c1/invitations/i1/decision",
      { method: "POST", body: { approve } }));
    expect(await screen.findByText(message)).toBeInTheDocument();
    expect(screen.queryByText("Chess Club invited you to be treasurer.")).not.toBeInTheDocument();
  },
);

it("allows only the owner to request renewal for an expired club", async () => {
  api.mockImplementation(async (path, opts) => {
    if (opts?.method === "POST") return { status: "pending" };
    if (path === "/clubs/invitations") return { items: [], next_cursor: null };
    return { items: [{ id: "c1", name: "Chess Club", status: "expired", approval_year: 2025,
      category: "games", description: "Chess", members: { bob: "owner" } }], next_cursor: null };
  });
  render(ClubRegistrationView);
  expect(await screen.findByText(/Approved for 2025–2026/)).toBeInTheDocument();
  await fireEvent.click(screen.getByRole("button", { name: "Request renewal" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/clubs/c1/renewal", { method: "POST" }));
  expect(await screen.findByText("Renewal submitted for review.")).toBeInTheDocument();
});

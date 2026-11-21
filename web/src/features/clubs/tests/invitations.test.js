import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import ClubRegistrationView from "../views/ClubRegistrationView.svelte";
import { session } from "../../../lib/session.svelte.js";

vi.mock("../../../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
import { api } from "../../../lib/api.js";

beforeEach(() => {
  session.me = { user: { uuid: "bob" } };
});
afterEach(() => { cleanup(); api.mockReset(); session.me = null; });

it.each([["Accept", true, "Invitation accepted."], ["Decline", false, "Invitation declined."]])(
  "lets the invited person %s a club invitation", async (label, approve, message) => {
    let pending = true;
    api.mockImplementation(async (path, opts) => {
      if (opts?.method === "POST") { pending = false; return { status: approve ? "accepted" : "declined" }; }
      if (path === "/clubs/invitations") return pending ? [{ id: "i1", club_id: "c1", club_name: "Chess Club", role: "treasurer" }] : [];
      return [];
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
    if (path === "/clubs/invitations") return [];
    return [{ id: "c1", name: "Chess Club", status: "expired", approval_year: 2025,
      category: "games", description: "Chess", members: { bob: "owner" } }];
  });
  render(ClubRegistrationView);
  expect(await screen.findByText(/Approved for 2025–2026/)).toBeInTheDocument();
  await fireEvent.click(screen.getByRole("button", { name: "Request renewal" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/clubs/c1/renewal", { method: "POST" }));
  expect(await screen.findByText("Renewal submitted for review.")).toBeInTheDocument();
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import AgmAdminView from "../views/AgmAdminView.svelte";

vi.mock("../../../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn(), downloadReport: vi.fn() }));
import { api } from "../../../lib/api.js";

const called = path => api.mock.calls.filter(([p]) => p === path);

afterEach(() => { cleanup(); api.mockReset(); vi.restoreAllMocks(); });

describe("AGM administration", () => {
  it("lists registrations and checks the roster, discarding an answer for edited inputs", async () => {
    api.mockImplementation(async path => {
      if (path.startsWith("/admin/agm/rsvps")) return { items: [{ id: "1", uuid: "u1", name: "Ada Lovelace",
        attendance: "Online", submission_status: "submitted", eligible: true, roster_reason: "matched",
        checked_in: false }], next_cursor: null };
      if (path === "/admin/agm/verify") return { verified: true, reason: "matched" };
      return {};
    });
    render(AgmAdminView);
    expect(await screen.findByText(/Ada Lovelace — Online — submitted — eligible \(matched\) — not checked in/)).toBeInTheDocument();
    await fireEvent.input(screen.getByLabelText("Full legal name"), { target: { value: "Ada Lovelace" } });
    await fireEvent.input(screen.getByLabelText("Student number (optional)"), { target: { value: "1234567890" } });
    await fireEvent.click(screen.getByRole("button", { name: "Check roster" }));
    expect(await screen.findByText("Verified — matched")).toBeInTheDocument();
    expect(called("/admin/agm/verify")[0][1].body).toMatchObject({ full_name: "Ada Lovelace", student_number: "1234567890" });
    await fireEvent.input(screen.getByLabelText("Full legal name"), { target: { value: "Ada L" } });
    expect(screen.queryByText("Verified — matched")).not.toBeInTheDocument();
  });
});

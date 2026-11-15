import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import SenateAdminView from "./SenateAdminView.svelte";
import SenatePublicView from "./SenatePublicView.svelte";
import AgmAdminView from "./AgmAdminView.svelte";

vi.mock("../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn(), downloadReport: vi.fn() }));
import { api } from "../lib/api.js";

let delayed;
function serve() {
  api.mockImplementation(async (path, opts = {}) => {
    if (path === "/senate/current") return { version: 1, seed: 42, inputs_hash: "hash",
      seated: [{ constituency: "General Member", division: "", name: "seat filled" }] };
    if (path === "/admin/senate/seats" && !opts.method) return { seats: [{ constituency: "General Member", division: "", seats: 1 }] };
    if (path.startsWith("/admin/senate/applications")) return { items: [], next_cursor: null };
    if (path === "/admin/senate/seating") {
      if (delayed) return new Promise(resolve => { delayed.resolve = resolve; });
      return { inputs_hash: "a".repeat(64), result: { seated: [] } };
    }
    if (path === "/admin/senate/seating/publish") return { version: 2 };
    if (path.startsWith("/admin/agm/rsvps")) return { items: [{ id: "1", uuid: "u1", name: "Ada Lovelace", attendance: "Online",
      submission_status: "submitted", eligible: true, roster_reason: "matched", checked_in: false }], next_cursor: null };
    if (path === "/admin/agm/verify") return { verified: true, reason: "matched" };
    return {};
  });
}
const called = path => api.mock.calls.filter(([p]) => p === path);

afterEach(() => { cleanup(); api.mockReset(); delayed = undefined; vi.restoreAllMocks(); });

describe("Senate results", () => {
  it("shows the published seats", async () => {
    serve();
    render(SenatePublicView);
    expect(await screen.findByText("General Member: seat filled")).toBeInTheDocument();
  });
});

describe("Senate administration", () => {
  it("publishes only a preview of the saved configuration, and drops a stale one", async () => {
    serve();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(SenateAdminView);
    const count = await screen.findByLabelText("Seat count");
    const run = screen.getByRole("button", { name: "Preview seating" });
    const publish = screen.getByRole("button", { name: "Publish reviewed preview" });
    expect(publish).toBeDisabled();

    await fireEvent.input(count, { target: { value: "2" } });  // unsaved edit: previewing is refused
    await fireEvent.click(run);
    expect(await screen.findByText("Save seat configuration changes before previewing.")).toBeInTheDocument();
    expect(called("/admin/senate/seating")).toHaveLength(0);

    await fireEvent.click(screen.getByRole("button", { name: "Save seat configuration" }));
    await screen.findByText("Seat configuration saved.");
    await fireEvent.click(run);
    await waitFor(() => expect(publish).toBeEnabled());
    await fireEvent.click(publish);
    await screen.findByText("Published version 2.");
    expect(called("/admin/senate/seating/publish")[0][1].body).toEqual({ seed: 2026, inputs_hash: "a".repeat(64) });
    expect(publish).toBeDisabled();

    delayed = {};  // an edit made while the preview is computing discards its answer
    await fireEvent.click(run);
    await fireEvent.input(screen.getByLabelText("Seating seed (integer)"), { target: { value: "2027" } });
    delayed.resolve({ inputs_hash: "b".repeat(64), result: { seated: [] } });
    expect(await screen.findByText("Inputs changed while computing; request a new preview.")).toBeInTheDocument();
    expect(publish).toBeDisabled();
  });
});

describe("AGM administration", () => {
  it("lists registrations and checks the roster, discarding an answer for edited inputs", async () => {
    serve();
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

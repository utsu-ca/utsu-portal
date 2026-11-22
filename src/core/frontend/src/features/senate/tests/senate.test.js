import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import SenateAdminView from "../../../../../../senate/frontend/views/SenateAdminView.svelte";
import SenatePublicView from "../../../../../../senate/frontend/views/SenatePublicView.svelte";

vi.mock("@utsu/portal", async importOriginal => ({ ...(await importOriginal()), api: vi.fn(), downloadReport: vi.fn() }));
import { api } from "@utsu/portal";

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

    await fireEvent.input(count, { target: { value: "2" } });
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

    delayed = {};
    await fireEvent.click(run);
    await fireEvent.input(screen.getByLabelText("Seating seed (integer)"), { target: { value: "2027" } });
    delayed.resolve({ inputs_hash: "b".repeat(64), result: { seated: [] } });
    expect(await screen.findByText("Inputs changed while computing; request a new preview.")).toBeInTheDocument();
    expect(publish).toBeDisabled();
  });
});

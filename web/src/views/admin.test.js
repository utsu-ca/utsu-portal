import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import AdminRoster from "./AdminRoster.svelte";

vi.mock("../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn(), downloadReport: vi.fn() }));
import { api } from "../lib/api.js";

afterEach(() => { cleanup(); api.mockReset(); vi.restoreAllMocks(); });

describe("roster upload", () => {
  it("needs a file and an explicit confirmation to replace the roster", async () => {
    api.mockResolvedValue({ imported: true, count: 2, checked: 1, rostered: 1 });
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(AdminRoster);
    const button = screen.getByRole("button", { name: "Import roster" });
    await fireEvent.click(button);
    expect(await screen.findByText("Choose one roster CSV file.")).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();

    const file = new File(["roster csv"], "roster.csv", { type: "text/csv" });
    await fireEvent.change(screen.getByLabelText("Roster CSV"), { target: { files: [file] } });
    await fireEvent.click(button);
    await waitFor(() => expect(api).toHaveBeenCalledTimes(1));
    expect(api.mock.calls[0][0]).toBe("/admin/roster");
    expect(api.mock.calls[0][1].body).toEqual({ csv: "roster csv", replace: false });

    await fireEvent.click(screen.getByLabelText(/Replace the existing roster/));
    await fireEvent.click(button);
    expect(confirmSpy).toHaveBeenCalled();
    expect(api).toHaveBeenCalledTimes(1);
    confirmSpy.mockReturnValue(true);
    await fireEvent.click(button);
    await waitFor(() => expect(api).toHaveBeenCalledTimes(2));
    expect(api.mock.calls[1][1].body.replace).toBe(true);
  });
});

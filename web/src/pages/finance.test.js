import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import Finance from "./Finance.svelte";

vi.mock("../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn(), downloadReport: vi.fn() }));
import { api, downloadReport } from "../lib/api.js";

const ada = { uuid: "u-ada", name: "Ada Lovelace", status: "on_file", approved: true, awarded_amount: 750, request_expires: null, has_cheque: false };
const bob = { uuid: "u-bob", name: "Bob Test", status: "reverify_received", approved: true, awarded_amount: 500, request_expires: null, has_cheque: true };

beforeEach(() => sessionStorage.setItem("token", "t"));
afterEach(() => { cleanup(); api.mockReset(); downloadReport.mockReset(); sessionStorage.clear(); vi.restoreAllMocks(); });

const card = name => screen.getByRole("heading", { name }).closest(".card");

describe("finance portal", () => {
  it("asks for sign-in when there is no session", () => {
    sessionStorage.clear();
    render(Finance);
    expect(screen.getByText(/Sign in to the portal with your finance account/)).toBeInTheDocument();
    expect(api).not.toHaveBeenCalled();
  });

  it("flags failed validation with one click and tells the controller the applicant was notified", async () => {
    let items = [ada, bob];
    api.mockImplementation(async (path, opts = {}) => {
      if (path.startsWith("/finance/sap/banking?")) return { items, next_cursor: null };
      if (path === "/finance/sap/banking/u-ada/validation-failed") {
        items = [{ ...ada, status: "reverify_requested", request_expires: 2e9 }, bob];
        return { status: "reverify_requested" };
      }
    });
    render(Finance);
    const adaCard = within(await screen.findByRole("heading", { name: "Ada Lovelace" }).then(h => h.closest(".card")));
    expect(adaCard.getByText("On file")).toBeInTheDocument();

    await fireEvent.click(adaCard.getByRole("button", { name: "Banking failed validation" }));  // no confirmation step
    expect(await screen.findByText(/Ada Lovelace was emailed a link/)).toBeInTheDocument();
    const posts = api.mock.calls.filter(([, o]) => o?.method === "POST");
    expect(posts).toHaveLength(1);
    expect(posts[0][0]).toBe("/finance/sap/banking/u-ada/validation-failed");
    await waitFor(() => expect(within(card("Ada Lovelace")).getByText("Waiting for applicant")).toBeInTheDocument());
    expect(within(card("Ada Lovelace")).getByRole("button", { name: "Send the request again" })).toBeInTheDocument();
  });

  it("reviews newly submitted details and the void cheque before accepting", async () => {
    api.mockImplementation(async (path, opts = {}) => {
      if (path.startsWith("/finance/sap/banking?")) return { items: [bob], next_cursor: null };
      if (path === "/finance/sap/banking/u-bob/pending") return {
        active: { institution_number: "001", transit_number: "00023", account_number: "0000456" },
        pending: { institution_number: "002", transit_number: "12345", account_number: "999888" },
        void_cheque: { sha256: "abcdef0123456789", size: 2048, uploaded_at: 1 }, revision: 3 };
      return { status: "on_file" };
    });
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(Finance);
    const bobCard = within(await screen.findByRole("heading", { name: "Bob Test" }).then(h => h.closest(".card")));
    expect(bobCard.queryByRole("button", { name: "Banking failed validation" })).not.toBeInTheDocument();

    await fireEvent.click(bobCard.getByRole("button", { name: "Review new details" }));
    expect(await bobCard.findByText("999888")).toBeInTheDocument();
    await fireEvent.click(bobCard.getByRole("button", { name: "Download void cheque (PDF)" }));
    expect(downloadReport).toHaveBeenCalledWith("/finance/sap/banking/u-bob/cheque", "void-cheque.pdf");

    await fireEvent.click(bobCard.getByRole("button", { name: "Accept new details" }));
    await waitFor(() => expect(api.mock.calls.some(([p]) => p === "/finance/sap/banking/u-bob/accept")).toBe(true));
    expect(api.mock.calls.find(([p]) => p === "/finance/sap/banking/u-bob/accept")[1].body).toEqual({ confirm: true, revision: 3 });
  });

  it("filters to the records waiting for review", async () => {
    api.mockResolvedValue({ items: [ada, bob], next_cursor: null });
    render(Finance);
    await screen.findByRole("heading", { name: "Ada Lovelace" });
    await fireEvent.change(screen.getByLabelText("Show"), { target: { value: "reverify_received" } });
    expect(screen.queryByRole("heading", { name: "Ada Lovelace" })).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Bob Test" })).toBeInTheDocument();
  });
});

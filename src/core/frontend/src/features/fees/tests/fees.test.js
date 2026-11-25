import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import FeeOptOutView from "../views/FeeOptOutView.svelte";
import { formatCents, parseDollars } from "../lib/money.js";

vi.mock("../../../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
import { api } from "../../../lib/api.js";

afterEach(() => { cleanup(); api.mockReset(); });

const fee = { id: "f1", name: "Radio levy", description: "", amount_cents: 1000, refundable: true, request: null };
const offer = (banking_status = "not_submitted") => ({
  banking_status, requests: [],
  windows: [
    { id: "fall", open: true, session_label: "Fall-Winter Session 2027-28", opens_at: 0, closes_at: 2_000_000_000, fees: [fee] },
    { id: "summer", open: false, session_label: "Summer Session 2028", opens_at: 2_000_000_000, closes_at: 2_100_000_000, fees: [] },
  ],
});

it("parses and formats money in cents", () => {
  expect(parseDollars("$25.5")).toBe(2550);
  expect(parseDollars("0")).toBeNull();
  expect(parseDollars("1.234")).toBeNull();
  expect(formatCents(1250)).toBe("$12.50");
});

it("requires banking information before a refund can be requested", async () => {
  api.mockResolvedValueOnce(offer());
  render(FeeOptOutView);
  await fireEvent.click(await screen.findByLabelText("Opt out of Radio levy"));
  await fireEvent.click(screen.getByLabelText(/already paid this fee/));
  expect(screen.getByRole("alert")).toHaveTextContent("banking information");
  expect(screen.getByRole("button", { name: "Submit opt-out" })).toBeDisabled();
});

it("submits an opt-out for an unpaid fee", async () => {
  api.mockResolvedValueOnce(offer()).mockResolvedValueOnce({ requests: [] }).mockResolvedValueOnce(offer());
  render(FeeOptOutView);
  await fireEvent.click(await screen.findByLabelText("Opt out of Radio levy"));
  await fireEvent.click(screen.getByRole("button", { name: "Submit opt-out" }));
  await screen.findByText("Your request was recorded.");
  expect(api).toHaveBeenCalledWith("/fees/optout", {
    method: "POST", body: { window: "fall", items: [{ fee_id: "f1", already_paid: false }] },
  });
});

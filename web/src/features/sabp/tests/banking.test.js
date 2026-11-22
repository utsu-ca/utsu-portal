import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import { BankingView } from "@utsu/portal";
import SapBankingView from "../../../../../src/sabp/frontend/views/SapBankingView.svelte";
import { session } from "../../../lib/session.svelte.js";
import { bankingStatus, clearBankingStatus, loadBankingStatus } from "../../profile/state/banking.svelte.js";

vi.mock("../../../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
import { api } from "@utsu/portal";

const member = { status: "verified", roles: [], user: { uuid: "u1" } };
const pdf = (content = "%PDF-1.4\n%%EOF") => new File([content], "cheque.pdf", { type: "application/pdf" });
const fill = async () => {
  await fireEvent.input(screen.getByLabelText(/Institution number/), { target: { value: "002" } });
  await fireEvent.input(screen.getByLabelText(/Transit number/), { target: { value: "12345" } });
  await fireEvent.input(screen.getByLabelText(/Account number/), { target: { value: "999888" } });
};

beforeEach(() => {
  sessionStorage.clear();
  history.replaceState(null, "", "/");
  session.token = null;
  session.me = null;
  clearBankingStatus();
});
afterEach(() => { cleanup(); api.mockReset(); });

describe("banking information verification page", () => {
  it("asks the applicant to re-enter details and upload a void cheque PDF", async () => {
    session.token = "t";
    session.me = member;
    history.replaceState(null, "", "/#sap-banking=tok123");
    api.mockImplementation(async path => path === "/profile/financial"
      ? { status: "reverify_requested", expires_at: 2e9 }
      : { status: "reverify_received" });
    render(SapBankingView);
    expect(await screen.findByText("We could not verify the banking information you gave us.")).toBeInTheDocument();
    await fill();

    await fireEvent.click(screen.getByRole("button", { name: "Submit for verification" }));
    expect(await screen.findByText("Choose a PDF copy of your void cheque.")).toBeInTheDocument();
    const upload = screen.getByLabelText("Void cheque (PDF)");
    await fireEvent.change(upload, { target: { files: [pdf("not a pdf")] } });
    await fireEvent.click(screen.getByRole("button", { name: "Submit for verification" }));
    expect(await screen.findByText("The void cheque must be a PDF file.")).toBeInTheDocument();
    expect(api.mock.calls.some(([, opts]) => opts?.method === "PUT")).toBe(false);

    await fireEvent.change(upload, { target: { files: [pdf()] } });
    await fireEvent.click(screen.getByRole("button", { name: "Submit for verification" }));
    await waitFor(() => expect(api.mock.calls.some(([, opts]) => opts?.method === "PUT")).toBe(true));
    const [path, opts] = api.mock.calls.find(([, o]) => o?.method === "PUT");
    expect(path).toBe("/sap/banking/reverify");
    expect(opts.body).toMatchObject({ institution_number: "002", transit_number: "12345", account_number: "999888", token: "tok123" });
    expect(opts.body.void_cheque).toMatch(/^data:application\/pdf;base64,/);
    expect(location.hash).toBe("");
  });

  it("points an applicant who arrives without the emailed link back to their email", async () => {
    session.token = "t";
    session.me = member;
    api.mockResolvedValue({ status: "reverify_requested", expires_at: 2e9 });
    render(SapBankingView);
    expect(await screen.findByText(/Open the link in the email we sent you/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Void cheque (PDF)")).not.toBeInTheDocument();
  });

  it("takes first-time banking details without a cheque", async () => {
    session.token = "t";
    session.me = member;
    api.mockImplementation(async (path, opts) => opts?.method === "PUT" ? { status: "on_file" } : { status: "not_submitted" });
    render(SapBankingView);
    await screen.findByText(/An active SABP application is required/);
    expect(screen.queryByLabelText("Void cheque (PDF)")).not.toBeInTheDocument();
    await fill();
    await fireEvent.click(screen.getByRole("button", { name: "Submit banking details" }));
    await waitFor(() => expect(api.mock.calls.some(([p, o]) => p === "/sap/banking" && o?.method === "PUT")).toBe(true));
  });

  it("saves profile banking independently of a student aid application", async () => {
    session.token = "t";
    session.me = member;
    api.mockImplementation(async (path, opts) => opts?.method === "PUT" ? { status: "on_file" } : { status: "not_submitted" });
    render(BankingView);
    await screen.findByText(/Banking details are stored separately from your profile/);
    await fill();
    await fireEvent.click(screen.getByRole("button", { name: "Submit banking details" }));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/profile/financial", {
      method: "PUT", body: { institution_number: "002", transit_number: "12345", account_number: "999888" },
    }));
  });
});

it("ignores a banking response from a session that signed out while it was pending", async () => {
  session.token = "old";
  session.me = member;
  let resolve;
  api.mockImplementation(() => new Promise(done => { resolve = done; }));
  const pending = loadBankingStatus();
  session.token = null;
  session.me = null;
  clearBankingStatus();
  resolve({ status: "reverify_requested" });
  await pending;
  expect(bankingStatus.status).toBeNull();
});

it("replaces a verified session's cached banking status before effects can clear it", async () => {
  session.token = "old";
  session.me = member;
  api.mockResolvedValueOnce({ status: "reverify_requested" }).mockResolvedValueOnce({ status: "on_file" });
  await loadBankingStatus();
  session.token = null;
  session.me = null;
  session.token = "new";
  session.me = { ...member, user: { uuid: "u2" } };
  await loadBankingStatus();
  expect(api).toHaveBeenCalledTimes(2);
  expect(bankingStatus.status).toEqual({ status: "on_file" });
});

it("does not display a stale banking failure in a replacement verified session", async () => {
  session.token = "old";
  session.me = member;
  let rejectOld;
  api.mockImplementationOnce(() => new Promise((resolve, reject) => { rejectOld = reject; }))
    .mockResolvedValueOnce({ status: "on_file" });
  const oldRequest = loadBankingStatus();
  session.token = "new";
  session.me = { ...member, user: { uuid: "u2" } };
  await loadBankingStatus();
  rejectOld(new Error("Previous session failed."));
  await expect(oldRequest).rejects.toThrow("Previous session failed.");
  expect(bankingStatus.status).toEqual({ status: "on_file" });
  expect(bankingStatus.error).toBe("");
  expect(bankingStatus.busy).toBe(false);
});

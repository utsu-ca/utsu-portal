import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import BankingView from "./BankingView.svelte";
import FormView from "./FormView.svelte";

vi.mock("../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
import { api } from "../lib/api.js";

const pdf = (content = "%PDF-1.4\n%%EOF") => new File([content], "cheque.pdf", { type: "application/pdf" });
const fill = async () => {
  await fireEvent.input(screen.getByLabelText(/Institution number/), { target: { value: "002" } });
  await fireEvent.input(screen.getByLabelText(/Transit number/), { target: { value: "12345" } });
  await fireEvent.input(screen.getByLabelText(/Account number/), { target: { value: "999888" } });
};

beforeEach(() => { sessionStorage.clear(); history.replaceState(null, "", "/"); });
afterEach(() => { cleanup(); api.mockReset(); });

describe("banking information verification page", () => {
  it("asks the applicant to re-enter details and upload a void cheque PDF", async () => {
    history.replaceState(null, "", "/#sap-banking=tok123");
    api.mockImplementation(async path => path === "/sap/banking" ? { status: "reverify_requested", expires_at: 2e9 } : { status: "reverify_received" });
    render(BankingView);
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
    expect(location.hash).toBe("");  // the single-use link is not left in the address bar
  });

  it("points an applicant who arrives without the emailed link back to their email", async () => {
    api.mockResolvedValue({ status: "reverify_requested", expires_at: 2e9 });
    render(BankingView);
    expect(await screen.findByText(/Open the link in the email we sent you/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Void cheque (PDF)")).not.toBeInTheDocument();
  });

  it("takes first-time banking details without a cheque", async () => {
    api.mockImplementation(async (path, opts) => opts?.method === "PUT" ? { status: "on_file" } : { status: "not_submitted" });
    render(BankingView);
    await screen.findByText(/An active SABP application is required/);
    expect(screen.queryByLabelText("Void cheque (PDF)")).not.toBeInTheDocument();
    await fill();
    await fireEvent.click(screen.getByRole("button", { name: "Submit banking details" }));
    await waitFor(() => expect(api.mock.calls.some(([p, o]) => p === "/sap/banking" && o?.method === "PUT")).toBe(true));
  });
});

describe("a submission refused because student status cannot be verified", () => {
  it("shows the reason beside the form so the person can correct their details", async () => {
    const schema = { title: "AGM RSVP", fields: [{ id: "attendance", type: "select", label: "Attendance", options: ["Online", "In person"], required: true }] };
    api.mockImplementation(async (path, opts) => {
      if (path === "/forms/agm_rsvp/static") return { schema, version: 2, hash: "h" };
      if (path === "/me/submissions") return [];
      if (opts?.method === "PUT") throw Object.assign(new Error("We could not verify your student status (no roster record)."), { status: 403 });
    });
    render(FormView, { id: "agm_rsvp" });
    await fireEvent.change(await screen.findByLabelText(/Attendance/), { target: { value: "Online" } });
    await fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(await screen.findByText(/could not verify your student status/)).toBeInTheDocument();
  });

  it("highlights fields the server rejected", async () => {
    const schema = { title: "Form", fields: [{ id: "name", type: "text", label: "Name" }] };
    api.mockImplementation(async (path, opts) => {
      if (path === "/forms/f/static") return { schema, version: 1, hash: "h" };
      if (path === "/me/submissions") return [];
      if (opts?.method === "PUT") throw Object.assign(new Error("{}"), { status: 422, detail: { name: "at most 3 characters" } });
    });
    render(FormView, { id: "f" });
    await fireEvent.input(await screen.findByLabelText("Name"), { target: { value: "Ada" } });
    await fireEvent.click(screen.getByRole("button", { name: "Submit" }));
    expect(await screen.findByText("at most 3 characters")).toBeInTheDocument();
  });
});

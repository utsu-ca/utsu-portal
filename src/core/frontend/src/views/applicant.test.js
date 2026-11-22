import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import FormView from "./FormView.svelte";

vi.mock("../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
import { api } from "../lib/api.js";

beforeEach(() => { sessionStorage.clear(); history.replaceState(null, "", "/"); });
afterEach(() => { cleanup(); api.mockReset(); });

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

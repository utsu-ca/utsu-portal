import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import AdminRoster from "./AdminRoster.svelte";
import AdminMembers from "./AdminMembers.svelte";

vi.mock("../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn(), downloadReport: vi.fn() }));
import { api } from "../lib/api.js";

afterEach(() => { cleanup(); api.mockReset(); vi.restoreAllMocks(); });

describe("roster upload", () => {
  it("needs a file and an explicit confirmation to replace the roster", async () => {
    api.mockImplementation((path, options) => options?.method === "POST"
      ? Promise.resolve({ imported: true, count: 2, checked: 1, rostered: 1 })
      : Promise.resolve({ items: [], total: 0, offset: 0, limit: 25 }));
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    render(AdminRoster);
    await screen.findByText("No roster entries.");
    const button = screen.getByRole("button", { name: "Import roster" });
    await fireEvent.click(button);
    expect(await screen.findByText("Choose one roster CSV file.")).toBeInTheDocument();
    expect(api.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(0);

    const file = new File(["roster csv"], "roster.csv", { type: "text/csv" });
    await fireEvent.change(screen.getByLabelText("Roster CSV"), { target: { files: [file] } });
    await fireEvent.click(button);
    await waitFor(() => expect(api.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1));
    expect(api.mock.calls.find(([, options]) => options?.method === "POST")[1].body).toEqual({ csv: "roster csv", replace: false });
    await waitFor(() => expect(api.mock.calls.filter(([path]) => path.startsWith("/admin/roster?"))).toHaveLength(2));

    await fireEvent.click(screen.getByLabelText(/Replace the existing roster/));
    await fireEvent.click(button);
    expect(confirmSpy).toHaveBeenCalled();
    expect(api.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(1);
    confirmSpy.mockReturnValue(true);
    await fireEvent.click(button);
    await waitFor(() => expect(api.mock.calls.filter(([, options]) => options?.method === "POST")).toHaveLength(2));
    expect(api.mock.calls.filter(([, options]) => options?.method === "POST")[1][1].body.replace).toBe(true);
  });

  it("shows sanitized roster entries and navigates pages", async () => {
    api.mockImplementation(path => Promise.resolve(path.includes("offset=25")
      ? { items: [{ name: "No A.", student_number: "xxxx0001", email: null, last_login: null }], total: 26, offset: 25, limit: 25 }
      : { items: Array.from({ length: 25 }, (_, i) => ({ name: `Dani ${i}.`, student_number: "xxxx1920", email: "dani@example.test", last_login: 1710000000 })), total: 26, offset: 0, limit: 25 }));
    render(AdminRoster);
    expect(await screen.findByText("Dani 0.")).toBeInTheDocument();
    expect(screen.getAllByText("xxxx1920")).toHaveLength(25);
    expect(screen.getByText("Showing 1–25 of 26")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Next" }));
    expect(await screen.findByText("No A.")).toBeInTheDocument();
    expect(screen.getByText("Showing 26–26 of 26")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next" })).toBeDisabled();
    await fireEvent.click(screen.getByRole("button", { name: "Previous" }));
    expect(await screen.findByText("Dani 0.")).toBeInTheDocument();
  });

  it("shows loading failures and retries", async () => {
    api.mockRejectedValueOnce(new Error("Service unavailable"))
      .mockResolvedValueOnce({ items: [], total: 0, offset: 0, limit: 25 });
    render(AdminRoster);
    expect(await screen.findByRole("alert")).toHaveTextContent("Service unavailable");
    await fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("No roster entries.")).toBeInTheDocument();
  });
});

describe("member lookup", () => {
  const member = (extra = {}) => ({
    uuid: "u1", name: "Ada Lovelace", email: "ada@example.test", student_number: "xxxxx7890x", status: "verified", roles: [],
    roster: { rostered: false, reason: "name not found", division: null },
    membership: { session: "Fall 2026", live_member: false, rostered_member: false }, honorary: null, ...extra,
  });

  it("looks an account up and grants then removes honorary membership", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.mockImplementation((path, options) => {
      if (options?.method === "PUT") return Promise.resolve(member({
        membership: { session: "Fall 2026", live_member: true, rostered_member: true },
        honorary: { session: "Fall 2026", reason: "Past president", current: true },
      }));
      if (options?.method === "DELETE") return Promise.resolve(member());
      return Promise.resolve(member());
    });
    render(AdminMembers);
    await fireEvent.input(screen.getByLabelText("Email address"), { target: { value: " ada@example.test " } });
    await fireEvent.click(screen.getByRole("button", { name: "Look up" }));
    expect(await screen.findByText("Ada Lovelace")).toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/admin/members/lookup?email=ada%40example.test");
    expect(screen.getByText(/Member of this session: no/)).toBeInTheDocument();

    const grant = screen.getByRole("button", { name: "Grant honorary membership" });
    expect(grant).toBeDisabled();
    await fireEvent.input(screen.getByLabelText("Reason"), { target: { value: "Past president" } });
    await fireEvent.click(grant);
    expect(await screen.findByText(/Member of this session: yes/)).toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/admin/members/u1/honorary", { method: "PUT", body: { reason: "Past president", division: "" } });

    await fireEvent.click(screen.getByRole("button", { name: "Remove honorary membership" }));
    expect(await screen.findByText(/Member of this session: no/)).toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/admin/members/u1/honorary", { method: "DELETE" });
  });

  it("looks up by student number and reports a missing account", async () => {
    api.mockRejectedValueOnce(new Error("no account with that student number"));
    render(AdminMembers);
    await fireEvent.change(screen.getByLabelText("Look up by"), { target: { value: "student_number" } });
    await fireEvent.input(screen.getByLabelText("Full student number"), { target: { value: "1234567890" } });
    await fireEvent.click(screen.getByRole("button", { name: "Look up" }));
    expect(await screen.findByText("no account with that student number")).toBeInTheDocument();
    expect(api).toHaveBeenCalledWith("/admin/members/lookup?student_number=1234567890");
  });
});

import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import SapAdminView from "../views/SapAdminView.svelte";

vi.mock("../../../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn(), downloadReport: vi.fn() }));
import { api } from "../../../lib/api.js";

afterEach(() => { cleanup(); api.mockReset(); vi.restoreAllMocks(); });

const post = path => api.mock.calls.find(([p, o]) => p.endsWith(path) && o?.method);

describe("SABP administration", () => {
  it("saves weights, validates the review count, requires a reason to resolve, and imports history", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    api.mockImplementation(async (path, opts) => {
      if (path.startsWith("/admin/sap/applications?")) return { items: [{
        uuid: "student", name: "Student", eligible: true, roster_reason: "", submission_status: "submitted",
        review_stage: "discuss", revision: 7, data: {}, wfni: {}, review_hardship_prepared: true }], next_cursor: null };
      if (path === "/admin/sap/review-report") return [{ uuid: "student", review_id: "r1", stage: "discuss", reviews: [], decision: null }];
      if (path === "/admin/sap/weights") return opts?.method
        ? { hash: "weights-hash" } : { csv: "field,answer,points\nsaved,answer,1" };
      return {};
    });
    render(SapAdminView);
    await screen.findByRole("heading", { name: "Student" });
    await waitFor(() => expect(screen.getByLabelText("WFNI weights CSV")).toHaveValue("field,answer,points\nsaved,answer,1"));

    await fireEvent.input(screen.getByLabelText("WFNI weights CSV"), { target: { value: "field,answer,points" } });
    await fireEvent.click(screen.getByRole("button", { name: "Save WFNI weights" }));
    expect(await screen.findByText("Weights saved (SHA-256: weights-hash). Reload applications to see updated scores.")).toBeInTheDocument();
    expect(post("/admin/sap/weights")[1].body.csv).toBe("field,answer,points");

    const count = screen.getByLabelText("Required reviews (increase only, 2–5)");
    await fireEvent.input(count, { target: { value: "2.5" } });
    await fireEvent.click(screen.getByRole("button", { name: "Increase required reviews" }));
    expect(await screen.findByText("Enter a whole number between 2 and 5.")).toBeInTheDocument();
    expect(post("/review-count")).toBeUndefined();
    await fireEvent.input(count, { target: { value: "3" } });
    await fireEvent.click(screen.getByRole("button", { name: "Increase required reviews" }));
    await waitFor(() => expect(post("/review-count")).toBeDefined());
    expect(post("/review-count")[0]).toBe("/admin/sap/applications/student/review-count");
    expect(post("/review-count")[1].body.required_reviews).toBe(3);

    await fireEvent.click(await screen.findByRole("button", { name: "Resolve disagreement" }));
    expect(await screen.findByText("Enter a reason for resolving the disagreement.")).toBeInTheDocument();
    expect(post("/resolve-review")).toBeUndefined();
    const reason = screen.getByPlaceholderText("Reason for resolving the disagreement");
    await fireEvent.input(reason, { target: { value: "Committee reviewed the disagreement." } });
    await fireEvent.click(screen.getByRole("button", { name: "Resolve disagreement" }));
    await waitFor(() => expect(post("/resolve-review")).toBeDefined());
    expect(post("/resolve-review")[1].body).toEqual({ reason: "Committee reviewed the disagreement.", revision: 7 });

    await fireEvent.input(screen.getByLabelText("Historical source reference"), { target: { value: "B012-01" } });
    await fireEvent.input(screen.getByLabelText("Historical terms (semicolon-separated)"), { target: { value: "Fall 2025; Winter 2026" } });
    await fireEvent.input(screen.getByLabelText("Historical decision date"), { target: { value: "2025-11-01" } });
    await fireEvent.input(screen.getByLabelText("Historical awarded amount"), { target: { value: "500" } });
    await fireEvent.click(screen.getByLabelText("Approved"));
    const ratings = screen.getByLabelText("Historical ratings (optional JSON)");
    await fireEvent.input(ratings, { target: { value: "not json" } });
    await fireEvent.click(screen.getByRole("button", { name: "Import historical decision" }));
    expect(await screen.findByText("Historical ratings must be a JSON array.")).toBeInTheDocument();
    expect(post("/history")).toBeUndefined();
    await fireEvent.input(ratings, { target: { value: '[{"reviewer":"Reviewer","level":3}]' } });
    await fireEvent.click(screen.getByRole("button", { name: "Import historical decision" }));
    await waitFor(() => expect(post("/history")).toBeDefined());
    expect(post("/history")[1].body).toMatchObject({
      source: "B012-01", terms: ["Fall 2025", "Winter 2026"], decided_on: "2025-11-01", amount: 500,
      approved: true, revision: 7, ratings: [{ reviewer: "Reviewer", level: 3 }], confirm: true });
  });

  it("shows the bundled weights as an inactive draft when no configuration exists", async () => {
    api.mockImplementation(async path => {
      if (path === "/admin/sap/weights") throw Object.assign(new Error("not configured"), { status: 404 });
      if (path.startsWith("/admin/sap/applications?")) return { items: [], next_cursor: null };
      return [];
    });
    render(SapAdminView);
    expect(await screen.findByText(/draft default weights/)).toBeInTheDocument();
    expect(screen.getByLabelText("WFNI weights CSV").value).toContain("disability_mobility,Yes,2");
    expect(post("/admin/sap/weights")).toBeUndefined();
  });

  it("does not substitute defaults or permit saving when loading weights fails", async () => {
    api.mockImplementation(async path => {
      if (path === "/admin/sap/weights") throw Object.assign(new Error("Service unavailable"), { status: 503 });
      if (path.startsWith("/admin/sap/applications?")) return { items: [], next_cursor: null };
      return [];
    });
    render(SapAdminView);
    expect(await screen.findByText("Service unavailable")).toBeInTheDocument();
    expect(screen.getByLabelText("WFNI weights CSV")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Save WFNI weights" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Retry loading weights" })).toBeInTheDocument();
  });
});


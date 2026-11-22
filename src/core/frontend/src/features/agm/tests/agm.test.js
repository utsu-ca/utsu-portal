import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import AgmAdminView from "../../../../../../agm/frontend/views/AgmAdminView.svelte";
import AgmView from "../../../../../../agm/frontend/views/AgmView.svelte";
import AgmIdQueue from "../../../../../../agm/frontend/views/AgmIdQueue.svelte";

vi.mock("@utsu/portal", async importOriginal => ({ ...(await importOriginal()), api: vi.fn(), downloadReport: vi.fn() }));
import { api } from "@utsu/portal";

const called = path => api.mock.calls.filter(([p]) => p === path);

afterEach(() => { cleanup(); vi.useRealTimers(); api.mockReset(); vi.restoreAllMocks(); });

it("polls at the latest server interval and stops once Zoom sign-in is unlocked", async () => {
  vi.useFakeTimers();
  api.mockImplementation(async path => {
    if (path === "/agm/invitation") return { registered: true, mode: "online", id_verified: false };
    const attempt = called("/agm/verify").length;
    return attempt < 3
      ? { status: "pending", position: 3 - attempt, queue_length: 2, wait_after: attempt === 1 ? 11 : 8 }
      : { status: "approved" };
  });
  render(AgmView);
  await vi.advanceTimersByTimeAsync(0);
  expect(screen.getByText(/2 of 2 in the queue/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Log in to Zoom" })).not.toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(10999);
  expect(called("/agm/verify")).toHaveLength(1);
  await vi.advanceTimersByTimeAsync(1);
  expect(screen.getByText(/1 of 2 in the queue/)).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(8000);
  expect(screen.getByRole("button", { name: "Log in to Zoom" })).toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(60000);
  expect(called("/agm/verify")).toHaveLength(3);
});

it("shows photos before allowing a reviewer to decide and requires resubmission instructions", async () => {
  const queue = "/admin/agm/verifications";
  api.mockImplementation(async path => {
    if (path === queue) return called(queue).length === 1
      ? [{ id: "c1", position: 1, first_name: "Ada", last_name: "Test", submitted_at: 1700000000, can_review: true }]
      : [];
    if (path.endsWith("/media")) return { face: "data:image/png;base64,AAAA", id_card: "data:image/png;base64,AAAA" };
    return { status: "rejected" };
  });
  render(AgmIdQueue);
  const review = await screen.findByRole("button", { name: "Review photos" });
  expect(screen.queryByRole("button", { name: "Approve Zoom sign-in" })).not.toBeInTheDocument();
  await fireEvent.click(review);
  expect(await screen.findByAltText("Student ID card")).toBeInTheDocument();
  const reject = screen.getByRole("button", { name: "Request new photos" });
  expect(reject).toBeDisabled();
  await fireEvent.input(screen.getByLabelText(/Resubmission instructions/), { target: { value: "Show all four corners." } });
  await fireEvent.click(reject);
  expect(await screen.findByText("Nothing pending.")).toBeInTheDocument();
  expect(screen.queryByAltText("Student ID card")).not.toBeInTheDocument();
  expect(called(`${queue}/c1/decision`)[0][1].body).toEqual({ approve: false, note: "Show all four corners." });
});

describe("AGM administration", () => {
  it("lists registrations and checks the roster, discarding an answer for edited inputs", async () => {
    api.mockImplementation(async path => {
      if (path.startsWith("/admin/agm/rsvps")) return { items: [{ id: "1", uuid: "u1", name: "Ada Lovelace",
        attendance: "Online", submission_status: "submitted", eligible: true, roster_reason: "matched",
        checked_in: false }], next_cursor: null };
      if (path === "/admin/agm/verify") return { verified: true, reason: "matched" };
      return {};
    });
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

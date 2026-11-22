import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/svelte";
import AccountView from "./AccountView.svelte";
import { api } from "../lib/api.js";
import { nav } from "../lib/nav.svelte.js";
import { session } from "../lib/session.svelte.js";

vi.mock("../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));

const user = {
  uuid: "test-id", email: "student@example.test", first_name: "Ada", last_name: "Lovelace",
  preferred_name: "Ada", student_number: "5678"
};
afterEach(() => {
  cleanup();
  session.me = null;
  api.mockReset();
});

it("offers editable names, routes legal-name edits to review, and displays the recovery email", async () => {
  api.mockResolvedValue({ user, status: "verified", backup_email: "ada@example.test" });
  render(AccountView);
  expect(await screen.findByLabelText("Account ID")).toHaveValue("test-id");
  expect(screen.getByLabelText("Verified recovery email")).toHaveValue("ada@example.test");
  expect(screen.getByLabelText("New recovery email")).toHaveAttribute("type", "email");
  const number = screen.getByLabelText("Confirm student number for roster matching");
  expect(number).toHaveAccessibleDescription(/does not change your student number/);
  await fireEvent.input(number, { target: { value: "1234567890" } });
  await fireEvent.click(screen.getByRole("button", { name: "Confirm student number" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/me/roster-number", {
    method: "POST", body: { student_number: "1234567890" }
  }));
  await fireEvent.input(screen.getByLabelText("Legal last name"), { target: { value: "Byron" } });
  await fireEvent.click(screen.getByRole("button", { name: /Request a legal name/ }));
  expect(nav.name).toBe("recovery");
  expect(nav.props).toEqual({ identifier: user.email, legalFirst: "Ada", legalLast: "Byron" });
});

it("uploads both verification photos with the challenge without changing the registered name", async () => {
  api.mockImplementation(async path => {
    if (path === "/me") return { user, status: "pending_review" };
    if (path === "/verification/challenge") return { phrase: "ABC123", challenge: "test-challenge" };
    return { status: "pending" };
  });
  render(AccountView);
  await fireEvent.input(await screen.findByLabelText("Legal last name"), { target: { value: "Byron" } });
  await fireEvent.input(screen.getByLabelText(/Division codes/), { target: { value: "UTSG" } });
  await fireEvent.click(screen.getByRole("button", { name: "Upload photos instead" }));
  expect(await screen.findByText("ABC123")).toBeInTheDocument();
  for (const label of ["face", "ID card"]) {
    const input = screen.getByLabelText(`Upload ${label} photo`);
    await fireEvent.change(input, { target: { files: [new File(["photo"], "photo.png", { type: "image/png" })] } });
    await screen.findByAltText(`${label} photo preview`);
  }
  await fireEvent.click(screen.getByRole("button", { name: "Submit for review" }));
  await waitFor(() => expect(api).toHaveBeenCalledWith("/verification", { method: "POST", body: {
    first_name: "Ada", last_name: "Lovelace", division_codes: ["UTSG"], challenge: "test-challenge",
    face: "data:image/png;base64,cGhvdG8=", id_card: "data:image/png;base64,cGhvdG8="
  } }));
});

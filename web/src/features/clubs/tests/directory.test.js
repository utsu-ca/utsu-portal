import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import ClubDirectoryView from "../views/ClubDirectoryView.svelte";

vi.mock("../../../lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
import { api } from "../../../lib/api.js";

afterEach(() => { cleanup(); api.mockReset(); });

it("shows public clubs and filters by name, category or description", async () => {
  api.mockResolvedValue([
    { id: "1", name: "Chess Club", category: "Games", description: "Weekly matches" },
    { id: "2", name: "Art Club", category: "Arts", description: "Watercolour classes" },
  ]);
  render(ClubDirectoryView);
  expect(await screen.findByRole("heading", { name: "Chess Club" })).toBeInTheDocument();
  const search = screen.getByLabelText("Search clubs");
  await fireEvent.input(search, { target: { value: "WATERCOLOUR" } });
  expect(screen.getByRole("heading", { name: "Art Club" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Chess Club" })).not.toBeInTheDocument();
  await fireEvent.input(search, { target: { value: "games" } });
  expect(screen.getByRole("heading", { name: "Chess Club" })).toBeInTheDocument();
  await fireEvent.input(search, { target: { value: "not found" } });
  expect(screen.getByText("No clubs match your search.")).toBeInTheDocument();
});

it("shows loading failures instead of an empty directory and can retry", async () => {
  api.mockRejectedValueOnce(new Error("Directory unavailable")).mockResolvedValueOnce([]);
  render(ClubDirectoryView);
  expect(await screen.findByText("Directory unavailable")).toBeInTheDocument();
  expect(screen.queryByText("No approved clubs are listed yet.")).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("No approved clubs are listed yet.")).toBeInTheDocument();
});

import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/svelte";
import ClubDirectoryView from "../../../../../../clubs/frontend/views/ClubDirectoryView.svelte";

const { apiMock, apiAllPagesMock } = vi.hoisted(() => ({ apiMock: vi.fn(), apiAllPagesMock: vi.fn() }));
vi.mock("@utsu/portal", async importOriginal => ({
  ...(await importOriginal()), api: apiMock, apiAllPages: apiAllPagesMock,
}));
import { api, apiAllPages } from "@utsu/portal";

beforeEach(() => {
  apiAllPages.mockImplementation(async (path, collection = "items") => {
    const page = await api(path);
    const items = Array.isArray(page?.[collection]) ? page[collection] : [];
    return collection === "items" ? items : { ...page, [collection]: items, next_cursor: null };
  });
});
afterEach(() => { cleanup(); api.mockReset(); apiAllPages.mockReset(); });

it("shows public clubs and filters by name, category or description", async () => {
  api.mockResolvedValue({ items: [
    { id: "1", name: "Chess Club", category: "Games", description: "Weekly matches" },
    { id: "2", name: "Art Club", category: "Arts", description: "Watercolour classes" },
  ], next_cursor: null });
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
  api.mockRejectedValueOnce(new Error("Directory unavailable"))
    .mockResolvedValueOnce({ items: [], next_cursor: null });
  render(ClubDirectoryView);
  expect(await screen.findByText("Directory unavailable")).toBeInTheDocument();
  expect(screen.queryByText("No approved clubs are listed yet.")).not.toBeInTheDocument();
  await fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  expect(await screen.findByText("No approved clubs are listed yet.")).toBeInTheDocument();
});

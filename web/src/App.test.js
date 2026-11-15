import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import App from "./App.svelte";
import { go } from "./lib/nav.svelte.js";
import { session, setAdminMode } from "./lib/session.svelte.js";
import { layout } from "./lib/layout.svelte.js";

vi.mock("./lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
import { api } from "./lib/api.js";

const member = { status: "verified", roles: [], user: { uuid: "u1", first_name: "Ada", last_name: "L", email: "a@x.ca", student_number: "7890", preferred_name: "Ada" } };
const staff = { ...member, roles: ["sap_admin", "senate_admin"] };

function serve(me) {
  api.mockImplementation(async path => {
    if (path === "/me") return me;
    if (path === "/sap/banking") return { status: "on_file" };
    if (path === "/senate/current") return null;
    return {};
  });
}
const menu = () => screen.getByRole("navigation", { name: "Main" });

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  layout.collapsed = false;
  layout.mobileOpen = false;
  layout.folded = {};
  session.token = null;
  session.me = null;
  session.banking = null;
  setAdminMode(false);
  go("home");
});
afterEach(() => { cleanup(); api.mockReset(); });

describe("signed out", () => {
  it("shows the public menu, the home page and a copyright footer", async () => {
    render(App);
    expect(within(menu()).getByRole("button", { name: "Home" })).toBeInTheDocument();
    for (const name of ["Student Aid", "Bursaries", "Scholarships", "Clubs", "Finances", "Registration", "AGM", "Senate", "Profile"])
      expect(within(menu()).getByRole("button", { name })).toBeInTheDocument();
    expect(await screen.findByText(/Learn more about the/)).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toHaveTextContent(`© ${new Date().getFullYear()}`);
    expect(screen.queryByRole("button", { name: /admin mode/i })).not.toBeInTheDocument();
  });

  it("opens a program's public page, with a sign-in prompt instead of actions", async () => {
    render(App);
    await fireEvent.click(within(menu()).getByRole("button", { name: "Bursaries" }));
    expect(await screen.findByRole("heading", { name: "Student Aid Bursaries" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Eligibility" })).toBeInTheDocument();
    expect(screen.getByText("Sign in to apply or manage your records.")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Apply for a bursary" })).not.toBeInTheDocument();
  });

  it("shows scholarships as coming soon", async () => {
    render(App);
    await fireEvent.click(within(menu()).getByRole("button", { name: "Scholarships" }));
    expect(await screen.findByText("Coming soon")).toBeInTheDocument();
    expect(screen.queryByText("Your actions")).not.toBeInTheDocument();
  });
});

describe("collapsed rail", () => {
  it("keeps one icon per main category and marks the one being read", async () => {
    layout.collapsed = true;
    render(App);
    await fireEvent.click(within(menu()).getByTitle("AGM"));
    await screen.findByRole("heading", { name: "Annual General Meeting" });
    const icons = [...menu().querySelectorAll(".side-icon")].map(i => i.textContent.codePointAt(0).toString(16));
    expect(icons).toEqual(["1f3e0", "1f393", "1f465", "1f5f3", "1f3db", "1f464"]);
    expect(within(menu()).getByTitle("AGM")).toHaveAttribute("data-active");
    expect(within(menu()).getByTitle("Clubs")).not.toHaveAttribute("data-active");
  });

  it("opens the menu when a group icon is chosen, so a page can be picked", async () => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }));  // a desktop-sized screen
    layout.collapsed = true;
    render(App);
    await fireEvent.click(within(menu()).getByTitle("Student Aid"));
    expect(layout.collapsed).toBe(false);
    vi.unstubAllGlobals();
  });
});

describe("section sidecar", () => {
  it("shows the section's pages and the parts of the page for wide screens", async () => {
    render(App);
    await fireEvent.click(within(menu()).getByRole("button", { name: "Bursaries" }));
    const side = await screen.findByRole("complementary", { name: "In this section" });
    expect(within(side).getByRole("button", { name: "Eligibility" })).toBeInTheDocument();
    expect(within(side).getByRole("button", { name: "Scholarships" })).toBeInTheDocument();
    expect(within(side).getByRole("button", { name: "Bursaries" })).toHaveAttribute("aria-current", "page");
    await fireEvent.click(within(side).getByRole("button", { name: "Scholarships" }));
    expect(await screen.findByText("Coming soon")).toBeInTheDocument();
  });

  it("is absent on the home page", async () => {
    render(App);
    await screen.findByText(/Learn more about the/);
    expect(screen.queryByRole("complementary", { name: "In this section" })).not.toBeInTheDocument();
  });
});

describe("menu behaviour", () => {
  it("collapses and expands, and remembers the choice", async () => {
    render(App);
    const toggle = screen.getAllByRole("button", { name: /menu/i }).find(b => b.getAttribute("aria-controls") === "side-nav");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    await fireEvent.click(toggle);
    expect(layout.collapsed || layout.mobileOpen).toBe(true);
  });

  it("folds a group away", async () => {
    render(App);
    await fireEvent.click(screen.getByRole("button", { name: "Collapse Student Aid" }));
    expect(within(menu()).queryByRole("button", { name: "Bursaries" })).not.toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Expand Student Aid" }));
    expect(within(menu()).getByRole("button", { name: "Bursaries" })).toBeInTheDocument();
  });
});

describe("signed in", () => {
  it("adds the member's buttons under each program, and signs out from the profile group", async () => {
    sessionStorage.setItem("token", "t");
    session.token = "t";
    serve(member);
    render(App);
    expect(await within(menu()).findByRole("button", { name: "Account" })).toBeInTheDocument();
    await fireEvent.click(within(menu()).getByRole("button", { name: "Bursaries" }));
    const actions = (await screen.findByRole("heading", { name: "Your actions" })).closest(".card");
    expect(within(actions).getByRole("button", { name: "Apply for a bursary" })).toBeInTheDocument();
    expect(within(actions).getByRole("button", { name: "Banking information" })).toBeInTheDocument();
    expect(within(actions).queryByRole("button", { name: "Administer applications" })).not.toBeInTheDocument();
    await fireEvent.click(within(menu()).getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(session.token).toBeNull());
    expect(within(menu()).queryByRole("button", { name: "Account" })).not.toBeInTheDocument();
  });
});

describe("admin mode", () => {
  it("is offered only to staff, reveals staff tools and recolours the page; leaving hides them again", async () => {
    sessionStorage.setItem("token", "t");
    session.token = "t";
    serve(staff);
    render(App);
    const enter = await screen.findByRole("button", { name: "Enter admin mode" });
    expect(within(menu()).queryByRole("button", { name: "Administer applications" })).not.toBeInTheDocument();
    expect(document.documentElement.dataset.mode).not.toBe("admin");

    await fireEvent.click(enter);
    expect(document.documentElement.dataset.mode).toBe("admin");
    expect(await screen.findByText(/Admin mode: staff tools are shown/)).toBeInTheDocument();
    expect(within(menu()).getByRole("button", { name: "Administer applications" })).toBeInTheDocument();
    expect(within(menu()).getByRole("button", { name: "Administer seating" })).toBeInTheDocument();
    expect(within(menu()).queryByRole("button", { name: "Roles" })).not.toBeInTheDocument();  // not an admin

    await fireEvent.click(screen.getByRole("button", { name: "Exit admin mode" }));
    expect(document.documentElement.dataset.mode).toBe("user");
    expect(within(menu()).queryByRole("button", { name: "Administer applications" })).not.toBeInTheDocument();
  });

  it("is not offered to ordinary members", async () => {
    sessionStorage.setItem("token", "t");
    session.token = "t";
    serve(member);
    render(App);
    await within(menu()).findByRole("button", { name: "Account" });
    expect(screen.queryByRole("button", { name: /admin mode/i })).not.toBeInTheDocument();
  });

  it("is switched off if a stale session claims it for a non-staff account", async () => {
    sessionStorage.setItem("token", "t");
    session.token = "t";
    setAdminMode(true);
    serve(member);
    render(App);
    await within(menu()).findByRole("button", { name: "Account" });
    await waitFor(() => expect(session.adminMode).toBe(false));
  });
});

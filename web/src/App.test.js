import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/svelte";
import App from "./App.svelte";
import { go, nav } from "./lib/nav.svelte.js";
import { session, setAdminMode } from "./lib/session.svelte.js";
import { layout } from "./lib/layout.svelte.js";
import { applyTheme, theme } from "./lib/theme.svelte.js";
import { bankingStatus, clearBankingStatus } from "./features/sabp/state/banking.svelte.js";

vi.mock("./lib/api.js", async importOriginal => ({ ...(await importOriginal()), api: vi.fn() }));
import { api } from "./lib/api.js";

const member = { status: "verified", roles: [], user: { uuid: "u1", first_name: "Ada", last_name: "L", email: "a@x.ca", student_number: "7890", preferred_name: "Ada" } };
const staff = { ...member, roles: ["sap_admin", "senate_admin"] };

function serve(me) {
  api.mockImplementation(async path => {
    if (path === "/content") return { pages: {} };
    if (path === "/plugins") return { enabled: [] };
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
  history.replaceState(null, "", "/");
  layout.collapsed = false;
  layout.mobileOpen = false;
  layout.folded = {};
  session.token = null;
  session.me = null;
  clearBankingStatus();
  setAdminMode(false);
  go("home");
  api.mockImplementation(async path => path === "/content" ? { pages: {} } : { enabled: [] });
});
afterEach(() => { cleanup(); api.mockReset(); });

describe("signed out", () => {
  it("offers staff Google sign-in after the email-code and recovery options", async () => {
    api.mockImplementation(async path => {
      if (path === "/auth/google") return { enabled: true };
      if (path === "/content") return { pages: {} };
      return { enabled: [] };
    });
    render(App);
    await screen.findByText(/Learn more about the/);
    go("auth");
    const google = await screen.findByRole("button", { name: "Continue with Google" }, { timeout: 5000 });
    const otp = screen.getByRole("button", { name: "Email me a code" });
    const recovery = screen.getByRole("button", { name: "Account recovery" });
    expect(otp.compareDocumentPosition(google) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(recovery.compareDocumentPosition(google) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole("region", { name: "Staff? Log in here!" })).toContainElement(google);
    await waitFor(() => expect(google.disabled).toBe(false));
  });

  it("shows a disabled SSO box when Google sign-in is not configured", async () => {
    api.mockImplementation(async path => {
      if (path === "/auth/google") return { enabled: false };
      if (path === "/content") return { pages: {} };
      return { enabled: [] };
    });
    render(App);
    await screen.findByText(/Learn more about the/);
    go("auth");
    await screen.findByText(/Single sign-on is not set up/);
    expect(screen.getByRole("button", { name: "Continue with Google" }).disabled).toBe(true);
  });

  it.each(["auth", "profile"])("signs in by email from %s after requesting a code", async view => {
    api.mockImplementation(async (path, opts) => {
      if (path === "/content") return { pages: {} };
      if (path === "/auth/login-email") return {};
      if (path === "/auth/token-email") return { token: "t1" };
      if (path === "/me") return member;
      return { enabled: [] };
    });
    render(App);
    await screen.findByText(/Learn more about the/);
    go(view);
    await fireEvent.input(await screen.findByLabelText("Primary email"), { target: { value: "a@x.ca" } });
    expect(screen.queryByLabelText("Email code")).toBeNull();
    await fireEvent.click(screen.getByRole("button", { name: "Email me a code" }));
    expect(api).toHaveBeenCalledWith("/auth/login-email", { method: "POST", body: { email: "a@x.ca" } });
    const code = await screen.findByLabelText("Email code");
    await waitFor(() => expect(document.activeElement).toBe(code));
    await fireEvent.input(code, { target: { value: "123456" } });
    await fireEvent.click(screen.getByRole("button", { name: "Sign in with code" }));
    await waitFor(() => expect(session.me).toEqual(member));
    expect(api).toHaveBeenCalledWith("/auth/token-email", { method: "POST", body: { email: "a@x.ca", code: "123456" } });
    expect(screen.queryByText("onsignin is not a function")).toBeNull();
  });

  it("requests and exchanges a backup code from account recovery", async () => {
    serve(member);
    const normalResponse = api.getMockImplementation();
    api.mockImplementation(async (path, opts) => {
      if (path === "/recovery/backup/start") return {};
      if (path === "/recovery/backup/token") return { token: "backup-session" };
      return normalResponse(path, opts);
    });
    render(App);
    await screen.findByText(/Learn more about the/);
    go("recovery");
    await fireEvent.input(await screen.findByLabelText("Primary email or username"), { target: { value: "ada" } });
    await fireEvent.input(screen.getByLabelText("Backup email"), { target: { value: "backup@example.ca" } });
    await fireEvent.click(screen.getByRole("button", { name: "Send code to backup email" }));
    expect(api).toHaveBeenCalledWith("/recovery/backup/start", { method: "POST", body: { username: "ada", email: "backup@example.ca" } });
    await fireEvent.input(screen.getByLabelText("Backup sign-in code"), { target: { value: "123456" } });
    await fireEvent.click(screen.getByRole("button", { name: "Sign in with backup code" }));
    await waitFor(() => expect(session.me).toEqual(member));
    expect(api).toHaveBeenCalledWith("/recovery/backup/token", { method: "POST", body: { username: "ada", code: "123456" } });
    expect(sessionStorage.getItem("token")).toBe("backup-session");
  });

  it("registers without a username and verifies the primary email", async () => {
    api.mockImplementation(async path => {
      if (path === "/content") return { pages: {} };
      if (path === "/auth/registration-challenge") return { local_bypass: true, site_key: null };
      return { enabled: [] };
    });
    render(App);
    await screen.findByText(/Learn more about the/);
    go("register");
    await fireEvent.input(await screen.findByLabelText("Email"), { target: { value: "a@x.ca" } });
    await fireEvent.input(screen.getByLabelText("Legal first name"), { target: { value: "Ada" } });
    await fireEvent.input(screen.getByLabelText("Legal last name"), { target: { value: "L" } });
    await fireEvent.input(screen.getByLabelText("Student number"), { target: { value: "1234567890" } });
    await fireEvent.click(screen.getByRole("button", { name: "Register" }));
    await fireEvent.input(await screen.findByLabelText("Email code"), { target: { value: "123456" } });
    await fireEvent.click(screen.getByRole("button", { name: "Verify email" }));
    expect(api).toHaveBeenCalledWith("/auth/register", expect.objectContaining({ body: expect.objectContaining({ username: "", email: "a@x.ca" }) }));
    expect(api).toHaveBeenCalledWith("/auth/verify-email", { method: "POST", body: { email: "a@x.ca", code: "123456" } });
  });

  it("requires a Turnstile token and resets the widget after a failed registration", async () => {
    let callbacks;
    const widget = {
      render: vi.fn((_element, options) => { callbacks = options; return "widget"; }),
      reset: vi.fn(),
      remove: vi.fn(),
    };
    vi.stubGlobal("turnstile", widget);
    try {
      api.mockImplementation(async (path, opts) => {
        if (path === "/content") return { pages: {} };
        if (path === "/auth/registration-challenge") return { site_key: "site", local_bypass: false };
        if (path === "/auth/register") throw new Error("Try again");
        return { enabled: [] };
      });
      render(App);
      await screen.findByText(/Learn more about the/);
      go("register");
      await vi.waitFor(() => expect(callbacks).toBeDefined());
      const button = screen.getByRole("button", { name: "Register" });
      expect(button).toBeDisabled();
      callbacks.callback("challenge-token");
      await vi.waitFor(() => expect(button).toBeEnabled());
      await fireEvent.click(button);
      expect(await screen.findByText("Try again")).toBeInTheDocument();
      expect(widget.reset).toHaveBeenCalledWith("widget");
      expect(button).toBeDisabled();
      expect(api).toHaveBeenCalledWith("/auth/register", expect.objectContaining({ body: expect.objectContaining({ challenge_token: "challenge-token" }) }));
    } finally {
      cleanup();
      vi.unstubAllGlobals();
    }
  });

  it("completes a Google return in the same tab and removes the code from the URL", async () => {
    serve(staff);
    const normalResponse = api.getMockImplementation();
    api.mockImplementation(async (path, opts) => {
      if (path === "/auth/google/complete") {
        expect(location.search).toBe("");
        expect(opts.body).toEqual({ code: "authorization-code", state: "google.state", verifier: "verifier" });
        return { token: "staff-session" };
      }
      return normalResponse(path);
    });
    sessionStorage.setItem("google-staff-login", JSON.stringify({ state: "google.state", verifier: "verifier" }));
    history.replaceState(null, "", "/?code=authorization-code&state=google.state");
    render(App);
    await waitFor(() => expect(session.me).toEqual(staff));
    expect(sessionStorage.getItem("token")).toBe("staff-session");
    expect(sessionStorage.getItem("google-staff-login")).toBeNull();
  });

  it("shows the public menu, the home page and a copyright footer", async () => {
    render(App);
    expect(within(menu()).getByRole("button", { name: "Home" })).toBeInTheDocument();
    for (const name of ["Student Aid", "Bursaries", "Scholarships", "Clubs", "Finances", "Registration", "AGM", "Senate", "Profile"])
      expect(within(menu()).getByRole("button", { name })).toBeInTheDocument();
    expect(await screen.findByText(/Learn more about the/)).toBeInTheDocument();
    expect(screen.getByRole("contentinfo")).toHaveTextContent(`© ${new Date().getFullYear()}`);
    expect(screen.queryByRole("button", { name: /admin mode/i })).not.toBeInTheDocument();
  });

  it("shows the running server's version and commit next to the copyright", async () => {
    api.mockImplementation(async path => {
      if (path === "/content") return { pages: {} };
      if (path === "/version") return { version: "0.1.0", commit: "3d1f3c5abcde" };
      return { enabled: [] };
    });
    render(App);
    await waitFor(() => expect(screen.getByRole("contentinfo")).toHaveTextContent(/© \d{4} .* · v0\.1\.0 \(3d1f3c5\)$/));
  });

  it("turns red and renames the service while the local development admin exists on the server", async () => {
    const before = theme.name;
    api.mockImplementation(async path => {
      if (path === "/content") return { pages: {} };
      if (path === "/version") return { version: "0.1.0", commit: null, dev_local: true };
      return { enabled: [] };
    });
    try {
      render(App);
      await waitFor(() => expect(document.documentElement.dataset.env).toBe("dev-local"));
      expect(await screen.findAllByText("ACTIVE DEV LOCAL, DO NOT USE")).not.toHaveLength(0);
      applyTheme({ name: "Rebranded" });
      expect(theme.name).toBe("ACTIVE DEV LOCAL, DO NOT USE");
    } finally {
      theme.devLocal = false;
      theme.name = before;
      delete document.documentElement.dataset.env;
    }
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
  it("previews public copy as text and updates the site after publishing", async () => {
    session.token = "t";
    setAdminMode(true);
    api.mockImplementation(async (path, opts) => {
      if (path === "/me") return { ...member, roles: ["admin"] };
      if (path === "/plugins") return { enabled: [] };
      if (path === "/content") return { pages: {} };
      if (path === "/admin/content/bursaries") return { rev: 1, copy: opts.body.copy };
      return {};
    });
    render(App);
    await fireEvent.click(await within(menu()).findByRole("button", { name: "Page content" }));
    await fireEvent.change(await screen.findByLabelText("Page"), { target: { value: "bursaries" } });
    const literal = "<b>Applications open soon.</b>";
    await fireEvent.input(screen.getByLabelText("Short blurb (optional)"), { target: { value: literal } });
    expect(within(screen.getByRole("region", { name: "Public copy preview" })).getByText(literal)).toBeInTheDocument();
    expect(api.mock.calls.some(([, opts]) => opts?.method === "PUT")).toBe(false);
    await fireEvent.click(screen.getByRole("button", { name: "Publish to site" }));
    await screen.findByText(/Published\. Visitors/);
    expect(api).toHaveBeenCalledWith("/admin/content/bursaries", {
      method: "PUT", body: { base_rev: 0, copy: expect.objectContaining({ tagline: literal }) },
    });
    await fireEvent.click(within(menu()).getByRole("button", { name: "Bursaries" }));
    expect(await screen.findByText(literal)).toBeInTheDocument();
    await fireEvent.click(within(menu()).getByRole("button", { name: "Home" }));
    expect(await screen.findByText(literal)).toBeInTheDocument();
  });

  it("keeps the page usable and offers a retry when program discovery fails", async () => {
    session.token = "t";
    let attempts = 0;
    api.mockImplementation(async path => {
      if (path === "/content") return { pages: {} };
      if (path === "/me") return member;
      if (path === "/plugins") {
        if (++attempts === 1) throw new Error("Program service unavailable.");
        return { enabled: ["discussion"] };
      }
      return {};
    });
    render(App);
    expect(await screen.findByText("Program service unavailable.")).toBeInTheDocument();
    expect(await screen.findByText(/Learn more about the/)).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Retry program links" }));
    expect(await within(menu()).findByRole("button", { name: "Discussions" })).toBeInTheDocument();
    expect(screen.queryByText("Program service unavailable.")).not.toBeInTheDocument();
  });

  it("keeps the portal usable after a banking failure and offers a working retry", async () => {
    sessionStorage.setItem("token", "t");
    session.token = "t";
    let bankingCalls = 0;
    api.mockImplementation(async path => {
      if (path === "/content") return { pages: {} };
      if (path === "/plugins") return { enabled: [] };
      if (path === "/me") return member;
      if (path === "/sap/banking") {
        if (++bankingCalls === 1) throw new Error("Banking service unavailable.");
        return { status: "reverify_requested" };
      }
      return {};
    });
    render(App);
    expect(await screen.findByText(/Learn more about the/)).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Banking service unavailable.");
    expect(screen.getByText("We could not load your banking status. Retry or open banking information.")).toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Retry banking status" }));
    expect(await screen.findByText("Action needed: we could not verify your banking information.")).toBeInTheDocument();
    expect(screen.queryByText("Banking service unavailable.")).not.toBeInTheDocument();
    await fireEvent.click(screen.getByRole("button", { name: "Verify banking information" }));
    await waitFor(() => expect(nav.name).toBe("banking"));
  });

  it("completes the emailed banking route without waiting for the status request", async () => {
    sessionStorage.setItem("token", "t");
    session.token = "t";
    history.replaceState(null, "", "/#sap-banking=tok123");
    const resolveBanking = [];
    api.mockImplementation(path => {
      if (path === "/content") return Promise.resolve({ pages: {} });
      if (path === "/plugins") return Promise.resolve({ enabled: [] });
      if (path === "/me") return Promise.resolve(member);
      if (path === "/sap/banking") return new Promise(resolve => { resolveBanking.push(resolve); });
      return Promise.resolve({});
    });
    render(App);
    await waitFor(() => expect(resolveBanking.length).toBeGreaterThan(0));
    expect(resolveBanking).toHaveLength(1);
    expect(nav.name).toBe("banking");
    resolveBanking.forEach(resolve => resolve({ status: "reverify_requested", expires_at: 2e9 }));
    await waitFor(() => expect(bankingStatus.status?.status).toBe("reverify_requested"));
    expect(await screen.findByText(/Re-enter your details below and upload/)).toBeInTheDocument();
  });

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
    await waitFor(() => expect(bankingStatus.status).toBeNull());
    expect(within(menu()).queryByRole("button", { name: "Account" })).not.toBeInTheDocument();
  });

  it("shows the account identifier on the account page", async () => {
    sessionStorage.setItem("token", "t");
    session.token = "t";
    serve(member);
    render(App);
    await within(menu()).findByRole("button", { name: "Account" });
    await fireEvent.click(within(menu()).getByRole("button", { name: "Account" }));
    expect(await screen.findByText("Account ID: u1")).toBeInTheDocument();
    await fireEvent.input(screen.getByLabelText("Public username (optional)"), { target: { value: "alias" } });
    await fireEvent.click(screen.getByRole("button", { name: "Save username" }));
    expect(api).toHaveBeenCalledWith("/me/username", { method: "PATCH", body: { username: "alias" } });
  });

  it("does not restore banking state from a response that arrives after sign-out", async () => {
    sessionStorage.setItem("token", "t");
    session.token = "t";
    let resolveBanking;
    api.mockImplementation(path => {
      if (path === "/content") return Promise.resolve({ pages: {} });
      if (path === "/plugins") return Promise.resolve({ enabled: [] });
      if (path === "/me") return Promise.resolve(member);
      if (path === "/sap/banking") return new Promise(resolve => { resolveBanking = resolve; });
      return Promise.resolve({});
    });
    render(App);
    await within(menu()).findByRole("button", { name: "Account" });
    await waitFor(() => expect(resolveBanking).toBeDefined());
    await fireEvent.click(within(menu()).getByRole("button", { name: "Sign out" }));
    await waitFor(() => expect(session.token).toBeNull());
    expect(bankingStatus.status).toBeNull();
    resolveBanking({ status: "reverify_requested" });
    await waitFor(() => expect(bankingStatus.status).toBeNull());
    expect(screen.queryByText(/Action needed:/)).not.toBeInTheDocument();
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

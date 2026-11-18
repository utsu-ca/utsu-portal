import { describe, expect, it } from "vitest";
import { buildMenu, containsCurrent, findNode, isCurrent, sectionFor } from "./menu.js";

const labels = nodes => nodes.map(n => n.label);
const actions = (menu, id) => labels(findNode(menu, n => n.id === id).items);

describe("menu structure", () => {
  it("lists the public programs in the requested order, for everyone", () => {
    const menu = buildMenu();
    expect(labels(menu)).toEqual(["Home", "Student Aid", "Clubs", "AGM", "Senate", "Profile"]);
    expect(labels(findNode(menu, n => n.id === "aid").items)).toEqual(["Bursaries", "Scholarships"]);
    expect(labels(findNode(menu, n => n.id === "clubs").items)).toEqual(["Finances", "Registration"]);
    expect(findNode(menu, n => n.id === "profile").items).toEqual([]);
  });

  it("offers no program actions while signed out", () => {
    const menu = buildMenu();
    for (const id of ["bursaries", "club-finances", "club-registration", "agm", "senate"]) expect(actions(menu, id)).toEqual([]);
  });
});

describe("signing in exposes program functions", () => {
  const member = buildMenu({ signedIn: true, verified: true });

  it("adds the member's own actions", () => {
    expect(actions(member, "bursaries")).toEqual(["Apply for a bursary", "Banking information"]);
    expect(actions(member, "agm")).toEqual(["RSVP for the AGM", "My invitation"]);
    expect(actions(member, "club-registration")).toEqual(["Register or manage a club"]);
    expect(labels(findNode(member, n => n.id === "profile").items)).toEqual(["Account", "My applications", "Sign out"]);
  });

  it("gives unverified accounts only the profile", () => {
    const pending = buildMenu({ signedIn: true, verified: false });
    expect(actions(pending, "bursaries")).toEqual([]);
    expect(findNode(pending, n => n.id === "profile").items).not.toEqual([]);
  });

  it("never shows staff tools without the role", () => {
    const text = JSON.stringify(member);
    for (const word of ["Administer", "Review applications", "Finance portal", "Administration"]) expect(text).not.toContain(word);
  });

  it("shows each staff tool only for its role", () => {
    const has = granted => role => granted.includes(role);
    const finance = buildMenu({ signedIn: true, verified: true, has: has(["sap_finance"]) });
    expect(actions(finance, "bursaries")).toContain("Finance portal (banking)");
    expect(actions(finance, "bursaries")).not.toContain("Administer applications");
    const admin = buildMenu({ signedIn: true, verified: true, has: () => true });
    expect(actions(admin, "bursaries")).toEqual(expect.arrayContaining(["Review applications", "Administer applications", "Finance portal (banking)"]));
    expect(actions(admin, "senate")).toContain("Administer seating");
    expect(actions(admin, "club-registration")).toContain("Review registrations");
    expect(actions(admin, "club-finances")).toContain("Approve expenses and grants");
    expect(labels(findNode(admin, n => n.id === "administration").items)).toEqual(["Forms", "Identity checks", "Roles", "Student roster", "Page content"]);
  });
});

describe("opt-in plug-ins", () => {
  const on = ids => id => ids.includes(id);

  it("lists Discussions only where the deployment runs it, for verified members", () => {
    expect(findNode(buildMenu({ signedIn: true, verified: true }), n => n.id === "discussions")).toBeUndefined();
    expect(findNode(buildMenu({ signedIn: true, verified: false, enabled: on(["discussion"]) }), n => n.id === "discussions")).toBeUndefined();
    const menu = buildMenu({ signedIn: true, verified: true, enabled: on(["discussion"]) });
    expect(labels(menu)).toEqual(["Home", "Student Aid", "Clubs", "AGM", "Senate", "Discussions", "Profile"]);
    expect(findNode(menu, n => n.id === "discussions").icon).toBeTruthy();
  });

  it("adds the ballots tool for elections officials only when voting runs", () => {
    const official = { signedIn: true, verified: true, has: role => role === "elections_admin" };
    expect(actions(buildMenu({ ...official, enabled: on(["discussion"]) }), "discussions")).toEqual([]);
    expect(actions(buildMenu({ ...official, enabled: on(["discussion", "voting"]) }), "discussions")).toEqual(["Ballots and polls"]);
    expect(actions(buildMenu({ signedIn: true, verified: true, enabled: on(["discussion", "voting"]) }), "discussions")).toEqual([]);
  });

  it("keeps the discussion category selected on boards, topics and ballots", () => {
    const menu = buildMenu({ signedIn: true, verified: true, enabled: on(["discussion", "voting"]), has: () => true });
    const category = findNode(menu, node => node.id === "discussions");
    for (const name of ["discussionBoard", "discussionTopic", "ballot"]) {
      const current = { name, props: { id: "record-1" } };
      expect(containsCurrent(category, current)).toBe(true);
      expect(isCurrent(category, current)).toBe(false);
      expect(sectionFor(menu, current).title).toBe("Discussions");
    }
  });
});

it("recognises the current view, including its props", () => {
  const node = { view: "form", props: { id: "agm_rsvp" } };
  expect(isCurrent(node, { name: "form", props: { id: "agm_rsvp" } })).toBe(true);
  expect(isCurrent(node, { name: "form", props: { id: "other" } })).toBe(false);
  expect(isCurrent({ label: "group" }, { name: "form", props: {} })).toBe(false);
});

describe("icons", () => {
  it("gives every main category an icon", () => {
    const menu = buildMenu({ signedIn: true, verified: true });
    expect(menu.map(n => [n.label, Boolean(n.icon)])).toEqual([
      ["Home", true], ["Student Aid", true], ["Clubs", true], ["AGM", true], ["Senate", true], ["Profile", true]]);
  });

  it("finds the main category that holds the current view, however deep", () => {
    const menu = buildMenu({ signedIn: true, verified: true });
    const clubs = findNode(menu, n => n.id === "clubs");
    expect(containsCurrent(clubs, { name: "program", props: { program: "club-finances" } })).toBe(true);
    expect(containsCurrent(clubs, { name: "program", props: { program: "agm" } })).toBe(false);
    const agm = findNode(menu, n => n.id === "agm");
    expect(containsCurrent(agm, { name: "form", props: { id: "agm_rsvp" } })).toBe(true);
  });
});

describe("section links", () => {
  const current = (name, props = {}) => ({ name, props });

  it("lists the sibling pages of a group, with the current page's actions beneath it", () => {
    const menu = buildMenu({ signedIn: true, verified: true });
    const section = sectionFor(menu, current("program", { program: "bursaries" }));
    expect(section.title).toBe("Student Aid");
    expect(section.sections.map(s => [s.node.label, s.current, s.children.map(c => c.label)])).toEqual([
      ["Bursaries", true, ["Apply for a bursary", "Banking information"]],
      ["Scholarships", false, []],
    ]);
  });

  it("lists a single-page program's actions", () => {
    const menu = buildMenu({ signedIn: true, verified: true });
    const section = sectionFor(menu, current("program", { program: "agm" }));
    expect(section.title).toBe("AGM");
    expect(section.sections[0].children.map(c => c.label)).toEqual(["RSVP for the AGM", "My invitation"]);
  });

  it("has nothing to list for a lone page with no actions", () => {
    expect(sectionFor(buildMenu(), current("program", { program: "agm" }))).toBeNull();
    expect(sectionFor(buildMenu(), current("home"))).toBeNull();
  });
});

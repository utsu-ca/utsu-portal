import { compiledPrograms, menuContributions } from "virtual:portal-programs";

// The portal's navigation, built from who is signed in. Everything public is always listed; signing in (and, for
// staff, holding a role) adds the buttons that expose a program's extra functions beneath it.
//
// A node is {id, label, view?, props?, href?, action?, items?, program?, relatedViews?}:
//   view/props open a portal view, href leaves for another page, action runs a named command ("signOut"),
//   items are children, program names the public page, and relatedViews associates record detail views with a category.

export function buildMenu({ signedIn = false, verified = false, has = () => false, enabled = () => false } = {}) {
  const user = (cond, node) => (cond ? [node] : []);
  const context = { signedIn, verified, has, enabled };
  const compiled = new Set(compiledPrograms);
  const programEnabled = id => compiled.has(id) && enabled(id);
  const administration = [
    ...user(has("payments_finance"), { id: "admin-orders", label: "Payment orders", view: "paymentOrders" }),
    ...user(has("forms_editor"), { id: "admin-forms", label: "Forms", view: "adminForms" }),
    ...user(has("verifier"), { id: "admin-verifications", label: "Identity checks", view: "adminVerifications" }),
    ...user(has("admin"), { id: "admin-roles", label: "Roles", view: "adminRoles" }),
    ...user(has("admin"), { id: "admin-roster", label: "Student roster", view: "adminRoster" }),
    ...user(has("admin"), { id: "admin-content", label: "Page content", view: "adminContent" }),
  ];
  // Discussion and voting are opt-in plug-ins; they are listed only where the deployment runs them.
  const discussionActions = [
    ...user(programEnabled("voting") && has("elections_admin"), { id: "elections-admin", label: "Ballots and polls", view: "electionsAdmin" }),
  ];
  const discussions = programEnabled("discussion") && verified
    ? [{ id: "discussions", label: "Discussions", icon: "\u{1F4AC}", view: "discussions",
      relatedViews: ["discussionBoard", "discussionTopic", "ballot"], items: discussionActions }]
    : [];

  const page = (id, label, program, items = [], icon) => ({ id, label, icon, view: "program", props: { program }, program, items });
  const bursaryActions = menuContributions.sap?.bursaryActions;
  const financeActions = menuContributions.clubs?.financeActions;
  const registrationActions = menuContributions.clubs?.registrationActions;
  const agmActions = menuContributions.agm?.agmActions;
  const senateNavigation = menuContributions.senate?.navigation;
  const externalActions = Object.entries(menuContributions).flatMap(([id, contribution]) => {
    if (id === "senate" || !programEnabled(id) || !contribution.navigation) return [];
    const nodes = contribution.navigation(context);
    if (!Array.isArray(nodes)) throw new Error(`Frontend contribution "${id}" navigation must return an array.`);
    return nodes;
  });

  return [
    { id: "home", label: "Home", icon: "\u{1F3E0}", view: "home" },
    { id: "aid", label: "Student Aid", icon: "\u{1F393}", items: [
      page("bursaries", "Bursaries", "bursaries", programEnabled("sap") && bursaryActions ? bursaryActions(context) : []),
      page("scholarships", "Scholarships", "scholarships"),
    ] },
    { id: "clubs", label: "Clubs", icon: "\u{1F465}", items: [
      ...user(programEnabled("clubs"), { id: "club-directory", label: "Directory", view: "clubDirectory" }),
      page("club-finances", "Finances", "club-finances",
        programEnabled("clubs") && financeActions ? financeActions(context) : []),
      page("club-registration", "Registration", "club-registration",
        programEnabled("clubs") && registrationActions ? registrationActions(context) : []),
    ] },
    page("agm", "AGM", "agm", programEnabled("agm") && agmActions ? agmActions(context) : [], "\u{1F5F3}"),
    page("senate", "Senate", "senate",
      programEnabled("senate") && senateNavigation ? senateNavigation(context) : [], "\u{1F3DB}"),
    ...discussions,
    ...externalActions,
    ...(administration.length ? [{ id: "administration", label: "Administration", items: administration }] : []),
    { id: "profile", label: "Profile", icon: "\u{1F464}", view: "profile", items: signedIn ? [
      { id: "profile-account", label: "Account", view: "account" },
      ...user(verified, { id: "profile-financial", label: "Financial information", view: "banking", props: { shared: true } }),
      ...user(verified, { id: "profile-orders", label: "Orders and payments", view: "orders" }),
      { id: "profile-applications", label: "My applications", view: "status" },
      { id: "profile-signout", label: "Sign out", action: "signOut" },
    ] : [] },
  ];
}

// Depth-first search for the first node matching `test`.
export function findNode(nodes, test) {
  for (const node of nodes) {
    if (test(node)) return node;
    const inner = node.items && findNode(node.items, test);
    if (inner) return inner;
  }
  return undefined;
}

// Whether a node is the view currently shown.
export function isCurrent(node, current) {
  if (!node.view || node.view !== current.name) return false;
  return Object.entries(node.props || {}).every(([key, value]) => current.props?.[key] === value);
}

// Whether a node, or anything beneath it, is the view currently shown.
export function containsCurrent(node, current) {
  return isCurrent(node, current) || !!node.relatedViews?.includes(current.name)
    || (node.items || []).some(child => containsCurrent(child, current));
}

// The links for the "In this section" panel: the main category the current view belongs to, its pages, and (under the
// page being viewed) that page's own actions. Returns null when there is nothing worth listing.
export function sectionFor(menu, current) {
  const top = menu.find(node => containsCurrent(node, current));
  if (!top) return null;
  const entries = top.view ? [top] : top.items || [];
  const sections = entries.map(entry => ({
    node: entry,
    current: isCurrent(entry, current),
    children: containsCurrent(entry, current) ? entry.items || [] : [],
  }));
  const nested = sections.some(section => section.children.length);
  if (sections.length <= 1 && !nested) return null;
  return { title: top.label, sections };
}

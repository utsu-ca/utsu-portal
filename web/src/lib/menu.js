// The portal's navigation, built from who is signed in. Everything public is always listed; signing in (and, for
// staff, holding a role) adds the buttons that expose a program's extra functions beneath it.
//
// A node is {id, label, view?, props?, href?, action?, items?, program?}:
//   view/props open a portal view, href leaves for another page, action runs a named command ("signOut"),
//   items are children, and program names the public page whose "Your actions" card lists those children.

export function buildMenu({ signedIn = false, verified = false, has = () => false } = {}) {
  const user = (cond, node) => (cond ? [node] : []);
  const apply = (id, label, form) => ({ id, label, view: "form", props: { id: form } });

  const bursaryActions = [
    ...user(verified, apply("bursary-apply", "Apply for a bursary", "student_aid_bursary")),
    ...user(verified, { id: "bursary-banking", label: "Banking information", view: "banking" }),
    ...user(has("sap_reviewer"), { id: "bursary-review", label: "Review applications", view: "sapReview" }),
    ...user(has("sap_admin"), { id: "bursary-admin", label: "Administer applications", view: "sapAdmin" }),
    ...user(has("sap_finance"), { id: "bursary-finance", label: "Finance portal (banking)", href: "/static/finance.html" }),
  ];
  const financeActions = [
    ...user(verified, { id: "club-finances-mine", label: "My club's finances", view: "clubFinances" }),
    ...user(has("finance"), { id: "club-finances-admin", label: "Approve expenses and grants", view: "clubFinanceAdmin" }),
  ];
  const registrationActions = [
    ...user(verified, { id: "club-register", label: "Register or manage a club", view: "clubRegistration" }),
    ...user(has("clubs"), { id: "club-review", label: "Review registrations", view: "clubAdmin" }),
  ];
  const agmActions = [
    ...user(verified, apply("agm-rsvp", "RSVP for the AGM", "agm_rsvp")),
    ...user(verified, { id: "agm-invitation", label: "My invitation", view: "agm" }),
    ...user(has("agm_admin"), { id: "agm-admin", label: "Registrations and roster", view: "agmAdmin" }),
    ...user(has("verifier"), { id: "agm-verifications", label: "Online ID checks", view: "adminVerifications", props: { kind: "agm" } }),
    ...user(has("checkin"), { id: "agm-checkin", label: "Check-in desk", href: "/static/checkin.html" }),
  ];
  const senateActions = [
    ...user(verified, apply("senate-apply", "Apply for a seat", "senate_application")),
    ...user(verified, { id: "senate-mine", label: "My application", view: "mySenate" }),
    ...user(has("senate_admin"), { id: "senate-admin", label: "Administer seating", view: "senateAdmin" }),
  ];
  const administration = [
    ...user(has("forms_editor"), { id: "admin-forms", label: "Forms", view: "adminForms" }),
    ...user(has("verifier"), { id: "admin-verifications", label: "Identity checks", view: "adminVerifications" }),
    ...user(has("admin"), { id: "admin-roles", label: "Roles", view: "adminRoles" }),
    ...user(has("admin"), { id: "admin-roster", label: "Student roster", view: "adminRoster" }),
  ];

  const page = (id, label, program, items = [], icon) => ({ id, label, icon, view: "program", props: { program }, program, items });

  return [
    { id: "home", label: "Home", icon: "\u{1F3E0}", view: "home" },
    { id: "aid", label: "Student Aid", icon: "\u{1F393}", items: [
      page("bursaries", "Bursaries", "bursaries", bursaryActions),
      page("scholarships", "Scholarships", "scholarships"),
    ] },
    { id: "clubs", label: "Clubs", icon: "\u{1F465}", items: [
      page("club-finances", "Finances", "club-finances", financeActions),
      page("club-registration", "Registration", "club-registration", registrationActions),
    ] },
    page("agm", "AGM", "agm", agmActions, "\u{1F5F3}"),
    page("senate", "Senate", "senate", senateActions, "\u{1F3DB}"),
    ...(administration.length ? [{ id: "administration", label: "Administration", items: administration }] : []),
    { id: "profile", label: "Profile", icon: "\u{1F464}", view: "profile", items: signedIn ? [
      { id: "profile-account", label: "Account", view: "account" },
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
  return isCurrent(node, current) || (node.items || []).some(child => containsCurrent(child, current));
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

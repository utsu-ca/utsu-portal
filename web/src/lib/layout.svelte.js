// Sidebar state: collapsed on desktop (remembered), open or closed on mobile, and which groups are folded.
const KEY = "sidebar-collapsed";

export const layout = $state({
  collapsed: typeof localStorage !== "undefined" && localStorage.getItem(KEY) === "1",
  mobileOpen: false,
  folded: {},
});

export const desktop = () => typeof matchMedia === "function" && matchMedia("(min-width: 48rem)").matches;

export function toggleSidebar() {
  if (desktop()) {
    layout.collapsed = !layout.collapsed;
    localStorage.setItem(KEY, layout.collapsed ? "1" : "0");
  } else {
    layout.mobileOpen = !layout.mobileOpen;
  }
}

export const closeMobile = () => { layout.mobileOpen = false; };
export const toggleFold = id => { layout.folded[id] = !layout.folded[id]; };

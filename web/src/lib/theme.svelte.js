// Branding and colours, read at run time from /branding/theme.json so they can be changed without rebuilding.
// Only plain hex colours and bare file names are accepted from the file.
export const theme = $state({ name: "UTSU Digital Service", logoUrl: "", copyright: { holder: "UTSU", startYear: null } });

const HEX = /^#[0-9a-f]{3,8}$/i;
const FILE = /^[\w][\w.\- ]*$/;
const VARIABLES = {
  user: { accent: "--user-accent", accentHover: "--user-accent-hover", chrome: "--user-chrome", page: "--user-page" },
  admin: { accent: "--admin-accent", accentHover: "--admin-accent-hover", chrome: "--admin-chrome" },
};

const brandingUrl = file => (typeof file === "string" && FILE.test(file) && !file.includes("..") ? "/branding/" + encodeURIComponent(file) : "");

export function applyTheme(config = {}) {
  const root = document.documentElement;
  for (const [mode, names] of Object.entries(VARIABLES)) {
    for (const [key, variable] of Object.entries(names)) {
      const value = config.colors?.[mode]?.[key];
      if (typeof value === "string" && HEX.test(value)) root.style.setProperty(variable, value);
      else root.style.removeProperty(variable);
    }
  }
  if (typeof config.name === "string" && config.name.trim()) theme.name = config.name.trim().slice(0, 80);
  theme.logoUrl = brandingUrl(config.logo);
  const holder = config.copyright?.holder;
  if (typeof holder === "string" && holder.trim()) theme.copyright.holder = holder.trim().slice(0, 80);
  const start = Number(config.copyright?.startYear);
  theme.copyright.startYear = Number.isInteger(start) && start > 1900 && start < 3000 ? start : null;
  const favicon = brandingUrl(config.favicon);
  if (favicon) {
    let link = document.querySelector("link[rel~='icon']");
    if (!link) {
      link = document.createElement("link");
      link.rel = "icon";
      document.head.append(link);
    }
    link.href = favicon;
  }
}

// A missing or broken theme file leaves the built-in colours and name in place.
export async function loadTheme() {
  try {
    const response = await fetch("/branding/theme.json", { cache: "no-cache" });
    if (response.ok) applyTheme(await response.json());
  } catch {
    /* keep the defaults */
  }
}

export function copyrightLine({ holder, startYear }, year = new Date().getFullYear()) {
  const years = startYear && startYear < year ? `${startYear}–${year}` : String(year);
  return `© ${years} ${holder}`;
}

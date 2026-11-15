import { beforeEach, describe, expect, it } from "vitest";
import { applyTheme, copyrightLine, theme } from "./theme.svelte.js";

beforeEach(() => {
  document.documentElement.removeAttribute("style");
  document.head.innerHTML = "";
});
const css = name => document.documentElement.style.getPropertyValue(name);

describe("theme", () => {
  it("applies user and admin colours, the name, logo and favicon from the config", () => {
    applyTheme({ name: "Other Union", logo: "mark.svg", favicon: "fav.svg",
      colors: { user: { accent: "#112233", chrome: "#000000" }, admin: { accent: "#aa0000" } } });
    expect(css("--user-accent")).toBe("#112233");
    expect(css("--admin-accent")).toBe("#aa0000");
    expect(theme.name).toBe("Other Union");
    expect(theme.logoUrl).toBe("/branding/mark.svg");
    expect(document.querySelector("link[rel~='icon']").getAttribute("href")).toBe("/branding/fav.svg");
  });

  it("ignores anything that is not a plain colour or a bare file name", () => {
    applyTheme({ logo: "../../etc/passwd", colors: { user: { accent: "red; background: url(x)", chrome: "javascript:1" } } });
    expect(css("--user-accent")).toBe("");
    expect(css("--user-chrome")).toBe("");
    expect(theme.logoUrl).toBe("");
  });

  it("drops a colour that is removed from the config", () => {
    applyTheme({ colors: { user: { accent: "#112233" } } });
    applyTheme({ colors: {} });
    expect(css("--user-accent")).toBe("");
  });
});

describe("copyright line", () => {
  it("shows the current year, or a range from the start year", () => {
    expect(copyrightLine({ holder: "UTSU", startYear: null }, 2031)).toBe("© 2031 UTSU");
    expect(copyrightLine({ holder: "UTSU", startYear: 2026 }, 2026)).toBe("© 2026 UTSU");
    expect(copyrightLine({ holder: "UTSU", startYear: 2026 }, 2031)).toBe("© 2026–2031 UTSU");
  });
});

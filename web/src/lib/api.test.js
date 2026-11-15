import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, downloadReport, setToken } from "./api.js";

beforeEach(() => setToken("session"));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); setToken(null); });

describe("api", () => {
  it("sends the bearer token and a JSON body", async () => {
    const fetchMock = vi.fn(async () => ({ ok: true, status: 200, json: async () => ({ fine: true }) }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await api("/x", { method: "POST", body: { a: 1 } })).toEqual({ fine: true });
    const [path, init] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/x");
    expect(init.headers.Authorization).toBe("Bearer session");
    expect(init.body).toBe('{"a":1}');
  });

  it("raises the server's message and keeps the field errors", async () => {
    vi.stubGlobal("fetch", async () => ({ ok: false, status: 422, json: async () => ({ detail: { name: "required" } }) }));
    await expect(api("/x")).rejects.toMatchObject({ status: 422, detail: { name: "required" } });
    vi.stubGlobal("fetch", async () => ({ ok: false, status: 403, json: async () => ({ detail: "not on the student roster" }) }));
    await expect(api("/x")).rejects.toThrow("not on the student roster");
  });
});

describe("downloadReport", () => {
  it("uses the session token and releases the object URL", async () => {
    const events = [];
    vi.stubGlobal("fetch", async (path, opts) => {
      events.push(["fetch", path, opts.headers.Authorization]);
      return { ok: true, blob: async () => "blob" };
    });
    URL.createObjectURL = blob => { events.push(["url", blob]); return "blob:report"; };
    URL.revokeObjectURL = url => events.push(["revoke", url]);
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function () { events.push(["click", this.download]); });
    await downloadReport("/admin/agm/rsvps.csv", "agm.csv");
    expect(events).toEqual([["fetch", "/api/admin/agm/rsvps.csv", "Bearer session"], ["url", "blob"],
      ["click", "agm.csv"], ["revoke", "blob:report"]]);
  });

  it("reports why a download was refused", async () => {
    vi.stubGlobal("fetch", async () => ({ ok: false, status: 404, json: async () => ({ detail: "no results" }) }));
    await expect(downloadReport("/admin/senate/results.csv", "s.csv")).rejects.toThrow("no results");
  });
});

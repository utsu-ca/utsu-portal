import { afterEach, describe, expect, it, vi } from "vitest";
import { openStream, sseParser } from "./stream.js";

const encoder = new TextEncoder();

// A fetch Response whose body yields the given text chunks and then either ends or stays open.
function streamResponse(chunks, { status = 200, hold = false } = {}) {
  const body = new ReadableStream({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      if (!hold) controller.close();
    },
  });
  return { status, ok: status < 400, body };
}

const message = (seq, body = `post ${seq}`) => `id: ${seq}\nevent: message\ndata: {"seq":${seq},"body":"${body}"}\n\n`;

afterEach(() => sessionStorage.clear());

describe("sseParser", () => {
  it("joins events split across chunks and ignores comments", () => {
    const events = [];
    const retries = [];
    const feed = sseParser({ onEvent: e => events.push(e), onRetry: ms => retries.push(ms) });
    feed("retry: 3000\n\n: keepalive\n\nid: 4\nevent: mess");
    feed("age\ndata: {\"seq\":4}\r\n\r\ndata: a\ndata: b\n");
    feed("\n");
    expect(retries).toEqual([3000]);
    expect(events).toEqual([
      { type: "message", data: "{\"seq\":4}", id: "4" },
      { type: "message", data: "a\nb", id: null },
    ]);
  });
});

describe("openStream", () => {
  it("sends the token in a header and resumes from the last sequence after the server ends the stream", async () => {
    sessionStorage.setItem("token", "tok");
    const responses = [
      streamResponse([message(1), message(2), "event: ready\ndata: {\"cursor\":2}\n\n", "event: reconnect\ndata: {\"cursor\":2}\n\n"]),
      streamResponse([message(3), "event: ready\ndata: {\"cursor\":3}\n\n"], { hold: true }),
    ];
    const fetchImpl = vi.fn(async () => responses.shift());
    const events = [];
    const statuses = [];
    const stream = openStream("/threads/t/r/stream", {
      fetchImpl, onEvent: (type, row) => events.push([type, row.seq]), onStatus: s => statuses.push(s),
    });
    await vi.waitFor(() => expect(events).toHaveLength(3));
    stream.close();

    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual(["/api/threads/t/r/stream?after=0", "/api/threads/t/r/stream?after=2"]);
    expect(fetchImpl.mock.calls[0][1].headers.Authorization).toBe("Bearer tok");
    expect(events).toEqual([["message", 1], ["message", 2], ["message", 3]]);
    expect(statuses).toEqual(["connecting", "live", "live"]);
  });

  it("backs off after a dropped connection and stops for good once access is denied", async () => {
    const responses = [
      () => { throw new TypeError("network down"); },
      () => streamResponse([message(5), "event: denied\ndata: {}\n\n"], { hold: true }),
    ];
    const fetchImpl = vi.fn(async () => responses.shift()());
    const waits = [];
    const statuses = [];
    const events = [];
    openStream("/threads/t/r/stream", {
      fetchImpl, wait: async ms => { waits.push(ms); }, onEvent: (type, row) => events.push(row.seq), onStatus: s => statuses.push(s),
    });
    await vi.waitFor(() => expect(statuses.at(-1)).toBe("denied"));
    expect(waits).toEqual([3000]);
    expect(events).toEqual([5]);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    expect(statuses).toEqual(["connecting", "reconnecting", "denied"]);
  });

  it("treats a refused stream as denied without retrying", async () => {
    const fetchImpl = vi.fn(async () => ({ status: 403, ok: false, body: null }));
    const statuses = [];
    openStream("/threads/t/r/stream", { fetchImpl, onEvent: () => {}, onStatus: s => statuses.push(s) });
    await vi.waitFor(() => expect(statuses).toEqual(["connecting", "denied"]));
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("forwards reconciliation and late rows without moving the reconnect cursor backwards", async () => {
    const responses = [
      streamResponse([
        "event: sync\ndata: {\"seqs\":[2,5]}\n\n", message(5), message(2),
        "event: reconnect\ndata: {\"cursor\":5}\n\n",
      ]),
      { status: 403, ok: false, body: null },
    ];
    const fetchImpl = vi.fn(async () => responses.shift());
    const onEvent = vi.fn();
    openStream("/threads/t/r/stream", { fetchImpl, onEvent });
    await vi.waitFor(() => expect(fetchImpl).toHaveBeenCalledTimes(2));
    expect(onEvent).toHaveBeenCalledWith("sync", { seqs: [2, 5] });
    expect(fetchImpl.mock.calls[1][0]).toBe("/api/threads/t/r/stream?after=5");
  });
});

// Server-sent events over fetch, so the session token travels in the Authorization header and never in a URL.
// The server ends each stream after a while (and networks drop); the client reconnects from the last sequence
// number it saw, so nothing is missed or shown twice.
import { getToken } from "./api.js";

const MAX_BACKOFF = 60000;

// Incremental parser for the text/event-stream format. Feed it decoded text in any chunking.
export function sseParser({ onEvent, onRetry = () => {} }) {
  let buffer = "";
  let type = "";
  let data = [];
  let id = null;
  const line = text => {
    if (text === "") {
      if (data.length) onEvent({ type: type || "message", data: data.join("\n"), id });
      type = "";
      data = [];
      id = null;
      return;
    }
    if (text.startsWith(":")) return;
    const colon = text.indexOf(":");
    const field = colon < 0 ? text : text.slice(0, colon);
    let value = colon < 0 ? "" : text.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") type = value;
    else if (field === "data") data.push(value);
    else if (field === "id") id = value;
    else if (field === "retry" && /^\d+$/.test(value)) onRetry(Number(value));
  };
  return chunk => {
    buffer += chunk;
    let end;
    while ((end = buffer.indexOf("\n")) >= 0) {
      line(buffer.slice(0, end).replace(/\r$/, ""));
      buffer = buffer.slice(end + 1);
    }
  };
}

// Opens a thread stream. `onEvent(type, payload)` receives "message"/"redact" rows or "sync" with visible seqs; `onStatus` receives
// "connecting", "live", "reconnecting" or "denied" (access ended or the session expired; the stream stops).
export function openStream(path, { after = 0, onEvent, onStatus = () => {}, fetchImpl = (...a) => fetch(...a),
  wait = ms => new Promise(resolve => setTimeout(resolve, ms)) }) {
  let cursor = after;
  let retry = 3000;
  let failures = 0;
  let stopped = false;
  let controller = null;

  async function once() {
    controller = new AbortController();
    const token = getToken();
    const headers = { Accept: "text/event-stream", ...(token ? { Authorization: "Bearer " + token } : {}) };
    const response = await fetchImpl(`/api${path}?after=${cursor}`, { headers, cache: "no-store", signal: controller.signal });
    if ([401, 403, 404].includes(response.status)) return "denied";
    if (!response.ok || !response.body) throw new Error(`stream failed (${response.status})`);
    let outcome = "dropped";
    const feed = sseParser({
      onRetry: ms => { retry = ms; },
      onEvent: ({ type, data }) => {
        const payload = data ? JSON.parse(data) : {};
        if (type === "message") cursor = Math.max(cursor, payload.seq);
        if (type === "message" || type === "redact" || type === "sync") onEvent(type, payload);
        else if (type === "ready") {
          failures = 0;
          onStatus("live");
        } else if (type === "reconnect") outcome = "reconnect";
        else if (type === "denied") outcome = "denied";
      },
    });
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    try {
      while (outcome !== "denied") {
        const { value, done } = await reader.read();
        if (done) break;
        feed(decoder.decode(value, { stream: true }));
      }
    } finally {
      controller.abort();
      reader.releaseLock();
    }
    return outcome;
  }

  (async () => {
    onStatus("connecting");
    while (!stopped) {
      let outcome;
      try {
        outcome = await once();
      } catch {
        outcome = "dropped";
      }
      if (stopped) return;
      if (outcome === "denied") return onStatus("denied");
      if (outcome === "reconnect") continue;
      onStatus("reconnecting");
      failures += 1;
      await wait(Math.min(retry * 2 ** (failures - 1), MAX_BACKOFF));
    }
  })();

  return {
    close() {
      stopped = true;
      controller?.abort();
    },
  };
}

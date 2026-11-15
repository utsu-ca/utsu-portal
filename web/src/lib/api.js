// Thin client for the portal API. The session token lives in sessionStorage so the check-in, SSO and finance
// pages (separate documents on the same origin) share the sign-in of the main portal.
export const getToken = () => sessionStorage.getItem("token");

export function setToken(token) {
  if (token) sessionStorage.setItem("token", token);
  else sessionStorage.removeItem("token");
}

export function errorMessage(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (detail && detail.error) return detail.error;
  return JSON.stringify(detail || status);
}

export async function api(path, opts = {}) {
  const token = getToken();
  const headers = { "Content-Type": "application/json", ...(token ? { Authorization: "Bearer " + token } : {}) };
  const init = { headers, ...opts };
  if (opts.body !== undefined) init.body = JSON.stringify(opts.body);
  const response = await fetch("/api" + path, init);
  const body = response.status === 204 ? null : await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(errorMessage(body, response.status));
    error.detail = body && body.detail;
    error.status = response.status;
    throw error;
  }
  return body;
}

// Fetches an authenticated file (CSV report, PDF) and hands it to the browser as a download.
export async function downloadReport(path, filename) {
  const token = getToken();
  const response = await fetch("/api" + path, { headers: token ? { Authorization: "Bearer " + token } : {} });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `Download failed (${response.status}).`);
  }
  const url = URL.createObjectURL(await response.blob());
  try {
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
  } finally {
    URL.revokeObjectURL(url);
  }
}

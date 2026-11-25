// Fees are handled in integer cents, as the API does.
export const formatCents = cents => (cents == null ? "—" : (cents < 0 ? "-" : "") + "$" + (Math.abs(cents) / 100).toFixed(2));

// "12.5" -> 1250. Returns null for anything that is not a positive amount with at most two decimals.
export function parseDollars(text) {
  const value = String(text ?? "").trim().replace(/^\$/, "");
  if (!/^\d+(\.\d{1,2})?$/.test(value)) return null;
  const cents = Math.round(Number(value) * 100);
  return cents > 0 ? cents : null;
}

export const formatDate = seconds => new Date(seconds * 1000).toLocaleDateString(undefined, { dateStyle: "long" });

export const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
  "October", "November", "December"];

export const STATUS_LABELS = {
  opted_out: "Opted out", refund_requested: "Refund requested", refunded: "Refunded", denied: "Refund denied",
  withdrawn: "Withdrawn",
};

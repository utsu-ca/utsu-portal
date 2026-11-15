import { readFileAsDataUrl } from "./cheque.js";

export const MAX_RECEIPT_BYTES = 3_000_000;
const TYPES = ["image/jpeg", "image/png", "image/webp", "application/pdf"];

// Returns the data URL for a receipt, or throws an Error saying which file to fix.
export async function readReceipt(file) {
  if (!TYPES.includes(file.type)) throw new Error(`${file.name}: receipts must be JPEG, PNG, WebP or PDF files.`);
  if (file.size > MAX_RECEIPT_BYTES) throw new Error(`${file.name}: receipts must be under 3 MB.`);
  return readFileAsDataUrl(file);
}

import { readFileAsDataUrl } from "../../../lib/file.js";

// A void cheque is uploaded as a PDF data URL. These checks mirror the server's so a bad file is caught before upload.
export const MAX_CHEQUE_BYTES = 3_000_000;

// Returns the data URL, or throws an Error whose message tells the person what to fix.
export async function readVoidCheque(file) {
  if (!file) throw new Error("Choose a PDF copy of your void cheque.");
  if (file.size > MAX_CHEQUE_BYTES) throw new Error("The PDF is too large. Upload a file under 3 MB.");
  const head = new TextDecoder().decode(await file.slice(0, 5).arrayBuffer());
  if (head !== "%PDF-") throw new Error("The void cheque must be a PDF file.");
  return readFileAsDataUrl(file);
}

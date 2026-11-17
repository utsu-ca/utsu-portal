import { describe, expect, it } from "vitest";
import { readVoidCheque } from "../lib/cheque.js";

const pdf = (bytes, name = "cheque.pdf") => new File([bytes], name, { type: "application/pdf" });

describe("void cheque", () => {
  it("accepts a PDF and returns it as a data URL", async () => {
    expect(await readVoidCheque(pdf("%PDF-1.4\n%%EOF"))).toMatch(/^data:application\/pdf;base64,/);
  });

  it("tells the person what to fix", async () => {
    await expect(readVoidCheque(null)).rejects.toThrow("Choose a PDF");
    await expect(readVoidCheque(pdf("not a pdf"))).rejects.toThrow("must be a PDF");
    await expect(readVoidCheque(pdf("%PDF-" + "0".repeat(3_000_001)))).rejects.toThrow("too large");
  });
});

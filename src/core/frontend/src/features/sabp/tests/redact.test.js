import { expect, it } from "vitest";
import { redactHardship } from "../../../../../../sabp/frontend/lib/redact.js";

it("redacts contact details and the applicant's own name", () => {
  const text = "Ada Lovelace (ada@x.ca, 416-555-0100, 1234567890) lives at M5S 1A1 see https://x.ca";
  expect(redactHardship(text, "Ada Lovelace")).toBe("[name] [name] ([email], [phone], [student number]) lives at [postal code] see [link]");
});

it("matches names across accents and scripts, and is idempotent", () => {
  const result = redactHardship("José and Jose\u0301, 李 王, 1234567, a@example.ca, https://x.ca", "Jose 李 王");
  expect(result).toBe("[name] and [name], [name] [name], [student number], [email], [link]");
  expect(redactHardship(result, "Jose 李 王")).toBe(result);
});

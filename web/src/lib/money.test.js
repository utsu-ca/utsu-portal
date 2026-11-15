import { expect, it } from "vitest";
import { formatCents, parseDollars } from "./money.js";

it("converts dollars to cents without floating point surprises", () => {
  expect([parseDollars("25.50"), parseDollars("$0.07"), parseDollars("19.99"), parseDollars("3")]).toEqual([2550, 7, 1999, 300]);
  for (const bad of ["", "0", "-5", "1.234", "abc", "1e3"]) expect(parseDollars(bad)).toBeNull();
  expect(formatCents(123456)).toBe("$1234.56");
  expect(formatCents(-50)).toBe("-$0.50");
});

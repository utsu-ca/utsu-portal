import { expect, it } from "vitest";
import { combineViews } from "./views.js";

it("rejects duplicate contributed view names", () => {
  const first = () => {}, second = () => {};
  expect(combineViews({ first }, { second })).toEqual({ first, second });
  expect(() => combineViews({ first }, { first: second })).toThrow("Duplicate view: first");
});

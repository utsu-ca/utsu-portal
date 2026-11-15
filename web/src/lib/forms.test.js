import { describe, expect, it } from "vitest";
import { validate, visible } from "./forms.js";

const schema = {
  fields: [
    { id: "name", type: "text", label: "Name", required: true, max: 5 },
    { id: "kind", type: "select", label: "Kind", options: ["a", "b"], required: true },
    { id: "email", type: "email", label: "Email" },
    { id: "age", type: "number", label: "Age", min: 1, max: 9 },
    { id: "why", type: "textarea", label: "Why", show_if: { field: "kind", equals: "b" }, required: true },
    { id: "consent", type: "checkbox", label: "Consent" },
  ],
};

describe("form validation", () => {
  it("collects every problem so the person can fix them together", () => {
    const { errors } = validate(schema, { name: "toolong", kind: "z", email: "nope", age: 12 });
    expect(errors).toEqual({ name: "at most 5 characters", kind: "not an allowed option", email: "invalid email", age: "out of range" });
  });

  it("requires only fields that are visible", () => {
    expect(validate(schema, { name: "ok", kind: "a" }).errors).toEqual({});
    expect(validate(schema, { name: "ok", kind: "b" }).errors).toEqual({ why: "required" });
  });

  it("cleans text and fills unchecked boxes", () => {
    const { data } = validate(schema, { name: "  a\u200b  b ", kind: "a" });
    expect(data).toEqual({ name: "a b", kind: "a", consent: false });
  });

  it("shows a field when any selected value matches", () => {
    expect(visible({ show_if: { field: "x", equals: 1 } }, { x: [1, 2] })).toBe(true);
    expect(visible({ show_if: { field: "x", equals: 1 } }, { x: 2 })).toBe(false);
  });
});

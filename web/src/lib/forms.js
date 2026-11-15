// Client-side form engine: validates and sanitises a submission with the same rules the server enforces.
const CTRL = /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b-\u200f\u202a-\u202e\u2066-\u2069]/g;

export function clean(value, max, multiline) {
  let s = String(value).normalize("NFC");
  if (!multiline) s = s.replace(/\s+/g, " ");
  return s.replace(CTRL, "").trim().slice(0, max);
}

export function visible(field, data) {
  const c = field.show_if;
  if (!c) return true;
  const v = data[c.field];
  return Array.isArray(v) ? v.includes(c.equals) : v === c.equals;
}

// Patterns are vetted server-side (RE2 + a JS-safe subset) before publishing; the try/catch only guards malformed input.
function matchesPattern(pattern, value) {
  try {
    return new RegExp("^(?:" + pattern + ")$").test(value.slice(0, 5000));
  } catch {
    return false;
  }
}

// A number input binds to a number (or null when empty), a text input to a string: blank means nothing entered.
export const isBlank = value => value == null || String(value).trim() === "";

// returns {data, errors}
export function validate(schema, raw) {
  const data = {}, errors = {};
  for (const f of schema.fields) {
    if (f.type === "info" || !visible(f, data)) continue;
    let v = raw[f.id];
    const empty = v == null || v === "" || (Array.isArray(v) && !v.length) || (f.type === "checkbox" && v === false);
    if (empty) {
      if (f.required) errors[f.id] = "required";
      else if (f.type === "checkbox") data[f.id] = false;
      continue;
    }
    const t = f.type;
    if (["text", "textarea", "email", "date", "select", "radio"].includes(t)) {
      v = clean(v, t === "textarea" ? 5000 : 500, t === "textarea");
      if ((t === "select" || t === "radio") && !f.options.includes(v)) errors[f.id] = "not an allowed option";
      else if (t === "email" && !/^[^@\s<>]{1,64}@[^@\s<>]{1,255}\.[^@\s<>]{2,}$/.test(v)) errors[f.id] = "invalid email";
      else if (t === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(v)) errors[f.id] = "date must be YYYY-MM-DD";
      else if ((t === "text" || t === "textarea") && f.min != null && v.length < f.min) errors[f.id] = "at least " + f.min + " characters";
      else if ((t === "text" || t === "textarea") && f.max != null && v.length > f.max) errors[f.id] = "at most " + f.max + " characters";
      else if (f.pattern && !matchesPattern(f.pattern, v)) errors[f.id] = "invalid format";
    } else if (t === "number") {
      v = Number(v);
      if (!Number.isFinite(v)) errors[f.id] = "must be a number";
      else if ((f.min != null && v < f.min) || (f.max != null && v > f.max)) errors[f.id] = "out of range";
    } else if (t === "multiselect") {
      if (!Array.isArray(v) || v.some(x => !f.options.includes(x))) errors[f.id] = "invalid selection";
    }
    if (!errors[f.id]) data[f.id] = v;
  }
  return { data, errors };
}

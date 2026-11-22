// Automatic first pass at redacting a hardship statement. It is incomplete by design: an administrator reviews it.
export function redactHardship(value, name) {
  const words = new Set(((name || "").normalize("NFC").match(/[\p{L}\p{N}_]+/gu) || [])
    .map(w => w.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase()));
  return (value || "").normalize("NFC")
    .replace(/https?:\/\/\S+/gi, "[link]")
    .replace(/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g, "[email]")
    .replace(/(?<!\d)\d{7,10}(?!\d)/g, "[student number]")
    .replace(/(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)/g, "[phone]")
    .replace(/\b[A-Za-z]\d[A-Za-z][\s-]?\d[A-Za-z]\d\b/g, "[postal code]")
    .replace(/\[(?:name|email|student number|phone|postal code|link)\]|[\p{L}\p{M}\p{N}_]+/gu, word => {
      if (word.startsWith("[")) return word;
      const normalized = word.normalize("NFKD").replace(/\p{M}/gu, "").toLowerCase();
      return words.has(normalized) ? "[name]" : word;
    });
}

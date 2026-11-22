// Recomputes a published ballot record in the browser, so a voter need not trust the server's word for it: the
// Merkle root over the receipt leaves and the SHA-256 digest of the committed record (the value anchored on chain).
// Mirrors src/voting/record.py.
const toHex = bytes => Array.from(bytes, byte => byte.toString(16).padStart(2, "0")).join("");
const fromHex = text => Uint8Array.from(text.match(/../g) || [], pair => parseInt(pair, 16));
const sha256 = async bytes => new Uint8Array(await globalThis.crypto.subtle.digest("SHA-256", bytes));

// Python's json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False) for the JSON values a record holds.
export function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export async function merkleRoot(leaves) {
  let level = [...leaves].sort().map(fromHex);
  if (!level.length) return toHex(await sha256(new Uint8Array()));
  while (level.length > 1) {
    const next = [];
    for (let i = 0; i < level.length; i += 2) {
      if (i + 1 === level.length) {
        next.push(level[i]);
        continue;
      }
      const pair = new Uint8Array(1 + level[i].length + level[i + 1].length);
      pair[0] = 1;
      pair.set(level[i], 1);
      pair.set(level[i + 1], 1 + level[i].length);
      next.push(await sha256(pair));
    }
    level = next;
  }
  return toHex(level[0]);
}

export async function recordDigest(committed) {
  return toHex(await sha256(new TextEncoder().encode(canonical(committed))));
}

// Checks a record from GET /voting/ballots/{id}/record, and whether `receipt` (if given) was counted.
export async function verifyRecord(record, receipt = "") {
  if (!globalThis.crypto?.subtle) {
    throw new Error("Record verification requires a secure connection and browser support for Web Crypto.");
  }
  const leaves = record.leaves || [];
  const wanted = receipt.trim().toLowerCase();
  return {
    root: (await merkleRoot(leaves)) === record.committed.root && leaves.length === record.committed.count,
    digest: (await recordDigest(record.committed)) === record.digest,
    included: wanted ? leaves.includes(wanted) : null,
  };
}

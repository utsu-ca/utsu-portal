import { describe, expect, it } from "vitest";
import { merkleRoot, recordDigest, verifyRecord } from "../../../../../../voting/frontend/lib/record.js";

// Reference values computed by src/voting/record.py.
const leaves = [
  "f8c90bd1c19992ec3bcc8ab892f54df949e013bfda0b9ce91aea2e20046b3f90",
  "c6478d3e8b1e7bc86c5cc881527bf1ce07d5320b82b02253c92f29bb6d78627f",
  "7bf64816b1826adf1577cd1df945496f1f5e755867023369e2250eca30f3ac83",
];
const root = "48ab6a3543ede9e5e2d1f9484b36878c88a7e895d0026c080f47235cee477d75";
const committed = {
  v: "utsu-ballot-record-v1", ballot: "b1", title: "Café \"motion\"\n2", count: 3, root,
  result: { quota: "15/7", rounds: [{ tally: { b: "1", a: "2" } }], quorum_met: true, tied: [] },
  closed_at: 1700000000, options: [{ id: "a", label: "Ä" }],
};
const digest = "7f4409184eb87c6699412e2eaf3566108868214900bc7fd8fe3170769f7424ef";

describe("ballot record verification matches the server's algorithm", () => {
  it("recomputes the Merkle root (odd node promoted) and the canonical-JSON digest", async () => {
    expect(await merkleRoot(leaves)).toBe(root);
    expect(await merkleRoot([])).toBe("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
    expect(await recordDigest(committed)).toBe(digest);
  });

  it("reports a counted receipt, a missing one, and a tampered record", async () => {
    const record = { committed, digest, leaves };
    expect(await verifyRecord(record, ` ${leaves[1].toUpperCase()} `)).toEqual({ root: true, digest: true, included: true });
    expect(await verifyRecord(record, "0".repeat(64))).toMatchObject({ included: false });
    const tampered = { ...record, committed: { ...committed, result: { ...committed.result, quorum_met: false } } };
    expect(await verifyRecord(tampered)).toEqual({ root: true, digest: false, included: null });
    expect(await verifyRecord({ ...record, leaves: leaves.slice(1) })).toMatchObject({ root: false });
  });
});

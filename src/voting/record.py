"""Vote leaves, the Merkle root over them and the canonical ballot-record digest. Anyone holding the published record
can recompute every value here; nothing depends on server secrets."""
from __future__ import annotations

import hashlib
import json
from typing import Any

LEAF_TAG = "utsu-vote-v1"
RECORD_TAG = "utsu-ballot-record-v1"


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def leaf_hash(ballot_id: str, vote_id: str, selection: list[str], salt: str) -> str:
    parts = (LEAF_TAG.encode(), ballot_id.encode(), vote_id.encode(), canonical(selection), salt.encode())
    return hashlib.sha256(b"|".join(parts)).hexdigest()


def merkle_root(leaves: list[str]) -> str:
    """Binary Merkle root over the sorted hex leaves: sha256(0x01 | left | right), odd nodes promoted unchanged."""
    level = [bytes.fromhex(leaf) for leaf in sorted(leaves)]
    if not level:
        return hashlib.sha256(b"").hexdigest()
    while len(level) > 1:
        level = [hashlib.sha256(b"\x01" + level[i] + level[i + 1]).digest() if i + 1 < len(level) else level[i]
                 for i in range(0, len(level), 2)]
    return level[0].hex()


def record_digest(committed: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON (sorted keys, no spaces, UTF-8) of the committed record."""
    return hashlib.sha256(canonical(committed)).hexdigest()

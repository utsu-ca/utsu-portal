"""Blockchain anchoring. A provider publishes one 32-byte checkpoint digest somewhere an operator cannot rewrite.
Only the digest leaves the system (never log content), and providers can be disabled with `PORTAL_ANCHOR=none`.

`PORTAL_ANCHOR`: `none` (default) | `local` (dev/test ledger in the store) | `ots` (OpenTimestamps calendar, Bitcoin-backed,
no keys or fees) | `http` (POST to `PORTAL_ANCHOR_URL`, a gateway you run that signs and sends e.g. an EVM transaction) |
`evm` (sign and send directly to an Ethereum-compatible JSON-RPC node; needs the `evm` extra and a funded wallet) |
`pkg.module:factory` returning an `AnchorProvider`."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.parse
import urllib.request
from typing import Any, Callable


class AnchorProvider:
    name = "none"
    enabled = False
    remote = True  # submits over the network, so callers should not wait on it

    def submit(self, digest: str, meta: dict) -> dict:
        """Publish `digest` (hex). Returns {'status': 'submitted'|'confirmed', 'ref': ..., 'proof': optional base64}."""
        raise NotImplementedError

    def confirm(self, record: dict) -> dict | None:
        """Optionally upgrade a 'submitted' record (e.g. wait for block inclusion). None = no change."""
        return None


class NullProvider(AnchorProvider):
    remote = False


class LocalLedgerProvider(AnchorProvider):
    """Development ledger: a second hash chain in the same store. Proves the plumbing, not independence."""
    name, enabled, remote = "local", True, False

    def __init__(self, store) -> None:
        self.store = store

    def submit(self, digest, meta):
        def add(h):
            h = h or {"id": "tip", "n": 0, "tip": ""}
            return {**h, "n": h["n"] + 1, "tip": hashlib.sha256(f"{h['tip']}|{digest}".encode()).hexdigest()}
        tip = self.store.update("anchor_ledger", "tip", add)
        return {"status": "confirmed", "ref": f"local:{tip['n']}:{tip['tip'][:16]}"}


class OpenTimestampsProvider(AnchorProvider):
    name, enabled = "ots", True
    CALENDAR = "https://alice.btc.calendar.opentimestamps.org"

    def __init__(self, calendar: str | None = None, timeout: float = 10.0) -> None:
        self.url, self.timeout = (calendar or self.CALENDAR).rstrip("/"), timeout
        if not self.url.startswith("https://"):
            raise ValueError("OpenTimestamps calendar must be https")

    def submit(self, digest, meta):
        req = urllib.request.Request(self.url + "/digest", data=bytes.fromhex(digest), headers={"Accept": "application/vnd.opentimestamps.v1", "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 (https enforced above)
            proof = r.read(1_000_000)
        return {"status": "submitted", "ref": f"ots:{self.url}", "proof": base64.b64encode(proof).decode()}  # pending proof; upgrade with `ots upgrade`


class HttpGatewayProvider(AnchorProvider):
    """POST {"digest": hex, ...meta} with a bearer token to your own relay and store the `{"tx": ...}` it returns."""
    name, enabled = "http", True

    def __init__(self, url: str, token: str | None = None, timeout: float = 15.0) -> None:
        if not url.startswith("https://"):
            raise ValueError("PORTAL_ANCHOR_URL must be https")
        self.url, self.token, self.timeout = url, token, timeout

    def submit(self, digest, meta):
        h = {"Content-Type": "application/json", **({"Authorization": "Bearer " + self.token} if self.token else {})}
        req = urllib.request.Request(self.url, data=json.dumps({"digest": digest, **meta}).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
            out = json.loads(r.read(100_000))
        return {"status": out.get("status", "submitted"), "ref": str(out.get("tx", ""))[:200]}


_HEX32 = re.compile(r"^[0-9a-f]{64}$")
_TX_HASH = re.compile(r"^0x[0-9a-fA-F]{64}$")


class EvmJsonRpcProvider(AnchorProvider):
    """Anchors on any Ethereum-compatible chain without a contract: a zero-value EIP-1559 transaction from the anchoring
    wallet to itself whose calldata is `b"UTSU" + digest`. The wallet only needs gas; keep it funded with little else.
    `confirm()` waits for a successful receipt with `confirmations` blocks on top; `verify()` re-reads the transaction."""

    name, enabled = "evm", True
    TAG = b"UTSU"

    def __init__(self, url: str, key: str | None, chain_id: int | None, confirmations: int = 12,
                 timeout: float = 15.0, rpc: Callable[[dict], dict] | None = None) -> None:
        host = urllib.parse.urlsplit(url or "").hostname or ""
        if rpc is None and not (url.startswith("https://") or (url.startswith("http://") and host in ("localhost", "127.0.0.1", "::1"))):
            raise ValueError("PORTAL_ANCHOR_URL must be an https JSON-RPC endpoint (http only for a local node)")
        if not key:
            raise ValueError("PORTAL_ANCHOR_EVM_KEY (or PORTAL_ANCHOR_EVM_KEY_FILE) is required for evm anchoring")
        if not chain_id or chain_id <= 0:
            raise ValueError("PORTAL_ANCHOR_CHAIN_ID is required for evm anchoring")
        if confirmations < 1:
            raise ValueError("PORTAL_ANCHOR_CONFIRMATIONS must be at least 1")
        try:
            from eth_account import Account
        except ImportError as exc:
            raise RuntimeError("evm anchoring needs the optional dependency: pip install 'utsu-core[evm]'") from exc
        self._account = Account.from_key(key)
        self.address: str = self._account.address
        self.url, self.chain_id, self.confirmations, self.timeout = url, chain_id, confirmations, timeout
        self._rpc = rpc or self._http

    def _http(self, payload: dict) -> dict:
        req = urllib.request.Request(self.url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 (https or loopback enforced above)
            return json.loads(r.read(1_000_000))

    def _call(self, method: str, *params: Any) -> Any:
        out = self._rpc({"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)})
        if not isinstance(out, dict):
            raise RuntimeError(f"{method}: malformed JSON-RPC response")
        if out.get("error"):
            error = out["error"]
            raise RuntimeError(f"{method}: {error.get('message', error) if isinstance(error, dict) else error}")
        return out.get("result")

    def calldata(self, digest: str) -> str:
        if not _HEX32.fullmatch(digest or ""):
            raise ValueError("anchor digest must be 32 bytes of lowercase hex")
        return "0x" + (self.TAG + bytes.fromhex(digest)).hex()

    def submit(self, digest, meta):
        data = self.calldata(digest)
        reported = int(self._call("eth_chainId"), 16)
        if reported != self.chain_id:
            raise RuntimeError(f"JSON-RPC endpoint is on chain {reported}, expected {self.chain_id}")
        nonce = int(self._call("eth_getTransactionCount", self.address, "pending"), 16)
        gas = int(self._call("eth_estimateGas", {"from": self.address, "to": self.address, "value": "0x0", "data": data}), 16)
        tip = int(self._call("eth_maxPriorityFeePerGas"), 16)
        block = self._call("eth_getBlockByNumber", "latest", False) or {}
        base = int(block.get("baseFeePerGas") or "0x0", 16)
        signed = self._account.sign_transaction({
            "type": 2, "chainId": self.chain_id, "nonce": nonce, "to": self.address, "value": 0, "data": data,
            "gas": gas + gas // 5, "maxPriorityFeePerGas": tip, "maxFeePerGas": 2 * base + tip,
        })
        tx = self._call("eth_sendRawTransaction", "0x" + bytes(signed.raw_transaction).hex())
        if not isinstance(tx, str) or not _TX_HASH.fullmatch(tx):
            raise RuntimeError("eth_sendRawTransaction returned no transaction hash")
        return {"status": "submitted", "ref": f"evm:{self.chain_id}:{tx}", "tx": tx, "chain_id": self.chain_id,
                "from": self.address}

    def confirm(self, record):
        tx = record.get("tx")
        if not isinstance(tx, str) or not _TX_HASH.fullmatch(tx):
            return None
        receipt = self._call("eth_getTransactionReceipt", tx)
        if not receipt:
            return None
        if int(receipt.get("status") or "0x0", 16) != 1:
            return {"status": "pending", "error": "anchoring transaction reverted"}
        block = int(receipt["blockNumber"], 16)
        if int(self._call("eth_blockNumber"), 16) - block + 1 < self.confirmations:
            return None
        return {"status": "confirmed", "block": block, "block_hash": receipt.get("blockHash")}

    def verify(self, record: dict, digest: str) -> bool:
        """True when the recorded transaction is from the anchoring wallet to itself and carries this digest."""
        tx = self._call("eth_getTransactionByHash", record.get("tx"))
        if not tx:
            return False
        sender, recipient = str(tx.get("from", "")).lower(), str(tx.get("to", "")).lower()
        return (sender == recipient == self.address.lower() and int(tx.get("chainId") or hex(self.chain_id), 16) == self.chain_id
                and str(tx.get("input", "")).lower() == self.calldata(digest))


def provider_from_settings(settings, store) -> AnchorProvider:
    spec = (settings.anchor or "none").strip()
    if spec == "none":
        return NullProvider()
    if spec == "local":
        return LocalLedgerProvider(store)
    if spec == "ots":
        return OpenTimestampsProvider(settings.anchor_url)
    if spec == "http":
        return HttpGatewayProvider(settings.anchor_url or "", settings.anchor_token)
    if spec == "evm":
        return EvmJsonRpcProvider(settings.anchor_url or "", settings.anchor_evm_key, settings.anchor_chain_id,
                                  settings.anchor_confirmations)
    if ":" in spec:
        import importlib

        mod, _, fn = spec.partition(":")
        return getattr(importlib.import_module(mod), fn)()
    raise ValueError(f"unknown PORTAL_ANCHOR {spec!r}")

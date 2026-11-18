from types import SimpleNamespace

import pytest
from eth_account import Account

from utsu_core.anchor import AnchorProvider, EvmJsonRpcProvider, NullProvider
from utsu_core.services.anchors import RETRY_AFTER, AnchorRuntime
from utsu_core.store import MemoryStore

KEY = "0x" + "11" * 32
DIGEST = "ab" * 32
CHAIN = 11155111
TX = "0x" + "cd" * 32


class FakeNode:
    def __init__(self) -> None:
        self.chain, self.head, self.receipt, self.tx, self.sent = CHAIN, 0, None, None, []

    def __call__(self, payload: dict) -> dict:
        method, params = payload["method"], payload["params"]
        if method == "eth_sendRawTransaction":
            self.sent.append(params[0])
            return {"result": TX}
        return {"result": {
            "eth_chainId": hex(self.chain),
            "eth_getTransactionCount": "0x7",
            "eth_estimateGas": "0x5300",
            "eth_maxPriorityFeePerGas": hex(10**9),
            "eth_getBlockByNumber": {"baseFeePerGas": hex(2 * 10**9)},
            "eth_getTransactionReceipt": self.receipt,
            "eth_blockNumber": hex(self.head),
            "eth_getTransactionByHash": self.tx,
        }[method]}


def test_evm_provider_sends_a_signed_self_transaction_carrying_the_digest():
    node = FakeNode()
    provider = EvmJsonRpcProvider("https://rpc.example", KEY, CHAIN, 3, rpc=node)
    result = provider.submit(DIGEST, {"purpose": "ballot-result"})
    assert result == {"status": "submitted", "ref": f"evm:{CHAIN}:{TX}", "tx": TX, "chain_id": CHAIN,
                      "from": provider.address}
    raw = node.sent[0]
    assert Account.recover_transaction(raw) == provider.address == Account.from_key(KEY).address
    assert provider.calldata(DIGEST) == "0x55545355" + DIGEST
    assert bytes.fromhex(provider.calldata(DIGEST)[2:]) in bytes.fromhex(raw[2:])
    node.chain = 1
    with pytest.raises(RuntimeError, match="chain 1"):
        provider.submit(DIGEST, {})
    assert len(node.sent) == 1


def test_evm_provider_confirms_after_enough_blocks_and_verifies_the_transaction():
    node = FakeNode()
    provider = EvmJsonRpcProvider("https://rpc.example", KEY, CHAIN, 3, rpc=node)
    assert provider.confirm({"tx": TX}) is None
    node.receipt, node.head = {"status": "0x1", "blockNumber": "0x10", "blockHash": "0xbeef"}, 0x11
    assert provider.confirm({"tx": TX}) is None
    node.head = 0x12
    assert provider.confirm({"tx": TX}) == {"status": "confirmed", "block": 16, "block_hash": "0xbeef"}
    node.receipt = {"status": "0x0", "blockNumber": "0x10"}
    assert provider.confirm({"tx": TX})["status"] == "pending"

    me = provider.address
    node.tx = {"from": me, "to": me, "input": provider.calldata(DIGEST), "chainId": hex(CHAIN)}
    assert provider.verify({"tx": TX}, DIGEST) is True
    assert provider.verify({"tx": TX}, "cd" * 32) is False
    node.tx = {**node.tx, "from": "0x" + "00" * 20}
    assert provider.verify({"tx": TX}, DIGEST) is False


@pytest.mark.parametrize("url,key,chain,confirmations,message", [
    ("http://rpc.example", KEY, CHAIN, 1, "https"),
    ("https://rpc.example", None, CHAIN, 1, "KEY"),
    ("https://rpc.example", KEY, None, 1, "CHAIN_ID"),
    ("https://rpc.example", KEY, CHAIN, 0, "CONFIRMATIONS"),
])
def test_evm_provider_refuses_unsafe_configuration(url, key, chain, confirmations, message):
    with pytest.raises(ValueError, match=message):
        EvmJsonRpcProvider(url, key, chain, confirmations)
    assert EvmJsonRpcProvider("http://localhost:8545", KEY, CHAIN).url == "http://localhost:8545"


class Flaky(AnchorProvider):
    name, enabled, remote = "flaky", True, False

    def __init__(self) -> None:
        self.calls = 0

    def submit(self, digest, meta):
        self.calls += 1
        if self.calls == 1:
            raise OSError("node unreachable")
        return {"status": "submitted", "ref": "flaky:1", "tx": TX}

    def confirm(self, record):
        return {"status": "confirmed", "block": 9}


def test_anchor_runtime_retries_on_read_and_scopes_anchors_to_their_owner():
    store, clock, actions = MemoryStore(), [1000], []
    provider = Flaky()
    runtime = AnchorRuntime(store, provider, SimpleNamespace(record=lambda *args: actions.append(args[1])),
                            lambda: clock[0])
    voting = runtime.for_owner("voting")
    anchor_id = voting.submit("ballot-result", DIGEST, {"ballot": "b1", "nested": {"x": 1}})
    assert voting.status(anchor_id)["status"] == "pending" and provider.calls == 1  # retry not yet due
    clock[0] += RETRY_AFTER
    assert voting.status(anchor_id)["status"] == "submitted"
    clock[0] += RETRY_AFTER
    assert (voting.status(anchor_id)["status"], voting.status(anchor_id)["block"]) == ("confirmed", 9)
    assert runtime.for_owner("other").status(anchor_id) is None
    assert store.get("anchors", anchor_id)["meta"] == {"ballot": "b1"}
    assert voting.submit("ballot-result", DIGEST) == anchor_id and provider.calls == 2
    assert actions == ["anchor.queued", "anchor.submitted", "anchor.confirmed"]
    with pytest.raises(ValueError):
        voting.submit("Ballot Result", DIGEST)
    with pytest.raises(ValueError):
        voting.submit("ballot-result", DIGEST.upper())

    disabled = AnchorRuntime(MemoryStore(), NullProvider(), SimpleNamespace(record=lambda *args: None), lambda: 1)
    off = disabled.for_owner("voting")
    assert off.enabled is False and off.status(off.submit("ballot-result", DIGEST))["status"] == "disabled"

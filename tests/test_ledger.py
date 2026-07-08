import json

from flightdeck.ledger import Ledger


def test_chain_appends_and_verifies(tmp_path):
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for index in range(5):
        ledger.append("event", {"n": index})
    result = ledger.verify()
    assert result.ok and result.entries == 5


def test_empty_ledger_verifies(tmp_path):
    result = Ledger(tmp_path / "missing.jsonl").verify()
    assert result.ok and result.entries == 0


def test_tampered_data_breaks_at_that_entry(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    for index in range(4):
        ledger.append("event", {"n": index})

    lines = path.read_text().splitlines()
    doctored = json.loads(lines[2])
    doctored["data"]["n"] = 999  # rewrite history
    lines[2] = json.dumps(doctored, sort_keys=True, separators=(",", ":"))
    path.write_text("\n".join(lines) + "\n")

    result = Ledger(path).verify()
    assert not result.ok
    assert result.broken_at == 2
    assert result.reason == "entry hash mismatch"


def test_deleted_line_breaks_the_sequence(tmp_path):
    path = tmp_path / "ledger.jsonl"
    ledger = Ledger(path)
    for index in range(4):
        ledger.append("event", {"n": index})

    lines = path.read_text().splitlines()
    del lines[1]
    path.write_text("\n".join(lines) + "\n")

    result = Ledger(path).verify()
    assert not result.ok
    assert result.broken_at == 2  # first surviving entry after the hole


def test_reopened_ledger_continues_the_chain(tmp_path):
    path = tmp_path / "ledger.jsonl"
    Ledger(path).append("first", {})
    Ledger(path).append("second", {})  # fresh instance must read the tail
    result = Ledger(path).verify()
    assert result.ok and result.entries == 2

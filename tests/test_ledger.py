import json

import pytest

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


def test_malformed_json_is_reported_as_integrity_failure(tmp_path):
    path = tmp_path / "ledger.jsonl"
    Ledger(path).append("event", {"n": 0})
    path.write_text(path.read_text(encoding="utf-8") + '\n{"seq": 1\n', encoding="utf-8")

    result = Ledger(path).verify()

    assert not result.ok
    assert result.entries == 2
    assert result.broken_at == 1
    assert result.reason.startswith("invalid JSON:")


@pytest.mark.parametrize(
    "entry",
    [
        None,
        [],
        {"seq": 0},
        {
            "seq": 0,
            "at": "2026-08-29T20:00:00+00:00",
            "event": "event",
            "data": [],
            "prev": "0" * 64,
            "hash": "0" * 64,
        },
    ],
)
def test_invalid_entry_shape_is_reported_as_integrity_failure(tmp_path, entry):
    path = tmp_path / "ledger.jsonl"
    path.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = Ledger(path).verify()

    assert not result.ok
    assert result.entries == 1
    assert result.broken_at == 0
    assert result.reason.startswith("invalid entry:")


def test_invalid_utf8_is_reported_as_integrity_failure(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b"\xff\n")

    result = Ledger(path).verify()

    assert not result.ok
    assert result.entries == 1
    assert result.broken_at == 0
    assert result.reason.startswith("invalid UTF-8:")


def test_utf16_record_is_not_accepted_as_utf8(tmp_path):
    path = tmp_path / "ledger.jsonl"
    Ledger(path).append("event", {"n": 0})
    record = path.read_text(encoding="utf-8").strip()
    path.write_bytes(record.encode("utf-16"))

    result = Ledger(path).verify()

    assert not result.ok
    assert result.broken_at == 0
    assert result.reason.startswith("invalid UTF-8:")


def test_entries_rejects_utf16_records(tmp_path):
    path = tmp_path / "ledger.jsonl"
    Ledger(path).append("event", {"n": 0})
    record = path.read_text(encoding="utf-8").strip()
    path.write_bytes(record.encode("utf-16"))

    with pytest.raises(UnicodeDecodeError):
        Ledger(path).entries()


def test_oversized_json_integer_is_reported_as_integrity_failure(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text('{"seq":' + "9" * 5000 + "}\n", encoding="utf-8")

    result = Ledger(path).verify()

    assert not result.ok
    assert result.broken_at == 0
    assert result.reason.startswith("invalid JSON:")


def test_invalid_unicode_is_reported_as_integrity_failure(tmp_path):
    path = tmp_path / "ledger.jsonl"
    path.write_text(
        '{"seq":0,"at":"2026-08-29T20:00:00+00:00","event":"\\ud800",'
        '"data":{},"prev":"' + "0" * 64 + '","hash":"x"}\n',
        encoding="utf-8",
    )

    result = Ledger(path).verify()

    assert not result.ok
    assert result.broken_at == 0
    assert result.reason.startswith("invalid entry:")


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

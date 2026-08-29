"""The audit ledger — an append-only, hash-chained JSONL file.

Governance oversight needs a record that is cheap to write, human-readable, and
TAMPER-EVIDENT. Each entry carries the SHA-256 of the previous one; editing or
deleting any line breaks every hash after it. `flightdeck audit verify` re-walks
the chain in pure code — trust is checked, not assumed.

Why a flat file and not a database table: auditors and DPOs can read JSONL with
`less`, diff it, and archive it. The chain does the integrity work; the format
stays boring on purpose. Timestamps may be supplied explicitly (imports,
backfills, the demo seeder) — the SEQUENCE proves append order either way, and
entry 0 records where the data came from.
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

GENESIS = "0" * 64
_ENTRY_FIELDS = ("seq", "at", "event", "data", "prev", "hash")


def _canonical(data: dict) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _entry_hash(seq: int, at: str, event: str, data: dict, prev: str) -> str:
    payload = f"{seq}|{at}|{event}|{_canonical(data)}|{prev}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _entry_shape_error(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return "invalid entry: expected a JSON object"
    missing = [field for field in _ENTRY_FIELDS if field not in entry]
    if missing:
        return f"invalid entry: missing field(s): {', '.join(missing)}"
    if type(entry["seq"]) is not int:
        return "invalid entry: field 'seq' must be an integer"
    for field in ("at", "event", "prev", "hash"):
        if not isinstance(entry[field], str):
            return f"invalid entry: field '{field}' must be a string"
    if not isinstance(entry["data"], dict):
        return "invalid entry: field 'data' must be a JSON object"
    return None


@dataclass
class VerifyResult:
    entries: int
    ok: bool
    broken_at: int | None = None  # seq of the first entry whose hash doesn't check out
    reason: str = ""


class Ledger:
    """Single-writer by design: one org directory, one ledger, appended by the CLI
    or the runner in-process. If you need concurrent writers you need a server,
    and that is explicitly out of scope for v0 (see ADR 002)."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._last: tuple[int, str] | None = None  # (seq, hash) cache of the tail

    def _tail(self) -> tuple[int, str]:
        """(last_seq, last_hash), reading the file once and caching afterwards."""
        if self._last is not None:
            return self._last
        seq, digest = -1, GENESIS
        if self.path.exists():
            with self.path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    seq, digest = entry["seq"], entry["hash"]
        self._last = (seq, digest)
        return self._last

    def append(self, event: str, data: dict, at: datetime | None = None) -> dict:
        last_seq, prev = self._tail()
        seq = last_seq + 1
        stamp = (at or datetime.now(UTC)).isoformat()
        digest = _entry_hash(seq, stamp, event, data, prev)
        entry = {"seq": seq, "at": stamp, "event": event, "data": data, "prev": prev, "hash": digest}
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(_canonical(entry) + "\n")
        self._last = (seq, digest)
        return entry

    def entries(self) -> list[dict]:
        return [json.loads(line.decode("utf-8")) for line in self._entry_lines()]

    def _entry_lines(self) -> Iterator[bytes]:
        if not self.path.exists():
            return
        with self.path.open("rb") as fh:
            for raw_line in fh:
                if line := raw_line.strip():
                    yield line

    def verify(self) -> VerifyResult:
        """Re-walk the chain: sequence must be gapless from 0, each prev must match
        the previous hash, each hash must recompute. First break wins."""
        entry_count = 0
        failure: tuple[int, str] | None = None
        prev = GENESIS
        for index, line in enumerate(self._entry_lines()):
            entry_count += 1
            if failure is not None:
                continue
            try:
                text = line.decode("utf-8")
            except UnicodeDecodeError as exc:
                failure = (index, f"invalid UTF-8: {exc.reason}")
                continue
            try:
                entry = json.loads(text)
            except json.JSONDecodeError as exc:
                failure = (index, f"invalid JSON: {exc.msg}")
                continue
            except (RecursionError, ValueError) as exc:
                failure = (index, f"invalid JSON: {exc}")
                continue
            if shape_error := _entry_shape_error(entry):
                failure = (index, shape_error)
                continue
            if entry["seq"] != index:
                failure = (entry["seq"], "sequence gap or reorder")
                continue
            if entry["prev"] != prev:
                failure = (entry["seq"], "broken chain link")
                continue
            try:
                expected = _entry_hash(
                    entry["seq"], entry["at"], entry["event"], entry["data"], entry["prev"]
                )
            except (RecursionError, UnicodeEncodeError, ValueError) as exc:
                failure = (entry["seq"], f"invalid entry: {exc}")
                continue
            if entry["hash"] != expected:
                failure = (entry["seq"], "entry hash mismatch")
                continue
            prev = entry["hash"]
        if failure is not None:
            return VerifyResult(entry_count, False, *failure)
        return VerifyResult(entry_count, True)

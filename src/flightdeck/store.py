"""The evidence store — SQLite, stdlib only.

Two tables: runs and feedback. That is deliberate. Every KPI flightdeck reports
is derivable from these rows plus the declared baselines; if a metric can't be
computed from evidence in this store, it doesn't belong in a report.

SQLite over anything fancier: an org's AI program produces thousands of runs a
month, not millions; a single file with zero setup beats a database server the
pilot team has to operate. Metrics load the window into memory and compute in
pure Python (see metrics.py) — the store stays a dumb, durable log.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from flightdeck.schemas import Feedback, Run

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    user        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status      TEXT NOT NULL,
    model_id    TEXT NOT NULL DEFAULT '',
    provider    TEXT NOT NULL DEFAULT '',
    tokens_in   INTEGER NOT NULL DEFAULT 0,
    tokens_out  INTEGER NOT NULL DEFAULT 0,
    cost        REAL NOT NULL DEFAULT 0,
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    redactions  INTEGER NOT NULL DEFAULT 0,
    reason      TEXT,
    output      TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_wf_time ON runs(workflow_id, started_at);
CREATE TABLE IF NOT EXISTS feedback (
    run_id        TEXT PRIMARY KEY REFERENCES runs(id),
    outcome       TEXT NOT NULL,
    human_minutes REAL,
    by            TEXT NOT NULL DEFAULT '',
    note          TEXT NOT NULL DEFAULT '',
    at            TEXT NOT NULL
);
"""


class Store:
    """Thin persistence layer. One feedback row per run — recording feedback twice
    means the human changed their mind, and the latest verdict wins."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ write

    def add_run(self, run: Run) -> None:
        self.conn.execute(
            """INSERT INTO runs (id, workflow_id, user, started_at, finished_at, status,
                                 model_id, provider, tokens_in, tokens_out, cost,
                                 latency_ms, redactions, reason, output)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run.id,
                run.workflow_id,
                run.user,
                run.started_at.isoformat(),
                run.finished_at.isoformat(),
                run.status,
                run.model_id,
                run.provider,
                run.tokens_in,
                run.tokens_out,
                run.cost,
                run.latency_ms,
                run.redactions,
                run.reason,
                run.output,
            ),
        )
        self.conn.commit()

    def add_feedback(self, feedback: Feedback) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO feedback (run_id, outcome, human_minutes, by, note, at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                feedback.run_id,
                feedback.outcome,
                feedback.human_minutes,
                feedback.by,
                feedback.note,
                feedback.at.isoformat(),
            ),
        )
        self.conn.commit()

    # ------------------------------------------------------------------- read

    def runs(self, since: datetime | None = None, workflow_id: str | None = None) -> list[Run]:
        query = "SELECT * FROM runs"
        clauses, params = [], []
        if since is not None:
            clauses.append("started_at >= ?")
            params.append(since.isoformat())
        if workflow_id is not None:
            clauses.append("workflow_id = ?")
            params.append(workflow_id)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY started_at"
        return [_row_to_run(row) for row in self.conn.execute(query, params)]

    def run(self, run_id: str) -> Run | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_run(row) if row else None

    def latest_runs(self, limit: int = 10) -> list[Run]:
        rows = self.conn.execute(
            "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [_row_to_run(row) for row in rows]

    def feedback_map(self) -> dict[str, Feedback]:
        rows = self.conn.execute("SELECT * FROM feedback").fetchall()
        return {row["run_id"]: _row_to_feedback(row) for row in rows}

    def month_cost(self, workflow_id: str, year: int, month: int) -> float:
        """AI spend already committed for a workflow in a calendar month — the
        number the budget guardrail compares against BEFORE allowing a new run."""
        prefix = f"{year:04d}-{month:02d}"
        row = self.conn.execute(
            "SELECT COALESCE(SUM(cost), 0) AS total FROM runs "
            "WHERE workflow_id = ? AND substr(started_at, 1, 7) = ?",
            (workflow_id, prefix),
        ).fetchone()
        return float(row["total"])


def _row_to_run(row: sqlite3.Row) -> Run:
    return Run(
        id=row["id"],
        workflow_id=row["workflow_id"],
        user=row["user"],
        started_at=datetime.fromisoformat(row["started_at"]),
        finished_at=datetime.fromisoformat(row["finished_at"]),
        status=row["status"],
        model_id=row["model_id"],
        provider=row["provider"],
        tokens_in=row["tokens_in"],
        tokens_out=row["tokens_out"],
        cost=row["cost"],
        latency_ms=row["latency_ms"],
        redactions=row["redactions"],
        reason=row["reason"],
        output=row["output"],
    )


def _row_to_feedback(row: sqlite3.Row) -> Feedback:
    return Feedback(
        run_id=row["run_id"],
        outcome=row["outcome"],
        human_minutes=row["human_minutes"],
        by=row["by"],
        note=row["note"],
        at=datetime.fromisoformat(row["at"]),
    )

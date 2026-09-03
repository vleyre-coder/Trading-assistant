"""Stockage local SQLite : historique des scores, watchlist, alertes.

Tout reste sur la machine : aucune donnee n'est transmise a un tiers.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from .models import CriterionResult, PillarResult, StockScore

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    universes    TEXT NOT NULL,
    n_analyzed   INTEGER DEFAULT 0,
    n_ranked     INTEGER DEFAULT 0,
    notes        TEXT DEFAULT '',
    -- Medianes de P/E par secteur de cette execution : conservees pour que
    -- le critere « P/E vs secteur » reste calculable apres redemarrage,
    -- sans relancer une analyse d'univers complete.
    sector_medians_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS scores (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id            INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ticker            TEXT NOT NULL,
    computed_at       TEXT NOT NULL,
    composite         REAL,
    rank              INTEGER,
    window_years      INTEGER,
    coverage          REAL,
    ranked            INTEGER NOT NULL DEFAULT 0,
    exclusion_reason  TEXT DEFAULT '',
    name              TEXT,
    sector            TEXT,
    region            TEXT,
    country           TEXT,
    sector_rank       INTEGER,
    sector_count      INTEGER,
    currency          TEXT,
    price             REAL,
    detail_json       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scores_ticker ON scores(ticker, computed_at);
CREATE INDEX IF NOT EXISTS idx_scores_run ON scores(run_id);

CREATE TABLE IF NOT EXISTS criteria_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    ticker      TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    criterion   TEXT NOT NULL,
    value       REAL,
    score       REAL
);
CREATE INDEX IF NOT EXISTS idx_criteria_ticker ON criteria_history(ticker, criterion, computed_at);

CREATE TABLE IF NOT EXISTS watchlist (
    ticker    TEXT PRIMARY KEY,
    added_at  TEXT NOT NULL,
    note      TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS alert_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker      TEXT NOT NULL,
    kind        TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL,
    last_fired  TEXT,
    -- Etat du seuil a la derniere evaluation ("crossed" / "clear"). Sans
    -- cette memoire, une regle "cours au-dessous de 300" se redeclencherait
    -- a chaque execution tant que le cours reste sous 300 : l'utilisateur
    -- recevrait la meme alerte tous les jours.
    last_state  TEXT
);
CREATE INDEX IF NOT EXISTS idx_rules_ticker ON alert_rules(ticker);

CREATE TABLE IF NOT EXISTS alert_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id      INTEGER REFERENCES alert_rules(id) ON DELETE SET NULL,
    ticker       TEXT NOT NULL,
    kind         TEXT NOT NULL,
    message      TEXT NOT NULL,
    triggered_at TEXT NOT NULL,
    delivered    INTEGER NOT NULL DEFAULT 0,
    channel      TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events_time ON alert_events(triggered_at);

CREATE TABLE IF NOT EXISTS earnings_seen (
    ticker      TEXT NOT NULL,
    last_report TEXT NOT NULL,
    seen_at     TEXT NOT NULL,
    PRIMARY KEY (ticker)
);
"""

# Types d'alertes reconnus.
ALERT_KINDS = (
    "price_above",
    "price_below",
    "score_change",
    "earnings_published",
    "top_n_entry",
    "top_n_exit",
)


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate(conn)

    @staticmethod
    def _migrate(conn: sqlite3.Connection) -> None:
        """Ajout des colonnes apparues apres la creation d'une base existante."""
        colonnes = {
            row["name"] for row in conn.execute("PRAGMA table_info(alert_rules)").fetchall()
        }
        if "last_state" not in colonnes:
            conn.execute("ALTER TABLE alert_rules ADD COLUMN last_state TEXT")
        colonnes_runs = {
            row["name"] for row in conn.execute("PRAGMA table_info(runs)").fetchall()
        }
        if "sector_medians_json" not in colonnes_runs:
            conn.execute(
                "ALTER TABLE runs ADD COLUMN sector_medians_json TEXT DEFAULT '{}'"
            )
        # Pays de l'emetteur et rang sectoriel : ajoutes apres coup, donc
        # absents des bases creees par une version anterieure. Sans cette
        # migration, l'enregistrement echouerait sur une base existante.
        colonnes_scores = {
            row["name"] for row in conn.execute("PRAGMA table_info(scores)").fetchall()
        }
        for colonne, definition in (
            ("country", "TEXT"),
            ("sector_rank", "INTEGER"),
            ("sector_count", "INTEGER"),
        ):
            if colonne not in colonnes_scores:
                conn.execute(f"ALTER TABLE scores ADD COLUMN {colonne} {definition}")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------- runs
    def start_run(self, universes: list[str]) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "INSERT INTO runs (started_at, universes) VALUES (?, ?)",
                (datetime.now().isoformat(timespec="seconds"), ",".join(universes)),
            )
            return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        n_analyzed: int,
        n_ranked: int,
        notes: str = "",
        sector_medians: dict[str, float] | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """UPDATE runs SET finished_at = ?, n_analyzed = ?, n_ranked = ?,
                   notes = ?, sector_medians_json = ? WHERE id = ?""",
                (
                    datetime.now().isoformat(timespec="seconds"),
                    n_analyzed,
                    n_ranked,
                    notes,
                    json.dumps(sector_medians or {}),
                    run_id,
                ),
            )

    def last_run(self) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM runs WHERE finished_at IS NOT NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()

    def runs(self, limit: int = 50) -> list[sqlite3.Row]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()

    # ----------------------------------------------------------- scores
    def save_scores(self, run_id: int, scores: list[StockScore], ranks: dict[str, int]) -> None:
        rows = []
        criteria_rows = []
        for s in scores:
            computed = (s.computed_at or datetime.now()).isoformat(timespec="seconds")
            rows.append(
                (
                    run_id, s.ticker, computed, s.composite, ranks.get(s.ticker),
                    s.window_years, s.coverage, int(s.ranked), s.exclusion_reason,
                    s.name, s.sector, s.region, s.currency, s.price,
                    s.country, s.sector_rank, s.sector_count,
                    json.dumps(_score_payload(s), ensure_ascii=False),
                )
            )
            for c in s.criteria_flat():
                criteria_rows.append((run_id, s.ticker, computed, c.key, c.value, c.score))
        with self.connect() as conn:
            conn.executemany(
                """INSERT INTO scores (run_id, ticker, computed_at, composite, rank,
                       window_years, coverage, ranked, exclusion_reason, name, sector,
                       region, currency, price, country, sector_rank, sector_count,
                       detail_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                rows,
            )
            conn.executemany(
                """INSERT INTO criteria_history (run_id, ticker, computed_at, criterion, value, score)
                   VALUES (?,?,?,?,?,?)""",
                criteria_rows,
            )

    def scores_for_run(self, run_id: int) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM scores WHERE run_id = ?", (run_id,)).fetchall()
        return [dict(r) for r in rows]

    def score_history(self, ticker: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT computed_at, composite, rank, window_years, coverage, ranked
                   FROM scores WHERE ticker = ? ORDER BY computed_at DESC LIMIT ?""",
                (ticker, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def criterion_history(self, ticker: str, criterion: str, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as conn:
            rows = conn.execute(
                """SELECT computed_at, value, score FROM criteria_history
                   WHERE ticker = ? AND criterion = ? ORDER BY computed_at DESC LIMIT ?""",
                (ticker, criterion, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def previous_snapshot(self, run_id: int) -> dict[str, dict[str, Any]]:
        """Scores et rangs de l'execution complete precedente (pour les alertes)."""
        with self.connect() as conn:
            previous = conn.execute(
                """SELECT id FROM runs WHERE id < ? AND finished_at IS NOT NULL
                   ORDER BY id DESC LIMIT 1""",
                (run_id,),
            ).fetchone()
            if previous is None:
                return {}
            rows = conn.execute(
                "SELECT ticker, composite, rank, ranked FROM scores WHERE run_id = ?",
                (previous["id"],),
            ).fetchall()
        return {r["ticker"]: dict(r) for r in rows}

    # -------------------------------------------------------- watchlist
    def watchlist(self) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(
                "SELECT * FROM watchlist ORDER BY ticker"
            ).fetchall()]

    def add_to_watchlist(self, ticker: str, note: str = "") -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO watchlist (ticker, added_at, note) VALUES (?,?,?)
                   ON CONFLICT(ticker) DO UPDATE SET note = excluded.note""",
                (ticker.upper(), datetime.now().isoformat(timespec="seconds"), note),
            )

    def remove_from_watchlist(self, ticker: str) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker.upper(),))

    # ----------------------------------------------------------- alertes
    def add_alert_rule(self, ticker: str, kind: str, params: dict[str, Any]) -> int:
        if kind not in ALERT_KINDS:
            raise ValueError(f"Type d'alerte inconnu : {kind} (attendu : {ALERT_KINDS})")
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO alert_rules (ticker, kind, params_json, created_at)
                   VALUES (?,?,?,?)""",
                (
                    ticker.upper(),
                    kind,
                    json.dumps(params, ensure_ascii=False),
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            return int(cur.lastrowid)

    def alert_rules(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM alert_rules"
        if enabled_only:
            query += " WHERE enabled = 1"
        query += " ORDER BY ticker, kind"
        with self.connect() as conn:
            rows = conn.execute(query).fetchall()
        out = []
        for r in rows:
            item = dict(r)
            item["params"] = json.loads(item.pop("params_json") or "{}")
            out.append(item)
        return out

    def set_rule_enabled(self, rule_id: int, enabled: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE alert_rules SET enabled = ? WHERE id = ?", (int(enabled), rule_id)
            )

    def delete_alert_rule(self, rule_id: int) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))

    def set_rule_state(self, rule_id: int, state: str) -> None:
        """Memorise l'etat du seuil pour ne notifier qu'au franchissement."""
        with self.connect() as conn:
            conn.execute(
                "UPDATE alert_rules SET last_state = ? WHERE id = ?", (state, rule_id)
            )

    def mark_rule_fired(self, rule_id: int) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE alert_rules SET last_fired = ? WHERE id = ?",
                (datetime.now().isoformat(timespec="seconds"), rule_id),
            )

    def record_event(
        self, ticker: str, kind: str, message: str, rule_id: int | None = None
    ) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """INSERT INTO alert_events (rule_id, ticker, kind, message, triggered_at)
                   VALUES (?,?,?,?,?)""",
                (
                    rule_id,
                    ticker.upper(),
                    kind,
                    message,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )
            return int(cur.lastrowid)

    def mark_delivered(self, event_ids: list[int], channel: str) -> None:
        if not event_ids:
            return
        with self.connect() as conn:
            conn.executemany(
                "UPDATE alert_events SET delivered = 1, channel = ? WHERE id = ?",
                [(channel, eid) for eid in event_ids],
            )

    def events(self, limit: int = 100, undelivered_only: bool = False) -> list[dict[str, Any]]:
        query = "SELECT * FROM alert_events"
        if undelivered_only:
            query += " WHERE delivered = 0"
        query += " ORDER BY id DESC LIMIT ?"
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(query, (limit,)).fetchall()]

    # --------------------------------------------------- publications
    def last_earnings_seen(self, ticker: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT last_report FROM earnings_seen WHERE ticker = ?", (ticker.upper(),)
            ).fetchone()
        return row["last_report"] if row else None

    def set_last_earnings_seen(self, ticker: str, last_report: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """INSERT INTO earnings_seen (ticker, last_report, seen_at) VALUES (?,?,?)
                   ON CONFLICT(ticker) DO UPDATE SET last_report = excluded.last_report,
                   seen_at = excluded.seen_at""",
                (
                    ticker.upper(),
                    last_report,
                    datetime.now().isoformat(timespec="seconds"),
                ),
            )


def score_from_row(row: dict[str, Any]) -> StockScore:
    """Reconstruit un StockScore complet a partir d'une ligne enregistree.

    Permet d'afficher le dernier classement des l'ouverture de l'application,
    sans relancer une analyse : une execution planifiee la nuit devient donc
    directement consultable le matin.
    """
    payload = json.loads(row.get("detail_json") or "{}")
    pillars: dict[str, PillarResult] = {}
    for key, bloc in (payload.get("pillars") or {}).items():
        pillars[key] = PillarResult(
            key=key,
            weight=float(bloc.get("weight", 0.0)),
            score=bloc.get("score"),
            coverage=float(bloc.get("coverage", 0.0)),
            neutralized=bool(bloc.get("neutralized", False)),
            criteria=[
                CriterionResult(
                    key=c["key"], label=c["label"], unit=c.get("unit", "ratio"),
                    value=c.get("value"), score=c.get("score"),
                    weight=float(c.get("weight", 0.0)), pillar=key,
                    detail=c.get("detail", ""), reason_missing=c.get("reason_missing", ""),
                    not_applicable=bool(c.get("not_applicable", False)),
                )
                for c in bloc.get("criteria", [])
            ],
        )
    computed = row.get("computed_at")
    return StockScore(
        ticker=row["ticker"], name=row.get("name"), sector=row.get("sector"),
        region=row.get("region"), currency=row.get("currency"), price=row.get("price"),
        composite=row.get("composite"),
        country=row.get("country"),
        sector_rank=row.get("sector_rank"),
        sector_count=row.get("sector_count"),
        pillars=pillars,
        window_years=int(row.get("window_years") or 0),
        coverage=float(row.get("coverage") or 0.0),
        ranked=bool(row.get("ranked")),
        exclusion_reason=row.get("exclusion_reason") or "",
        warnings=list(payload.get("warnings") or []),
        computed_at=datetime.fromisoformat(computed) if computed else None,
    )


def _score_payload(score: StockScore) -> dict[str, Any]:
    """Serialisation du detail par critere, pour re-affichage sans recalcul."""
    return {
        "composite": score.composite,
        "window_years": score.window_years,
        "coverage": score.coverage,
        "warnings": score.warnings,
        "pillars": {
            key: {
                "score": p.score,
                "weight": p.weight,
                "coverage": p.coverage,
                "neutralized": p.neutralized,
                "criteria": [
                    {
                        "key": c.key,
                        "label": c.label,
                        "unit": c.unit,
                        "value": c.value,
                        "score": c.score,
                        "weight": c.weight,
                        "detail": c.detail,
                        "reason_missing": c.reason_missing,
                        "not_applicable": c.not_applicable,
                    }
                    for c in p.criteria
                ],
            }
            for key, p in score.pillars.items()
        },
    }

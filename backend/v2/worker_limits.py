"""Local rolling budgets, containing only operation types and timestamps."""
import math
from pathlib import Path
import sqlite3
import threading
import time

WORKER_RULES = ((('search',), 3600, 30), (('import',), 3600, 60), (('search', 'import'), 86400, 240))
GATEWAY_RULES = ((('source',), 60, 60), (('source',), 3600, 1200), (('source',), 86400, 6000),
                 (('cloud',), 60, 60), (('cloud',), 86400, 12000))


class BudgetError(ValueError):
    def __init__(self, code='worker_budget_unavailable'):
        self.code = code
        super().__init__(code)


class RollingBudget:
    def __init__(self, rules=WORKER_RULES, *, path=None, clock=time.time):
        self.rules, self.clock, self.lock = rules, clock, threading.Lock()
        self.kinds = frozenset(kind for kinds, _, _ in rules for kind in kinds)
        try:
            if path is not None:
                target = Path(path).absolute()
                if str(target).startswith(('\\\\', '//')):
                    raise BudgetError()
                for part in (target, *target.parents):
                    if part.is_symlink() or (hasattr(part, 'is_junction') and part.is_junction()):
                        raise BudgetError()
                if target.exists() and target.stat().st_size > 4 * 1024 * 1024:
                    raise BudgetError()
                path = str(target)
            self.db = sqlite3.connect(path or ':memory:', timeout=2, check_same_thread=False)
            self.db.execute('PRAGMA trusted_schema=OFF')
            self.db.execute('PRAGMA max_page_count=1024')
            self.db.execute('CREATE TABLE IF NOT EXISTS budget_events (kind TEXT NOT NULL, at REAL NOT NULL)')
            self.db.execute('CREATE INDEX IF NOT EXISTS budget_window ON budget_events(kind, at)')
            self.db.execute('CREATE TABLE IF NOT EXISTS budget_clock (id INTEGER PRIMARY KEY CHECK(id=1), at REAL NOT NULL)')
            self.db.commit()
        except (OSError, sqlite3.Error):
            raise BudgetError() from None

    def admit(self, kind):
        if kind not in self.kinds:
            raise BudgetError()
        now = self.clock()
        if not isinstance(now, (int, float)) or not math.isfinite(now):
            raise BudgetError()
        try:
            with self.lock, self.db:
                self.db.execute('BEGIN IMMEDIATE')
                last = self.db.execute('SELECT at FROM budget_clock WHERE id=1').fetchone()
                if last and now < last[0] - 5:
                    raise BudgetError()
                now = max(now, last[0]) if last else now
                self.db.execute('DELETE FROM budget_events WHERE at <= ?', (now - 86400,))
                self.db.execute('INSERT OR REPLACE INTO budget_clock VALUES (1, ?)', (now,))
                if self.db.execute('SELECT count(*) FROM budget_events').fetchone()[0] > 20000:
                    raise BudgetError()
                for kinds, seconds, limit in self.rules:
                    if kind not in kinds:
                        continue
                    marks = ','.join('?' for _ in kinds)
                    count = self.db.execute(f'SELECT count(*) FROM budget_events WHERE kind IN ({marks}) AND at > ?',
                                            (*kinds, now - seconds)).fetchone()[0]
                    if count >= limit:
                        raise BudgetError('worker_rate_limited')
                self.db.execute('INSERT INTO budget_events VALUES (?, ?)', (kind, now))
        except sqlite3.Error:
            raise BudgetError() from None

    def close(self):
        self.db.close()

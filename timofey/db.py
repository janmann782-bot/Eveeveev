"""Small bounded SQLite operations; transactions never span an await."""

import json
import sqlite3
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path | str):
        self.conn = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY, created REAL NOT NULL, published REAL,
          state TEXT NOT NULL DEFAULT 'draft', message_id INTEGER UNIQUE,
          body TEXT NOT NULL, payload TEXT NOT NULL, kind TEXT NOT NULL,
          topic TEXT NOT NULL, location TEXT NOT NULL, story TEXT NOT NULL,
          depth INTEGER NOT NULL, canon_check TEXT NOT NULL DEFAULT 'pending');
        CREATE UNIQUE INDEX IF NOT EXISTS event_body_unique ON events(body);
        CREATE TABLE IF NOT EXISTS memory(
          id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, role TEXT NOT NULL,
          body TEXT NOT NULL, created REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS memory_user ON memory(user_id,id);
        CREATE TABLE IF NOT EXISTS messages(
          message_id INTEGER PRIMARY KEY, root_id INTEGER NOT NULL, body TEXT NOT NULL,
          created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS roots(
          message_id INTEGER PRIMARY KEY, channel_message_id INTEGER NOT NULL, body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY, created REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS cooldown(key TEXT PRIMARY KEY, until REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS calls(
          id INTEGER PRIMARY KEY, created REAL NOT NULL, purpose TEXT NOT NULL,
          reserved INTEGER NOT NULL, prompt INTEGER, completion INTEGER, total INTEGER,
          state TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS errors(
          id INTEGER PRIMARY KEY, created REAL NOT NULL, component TEXT, code TEXT);
        """)
        # A process that died after sending may have published: never retry blindly.
        self.conn.execute("UPDATE events SET state='uncertain' WHERE state='sending'")
        self.conn.execute("UPDATE events SET state='draft' WHERE state='editing'")

    def close(self):
        self.conn.close()

    def get(self, key, default=None):
        row = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        self.conn.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def error(self, component, code):
        # Store codes, never raw exceptions, prompts, URLs or response bodies containing secrets.
        self.conn.execute(
            "INSERT INTO errors(created,component,code) VALUES (?,?,?)",
            (time.time(), component[:40], code[:80]),
        )
        self.conn.execute(
            "DELETE FROM errors WHERE id NOT IN (SELECT id FROM errors ORDER BY id DESC LIMIT 200)"
        )

    def once(self, key):
        cur = self.conn.execute("INSERT OR IGNORE INTO seen VALUES (?,?)", (key, time.time()))
        return bool(cur.rowcount)

    def cooling(self, key):
        row = self.conn.execute("SELECT until FROM cooldown WHERE key=?", (key,)).fetchone()
        return bool(row and row[0] > time.time())

    def cool(self, key, seconds):
        self.conn.execute(
            "INSERT OR REPLACE INTO cooldown VALUES (?,?)", (key, time.time() + seconds)
        )

    def remember(self, user_id, role, body, limit=20, days=30):
        now = time.time()
        self.conn.execute(
            "INSERT INTO memory(user_id,role,body,created) VALUES (?,?,?,?)",
            (user_id, role, body[:2000], now),
        )
        self.conn.execute(
            "DELETE FROM memory WHERE user_id=? AND id NOT IN "
            "(SELECT id FROM memory WHERE user_id=? ORDER BY id DESC LIMIT ?)",
            (user_id, user_id, limit),
        )
        self.prune(days)

    def prune(self, days=30):
        cutoff = time.time() - days * 86400
        for table in ("memory", "messages", "seen"):
            self.conn.execute(f"DELETE FROM {table} WHERE created<?", (cutoff,))
        self.conn.execute("DELETE FROM cooldown WHERE until<?", (time.time(),))

    def history(self, user_id, limit=20):
        rows = self.conn.execute(
            "SELECT role,body FROM memory WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def reserve(self, purpose, amount, budget, rpm):
        now = time.time()
        day = now - now % 86400
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            # Comment traffic never consumes the local news quota.
            count = self.conn.execute(
                "SELECT count(*) FROM calls WHERE created>? AND purpose NOT IN ('comment','comment_audit')",
                (now - 60,),
            ).fetchone()[0]
            spent = self.conn.execute(
                "SELECT coalesce(sum(coalesce(total,reserved)),0) FROM calls "
                "WHERE created>=? AND purpose NOT IN ('comment','comment_audit')",
                (day,),
            ).fetchone()[0]
            # Comments (and their canon checks) are not throttled by local
            # per-minute or daily quotas. They remain logged and accounted for.
            # Provider-side 429 limits and free-only route protection remain.
            exempt = purpose in ("comment", "comment_audit")
            if not exempt and (count >= rpm or spent + amount > budget):
                raise ValueError("LOCAL_BUDGET_OR_RATE_LIMIT")
            cur = self.conn.execute(
                "INSERT INTO calls(created,purpose,reserved) VALUES (?,?,?)", (now, purpose, amount)
            )
            self.conn.execute("COMMIT")
            return cur.lastrowid
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def settle(self, call_id, usage=None):
        if usage is None:
            # Failed/ambiguous calls retain their reservation, including across restart.
            self.conn.execute("UPDATE calls SET state='unknown' WHERE id=?", (call_id,))
        else:
            self.conn.execute(
                "UPDATE calls SET state='done',prompt=?,completion=?,total=? WHERE id=?",
                (
                    usage["prompt_tokens"],
                    usage["completion_tokens"],
                    usage["total_tokens"],
                    call_id,
                ),
            )

    def recent(self, limit=30):
        return [
            dict(r)
            for r in self.conn.execute(
                "SELECT * FROM events WHERE state='published' ORDER BY published DESC LIMIT ?",
                (limit,),
            )
        ]

    def event(self, event_id):
        row = self.conn.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return dict(row) if row else None

    def draft(self, data, kind, topic, location, story, depth):
        cur = self.conn.execute(
            "INSERT INTO events(created,body,payload,kind,topic,location,story,depth,canon_check) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                time.time(),
                data["text"],
                json.dumps(data, ensure_ascii=False),
                kind,
                topic,
                location,
                story,
                depth,
                "passed",
            ),
        )
        return cur.lastrowid

    def claim_publication(self, event_id):
        return bool(
            self.conn.execute(
                "UPDATE events SET state='sending' WHERE id=? AND state='draft' AND canon_check='passed'",
                (event_id,),
            ).rowcount
        )

    def published(self, event_id, message_id):
        self.conn.execute(
            "UPDATE events SET state='published',published=?,message_id=? WHERE id=?",
            (time.time(), message_id, event_id),
        )
        row = self.event(event_id)
        if row["kind"] == "crossover":
            for related_id in json.loads(row["payload"]).get("related_events", []):
                parent = self.event(related_id)
                if parent:
                    data = json.loads(parent["payload"])
                    data["story_status"] = "closed"
                    self.conn.execute(
                        "UPDATE events SET payload=? WHERE id=?",
                        (json.dumps(data, ensure_ascii=False), related_id),
                    )

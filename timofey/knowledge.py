"""Local retrieval: Russian prefix tokens + SQLite FTS5 BM25, no paid embeddings."""

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path


def terms(text):
    return list(dict.fromkeys(re.findall(r"[а-яa-z0-9]{3,}", text.lower().replace("ё", "е"))))


def chunks(text, size=1800):
    text = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"<[^>]+>", " ", text)
    lines, acc = text.splitlines(), ""
    for line in lines:
        if len(acc) + len(line) > size and acc:
            yield acc.strip()
            acc = ""
        for offset in range(0, max(1, len(line)), size):
            part = line[offset : offset + size]
            if len(acc) + len(part) > size:
                if acc.strip():
                    yield acc.strip()
                acc = ""
            acc += part + "\n"
    if acc.strip():
        yield acc.strip()


def import_xml(source, output):
    root = ET.parse(source).getroot()
    ns = {"m": root.tag.split("}")[0][1:]}
    records = []
    for page in root.findall("m:page", ns):
        title = page.findtext("m:title", default="", namespaces=ns)
        namespace = page.findtext("m:ns", default="0", namespaces=ns)
        body = page.findtext("m:revision/m:text", default="", namespaces=ns)
        if namespace != "0" or body.lstrip().lower().startswith(("#redirect", "#перенаправление")):
            continue
        for i, part in enumerate(chunks(body)):
            records.append(
                {
                    "id": hashlib.sha256((title + str(i) + part).encode()).hexdigest()[:24],
                    "title": title,
                    "text": part,
                    "source": "Aurelia_Wiki_updated.xml",
                    "source_date": "2026-10-29",
                    "layer": "canon",
                }
            )
    Path(output).write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8"
    )
    return records


class Knowledge:
    def __init__(self, store):
        self.db = store.conn
        self.db.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS canon USING fts5(id UNINDEXED,title,text,source UNINDEXED, tokenize='unicode61')"
        )

    def load(self, path):
        raw = Path(path).read_bytes()
        anchors_path = Path(path).with_name("author_facts.json")
        anchors_raw = anchors_path.read_bytes() if anchors_path.exists() else b""
        digest = hashlib.sha256(raw + anchors_raw).hexdigest()
        prev = self.db.execute("SELECT value FROM settings WHERE key='canon_digest'").fetchone()
        if prev and json.loads(prev[0]) == digest:
            return
        records = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
        if anchors_raw:
            records.insert(0, json.loads(anchors_raw))
        if not records or any(r.get("layer") != "canon" for r in records):
            raise ValueError("Canon must be nonempty and explicitly marked")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self.db.execute("DELETE FROM canon WHERE source!='owner:telegram'")
            self.db.executemany(
                "INSERT INTO canon(id,title,text,source) VALUES (?,?,?,?)",
                [(r["id"], r["title"], r["text"], r["source"]) for r in records],
            )
            self.db.execute(
                "INSERT OR REPLACE INTO settings VALUES ('canon_digest',?)", (json.dumps(digest),)
            )
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def search(self, query, limit=8):
        tokens = terms(query)[:30]
        if not tokens:
            return []
        # Prefixes handle inflection (Рализму -> рализ*, выборы -> выбор*).
        expression = " OR ".join('"' + t[: max(4, len(t) - 2)] + '"*' for t in tokens)
        rows = self.db.execute(
            "SELECT id,title,text,source FROM canon WHERE canon MATCH ? ORDER BY bm25(canon,0,5,1,0) LIMIT ?",
            (expression, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def foundational(self):
        result = []
        result.extend(
            dict(r)
            for r in self.db.execute(
                "SELECT id,title,text,source FROM canon WHERE title='Подтвержденные автором ориентиры Аурелии' LIMIT 1"
            )
        )
        for title in ("Тимофей Рализм", "Кефирстан", "Либертарианская Партия Кефирстана"):
            result.extend(
                dict(r)
                for r in self.db.execute(
                    "SELECT id,title,text,source FROM canon WHERE title=? LIMIT 2", (title,)
                )
            )
        return result

    def add(self, title, text):
        for i, part in enumerate(chunks(text[:100000])):
            key = "owner-" + hashlib.sha256((title + str(i) + part).encode()).hexdigest()[:24]
            self.db.execute(
                "INSERT INTO canon VALUES (?,?,?,?)", (key, title[:200], part, "owner:telegram")
            )

    def context(self, query, budget=14000):
        result, seen = [], set()
        for record in self.foundational() + self.search(query, 10):
            if record["id"] in seen:
                continue
            seen.add(record["id"])
            if len(record["text"]) > budget:
                continue
            result.append(record)
            budget -= len(record["text"])
        return result

"""Frozen document samples and human annotations, separate from extraction proposals."""
from __future__ import annotations

from collections import Counter
from copy import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import uuid

from .legal_claims import LegalClaimStore, KINDS
from . import legal_rules


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _hash(raw):
    return hashlib.sha256(raw).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


class LegalReviewStore:
    """A campaign freezes its population, sampled bytes and first completed receipts.

    Human corrections never rewrite model receipts or promote graph claims. Review
    revisions are append-only; optimistic concurrency prevents silent lost edits.
    """

    def __init__(self, workspace):
        self.root = Path(workspace).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.root / "legal-review.sqlite")
        self.db.row_factory = sqlite3.Row
        self.ledger = LegalClaimStore(self.root / "legal-claims.sqlite")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS campaigns (
                campaign_id TEXT PRIMARY KEY, record TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS items (
                campaign_id TEXT NOT NULL REFERENCES campaigns, item_id TEXT NOT NULL,
                record TEXT NOT NULL, revision_id TEXT, version INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(campaign_id, item_id));
            CREATE TABLE IF NOT EXISTS reviews (
                campaign_id TEXT NOT NULL, item_id TEXT NOT NULL, version INTEGER NOT NULL,
                record TEXT NOT NULL, PRIMARY KEY(campaign_id, item_id, version),
                FOREIGN KEY(campaign_id, item_id) REFERENCES items);
        """)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.db.close()
        self.ledger.__exit__()

    def create(self, path, sample_size=24, seed=None, model="grap-legal-gemma4", max_chars=100000):
        root = Path(path).resolve()
        if not root.is_dir() or not 1 <= sample_size <= 5000 or not 1 <= max_chars <= 100000:
            raise ValueError("Choose a corpus directory, sample size 1..5000 and max_chars 1..100000")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("A model profile is required")
        seed = seed.strip() if isinstance(seed, str) and seed.strip() else secrets.token_hex(12)
        # Include every JSON file, including unreadable/malformed ones. Failures may
        # not be silently replaced by more convenient documents in a random sample.
        manifest = []
        for file in sorted(root.rglob("*.json")):
            row = {"file": file.relative_to(root).as_posix()}
            try:
                raw = file.read_bytes()
                row.update(sha256=_hash(raw), bytes=len(raw))
            except OSError as exc:
                row.update(sha256=None, error=str(exc))
            manifest.append(row)
        if not manifest:
            raise ValueError("No JSON documents found")
        ranked = sorted(manifest, key=lambda r: _hash((seed + "\0" + r["file"]).encode("utf-8")))
        campaign_id = uuid.uuid4().hex
        folder = self.root / "legal-review-sources" / campaign_id
        folder.mkdir(parents=True)
        items = []
        for index, row in enumerate(ranked[:sample_size]):
            item_id = f"{index+1:04}"
            snapshot = folder / (item_id + ".json")
            record = {**row, "original_path": str(root / row["file"]), "snapshot": str(snapshot)}
            try:
                raw = (root / row["file"]).read_bytes()
                if _hash(raw) != row["sha256"]:
                    raise ValueError("Source changed during sampling; create a new campaign")
                snapshot.write_bytes(raw)
                document = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(document, dict) or not document.get("id") or not isinstance(document.get("text_complet"), str):
                    raise ValueError("Expected a JSON object with id and text_complet")
                record.update(title=document.get("titlu") or row["file"], chars=len(document["text_complet"]))
            except (OSError, ValueError) as exc:
                record["error"] = str(exc)
            items.append((campaign_id, item_id, _json(record)))
        campaign = {"campaign_id": campaign_id, "created_at": _now(), "corpus_path": str(root),
                    "seed": seed, "model": model.strip(), "max_chars": max_chars,
                    "extractor_version": legal_rules.VERSION,
                    "generation": {"temperature": 0.0, "max_tokens": 2048, "reasoning_effort": "none", "retries": 0, "timeout_seconds": 90},
                    "population": len(manifest), "sample_size": len(items), "manifest": manifest,
                    "manifest_sha256": _hash(_json(manifest).encode("utf-8")),
                    "sampling": "Uniform document sample without replacement by seeded SHA-256 ordering; all JSON files eligible",
                    "measure": "Human extraction review of frozen sampled documents; not current-law validity or corpus-wide accuracy"}
        with self.db:
            self.db.execute("INSERT INTO campaigns VALUES (?, ?)", (campaign_id, _json(campaign)))
            self.db.executemany("INSERT INTO items(campaign_id,item_id,record) VALUES (?,?,?)", items)
        return self.get(campaign_id)

    def _campaign(self, campaign_id):
        row = self.db.execute("SELECT record FROM campaigns WHERE campaign_id=?", (campaign_id,)).fetchone()
        if not row:
            raise ValueError("Unknown review campaign")
        return json.loads(row[0])

    def _item(self, campaign_id, item_id):
        row = self.db.execute("SELECT * FROM items WHERE campaign_id=? AND item_id=?", (campaign_id, item_id)).fetchone()
        if not row:
            raise ValueError("Unknown review document")
        return dict(row) | {"record": json.loads(row["record"])}

    def _latest(self, row):
        saved = self.db.execute("SELECT record FROM reviews WHERE campaign_id=? AND item_id=? AND version=?",
                                (row["campaign_id"], row["item_id"], row["version"])).fetchone()
        return json.loads(saved[0]) if saved else None

    def _receipt(self, revision_id):
        return self.ledger.receipt(revision_id)

    def _extraction_state(self, record, revision_id):
        if revision_id:
            return {"extraction_status": "ready", "extraction_error": None, "progress": None}
        if record.get("error"):
            return {"extraction_status": "blocked", "extraction_error": record["error"], "progress": None}
        source = self.ledger.db.execute("SELECT * FROM sources WHERE source_file=?", (record["snapshot"],)).fetchone()
        return {"extraction_status": source["status"] if source else "pending",
                "extraction_error": source["error"] if source else None,
                "progress": self.ledger.work_progress(source["source_id"]) if source else None}

    def detail(self, campaign_id, item_id):
        row = self._item(campaign_id, item_id)
        record = row["record"]
        receipt = self._receipt(row["revision_id"]) if row["revision_id"] else None
        source_text = receipt["source_text"] if receipt else ""
        if not receipt and not record.get("error"):
            raw = Path(record["snapshot"]).read_bytes()
            if _hash(raw) != record["sha256"]:
                raise ValueError("Frozen source was modified")
            source_text = json.loads(raw.decode("utf-8-sig"))["text_complet"]
        return {**record, **self._extraction_state(record, row["revision_id"]), "item_id": item_id, "revision_id": row["revision_id"], "version": row["version"],
                "source_text": source_text, "receipt": receipt, "review": self._latest(row)}

    def process(self, campaign_id, item_id, provider_factory):
        campaign, item = self._campaign(campaign_id), self._item(campaign_id, item_id)
        if item["revision_id"]:
            return self.detail(campaign_id, item_id)
        if campaign.get("extractor_version", legal_rules.VERSION) != legal_rules.VERSION:
            raise ValueError("Extractor version changed; create a new campaign to avoid mixing extraction versions")
        record = item["record"]
        if record.get("error"):
            raise ValueError(record["error"])
        path = Path(record["snapshot"])
        if _hash(path.read_bytes()) != record["sha256"]:
            raise ValueError("Frozen source was modified")
        provider = copy(provider_factory(model=campaign["model"]))
        # A review button has a predictable request budget. Preserve caller-owned
        # provider objects, and never retry a long inference behind a Stop control.
        provider.retries, provider.timeout, provider.temperature, provider.max_tokens = 0, 90, 0.0, 2048
        if hasattr(provider, "extra_body"):
            provider.extra_body = {**provider.extra_body, "reasoning_effort": "none"}
        # One resumable batch per call; no review transaction spans model inference.
        with LegalClaimStore(self.root / "legal-claims.sqlite") as ledger:
            result = ledger.ingest_folder(path, provider, max_files=1, max_chars=campaign["max_chars"], max_batches=1)
            source = ledger.db.execute("SELECT * FROM sources WHERE source_file=?", (str(path.resolve()),)).fetchone()
            if source and source["status"] == "ready":
                receipt = ledger.receipt(source["accepted_revision"])
                if receipt["source_sha256"] != record["sha256"]:
                    raise ValueError("Extraction does not match the frozen source")
                with self.db:
                    self.db.execute("UPDATE items SET revision_id=? WHERE campaign_id=? AND item_id=? AND revision_id IS NULL",
                                    (source["accepted_revision"], campaign_id, item_id))
            progress = ledger.work_progress(source["source_id"]) if source else None
        return {**self.detail(campaign_id, item_id), "processing": result, "progress": progress}

    def save(self, campaign_id, item_id, revision_id, expected_version, reviewer, judgments, omissions, complete=False):
        row = self._item(campaign_id, item_id)
        if not revision_id or row["revision_id"] != revision_id:
            raise ValueError("Review must refer to this document's pinned extraction receipt")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 100:
            raise ValueError("Enter a reviewer name (1..100 characters)")
        receipt = self._receipt(revision_id)
        claims = {c["claim_id"]: c for c in receipt["claims"]}
        if not isinstance(judgments, list) or not isinstance(omissions, list):
            raise ValueError("Judgments and omissions must be lists")
        seen, checked = set(), []
        for j in judgments:
            if not isinstance(j, dict) or j.get("claim_id") not in claims or j["claim_id"] in seen:
                raise ValueError("Each judgment must identify a different claim in this receipt")
            seen.add(j["claim_id"])
            for key in ("kind", "subject", "qualifications"):
                if j.get(key) not in ("correct", "incorrect", "uncertain", "unreviewed"):
                    raise ValueError("Choose correct, incorrect, uncertain or unreviewed for each dimension")
            if j.get("corrected_kind") and j["corrected_kind"] not in KINDS:
                raise ValueError("Invalid corrected kind")
            if j.get("corrected_subject") and j["corrected_subject"] not in claims[j["claim_id"]]["statement"]:
                raise ValueError("A corrected subject must be copied from its statement")
            if not isinstance(j.get("note", ""), str) or len(j.get("note", "")) > 3000:
                raise ValueError("Notes must be text up to 3000 characters")
            checked.append({k: j.get(k, "") for k in ("claim_id", "kind", "subject", "qualifications", "corrected_kind", "corrected_subject", "note")})
        missing = []
        for m in omissions:
            if not isinstance(m, dict):
                raise ValueError("Each omission must have an exact source span")
            start, end = m.get("start"), m.get("end")
            if (type(start) is not int or type(end) is not int or not 0 <= start < end <= len(receipt["source_text"])
                    or receipt["source_text"][start:end] != m.get("quote")):
                raise ValueError("Omission evidence must exactly match its full-source offsets")
            if not isinstance(m.get("note", ""), str) or len(m.get("note", "")) > 3000:
                raise ValueError("Omission notes must be text up to 3000 characters")
            missing.append({k: m.get(k, "") for k in ("start", "end", "quote", "note")})
        if complete and (seen != set(claims) or any(j[k] == "unreviewed" for j in checked for k in ("kind", "subject", "qualifications"))):
            raise ValueError("Assess every claim in all three dimensions before completing the document review")
        review = {"reviewer": reviewer.strip(), "saved_at": _now(), "revision_id": revision_id, "version": expected_version+1,
                  "judgments": checked, "omissions": missing, "complete": bool(complete)}
        with self.db:
            changed = self.db.execute("UPDATE items SET version=version+1 WHERE campaign_id=? AND item_id=? AND version=?",
                                      (campaign_id, item_id, expected_version)).rowcount
            if not changed:
                raise ValueError("Review changed in another window; reload before saving")
            self.db.execute("INSERT INTO reviews VALUES (?,?,?,?)", (campaign_id, item_id, expected_version+1, _json(review)))
        return self.detail(campaign_id, item_id)

    def get(self, campaign_id=None, export=False):
        if not campaign_id:
            return {"campaigns": [{k: v for k, v in json.loads(r[0]).items() if k != "manifest"}
                                  for r in self.db.execute("SELECT record FROM campaigns ORDER BY rowid DESC")]}
        campaign = self._campaign(campaign_id)
        dimensions = {k: Counter({v: 0 for v in ("correct", "incorrect", "uncertain", "unreviewed")})
                      for k in ("kind", "subject", "qualifications", "joint")}
        items, ready, completed, omissions = [], 0, 0, 0
        for r in self.db.execute("SELECT * FROM items WHERE campaign_id=? ORDER BY item_id", (campaign_id,)).fetchall():
            row = dict(r)
            record, review = json.loads(row["record"]), self._latest(row)
            receipt = self._receipt(row["revision_id"]) if row["revision_id"] else None
            ready += bool(receipt)
            completed += bool(review and review["complete"])
            omissions += len(review["omissions"]) if review else 0
            judgments = {j["claim_id"]: j for j in review["judgments"]} if review else {}
            for claim in receipt["claims"] if receipt else []:
                states = [judgments.get(claim["claim_id"], {}).get(k, "unreviewed") for k in ("kind", "subject", "qualifications")]
                for key, state in zip(("kind", "subject", "qualifications"), states):
                    dimensions[key][state] += 1
                joint = next((s for s in ("unreviewed", "incorrect", "uncertain") if s in states), "correct")
                dimensions["joint"][joint] += 1
            item = {**record, **self._extraction_state(record, row["revision_id"]), "item_id": row["item_id"], "revision_id": row["revision_id"],
                    "version": row["version"], "claim_count": len(receipt["claims"]) if receipt else None,
                    "complete": bool(review and review["complete"])}
            if export:
                item.update(receipt=receipt, review=review, history=[json.loads(h[0]) for h in self.db.execute(
                    "SELECT record FROM reviews WHERE campaign_id=? AND item_id=? ORDER BY version", (campaign_id, row["item_id"]))])
                item["source_text"] = self.detail(campaign_id, row["item_id"])["source_text"]
            items.append(item)
        summary = {"population_documents": campaign["population"], "sample_documents": len(items),
                   "extracted_documents": ready, "completed_document_reviews": completed,
                   "unprocessed_documents": len(items)-ready, "reported_omissions": omissions,
                   "claim_judgments": dimensions,
                   "interpretation": "Counts describe this frozen sample. Unreviewed and uncertain claims are not successes. Omissions require a full-source human check; no corpus accuracy estimate is computed."}
        return {"campaign": {k: v for k, v in campaign.items() if export or k != "manifest"}, "items": items, "summary": summary}

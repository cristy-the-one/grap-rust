"""Preserve legal propositions independently of entity graph projection.

Plans refer to full-document spans, even when the accompanying entity chunk
ends mid-rule. Classification shares the entity request; no second model call.
"""
import copy
import hashlib
import json
import re
from bisect import bisect_left, bisect_right

from . import legal_rules

VERSION = "qualified-rules-v2"
_LEGAL = re.compile(r"\bART\.(?=\s*\d)|\b(?:Articolul|se interzice|este interzis|au obliga[țţ]ia|potrivit legii|cu excep[țţ]ia)\b", re.I)
_PROTECTED = {*legal_rules.KINDS, "unclassified"}


def plan(text, source_file=None):
    if not _LEGAL.search(text):
        return None
    units, definitions = legal_rules.source_units(text)
    return {"text": text, "source_file": source_file,
            "source_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "units": units, "definitions": definitions,
            "_intervals": {key: ([u["evidence"][0]["start"] for u in rows],
                                   [u["evidence"][0]["end"] for u in rows])
                           for key, rows in (("units", units), ("definitions", definitions))}}


def slice_plan(context, start, end):
    if context is None:
        return None
    result = {**context, "chunk_start": start}
    for key in ("units", "definitions"):
        starts, ends = context["_intervals"][key]
        result[key] = context[key][bisect_right(ends, start):bisect_left(starts, end)]
    return result


def prompt_units(context):
    if context is None:
        return {}
    # Oversized units remain in the receipt as unresolved; never truncate them.
    selected, size = {}, 0
    for unit in context["units"]:
        wire = len(json.dumps(unit["statement"], ensure_ascii=False).encode("utf-8"))
        if len(selected) < 12 and size + wire <= 12000:
            selected[unit["id"]] = unit["statement"]
            size += wire
    return selected


def preserve(context, labels, source):
    """Reuse legal-rule validation, retaining unresolved and nonassertion units."""
    if context is None:
        return [], {}
    from .llm import StubProvider
    sent = prompt_units(context)
    labels = labels if isinstance(labels, dict) else {}
    rules = []
    for unit in context["units"]:
        row = labels.get(unit["id"]) if unit["id"] in sent else None
        valid = (isinstance(row, dict) and {"kind", "subject"}.issubset(row)
                 and row["kind"] in (*legal_rules.KINDS, "other") and isinstance(row["subject"], str))
        selected = {k: row[k] for k in ("kind", "subject")} if valid else {"kind": "other", "subject": ""}
        receipt = legal_rules.extract_batches(
            StubProvider(default=json.dumps({"units": {unit["id"]: selected}})),
            context["text"], [[unit]])
        if receipt["claims"]:
            rule = receipt["claims"][0]
        else:
            rule = {k: copy.deepcopy(v) for k, v in unit.items() if k not in ("id", "word_count")}
            rule["kind"] = "other" if valid else "unclassified"
        if not valid:
            rule["review_issues"].append("unit not classified by model")
        rule["classification_status"] = "classified" if valid else "unresolved"
        if (rule["kind"] == "right" and legal_rules._strong_kind(rule["statement"]) != "right"
                and not re.search(r"\b(?:drept|dreptul|drepturi|beneficiaz[ăa]|[îi]ndrept[ăa][țţ]it)\b", rule["statement"], re.I)):
            rule["classification_status"] = "unresolved"
            rule["review_issues"].append("right label lacks explicit entitlement wording")
        rules.append(rule)
    rules.extend(copy.deepcopy(context["definitions"]))
    for rule in rules:
        span = rule["evidence"][0]
        identity = context["source_file"] or source
        rule["id"] = hashlib.sha256(f"{identity}\0{context['source_hash']}\0{span['start']}:{span['end']}".encode()).hexdigest()
        rule["source"] = source
        rule["source_hash"] = context["source_hash"]
        rule["source_file"] = context["source_file"]
        rule["offset_basis"] = "document" if context["source_file"] else "supplied_text"
        rule.setdefault("classification_status", "explicit-definition")
        rule["review_status"] = "proposed"
    return rules, {"version": VERSION, "units": len(rules),
                   "unresolved": sum(r["classification_status"] == "unresolved" or not r.get("subject")
                                     for r in rules if r["kind"] != "other"),
                   "nonassertions": sum(r["kind"] == "other" for r in rules)}


def attach(fragment, obj, text, source, context=None):
    context = context if context is not None else plan(text)
    if context is None:
        return fragment
    rules, coverage = preserve(context, obj.get("units"), source)
    fragment["qualified_rules"] = rules
    fragment["rule_coverage"] = coverage
    # A quotation containing a condition is not proof that a flat edge retains
    # that condition. Preserve the proposal for review, outside the entity graph.
    protected = [r for r in rules if r["kind"] in _PROTECTED or r["conditions"] or r["exceptions"]]
    def guarded(item):
        quote = item.get("evidence", {}).get("quote", "")
        at = context["text"].find(quote, context.get("chunk_start", 0)) if quote else -1
        return not quote or any(
            (at >= 0 and at < r["evidence"][0]["end"] and at + len(quote) > r["evidence"][0]["start"])
            or quote in r["statement"] or r["statement"] in quote for r in protected)
    deferred = []
    safe = []
    for edge in fragment["relations"]:
        if guarded(edge):
            deferred.append({"kind": "relation", "proposal": edge, "reason": "requires qualified rule interpretation"})
        else:
            safe.append(edge)
    fragment["relations"] = safe
    for outcome in fragment["outcomes"]:
        if outcome["characteristics"] and guarded(outcome):
            deferred.append({"kind": "requirements", "proposal": copy.deepcopy(outcome),
                             "reason": "requirements retained in complete qualified rule"})
            outcome["characteristics"] = []
    # Article labels must not become global entity IDs. Their original proposals
    # still remain reviewable, alongside the source-preserved rules.
    from .extract import is_structural_label
    for kind, items in (("outcome", obj.get("outcomes", [])), ("relation", obj.get("relations", []))):
        for item in items:
            if isinstance(item, dict) and any(is_structural_label(item.get(k, ""))
                                              for k in (("name",) if kind == "outcome" else ("from", "to"))):
                deferred.append({"kind": kind, "proposal": item, "reason": "article reference is not an entity identity"})
    fragment["deferred_proposals"] = deferred
    coverage["deferred_proposals"] = len(deferred)
    return fragment


def repair_labels(provider, fragment, context):
    """One bounded labeling-only retry; never ask the model to rewrite a rule."""
    pending = {r["evidence"][0]["start"] for r in fragment.get("qualified_rules", [])
               if r["classification_status"] == "unresolved" or (r["kind"] != "other" and not r.get("subject"))}
    if not pending or context is None:
        return None
    sent = prompt_units(context)
    batch = [u for u in context["units"] if u["evidence"][0]["start"] in pending and u["id"] in sent]
    if not batch:
        return None
    receipt = legal_rules.extract_batches(provider, context["text"], [batch])
    request = json.loads(receipt["raw_reply"])[0]
    return {"units": json.loads(request["raw_reply"])["units"], "raw_reply": request["raw_reply"],
            "unit_ids": request["unit_ids"]}


def report(records):
    rules, deferred = {}, []
    for source, record in records.items():
        for rule in record.get("qualified_rules", []):
            old = rules.get(rule["id"])
            if old is None:
                rules[rule["id"]] = copy.deepcopy(rule)
            elif old["classification_status"] == "unresolved" and rule["classification_status"] != "unresolved":
                rules[rule["id"]] = copy.deepcopy(rule)
            elif (old["classification_status"] != "unresolved" and rule["classification_status"] != "unresolved"
                  and (old["kind"], old["subject"]) != (rule["kind"], rule["subject"])):
                alternatives = old.setdefault("classification_alternatives", [{"kind": old["kind"], "subject": old["subject"]}])
                alternative = {"kind": rule["kind"], "subject": rule["subject"]}
                if alternative not in alternatives:
                    alternatives.append(alternative)
                old["classification_status"] = "conflict"
                if "conflicting classifications across chunks" not in old["review_issues"]:
                    old["review_issues"].append("conflicting classifications across chunks")
        deferred.extend({"source": source, **p} for p in record.get("deferred_proposals", []))
    return {"rules": list(rules.values()), "deferred_proposals": deferred,
            "coverage": {"rules": len(rules),
                         "unresolved": sum(r["classification_status"] == "conflict" or (r["kind"] != "other" and (r["classification_status"] == "unresolved" or not r.get("subject"))) for r in rules.values()),
                         "legacy_receipts": sum("qualified_rules" not in r and bool(_LEGAL.search(r.get("source_text", ""))) for r in records.values()),
                         "deferred_proposals": len(deferred)}}

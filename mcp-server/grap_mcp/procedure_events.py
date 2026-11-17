"""Events and the evidence snapshot for one procedure run (P-14).

§8 of docs/spec-procedure-runtime.md. The interpreter reports each step.
This module turns that report into a tracked run. It does not check a
draft, choose a template, or call a model.

Every step writes one explanation record and one audit event. The record
carries the step id, the operation, the procedure version, and the hashes
of that step's inputs and outputs. A skipped step, a failed check, and
each refine round are events. ``invoke_model`` also carries the model, the
prompt hash, the token counts, and the duration. When the operations
object sets ``model_step`` during that call, the event takes
``duration_ms``, ``token_counts`` and ``level_ignored`` from it.

``fetch_context`` stores the node keys it returned and each node's content
hash, copied at that moment. A later edit to the graph does not rewrite
the record. ``evidence_diff`` compares the stored hashes with the graph
as it is now.

``Workspace.record_event`` writes an event with no links, and the Python
audit event does not return its details. The step body is therefore an
explanation record (``record_explanation`` / ``get_record``). The audit
line is appended to ``audit/events.jsonl`` in the shape that log already
uses, so ``events_for_run`` and ``events_linked_to`` see the node links.
The Rust log is unchanged.

Judgements this card had to make, and the tests lock in:

- A node link is ``read``. A key is linked when it occurs in the step's
  resolved inputs or outputs and it is a key in the projection. The
  stored ``links`` list is those nodes, sorted by id. The run link and
  the explanation link are audit bookkeeping and are not in that list.
- A skipped step did nothing, so both hashes are ``n/a``. They are not
  the hash of ``None``.
- ``refine`` that runs is one ``procedure_refine_round`` event. A refine
  the guard skips is a skipped step, not a round.
- A failed check is one event after the ``validate_output`` step. A
  string issue is the check name. A mapping uses ``case``, ``check``,
  and ``value``. A missing field is ``n/a``.
- A model receipt is stored as supplied. With none, the model, the token
  counts, and the duration are ``n/a``. The prompt hash is then the
  sha256 of the instruction text.
- A model name other than ``n/a`` or ``stub`` is a real model. The run is
  not replayable, and the note says a second run can differ.
- The evidence snapshot is the grouped node keys ``fetch_context``
  returned. An empty group is a snapshot of nothing, so the hash counts
  are ``n/a``. A context that is not grouped node keys is not a snapshot.
- The workspace run status is ``completed`` or ``failed``.
  ``failed-validation`` is kept on the procedure record. The run kind is
  ``experiment``. ``gaps_found`` stays 0; this run does not count gaps,
  and the summary says ``gaps=n/a``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from grap_mcp.procedure_interpreter import ProcedureRunError, interpret

_STEP_TYPES = ("procedure_step", "procedure_step_skipped", "procedure_refine_round")


@dataclass(frozen=True)
class RecordedRun:
    run_id: str
    status: str
    rounds: int
    events: tuple
    evidence: dict | None
    replayable: bool
    replay_note: str
    explanation_hashes: tuple


def sha256_canonical(value: Any) -> str:
    """sha256 of the canonical JSON of ``value``."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def format_evidence(evidence: Mapping[str, Any] | None) -> str:
    """The evidence line. Nothing stored, or nothing hashed, is ``n/a``."""
    if not evidence or evidence.get("n_nodes", 0) == 0:
        requirements = 0 if not evidence else evidence.get("n_requirements", 0)
        nodes = 0 if not evidence else evidence.get("n_nodes", 0)
        return (
            f"evidence=n/a n_requirements={requirements} n_nodes={nodes} "
            f"n_hashed=n/a n_missing=n/a"
        )
    return (
        f"evidence n_requirements={evidence['n_requirements']} "
        f"n_nodes={evidence['n_nodes']} n_hashed={evidence['n_hashed']} "
        f"n_missing={evidence['n_missing']}"
    )


def format_diff(diff: Mapping[str, Any]) -> str:
    """The comparison line. No recorded nodes means there is nothing to compare."""
    if diff["n_recorded"] == 0:
        return (
            "evidence_diff=n/a n_recorded=0 n_changed=n/a "
            "n_unchanged=n/a n_missing=n/a"
        )
    return (
        f"evidence_diff n_recorded={diff['n_recorded']} n_changed={diff['n_changed']} "
        f"n_unchanged={diff['n_unchanged']} n_missing={diff['n_missing']}"
    )


def evidence_diff(kg, projection: Mapping[str, Any], evidence: Mapping[str, Any] | None) -> dict:
    """Compare stored content hashes with the graph now. Prints the denominators.

    The stored hashes are not rewritten. A node the snapshot does not name
    is not reported.
    """
    recorded_nodes = [] if not evidence else list(evidence.get("nodes") or [])
    if not recorded_nodes:
        result = {
            "changed": [],
            "n_recorded": 0,
            "n_changed": "n/a",
            "n_unchanged": "n/a",
            "n_missing": "n/a",
        }
        print(format_diff(result))
        return result
    by_key = {node["key"]: node for node in projection["nodes"]}
    changed = []
    unchanged = 0
    missing = 0
    for entry in recorded_nodes:
        key = entry["key"]
        recorded_hash = entry["content_hash"]
        node = by_key.get(key)
        if node is None:
            missing += 1
            changed.append({"key": key, "recorded": recorded_hash, "current": "n/a"})
            continue
        current = _content_hash(kg, node)
        if current != recorded_hash:
            changed.append({"key": key, "recorded": recorded_hash, "current": current})
        else:
            unchanged += 1
    result = {
        "changed": changed,
        "n_recorded": len(recorded_nodes),
        "n_changed": len(changed),
        "n_unchanged": unchanged,
        "n_missing": missing,
    }
    print(format_diff(result))
    return result


def load_recorded(workspace, run_id: str) -> RecordedRun:
    """Read a run back from its explanation records. Does not read the graph."""
    run = workspace.get_run(run_id)
    if run is None:
        raise ProcedureRunError(f"run {run_id!r} is not in the workspace")
    payloads = []
    for digest in run.explanation_hashes:
        raw = workspace.get_record(digest)
        if raw is None:
            raise ProcedureRunError(f"run {run_id!r} explanation {digest} is missing")
        payloads.append(json.loads(raw))
    summary = next(
        (item for item in payloads if item.get("event_type") == "procedure_run"),
        None,
    )
    if summary is None:
        raise ProcedureRunError(f"run {run_id!r} has no procedure record")
    events = tuple(item for item in payloads if item.get("event_type") != "procedure_run")
    return RecordedRun(
        run_id=run_id,
        status=summary["status"],
        rounds=summary["rounds"],
        events=events,
        evidence=summary["evidence"],
        replayable=summary["replayable"],
        replay_note=summary["replay_note"],
        explanation_hashes=tuple(run.explanation_hashes),
    )


def run_procedure(
    procedure,
    operations,
    workspace,
    workspace_root,
    *,
    kg=None,
    projection: Mapping[str, Any] | None = None,
    run_id: str,
    model_receipt: Mapping[str, Any] | None = None,
) -> RecordedRun:
    """Run ``procedure`` and store one event per step. Prints the denominators."""
    receipt = _receipt(model_receipt)
    workspace.start_run(run_id, kind="experiment", scope="procedure")
    log = _Log(
        procedure=procedure,
        workspace=workspace,
        workspace_root=workspace_root,
        run_id=run_id,
        kg=kg,
        projection=projection,
        receipt=receipt,
        operations=operations,
    )
    try:
        result = interpret(procedure, operations, observe=log.observe)
    except Exception:
        log.close_failed()
        raise
    return log.finish(result)


class _Log:
    def __init__(
        self, procedure, workspace, workspace_root, run_id, kg, projection, receipt,
        operations=None,
    ):
        self.procedure = procedure
        self.workspace = workspace
        self.workspace_root = workspace_root
        self.run_id = run_id
        self.kg = kg
        self.projection = projection
        self.receipt = receipt
        self.operations = operations
        self.events: list[dict] = []
        self.hashes: list[str] = []
        self.evidence: dict | None = None
        self._closed = False

    def observe(self, event, inputs, outputs) -> None:
        payload = _step_payload(
            event, inputs, outputs, self.procedure.version, self.run_id,
            self.projection, self.receipt,
        )
        if event.kind == "ran" and event.operation == "invoke_model":
            _overlay_model_step(payload, self.operations)
        self._keep(payload)
        if event.kind == "ran" and event.operation == "fetch_context":
            snapshot = _snapshot(self.kg, self.projection, outputs.get("context") if outputs else None)
            if snapshot is not None:
                self.evidence = snapshot
        if (
            event.kind == "ran"
            and event.operation == "validate_output"
            and isinstance(outputs, Mapping)
            and outputs.get("passed") is False
        ):
            for issue in _issues(outputs.get("issues")):
                self._keep(_check_payload(event, issue, self.procedure.version, self.run_id))

    def finish(self, result) -> RecordedRun:
        events = tuple(self.events)
        replayable, note = _replay(events, self.evidence)
        summary = _run_payload(
            self.run_id, self.procedure.version, result, events, self.evidence,
            replayable, note,
        )
        self._keep(summary)
        self._complete("completed" if result.status == "ok" else "failed", result.status, replayable)
        text = _summary_text(events, self.evidence, replayable, note)
        print(text)
        return RecordedRun(
            run_id=self.run_id,
            status=result.status,
            rounds=result.rounds,
            events=events,
            evidence=self.evidence,
            replayable=replayable,
            replay_note=note,
            explanation_hashes=tuple(self.hashes),
        )

    def close_failed(self) -> None:
        if self._closed:
            return
        try:
            self._complete("failed", "error", False)
        except Exception:
            return

    def _keep(self, payload: dict) -> None:
        if payload["event_type"] != "procedure_run":
            self.events.append(payload)
        self.hashes.append(
            keep_event(self.workspace, self.workspace_root, self.run_id, payload)
        )

    def _complete(self, status: str, procedure_status: str, replayable: bool) -> None:
        if self._closed:
            return
        self.workspace.complete_run(
            self.run_id,
            status=status,
            gaps_found=0,
            explanation_hashes=list(self.hashes),
            summary={
                "procedure_status": procedure_status,
                "gaps": "n/a",
                "replayable": "true" if replayable else "false",
                "evidence": format_evidence(self.evidence),
            },
        )
        self._closed = True


def _step_payload(event, inputs, outputs, version: str, run_id: str, projection, receipt) -> dict:
    if event.kind == "skipped":
        event_type = "procedure_step_skipped"
        input_hash = "n/a"
        output_hash = "n/a"
    elif event.operation == "refine":
        event_type = "procedure_refine_round"
        input_hash = sha256_canonical(inputs)
        output_hash = sha256_canonical(outputs)
    else:
        event_type = "procedure_step"
        input_hash = sha256_canonical(inputs)
        output_hash = sha256_canonical(outputs)
    payload = {
        "event_type": event_type,
        "step_id": event.step_id,
        "operation": event.operation,
        "procedure_version": version,
        "iteration": event.iteration,
        "input_hash": input_hash,
        "output_hash": output_hash,
        "links": _node_links(inputs, outputs, projection),
        "run_id": run_id,
    }
    if event.kind == "skipped":
        payload["reason"] = event.reason
        payload["guard_ref"] = event.guard_ref
        payload["guard_op"] = event.guard_op
    if event_type == "procedure_step" and event.operation == "invoke_model":
        payload.update(_model_fields(inputs, receipt))
    return payload


def _check_payload(event, issue: Mapping[str, str], version: str, run_id: str) -> dict:
    return {
        "event_type": "procedure_check_failed",
        "step_id": event.step_id,
        "operation": event.operation,
        "procedure_version": version,
        "iteration": event.iteration,
        "check": issue["check"],
        "case": issue["case"],
        "value": issue["value"],
        "input_hash": "n/a",
        "output_hash": "n/a",
        "links": [],
        "run_id": run_id,
    }


def _run_payload(run_id, version, result, events, evidence, replayable, note) -> dict:
    counts = _counts(events)
    return {
        "event_type": "procedure_run",
        "run_id": run_id,
        "procedure_version": version,
        "status": result.status,
        "rounds": result.rounds,
        "replayable": replayable,
        "replay_note": note,
        "evidence": evidence,
        "n_steps": counts["n_steps"],
        "n_skipped": counts["n_skipped"],
        "n_refine_rounds": counts["n_refine_rounds"],
        "n_failed_checks": counts["n_failed_checks"],
        "n_node_links": counts["n_node_links"],
    }


def _counts(events) -> dict[str, int]:
    return {
        "n_steps": sum(1 for event in events if event["event_type"] in _STEP_TYPES),
        "n_skipped": sum(1 for event in events if event["event_type"] == "procedure_step_skipped"),
        "n_refine_rounds": sum(
            1 for event in events if event["event_type"] == "procedure_refine_round"
        ),
        "n_failed_checks": sum(
            1 for event in events if event["event_type"] == "procedure_check_failed"
        ),
        "n_node_links": sum(len(event.get("links") or []) for event in events),
    }


def _summary_text(events, evidence, replayable: bool, note: str) -> str:
    counts = _counts(events)
    flag = "true" if replayable else "false"
    lines = [
        (
            f"procedure_events n_steps={counts['n_steps']} n_skipped={counts['n_skipped']} "
            f"n_refine_rounds={counts['n_refine_rounds']} "
            f"n_failed_checks={counts['n_failed_checks']} "
            f"n_node_links={counts['n_node_links']}"
        ),
        format_evidence(evidence),
        _format_model(events),
        f"replayable={flag} note={note}",
    ]
    return "\n".join(lines)


def _format_model(events) -> str:
    calls = [
        event for event in events
        if event.get("operation") == "invoke_model" and event.get("event_type") == "procedure_step"
    ]
    if not calls:
        return "invoke_model=n/a n_calls=0 model=n/a duration_ms=n/a token_counts=n/a"
    last = calls[-1]
    return (
        f"invoke_model n_calls={len(calls)} model={last.get('model', 'n/a')} "
        f"duration_ms={last.get('duration_ms', 'n/a')} {_format_tokens(last.get('token_counts', 'n/a'))}"
    )


def _format_tokens(tokens) -> str:
    if not isinstance(tokens, Mapping):
        return "token_counts=n/a"
    parts = " ".join(f"{key}={tokens[key]}" for key in sorted(tokens))
    return f"token_counts {parts}"


def _replay(events, evidence) -> tuple[bool, str]:
    calls = [
        event for event in events
        if event.get("operation") == "invoke_model" and event.get("event_type") == "procedure_step"
    ]
    if any(_real_model(event.get("model", "n/a")) for event in calls):
        return False, "a real model was called; the output can differ on a second run"
    has_snapshot = evidence is not None
    has_model = bool(calls)
    if has_snapshot and has_model:
        return True, (
            "replayable only as far as the stored evidence snapshot and a deterministic model stub"
        )
    if has_snapshot:
        return True, (
            "replayable only as far as the stored evidence snapshot; no model was called"
        )
    if has_model:
        return True, (
            "replayable only as far as a deterministic model stub; "
            "no evidence snapshot was stored"
        )
    return True, (
        "replayable only as far as the deterministic record; "
        "no model was called and no evidence snapshot was stored"
    )


def _real_model(model: str) -> bool:
    return model not in ("n/a", "stub")


def _receipt(receipt: Mapping[str, Any] | None) -> dict:
    if receipt is None:
        return {"model": "n/a", "prompt_hash": None, "token_counts": "n/a", "duration_ms": "n/a"}
    if not isinstance(receipt, Mapping):
        raise ProcedureRunError("model_receipt must be a mapping or omitted")
    model = receipt.get("model", "n/a")
    if model is None or model == "":
        model = "n/a"
    if not isinstance(model, str):
        raise ProcedureRunError("model_receipt model must be a string")
    prompt_hash = receipt.get("prompt_hash")
    if prompt_hash is not None and not isinstance(prompt_hash, str):
        raise ProcedureRunError("model_receipt prompt_hash must be a string")
    tokens = receipt.get("token_counts", "n/a")
    if tokens is None:
        tokens = "n/a"
    if tokens != "n/a":
        if not isinstance(tokens, Mapping) or not tokens:
            raise ProcedureRunError("model_receipt token_counts must be a mapping of counts")
        clean = {}
        for key, value in tokens.items():
            if not isinstance(key, str) or isinstance(value, bool) or not isinstance(value, int):
                raise ProcedureRunError(
                    "model_receipt token_counts must map names to integers"
                )
            clean[key] = value
        tokens = clean
    duration = receipt.get("duration_ms", "n/a")
    if duration is None:
        duration = "n/a"
    if duration != "n/a" and (isinstance(duration, bool) or not isinstance(duration, int)):
        raise ProcedureRunError("model_receipt duration_ms must be an integer or omitted")
    return {
        "model": model,
        "prompt_hash": prompt_hash,
        "token_counts": tokens,
        "duration_ms": duration,
    }


def _overlay_model_step(payload: dict, operations) -> None:
    """Copy the call's measured fields when the operation recorded them.

    A caller that does not set ``model_step`` keeps the receipt copied at
    the start of the run.
    """
    step = getattr(operations, "model_step", None)
    if not isinstance(step, Mapping):
        return
    for key in ("duration_ms", "token_counts", "level_ignored"):
        if key in step:
            payload[key] = step[key]


def _model_fields(inputs, receipt: Mapping[str, Any]) -> dict:
    supplied = receipt.get("prompt_hash")
    if isinstance(supplied, str) and supplied != "":
        prompt_hash = supplied
    else:
        instructions = inputs.get("instructions") if isinstance(inputs, Mapping) else None
        if isinstance(instructions, str):
            prompt_hash = hashlib.sha256(instructions.encode("utf-8")).hexdigest()
        elif instructions is None:
            prompt_hash = "n/a"
        else:
            prompt_hash = sha256_canonical(instructions)
    return {
        "model": receipt["model"],
        "prompt_hash": prompt_hash,
        "token_counts": receipt["token_counts"],
        "duration_ms": receipt["duration_ms"],
    }


def _issues(issues: Any) -> list[dict]:
    if not isinstance(issues, list) or not issues:
        return [{"case": "n/a", "check": "n/a", "value": "n/a"}]
    return [_one_issue(item) for item in issues]


def _one_issue(item: Any) -> dict:
    if isinstance(item, str):
        return {"case": "n/a", "check": item if item != "" else "n/a", "value": "n/a"}
    if isinstance(item, Mapping):
        return {
            "case": _text_field(item.get("case")),
            "check": _text_field(item.get("check")),
            "value": _text_field(item.get("value")),
        }
    return {"case": "n/a", "check": "n/a", "value": "n/a"}


def _text_field(value: Any) -> str:
    if isinstance(value, str) and value != "":
        return value
    return "n/a"


def _node_links(inputs, outputs, projection) -> list[dict]:
    if not projection:
        return []
    known = {
        node["key"] for node in projection["nodes"]
        if isinstance(node.get("key"), str)
    }
    found: list[str] = []
    _collect(inputs, known, found)
    _collect(outputs, known, found)
    unique = sorted(set(found))
    return [{"relation": "read", "kind": "kg_node", "id": key} for key in unique]


def _collect(value, known: set[str], found: list[str]) -> None:
    if isinstance(value, str):
        if value in known:
            found.append(value)
        return
    if isinstance(value, Mapping):
        for item in value.values():
            _collect(item, known, found)
        return
    if isinstance(value, list):
        for item in value:
            _collect(item, known, found)


def _snapshot(kg, projection, context) -> dict | None:
    groups = _groups(context)
    if groups is None:
        return None
    by_key = {}
    if projection:
        by_key = {node["key"]: node for node in projection["nodes"]}
    stored_groups = []
    flat = []
    seen: set[str] = set()
    hashed = 0
    missing = 0
    for group in groups:
        entries = []
        for key in group["nodes"]:
            content_hash = _content_hash(kg, by_key.get(key))
            entry = {"key": key, "content_hash": content_hash}
            entries.append(entry)
            if key in seen:
                continue
            seen.add(key)
            flat.append(entry)
            if content_hash == "n/a":
                missing += 1
            else:
                hashed += 1
        stored_groups.append({"key": group["key"], "nodes": entries})
    node_count = len(flat)
    return {
        "requirements": stored_groups,
        "nodes": flat,
        "n_requirements": len(stored_groups),
        "n_nodes": node_count,
        "n_hashed": "n/a" if node_count == 0 else hashed,
        "n_missing": "n/a" if node_count == 0 else missing,
    }


def _groups(context) -> list[dict] | None:
    if not isinstance(context, Mapping):
        return None
    groups = context.get("requirements")
    if not isinstance(groups, list):
        return None
    parsed = []
    for group in groups:
        if not isinstance(group, Mapping):
            return None
        key = group.get("key")
        nodes = group.get("nodes")
        if not isinstance(key, str) or not isinstance(nodes, list):
            return None
        if not all(isinstance(node, str) for node in nodes):
            return None
        parsed.append({"key": key, "nodes": list(nodes)})
    return parsed


def _content_hash(kg, node) -> str:
    if kg is None or node is None:
        return "n/a"
    engine_id = node.get("engine_id")
    if isinstance(engine_id, bool) or not isinstance(engine_id, int):
        return "n/a"
    digest = kg.get_node_content_hash(engine_id)
    if not isinstance(digest, str) or digest == "":
        return "n/a"
    return digest


def _details(payload: Mapping[str, Any]) -> dict[str, str]:
    details = {
        "event_type": str(payload.get("event_type", "")),
        "procedure_version": str(payload.get("procedure_version", "")),
        "input_hash": str(payload.get("input_hash", "n/a")),
        "output_hash": str(payload.get("output_hash", "n/a")),
    }
    if "step_id" in payload:
        details["step_id"] = str(payload["step_id"])
    if "operation" in payload:
        details["operation"] = str(payload["operation"])
    if "iteration" in payload:
        details["iteration"] = str(payload["iteration"])
    if "model" in payload:
        details["model"] = str(payload["model"])
    if "prompt_hash" in payload:
        details["prompt_hash"] = str(payload["prompt_hash"])
    if "duration_ms" in payload:
        details["duration_ms"] = str(payload["duration_ms"])
    if "token_counts" in payload:
        tokens = payload["token_counts"]
        details["token_counts"] = (
            tokens if isinstance(tokens, str)
            else json.dumps(tokens, sort_keys=True, ensure_ascii=False)
        )
    if "check" in payload:
        details["check"] = str(payload["check"])
        details["case"] = str(payload.get("case", "n/a"))
        details["value"] = str(payload.get("value", "n/a"))
    if payload.get("event_type") == "procedure_run":
        details["status"] = str(payload.get("status", ""))
        details["replayable"] = "true" if payload.get("replayable") else "false"
        details["replay_note"] = str(payload.get("replay_note", ""))
    if payload.get("event_type") == "judge_verdict":
        details["case"] = payload["case_id"]
        details["verdict"] = payload["verdict"]
    return details


def keep_event(workspace, workspace_root, run_id: str, payload: Mapping[str, Any]) -> str:
    """Store one event and return its explanation hash.

    An explanation record, then one audit line for ``run_id``. This is the
    body of ``_Log._keep``. It does not complete the run.
    """
    text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    version = workspace.record_explanation(
        text,
        metadata={"event_type": payload["event_type"], "run_id": run_id},
    )
    _append_audit(
        workspace_root,
        payload["event_type"],
        run_id,
        _details(payload),
        list(payload.get("links") or []),
        version.hash,
    )
    return version.hash


def _append_audit(workspace_root, event_type: str, run_id: str, details: dict, node_links, explanation_hash: str) -> None:
    links = [dict(link) for link in node_links]
    links.append({"relation": "recorded", "kind": "run", "id": run_id})
    links.append({"relation": "recorded", "kind": "explanation", "id": explanation_hash})
    payload = {
        "ts": int(time.time() * 1000),
        "event_type": event_type,
        "actor": None,
        "subject": run_id,
        "details": details,
        "rationale": None,
        "links": links,
    }
    path = Path(workspace_root) / "audit" / "events.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, sort_keys=True) + "\n")

"""Instance-owned application operations shared by MCP, GUI and batch callers."""

import pygrap
from pathlib import Path
import logging
import os
import shutil
import uuid
import json
import time

from . import procedure_graph as pg_loader
from .validation import validate_pg
from .diff import diff_pg
from .procedure_graph import _default_resolver
from . import ontology as onto_loader
from . import reasoner
from .publish_validation import validate_for_publish
from .experiments import experiment_summary, quality_metrics
from . import config as config_store
from . import auth
from . import governance
from . import ingest
from .session import SessionJournal
from .pagination import paginate
# Defaults can be replaced per application instance.
from .kee.llm import get_provider
from .kee.embeddings import get_embedder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grap-mcp")

from .tiers import active_tier, tier_includes

_TIERS = {
    "grap_guide": "core", "suggest_queries": "core", "list_capabilities": "core",
    "get_status": "core", "create_knowledge_graph": "core", "load_knowledge_graph": "core",
    "ingest_documents": "core", "graph_neighbors": "core", "get_context_bundle": "core",
    "resolve_entity": "core",
    "run_experiment": "full", "get_quality_metrics": "full", "adopt_experiment": "full",
    "get_config": "full", "set_weight": "full", "set_retention": "full", "set_policy": "full",
    "run_maintenance": "full",
    "enforce_retention": "full", "get_audit_log": "full", "reconstruct_run": "full",
    "get_telemetry": "full", "build_legal_graph": "full", "legal_graph_from_relations": "full",
    "extract_legal_claims": "full", "get_legal_claims": "full", "get_legal_sources": "full",
    "export_legal_claim_graph": "full",
}

class ApplicationState:
    """Volatile state owned by one application instance.
    Persisted aspects (role, config) come from workspace; volatile (kg, pg, explanations)
    are in-memory for the session. Supports future multi-session or restart recovery.
    """
    def __init__(self):
        self.kg = None
        self.pg = None
        self.ws = None
        self.ws_root = None
        self.current_logic_id = None
        self.pg_version = None
        self.kg_path = None
        self.role = None
        self.ontology = None
        self.ontology_version = None
        # Nodes projected into the EVIDENCE kg that must not count as evidence.
        # Retained for legacy callers that explicitly project metadata.
        self.projected_nodes = set()
        # The ontology's own graph: scaffolding lives here so it never enters
        # the evidence index. See docs/canary-noise-floor-investigation.md.
        self.ontology_kg = None
        self.ontology_kg_path = None
        self.ontology_nodes = set()
        self.explanations = {}
        self.extraction_workdir = None

    # Dict-style access kept for tests (monkeypatch.setitem) and any remaining
    # _state["key"] call sites during the dict -> class transition. These must
    # be real methods on the class: Python resolves dunders on the type, so
    # instance-attribute lambdas would not make subscription work.
    def __getitem__(self, key):
        return getattr(self, key, None)

    def __setitem__(self, key, value):
        setattr(self, key, value)

    def __delitem__(self, key):
        try:
            delattr(self, key)
        except AttributeError:
            pass

    def get(self, key, default=None):
        return getattr(self, key, default)

    def reset(self):
        """Clear volatile session state (keep ws if open)."""
        self.kg = None
        self.pg = None
        self.current_logic_id = None
        self.pg_version = None
        self.kg_path = None
        self.ontology = None
        self.ontology_version = None
        self.projected_nodes = set()
        self.ontology_kg = None
        self.ontology_kg_path = None
        self.ontology_nodes = set()
        self.explanations = {}
        self.extraction_workdir = None

    def set_kg(self, kg, path=None):
        """Select a graph and clear state belonging to the previous graph."""
        self.kg = kg
        self.kg_path = path
        self.extraction_workdir = None
        self.projected_nodes = set()

    def set_ontology(self, ontology, version=None):
        self.ontology = ontology
        if version:
            self.ontology_version = version

    def clear_kg(self):
        self.kg = None
        self.kg_path = None
        self.projected_nodes = set()
        self.extraction_workdir = None

    def get_role(self) -> str:
        """Effective role. GRAP_ROLE overrides for this process only (the
        bootstrap path — e.g. GRAP_ROLE=admin to reach set_role); otherwise
        the persisted workspace config wins. Reading never writes config —
        only set_role persists."""
        if self.role is None:
            env_role = os.environ.get("GRAP_ROLE")
            if env_role in auth.ROLES:
                self.role = env_role
            else:
                root = self.ws_root or os.environ.get("GRAP_WORKSPACE", "./grap-workspace")
                cfg = config_store.load_config(root)
                self.role = cfg.get("role", "operator")
        return self.role

    def set_role(self, role: str, rationale: str = None):
        if role not in auth.ROLES:
            raise ValueError(f"unknown role {role}")
        root = self.ws_root or os.environ.get("GRAP_WORKSPACE", "./grap-workspace")
        cfg = config_store.load_config(root)
        old = cfg.get("role", "operator")
        cfg["role"] = role
        cfg["last_changed"] = int(time.time() * 1000)
        config_store.save_config(root, cfg)
        self.role = role
        if self.ws:
            try:
                self.ws.record_event("role_changed", f"{old}->{role}",
                                     details={"from": old, "to": role}, rationale=rationale)
            except Exception:
                pass


def operation(func):
    """Mark an application operation for transport discovery without registering it."""
    func.is_grap_operation = True
    return func


class GrapApplication:
    """Own one workspace session; transports bind to this instance's operations.

    Construct another instance to switch workspaces. No environment mutation or
    transport-module state is involved. Instances are not concurrent writers to
    the same graph directory.
    """
    def __init__(self, workspace=None, *, role=None, provider_factory=None,
                 embedder_factory=None, tier=None):
        self._state = ApplicationState()
        self._state.ws_root = str(Path(workspace or os.environ.get(
            "GRAP_WORKSPACE", "./grap-workspace")).resolve())
        self._state.role = role
        self.get_provider = provider_factory or get_provider
        self.get_embedder = embedder_factory or get_embedder
        self.session = SessionJournal()
        self.active_tier = tier or active_tier()

    @classmethod
    def operation_names(cls):
        return tuple(name for name, value in vars(cls).items()
                     if getattr(value, "is_grap_operation", False))

    def _get_workspace(self):
        """Lazily open the lifecycle workspace (env GRAP_WORKSPACE or ./grap-workspace).

        Must not touch volatile state destructively: tools routinely set the KG/PG
        first and only then open the workspace to write an audit event, so a reset
        here would silently discard what the caller just loaded. On first open the
        active ontology is *rehydrated* (additively) from the audit trail so a
        restarted process knows what is in production — which also restores the
        canary's rollback target.
        """
        if self._state.ws is None:
            root = self._state.ws_root
            self._state.ws = pygrap.Workspace.open(root)
            self._state.ws_root = root
            self._rehydrate_active_ontology()
        return self._state.ws


    def _project_ontology(self, ontology):
        """Project `ontology` into its OWN graph and return the projection record.

        The ontology used to be projected into the evidence KG. Measured at 950
        documents, that took the index from 4,692 nodes to 23,170 — **80% of what
        BM25 ranked over was ontology scaffolding**. Excluding those nodes from
        *results* does not stop them being *ranked*: a top-5 computed over the
        polluted index and filtered afterwards returned 1-2 real documents, which is
        the whole of the canary's ~830 "regression" floor and a permanent tax on
        retrieval quality for every production gap run.
        (docs/canary-noise-floor-investigation.md)

        So the scaffolding gets its own graph. Two consequences worth stating:

        - The evidence index contains evidence only, so idf means what it says.
        - The graph is rebuilt from scratch on every projection, so it always
          describes the *current* ontology. The old union-into-the-evidence-KG
          accumulated every projection ever made and could never be pruned, because
          pruning it would have meant deleting nodes out of the evidence index.
        """
        if self._state.kg_path:
            path = str(Path(self._state.kg_path).parent / "ontology-kg")
        else:
            path = str(Path(self._state.ws_root or ".") / "ontology-kg")
        # Release the handle before removing the directory: on Windows an open
        # pygrap handle makes rmtree silently skip files (same trap as
        # WorkingGraph._project).
        self._state.ontology_kg = None
        shutil.rmtree(path, ignore_errors=True)
        okg = pygrap.IndexedKnowledgeGraph.with_merge_policy(path, 2)
        projection = onto_loader.project_ontology_to_grap(ontology, okg)
        self._state.ontology_kg = okg
        self._state.ontology_kg_path = path
        self._state.ontology_nodes = set(projection.get("projected_node_ids", []))
        return projection


    def _reference_resolver(self):
        """Resolve `kg:aspice.*` PG references against the ontology graph, falling
        back to the evidence KG for anything else (PG steps still live there)."""
        onto = self._state.ontology_kg
        evidence = self._state.kg
        r_onto = _default_resolver(onto) if onto is not None else None
        r_evidence = _default_resolver(evidence) if evidence is not None else None

        def resolve(ref_id):
            if r_onto is not None and str(ref_id).startswith("kg:aspice."):
                hit = r_onto(ref_id)
                if hit is not None:
                    return hit
            return r_evidence(ref_id) if r_evidence is not None else None
        return resolve


    def _rehydrate_active_ontology(self):
        """Best-effort restore of the active ontology after a process restart,
        from the latest ontology_adopted / ontology_rolled_back audit event
        (adoption re-publishes the same content, so the event subject's record is
        the active yaml). Projected-node exclusion is NOT rebuilt — evidence
        filtering falls back to _is_projected's label/text-prefix guards, which is
        why restarts stay safe even without this."""
        if self._state.ontology is not None:
            return
        try:
            ws = self._state.ws
            candidates = []
            for event_type in ("ontology_adopted", "ontology_rolled_back"):
                evs = ws.query_events(event_type) or []
                if evs:
                    candidates.append(evs[-1])
            if not candidates:
                return
            ev = max(candidates, key=lambda e: e.ts)
            raw = ws.get_record(ev.subject)
            if not raw:
                return
            import yaml
            parsed = yaml.safe_load(raw) or {}
            if not parsed.get("process_outcomes"):
                return
            self._state.set_ontology(self._load_ontology_from_raw(raw), ev.subject)
            logger.info("rehydrated active ontology %s from audit trail", ev.subject[:12])
        except Exception:
            logger.warning("active-ontology rehydration failed", exc_info=True)


    def _resolve_ws_root(self):
        """Return the workspace root path, opening the workspace if needed."""
        if self._state.ws is None:
            self._get_workspace()
        return self._state.ws_root


    def _get_role(self) -> str:
        """Effective current role: delegated to state (persisted/governed)."""
        return self._state.get_role()


    def _get_telemetry_snapshot(self):
        """Clean helper for telemetry snapshot in status."""
        try:
            import grap_mcp.telemetry as t
            if t._enabled():
                snap = t.snapshot()
                snap["enabled"] = True
                return snap
            else:
                return {"enabled": False, "note": "enable with GRAP_TELEMETRY=1"}
        except Exception:
            return {"enabled": False, "error": "telemetry module unavailable"}


    def _require(self, required: str):
        """Return an error dict if the current role is below `required`, else None."""
        current = self._get_role()
        if not auth.role_allows(current, required):
            return {"status": "error",
                    "message": f"requires '{required}' role (current: '{current}')"}
        return None


    def _require_mutable_evidence(self):
        if self._state.extraction_workdir:
            return {"status": "error", "message":
                    "This is a derived extraction view. Use extend_extraction_graph "
                    "to change durable extraction records, or load a separate evidence KG."}
        return None

    def _cached_validation(self, ws, version_hash):
        """Return the cached validation report for a PG version, or None.

        Looks up the latest `validation_run` event linked to the version, fetches the
        linked validation record, and only returns it if it was validated against the
        currently loaded ontology version.
        """
        try:
            events = [e for e in ws.events_linked_to("pg_version", version_hash)
                      if e.event_type == "validation_run"]
            if not events:
                return None
            latest = events[-1]
            record_hash = next((i for (_r, k, i) in latest.links if k == "validation"), None)
            if not record_hash:
                return None
            raw = ws.get_record(record_hash)
            if not raw:
                return None
            report = json.loads(raw)
            if report.get("ontology_version") != getattr(self._state, "ontology_version", None):
                return None
            return report
        except Exception:
            logger.warning("cached validation lookup failed", exc_info=True)
            return None


    # =============================================================================
    # P0 Capabilities (Tier 1 for the narrow vertical POC)
    # =============================================================================

    @operation
    def load_knowledge_graph(self, path: str) -> dict:
        """
        Load a Knowledge Graph (with ontology) from disk.
        For the POC this can be a simple on-disk grap store.
        """
        logger.info(f"Loading Knowledge Graph from: {path}")
        denied = self._require("operator")
        if denied:
            return denied
        try:
            kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(path, 2)
            self._state.set_kg(kg, path)
            try:
                self._get_workspace().record_event("kg_loaded", path,
                                              details={"node_count": str(kg.node_count())})
            except Exception:
                logger.warning("audit record_event failed for kg_loaded", exc_info=True)
            return {
                "status": "ok",
                "path": path,
                "node_count": kg.node_count(),
                "message": "Knowledge Graph loaded successfully (POC mode)"
            }
        except Exception as e:
            logger.exception("Failed to load KG")
            return {"status": "error", "message": str(e)}


    @operation
    def create_knowledge_graph(self, path: str) -> dict:
        """Create a fresh on-disk Knowledge Graph and make it the current KG."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            # "Create" means start empty: with_merge_policy loads any graph already
            # on disk at `path`, so clear the directory first to avoid silently
            # reusing/accumulating stale nodes across runs.
            target = Path(path)
            if target.exists():
                shutil.rmtree(target)
            kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(path, 2)
            kg.save_to_dir(path)
            self._state.set_kg(kg, path)
            self._state.projected_nodes = set()
            try:
                self._get_workspace().record_event("kg_created", path,
                                              details={"node_count": str(kg.node_count())})
            except Exception:
                logger.warning("audit record_event failed for kg_created", exc_info=True)
            return {"status": "ok", "path": path, "node_count": kg.node_count()}
        except Exception as e:
            logger.exception("create_knowledge_graph failed")
            return {"status": "error", "message": str(e)}


    @operation
    def ingest_documents(self, path: str) -> dict:
        """Chunk + add .md/.txt documents under `path` into the loaded KG (real evidence)."""
        denied = self._require("operator") or self._require_mutable_evidence()
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "Load or create a Knowledge Graph first."}
        try:
            result = ingest.ingest_paths(kg, path)
            kg_path = self._state.kg_path
            if kg_path:
                kg.save_to_dir(kg_path)
            try:
                self._get_workspace().record_event("documents_ingested", path,
                                              details={"files": str(result["files"]),
                                                       "nodes_added": str(result["nodes_added"])})
            except Exception:
                logger.warning("audit record_event failed for documents_ingested", exc_info=True)
            return {"status": "ok", **result}
        except Exception as e:
            logger.exception("ingest_documents failed")
            return {"status": "error", "message": str(e)}


    @operation
    def reingest_documents(self, path: str) -> dict:
        """Incrementally (re)ingest .md/.txt under `path`: skip unchanged chunks,
        add new/changed ones, tombstone removed ones. Non-duplicating."""
        denied = self._require("operator") or self._require_mutable_evidence()
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "Load or create a Knowledge Graph first."}
        try:
            from grap_mcp import incremental
            from pathlib import Path as _P
            kg_path = self._state.kg_path
            manifest_path = str(_P(kg_path) / "ingest-manifest.json") if kg_path else None
            result = incremental.ingest_incremental(kg, path, manifest_path=manifest_path)
            if kg_path:
                kg.save_to_dir(kg_path)
            try:
                self._get_workspace().record_event("documents_reingested", path,
                                              details={"files": str(result.get("files", 0)),
                                                       "nodes_added": str(result["nodes_added"]),
                                                       "nodes_removed": str(result["nodes_removed"]),
                                                       "skipped": str(result["skipped"])})
            except Exception:
                logger.warning("audit record_event failed for documents_reingested", exc_info=True)
            return {"status": "ok", **result}
        except Exception as e:
            logger.exception("reingest_documents failed")
            return {"status": "error", "message": str(e)}


    def _baseline_outcome_ids(self, ws, prev_version: str):
        """Outcome ids of the ontology that was active BEFORE this adoption.

        Returns None when unavailable, which makes the canary fall back to its old
        (undiscriminating) behaviour rather than silently pass everything."""
        if not prev_version:
            return None
        try:
            raw = ws.get_record(prev_version)
            if not raw:
                return None
            import yaml
            parsed = yaml.safe_load(raw) or {}
            ids = {o.get("id") for o in parsed.get("process_outcomes", []) if o.get("id")}
            return ids or None
        except Exception:
            return None


    def _canary_check(self, ws, adopted_hash: str, baseline_run_id: str = None,
                      rename_pairs=(), prev_version: str = None) -> dict:
        """Gap-detection canary for a just-adopted ontology.

        Baseline preference: a fresh pre-adoption run from this cycle
        (`baseline_run_id`, review fix 1 — avoids blaming the candidate for
        evidence changes since the last cycle), else the most recent production
        run, else a bootstrap skip. The canary itself runs as a quarantined
        experiment. Gap targets that merely moved to a renamed outcome id
        (review fix 2) are not counted as new. Returns the new-gap count —
        conservatively huge on any failure."""
        baseline = baseline_run_id
        if not baseline:
            prod = ws.list_runs("production") or []
            if not prod:
                return {"skipped": "no production baseline"}
            baseline = prod[-1].run_id
        exp = self._run_gap_detection("all", "experiment", {"canary_for": adopted_hash})
        if not isinstance(exp, dict) or exp.get("status") != "ok":
            return {"baseline_run": baseline, "canary_run": None,
                    "new_gaps": 10 ** 9, "detail": exp}
        d = self.diff_runs(baseline, exp["run_id"])
        if d.get("status") != "ok":
            return {"baseline_run": baseline, "canary_run": exp["run_id"],
                    "new_gaps": 10 ** 9, "detail": d}
        renames = {new: old for old, new in (rename_pairs or [])}
        resolved = set(d["resolved_gaps"])

        def _renamed_carryover(target: str) -> bool:
            oid, _, char = str(target).partition(":")
            old = renames.get(oid)
            return old is not None and f"{old}:{char}" in resolved

        new_gaps = [t for t in d["new_gaps"] if not _renamed_carryover(t)]

        # Only an outcome that EXISTED BEFORE can regress. A gap on a newly added
        # outcome is expected growth — the new outcome's characteristics simply have
        # no threshold-matching evidence yet — and counting it made the canary
        # measure ontology churn instead of lost coverage: over 27 governed batches
        # the raw count sat at mean 848 / sd 17 (CV 2%) while the batches behind it
        # added anywhere from 0 to 45 outcomes, i.e. no threshold could discriminate
        # (40 rejected every batch, 2500 accepted every batch). Partitioning against
        # the previous ontology's outcome ids isolates the real signal.
        prev_ids = self._baseline_outcome_ids(ws, prev_version)
        if prev_ids is None:
            regressions, growth = new_gaps, []
        else:
            regressions = [t for t in new_gaps if str(t).partition(":")[0] in prev_ids]
            growth = [t for t in new_gaps if str(t).partition(":")[0] not in prev_ids]

        return {"baseline_run": baseline, "canary_run": exp["run_id"],
                "new_gaps": len(regressions),          # what the budget gates on
                "regressions": len(regressions),
                "growth_gaps": len(growth),            # expected, reported for observability
                "raw_new_gaps": len(new_gaps),
                "detail": d.get("counts")}


    def _run_canary(self, ws, prev_version, adopted_hash: str, policy: dict,
                    baseline_run_id: str = None, rename_pairs=()) -> dict:
        """Run the canary for a just-adopted ontology; over budget -> roll back to
        prev_version. Records a single `canary_result` event (latest-state alarm
        source). Returns fields merged into the auto_adoption report — the line
        never blocks on a canary failure."""
        c = self._canary_check(ws, adopted_hash, baseline_run_id, rename_pairs,
                          prev_version=prev_version)
        if c.get("skipped"):
            try:
                ws.record_event("canary_result",
                                f"result=passed;new=0;to={adopted_hash};note=bootstrap")
            except Exception:
                pass
            return {"canary": "skipped", "canary_note": c["skipped"]}
        budget = int(policy.get("canary_max_new_gaps", 0))
        if c["new_gaps"] > budget:
            # A production baseline normally implies an ontology was active in
            # this session (prev_version set); a fresh process over an old
            # workspace can lack a rollback target — flag rather than crash.
            rollback_status = "no_rollback_target"
            if prev_version:
                rb = self.rollback_ontology(
                    prev_version,
                    reason=f"canary: {c['new_gaps']} new gap(s) over budget {budget}")
                rollback_status = rb.get("status", "?") if isinstance(rb, dict) else "?"
            try:
                ws.record_event("canary_result",
                                f"result=failed;new={c['new_gaps']};"
                                f"from={prev_version or ''};to={adopted_hash}")
            except Exception:
                pass
            return {"canary": "failed", "new_gaps": c["new_gaps"],
                    "canary_run": c.get("canary_run"),
                    "baseline_run": c.get("baseline_run"),
                    "rolled_back_to": prev_version,
                    "rollback_status": rollback_status}
        try:
            ws.record_event("canary_result",
                            f"result=passed;new={c['new_gaps']};"
                            f"growth={c.get('growth_gaps', 0)};to={adopted_hash}")
        except Exception:
            pass
        return {"canary": "passed", "new_gaps": c["new_gaps"],
                "growth_gaps": c.get("growth_gaps"),
                "raw_new_gaps": c.get("raw_new_gaps"),
                "canary_run": c.get("canary_run"),
                "baseline_run": c.get("baseline_run")}


    def _publish_and_gate_proposal(self, yaml_text: str, source: str, provider_name: str,
                                   embedder_name: str, outcomes_count: int, *,
                                   extraction_complete: bool = True):
        """Publish a candidate ontology as a *proposed* version, audit it, and —
        when the governed adoption policy is enabled — let the policy decide:
        pass -> auto-adopt (audited adopted_by=policy); fail -> audited rejection
        that surfaces in list_pending_interventions. Either way the line never
        blocks. Returns (version, auto_adoption).

        Idempotency: if the proposal content is byte-identical to the active
        ontology, nothing is re-adopted — pipeline re-runs on an unchanged corpus
        are no-ops."""
        ws = self._get_workspace()
        version = ws.publish_ontology(
            yaml_text, metadata={"kind": "proposed", "source": source})
        try:
            ws.record_event(
                "ontology_proposed", version.hash,
                details={"source": source, "outcomes": str(outcomes_count),
                         "provider": provider_name, "embedder": embedder_name})
        except Exception:
            logger.warning("audit record_event failed for ontology_proposed", exc_info=True)

        auto_adoption = None
        policy = governance.merged_policy(config_store.load_config(self._resolve_ws_root()))
        if policy.get("enabled"):
            if not extraction_complete:
                ws.record_event("adoption_policy_rejected", version.hash,
                                details={"failed": "incomplete_extraction"})
                return version, {"attempted": False, "pass": False, "adopted": False,
                                 "failed_checks": ["incomplete_extraction"],
                                 "reason": "Some source chunks failed extraction; review or retry before adoption"}
            active_hash = getattr(self._state, "ontology_version", None)
            if active_hash and ws.get_record(active_hash) == yaml_text:
                return version, {"attempted": False, "reason": "already_active"}
            import yaml as _yaml
            verdict = governance.evaluate_proposal_dict(
                _yaml.safe_load(yaml_text) or {}, self._active_ontology_dict(ws), policy,
                pg_referenced_ids=self._pg_referenced_outcome_ids())
            failed = [c["name"] for c in verdict["checks"] if not c["ok"]]
            # The check NAMES alone are not actionable: a bare ['churn'] was recorded
            # fourteen times across two runs and the budget that actually tripped had
            # to be queried from the workspace each time. Carry the details too.
            failed_detail = {c["name"]: c.get("detail")
                             for c in verdict["checks"] if not c["ok"]}
            if verdict["pass"] and self._state.kg is not None:
                prev_version = getattr(self._state, "ontology_version", None)
                # Fresh pre-adoption baseline (review fix 1): measured against the
                # *previous* ontology on the current evidence, so the canary diff
                # attributes gap changes to the candidate, not to evidence churn
                # since the last cycle.
                baseline_run_id = None
                if policy.get("canary_enabled") and prev_version and self._state.ontology is not None:
                    base = self._run_gap_detection("all", "experiment",
                                              {"canary_baseline_for": version.hash})
                    if isinstance(base, dict) and base.get("status") == "ok":
                        baseline_run_id = base["run_id"]
                auto_rationale = ("adoption_policy: all checks passed ("
                                  + ", ".join(c["name"] for c in verdict["checks"]) + ")")
                adopt_res = self._adopt_record(ws, self._state.kg, version.hash, yaml_text,
                                          auto_rationale, adopted_by="policy")
                auto_adoption = {"attempted": True, "pass": True,
                                 "adopted": adopt_res.get("status") == "ok",
                                 "new_version_hash": adopt_res.get("new_version_hash")}
                if auto_adoption["adopted"] and policy.get("canary_enabled"):
                    rename_pairs = (verdict.get("diff") or {}).get("outcomes_renamed") or []
                    auto_adoption.update(
                        self._run_canary(ws, prev_version, version.hash, policy,
                                    baseline_run_id, rename_pairs))
                # Phase 4: on a kept adoption (canary passed/skipped/off), draft a
                # PG patch so the human's remaining act is an approval click.
                if (auto_adoption.get("adopted") and policy.get("pg_draft_enabled")
                        and auto_adoption.get("canary") in (None, "passed", "skipped")
                        and self._state.pg is not None):
                    d = self.draft_pg_patch()
                    auto_adoption["pg_draft"] = {
                        "status": d.get("status"),
                        "draft_version_hash": d.get("draft_version_hash"),
                        "uncovered": d.get("uncovered"),
                        "orphaned": d.get("orphaned"),
                        "note": d.get("note") or d.get("message")}
            else:
                if self._state.kg is None:
                    failed.append("no_kg_loaded")
                try:
                    ws.record_event("adoption_policy_rejected", version.hash,
                                    details={"failed": ",".join(failed) or "unknown"})
                except Exception:
                    logger.warning("audit record_event failed for policy_rejected",
                                   exc_info=True)
                auto_adoption = {"attempted": True, "pass": False,
                                 "failed_checks": failed,
                                 "failed_detail": failed_detail,
                                 "churn_violations": failed_detail.get("churn"),
                                 "version_hash": version.hash}
        return version, auto_adoption


    @operation
    def extract_ontology(self, path: str, out_path: str = None, sim: float = 0.86,
                         model: str = None, max_files: int = None) -> dict:
        """Propose an ontology from documents (Knowledge Extraction Engine).

        Reads .md/.txt/.json under `path`, asks the configured LLM to extract
        ontology fragments, reconciles near-duplicate concepts by embedding
        similarity, and writes a candidate ontology. The candidate is versioned as a
        *proposed* artifact for human review — it does NOT become the active
        ontology until a human loads/promotes it (governance stays deterministic).

        `model` overrides the LLM model (e.g. "qwen3:8b"); blank uses the server
        default (GRAP_LLM_MODEL). `max_files` caps how many files are read (a safety
        guard for large corpora — one LLM call per chunk).
        """
        denied = self._require("operator")
        if denied:
            return denied
        try:
            from .kee import pipeline, validate
            provider = self.get_provider(model=model)
            embedder = self.get_embedder()
            result = pipeline.extract_ontology(path, provider, embedder, sim=sim,
                                               max_files=max_files)
            onto = result["ontology"]
            self.session.record_query(f"extract_ontology:{path}", result.get("fragments", 0))
            validation = validate.validate_ontology(onto)
            yaml_text = pipeline.to_yaml(onto)
            out = out_path or str(Path(self._resolve_ws_root()) / "candidate-ontology.yaml")
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(yaml_text, encoding="utf-8")
            version, auto_adoption = self._publish_and_gate_proposal(
                yaml_text, path, provider.name, embedder.name,
                len(onto["process_outcomes"]),
                extraction_complete=not result.get("failures"))

            return {
                "status": "ok",
                "out_path": out,
                "version_hash": version.hash,
                "provider": provider.name,
                "model": getattr(provider, "model", None),
                "embedder": embedder.name,
                "chunks": result.get("chunks", 0),
                "fragments": result.get("fragments", 0),
                "quality": result.get("quality"),
                "failures": result.get("failures", []),
                "process_outcomes": [
                    {"id": o["id"], "name": o["name"],
                     "characteristics": o["required_characteristics"]}
                    for o in onto["process_outcomes"]],
                "provenance": onto.get("provenance", []),
                "relations": onto.get("relations", []),
                "validation": validation,
                "concept_hashes": onto.get("concept_hashes", {}),
                "auto_adoption": auto_adoption,
                "note": ("LLM proposal — review before adopting. Inspect with "
                         "get_proposed_ontology(version_hash), then "
                         "adopt_proposed_ontology(version_hash, rationale); adoption "
                         "verifies concept_hashes (tamper-evidence). With the adoption "
                         "policy enabled, auto_adoption reports the policy verdict."),
            }
        except Exception as e:
            logger.exception("extract_ontology failed")
            return {"status": "error", "message": str(e)}


    @operation
    def draft_pg_patch(self, model: str = None) -> dict:
        """Draft deterministic reference additions/removals for the active ontology.

        Existing operations, guards and dataflow are preserved. Validation and
        promotion policy still apply. The legacy model argument is ignored.
        """
        denied = self._require("operator")
        if denied:
            return denied
        pg = self._state.pg
        if not pg or not getattr(pg, "source_path", None):
            return {"status": "error", "message": "Load a Procedure Graph (from a file) first."}
        ws = self._get_workspace()
        active = self._active_ontology_dict(ws)
        if not active:
            return {"status": "error", "message": "No active ontology to cover."}
        try:
            from . import pg_patch
            onto_out = [o for o in active.get("process_outcomes", []) if isinstance(o, dict)]
            onto_ids = {o.get("id") for o in onto_out}
            pg_ids = pg_patch.referenced_outcome_ids(pg)
            uncovered_ids = sorted(onto_ids - pg_ids)
            orphaned = sorted(pg_ids - onto_ids)
            if not uncovered_ids and not orphaned:
                return {"status": "ok", "note": "PG already covers the active ontology",
                        "uncovered": [], "orphaned": [], "draft_version_hash": None}
            uncovered = [o for o in onto_out if o.get("id") in set(uncovered_ids)]
            raw = pg_patch.patch_references(pg, uncovered, orphaned)

            def _fail(reason: str) -> dict:
                try:
                    ws.record_event("pg_draft_result", f"result=failed;reason={reason[:80]}")
                except Exception:
                    pass
                return {"status": "error", "message": f"draft rejected: {reason}",
                        "uncovered": uncovered_ids, "orphaned": orphaned}

            try:
                draft = pg_loader.load_from_string(raw)
            except Exception as e:
                return _fail(f"unparseable YAML ({e})")
            violations = pg_patch.coverage_violations(
                draft, pg_ids, set(uncovered_ids), set(orphaned))
            if violations:
                return _fail("; ".join(violations))
            report = validate_for_publish(draft, self._state.kg, self._state.ontology,
                                          resolver=self._reference_resolver())
            if report["errors"]:
                return _fail(f"publish validation errors: {report['errors']}")

            version = ws.publish_pg(
                raw, level="draft",
                rationale="Deterministic PG reference patch (validated; subject to promotion policy)",
                metadata={"name": draft.name, "drafted_by": "reference_patch",
                          "covers": ",".join(uncovered_ids),
                          "removes": ",".join(orphaned)})
            report["ontology_version"] = getattr(self._state, "ontology_version", None)
            ws.record_validation(version.hash, json.dumps(report), report["ok"])
            try:
                ws.record_event("pg_draft_result",
                                f"result=ok;version={version.hash};"
                                f"covers={len(uncovered_ids)};removes={len(orphaned)}")
            except Exception:
                logger.warning("audit record_event failed for pg_draft_result", exc_info=True)

            pdiff = diff_pg(pg, draft)
            # Phase 5: purely additive drafts may self-promote through the same
            # validation-gated path a human would use; anything touching existing
            # process logic stays a human decision in the queue.
            auto_promoted = False
            written_back = False
            pol = governance.merged_policy(config_store.load_config(self._resolve_ws_root()))
            if pol.get("pg_autopromote_enabled") and governance.pg_draft_is_trivial(pdiff):
                pr = self.promote_pg(version.hash,
                                rationale="auto-promoted: trivial additive draft "
                                          "(machine-checked coverage + validation)")
                auto_promoted = isinstance(pr, dict) and pr.get("status") == "ok"
            # Review fix 4 (opt-in): keep the PG source file tracking the promoted
            # head so later cycles draft from the current PG, not a stale file.
            if auto_promoted and pol.get("pg_write_back"):
                try:
                    Path(pg.source_path).write_text(raw, encoding="utf-8")
                    draft.source_path = pg.source_path
                    self._state.pg = draft
                    self._state.pg_version = version.hash
                    self._state.current_logic_id = draft.name
                    written_back = True
                except Exception:
                    logger.warning("pg_write_back failed", exc_info=True)
            return {"status": "ok", "draft_version_hash": version.hash,
                    "uncovered": uncovered_ids, "orphaned": orphaned,
                    "validation": report, "diff": pdiff,
                    "auto_promoted": auto_promoted,
                    "written_back": written_back,
                    "note": ("Draft auto-promoted (trivial additive change)."
                             if auto_promoted else
                             "Draft published (level=draft). Review the diff, then "
                             "promote_pg(draft_version_hash, rationale) to accept.")}
        except Exception as e:
            logger.exception("draft_pg_patch failed")
            return {"status": "error", "message": str(e)}


    def _provider_from_spec(self, spec):
        """Build an LLM provider from a pipeline model spec: a plain model name
        (routed through the default provider factory) or a
        {"base_url", "model", "extra_body"} dict for a specific local server."""
        if isinstance(spec, dict):
            from .kee import llm as kee_llm
            return kee_llm.OpenAICompatProvider(base_url=spec.get("base_url"),
                                                model=spec.get("model"),
                                                extra_body=spec.get("extra_body"))
        return self.get_provider(model=str(spec))


    @operation
    def extract_ontology_nversion(self, path: str, models: list = None, sim: float = 0.86,
                                  max_files: int = None) -> dict:
        """N-version extraction: run the KEE with two models **sequentially**,
        auto-accept only the agreement set (the merged proposal goes through the
        normal policy gate + canary), and publish the disagreement residue as a
        `disputed` proposal for human review (exception-queue kind
        model_disagreement, cleared when a later run agrees fully).

        `models`: exactly two specs — a model name string, or
        {"base_url", "model", "extra_body"} for a specific local server.
        Each source proposal remains inspectable by hash. Agreement measures
        consistency between models, not factual verification."""
        denied = self._require("operator")
        if denied:
            return denied
        if not models or len(models) != 2:
            return {"status": "error", "message": "provide exactly two model specs"}
        try:
            from .kee import pipeline as kee_pipeline
            embedder = self.get_embedder()
            ontos = []
            source_proposals = []
            ws = self._get_workspace()
            for spec in models:                     # sequential on purpose: local
                prov = self._provider_from_spec(spec)    # models share one GPU
                res = kee_pipeline.extract_ontology(path, prov, embedder,
                                                    sim=sim, max_files=max_files)
                ontos.append(res["ontology"])
                # Keep each model's complete proposal, including quotes and
                # relations, before intersecting outcomes and requirements.
                model_name = getattr(prov, "model", None) or (
                    spec.get("model", "?") if isinstance(spec, dict) else spec)
                source_version = ws.publish_ontology(
                    kee_pipeline.to_yaml(res["ontology"]),
                    metadata={"kind": "extraction_source", "source": path,
                              "model": str(model_name)})
                source_proposals.append({"version_hash": source_version.hash,
                                         "model": model_name,
                                         "quality": res.get("quality"),
                                         "failures": res.get("failures", [])})
            merged, disagreement = governance.agreement_merge(
                ontos[0], ontos[1], embedder, sim)
            merged["source_proposals"] = source_proposals

            import yaml as _yaml
            header = ("# Agreement-set ontology from n-version extraction.\n"
                      "# Only outcomes both models proposed; disputes are published "
                      "separately for review.\n\n")
            yaml_text = header + _yaml.safe_dump(merged, sort_keys=False,
                                                 allow_unicode=True,
                                                 default_flow_style=False)
            version, auto = self._publish_and_gate_proposal(
                yaml_text, path, "nversion", getattr(embedder, "name", "?"),
                len(merged.get("process_outcomes", [])),
                extraction_complete=not any(p["failures"] for p in source_proposals))

            ws = self._get_workspace()
            n_outcomes = (len(disagreement["outcomes_only_in_a"])
                          + len(disagreement["outcomes_only_in_b"]))
            n_chars = len(disagreement["disputed_characteristics"])
            disputed_hash = None
            if n_outcomes or n_chars:
                disputed_hash = ws.publish_ontology(
                    _yaml.safe_dump(disagreement, sort_keys=False),
                    metadata={"kind": "disputed", "source": path}).hash
            try:
                ws.record_event("model_disagreement",
                                f"outcomes={n_outcomes};disputed_chars={n_chars};"
                                f"version={disputed_hash or ''}")
            except Exception:
                logger.warning("audit record_event failed for model_disagreement",
                               exc_info=True)
            return {"status": "ok", "version_hash": version.hash,
                    "source_proposals": source_proposals,
                    "auto_adoption": auto,
                    "agreed_outcomes": disagreement["agreed_outcomes"],
                    "disagreement": disagreement,
                    "disputed_version_hash": disputed_hash}
        except Exception as e:
            logger.exception("extract_ontology_nversion failed")
            return {"status": "error", "message": str(e)}


    @operation
    def list_proposed_ontologies(self, limit: int = None, cursor: str = None) -> dict:
        """List ontology proposals recorded by extract_ontology, newest last.

        Audit-backed: each extract_ontology call writes an 'ontology_proposed'
        event whose subject is the proposal's version hash. Inspect one with
        get_proposed_ontology(version_hash); promote with
        adopt_proposed_ontology(version_hash, rationale)."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            ws = self._get_workspace()
            events = ws.query_events("ontology_proposed") or []
            entries = [{"version_hash": e.subject, "ts": e.ts} for e in events]
            page, meta = paginate(entries, limit, cursor)
            return {"status": "ok", "proposed": page, "count": len(page), **meta}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def get_proposed_ontology(self, version_hash: str) -> dict:
        """Fetch a proposed ontology by its version hash (content hash of the yaml).

        Returns the raw content, parsed structure, and any concept_hashes if available
        from the extraction. Useful for human review before adopt."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            ws = self._get_workspace()
            raw = ws.get_record(version_hash)
            if not raw:
                return {"status": "error", "message": f"Unknown version '{version_hash}'."}
            import yaml
            try:
                parsed = yaml.safe_load(raw) or {}
            except Exception:
                parsed = {}
            return {
                "status": "ok",
                "version_hash": version_hash,
                "raw": raw,
                "parsed": parsed,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}


    def _load_ontology_from_raw(self, raw: str):
        """Materialize a versioned ontology yaml to a temp file for the loader."""
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False,
                                         encoding="utf-8") as tf:
            tf.write(raw)
            tmp_path = tf.name
        try:
            return onto_loader.load_ontology_from_file(tmp_path)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


    def _pg_referenced_outcome_ids(self) -> set:
        """Outcome ids referenced by the loaded PG (kg:...process_outcome.<id>)."""
        pg = self._state.pg
        ids = set()
        if not pg or not hasattr(pg, "steps"):
            return ids
        for step in pg.steps:
            for ref in getattr(step, "references", []) or []:
                marker = "process_outcome."
                if marker in str(ref):
                    ids.add(str(ref).split(marker, 1)[1])
        return ids


    def _active_ontology_dict(self, ws):
        """Parsed yaml of the active ontology version, or None if none is active."""
        vh = getattr(self._state, "ontology_version", None)
        if not vh:
            return None
        raw = ws.get_record(vh)
        if not raw:
            return None
        import yaml
        try:
            return yaml.safe_load(raw) or {}
        except Exception:
            return None


    def _adopt_record(self, ws, kg, version_hash: str, raw: str, rationale: str,
                      adopted_by: str = "human") -> dict:
        """Shared adoption path for the human tool and policy auto-adoption:
        verify concept_hashes (refuse on mismatch), project, core-verify the
        written nodes, publish the adoption version, audit."""
        import yaml
        parsed = yaml.safe_load(raw) or {}
        if not parsed.get("process_outcomes"):
            # An empty "ontology" (a disputed report, a malformed record) would
            # pass hash verification trivially, project nothing, and silently
            # deactivate gap detection — the factory would look perfect while
            # governing nothing. Refuse.
            return {"status": "error",
                    "message": ("refusing to adopt a document with zero process "
                                "outcomes — it is not a loadable ontology "
                                "(disputed reports and malformed records look "
                                "like this).")}
        checked, mismatches = governance.verify_concept_hashes(parsed)
        if mismatches:
            try:
                ws.record_event("ontology_adoption_rejected", version_hash,
                                details={"reason": "concept_hashes mismatch",
                                         "mismatches": str(len(mismatches))})
            except Exception:
                logger.warning("audit record_event failed for adoption_rejected", exc_info=True)
            return {"status": "error",
                    "message": ("concept_hashes verification failed — the proposal "
                                "content changed since extraction. Re-extract or "
                                "review the stored version."),
                    "hashes_checked": checked,
                    "mismatches": mismatches}
        ontology = self._load_ontology_from_raw(raw)
        projection = self._project_ontology(ontology)
        # Post-projection self-check: the nodes just written must pass the core's
        # stored-hash-vs-blob verification. Follows the projection to the ontology
        # graph — it verifies the nodes that were actually written.
        okg = self._state.ontology_kg
        projection_verified = True
        if hasattr(okg, "verify_node"):
            for nid in projection.get("projected_node_ids", []):
                try:
                    if okg.verify_node(nid) != "match":
                        projection_verified = False
                except Exception:
                    projection_verified = False
        # Re-publish the adopted version (new hash) with adoption info.
        version = ws.publish_ontology(
            raw, metadata={"version": ontology.version, "adopted_from": version_hash,
                           "rationale": rationale, "adopted_by": adopted_by})
        self._state.set_ontology(ontology, version.hash)
        ws.record_event(
            "ontology_adopted", version_hash,
            details={"new_version": version.hash,
                     "outcomes": str(projection["outcomes_projected"]),
                     "concept_hashes_checked": str(checked),
                     "adopted_by": adopted_by},
            rationale=rationale
        )
        return {
            "status": "ok",
            "adopted_from": version_hash,
            "new_version_hash": version.hash,
            "outcomes_projected": projection["outcomes_projected"],
            "characteristics_projected": projection["characteristics_projected"],
            "concept_hashes_checked": checked,
            "projection_verified": projection_verified,
            "adopted_by": adopted_by,
            "note": "Ontology adopted and active. Review via get_status or run procedures."
        }


    @operation
    def adopt_proposed_ontology(self, version_hash: str, rationale: str = None) -> dict:
        """Adopt a KEE-proposed ontology version (by its content hash) into the active KG.

        Fetches the versioned yaml from the workspace, verifies its concept_hashes
        (tamper-evidence: each hash is the core content hash of the concept node
        captured at extraction; adoption is refused on mismatch), projects it, and
        loads it as the active ontology (publishing a new versioned snapshot with
        adoption metadata). Requires a rationale for full audit trail. This
        completes the human-review loop.
        """
        denied = self._require("operator")
        if denied:
            return denied
        if not rationale or not rationale.strip():
            return {"status": "error", "message": "rationale is required for adoption"}
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "Load a Knowledge Graph first."}
        try:
            ws = self._get_workspace()
            raw = ws.get_record(version_hash)
            if not raw:
                return {"status": "error", "message": f"Unknown version '{version_hash}'."}
            return self._adopt_record(ws, kg, version_hash, raw, rationale, adopted_by="human")
        except Exception as e:
            logger.exception("adopt_proposed_ontology failed")
            return {"status": "error", "message": str(e)}


    @operation
    def evaluate_proposal(self, version_hash: str) -> dict:
        """Run the adoption-policy checks on a proposal without adopting it.

        Deterministic verdict over integrity (concept_hashes), validity, churn
        vs the active ontology (incl. PG-referenced removals), and provenance
        floor. The same checks drive auto-adoption when the policy is enabled
        (set_policy('enabled', True, rationale))."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            ws = self._get_workspace()
            raw = ws.get_record(version_hash)
            if not raw:
                return {"status": "error", "message": f"Unknown version '{version_hash}'."}
            import yaml
            proposal = yaml.safe_load(raw) or {}
            policy = governance.merged_policy(config_store.load_config(self._resolve_ws_root()))
            verdict = governance.evaluate_proposal_dict(
                proposal, self._active_ontology_dict(ws), policy,
                pg_referenced_ids=self._pg_referenced_outcome_ids())
            return {"status": "ok", "version_hash": version_hash,
                    "pass": verdict["pass"], "checks": verdict["checks"],
                    "diff": verdict["diff"], "policy": policy}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def rollback_ontology(self, to_version_hash: str, reason: str = None) -> dict:
        """Re-activate a previously published ontology version (compensating action).

        The dark-factory safety valve: a wrong (auto-)adoption costs one audited
        revert instead of an incident. Verifies the stored version's
        concept_hashes, re-projects it into the current KG, and records
        `ontology_rolled_back` with the reason."""
        denied = self._require("operator")
        if denied:
            return denied
        if not reason or not reason.strip():
            return {"status": "error", "message": "reason is required for rollback"}
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "Load a Knowledge Graph first."}
        try:
            ws = self._get_workspace()
            raw = ws.get_record(to_version_hash)
            if not raw:
                return {"status": "error", "message": f"Unknown version '{to_version_hash}'."}
            import yaml
            parsed = yaml.safe_load(raw) or {}
            if not parsed.get("process_outcomes"):
                return {"status": "error",
                        "message": ("refusing to roll back to a document with zero "
                                    "process outcomes — not a loadable ontology.")}
            checked, mismatches = governance.verify_concept_hashes(parsed)
            if mismatches:
                return {"status": "error",
                        "message": "stored version fails concept_hashes verification",
                        "mismatches": mismatches}
            previous = getattr(self._state, "ontology_version", None)
            ontology = self._load_ontology_from_raw(raw)
            projection = self._project_ontology(ontology)
            self._state.set_ontology(ontology, to_version_hash)
            ws.record_event("ontology_rolled_back", to_version_hash,
                            details={"from": previous or "",
                                     "outcomes": str(projection["outcomes_projected"])},
                            rationale=reason)
            return {"status": "ok", "active_version_hash": to_version_hash,
                    "rolled_back_from": previous,
                    "outcomes_projected": projection["outcomes_projected"],
                    "concept_hashes_checked": checked}
        except Exception as e:
            logger.exception("rollback_ontology failed")
            return {"status": "error", "message": str(e)}


    @operation
    def diff_ontologies(self, version_hash: str, against: str = None) -> dict:
        """What `version_hash` would change relative to `against` (default: the
        active ontology): outcomes added/removed/changed, characteristic churn."""
        try:
            ws = self._get_workspace()
            base_hash = against or getattr(self._state, "ontology_version", None)
            if not base_hash:
                return {"status": "error",
                        "message": "'against' omitted and no ontology is active"}
            raw_t = ws.get_record(version_hash)
            if not raw_t:
                return {"status": "error", "message": f"Unknown version '{version_hash}'."}
            raw_b = ws.get_record(base_hash)
            if not raw_b:
                return {"status": "error", "message": f"Unknown version '{base_hash}'."}
            import yaml
            diff = governance.diff_ontology_dicts(yaml.safe_load(raw_b) or {},
                                                  yaml.safe_load(raw_t) or {})
            return {"status": "ok", "base": base_hash, "target": version_hash, **diff}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def diff_runs(self, run_id_a: str, run_id_b: str) -> dict:
        """Gap-target delta between two runs: new, resolved, persistent.

        Dark-factory triage primitive — automation watches deltas; only new or
        regressed gaps deserve anyone's attention."""
        try:
            ws = self._get_workspace()

            def _targets(run_id):
                r = ws.get_run(run_id)
                if not r:
                    raise ValueError(f"Run '{run_id}' not found.")
                out = set()
                for h in (r.explanation_hashes or []):
                    rec = ws.get_record(h)
                    if rec:
                        try:
                            out.add(json.loads(rec).get("target"))
                        except ValueError:
                            pass
                out.discard(None)
                return out

            a, b = _targets(run_id_a), _targets(run_id_b)
            return {"status": "ok", "run_a": run_id_a, "run_b": run_id_b,
                    "new_gaps": sorted(b - a), "resolved_gaps": sorted(a - b),
                    "persistent_gaps": sorted(a & b),
                    "counts": {"a": len(a), "b": len(b),
                               "new": len(b - a), "resolved": len(a - b)}}
        except ValueError as e:
            return {"status": "error", "message": str(e)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def set_policy(self, key: str, value, rationale: str) -> dict:
        """Set an adoption-policy field (admin + rationale; persisted + audited).

        Keys: enabled (bool), max_outcomes_added, max_outcomes_removed,
        max_characteristics_removed, min_source_chunks (ints >= 0). Changing the
        policy is the designed rare human act; the policy then owns the routine
        adoption verdicts."""
        denied = self._require("admin")
        if denied:
            return denied
        if not rationale:
            return {"status": "error", "message": "rationale is required"}
        if key not in governance.DEFAULT_ADOPTION_POLICY:
            return {"status": "error",
                    "message": f"unknown policy key '{key}' "
                               f"(valid: {sorted(governance.DEFAULT_ADOPTION_POLICY)})"}
        try:
            if key in ("enabled", "canary_enabled", "pg_draft_enabled",
                       "pg_autopromote_enabled", "pg_write_back"):
                value = bool(value)
            else:
                value = int(value)
                if value < 0:
                    return {"status": "error", "message": "policy budgets must be >= 0"}
        except (TypeError, ValueError):
            return {"status": "error", "message": f"invalid value for '{key}'"}
        root = self._resolve_ws_root()
        cfg = config_store.load_config(root)
        pol = governance.merged_policy(cfg)
        old = pol.get(key)
        pol[key] = value
        cfg["adoption_policy"] = pol
        cfg["last_changed"] = int(time.time() * 1000)
        config_store.save_config(root, cfg)
        try:
            self._get_workspace().record_event("config_changed", f"adoption_policy.{key}",
                                          details={"from": str(old), "to": str(value)},
                                          rationale=rationale)
        except Exception:
            logger.warning("audit record_event failed for set_policy", exc_info=True)
        return {"status": "ok", "adoption_policy": pol}


    def _kv_subject(self, subject: str) -> dict:
        """Parse a machine-readable event subject of the form k1=v1;k2=v2.

        The pyo3 binding accepts event `details` on write but does not expose
        them on read, so factory events carry their state in the subject."""
        out = {}
        for part in str(subject or "").split(";"):
            if "=" in part:
                k, _, v = part.partition("=")
                out[k.strip()] = v.strip()
        return out


    def _latest_event(self, ws, event_type: str):
        """Most recent event of a type (audit order is append-only), or None."""
        evs = ws.query_events(event_type) or []
        return evs[-1] if evs else None


    @operation
    def list_pending_interventions(self, limit: int = None, cursor: str = None) -> dict:
        """The exception queue: everything currently awaiting a human, oldest
        first. Sources: proposals not yet adopted (awaiting review, or rejected by
        the adoption policy / integrity gate), parked extraction items in the open
        KEE workdir, and latest-state alarms (failed maintenance, integrity
        mismatches, gap regressions) that clear on the next healthy run of their
        producer. Empty queue = the factory needs nobody."""
        try:
            ws = self._get_workspace()
            adopted = {e.subject for e in (ws.query_events("ontology_adopted") or [])}
            latest = {}  # proposal hash -> (ts, kind); later events refine the kind
            for e in ws.query_events("ontology_proposed") or []:
                latest[e.subject] = (e.ts, "proposal_awaiting_review")
            for e in ws.query_events("adoption_policy_rejected") or []:
                if e.subject in latest:
                    latest[e.subject] = (e.ts, "policy_rejected")
            for e in ws.query_events("ontology_adoption_rejected") or []:
                latest[e.subject] = (e.ts, "integrity_rejected")
            now_ms = int(time.time() * 1000)
            # Superseded proposals clear (law-ro pilot finding): any proposal
            # event older than the latest adoption lost the race to a newer
            # candidate — it is dead work, not pending work.
            adopt_events = ws.query_events("ontology_adopted") or []
            last_adopt_ts = adopt_events[-1].ts if adopt_events else 0
            items = [{"kind": kind, "subject": subj, "ts": ts,
                      "age_s": max(0, round((now_ms - ts) / 1000))}
                     for subj, (ts, kind) in latest.items()
                     if subj not in adopted and ts > last_adopt_ts]
            wd = getattr(self._state, "extraction_workdir", None)
            if wd:
                try:
                    import sqlite3
                    db = Path(wd) / "graph_store.db"
                    if db.exists():
                        conn = sqlite3.connect(str(db))
                        n = conn.execute("SELECT COUNT(*) FROM parked").fetchone()[0]
                        conn.close()
                        if n:
                            items.append({"kind": "parked_extraction_items",
                                          "subject": str(wd), "count": n,
                                          "ts": now_ms, "age_s": 0})
                except Exception:
                    pass
            # Latest-state alarms: pending while the most recent event of the
            # producer reports trouble; the next healthy run clears them.
            ev = self._latest_event(ws, "maintenance_run")
            if ev and str(ev.subject).startswith("failed"):
                items.append({"kind": "maintenance_failed", "subject": ev.subject,
                              "ts": ev.ts, "age_s": max(0, round((now_ms - ev.ts) / 1000))})
            ev = self._latest_event(ws, "graph_verified")
            if ev and int(self._kv_subject(ev.subject).get("mismatch", 0) or 0) > 0:
                items.append({"kind": "integrity_alarm", "subject": ev.subject,
                              "ts": ev.ts, "age_s": max(0, round((now_ms - ev.ts) / 1000))})
            ev = self._latest_event(ws, "evidence_ingest")
            if ev:
                kv = self._kv_subject(ev.subject)
                if int(kv.get("dirs", 0) or 0) > 0 and int(kv.get("files", -1) or 0) == 0:
                    items.append({"kind": "evidence_empty", "subject": ev.subject,
                                  "ts": ev.ts,
                                  "age_s": max(0, round((now_ms - ev.ts) / 1000))})
            ev = self._latest_event(ws, "gap_delta")
            if ev:
                kv = self._kv_subject(ev.subject)
                new = int(kv.get("new", 0) or 0)
                resolved = int(kv.get("resolved", 0) or 0)
                # `growth` = new gaps on outcomes that did not exist at the baseline
                # run; those are expected, not regressions. Absent (older events) it
                # defaults to 0, i.e. the previous gross behaviour.
                growth = int(kv.get("growth", 0) or 0)
                regressions = max(0, new - growth)
                # Fire on a NET regression of *regressions*, not on any new gap at
                # all. Two flaws were compounding here: counting gross additions made
                # "new=3; resolved=6" — a net improvement — queue as a regression; and
                # counting growth as regression made a healthy adoption of 106
                # outcomes report "new=713" and queue. Both are the same mistake the
                # canary made before `_baseline_outcome_ids` partitioned it.
                if regressions > resolved:
                    items.append({"kind": "gap_regression", "subject": ev.subject,
                                  "ts": ev.ts,
                                  "age_s": max(0, round((now_ms - ev.ts) / 1000))})
            ev = self._latest_event(ws, "canary_result")
            if ev and self._kv_subject(ev.subject).get("result") == "failed":
                items.append({"kind": "canary_failed", "subject": ev.subject,
                              "ts": ev.ts, "age_s": max(0, round((now_ms - ev.ts) / 1000))})
            ev = self._latest_event(ws, "model_disagreement")
            if ev:
                kv = self._kv_subject(ev.subject)
                if int(kv.get("outcomes", 0) or 0) + int(kv.get("disputed_chars", 0) or 0) > 0:
                    items.append({"kind": "model_disagreement", "subject": ev.subject,
                                  "ts": ev.ts,
                                  "age_s": max(0, round((now_ms - ev.ts) / 1000))})
            # PG reference patches: pending until promoted
            promoted_pg = {e.subject for e in (ws.query_events("version_promoted") or [])}
            for e in ws.query_events("pg_draft_result") or []:
                kv = self._kv_subject(e.subject)
                if (kv.get("result") == "ok" and kv.get("version")
                        and kv["version"] not in promoted_pg):
                    items.append({"kind": "pg_draft_pending", "subject": e.subject,
                                  "ts": e.ts,
                                  "age_s": max(0, round((now_ms - e.ts) / 1000))})
            ev = self._latest_event(ws, "pg_draft_result")
            if ev and self._kv_subject(ev.subject).get("result") == "failed":
                items.append({"kind": "pg_draft_failed", "subject": ev.subject,
                              "ts": ev.ts, "age_s": max(0, round((now_ms - ev.ts) / 1000))})
            items.sort(key=lambda i: i["ts"])
            page, meta = paginate(items, limit, cursor)
            return {"status": "ok", "pending": page, "count": len(page), **meta,
                    "note": "empty queue = the factory needs nobody"}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def verify_graph(self, sample: int = 200) -> dict:
        """Core integrity sweep: verify stored content hashes for up to `sample`
        evenly spaced nodes of the current KG. Records `graph_verified`
        (checked=N;mismatch=M;unknown=U); a mismatch is an integrity alarm that
        surfaces in the exception queue until a clean sweep clears it."""
        denied = self._require("operator")
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "no Knowledge Graph loaded"}
        if not hasattr(kg, "verify_node"):
            return {"status": "error", "message": "core verify_node unavailable on this KG"}
        try:
            total = kg.node_count()
            checked = mismatch = unknown = 0
            if total:
                n = max(1, min(int(sample), total))
                step = max(1, total // n)
                for nid in range(0, total, step):
                    try:
                        v = kg.verify_node(nid)
                    except Exception:
                        v = "unknown"
                    checked += 1
                    if v == "mismatch":
                        mismatch += 1
                    elif v == "unknown":
                        unknown += 1
            subject = f"checked={checked};mismatch={mismatch};unknown={unknown}"
            try:
                self._get_workspace().record_event("graph_verified", subject)
            except Exception:
                logger.warning("audit record_event failed for graph_verified", exc_info=True)
            return {"status": "ok", "nodes_total": total, "checked": checked,
                    "match": checked - mismatch - unknown,
                    "mismatch": mismatch, "unknown": unknown,
                    "integrity_ok": mismatch == 0}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def run_maintenance(self) -> dict:
        """The maintenance autopilot: compact the KG, rebuild the entity index,
        enforce retention, and run an integrity sweep — one idempotent call for a
        scheduler. Records `maintenance_run` ('ok' or 'failed:<steps>'); a failed
        latest run stays in the exception queue until the next clean one."""
        denied = self._require("admin")
        if denied:
            return denied
        steps, failed = {}, []

        def _step(name, fn, skip=None):
            if skip:
                steps[name] = {"status": "skipped", "note": skip}
                return
            try:
                r = fn()
                steps[name] = r if isinstance(r, dict) else {"status": "ok"}
                if isinstance(r, dict) and r.get("status") == "error":
                    failed.append(name)
            except Exception as e:
                steps[name] = {"status": "error", "message": str(e)}
                failed.append(name)

        kg = self._state.kg
        _step("compact_graph", self.compact_graph, skip=None if kg else "no KG loaded")
        _step("reindex_entities", self.reindex_entities,
              skip=None if (kg and self._state.kg_path) else "no persisted KG")
        _step("enforce_retention", self.enforce_retention)
        _step("verify_graph", self.verify_graph, skip=None if kg else "no KG loaded")

        ok = not failed
        subject = "ok" if ok else "failed:" + ",".join(failed)
        try:
            self._get_workspace().record_event("maintenance_run", subject)
        except Exception:
            logger.warning("audit record_event failed for maintenance_run", exc_info=True)
        return {"status": "ok" if ok else "error", "ok": ok,
                "failed": failed, "steps": steps}


    @operation
    def get_factory_status(self) -> dict:
        """One call answering: is the factory healthy and does it need anyone?

        Policy state, exception-queue depth, gap trend over recent production
        runs (with a spike alarm), and latest-state alarms. healthy=true means
        the factory needs nobody. Also writes <ws_root>/factory_pulse.json for
        dashboards/notifiers to poll."""
        try:
            ws = self._get_workspace()
            policy = governance.merged_policy(config_store.load_config(self._resolve_ws_root()))
            queue = self.list_pending_interventions()
            pending = queue.get("count", 0) if isinstance(queue, dict) else 0
            runs = ws.list_runs("production") or []
            trend = [{"run_id": r.run_id, "gaps": r.gaps_found} for r in runs[-5:]]
            alarms = []
            if (len(trend) >= 2 and trend[-2]["gaps"] > 0
                    and trend[-1]["gaps"] >= trend[-2]["gaps"] * 1.5):
                alarms.append(f"gap spike: {trend[-2]['gaps']} -> {trend[-1]['gaps']}")
            for i in (queue.get("pending") or []):
                if i["kind"] in ("integrity_alarm", "maintenance_failed",
                                 "gap_regression", "canary_failed", "pg_draft_failed"):
                    alarms.append(f"{i['kind']}: {i['subject']}")
            status = {
                "status": "ok",
                "policy_enabled": bool(policy.get("enabled")),
                "pending_interventions": pending,
                "gap_trend": trend,
                "alarms": alarms,
                "healthy": pending == 0 and not alarms,
                "note": "healthy=true means the factory needs nobody",
            }
            try:
                (Path(self._resolve_ws_root()) / "factory_pulse.json").write_text(
                    json.dumps({**status, "ts": time.time()}), encoding="utf-8")
            except Exception:
                pass
            return status
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def run_pipeline(self, config_path: str = "pipeline.json") -> dict:
        """Run the whole factory line once from a declarative JSON config —
        idempotent and scheduler-friendly (run_factory.py wraps this in a watch
        loop). Config keys:

          kg_path (str, required)   evidence KG dir (loaded, or created if absent)
          evidence_dirs (list)      incremental ingest sources (manifest-based)
          extract {docs_path, workdir?, sim?, max_files?, chunk_chars?, concurrency?}
                                    workdir -> incremental KEE working graph (only
                                    changed chunks hit the LLM); else stateless
          detect_gaps (bool)        gap run + delta vs the previous production run
                                    (gap_delta event feeds the exception queue)
          maintenance (bool)        run the maintenance autopilot at the end

        A policy rejection never hard-stops the line; it lands in the queue."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            cfg = json.loads(Path(config_path).read_text(encoding="utf-8"))
        except Exception as e:
            return {"status": "error", "message": f"cannot read config '{config_path}': {e}"}
        report = {"status": "ok", "steps": {}}
        # Wall-clock per pipeline step. A batch is ~69 minutes and until now the
        # only visible number was the total, so "which part is slow" was answered by
        # reasoning rather than measurement — and the reasoning was wrong twice.
        step_s: dict[str, float] = {}
        _step_t = time.time()

        def _mark(name: str) -> None:
            nonlocal _step_t
            step_s[name] = round(time.time() - _step_t, 1)
            _step_t = time.time()
        try:
            kgp = cfg.get("kg_path")
            if not kgp:
                return {"status": "error", "message": "config needs kg_path"}
            r = self.load_knowledge_graph(kgp) if Path(kgp).exists() else self.create_knowledge_graph(kgp)
            report["steps"]["kg"] = {"status": r.get("status"), "path": kgp}
            if r.get("status") != "ok":
                report["status"] = "error"
                return report

            # Optional PG: loaded before extraction so churn checks see its
            # references and pg_draft auto-triggering has a PG to patch.
            if cfg.get("pg_path"):
                rp = self.load_procedure_graph(cfg["pg_path"])
                report["steps"]["pg"] = {"status": rp.get("status"),
                                         "name": rp.get("name")}

            ingested = []
            for d in cfg.get("evidence_dirs") or []:
                rr = self.reingest_documents(d)
                ingested.append({"path": d, "status": rr.get("status"),
                                 "files": rr.get("files"),
                                 "nodes_added": rr.get("nodes_added"),
                                 "nodes_removed": rr.get("nodes_removed"),
                                 "skipped": rr.get("skipped")})
            report["steps"]["evidence"] = ingested
            _mark("evidence")
            if ingested:
                # Latest-state alarm feed: gap detection against a KG that holds
                # zero evidence silently reports every characteristic as a gap
                # (law-ro pilot finding 13 — the corpus was .json, the ingester
                # only took .md/.txt, and seven tuning cycles scored nothing).
                # files=0 across all configured dirs must surface in the queue.
                total_files = sum(int(e.get("files") or 0) for e in ingested)
                try:
                    self._get_workspace().record_event(
                        "evidence_ingest",
                        f"files={total_files};dirs={len(ingested)}")
                except Exception:
                    logger.warning("audit record_event failed for evidence_ingest",
                                   exc_info=True)

            ext = cfg.get("extract") or {}
            if ext.get("docs_path"):
                if ext.get("workdir"):
                    from .kee.graph_extract import extend_graph_ontology
                    from .kee import pipeline as kee_pipeline
                    out = extend_graph_ontology(
                        ext["workdir"], ext["docs_path"], self.get_provider(), self.get_embedder(),
                        sim=float(ext.get("sim", 0.86)),
                        relook_ratio=float(ext.get("relook_ratio", 0.2)),
                        max_files=ext.get("max_files"),
                        chunk_chars=int(ext.get("chunk_chars", 800)),
                        concurrency=int(ext.get("concurrency", 1)),
                        refresh=bool(ext.get("refresh", False)))
                    yaml_text = kee_pipeline.to_yaml(out["ontology"])
                    version, auto = self._publish_and_gate_proposal(
                        yaml_text, ext["docs_path"],
                        getattr(self.get_provider(), "name", "?"),
                        getattr(self.get_embedder(), "name", "?"),
                        len(out["ontology"].get("process_outcomes", [])),
                        extraction_complete=not out.get("pending_chunks"))
                    # Forward the extractor's own telemetry rather than a fixed key
                    # list: quality indicators and promotion counts were being
                    # silently dropped here, so a run's extraction *shape* never
                    # reached the caller even though the extractor computed it.
                    report["steps"]["extract"] = {
                        "status": "ok", "mode": "incremental",
                        "added": out["added"], "changed": out["changed"],
                        "removed": out["removed"], "version_hash": version.hash,
                        "auto_adoption": auto,
                        "quality": out.get("quality"),
                        "context_mode": out.get("context_mode"),
                        "promoted": out.get("promoted"),
                        "relations_recovered": out.get("relations_recovered"),
                        "parked": out.get("parked"),
                        "quarantined": out.get("quarantined"),
                        "stale_chunks": out.get("stale_chunks"),
                        "pending_chunks": out.get("pending_chunks"),
                        "phase_s": out.get("phase_s")}
                    _mark("extract")
                elif ext.get("models"):
                    rr = self.extract_ontology_nversion(ext["docs_path"],
                                                   models=ext["models"],
                                                   sim=float(ext.get("sim", 0.86)),
                                                   max_files=ext.get("max_files"))
                    report["steps"]["extract"] = {
                        "status": rr.get("status"), "mode": "n-version",
                        "version_hash": rr.get("version_hash"),
                        "auto_adoption": rr.get("auto_adoption"),
                        "agreed_outcomes": rr.get("agreed_outcomes"),
                        "disputed_version_hash": rr.get("disputed_version_hash")}
                    if rr.get("status") != "ok":
                        report["status"] = "error"
                else:
                    rr = self.extract_ontology(ext["docs_path"],
                                          sim=float(ext.get("sim", 0.86)),
                                          max_files=ext.get("max_files"))
                    report["steps"]["extract"] = {
                        "status": rr.get("status"), "mode": "stateless",
                        "version_hash": rr.get("version_hash"),
                        "auto_adoption": rr.get("auto_adoption")}
                    if rr.get("status") != "ok":
                        report["status"] = "error"

            if cfg.get("detect_gaps") and self._state.ontology is not None:
                g = self.detect_gaps()
                step = {"status": g.get("status") if isinstance(g, dict) else "ok",
                        "run_id": g.get("run_id") if isinstance(g, dict) else None,
                        "gaps_found": g.get("gaps_found") if isinstance(g, dict) else None}
                ws = self._get_workspace()
                prod = ws.list_runs("production") or []
                if len(prod) >= 2:
                    d = self.diff_runs(prod[-2].run_id, prod[-1].run_id)
                    if d.get("status") == "ok":
                        # Partition new gaps into GROWTH (on outcomes that did not
                        # exist when the baseline run scored) and REGRESSION (on
                        # outcomes that did). Without this the alarm counts growth as
                        # regression: adopting 106 outcomes brings ~300
                        # initially-uncovered characteristics, so a healthy cycle
                        # reports "new=713" and queues. Same fix as the canary's
                        # `_baseline_outcome_ids` partition — the flaw was fixed there
                        # and left standing here.
                        base_ids = self._baseline_outcome_ids(
                            ws, getattr(prod[-2], "kg_version", None))
                        growth = 0
                        if base_ids is not None:
                            growth = sum(1 for t in d.get("new_gaps") or []
                                         if str(t).partition(":")[0] not in base_ids)
                        step["delta"] = {**d["counts"], "growth": growth,
                                         "regressions": d["counts"]["new"] - growth}
                        subject = (f"new={d['counts']['new']};"
                                   f"resolved={d['counts']['resolved']};"
                                   f"growth={growth};"
                                   f"run={prod[-1].run_id}")
                        try:
                            ws.record_event("gap_delta", subject)
                        except Exception:
                            logger.warning("audit record_event failed for gap_delta",
                                           exc_info=True)
                _mark("gaps")
                report["steps"]["gaps"] = step

            if cfg.get("maintenance"):
                report["steps"]["maintenance"] = self.run_maintenance()

            try:
                self._get_workspace().record_event(
                    "pipeline_run", "ok" if report["status"] == "ok" else "failed")
            except Exception:
                pass
            report["step_s"] = step_s
            logger.info("run_pipeline steps: %s",
                        " ".join(f"{k}={v}s" for k, v in step_s.items()))
            return report
        except Exception as e:
            logger.exception("run_pipeline failed")
            return {"status": "error", "message": str(e), "steps": report.get("steps"),
                    "step_s": step_s}


    @operation
    def open_extraction_graph(self, path: str) -> dict:
        """Open a durable extraction graph and load its projection as the active KG
        so the existing graph/query tools operate on it. Returns counts."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            from .kee.working_graph import WorkingGraph
            wg = WorkingGraph.open(path, self.get_embedder())
            # kg_path stays None: the projection at <workdir>/graph is derived from
            # the sqlite store — persisting the active KG over it (what a non-None
            # kg_path invites via ingest/compact save_to_dir) would desync the two.
            # Durable changes go through extend_extraction_graph.
            self._state.set_kg(wg.kg, None)
            self._state.extraction_workdir = path
            out = {"status": "ok",
                   "concepts": len(wg._live_concepts()),
                   "edges": len(wg.relations),
                   "chunks": len(wg.manifest)}
            wg.close()  # non-owned workdir: releases the sqlite handle only; wg.kg stays valid
            return out
        except Exception as e:
            logger.exception("open_extraction_graph failed")
            return {"status": "error", "message": str(e)}


    @operation
    def extend_extraction_graph(self, path: str, docs_path: str, model: str = None,
                                sim: float = 0.86, relook_ratio: float = 0.2,
                                max_files: int = None, chunk_chars: int = 800,
                                concurrency: int = 1, refresh: bool = False) -> dict:
        """Sync a durable extraction graph to `docs_path` (new/changed/removed) and
        persist it, then load its projection as the active KG. Returns sync stats.

        `chunk_chars` controls chunk size (bigger -> fewer LLM calls); changing it
        on an existing workdir re-chunks and re-extracts everything.
        `concurrency` runs that many LLM calls in parallel per window (needs a
        backend that accepts parallel requests, e.g. OLLAMA_NUM_PARALLEL)."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            from .kee.graph_extract import extend_graph_ontology
            from .kee.working_graph import WorkingGraph
            out = extend_graph_ontology(path, docs_path, self.get_provider(model), self.get_embedder(),
                                        sim=sim, relook_ratio=relook_ratio, max_files=max_files,
                                        chunk_chars=chunk_chars, concurrency=concurrency, refresh=refresh)
            wg = WorkingGraph.open(path, self.get_embedder())
            self._state.set_kg(wg.kg, None)  # projection is derived; see open_extraction_graph
            self._state.extraction_workdir = path
            wg.close()  # non-owned workdir: releases the sqlite handle only; wg.kg stays valid
            out["status"] = "ok"
            return {k: out[k] for k in ("status", "added", "changed", "removed",
                                        "concepts", "edges", "relooks", "parked",
                                        "stale_chunks", "pending_chunks", "quality")}
        except Exception as e:
            logger.exception("extend_extraction_graph failed")
            return {"status": "error", "message": str(e)}


    @operation
    def get_extraction_evidence(self, path: str, source: str) -> dict:
        """Read a chunk's source text, checked quotes, proposed claims and model.

        Quote offsets establish traceability, not semantic truth. Earlier stores
        need an explicit refresh to acquire receipts. A failed refresh leaves its
        previous receipt available alongside the latest failure record.
        """
        denied = self._require("operator")
        if denied:
            return denied
        db = Path(path) / "graph_store.db"
        if not db.is_file():
            return {"status": "error", "message": "No extraction store at this path"}
        from .kee.graph_store import GraphStore
        try:
            with GraphStore(str(db)) as store:
                record = store.get_extraction(source)
                failure = store.conn.execute("SELECT content_hash, reason, attempts, quarantined "
                                             "FROM chunk_failures WHERE chunk_id=?", (source,)).fetchone()
            return {"status": "ok", "source": source, "receipt": record,
                    "latest_failure": dict(failure) if failure else None,
                    "note": None if record else "No source receipt; refresh older extractions to record one"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @operation
    def export_ontology(self, path: str, out_path: str = None) -> dict:
        """Export the durable graph's candidate ontology (and optionally write YAML)."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            from .kee.working_graph import WorkingGraph
            from .kee.pipeline import to_yaml
            wg = WorkingGraph.open(path, self.get_embedder())
            onto = wg.to_candidate_ontology()
            wg.close()
            result = {"status": "ok", "ontology": onto}
            if out_path:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(to_yaml(onto))
                result["yaml_path"] = out_path
            return result
        except Exception as e:
            logger.exception("export_ontology failed")
            return {"status": "error", "message": str(e)}


    @operation
    def project_relations(self, relations: list = None) -> dict:
        """Project extracted relations as typed edges in the current Knowledge Graph.

        Each relation's `from`/`to` become nodes (deduped by label within the call)
        and a typed edge (`relation_type`) connects them — turning KEE's proposed
        relations into a navigable graph alongside any existing edges. Creates a
        fresh KG in the workspace if none is loaded.
        """
        denied = self._require("operator") or self._require_mutable_evidence()
        if denied:
            return denied
        relations = relations or []
        try:
            kg = self._state.kg
            if not kg:
                # build a dedicated relations graph (don't clobber/contend the main KG)
                res = self.create_knowledge_graph(str(Path(self._resolve_ws_root()) / "relations-kg"))
                if res.get("status") == "error":
                    return res
                kg = self._state.kg

            node_by_label: dict = {}

            def node_for(name: str) -> int:
                label = ("kee:" + name.strip())[:120]
                nid = node_by_label.get(label)
                if nid is None:
                    nid = kg.add_node(text=name.strip(), label=label)
                    node_by_label[label] = nid
                return nid

            edges = 0
            for r in relations:
                f, t, ty = r.get("from"), r.get("to"), r.get("type")
                if not all(isinstance(x, str) and x.strip() for x in (f, t, ty)):
                    continue
                kg.add_edge(node_for(f), node_for(t), 0.7,
                            {"relation_type": ty.strip(), "source": "kee_extraction"})
                edges += 1

            kg_path = self._state.kg_path
            if kg_path:
                kg.save_to_dir(kg_path)
            try:
                self._get_workspace().record_event(
                    "relations_projected", "kee",
                    details={"edges": str(edges), "nodes": str(len(node_by_label))})
            except Exception:
                logger.warning("audit record_event failed for relations_projected", exc_info=True)
            return {"status": "ok", "nodes_added": len(node_by_label), "edges_added": edges,
                    "node_count": kg.node_count(), "edge_count": kg.edge_count()}
        except Exception as e:
            logger.exception("project_relations failed")
            return {"status": "error", "message": str(e)}


    @operation
    def extract_legal_claims(self, path: str, max_files: int = 3, max_chars: int = 8000,
                             refresh: bool = False, model: str = None) -> dict:
        """Extract qualified legal proposals from a JSON file or folder into this workspace.

        max_files limits document attempts, not the coverage scan or transport retries. Only whole documents
        within max_chars are extracted; oversized sources are explicitly deferred.
        Unchanged successful or failed sources are skipped unless refresh is set. Prior
        revisions survive failed refreshes. Claims remain unreviewed proposals.
        """
        denied = self._require("operator")
        if denied:
            return denied
        from .kee.legal_claims import LegalClaimStore
        try:
            with LegalClaimStore(Path(self._resolve_ws_root()) / "legal-claims.sqlite") as store:
                return store.ingest_folder(path, self.get_provider(model=model), max_files=max_files,
                                           max_chars=max_chars, refresh=refresh)
        except Exception as exc:
            logger.exception("extract_legal_claims failed")
            return {"status": "error", "message": str(exc)}

    @operation
    def get_legal_claims(self, kind: str = None, source_id: str = None,
                         revision_id: str = None, include_stale: bool = False,
                         limit: int = 20, offset: int = 0) -> dict:
        """Read typed legal proposals, or an exact revision's full source/evidence receipt.

        kind is fact, definition, right, obligation, prohibition, permission or
        amendment. Stale sources are excluded by default. revision_id requests
        historical evidence explicitly and ignores the other filters.
        """
        from .kee.legal_claims import LegalClaimStore
        try:
            with LegalClaimStore(Path(self._resolve_ws_root()) / "legal-claims.sqlite") as store:
                result = (store.receipt(revision_id) if revision_id else
                          store.read(kind=kind, source_id=source_id, include_stale=include_stale,
                                     limit=limit, offset=offset))
                return {"status": "ok", **result}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @operation
    def get_legal_sources(self, source_id: str = None, limit: int = 20, offset: int = 0) -> dict:
        """List observed files, including failures/deferrals, or one source's revision history."""
        from .kee.legal_claims import LegalClaimStore
        try:
            with LegalClaimStore(Path(self._resolve_ws_root()) / "legal-claims.sqlite") as store:
                result = store.source_history(source_id) if source_id else store.sources(limit=limit, offset=offset)
                return {"status": "ok", **result}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @operation
    def export_legal_claim_graph(self) -> dict:
        """Export accepted legal proposals as a new Rust graph snapshot with source receipts.

        Each claim remains a qualified node, linked to its source revision. Returns
        a graph_path for load_knowledge_graph; does not change the active graph.
        """
        denied = self._require("operator")
        if denied:
            return denied
        from .kee.legal_claims import LegalClaimStore
        root = Path(self._resolve_ws_root())
        try:
            with LegalClaimStore(root / "legal-claims.sqlite") as store:
                return {"status": "ok", **store.export_graph(root / "legal-claim-snapshots" / uuid.uuid4().hex)}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @operation
    def build_legal_graph(self, path: str, max_files: int = 400, with_relations: bool = False,
                          relation_files: int = 3, model: str = None) -> dict:
        """Build one navigable legal KG: act nodes + citation ('cites') edges merged
        with KEE-extracted entity relations.

        Citations are resolved over up to `max_files` acts (fast, regex). When
        `with_relations` is set, KEE runs over the first `relation_files` acts (LLM)
        and its relations are projected as entity nodes + typed edges, linked back to
        their source act via 'mentions' edges — connecting the two networks.

        Returns the connected subgraph (nodes with at least one edge) for display,
        plus full KG counts.
        """
        denied = self._require("operator")
        if denied:
            return denied
        try:
            from .kee import legal, pipeline, llm, embeddings
            acts = legal.load_acts(path, max_files=max_files)
            if not acts:
                return {"status": "error", "message": f"no legal-act JSON found under {path}"}

            kgp = str(Path(self._resolve_ws_root()) / "legal-kg")
            res = self.create_knowledge_graph(kgp)
            if res.get("status") == "error":
                return res
            kg = self._state.kg

            node_info: dict = {}      # node_id -> {label, kind, title}
            id_to_node: dict = {}     # act id -> node_id
            for a in acts:
                label = f"act-{a['id']}"
                nid = kg.add_node(text=(a["title"] + "\n\n" + a["text"]).strip() or label, label=label)
                id_to_node[a["id"]] = nid
                node_info[nid] = {"id": nid, "label": label, "kind": "act",
                                  "title": (a["title"] or label)[:140]}

            edges: list = []
            citations = legal.citation_report(acts)
            for s, t in citations["pairs"]:
                if s in id_to_node and t in id_to_node:
                    kg.add_edge(id_to_node[s], id_to_node[t], 0.8, {"relation_type": "cites"})
                    edges.append({"source": id_to_node[s], "target": id_to_node[t], "type": "cites"})

            rel_count = 0
            if with_relations:
                provider = self.get_provider(model=model)
                embedder = self.get_embedder()
                r = pipeline.extract_ontology(path, provider, embedder, sim=0.8,
                                              max_files=relation_files)
                relations = r["ontology"].get("relations", [])
                rel_count = len(relations)
                ent_node: dict = {}
                mentions_seen: set = set()

                def ent_for(name: str) -> int:
                    lbl = ("kee:" + name.strip())[:120]
                    nid = ent_node.get(lbl)
                    if nid is None:
                        nid = kg.add_node(text=name.strip(), label=lbl)
                        ent_node[lbl] = nid
                        node_info[nid] = {"id": nid, "label": lbl, "kind": "entity",
                                          "title": name.strip()[:140]}
                    return nid

                for rel in relations:
                    f, t, ty = rel.get("from"), rel.get("to"), rel.get("type")
                    if not all(isinstance(x, str) and x.strip() for x in (f, t, ty)):
                        continue
                    fn, tn = ent_for(f), ent_for(t)
                    kg.add_edge(fn, tn, 0.7, {"relation_type": ty.strip(), "source": "kee"})
                    edges.append({"source": fn, "target": tn, "type": ty.strip()})
                    # link extracted entities back to the act they came from
                    for src in rel.get("sources", []):
                        act_id = src.split("#")[0]
                        an = id_to_node.get(act_id)
                        if an is not None and (an, fn) not in mentions_seen:
                            mentions_seen.add((an, fn))
                            kg.add_edge(an, fn, 0.5, {"relation_type": "mentions"})
                            edges.append({"source": an, "target": fn, "type": "mentions"})

            kg.save_to_dir(kgp)
            # Citation offsets refer to the original body, retained in the graph.
            # Keep source identity and unresolved references alongside native storage.
            citation_path = Path(kgp) / "citation-report.json"
            citation_path.write_text(json.dumps(citations, ensure_ascii=False, indent=2), encoding="utf-8")
            (Path(kgp) / "legal-sources.json").write_text(json.dumps(
                [{key:value for key,value in a.items() if key != "text"} for a in acts],
                ensure_ascii=False, indent=2), encoding="utf-8")
            try:
                self._get_workspace().record_event("legal_graph_built", path,
                                              details={"acts": str(len(acts)),
                                                       "edges": str(len(edges))})
            except Exception:
                logger.warning("audit record_event failed for legal_graph_built", exc_info=True)

            all_types = sorted({e["type"] for e in edges})
            # Initial view = the CITATION BACKBONE (act nodes + 'cites' edges). The
            # extracted entity/relation layer stays in the KG and is revealed on
            # demand via expand-on-click (graph_neighbors). Acts that have extracted
            # entities (a 'mentions' edge) are also shown so they can be expanded.
            cites = [e for e in edges if e["type"] == "cites"]
            shown_act_ids: set = set()
            for e in cites:
                shown_act_ids.add(e["source"])
                shown_act_ids.add(e["target"])
            for e in edges:
                if e["type"] == "mentions":
                    shown_act_ids.add(e["source"])   # the act side of a mention
            connected = [node_info[n] for n in shown_act_ids
                         if n in node_info and node_info[n]["kind"] == "act"][:200]
            connected_ids = {n["id"] for n in connected}
            shown_edges = [e for e in cites
                           if e["source"] in connected_ids and e["target"] in connected_ids][:600]

            return {
                "status": "ok",
                "nodes": connected,
                "edges": shown_edges,
                "edge_types": all_types,   # all types (incl. mentions/relations) for expand
                "citation_report": str(citation_path),
                "coverage": {"missing_references": sum(r["status"] == "missing" for r in citations["references"]),
                             "ambiguous_references": sum(r["status"] == "ambiguous" for r in citations["references"]),
                             "unidentified_acts": len(citations["unidentified_acts"])},
                "counts": {
                    "acts": len(acts),
                    "cites": sum(1 for e in edges if e["type"] == "cites"),
                    "relations": rel_count,
                    "entities": sum(1 for n in node_info.values() if n["kind"] == "entity"),
                    "shown_nodes": len(connected),
                    "shown_edges": len(shown_edges),
                    "node_count": kg.node_count(),
                    "edge_count": kg.edge_count(),
                },
            }
        except Exception as e:
            logger.exception("build_legal_graph failed")
            return {"status": "error", "message": str(e)}


    @operation
    def legal_graph_from_relations(self, path: str, top: int = 200) -> dict:
        """Load a relations JSON file (e.g. relations.curated.json from the batch
        runner) into a fresh KG and return it for the graph view.

        Each relation's endpoints become entity nodes (deduped) and a typed edge
        connects them; the KG is saved so expand-on-click (graph_neighbors) works.
        Returns the connected subgraph capped at the top-`top` most-connected nodes.
        """
        denied = self._require("operator")
        if denied:
            return denied
        try:
            rels = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            return {"status": "error", "message": f"cannot read relations file '{path}': {e}"}
        if not isinstance(rels, list):
            return {"status": "error", "message": "expected a JSON list of {from,type,to} relations"}
        try:
            kgp = str(Path(self._resolve_ws_root()) / "relations-kg")
            res = self.create_knowledge_graph(kgp)
            if res.get("status") == "error":
                return res
            kg = self._state.kg

            node_info: dict = {}
            ent: dict = {}

            def ent_for(name: str) -> int:
                lbl = ("kee:" + name.strip())[:120]
                nid = ent.get(lbl)
                if nid is None:
                    nid = kg.add_node(text=name.strip(), label=lbl)
                    ent[lbl] = nid
                    node_info[nid] = {"id": nid, "label": lbl, "kind": "entity",
                                      "title": name.strip()[:140]}
                return nid

            edges: list = []
            for r in rels:
                f, t, ty = r.get("from"), r.get("to"), r.get("type")
                if not all(isinstance(x, str) and x.strip() for x in (f, t, ty)):
                    continue
                fn, tn = ent_for(f), ent_for(t)
                kg.add_edge(fn, tn, 0.7, {"relation_type": ty.strip(), "source": "kee"})
                edges.append({"source": fn, "target": tn, "type": ty.strip()})

            kg.save_to_dir(kgp)
            try:
                self._get_workspace().record_event("relations_graph_loaded", path,
                                              details={"relations": str(len(edges))})
            except Exception:
                logger.warning("audit record_event failed for relations_graph_loaded", exc_info=True)

            degree: dict = {}
            for e in edges:
                degree[e["source"]] = degree.get(e["source"], 0) + 1
                degree[e["target"]] = degree.get(e["target"], 0) + 1
            ranked = sorted(degree, key=lambda n: degree[n], reverse=True)
            keep = set(ranked[:top]) if top and top > 0 else set(degree)
            nodes = [node_info[n] for n in keep if n in node_info]
            shown_edges = [e for e in edges
                           if e["source"] in keep and e["target"] in keep][:1200]
            return {
                "status": "ok",
                "nodes": nodes,
                "edges": shown_edges,
                "edge_types": sorted({e["type"] for e in edges}),
                "counts": {"relations": len(edges), "entities": len(node_info),
                           "shown_nodes": len(nodes), "shown_edges": len(shown_edges),
                           "node_count": kg.node_count(), "edge_count": kg.edge_count()},
            }
        except Exception as e:
            logger.exception("legal_graph_from_relations failed")
            return {"status": "error", "message": str(e)}


    @operation
    def graph_neighbors(self, node_id: int, types: list = None, wire_format: str = "json",
                        limit: int = None, cursor: str = None):
        """Return a node's neighbors from the current KG (live), for the given
        relation types (default 'cites'/'mentions'). Powers expand-on-click in the
        graph view — querying the actual graph, not a precomputed snapshot.

        ``wire_format`` is opt-in: "json" (default) returns the usual dict; "compact"
        returns a token-budgeted ``#MUNCH`` string produced by the Rust wire encoder.
        """
        denied = self._require("operator")
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "no Knowledge Graph loaded"}
        types = types or ["cites", "mentions"]
        try:
            seen: set = set()
            neighbors: list = []
            edges: list = []

            def add(nid: int) -> None:
                if nid in seen:
                    return
                seen.add(nid)
                try:
                    label = kg.get_node_label(nid)
                except Exception:
                    label = None
                try:
                    text = kg.get_node_text(nid)
                except Exception:
                    text = label or str(nid)
                kind = "entity" if (label or "").startswith("kee:") else "act"
                neighbors.append({"id": nid, "label": label or f"node-{nid}",
                                  "kind": kind, "title": (text or "")[:140]})

            for ty in types:
                try:
                    outs = kg.neighbors_by_relation(node_id, "relation_type", ty)
                except Exception:
                    outs = []
                for nid, _s in outs:
                    add(nid)
                    edges.append({"source": node_id, "target": nid, "type": ty})
                try:
                    ins = kg.predecessors_by_relation(node_id, "relation_type", ty)
                except Exception:
                    ins = []
                for pid, _s in ins:
                    add(pid)
                    edges.append({"source": pid, "target": node_id, "type": ty})
            page, meta = paginate(neighbors, limit, cursor)
            paged_ids = {n["id"] for n in page}
            edges_page = [e for e in edges
                          if e["source"] in paged_ids or e["target"] in paged_ids]
            self.session.record_node_access(node_id, "graph_neighbors")
            for nid in paged_ids:
                self.session.record_node_access(nid, "graph_neighbors")
            r = {"status": "ok", "center": node_id, "neighbors": page,
                 "edges": edges_page, **meta}
            from .encoding import encode_response
            payload, _meta = encode_response("graph_neighbors", r, requested_format=wire_format)
            return payload  # dict when json (unchanged), #MUNCH string when wire_format="compact"
        except Exception as e:
            logger.exception("graph_neighbors failed")
            return {"status": "error", "message": str(e)}


    @operation
    def get_session_context(self, sort_by: str = "recency") -> dict:
        """The MCP's working memory: recently/frequently touched nodes, recent
        queries (with negative evidence), and edits this session."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            ctx = self.session.get_context(sort_by=sort_by)
            ctx["status"] = "ok"
            return ctx
        except Exception as e:
            return {"status": "error", "message": str(e)}


    # Token estimate heuristic; matches the wire encoder's BYTES_PER_TOKEN (encoding/__init__.py).
    _CHARS_PER_TOKEN = 4


    @operation
    def get_context_bundle(self, query: str, max_results: int = 20, token_budget: int = 2000,
                           min_strength: float = 0.3, max_depth: int = 3) -> dict:
        """Assemble a token-budgeted bundle of the most relevant KG nodes for
        `query` — the Procedure-Graph 'fetch relevant KG context' step. Greedily
        packs hybrid-search hits (highest fused score first) under `token_budget`
        (~4 chars/token) and records the query in the session journal.

        Assumes the indexed (single-id) KG: each hit's ``.node`` is an int. The
        sharded graph's ``(shard, local)`` tuple ids are not supported here.
        """
        denied = self._require("operator")
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "no Knowledge Graph loaded"}
        try:
            results = kg.search_and_traverse_hybrid(
                query, max_results=max_results, min_strength=min_strength, max_depth=max_depth
            )
            self.session.record_query(query, len(results))
            bundle = []
            used = 0
            for r in results:
                text = getattr(r, "text", "") or ""
                toks = max(1, len(text) // self._CHARS_PER_TOKEN)
                if used + toks > token_budget:
                    continue
                try:
                    label = kg.get_node_label(r.node)
                except Exception:
                    label = None
                bundle.append({"node": r.node, "label": label,
                               "score": round(getattr(r, "fused_score", 0.0), 4), "text": text})
                try:
                    self.session.record_node_access(r.node, "get_context_bundle")
                except Exception:
                    pass
                used += toks
            return {"status": "ok", "query": query, "count": len(bundle),
                    "tokens_used": used, "token_budget": token_budget,
                    "tokens_remaining": max(0, token_budget - used),
                    "truncated": len(bundle) < len(results), "bundle": bundle}
        except Exception as e:
            logger.exception("get_context_bundle failed")
            return {"status": "error", "message": str(e)}


    @operation
    def get_blast_radius(self, node_id: int, relations: list = None, max_depth: int = 10,
                         limit: int = None, cursor: str = None,
                         wire_format: str = "json") -> dict:
        """Transitive dependents of a node — what would be impacted if it changes.

        ``wire_format`` is accepted for uniformity but the int-list payload has no
        compact schema, so responses are always JSON.
        """
        denied = self._require("operator")
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "no Knowledge Graph loaded"}
        try:
            impacted = kg.blast_radius(node_id, relations, max_depth)
            page, meta = paginate(impacted, limit, cursor)
            r = {"status": "ok", "center": node_id, "impacted": page, **meta}
            self.session.record_node_access(node_id, "get_blast_radius")
            for n in page[:5]:  # sample (blast_radius returns plain node ids)
                self.session.record_node_access(n if isinstance(n, int) else n.get("id", 0),
                                           "blast_impact")
            from .encoding import encode_response
            payload, _meta = encode_response("get_blast_radius", r, requested_format=wire_format)
            return payload
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def find_cycles(self, limit: int = None, cursor: str = None, wire_format: str = "json") -> dict:
        """Dependency cycles (strongly-connected components) in the current KG.

        ``wire_format`` is accepted for uniformity but the list-of-lists payload
        has no compact schema, so responses are always JSON.
        """
        denied = self._require("operator")
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "no Knowledge Graph loaded"}
        try:
            cycles = kg.find_cycles()
            page, meta = paginate(cycles, limit, cursor)
            r = {"status": "ok", "cycle_count": meta["total"], "cycles": page, **meta}
            from .encoding import encode_response
            payload, _meta = encode_response("find_cycles", r, requested_format=wire_format)
            return payload
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def find_orphans(self, entry_points: list = None, limit: int = None, cursor: str = None,
                    wire_format: str = "json") -> dict:
        """Live nodes unreachable from the given entry points (or from KG roots).

        ``wire_format`` is accepted for uniformity but the int-list payload has no
        compact schema, so responses are always JSON.
        """
        denied = self._require("operator")
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "no Knowledge Graph loaded"}
        try:
            orphans = kg.find_orphans(entry_points)
            page, meta = paginate(orphans, limit, cursor)
            r = {"status": "ok", "orphan_count": meta["total"], "orphans": page, **meta}
            from .encoding import encode_response
            payload, _meta = encode_response("find_orphans", r, requested_format=wire_format)
            return payload
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def kg_health(self, wire_format: str = "json") -> dict:
        """Structural health snapshot of the current KG (counts, cycles, orphans, top-central).

        Supports ``wire_format="compact"`` (falls back to JSON if no schema).
        """
        denied = self._require("operator")
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "no Knowledge Graph loaded"}
        try:
            h = kg.health()
            h["status"] = "ok"
            from .encoding import encode_response
            payload, _meta = encode_response("kg_health", h, requested_format=wire_format)
            return payload
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def load_ontology(self, path: str) -> dict:
        """Load an ASPICE ontology, project it into the KG, and version it."""
        denied = self._require("operator")
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "Load a Knowledge Graph first."}
        try:
            ontology = onto_loader.load_ontology_from_file(path)
            projection = self._project_ontology(ontology)
            raw = open(path, "r", encoding="utf-8").read()
            version = self._get_workspace().publish_ontology(raw, metadata={"version": ontology.version})
            self._state.ontology = ontology
            self._state.ontology_version = version.hash
            self._get_workspace().record_event("ontology_loaded", path,
                                          details={"outcomes": str(projection["outcomes_projected"])})
            return {
                "status": "ok",
                "path": path,
                "version_hash": version.hash,
                "outcomes_projected": projection["outcomes_projected"],
                "characteristics_projected": projection["characteristics_projected"],
            }
        except Exception as e:
            logger.exception("load_ontology failed")
            return {"status": "error", "message": str(e)}


    @operation
    def load_procedure_graph(self, path: str) -> dict:
        """
        Load a Procedure Graph from a .pg.yaml file using the POC flat format.
        """
        logger.info(f"Loading Procedure Graph from: {path}")
        denied = self._require("operator")
        if denied:
            return denied
        try:
            pg = pg_loader.load_from_file(path)
            self._state.pg = pg
            self._state.pg_version = None
            self._state.current_logic_id = pg.name
            try:
                self._get_workspace().record_event("pg_loaded", path, details={"name": pg.name})
            except Exception:
                logger.warning("audit record_event failed for pg_loaded", exc_info=True)

            return {
                "status": "ok",
                "path": path,
                "name": pg.name,
                "version": pg.version,
                "step_count": len(pg.steps),
                "projection": {},
                "execution_mode": "gap_check",
                "message": "Procedure Graph loaded as gap-check scope metadata"
            }
        except Exception as e:
            logger.exception("Failed to load PG")
            return {"status": "error", "message": str(e)}


    @operation
    def run_procedure(self, scope: str = "all") -> dict:
        """Compatibility entry point for a tracked, PG-scoped gap check.

        Operations, guards and dataflow in the PG are descriptive; there is no
        procedure interpreter. Only its outcome references scope this check.
        """
        denied = self._require("operator")
        if denied:
            return denied
        if not self._state.pg:
            return {"status": "error", "message": "No Procedure Graph loaded or activated."}
        result = self._run_gap_detection(scope, "production")
        if result.get("status") == "ok":
            result.update(
                message="PG-scoped gap check completed; procedure operations were not executed.",
                execution_mode="gap_check", procedure_executed=False,
                kg_node_count=self._state.kg.node_count(),
                gap_summary=[f"{g['outcome_id']}:{g['missing_characteristic']}"
                             for g in result["gaps"]])
        return result


    def _run_gap_detection(self, scope: str, kind: str, extra_summary: dict = None) -> dict:
        """Run gap detection as a tracked run (production or experiment)."""
        if scope != "all":
            return {"status": "error", "message":
                    "Only scope='all' is supported. PG outcome references select the outcomes."}
        kg = self._state.kg
        ontology = self._state.ontology
        if not kg or not ontology:
            return {"status": "error", "message": "KG and ontology must be loaded first."}
        run_id = None
        try:
            ws = self._get_workspace()
            pg = self._state.pg
            logic_id = self._state.current_logic_id
            activation = ws.current_production(logic_id) if logic_id else None
            activation_id = activation.activation_id if activation else None
            pg_version = (activation.pg_version if activation else None) or getattr(self._state, "pg_version", None)
            # Record the ontology actually inspected. An activation's ontology
            # reference is not a snapshot of the current evidence graph.
            kg_version = self._state.ontology_version
            if activation is not None:
                raw = ws.get_record(activation.pg_version)
                if raw is None:
                    raise ValueError("The production PG artifact is missing")
                pg = pg_loader.load_from_string(raw)

            run_id = f"run-{uuid.uuid4().hex[:8]}"
            ws.start_run(run_id, kind=kind, activation_id=activation_id,
                         pg_version=pg_version, kg_version=kg_version, scope=scope)

            if activation is not None:
                weights = dict(activation.frozen_weights)
            else:
                weights = config_store.load_config(self._resolve_ws_root())["weights"]
            # Record the size of the exclusion set with the run. Two runs over the
            # SAME ontology, scope and weights have reported 1,579 and 824 gaps
            # (docs/canary-noise-floor-investigation.md); `projected_nodes` only ever
            # grows within a process, and the canary baseline runs before an adoption
            # updates it while the production run runs after. If that is the cause,
            # these two numbers differ — which is not visible anywhere today.
            excluded = getattr(self._state, "projected_nodes", set())
            gaps = reasoner.detect_gaps(kg, ontology, pg, weights=weights,
                                        exclude_nodes=excluded)
            self.session.record_query(f"gaps:{scope}", len(gaps))
            explanations = {}
            hashes = []
            for gap in gaps:
                oc = ontology.outcome(gap["outcome_id"])
                exp = reasoner.explain_gap(gap, getattr(self._state, "ontology_version", None),
                                           expected=oc.description if oc else "")
                explanations[exp["target"]] = exp
                version = ws.record_explanation(json.dumps(exp), metadata={"target": exp["target"]})
                hashes.append(version.hash)
            self._state.explanations = explanations

            summary = {"scope": scope, "weights": json.dumps(weights),
                       "execution_mode": "gap_check",
                       "evidence_snapshot": "not_recorded",
                       "kg_version_kind": "ontology",
                       "excluded_nodes": str(len(excluded)),
                       "kg_nodes": str(kg.node_count() if hasattr(kg, "node_count") else -1)}
            if extra_summary:
                summary.update(extra_summary)
            ws.complete_run(run_id, status="completed", gaps_found=len(gaps),
                            explanation_hashes=hashes, summary=summary)
            return {"status": "ok", "run_id": run_id, "kind": kind, "scope": scope,
                    "gaps": gaps, "gaps_found": len(gaps),
                    "activation_id": activation_id, "execution_mode": "gap_check",
                    "procedure_executed": False, "pg_step_count": len(pg.steps) if pg else 0}
        except Exception as e:
            logger.exception("gap detection failed")
            if run_id is not None:
                try:
                    ws.complete_run(run_id, status="failed", gaps_found=0,
                                    explanation_hashes=[], summary={"error": str(e)})
                except Exception:
                    logger.exception("could not mark failed gap run")
            return {"status": "error", "message": str(e)}


    @operation
    def detect_gaps(self, scope: str = "all", wire_format: str = "json") -> dict:
        """Detect ASPICE gaps as a production run (persists run + explanations).

        Supports ``wire_format="compact"`` for token-efficient #MUNCH encoding on the gaps table.
        """
        denied = self._require("operator")
        if denied:
            return denied
        result = self._run_gap_detection(scope, "production")
        if isinstance(result, dict) and result.get("status") == "ok":
            from .encoding import encode_response
            payload, _meta = encode_response("detect_gaps", result, requested_format=wire_format)
            return payload
        return result


    @operation
    def run_experiment(self, scope: str = "all", hypothesis: str = None) -> dict:
        """Run gap detection as a quarantined experiment (full audit parity with production)."""
        denied = self._require("operator")
        if denied:
            return denied
        return self._run_gap_detection(scope, "experiment", experiment_summary(scope, hypothesis))


    @operation
    def validate_procedure_graph(self, path: str) -> dict:
        """Parse a PG file and validate it (publish-time rules; non-blocking)."""
        try:
            pg = pg_loader.load_from_file(path)
        except Exception as e:
            return {"status": "error", "message": str(e)}
        report = validate_for_publish(pg, self._state.kg, self._state.ontology,
                                      resolver=self._reference_resolver())
        return {"status": "ok", "path": path, **report}


    @operation
    def get_validation(self, version_hash: str) -> dict:
        """Return the cached validation report for a published PG version."""
        report = self._cached_validation(self._get_workspace(), version_hash)
        if report is None:
            return {"status": "error", "message": "not validated yet (or ontology changed)"}
        return {"status": "ok", "validation": report}


    @operation
    def diff_procedure_graphs(self, path_a: str, path_b: str) -> dict:
        """Parse two PG files and return their structural diff."""
        try:
            pg_a = pg_loader.load_from_file(path_a)
            pg_b = pg_loader.load_from_file(path_b)
        except Exception as e:
            return {"status": "error", "message": str(e)}

        return {"status": "ok", "diff": diff_pg(pg_a, pg_b)}


    @operation
    def get_explanation(self, target: str) -> dict:
        """Return the sectioned explanation for a gap target (e.g. 'SYS.5.BP3:acceptance_criteria_defined')."""
        exp = (self._state.explanations or {}).get(target)
        if not exp:
            return {"status": "error", "message": f"No explanation for target '{target}'. Run detect_gaps first."}
        return {"status": "ok", "explanation": exp}


    @operation
    def get_status(self) -> dict:
        """Developer/debugging tool. Returns current server state (now using GrapServerState)."""
        kg = self._state.kg
        pg = self._state.pg
        logic_id = self._state.current_logic_id
        production = None
        if logic_id and self._state.ws:
            prod = self._state.ws.current_production(logic_id)
            production = prod.activation_id if prod else None

        return {
            "kg": {
                "loaded": kg is not None,
                "node_count": kg.node_count() if kg else 0,
            },
            "pg": {
                "loaded": pg is not None,
                "name": getattr(pg, "name", None) if pg else None,
                "version": getattr(pg, "version", None) if pg else None,
                "step_count": len(pg.steps) if pg and hasattr(pg, "steps") else 0,
            },
            "lifecycle": {
                "workspace_open": self._state.ws is not None,
                "logic_id": logic_id,
                "production_id": production,
                "role": self._get_role(),
                "retention": config_store.load_config(self._resolve_ws_root()).get("retention", {}),
                "extraction_workdir": getattr(self._state, "extraction_workdir", None),
            },
            "telemetry": self._get_telemetry_snapshot()
        }


    @operation
    def publish_pg(self, level: str = "draft", rationale: str = None) -> dict:
        """Publish the currently loaded Procedure Graph; runs (non-blocking) validation."""
        denied = self._require("operator")
        if denied:
            return denied
        pg = self._state.pg
        if not pg:
            return {"status": "error", "message": "No Procedure Graph loaded."}
        try:
            ws = self._get_workspace()
            raw = open(pg.source_path, "r", encoding="utf-8").read() if pg.source_path else ""
            version = ws.publish_pg(raw, level=level, rationale=rationale,
                                    metadata={"name": pg.name, "version": pg.version})
            self._state.current_logic_id = pg.name
            self._state.pg_version = version.hash

            report = validate_for_publish(pg, self._state.kg, self._state.ontology,
                                      resolver=self._reference_resolver())
            report["ontology_version"] = getattr(self._state, "ontology_version", None)
            ws.record_validation(version.hash, json.dumps(report), report["ok"])

            return {"status": "ok", "version_hash": version.hash, "level": level,
                    "validation": {"ok": report["ok"], "error_count": len(report["errors"]),
                                   "warning_count": len(report["warnings"])}}
        except Exception as e:
            logger.exception("publish_pg failed")
            return {"status": "error", "message": str(e)}


    @operation
    def promote_pg(self, version_hash: str, rationale: str) -> dict:
        """Promote a published PG version (rationale required); refused if validation has errors."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            ws = self._get_workspace()
            report = self._cached_validation(ws, version_hash)
            if report is None:
                # Cold path: reload the PG by hash, re-validate, and cache the result.
                raw = ws.get_record(version_hash)
                if not raw:
                    return {"status": "error", "message": f"Unknown version '{version_hash}'."}
                pg = pg_loader.load_from_string(raw)
                report = validate_for_publish(pg, self._state.kg, self._state.ontology,
                                      resolver=self._reference_resolver())
                report["ontology_version"] = getattr(self._state, "ontology_version", None)
                ws.record_validation(version_hash, json.dumps(report), report["ok"])

            if report["errors"]:
                return {"status": "error", "message": "validation failed; cannot promote",
                        "validation": report}

            ws.promote(version_hash, rationale)
            return {"status": "ok", "version_hash": version_hash, "level": "promoted",
                    "validation": {"ok": True, "warning_count": len(report["warnings"])}}
        except Exception as e:
            logger.exception("promote_pg failed")
            return {"status": "error", "message": str(e)}


    @operation
    def activate(self, activation_id: str, version_hash: str, weights: dict = None,
                 set_as_production: bool = False, kg_version: str = None) -> dict:
        """Activate a promoted PG version with frozen weights."""
        denied = self._require("operator")
        if denied:
            return denied
        pg = self._state.pg
        logic_id = self._state.current_logic_id or (pg.name if pg else None)
        if not logic_id:
            return {"status": "error", "message": "No PG logic id known; load and publish a PG first."}
        try:
            ws = self._get_workspace()
            cfg = config_store.load_config(self._resolve_ws_root())
            effective_weights = weights if weights is not None else cfg["weights"]
            act = ws.activate(activation_id, logic_id, version_hash,
                              weights=effective_weights, kg_version=kg_version)
            if set_as_production:
                act = ws.set_production(activation_id)
            return {"status": "ok", "activation_id": act.activation_id,
                    "is_production": act.is_production,
                    "frozen_weights": effective_weights,
                    "weights_last_changed": cfg["last_changed"]}
        except Exception as e:
            logger.exception("activate failed")
            return {"status": "error", "message": str(e)}


    @operation
    def set_production(self, activation_id: str) -> dict:
        """Make the given activation the production one for its PG logic."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            act = self._get_workspace().set_production(activation_id)
            return {"status": "ok", "production_id": act.activation_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def get_production(self) -> dict:
        """Return the current production activation for the loaded PG logic."""
        logic_id = self._state.current_logic_id
        if not logic_id:
            return {"active": False, "message": "No PG logic loaded."}
        act = self._get_workspace().current_production(logic_id)
        if not act:
            return {"active": False, "message": f"No production activation for '{logic_id}'."}
        return {"active": True, "activation_id": act.activation_id,
                "pg_version": act.pg_version, "is_production": act.is_production}


    @operation
    def list_versions(self) -> dict:
        """List published PG versions in the workspace."""
        try:
            versions = self._get_workspace().list_versions()
            return {"versions": [{"hash": v.hash, "kind": v.kind} for v in versions]}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def get_audit_log(self, limit: int = None, cursor: str = None, wire_format: str = "json") -> dict:
        """Return the workspace audit event log.

        Supports ``wire_format="compact"`` for token-efficient #MUNCH encoding.
        """
        try:
            events = self._get_workspace().read_audit()
            page, meta = paginate(events, limit, cursor)
            r = {"events": [{"ts": e.ts, "type": e.event_type, "subject": e.subject,
                             "rationale": e.rationale} for e in page],
                 "count": len(page), **meta}
            from .encoding import encode_response
            payload, _meta = encode_response("query_events", r, requested_format=wire_format)
            return payload
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def list_runs(self, kind: str = None, limit: int = None, cursor: str = None, wire_format: str = "json") -> dict:
        """List execution runs, optionally filtered by kind (production|experiment).

        Supports ``wire_format="compact"`` for token-efficient #MUNCH encoding.
        """
        try:
            runs = self._get_workspace().list_runs(kind)
            page, meta = paginate(runs, limit, cursor)
            r = {"runs": [{"run_id": r.run_id, "kind": r.kind, "status": r.status,
                           "gaps_found": r.gaps_found} for r in page],
                 "count": len(page), **meta}
            from .encoding import encode_response
            payload, _meta = encode_response("list_runs", r, requested_format=wire_format)
            return payload
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def get_run(self, run_id: str) -> dict:
        """Return a single run record."""
        try:
            r = self._get_workspace().get_run(run_id)
            if not r:
                return {"status": "error", "message": f"Run '{run_id}' not found."}
            return {"status": "ok", "run_id": r.run_id, "kind": r.kind, "status_": r.status,
                    "gaps_found": r.gaps_found, "pg_version": r.pg_version,
                    "kg_version": r.kg_version, "explanation_hashes": r.explanation_hashes}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def reconstruct_run(self, run_id: str) -> dict:
        """Check archived artifact availability; this does not replay the evidence graph."""
        try:
            rep = self._get_workspace().reconstruct(run_id)
            return {"status": "ok", "run_id": rep.run_id, "complete": rep.complete,
                    "artifacts_available": rep.complete, "replayable": False,
                    "replay_limitations": ["Evidence graph snapshots are not recorded",
                                           "No procedure interpreter or replay engine is implemented"],
                    "pg_version_present": rep.pg_version_present,
                    "kg_version_present": rep.kg_version_present,
                    "explanations_present": rep.explanations_present,
                    "explanations_total": rep.explanations_total, "missing": rep.missing}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def query_events(self, event_type: str = None, run_id: str = None,
                     limit: int = None, cursor: str = None,
                     wire_format: str = "json") -> dict:
        """Query the Temporal Graph: by event_type, or all events for a run_id.

        Supports ``wire_format="compact"`` for token-efficient #MUNCH encoding on the events table.
        """
        try:
            ws = self._get_workspace()
            if run_id:
                raw = ws.events_for_run(run_id)
            else:
                raw = ws.query_events(event_type)
            events = [{"ts": e.ts, "type": e.event_type, "subject": e.subject,
                       "links": [{"relation": r, "kind": k, "id": i} for (r, k, i) in e.links]}
                      for e in raw]
            page, meta = paginate(events, limit, cursor)
            r = {"events": page, **meta, "count": len(page)}
            self.session.record_query(f"events:{event_type or 'all'}", len(page))
            from .encoding import encode_response
            payload, _meta = encode_response("query_events", r, requested_format=wire_format)
            return payload
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def get_quality_metrics(self) -> dict:
        """Production-only quality view; experiment runs are excluded (quarantine boundary)."""
        try:
            return {"status": "ok", **quality_metrics(self._get_workspace())}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def adopt_experiment(self, run_id: str, activation_id: str, rationale: str) -> dict:
        """Promote an experiment's PG version into production via the gated lifecycle."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            run = self._get_workspace().get_run(run_id)
        except Exception as e:
            return {"status": "error", "message": str(e)}
        if not run:
            return {"status": "error", "message": f"Run '{run_id}' not found."}
        if run.kind != "experiment":
            return {"status": "error", "message": f"Run '{run_id}' is not an experiment."}
        if not run.pg_version:
            return {"status": "error", "message": "Experiment run has no frozen PG version to adopt."}

        promo = self.promote_pg(run.pg_version, rationale)
        if promo["status"] != "ok":
            return promo  # validation gate / rationale enforcement surfaced as-is

        act = self.activate(activation_id, run.pg_version, set_as_production=True)
        if act["status"] != "ok":
            return act
        return {"status": "ok", "activation_id": activation_id,
                "version_hash": run.pg_version, "promoted": True, "is_production": True}


    @operation
    def get_config(self) -> dict:
        """Return the governed configuration (weights + retention + role + last_changed).
        Now fully integrated with GrapServerState.
        """
        root = self._resolve_ws_root()
        cfg = config_store.load_config(root)
        return {"status": "ok", "weights": cfg["weights"], "retention": cfg["retention"],
                "role": self._get_role(), "last_changed": cfg["last_changed"]}


    @operation
    def set_weight(self, name: str, value: float, rationale: str) -> dict:
        """Set a prioritization weight (rationale required; audited)."""
        denied = self._require("admin")
        if denied:
            return denied
        if not rationale:
            return {"status": "error", "message": "rationale is required"}
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return {"status": "error", "message": "value must be a number"}
        root = self._resolve_ws_root()
        cfg = config_store.load_config(root)
        cfg["weights"][name] = float(value)
        cfg["last_changed"] = int(time.time() * 1000)
        config_store.save_config(root, cfg)
        self._get_workspace().record_event("config_changed", f"weight:{name}",
                                      details={"value": str(value)}, rationale=rationale)
        return {"status": "ok", "weights": cfg["weights"], "last_changed": cfg["last_changed"]}


    @operation
    def set_retention(self, key: str, days: int, rationale: str) -> dict:
        """Set a retention-policy value in days (rationale required; audited). Enforcement via enforce_retention()."""
        denied = self._require("admin")
        if denied:
            return denied
        if not rationale:
            return {"status": "error", "message": "rationale is required"}
        if isinstance(days, bool) or not isinstance(days, int):
            return {"status": "error", "message": "days must be an integer"}
        root = self._resolve_ws_root()
        cfg = config_store.load_config(root)
        cfg["retention"][key] = int(days)
        cfg["last_changed"] = int(time.time() * 1000)
        config_store.save_config(root, cfg)
        self._get_workspace().record_event("config_changed", f"retention:{key}",
                                      details={"days": str(days)}, rationale=rationale)
        return {"status": "ok", "retention": cfg["retention"], "last_changed": cfg["last_changed"]}


    @operation
    def enforce_retention(self) -> dict:
        """Purge runs (+ orphaned explanations) older than the configured retention windows.
        Fully enforced via Workspace (uses core content hashes for integrity where applicable).
        """
        denied = self._require("admin")
        if denied:
            return denied
        try:
            ws = self._get_workspace()
            retention = config_store.load_config(self._resolve_ws_root())["retention"]
            now_ms = int(time.time() * 1000)
            day_ms = 86_400_000
            prod_cutoff = max(0, now_ms - int(retention.get("production_days", 365)) * day_ms)
            exp_cutoff = max(0, now_ms - int(retention.get("experiment_days", 30)) * day_ms)
            rep = ws.enforce_retention(prod_cutoff, exp_cutoff)
            return {"status": "ok", "runs_deleted": rep.runs_deleted,
                    "explanations_deleted": rep.explanations_deleted, "run_ids": rep.run_ids}
        except Exception as e:
            logger.exception("enforce_retention failed")
            return {"status": "error", "message": str(e)}


    @operation
    def get_role(self) -> dict:
        """Return the current role and the available roles (now persisted/governed in config)."""
        return {"status": "ok", "role": self._get_role(),
                "roles": sorted(auth.ROLES, key=lambda r: auth.ROLES[r])}


    @operation
    def set_role(self, role: str, rationale: str = None) -> dict:
        """Set the current role (requires admin + rationale; persisted + audited).

        Bootstrap: the default persisted role is 'operator', so the first admin
        must come from the environment — start the server with GRAP_ROLE=admin
        (process-scoped, never persisted) and then set_role as needed."""
        denied = self._require("admin")
        if denied:
            return denied
        if role not in auth.ROLES:
            return {"status": "error", "message": f"unknown role '{role}'"}
        if not rationale:
            return {"status": "error", "message": "rationale is required"}
        self._state.set_role(role, rationale)  # delegates to state (handles persist/audit)
        return {"status": "ok", "role": role}


    @operation
    def get_telemetry(self) -> dict:
        """Opt-in local telemetry: per-tool call counts and tokens saved by
        compact wire encoding (enable via GRAP_TELEMETRY=1)."""
        denied = self._require("operator")
        if denied:
            return denied
        try:
            from grap_mcp import telemetry as _tel
            snap = _tel.snapshot()
            snap["status"] = "ok"
            return snap
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def grap_guide(self) -> dict:
        """Orientation for agents: what grap can do, the active tool tier, and how
        to widen it. Start here, then call list_capabilities for the full taxonomy."""
        core_tools = sorted(n for n, t_ in _TIERS.items() if t_ == "core")
        full_tools = sorted(n for n, t_ in _TIERS.items() if t_ == "full")
        return {
            "status": "ok",
            "active_tier": self.active_tier,
            "overview": (
                "grap is a local graph engine with three graph kinds over one store: "
                "Knowledge (what we know), Procedure (how we do it), Temporal (what happened). "
                "Build a KG (create/load_knowledge_graph + ingest_documents), retrieve with "
                "graph_neighbors / get_context_bundle, analyse with get_blast_radius/find_cycles/"
                "kg_health, and run governed procedures (publish_pg -> promote_pg -> activate -> "
                "run_procedure / detect_gaps) with a full audit trail."
            ),
            "tiers": {
                "core": core_tools,
                "standard": "default operator tools (everything not listed core/full)",
                "full": full_tools,
            },
            "how_to_widen": "Set the GRAP_TOOL_TIER env var to core | standard | full "
                            "(default full) before starting the server to control the tool surface.",
            "next": "Call list_capabilities for the full domain taxonomy, or "
                    "suggest_queries(node_id) to explore a specific node.",
        }


    @operation
    def suggest_queries(self, node_id: int) -> dict:
        """Propose concrete next tool calls for exploring `node_id` — neighbor
        expansion, impact (blast radius), and related-context retrieval."""
        denied = self._require("operator")
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "no Knowledge Graph loaded"}
        label = None
        snippet = ""
        try:
            label = kg.get_node_label(node_id)
        except Exception:
            pass
        try:
            snippet = (kg.get_node_text(node_id) or "")[:60]
        except Exception:
            pass
        query_text = label or snippet or str(node_id)
        suggestions = [
            {"tool": "graph_neighbors", "args": {"node_id": node_id},
             "why": "Expand this node's direct neighbors (cites/mentions)."},
            {"tool": "get_blast_radius", "args": {"node_id": node_id},
             "why": "See everything that transitively depends on this node."},
            {"tool": "get_context_bundle", "args": {"query": query_text, "token_budget": 1500},
             "why": "Retrieve a budget-packed bundle of related KG context."},
        ]
        # Only suggest tools actually registered under the active tier, so a
        # narrow tier never points the agent at an unavailable tool.
        suggestions = [
            sug for sug in suggestions
            if tier_includes(_TIERS.get(sug["tool"], "standard"), self.active_tier)
        ]
        return {"status": "ok", "node": node_id, "label": label, "suggestions": suggestions}


    def _entity_index_path(self):
        if self._state.extraction_workdir:
            return str(Path(self._state.extraction_workdir) / "entities.db")
        kg_path = self._state.kg_path
        if not kg_path:
            return None
        from pathlib import Path as _P
        return str(_P(kg_path) / "entity_index.db")


    @operation
    def resolve_entity(self, alias: str) -> dict:
        """Resolve an entity name/alias to KG node id(s) via the SQLite entity index
        (exact, case-insensitive). Build it first with reindex_entities."""
        denied = self._require("operator")
        if denied:
            return denied
        path = self._entity_index_path()
        if not path:
            return {"status": "error", "message": "no Knowledge Graph path; create/load a KG first"}
        try:
            from grap_mcp.entity_index import EntityIndex
            with EntityIndex(path) as ei:
                return {"status": "ok", "alias": alias, "node_ids": ei.resolve(alias)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def register_alias(self, alias: str, node_id: int, kind: str = None) -> dict:
        """Register an alias -> node-id mapping in the entity index."""
        denied = self._require("operator") or self._require_mutable_evidence()
        if denied:
            return denied
        path = self._entity_index_path()
        if not path:
            return {"status": "error", "message": "no Knowledge Graph path; create/load a KG first"}
        try:
            from grap_mcp.entity_index import EntityIndex
            with EntityIndex(path) as ei:
                ei.register(alias, node_id, kind)
            return {"status": "ok", "alias": alias, "node_id": node_id}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def aliases_for_node(self, node_id: int) -> dict:
        """List the registered aliases for a node id."""
        denied = self._require("operator")
        if denied:
            return denied
        path = self._entity_index_path()
        if not path:
            return {"status": "error", "message": "no Knowledge Graph path; create/load a KG first"}
        try:
            from grap_mcp.entity_index import EntityIndex
            with EntityIndex(path) as ei:
                return {"status": "ok", "node_id": node_id, "aliases": ei.aliases_for(node_id)}
        except Exception as e:
            return {"status": "error", "message": str(e)}


    @operation
    def reindex_entities(self) -> dict:
        """Rebuild the entity index from the current KG: every live node's label
        becomes a resolvable alias. Returns the number indexed."""
        denied = self._require("operator") or self._require_mutable_evidence()
        if denied:
            return denied
        kg = self._state.kg
        path = self._entity_index_path()
        if not kg or not path:
            return {"status": "error", "message": "Load or create a Knowledge Graph first."}
        if not hasattr(kg, "node_id_space"):
            return {"status": "error",
                    "message": "reindex requires a KG exposing node_id_space() (IndexedKnowledgeGraph)"}
        try:
            from grap_mcp.entity_index import EntityIndex
            n = kg.node_id_space()
            rows = []
            for nid in range(n):
                try:
                    if kg.is_node_deleted(nid):
                        continue
                    label = kg.get_node_label(nid)
                except Exception:
                    continue
                if label and label.strip():
                    rows.append((label, nid, None))
            with EntityIndex(path) as ei:
                ei.clear()
                indexed = ei.register_many(rows)
            return {"status": "ok", "indexed": indexed}
        except Exception as e:
            logger.exception("reindex_entities failed")
            return {"status": "error", "message": str(e)}


    @operation
    def compact_graph(self) -> dict:
        """Reclaim disk/RAM held by dead blobs: compact the blob store (bytes orphaned
        by node updates/merges/deletes) and vacuum deleted nodes from the text index,
        then persist. Node ids, edges, and search results are unchanged."""
        denied = self._require("operator") or self._require_mutable_evidence()
        if denied:
            return denied
        kg = self._state.kg
        if not kg:
            return {"status": "error", "message": "no Knowledge Graph loaded"}
        try:
            report = kg.vacuum_blobs()
            try:
                kg.vacuum_deleted_nodes()
            except Exception:
                logger.warning("vacuum_deleted_nodes failed/unavailable", exc_info=True)
            kg_path = self._state.kg_path
            if kg_path:
                kg.save_to_dir(kg_path)
            try:
                self._get_workspace().record_event("graph_compacted", kg_path or "",
                                              details={"bytes_reclaimed": str(report.get("bytes_reclaimed", 0))})
            except Exception:
                logger.warning("audit record_event failed for graph_compacted", exc_info=True)
            return {"status": "ok", **report}
        except Exception as e:
            logger.exception("compact_graph failed")
            return {"status": "error", "message": str(e)}


    @operation
    def list_capabilities(self) -> dict:
        """Return the MCP capability taxonomy grouped by domain."""
        return {
            "domains": {
                "discovery": ["grap_guide", "suggest_queries"],
                "knowledge": ["load_knowledge_graph", "create_knowledge_graph",
                              "ingest_documents", "reingest_documents", "extract_ontology",
                              "list_proposed_ontologies", "get_proposed_ontology",
                              "adopt_proposed_ontology", "evaluate_proposal",
                              "extract_ontology_nversion",
                              "rollback_ontology", "diff_ontologies", "project_relations",
                              "open_extraction_graph", "extend_extraction_graph", "export_ontology",
                              "get_extraction_evidence",
                              "build_legal_graph", "legal_graph_from_relations",
                              "extract_legal_claims", "get_legal_claims", "get_legal_sources",
                              "export_legal_claim_graph",
                              "graph_neighbors", "load_ontology",
                              "get_blast_radius", "find_cycles", "find_orphans", "kg_health",
                              "get_context_bundle",
                              "resolve_entity", "register_alias", "aliases_for_node", "reindex_entities"],
                "pg_lifecycle": ["load_procedure_graph", "draft_pg_patch", "publish_pg", "promote_pg",
                                 "activate", "set_production", "get_production", "list_versions"],
                "execution_reasoning": ["run_procedure", "detect_gaps", "get_explanation"],
                "experimentation": ["run_experiment", "get_quality_metrics", "adopt_experiment"],
                "governance": ["get_config", "set_weight", "set_retention", "set_policy",
                               "enforce_retention", "list_pending_interventions"],
                "factory": ["run_pipeline", "get_factory_status", "run_maintenance",
                            "verify_graph"],
                "auth": ["set_role", "get_role"],
                "validation": ["validate_procedure_graph", "diff_procedure_graphs", "get_validation"],
                "audit": ["get_audit_log", "get_status", "get_telemetry", "list_capabilities",
                          "list_runs", "get_run", "diff_runs", "reconstruct_run", "query_events",
                          "get_session_context", "compact_graph"],
            },
            "active_tier": self.active_tier,
            "tier_of": dict(_TIERS),
            "tier_note": "Tools not in tier_of are 'standard'. Set GRAP_TOOL_TIER=core|standard|full.",
            "auth": "deferred (POC: no authentication/authorization)",
            "wire_note": ("JSON is the default wire format. Tools with a compact schema "
                          "(graph_neighbors, list_runs, query_events, detect_gaps, get_audit_log) "
                          "accept wire_format='compact' (or GRAP_WIRE_FORMAT=compact) for "
                          "token-efficient #MUNCH encoding; tools without a schema always return JSON."),
        }



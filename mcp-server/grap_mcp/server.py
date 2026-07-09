"""
grap-mcp-server
Minimal MCP server for the grap engine POC.

This is the starting point for making the narrow-but-vertical ASPICE gap-detection POC real.
"""

from mcp.server.fastmcp import FastMCP
import pygrap
from pathlib import Path
import logging
import os
import shutil
import uuid
import json
import time
import functools

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
from . import ingest
from .pagination import paginate
# Module-level provider/embedder factories so lifecycle tools (and tests that
# monkeypatch s.get_provider / s.get_embedder) share one indirection point.
from .kee.llm import get_provider
from .kee.embeddings import get_embedder

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("grap-mcp")

# Create the MCP server
mcp = FastMCP("Grap Engine")

from .tiers import active_tier, tier_includes

# Tool-tier policy: only core + full are listed; everything else is 'standard'.
# Single source of truth — tune freely.
_TIERS = {
    "grap_guide": "core", "suggest_queries": "core", "list_capabilities": "core",
    "get_status": "core", "create_knowledge_graph": "core", "load_knowledge_graph": "core",
    "ingest_documents": "core", "graph_neighbors": "core", "get_context_bundle": "core",
    "resolve_entity": "core",
    "run_experiment": "full", "get_quality_metrics": "full", "adopt_experiment": "full",
    "get_config": "full", "set_weight": "full", "set_retention": "full",
    "enforce_retention": "full", "get_audit_log": "full", "reconstruct_run": "full",
    "get_telemetry": "full", "build_legal_graph": "full", "legal_graph_from_relations": "full",
}
_ACTIVE_TIER = active_tier()


def tiered(func):
    """Register `func` as an MCP tool only if its tier (from _TIERS, default
    'standard') is within the active tier. Out-of-tier tools stay defined on the
    module (callable in tests) but are not advertised to clients.
    Also records to telemetry if enabled.
    """
    tool_tier = _TIERS.get(func.__name__, "standard")
    if tier_includes(tool_tier, _ACTIVE_TIER):
        @functools.wraps(func)
        def _wrapped(*args, **kwargs):
            try:
                from grap_mcp import telemetry as _tel
                _tel.record(func.__name__)
            except Exception:
                pass
            return func(*args, **kwargs)
        return mcp.tool()(_wrapped)
    return func


class GrapServerState:
    """Encapsulates server state. Goal: move away from pure global dict for robustness.
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
        self.projected_nodes = set()
        self.explanations = {}
        self.extraction_workdir = None

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
        self.explanations = {}
        self.extraction_workdir = None

    def set_kg(self, kg, path=None):
        """Set kg and path, clear related caches."""
        self.kg = kg
        self.kg_path = path
        # optionally clear projected if new kg

    def set_ontology(self, ontology, version=None):
        self.ontology = ontology
        if version:
            self.ontology_version = version

    def clear_kg(self):
        self.kg = None
        self.kg_path = None
        self.projected_nodes = set()

    def get_role(self) -> str:
        if self.role is None:
            root = self.ws_root or os.environ.get("GRAP_WORKSPACE", "./grap-workspace")
            cfg = config_store.load_config(root)
            env_role = os.environ.get("GRAP_ROLE")
            role = env_role or cfg.get("role", "operator")
            if "role" not in cfg or cfg.get("role") != role:
                cfg["role"] = role
                cfg["last_changed"] = int(time.time() * 1000)
                config_store.save_config(root, cfg)
            self.role = role
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

# Global instance. Backed by class for robustness (future multi-session support,
# cleaner reloads, less fragile global mutation).
_state = GrapServerState()

# Support dict protocol for existing test monkeypatches (setitem on "role" etc.)
# and any remaining _state["key"] accesses during transition.
_state.__getitem__ = lambda k: getattr(_state, k, None)
_state.__setitem__ = lambda k, v: setattr(_state, k, v)
_state.get = lambda k, d=None: getattr(_state, k, d)


def _get_workspace():
    """Lazily open the lifecycle workspace (env GRAP_WORKSPACE or ./grap-workspace).
    On (re)load, reset volatile state and ensure governed role is loaded.
    """
    if _state.ws is None:
        root = os.environ.get("GRAP_WORKSPACE", "./grap-workspace")
        _state.ws = pygrap.Workspace.open(root)
        _state.ws_root = root
        _state.reset()  # clear old kg/pg/ontology etc for clean session
        # Ensure role is loaded from persisted config (governed)
        _ = _get_role()
    return _state.ws


def _resolve_ws_root():
    """Return the workspace root path, opening the workspace if needed."""
    if _state.ws_root is None:
        _get_workspace()
    return _state.ws_root


def _get_role() -> str:
    """Effective current role: delegated to state (persisted/governed)."""
    return _state.get_role()


def _get_telemetry_snapshot():
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


def _require(required: str):
    """Return an error dict if the current role is below `required`, else None."""
    current = _get_role()
    if not auth.role_allows(current, required):
        return {"status": "error",
                "message": f"requires '{required}' role (current: '{current}')"}
    return None


def _cached_validation(ws, version_hash):
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
        if report.get("ontology_version") != getattr(_state, "ontology_version", None):
            return None
        return report
    except Exception:
        logger.warning("cached validation lookup failed", exc_info=True)
        return None


# =============================================================================
# P0 Capabilities (Tier 1 for the narrow vertical POC)
# =============================================================================

@tiered
def load_knowledge_graph(path: str) -> dict:
    """
    Load a Knowledge Graph (with ontology) from disk.
    For the POC this can be a simple on-disk grap store.
    """
    logger.info(f"Loading Knowledge Graph from: {path}")
    denied = _require("operator")
    if denied:
        return denied
    try:
        kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(path, 2)
        _state.set_kg(kg, path)
        try:
            _get_workspace().record_event("kg_loaded", path,
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


@tiered
def create_knowledge_graph(path: str) -> dict:
    """Create a fresh on-disk Knowledge Graph and make it the current KG."""
    denied = _require("operator")
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
        _state.set_kg(kg, path)
        _state.projected_nodes = set()
        try:
            _get_workspace().record_event("kg_created", path,
                                          details={"node_count": str(kg.node_count())})
        except Exception:
            logger.warning("audit record_event failed for kg_created", exc_info=True)
        return {"status": "ok", "path": path, "node_count": kg.node_count()}
    except Exception as e:
        logger.exception("create_knowledge_graph failed")
        return {"status": "error", "message": str(e)}


@tiered
def ingest_documents(path: str) -> dict:
    """Chunk + add .md/.txt documents under `path` into the loaded KG (real evidence)."""
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
    if not kg:
        return {"status": "error", "message": "Load or create a Knowledge Graph first."}
    try:
        result = ingest.ingest_paths(kg, path)
        kg_path = _state.kg_path
        if kg_path:
            kg.save_to_dir(kg_path)
        try:
            _get_workspace().record_event("documents_ingested", path,
                                          details={"files": str(result["files"]),
                                                   "nodes_added": str(result["nodes_added"])})
        except Exception:
            logger.warning("audit record_event failed for documents_ingested", exc_info=True)
        return {"status": "ok", **result}
    except Exception as e:
        logger.exception("ingest_documents failed")
        return {"status": "error", "message": str(e)}


@tiered
def reingest_documents(path: str) -> dict:
    """Incrementally (re)ingest .md/.txt under `path`: skip unchanged chunks,
    add new/changed ones, tombstone removed ones. Non-duplicating."""
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
    if not kg:
        return {"status": "error", "message": "Load or create a Knowledge Graph first."}
    try:
        from grap_mcp import incremental
        from pathlib import Path as _P
        kg_path = _state.kg_path
        manifest_path = str(_P(kg_path) / "ingest-manifest.json") if kg_path else None
        result = incremental.ingest_incremental(kg, path, manifest_path=manifest_path)
        if kg_path:
            kg.save_to_dir(kg_path)
        try:
            _get_workspace().record_event("documents_reingested", path,
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


@tiered
def extract_ontology(path: str, out_path: str = None, sim: float = 0.86,
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
    denied = _require("operator")
    if denied:
        return denied
    try:
        from .kee import pipeline, llm, embeddings, validate
        provider = llm.get_provider(model=model)
        embedder = embeddings.get_embedder()
        result = pipeline.extract_ontology(path, provider, embedder, sim=sim,
                                           max_files=max_files)
        onto = result["ontology"]
        try:
            from grap_mcp import session
            session.record_query(f"extract_ontology:{path}", result.get("fragments", 0))
        except Exception:
            pass
        validation = validate.validate_ontology(onto)
        yaml_text = pipeline.to_yaml(onto)
        out = out_path or str(Path(_resolve_ws_root()) / "candidate-ontology.yaml")
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(yaml_text, encoding="utf-8")
        version = _get_workspace().publish_ontology(
            yaml_text, metadata={"kind": "proposed", "source": path})
        try:
            _get_workspace().record_event(
                "ontology_proposed", path,
                details={"outcomes": str(len(onto["process_outcomes"])),
                         "provider": provider.name, "embedder": embedder.name})
        except Exception:
            logger.warning("audit record_event failed for ontology_proposed", exc_info=True)
        return {
            "status": "ok",
            "out_path": out,
            "version_hash": version.hash,
            "provider": provider.name,
            "model": getattr(provider, "model", None),
            "embedder": embedder.name,
            "chunks": result["chunks"],
            "fragments": result["fragments"],
            "process_outcomes": [
                {"id": o["id"], "name": o["name"],
                 "characteristics": o["required_characteristics"]}
                for o in onto["process_outcomes"]],
            "provenance": onto["provenance"],
            "relations": onto.get("relations", []),
            "validation": validation,
            "concept_hashes": onto.get("concept_hashes", {}),
            "note": ("LLM proposal — review before loading. To adopt: "
                     "load_ontology(out_path). Use concept_hashes for provenance."),
        }
    except Exception as e:
        logger.exception("extract_ontology failed")
        return {"status": "error", "message": str(e)}


@tiered
def list_proposed_ontologies() -> dict:
    """List versioned ontology proposals (from KEE extract) that are candidates for human review/promotion.

    Use adopt_proposed_ontology(version_hash, rationale) to adopt after review.
    These are stored via publish_ontology with kind=proposed."""
    denied = _require("operator")
    if denied:
        return denied
    try:
        ws = _get_workspace()
        # list_versions in binding is PG-focused; list and inspect via content where possible.
        # For simplicity fall back to broad audit or note that proposals are returned from extract_ontology.
        # Better: use workspace to list all ontology versions (extend if binding allows).
        versions = []
        try:
            versions = ws.list_versions() or []  # may be PG only
        except Exception:
            pass
        proposed = []
        for v in versions:
            try:
                # metadata may be on Version in some paths
                meta = getattr(v, "metadata", {}) or {}
                if isinstance(meta, dict) and meta.get("kind") == "proposed":
                    proposed.append({
                        "version_hash": getattr(v, "hash", str(v)),
                        "created_at": getattr(v, "created_at", 0),
                        "source": meta.get("source"),
                    })
            except Exception:
                continue
        # Also surface recent from audit as fallback for proposed.
        if not proposed:
            try:
                events = ws.query_events("ontology_proposed") or []
                for e in events[-10:]:
                    proposed.append({"version_hash": e.subject, "from_audit": True})
            except Exception:
                pass
        return {"status": "ok", "proposed": proposed, "count": len(proposed),
                "note": "Proposals from extract_ontology are versioned; inspect via load or audit."}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def get_proposed_ontology(version_hash: str) -> dict:
    """Fetch a proposed ontology by its version hash (content hash of the yaml).

    Returns the raw content, parsed structure, and any concept_hashes if available
    from the extraction. Useful for human review before adopt."""
    denied = _require("operator")
    if denied:
        return denied
    try:
        ws = _get_workspace()
        raw = ws.get_record(version_hash)
        if not raw:
            return {"status": "error", "message": f"Unknown version '{version_hash}'."}
        import yaml
        try:
            parsed = yaml.safe_load(raw) or {}
        except Exception:
            parsed = {}
        # Try to attach concept hashes if present in metadata or from graph
        hashes = {}
        try:
            # If it was from KEE graph, hashes may be in a side file or we note
            # For now return raw + parsed; hashes are in the extract response originally
            pass
        except Exception:
            pass
        try:
            from grap_mcp import telemetry as _tel
            _tel.record("get_proposed_ontology")
        except Exception:
            pass
        return {
            "status": "ok",
            "version_hash": version_hash,
            "raw": raw,
            "parsed": parsed,
            "note": "Use concept_hashes from original extract_ontology response or inspect via working graph.",
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def adopt_proposed_ontology(version_hash: str, rationale: str = None) -> dict:
    """Adopt a KEE-proposed ontology version (by its content hash) into the active KG.

    Fetches the versioned yaml from the workspace, projects it, and loads it as the
    active ontology (publishing a new versioned snapshot with adoption metadata).
    Requires a rationale for full audit trail. This completes the human-review loop.
    """
    denied = _require("operator")
    if denied:
        return denied
    if not rationale or not rationale.strip():
        return {"status": "error", "message": "rationale is required for adoption"}
    kg = _state.kg
    if not kg:
        return {"status": "error", "message": "Load a Knowledge Graph first."}
    try:
        ws = _get_workspace()
        raw = ws.get_record(version_hash)
        if not raw:
            return {"status": "error", "message": f"Unknown version '{version_hash}'."}
        # Write to temp to reuse loader (or parse directly); keep simple with temp.
        import tempfile, os
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as tf:
            tf.write(raw)
            tmp_path = tf.name
        try:
            ontology = onto_loader.load_ontology_from_file(tmp_path)
            projection = onto_loader.project_ontology_to_grap(ontology, kg)
            # Verify concept_hashes from proposal against current kg nodes (using core hashes).
            try:
                import yaml
                prop = yaml.safe_load(raw) or {}
                chashes = prop.get("concept_hashes", {})
                verified = 0
                for oid, h in list(chashes.items())[:5]:  # sample
                    # Note: oids in proposal may not directly map to nodes, but we can note.
                    if h and kg.node_content_hash:  # if method
                        # simplistic, would need mapping; for now just presence
                        verified += 1
                if chashes:
                    logger.info("adopt: proposal had %d concept_hashes, sampled %d", len(chashes), verified)
            except Exception:
                pass
            # Re-publish the adopted version (new hash) with adoption info.
            version = ws.publish_ontology(
                raw, metadata={"version": ontology.version, "adopted_from": version_hash, "rationale": rationale}
            )
            _state.set_ontology(ontology, version.hash)
            _state.projected_nodes.update(projection.get("projected_node_ids", []))
            ws.record_event(
                "ontology_adopted", version_hash,
                details={"new_version": version.hash, "outcomes": str(projection["outcomes_projected"])},
                rationale=rationale
            )
            try:
                from grap_mcp import telemetry as _tel
                _tel.record("adopt_proposed_ontology")
            except Exception:
                pass
            return {
                "status": "ok",
                "adopted_from": version_hash,
                "new_version_hash": version.hash,
                "outcomes_projected": projection["outcomes_projected"],
                "characteristics_projected": projection["characteristics_projected"],
                "note": "Ontology adopted and active. Review via get_status or run procedures."
            }
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
    except Exception as e:
        logger.exception("adopt_proposed_ontology failed")
        return {"status": "error", "message": str(e)}


@tiered
def open_extraction_graph(path: str) -> dict:
    """Open a durable extraction graph and load its projection as the active KG
    so the existing graph/query tools operate on it. Returns counts."""
    denied = _require("operator")
    if denied:
        return denied
    try:
        from .kee.working_graph import WorkingGraph
        wg = WorkingGraph.open(path, get_embedder())
        _state.set_kg(wg.kg, path)  # use new method
        _state.extraction_workdir = path
        out = {"status": "ok",
               "concepts": len(wg._live_concepts()),
               "edges": len(wg.relations),
               "chunks": len(wg.manifest)}
        wg.close()  # non-owned workdir: releases the sqlite handle only; wg.kg stays valid
        return out
    except Exception as e:
        logger.exception("open_extraction_graph failed")
        return {"status": "error", "message": str(e)}


@tiered
def extend_extraction_graph(path: str, docs_path: str, model: str = None,
                            sim: float = 0.86, relook_ratio: float = 0.2,
                            max_files: int = None, chunk_chars: int = 800,
                            concurrency: int = 1) -> dict:
    """Sync a durable extraction graph to `docs_path` (new/changed/removed) and
    persist it, then load its projection as the active KG. Returns sync stats.

    `chunk_chars` controls chunk size (bigger -> fewer LLM calls); changing it
    on an existing workdir re-chunks and re-extracts everything.
    `concurrency` runs that many LLM calls in parallel per window (needs a
    backend that accepts parallel requests, e.g. OLLAMA_NUM_PARALLEL)."""
    denied = _require("operator")
    if denied:
        return denied
    try:
        from .kee.graph_extract import extend_graph_ontology
        from .kee.working_graph import WorkingGraph
        out = extend_graph_ontology(path, docs_path, get_provider(model), get_embedder(),
                                    sim=sim, relook_ratio=relook_ratio, max_files=max_files,
                                    chunk_chars=chunk_chars, concurrency=concurrency)
        wg = WorkingGraph.open(path, get_embedder())
        _state.set_kg(wg.kg, path)
        _state.extraction_workdir = path
        wg.close()  # non-owned workdir: releases the sqlite handle only; wg.kg stays valid
        out["status"] = "ok"
        return {k: out[k] for k in ("status", "added", "changed", "removed",
                                    "concepts", "edges", "relooks", "parked")}
    except Exception as e:
        logger.exception("extend_extraction_graph failed")
        return {"status": "error", "message": str(e)}


@tiered
def export_ontology(path: str, out_path: str = None) -> dict:
    """Export the durable graph's candidate ontology (and optionally write YAML)."""
    denied = _require("operator")
    if denied:
        return denied
    try:
        from .kee.working_graph import WorkingGraph
        from .kee.pipeline import to_yaml
        wg = WorkingGraph.open(path, get_embedder())
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


@tiered
def project_relations(relations: list = None) -> dict:
    """Project extracted relations as typed edges in the current Knowledge Graph.

    Each relation's `from`/`to` become nodes (deduped by label within the call)
    and a typed edge (`relation_type`) connects them — turning KEE's proposed
    relations into a navigable graph alongside any existing edges. Creates a
    fresh KG in the workspace if none is loaded.
    """
    denied = _require("operator")
    if denied:
        return denied
    relations = relations or []
    try:
        kg = _state.kg
        if not kg:
            # build a dedicated relations graph (don't clobber/contend the main KG)
            res = create_knowledge_graph(str(Path(_resolve_ws_root()) / "relations-kg"))
            if res.get("status") == "error":
                return res
            kg = _state.kg

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

        kg_path = _state.kg_path
        if kg_path:
            kg.save_to_dir(kg_path)
        try:
            _get_workspace().record_event(
                "relations_projected", "kee",
                details={"edges": str(edges), "nodes": str(len(node_by_label))})
        except Exception:
            logger.warning("audit record_event failed for relations_projected", exc_info=True)
        return {"status": "ok", "nodes_added": len(node_by_label), "edges_added": edges,
                "node_count": kg.node_count(), "edge_count": kg.edge_count()}
    except Exception as e:
        logger.exception("project_relations failed")
        return {"status": "error", "message": str(e)}


@tiered
def build_legal_graph(path: str, max_files: int = 400, with_relations: bool = False,
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
    denied = _require("operator")
    if denied:
        return denied
    try:
        from .kee import legal, pipeline, llm, embeddings
        acts = legal.load_acts(path, max_files=max_files)
        if not acts:
            return {"status": "error", "message": f"no legal-act JSON found under {path}"}

        kgp = str(Path(_resolve_ws_root()) / "legal-kg")
        res = create_knowledge_graph(kgp)
        if res.get("status") == "error":
            return res
        kg = _state.kg

        node_info: dict = {}      # node_id -> {label, kind, title}
        id_to_node: dict = {}     # act id -> node_id
        for a in acts:
            label = f"act-{a['id']}"
            nid = kg.add_node(text=(a["title"] or label)[:2000], label=label)
            id_to_node[a["id"]] = nid
            node_info[nid] = {"id": nid, "label": label, "kind": "act",
                              "title": (a["title"] or label)[:140]}

        edges: list = []
        for s, t in legal.citation_pairs(acts):
            if s in id_to_node and t in id_to_node:
                kg.add_edge(id_to_node[s], id_to_node[t], 0.8, {"relation_type": "cites"})
                edges.append({"source": id_to_node[s], "target": id_to_node[t], "type": "cites"})

        rel_count = 0
        if with_relations:
            provider = llm.get_provider(model=model)
            embedder = embeddings.get_embedder()
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
        try:
            _get_workspace().record_event("legal_graph_built", path,
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


@tiered
def legal_graph_from_relations(path: str, top: int = 200) -> dict:
    """Load a relations JSON file (e.g. relations.curated.json from the batch
    runner) into a fresh KG and return it for the graph view.

    Each relation's endpoints become entity nodes (deduped) and a typed edge
    connects them; the KG is saved so expand-on-click (graph_neighbors) works.
    Returns the connected subgraph capped at the top-`top` most-connected nodes.
    """
    denied = _require("operator")
    if denied:
        return denied
    try:
        rels = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        return {"status": "error", "message": f"cannot read relations file '{path}': {e}"}
    if not isinstance(rels, list):
        return {"status": "error", "message": "expected a JSON list of {from,type,to} relations"}
    try:
        kgp = str(Path(_resolve_ws_root()) / "relations-kg")
        res = create_knowledge_graph(kgp)
        if res.get("status") == "error":
            return res
        kg = _state.kg

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
            _get_workspace().record_event("relations_graph_loaded", path,
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


@tiered
def graph_neighbors(node_id: int, types: list = None, wire_format: str = "compact",
                    limit: int = None, cursor: str = None):
    """Return a node's neighbors from the current KG (live), for the given
    relation types (default 'cites'/'mentions'). Powers expand-on-click in the
    graph view — querying the actual graph, not a precomputed snapshot.

    ``wire_format`` is opt-in: "json" (default) returns the usual dict; "compact"
    returns a token-budgeted ``#MUNCH`` string produced by the Rust wire encoder.
    """
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
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
        try:
            from grap_mcp import session
            session.record_node_access(node_id, "graph_neighbors")
            for nid in paged_ids:
                session.record_node_access(nid, "graph_neighbors")
        except Exception:
            pass
        r = {"status": "ok", "center": node_id, "neighbors": page,
             "edges": edges_page, **meta}
        from .encoding import encode_response
        payload, _meta = encode_response("graph_neighbors", r, requested_format=wire_format)
        return payload  # dict when json (unchanged), #MUNCH string when wire_format="compact"
    except Exception as e:
        logger.exception("graph_neighbors failed")
        return {"status": "error", "message": str(e)}


@tiered
def get_session_context(sort_by: str = "recency") -> dict:
    """The MCP's working memory: recently/frequently touched nodes, recent
    queries (with negative evidence), and edits this session."""
    denied = _require("operator")
    if denied:
        return denied
    try:
        from grap_mcp import session
        ctx = session.get_context(sort_by=sort_by)
        ctx["status"] = "ok"
        return ctx
    except Exception as e:
        return {"status": "error", "message": str(e)}


# Token estimate heuristic; matches the wire encoder's BYTES_PER_TOKEN (encoding/__init__.py).
_CHARS_PER_TOKEN = 4


@tiered
def get_context_bundle(query: str, max_results: int = 20, token_budget: int = 2000,
                       min_strength: float = 0.3, max_depth: int = 3) -> dict:
    """Assemble a token-budgeted bundle of the most relevant KG nodes for
    `query` — the Procedure-Graph 'fetch relevant KG context' step. Greedily
    packs hybrid-search hits (highest fused score first) under `token_budget`
    (~4 chars/token) and records the query in the session journal.

    Assumes the indexed (single-id) KG: each hit's ``.node`` is an int. The
    sharded graph's ``(shard, local)`` tuple ids are not supported here.
    """
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
    if not kg:
        return {"status": "error", "message": "no Knowledge Graph loaded"}
    try:
        from grap_mcp import session
        results = kg.search_and_traverse_hybrid(
            query, max_results=max_results, min_strength=min_strength, max_depth=max_depth
        )
        session.record_query(query, len(results))
        bundle = []
        used = 0
        for r in results:
            text = getattr(r, "text", "") or ""
            toks = max(1, len(text) // _CHARS_PER_TOKEN)
            if used + toks > token_budget:
                continue
            try:
                label = kg.get_node_label(r.node)
            except Exception:
                label = None
            bundle.append({"node": r.node, "label": label,
                           "score": round(getattr(r, "fused_score", 0.0), 4), "text": text})
            try:
                session.record_node_access(r.node, "get_context_bundle")
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


@tiered
def get_blast_radius(node_id: int, relations: list = None, max_depth: int = 10,
                     limit: int = None, cursor: str = None,
                     wire_format: str = "compact") -> dict:
    """Transitive dependents of a node — what would be impacted if it changes.

    Supports ``wire_format="compact"`` for token-efficient #MUNCH encoding.
    """
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
    if not kg:
        return {"status": "error", "message": "no Knowledge Graph loaded"}
    try:
        impacted = kg.blast_radius(node_id, relations, max_depth)
        page, meta = paginate(impacted, limit, cursor)
        r = {"status": "ok", "center": node_id, "impacted": page, **meta}
        try:
            from grap_mcp import session
            session.record_node_access(node_id, "get_blast_radius")
            for n in page[:5]:  # sample
                if isinstance(n, dict):
                    session.record_node_access(n.get("id", 0), "blast_impact")
        except Exception:
            pass
        from .encoding import encode_response
        payload, _meta = encode_response("get_blast_radius", r, requested_format=wire_format)
        return payload
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def find_cycles(limit: int = None, cursor: str = None, wire_format: str = "compact") -> dict:
    """Dependency cycles (strongly-connected components) in the current KG.

    Supports ``wire_format="compact"`` for token-efficient #MUNCH encoding.
    """
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
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


@tiered
def find_orphans(entry_points: list = None, limit: int = None, cursor: str = None,
                wire_format: str = "compact") -> dict:
    """Live nodes unreachable from the given entry points (or from KG roots).

    Supports ``wire_format="compact"`` for token-efficient #MUNCH encoding.
    """
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
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


@tiered
def kg_health(wire_format: str = "compact") -> dict:
    """Structural health snapshot of the current KG (counts, cycles, orphans, top-central).

    Supports ``wire_format="compact"`` (falls back to JSON if no schema).
    """
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
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


@tiered
def load_ontology(path: str) -> dict:
    """Load an ASPICE ontology, project it into the KG, and version it."""
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
    if not kg:
        return {"status": "error", "message": "Load a Knowledge Graph first."}
    try:
        ontology = onto_loader.load_ontology_from_file(path)
        projection = onto_loader.project_ontology_to_grap(ontology, kg)
        raw = open(path, "r", encoding="utf-8").read()
        version = _get_workspace().publish_ontology(raw, metadata={"version": ontology.version})
        _state.ontology = ontology
        _state.ontology_version = version.hash
        _state.projected_nodes.update(projection.get("projected_node_ids", []))
        _get_workspace().record_event("ontology_loaded", path,
                                      details={"outcomes": str(projection["outcomes_projected"])})
        try:
            from grap_mcp import telemetry as _tel
            _tel.record("load_ontology")
        except Exception:
            pass
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


@tiered
def load_procedure_graph(path: str) -> dict:
    """
    Load a Procedure Graph from a .pg.yaml file using the POC flat format.
    """
    logger.info(f"Loading Procedure Graph from: {path}")
    denied = _require("operator")
    if denied:
        return denied
    try:
        pg = pg_loader.load_from_file(path)
        _state.pg = pg
        _state.current_logic_id = pg.name
        try:
            _get_workspace().record_event("pg_loaded", path, details={"name": pg.name})
        except Exception:
            logger.warning("audit record_event failed for pg_loaded", exc_info=True)

        # Attempt early projection into the KG (POC)
        projection_result = {}
        kg = _state.kg
        if kg:
            projection_result = pg_loader.project_to_grap(pg, kg)
            _state.projected_nodes.update(projection_result.get("projected_node_ids", []))

        return {
            "status": "ok",
            "path": path,
            "name": pg.name,
            "version": pg.version,
            "step_count": len(pg.steps),
            "projection": projection_result,
            "message": "Procedure Graph loaded, parsed, and projected (POC)"
        }
    except Exception as e:
        logger.exception("Failed to load PG")
        return {"status": "error", "message": str(e)}


@tiered
def run_procedure(scope: str = "all") -> dict:
    """
    Execute the currently active (or loaded) Procedure Graph against the KG.
    Respects the current activation and frozen weights when present.
    """
    logger.info(f"Running procedure with scope: {scope}")
    denied = _require("operator")
    if denied:
        return denied

    activation = None
    logic_id = _state.current_logic_id
    if logic_id and _state.ws:
        activation = _state.ws.current_production(logic_id)

    pg = _state.pg
    kg = _state.kg

    if not kg:
        return {"status": "error", "message": "No Knowledge Graph loaded."}
    if not pg:
        return {"status": "error", "message": "No Procedure Graph loaded or activated."}

    try:
        node_count = kg.node_count()
        step_count = len(pg.steps) if hasattr(pg, "steps") else 0

        ws = _get_workspace()
        ontology = _state.ontology
        run_id = f"run-{uuid.uuid4().hex[:8]}"
        activation_id = activation.activation_id if activation else None
        pg_version = activation.pg_version if activation else None
        kg_version = (activation.kg_version if activation else None) or getattr(_state, "ontology_version", None)
        ws.start_run(run_id, kind="production", activation_id=activation_id,
                     pg_version=pg_version, kg_version=kg_version, scope=scope)

        gaps = (reasoner.detect_gaps(kg, ontology, pg,
                                     exclude_nodes=getattr(_state, "projected_nodes", set()))
                if ontology else [])
        ws.complete_run(run_id, status="completed", gaps_found=len(gaps),
                        explanation_hashes=[], summary={"scope": scope})

        return {
            "status": "ok",
            "message": "Procedure executed (gap detection wired)",
            "scope": scope,
            "run_id": run_id,
            "activation_id": activation_id,
            "kg_node_count": node_count,
            "pg_step_count": step_count,
            "gaps_found": len(gaps),
            "gap_summary": [f"{g['outcome_id']}:{g['missing_characteristic']}" for g in gaps],
        }
    except Exception as e:
        logger.exception("Execution failed")
        return {"status": "error", "message": str(e)}


def _run_gap_detection(scope: str, kind: str, extra_summary: dict = None) -> dict:
    """Run gap detection as a tracked run (production or experiment)."""
    kg = _state.kg
    ontology = _state.ontology
    if not kg or not ontology:
        return {"status": "error", "message": "KG and ontology must be loaded first."}
    try:
        ws = _get_workspace()
        pg = _state.pg
        logic_id = _state.current_logic_id
        activation = ws.current_production(logic_id) if logic_id else None
        activation_id = activation.activation_id if activation else None
        pg_version = (activation.pg_version if activation else None) or getattr(_state, "pg_version", None)
        kg_version = (activation.kg_version if activation else None) or getattr(_state, "ontology_version", None)

        try:
            from grap_mcp import session
            session.record_query(f"run_procedure:{scope}", 0)
        except Exception:
            pass

        run_id = f"run-{uuid.uuid4().hex[:8]}"
        ws.start_run(run_id, kind=kind, activation_id=activation_id,
                     pg_version=pg_version, kg_version=kg_version, scope=scope)

        if activation is not None:
            weights = dict(activation.frozen_weights)
        else:
            weights = config_store.load_config(_resolve_ws_root())["weights"]
        gaps = reasoner.detect_gaps(kg, ontology, pg, weights=weights,
                                    exclude_nodes=getattr(_state, "projected_nodes", set()))
        try:
            from grap_mcp import session
            session.record_query(f"gaps:{scope}", len(gaps))
        except Exception:
            pass
        explanations = {}
        hashes = []
        for gap in gaps:
            oc = ontology.outcome(gap["outcome_id"])
            exp = reasoner.explain_gap(gap, getattr(_state, "ontology_version", None),
                                       expected=oc.description if oc else "")
            explanations[exp["target"]] = exp
            version = ws.record_explanation(json.dumps(exp), metadata={"target": exp["target"]})
            hashes.append(version.hash)
        _state.explanations = explanations

        summary = {"scope": scope, "weights": json.dumps(weights)}
        if extra_summary:
            summary.update(extra_summary)
        ws.complete_run(run_id, status="completed", gaps_found=len(gaps),
                        explanation_hashes=hashes, summary=summary)
        return {"status": "ok", "run_id": run_id, "kind": kind, "scope": scope,
                "gaps": gaps, "gaps_found": len(gaps)}
    except Exception as e:
        logger.exception("gap detection failed")
        return {"status": "error", "message": str(e)}


@tiered
def detect_gaps(scope: str = "all", wire_format: str = "compact") -> dict:
    """Detect ASPICE gaps as a production run (persists run + explanations).

    Supports ``wire_format="compact"`` for token-efficient #MUNCH encoding on the gaps table.
    """
    denied = _require("operator")
    if denied:
        return denied
    result = _run_gap_detection(scope, "production")
    if isinstance(result, dict) and result.get("status") == "ok":
        from .encoding import encode_response
        payload, _meta = encode_response("detect_gaps", result, requested_format=wire_format)
        return payload
    return result


@tiered
def run_experiment(scope: str = "all", hypothesis: str = None) -> dict:
    """Run gap detection as a quarantined experiment (full audit parity with production)."""
    denied = _require("operator")
    if denied:
        return denied
    return _run_gap_detection(scope, "experiment", experiment_summary(scope, hypothesis))


@tiered
def validate_procedure_graph(path: str) -> dict:
    """Parse a PG file and validate it (publish-time rules; non-blocking)."""
    try:
        pg = pg_loader.load_from_file(path)
    except Exception as e:
        return {"status": "error", "message": str(e)}
    report = validate_for_publish(pg, _state.kg, _state.ontology)
    return {"status": "ok", "path": path, **report}


@tiered
def get_validation(version_hash: str) -> dict:
    """Return the cached validation report for a published PG version."""
    report = _cached_validation(_get_workspace(), version_hash)
    if report is None:
        return {"status": "error", "message": "not validated yet (or ontology changed)"}
    return {"status": "ok", "validation": report}


@tiered
def diff_procedure_graphs(path_a: str, path_b: str) -> dict:
    """Parse two PG files and return their structural diff."""
    try:
        pg_a = pg_loader.load_from_file(path_a)
        pg_b = pg_loader.load_from_file(path_b)
    except Exception as e:
        return {"status": "error", "message": str(e)}

    return {"status": "ok", "diff": diff_pg(pg_a, pg_b)}


@tiered
def get_explanation(target: str) -> dict:
    """Return the sectioned explanation for a gap target (e.g. 'SYS.5.BP3:acceptance_criteria_defined')."""
    exp = (_state.explanations or {}).get(target)
    if not exp:
        return {"status": "error", "message": f"No explanation for target '{target}'. Run detect_gaps first."}
    return {"status": "ok", "explanation": exp}


@tiered
def get_status() -> dict:
    """Developer/debugging tool. Returns current server state (now using GrapServerState)."""
    kg = _state.kg
    pg = _state.pg
    logic_id = _state.current_logic_id
    production = None
    if logic_id and _state.ws:
        prod = _state.ws.current_production(logic_id)
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
            "workspace_open": _state.ws is not None,
            "logic_id": logic_id,
            "production_id": production,
            "role": _get_role(),
            "retention": config_store.load_config(_resolve_ws_root()).get("retention", {}),
            "extraction_workdir": getattr(_state, "extraction_workdir", None),
        },
        "state_class": "GrapServerState (robust refactor in progress)",
        "telemetry": _get_telemetry_snapshot()
    }


@tiered
def publish_pg(level: str = "draft", rationale: str = None) -> dict:
    """Publish the currently loaded Procedure Graph; runs (non-blocking) validation."""
    denied = _require("operator")
    if denied:
        return denied
    pg = _state.pg
    if not pg:
        return {"status": "error", "message": "No Procedure Graph loaded."}
    try:
        ws = _get_workspace()
        raw = open(pg.source_path, "r", encoding="utf-8").read() if pg.source_path else ""
        version = ws.publish_pg(raw, level=level, rationale=rationale,
                                metadata={"name": pg.name, "version": pg.version})
        _state.current_logic_id = pg.name
        _state.pg_version = version.hash

        report = validate_for_publish(pg, _state.kg, _state.ontology)
        report["ontology_version"] = getattr(_state, "ontology_version", None)
        ws.record_validation(version.hash, json.dumps(report), report["ok"])

        return {"status": "ok", "version_hash": version.hash, "level": level,
                "validation": {"ok": report["ok"], "error_count": len(report["errors"]),
                               "warning_count": len(report["warnings"])}}
    except Exception as e:
        logger.exception("publish_pg failed")
        return {"status": "error", "message": str(e)}


@tiered
def promote_pg(version_hash: str, rationale: str) -> dict:
    """Promote a published PG version (rationale required); refused if validation has errors."""
    denied = _require("operator")
    if denied:
        return denied
    try:
        ws = _get_workspace()
        report = _cached_validation(ws, version_hash)
        if report is None:
            # Cold path: reload the PG by hash, re-validate, and cache the result.
            raw = ws.get_record(version_hash)
            if not raw:
                return {"status": "error", "message": f"Unknown version '{version_hash}'."}
            pg = pg_loader.load_from_string(raw)
            report = validate_for_publish(pg, _state.kg, _state.ontology)
            report["ontology_version"] = getattr(_state, "ontology_version", None)
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


@tiered
def activate(activation_id: str, version_hash: str, weights: dict = None,
             set_as_production: bool = False, kg_version: str = None) -> dict:
    """Activate a promoted PG version with frozen weights."""
    denied = _require("operator")
    if denied:
        return denied
    pg = _state.pg
    logic_id = _state.current_logic_id or (pg.name if pg else None)
    if not logic_id:
        return {"status": "error", "message": "No PG logic id known; load and publish a PG first."}
    try:
        ws = _get_workspace()
        cfg = config_store.load_config(_resolve_ws_root())
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


@tiered
def set_production(activation_id: str) -> dict:
    """Make the given activation the production one for its PG logic."""
    denied = _require("operator")
    if denied:
        return denied
    try:
        act = _get_workspace().set_production(activation_id)
        return {"status": "ok", "production_id": act.activation_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def get_production() -> dict:
    """Return the current production activation for the loaded PG logic."""
    logic_id = _state.current_logic_id
    if not logic_id:
        return {"active": False, "message": "No PG logic loaded."}
    act = _get_workspace().current_production(logic_id)
    if not act:
        return {"active": False, "message": f"No production activation for '{logic_id}'."}
    return {"active": True, "activation_id": act.activation_id,
            "pg_version": act.pg_version, "is_production": act.is_production}


@tiered
def list_versions() -> dict:
    """List published PG versions in the workspace."""
    try:
        versions = _get_workspace().list_versions()
        return {"versions": [{"hash": v.hash, "kind": v.kind} for v in versions]}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def get_audit_log(limit: int = None, cursor: str = None, wire_format: str = "compact") -> dict:
    """Return the workspace audit event log.

    Supports ``wire_format="compact"`` for token-efficient #MUNCH encoding.
    """
    try:
        events = _get_workspace().read_audit()
        page, meta = paginate(events, limit, cursor)
        r = {"events": [{"ts": e.ts, "type": e.event_type, "subject": e.subject,
                         "rationale": e.rationale} for e in page],
             "count": len(page), **meta}
        from .encoding import encode_response
        payload, _meta = encode_response("query_events", r, requested_format=wire_format)
        return payload
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def list_runs(kind: str = None, wire_format: str = "compact") -> dict:
    """List execution runs, optionally filtered by kind (production|experiment).

    Supports ``wire_format="compact"`` for token-efficient #MUNCH encoding.
    """
    try:
        runs = _get_workspace().list_runs(kind)
        r = {"runs": [{"run_id": r.run_id, "kind": r.kind, "status": r.status,
                       "gaps_found": r.gaps_found} for r in runs],
             "count": len(runs)}
        from .encoding import encode_response
        payload, _meta = encode_response("list_runs", r, requested_format=wire_format)
        return payload
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def get_run(run_id: str) -> dict:
    """Return a single run record."""
    try:
        r = _get_workspace().get_run(run_id)
        if not r:
            return {"status": "error", "message": f"Run '{run_id}' not found."}
        return {"status": "ok", "run_id": r.run_id, "kind": r.kind, "status_": r.status,
                "gaps_found": r.gaps_found, "pg_version": r.pg_version,
                "kg_version": r.kg_version, "explanation_hashes": r.explanation_hashes}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def reconstruct_run(run_id: str) -> dict:
    """Reconstruct a past run, reporting any missing frozen artifacts."""
    try:
        rep = _get_workspace().reconstruct(run_id)
        return {"status": "ok", "run_id": rep.run_id, "complete": rep.complete,
                "pg_version_present": rep.pg_version_present,
                "kg_version_present": rep.kg_version_present,
                "explanations_present": rep.explanations_present,
                "explanations_total": rep.explanations_total, "missing": rep.missing}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def query_events(event_type: str = None, run_id: str = None,
                 limit: int = None, cursor: str = None,
                 wire_format: str = "compact") -> dict:
    """Query the Temporal Graph: by event_type, or all events for a run_id.

    Supports ``wire_format="compact"`` for token-efficient #MUNCH encoding on the events table.
    """
    try:
        ws = _get_workspace()
        if run_id:
            raw = ws.events_for_run(run_id)
        else:
            raw = ws.query_events(event_type)
        events = [{"ts": e.ts, "type": e.event_type, "subject": e.subject,
                   "links": [{"relation": r, "kind": k, "id": i} for (r, k, i) in e.links]}
                  for e in raw]
        page, meta = paginate(events, limit, cursor)
        r = {"events": page, **meta, "count": len(page)}
        try:
            from grap_mcp import session
            session.record_query(f"events:{event_type or 'all'}", len(page))
        except Exception:
            pass
        from .encoding import encode_response
        payload, _meta = encode_response("query_events", r, requested_format=wire_format)
        return payload
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def get_quality_metrics() -> dict:
    """Production-only quality view; experiment runs are excluded (quarantine boundary)."""
    try:
        return {"status": "ok", **quality_metrics(_get_workspace())}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def adopt_experiment(run_id: str, activation_id: str, rationale: str) -> dict:
    """Promote an experiment's PG version into production via the gated lifecycle."""
    denied = _require("operator")
    if denied:
        return denied
    try:
        run = _get_workspace().get_run(run_id)
    except Exception as e:
        return {"status": "error", "message": str(e)}
    if not run:
        return {"status": "error", "message": f"Run '{run_id}' not found."}
    if run.kind != "experiment":
        return {"status": "error", "message": f"Run '{run_id}' is not an experiment."}
    if not run.pg_version:
        return {"status": "error", "message": "Experiment run has no frozen PG version to adopt."}

    promo = promote_pg(run.pg_version, rationale)
    if promo["status"] != "ok":
        return promo  # validation gate / rationale enforcement surfaced as-is

    act = activate(activation_id, run.pg_version, set_as_production=True)
    if act["status"] != "ok":
        return act
    return {"status": "ok", "activation_id": activation_id,
            "version_hash": run.pg_version, "promoted": True, "is_production": True}


@tiered
def get_config() -> dict:
    """Return the governed configuration (weights + retention + role + last_changed).
    Now fully integrated with GrapServerState.
    """
    root = _resolve_ws_root()
    cfg = config_store.load_config(root)
    return {"status": "ok", "weights": cfg["weights"], "retention": cfg["retention"],
            "role": _get_role(), "last_changed": cfg["last_changed"]}


@tiered
def set_weight(name: str, value: float, rationale: str) -> dict:
    """Set a prioritization weight (rationale required; audited)."""
    denied = _require("admin")
    if denied:
        return denied
    if not rationale:
        return {"status": "error", "message": "rationale is required"}
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return {"status": "error", "message": "value must be a number"}
    root = _resolve_ws_root()
    cfg = config_store.load_config(root)
    cfg["weights"][name] = float(value)
    cfg["last_changed"] = int(time.time() * 1000)
    config_store.save_config(root, cfg)
    _get_workspace().record_event("config_changed", f"weight:{name}",
                                  details={"value": str(value)}, rationale=rationale)
    return {"status": "ok", "weights": cfg["weights"], "last_changed": cfg["last_changed"]}


@tiered
def set_retention(key: str, days: int, rationale: str) -> dict:
    """Set a retention-policy value in days (rationale required; audited). Enforcement via enforce_retention()."""
    denied = _require("admin")
    if denied:
        return denied
    if not rationale:
        return {"status": "error", "message": "rationale is required"}
    if isinstance(days, bool) or not isinstance(days, int):
        return {"status": "error", "message": "days must be an integer"}
    root = _resolve_ws_root()
    cfg = config_store.load_config(root)
    cfg["retention"][key] = int(days)
    cfg["last_changed"] = int(time.time() * 1000)
    config_store.save_config(root, cfg)
    _get_workspace().record_event("config_changed", f"retention:{key}",
                                  details={"days": str(days)}, rationale=rationale)
    return {"status": "ok", "retention": cfg["retention"], "last_changed": cfg["last_changed"]}


@tiered
def enforce_retention() -> dict:
    """Purge runs (+ orphaned explanations) older than the configured retention windows.
    Fully enforced via Workspace (uses core content hashes for integrity where applicable).
    """
    denied = _require("admin")
    if denied:
        return denied
    try:
        ws = _get_workspace()
        retention = config_store.load_config(_resolve_ws_root())["retention"]
        now_ms = int(time.time() * 1000)
        day_ms = 86_400_000
        prod_cutoff = max(0, now_ms - int(retention.get("production_days", 365)) * day_ms)
        exp_cutoff = max(0, now_ms - int(retention.get("experiment_days", 30)) * day_ms)
        rep = ws.enforce_retention(prod_cutoff, exp_cutoff)
        try:
            from grap_mcp import telemetry as _tel
            _tel.record("enforce_retention")
        except Exception:
            pass
        return {"status": "ok", "runs_deleted": rep.runs_deleted,
                "explanations_deleted": rep.explanations_deleted, "run_ids": rep.run_ids}
    except Exception as e:
        logger.exception("enforce_retention failed")
        return {"status": "error", "message": str(e)}


@tiered
def get_role() -> dict:
    """Return the current role and the available roles (now persisted/governed in config)."""
    return {"status": "ok", "role": _get_role(),
            "roles": sorted(auth.ROLES, key=lambda r: auth.ROLES[r])}


@tiered
def set_role(role: str, rationale: str = None) -> dict:
    """Set the current role (requires admin + rationale; persisted + audited)."""
    denied = _require("admin")
    if denied:
        return denied
    if role not in auth.ROLES:
        return {"status": "error", "message": f"unknown role '{role}'"}
    if not rationale:
        return {"status": "error", "message": "rationale is required"}
    _state.set_role(role, rationale)  # delegates to state (handles persist/audit)
    return {"status": "ok", "role": role}


@tiered
def get_telemetry() -> dict:
    """Opt-in local telemetry: per-tool call counts and tokens saved by
    compact wire encoding (enable via GRAP_TELEMETRY=1)."""
    denied = _require("operator")
    if denied:
        return denied
    try:
        from grap_mcp import telemetry as _tel
        snap = _tel.snapshot()
        snap["status"] = "ok"
        return snap
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def grap_guide() -> dict:
    """Orientation for agents: what grap can do, the active tool tier, and how
    to widen it. Start here, then call list_capabilities for the full taxonomy."""
    core_tools = sorted(n for n, t_ in _TIERS.items() if t_ == "core")
    full_tools = sorted(n for n, t_ in _TIERS.items() if t_ == "full")
    return {
        "status": "ok",
        "active_tier": _ACTIVE_TIER,
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


@tiered
def suggest_queries(node_id: int) -> dict:
    """Propose concrete next tool calls for exploring `node_id` — neighbor
    expansion, impact (blast radius), and related-context retrieval."""
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
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
        if tier_includes(_TIERS.get(sug["tool"], "standard"), _ACTIVE_TIER)
    ]
    return {"status": "ok", "node": node_id, "label": label, "suggestions": suggestions}


def _entity_index_path():
    kg_path = _state.kg_path
    if not kg_path:
        return None
    from pathlib import Path as _P
    return str(_P(kg_path) / "entity_index.db")


@tiered
def resolve_entity(alias: str) -> dict:
    """Resolve an entity name/alias to KG node id(s) via the SQLite entity index
    (exact, case-insensitive). Build it first with reindex_entities."""
    denied = _require("operator")
    if denied:
        return denied
    path = _entity_index_path()
    if not path:
        return {"status": "error", "message": "no Knowledge Graph path; create/load a KG first"}
    try:
        from grap_mcp.entity_index import EntityIndex
        with EntityIndex(path) as ei:
            return {"status": "ok", "alias": alias, "node_ids": ei.resolve(alias)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def register_alias(alias: str, node_id: int, kind: str = None) -> dict:
    """Register an alias -> node-id mapping in the entity index."""
    denied = _require("operator")
    if denied:
        return denied
    path = _entity_index_path()
    if not path:
        return {"status": "error", "message": "no Knowledge Graph path; create/load a KG first"}
    try:
        from grap_mcp.entity_index import EntityIndex
        with EntityIndex(path) as ei:
            ei.register(alias, node_id, kind)
        return {"status": "ok", "alias": alias, "node_id": node_id}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def aliases_for_node(node_id: int) -> dict:
    """List the registered aliases for a node id."""
    denied = _require("operator")
    if denied:
        return denied
    path = _entity_index_path()
    if not path:
        return {"status": "error", "message": "no Knowledge Graph path; create/load a KG first"}
    try:
        from grap_mcp.entity_index import EntityIndex
        with EntityIndex(path) as ei:
            return {"status": "ok", "node_id": node_id, "aliases": ei.aliases_for(node_id)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@tiered
def reindex_entities() -> dict:
    """Rebuild the entity index from the current KG: every live node's label
    becomes a resolvable alias. Returns the number indexed."""
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
    path = _entity_index_path()
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


@tiered
def compact_graph() -> dict:
    """Reclaim disk/RAM held by dead blobs: compact the blob store (bytes orphaned
    by node updates/merges/deletes) and vacuum deleted nodes from the text index,
    then persist. Node ids, edges, and search results are unchanged."""
    denied = _require("operator")
    if denied:
        return denied
    kg = _state.kg
    if not kg:
        return {"status": "error", "message": "no Knowledge Graph loaded"}
    try:
        report = kg.vacuum_blobs()
        try:
            kg.vacuum_deleted_nodes()
        except Exception:
            logger.warning("vacuum_deleted_nodes failed/unavailable", exc_info=True)
        kg_path = _state.kg_path
        if kg_path:
            kg.save_to_dir(kg_path)
        try:
            _get_workspace().record_event("graph_compacted", kg_path or "",
                                          details={"bytes_reclaimed": str(report.get("bytes_reclaimed", 0))})
        except Exception:
            logger.warning("audit record_event failed for graph_compacted", exc_info=True)
        return {"status": "ok", **report}
    except Exception as e:
        logger.exception("compact_graph failed")
        return {"status": "error", "message": str(e)}


@tiered
def list_capabilities() -> dict:
    """Return the MCP capability taxonomy grouped by domain."""
    return {
        "domains": {
            "discovery": ["grap_guide", "suggest_queries"],
            "knowledge": ["load_knowledge_graph", "create_knowledge_graph",
                          "ingest_documents", "reingest_documents", "extract_ontology", "list_proposed_ontologies", "get_proposed_ontology", "adopt_proposed_ontology", "project_relations",
                          "open_extraction_graph", "extend_extraction_graph", "export_ontology",
                          "build_legal_graph", "legal_graph_from_relations",
                          "graph_neighbors", "load_ontology",
                          "get_blast_radius", "find_cycles", "find_orphans", "kg_health",
                          "get_context_bundle",
                          "resolve_entity", "register_alias", "aliases_for_node", "reindex_entities"],
            "pg_lifecycle": ["load_procedure_graph", "publish_pg", "promote_pg",
                             "activate", "set_production", "get_production", "list_versions"],
            "execution_reasoning": ["run_procedure", "detect_gaps", "get_explanation"],
            "experimentation": ["run_experiment", "get_quality_metrics", "adopt_experiment"],
            "governance": ["get_config", "set_weight", "set_retention", "enforce_retention"],
            "auth": ["set_role", "get_role"],
            "validation": ["validate_procedure_graph", "diff_procedure_graphs", "get_validation"],
            "audit": ["get_audit_log", "get_status", "get_telemetry", "list_capabilities",
                      "list_runs", "get_run", "reconstruct_run", "query_events",
                      "get_session_context", "compact_graph"],
        },
        "active_tier": _ACTIVE_TIER,
        "tier_of": dict(_TIERS),
        "tier_note": "Tools not in tier_of are 'standard'. Set GRAP_TOOL_TIER=core|standard|full.",
        "auth": "deferred (POC: no authentication/authorization)",
        "wire_note": "Wire is now DEFAULT='compact' for tools with schemas (massive agent token win). Tools: graph_neighbors, list_runs, query_events, detect_gaps, get_audit_log, get_blast_radius, find_cycles, find_orphans, kg_health etc. Override with wire_format='json' or GRAP_WIRE_FORMAT=json.",
    }


# =============================================================================
# Entry point
# =============================================================================

def main():
    """Run the MCP server."""
    logger.info("Starting grap-mcp-server (POC mode)")
    mcp.run()


if __name__ == "__main__":
    main()

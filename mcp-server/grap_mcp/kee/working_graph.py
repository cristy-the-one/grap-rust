"""Phase 1: the live working graph for graph-augmented extraction.

Wraps a real pygrap IndexedKnowledgeGraph + an EntityIndex sidecar. Holds the
graph built so far, resolves entities live (resolve-or-create), grounds
relations, emits conflict/correlation signals, and exports the candidate
ontology in the same schema as reconcile. No LLM here.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pygrap

from . import reconcile as rc
from ..entity_index import EntityIndex


@dataclass
class Context:
    """The compact known-graph slice fed back to the LLM for a chunk."""
    entities: List[str]
    characteristics: Dict[str, List[str]]
    relations: List[Tuple[str, str, str]]
    relation_vocab: List[str]


@dataclass
class Signals:
    """Graph-derived reasons to spend a re-look on this chunk."""
    conflicts: List[dict] = field(default_factory=list)
    correlations: List[dict] = field(default_factory=list)

    def warrants_relook(self) -> bool:
        return bool(self.conflicts or self.correlations)


@dataclass
class _Resolution:
    node_id: int
    created: bool
    ambiguous: bool


class WorkingGraph:
    def __init__(self, embedder, *, merge_threshold: float = 0.86,
                 context_cap: int = 8, workdir: Optional[str] = None) -> None:
        self.embedder = embedder
        self.merge_threshold = merge_threshold
        self.context_cap = context_cap
        self._dir = workdir or tempfile.mkdtemp(prefix="kee-wg-")
        self.kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(
            str(Path(self._dir) / "graph"), 2)
        self.idx = EntityIndex(str(Path(self._dir) / "entities.db"))

        self.canonical: Dict[int, str] = {}           # node -> first-seen name
        self.chars: Dict[int, List[str]] = {}          # node -> characteristics
        self.node_sources: Dict[int, Set[str]] = {}    # node -> source chunks
        self.relations: List[dict] = []                # [{from_id,to_id,type}]
        self._edge_types: Dict[Tuple[int, int], str] = {}
        self._rel_sources: Dict[Tuple[int, str, int], Set[str]] = {}
        self.rel_vocab: List[str] = []
        self.parked: List[dict] = []
        self._parent: Dict[int, int] = {}              # union-find over node ids

    # ---- union-find (component tracking for bridge signals) ----
    def _find(self, x: int) -> int:
        while self._parent.get(x, x) != x:
            self._parent[x] = self._parent.get(self._parent[x], self._parent[x])
            x = self._parent[x]
        return x

    def _union(self, a: int, b: int) -> None:
        ra, rb = self._find(a), self._find(b)
        if ra != rb:
            self._parent[max(ra, rb)] = min(ra, rb)

    def _embed_one(self, text: str) -> List[float]:
        return self.embedder.embed([text])[0]

    # ---- resolution ----
    def _resolve_outcome(self, name: str) -> _Resolution:
        hits = self.idx.resolve(name)
        if hits:
            return _Resolution(hits[0], False, len(hits) > 1)
        vec = self._embed_one(name)
        matches = self.kg.vector_search(vec, 2)
        if matches and matches[0][1] >= self.merge_threshold:
            node = matches[0][0]
            ambiguous = len(matches) > 1 and matches[1][1] >= self.merge_threshold
            self.idx.register(name, node)
            return _Resolution(node, False, ambiguous)
        node = self.kg.add_node(text=name, label="concept", embedding=vec)
        self.idx.register(name, node)
        self.canonical[node] = name
        self.chars.setdefault(node, [])
        self._parent[node] = node
        return _Resolution(node, True, False)

    def node_count(self) -> int:
        return self.kg.node_count()

    def upsert_fragment(self, frag: dict, source: str, relook: bool = False) -> Signals:
        sig = Signals()
        fragment_nodes: Dict[str, int] = {}
        for o in frag.get("outcomes", []):
            name = o.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            res = self._resolve_outcome(name.strip())
            fragment_nodes[rc.tokenize(name)] = res.node_id
            self.node_sources.setdefault(res.node_id, set()).add(source)
            chars = self.chars.setdefault(res.node_id, [])
            for ch in o.get("characteristics", []):
                if isinstance(ch, str) and ch.strip() and ch.strip() not in chars:
                    chars.append(ch.strip())
            if res.ambiguous:
                sig.conflicts.append({"kind": "ambiguous", "names": [name.strip()]})
        self._relations_pass(frag, source, fragment_nodes, sig)
        return sig

    def _resolve_endpoint(self, name: str, fragment_nodes: Dict[str, int]) -> Optional[int]:
        """Resolve a relation endpoint WITHOUT creating a node. Returns a node id
        if the name matches an existing entity (alias or embedding >= threshold)
        or an outcome named in this same fragment; otherwise None (=> park)."""
        if not isinstance(name, str) or not name.strip():
            return None
        name = name.strip()
        hits = self.idx.resolve(name)
        if hits:
            return hits[0]
        vec = self._embed_one(name)
        matches = self.kg.vector_search(vec, 1)
        if matches and matches[0][1] >= self.merge_threshold:
            self.idx.register(name, matches[0][0])
            return matches[0][0]
        return fragment_nodes.get(rc.tokenize(name))

    def _relations_pass(self, frag, source, fragment_nodes, sig):
        for r in frag.get("relations", []):
            f, t, ty = r.get("from"), r.get("to"), r.get("type")
            if not all(isinstance(x, str) and x.strip() for x in (f, t, ty)):
                continue
            f, t, ty = f.strip(), t.strip(), ty.strip()
            fn = self._resolve_endpoint(f, fragment_nodes)
            tn = self._resolve_endpoint(t, fragment_nodes)
            if fn is None or tn is None:
                self.parked.append({"from": f, "type": ty, "to": t,
                                    "source": source, "reason": "ungrounded"})
                continue
            key = (fn, tn)
            existing = self._edge_types.get(key)
            if existing is not None and existing != ty:
                self.parked.append({"from": f, "type": ty, "to": t,
                                    "source": source, "reason": "conflict"})
                sig.conflicts.append({"kind": "relation",
                                      "names": [f, t], "type": ty})
                continue
            if existing == ty:
                self._rel_sources[(fn, ty, tn)].add(source)
                continue
            bridge = self._find(fn) != self._find(tn)
            self.kg.add_edge(fn, tn, 1.0, {"rel": ty})
            self._edge_types[key] = ty
            self.relations.append({"from_id": fn, "to_id": tn, "type": ty})
            self._rel_sources[(fn, ty, tn)] = {source}
            if ty not in self.rel_vocab:
                self.rel_vocab.append(ty)
            self._union(fn, tn)
            if bridge:
                sig.correlations.append({"kind": "bridge", "names": [f, t]})

    def _canonical_label(self, node: int) -> str:
        aliases = self.idx.aliases_for(node)
        if aliases:
            return min(aliases, key=lambda s: (len(s), s))
        return self.canonical.get(node, f"concept_{node}")

    def to_candidate_ontology(self, version: str = "kee-candidate") -> dict:
        process_outcomes: List[dict] = []
        mapping_hints: List[dict] = []
        provenance: Dict[str, List[str]] = {}
        relations_out: List[dict] = []
        used: Set[str] = set()
        oid_by_node: Dict[int, str] = {}

        for node in sorted(self.canonical):
            label = self._canonical_label(node)
            raw_chars = self.chars.get(node, [])
            char_clusters = rc._dedup_strings(raw_chars, self.embedder, self.merge_threshold)
            char_tokens: List[str] = []
            seen: Set[str] = set()
            for c_can, _members in char_clusters:
                tok = rc.tokenize(c_can)
                if tok and tok not in seen:
                    seen.add(tok)
                    char_tokens.append(tok)
            base = rc.tokenize(label) or "concept"
            oid, n = base, 2
            while oid in used:
                oid = f"{base}_{n}"
                n += 1
            used.add(oid)
            oid_by_node[node] = oid
            process_outcomes.append({
                "id": oid,
                "name": label,
                "description": f"{label} (proposed by extraction; review before promotion).",
                "required_characteristics": char_tokens,
            })
            mapping_hints.append({
                "outcome": oid,
                "indicators": [{"keywords": rc.keywords(label + " " + " ".join(raw_chars))}],
            })
            provenance[oid] = sorted(self.node_sources.get(node, set()))

        for e in self.relations:
            relations_out.append({
                "from": self._canonical_label(e["from_id"]),
                "type": e["type"],
                "to": self._canonical_label(e["to_id"]),
                "sources": sorted(self._rel_sources.get(
                    (e["from_id"], e["type"], e["to_id"]), set())),
            })

        return {
            "version": version,
            "process_outcomes": process_outcomes,
            "mapping_hints": mapping_hints,
            "provenance": provenance,
            "relations": relations_out,
        }

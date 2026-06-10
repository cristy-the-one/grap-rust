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
    ambiguous: bool


class WorkingGraph:
    def __init__(self, embedder, *, merge_threshold: float = 0.86,
                 context_cap: int = 8, workdir: Optional[str] = None) -> None:
        """Build an empty working graph.

        `workdir` must be an empty or non-existent directory: this is a fresh
        per-run graph (Phase 1). State that drives context/export — `canonical`,
        `chars`, etc. — is only populated for nodes created in this process, so
        pointing `workdir` at a pre-existing graph would leave those nodes
        invisible to `context_for`/`to_candidate_ontology`. When `workdir` is
        omitted a temp dir is created and owned (removed by `close()`).
        """
        self.embedder = embedder
        self.merge_threshold = merge_threshold
        self.context_cap = context_cap
        self._owned_workdir = workdir is None
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
        self._has_edge: Set[int] = set()               # nodes with >=1 edge (for bridge)
        self.aliases_by_node: Dict[int, List[str]] = {}    # node -> [alias] (mirrors EntityIndex)
        self._char_sources: Dict[Tuple[int, str], Set[str]] = {}   # (node,text) -> chunks
        self._retracted: Set[int] = set()                  # logically removed concepts
        self._chunk_contrib: Dict[str, dict] = {}          # chunk -> {concepts,chars,edges}

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

    def _register_alias(self, alias: str, node: int) -> None:
        self.idx.register(alias, node)
        lst = self.aliases_by_node.setdefault(node, [])
        if alias not in lst:
            lst.append(alias)

    # ---- resolution ----
    def _resolve_outcome(self, name: str) -> _Resolution:
        hits = [h for h in self.idx.resolve(name) if h not in self._retracted]
        if hits:
            return _Resolution(hits[0], len(hits) > 1)
        vec = self._embed_one(name)
        matches = [(n, s) for (n, s) in self.kg.vector_search(vec, 2) if n not in self._retracted]
        if matches and matches[0][1] >= self.merge_threshold:
            node = matches[0][0]
            ambiguous = len(matches) > 1 and matches[1][1] >= self.merge_threshold
            self._register_alias(name, node)
            return _Resolution(node, ambiguous)
        node = self.kg.add_node(text=name, label="concept", embedding=vec)
        self._register_alias(name, node)
        self.canonical[node] = name
        self.chars.setdefault(node, [])
        self._parent[node] = node
        return _Resolution(node, False)

    def node_count(self) -> int:
        return self.kg.node_count()

    def close(self) -> None:
        """Release the sqlite handle and, if we created it, remove the temp dir."""
        self.idx.close()
        if self._owned_workdir:
            import shutil
            shutil.rmtree(self._dir, ignore_errors=True)

    def __enter__(self) -> "WorkingGraph":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()

    def upsert_fragment(self, frag: dict, source: str) -> Signals:
        sig = Signals()
        contrib = self._chunk_contrib.setdefault(
            source, {"concepts": set(), "chars": set(), "edges": set()})
        fragment_nodes: Dict[str, int] = {}
        for o in frag.get("outcomes", []):
            name = o.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            res = self._resolve_outcome(name.strip())
            node = res.node_id
            fragment_nodes[rc.tokenize(name)] = node
            self.node_sources.setdefault(node, set()).add(source)
            contrib["concepts"].add(node)
            chars = self.chars.setdefault(node, [])
            for ch in o.get("characteristics", []):
                if isinstance(ch, str) and ch.strip():
                    ch = ch.strip()
                    if ch not in chars:
                        chars.append(ch)
                    self._char_sources.setdefault((node, ch), set()).add(source)
                    contrib["chars"].add((node, ch))
            if res.ambiguous:
                sig.conflicts.append({"kind": "ambiguous", "names": [name.strip()]})
        self._relations_pass(frag, source, fragment_nodes, sig)
        return sig

    def _resolve_endpoint(self, name: str, fragment_nodes: Dict[str, int]) -> Optional[int]:
        if not isinstance(name, str) or not name.strip():
            return None
        name = name.strip()
        hits = [h for h in self.idx.resolve(name) if h not in self._retracted]
        if hits:
            return hits[0]
        vec = self._embed_one(name)
        matches = [(n, s) for (n, s) in self.kg.vector_search(vec, 1) if n not in self._retracted]
        if matches and matches[0][1] >= self.merge_threshold:
            self._register_alias(name, matches[0][0])
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
                self._chunk_contrib[source]["edges"].add((fn, tn))
                continue
            # A "bridge" connects two pieces of EXISTING structure: it only
            # counts when both endpoints already had an edge and sit in different
            # components. A first edge between two freshly-created nodes is not a
            # bridge (they start as disconnected singletons).
            bridge = (fn in self._has_edge and tn in self._has_edge
                      and self._find(fn) != self._find(tn))
            self.kg.add_edge(fn, tn, 1.0, {"rel": ty})
            self._edge_types[key] = ty
            self.relations.append({"from_id": fn, "to_id": tn, "type": ty})
            self._rel_sources[(fn, ty, tn)] = {source}
            self._chunk_contrib[source]["edges"].add((fn, tn))
            if ty not in self.rel_vocab:
                self.rel_vocab.append(ty)
            self._has_edge.add(fn)
            self._has_edge.add(tn)
            self._union(fn, tn)
            if bridge:
                sig.correlations.append({"kind": "bridge", "names": [f, t]})

    def retract_chunk(self, chunk_id: str) -> None:
        """Logically remove everything `chunk_id` contributed. pygrap is NOT
        mutated here (append-only during a run); retracted concepts are excluded
        from resolution/context/export and dropped at the next _project."""
        contrib = self._chunk_contrib.pop(chunk_id, None)
        for (node, text) in (contrib["chars"] if contrib else list(self._char_sources)):
            srcs = self._char_sources.get((node, text))
            if srcs is None:
                continue
            srcs.discard(chunk_id)
            if not srcs:
                self._char_sources.pop((node, text), None)
                if node in self.chars and text in self.chars[node]:
                    self.chars[node].remove(text)
        for (fn, tn) in (contrib["edges"] if contrib else [(e["from_id"], e["to_id"]) for e in list(self.relations)]):
            ty = self._edge_types.get((fn, tn))
            if ty is None:
                continue
            key = (fn, ty, tn)
            srcs = self._rel_sources.get(key)
            if srcs is not None:
                srcs.discard(chunk_id)
            if not srcs:
                self._remove_edge(fn, tn, ty)
        for node in (contrib["concepts"] if contrib else list(self.node_sources)):
            srcs = self.node_sources.get(node)
            if srcs is None:
                continue
            srcs.discard(chunk_id)
            if not srcs:
                self._retract_concept(node)
        self.parked = [p for p in self.parked if p.get("source") != chunk_id]

    def _remove_edge(self, fn: int, tn: int, ty: str) -> None:
        self._edge_types.pop((fn, tn), None)
        self._rel_sources.pop((fn, ty, tn), None)
        self.relations = [e for e in self.relations
                          if not (e["from_id"] == fn and e["to_id"] == tn)]

    def _retract_concept(self, node: int) -> None:
        self._retracted.add(node)
        self.node_sources.pop(node, None)
        self.canonical.pop(node, None)
        self.chars.pop(node, None)
        self.aliases_by_node.pop(node, None)
        self._parent.pop(node, None)
        self._has_edge.discard(node)
        self.idx.remove_node(node)
        for e in list(self.relations):
            if e["from_id"] == node or e["to_id"] == node:
                self._remove_edge(e["from_id"], e["to_id"], e["type"])
        for key in [k for k in self._char_sources if k[0] == node]:
            self._char_sources.pop(key, None)

    def _canonical_label(self, node: int) -> str:
        aliases = self.idx.aliases_for(node)
        if aliases:
            return min(aliases, key=lambda s: (len(s), s))
        return self.canonical.get(node, f"concept_{node}")

    def _context_from_nodes(self, node_ids: List[int]) -> Context:
        ids = [n for n in node_ids if n in self.canonical]
        nameset = set(ids)
        entities = [self._canonical_label(n) for n in ids]
        characteristics = {self._canonical_label(n): list(self.chars.get(n, []))
                           for n in ids}
        relations: List[Tuple[str, str, str]] = []
        for e in self.relations:
            if e["from_id"] in nameset and e["to_id"] in nameset:
                relations.append((self._canonical_label(e["from_id"]), e["type"],
                                  self._canonical_label(e["to_id"])))
        return Context(entities=entities, characteristics=characteristics,
                       relations=relations, relation_vocab=list(self.rel_vocab))

    def context_for(self, text: str) -> Context:
        if not self.canonical:
            return Context([], {}, [], [])
        vec = self._embed_one(text)
        cand: List[int] = [nid for nid, _ in self.kg.vector_search(vec, self.context_cap)
                           if nid not in self._retracted]
        low = text.lower()
        for nid, name in self.canonical.items():
            if nid in cand:
                continue
            spaced = rc.tokenize(name).replace("_", " ")
            if (name.lower() in low) or (spaced and spaced in low):
                cand.append(nid)
        return self._context_from_nodes(cand[: self.context_cap * 2])

    def focused_context(self, signals: Signals) -> Context:
        # Collect node ids for every entity name named by a signal.
        wanted: List[int] = []
        for s in list(signals.conflicts) + list(signals.correlations):
            for name in s.get("names", []):
                hits = self.idx.resolve(name)
                if hits and hits[0] not in wanted:
                    wanted.append(hits[0])
        return self._context_from_nodes(wanted)

    def to_candidate_ontology(self, version: str = "kee-candidate") -> dict:
        # rel_vocab may contain types whose edges were all retracted; recompute.
        self.rel_vocab = []
        for e in self.relations:
            if e["type"] not in self.rel_vocab:
                self.rel_vocab.append(e["type"])
        process_outcomes: List[dict] = []
        mapping_hints: List[dict] = []
        provenance: Dict[str, List[str]] = {}
        relations_out: List[dict] = []
        used: Set[str] = set()

        for node in sorted(self.canonical):
            label = self._canonical_label(node)
            raw_chars = self.chars.get(node, [])
            # Reuse reconcile's (package-private) clustering to collapse near-dup
            # characteristics, matching the legacy path's behavior exactly.
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

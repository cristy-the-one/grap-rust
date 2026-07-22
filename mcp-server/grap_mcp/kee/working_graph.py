"""Phase 1: the live working graph for graph-augmented extraction.

Wraps a real pygrap IndexedKnowledgeGraph + an EntityIndex sidecar. Holds the
graph built so far, resolves entities live (resolve-or-create), grounds
relations, emits conflict/correlation signals, and exports the candidate
ontology in the same schema as reconcile. No LLM here.
"""

from __future__ import annotations

import hashlib
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import pygrap

from . import reconcile as rc
from .graph_store import GraphStore, GraphState
from .. import session
from ..entity_index import EntityIndex

EMBED_BATCH = 128     # texts per /embeddings request; bounds payload + peak memory


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
        Path(self._dir).mkdir(parents=True, exist_ok=True)
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
        self.manifest: Dict[str, str] = {}                 # chunk_id -> content hash
        self.concept_hashes: Dict[int, str] = {}           # node -> hex content_hash from core (provenance + dedup)
        self._hash_to_node: Dict[str, int] = {}            # content hash -> node for exact dedup
        self._vec_cache: Dict[str, List[float]] = {}       # name -> embedding (see _prefetch_vectors)

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
        vec = self._vec_cache.get(text)
        if vec is None:
            vec = self.embedder.embed([text])[0]
            self._vec_cache[text] = vec
        return vec

    def _prefetch_vectors(self, names) -> None:
        """Embed every not-yet-cached name in ONE batched request.

        A single-text embed round-trip against a local Ollama costs ~2.2s, while
        the same texts sent as one batch cost ~53ms each — a 42x difference that
        is pure per-request overhead, not model time. Resolution needs a vector
        per outcome name and per relation endpoint, so batching once per fragment
        collapses dozens of sequential round-trips into one. Names that turn out
        to resolve by alias or content hash simply never consume their vector.

        Best-effort: on any embedder error we stay silent and fall back to the
        lazy per-name path in `_embed_one`, so this can only make things faster.
        """
        todo, seen = [], set()
        for name in names:
            if not isinstance(name, str):
                continue
            name = name.strip()
            if name and name not in self._vec_cache and name not in seen:
                seen.add(name)
                todo.append(name)
        if not todo:
            return
        for start in range(0, len(todo), EMBED_BATCH):
            group = todo[start:start + EMBED_BATCH]
            try:
                vecs = self.embedder.embed(group)
            except Exception:
                return
            for name, vec in zip(group, vecs):
                self._vec_cache[name] = vec

    def _content_hash(self, text: str) -> str:
        """Compute content hash using same algo as core (for dedup/provenance)."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

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

        # Highest-leverage dedup: exact content hash match (core-style sha of text)
        # before falling to vector similarity. Ensures identical concepts dedup perfectly.
        name_h = self._content_hash(name)
        if name_h in self._hash_to_node:
            node = self._hash_to_node[name_h]
            if node not in self._retracted:
                self._register_alias(name, node)
                return _Resolution(node, False)

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
        # Capture core content hash for integrity, provenance, and future dedup using hardened node hashes.
        if hasattr(self.kg, "get_node_content_hash"):
            try:
                h = self.kg.get_node_content_hash(node)
                if h:
                    self.concept_hashes[node] = h
                    self._hash_to_node[h] = node
            except Exception:
                pass
        session.record_node_access(node, "resolve_outcome")
        return _Resolution(node, False)

    def _would_resolve(self, name: str) -> bool:
        """Cheap, non-creating resolution check (alias + content hash only).

        Deliberately skips the vector path: this is used to *count* how often an
        unresolved endpoint recurs, and an approximate count is enough — the
        promotion itself goes through `_resolve_outcome`, which does the full
        alias/hash/vector resolution and only creates when all of them miss.
        """
        if not isinstance(name, str) or not name.strip():
            return False
        name = name.strip()
        if [h for h in self.idx.resolve(name) if h not in self._retracted]:
            return True
        return self._content_hash(name) in self._hash_to_node

    def promote_parked_endpoints(self, min_refs: int = 2) -> Dict[str, int]:
        """Promote recurring ungrounded relation endpoints into concepts, then
        re-attempt the relations that were parked because of them.

        Relations are parked as `ungrounded` when an endpoint never appeared in
        the model's `outcomes` list, so no concept exists to attach them to. In
        the law-ro corpus that discarded the most valuable edges in the graph —
        `<act> --emit--> GUVERNUL`, `--publicat în--> MONITORUL OFICIAL`,
        `--modificarea și--> <other act>` — 1904 parked against 357 kept.

        `min_refs` is the governance gate that replaces the outcomes list: a
        genuine entity is referenced by many chunks, while a hallucinated or
        malformed endpoint typically appears once. Raising it is stricter.
        Anything still ungrounded afterwards stays parked, so nothing is lost.
        """
        ungrounded = [p for p in self.parked if p.get("reason") == "ungrounded"]
        if not ungrounded:
            return {"candidates": 0, "promoted": 0, "relations_recovered": 0,
                    "still_parked": 0}

        counts: Dict[str, int] = {}
        for p in ungrounded:
            for name in (p.get("from"), p.get("to")):
                if isinstance(name, str) and name.strip() and not self._would_resolve(name):
                    counts[name.strip()] = counts.get(name.strip(), 0) + 1
        promote = sorted(n for n, c in counts.items() if c >= min_refs)

        self._prefetch_vectors(promote)          # one batched embed, not one per name
        before_nodes = len(self.canonical)
        sources_for: Dict[str, Set[str]] = {}
        for p in ungrounded:
            for name in (p.get("from"), p.get("to")):
                if isinstance(name, str) and name.strip() in counts:
                    sources_for.setdefault(name.strip(), set()).add(p.get("source"))
        for name in promote:
            res = self._resolve_outcome(name)
            # keep provenance: the chunks that referenced this endpoint
            for src in sources_for.get(name, ()):
                if src:
                    self.node_sources.setdefault(res.node_id, set()).add(src)
        promoted = len(self.canonical) - before_nodes

        # Re-attempt the parked relations through the normal path, so the
        # self-loop guard, conflict detection and provenance all still apply.
        # Drop them first; whatever still fails simply gets re-parked.
        self.parked = [p for p in self.parked if p.get("reason") != "ungrounded"]
        by_source: Dict[str, List[dict]] = {}
        for p in ungrounded:
            by_source.setdefault(p.get("source"), []).append(p)
        before_rels = len(self.relations)
        sig = Signals()
        for source, items in by_source.items():
            frag = {"relations": [{"from": p.get("from"), "type": p.get("type"),
                                   "to": p.get("to")} for p in items]}
            self._relations_pass(frag, source, {}, sig)
        recovered = len(self.relations) - before_rels

        return {"candidates": len(counts), "promoted": promoted,
                "relations_recovered": recovered,
                "still_parked": sum(1 for p in self.parked
                                    if p.get("reason") == "ungrounded")}

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
        # One batched embed for every name this fragment could need, instead of
        # one round-trip per name during resolution below.
        rels = frag.get("relations", []) or []
        self._prefetch_vectors(
            [o.get("name") for o in (frag.get("outcomes", []) or [])]
            + [r.get("from") for r in rels]
            + [r.get("to") for r in rels]
        )
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
                self._park(f, ty, t, source, "ungrounded")
                continue
            if fn == tn:
                # Both endpoints resolved to the same concept (the model emitted
                # "X <rel> X", or two aliases deduped together). A self-loop
                # carries no relational information and pollutes edge counts.
                self._park(f, ty, t, source, "self_loop")
                continue
            key = (fn, tn)
            existing = self._edge_types.get(key)
            if existing is not None and existing != ty:
                self._park(f, ty, t, source, "conflict")
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

    def _park(self, f: str, ty: str, t: str, source: str, reason: str) -> None:
        """Park a relation once per (from, type, to, chunk). The store's
        primary key rejects duplicates at save time — and a duplicate used to
        abort the save and lose the whole extraction (law-ro pilot cycle 5:
        a model emitting the same ungroundable relation twice in one chunk)."""
        for p in self.parked:
            if (p["from"], p["type"], p["to"], p.get("source")) == (f, ty, t, source):
                return
        self.parked.append({"from": f, "type": ty, "to": t,
                            "source": source, "reason": reason})

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
        outcome_hashes: Dict[str, str] = {}
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
            # Hash the name actually being exported: _canonical_label may pick
            # a (shorter) alias rather than the node's creation text, and the
            # adoption gate's contract is "these names are exactly what
            # extraction recorded" — verified as sha256(name). (Found by the
            # law-ro pilot: multi-alias concepts made stored node hashes stale.)
            if node in self.concept_hashes:
                outcome_hashes[oid] = hashlib.sha256(
                    label.encode("utf-8")).hexdigest()
            mapping_hints.append({
                "outcome": oid,
                "indicators": [{"keywords": rc.keywords(label + " " + " ".join(raw_chars))}],
            })
            provenance[oid] = sorted(self.node_sources.get(node, set()))

        session.record_query(f"to_candidate:{len(self.canonical)}", len(process_outcomes))

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
            "concept_hashes": outcome_hashes,
        }

    # ---- persistence (Phase 2) ----
    def _live_concepts(self) -> List[int]:
        """Live concept node ids (excludes retracted), ascending."""
        return sorted(n for n in self.canonical if n not in self._retracted)

    def to_state(self) -> GraphState:
        """Snapshot live state with COMPACTED dense concept ids (0..M-1) so a
        reload reproduces node id == concept id under the fresh projection."""
        live = self._live_concepts()
        remap = {old: new for new, old in enumerate(live)}
        st = GraphState()
        for old in live:
            new = remap[old]
            st.concepts[new] = self.canonical[old]
            st.aliases[new] = list(self.aliases_by_node.get(old, []))
            st.chars[new] = list(self.chars.get(old, []))
            st.concept_sources[new] = sorted(self.node_sources.get(old, set()))
        for (old, text), srcs in self._char_sources.items():
            if old in remap:
                st.char_sources[(remap[old], text)] = sorted(srcs)
        for e in self.relations:
            f, t = e["from_id"], e["to_id"]
            if f in remap and t in remap:
                st.edges.append((remap[f], remap[t], e["type"]))
                srcs = self._rel_sources.get((f, e["type"], t), set())
                st.edge_sources[(remap[f], remap[t])] = sorted(srcs)
        st.manifest = dict(self.manifest)
        st.concept_hashes = {remap.get(old, old): h for old, h in self.concept_hashes.items() if old in remap}
        st.rel_vocab = list(dict.fromkeys(e["type"] for e in self.relations))
        st.parked = [{"from": p["from"], "type": p["type"], "to": p["to"],
                      "chunk_id": p.get("source", ""), "reason": p.get("reason", "")}
                     for p in self.parked]
        return st

    def _load_state(self, st: GraphState) -> None:
        """Populate in-memory dicts from a (dense-id) GraphState, then project."""
        self.canonical = dict(st.concepts)
        self.aliases_by_node = {cid: list(al) for cid, al in st.aliases.items()}
        self.chars = {cid: list(cs) for cid, cs in st.chars.items()}
        self.node_sources = {cid: set(s) for cid, s in st.concept_sources.items()}
        self._char_sources = {k: set(v) for k, v in st.char_sources.items()}
        self.relations = [{"from_id": f, "to_id": t, "type": ty} for (f, t, ty) in st.edges]
        self._edge_types = {(f, t): ty for (f, t, ty) in st.edges}
        self._rel_sources = {(f, ty, t): set(st.edge_sources.get((f, t), []))
                             for (f, t, ty) in st.edges}
        self.rel_vocab = list(st.rel_vocab)
        self.parked = [{"from": p["from"], "type": p["type"], "to": p["to"],
                        "source": p["chunk_id"], "reason": p["reason"]} for p in st.parked]
        self.manifest = dict(st.manifest)
        self.concept_hashes = dict(st.concept_hashes)
        self._hash_to_node = {h: nid for nid, h in self.concept_hashes.items()}
        self._retracted = set()
        self._chunk_contrib = {}
        self._parent = {}
        self._has_edge = set()
        for f, t, _ty in st.edges:
            self._parent.setdefault(f, f)
            self._parent.setdefault(t, t)
            self._has_edge.add(f)
            self._has_edge.add(t)
            self._union(f, t)
        for cid in st.concepts:
            self._parent.setdefault(cid, cid)
        self._project()

    def _project(self) -> None:
        """Rebuild the pygrap KG + EntityIndex from live state. Node ids come out
        equal to the (already-dense) concept ids because we add in id order."""
        import shutil
        graph_dir = str(Path(self._dir) / "graph")
        # Release the current graph handle before deleting its directory: on
        # Windows an open pygrap handle would make rmtree silently skip files.
        self.kg = None
        shutil.rmtree(graph_dir, ignore_errors=True)
        self.idx.clear()
        self.kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(graph_dir, 2)
        # Every concept below needs its vector. Embedding them one at a time cost
        # ~2.2s each (≈1h for 1671 concepts), which is what made save() — and
        # therefore any checkpoint — prohibitively expensive. Batch them first;
        # names resolved earlier this run are already cached and cost nothing.
        self._prefetch_vectors([self.canonical[cid] for cid in sorted(self.canonical)])
        for cid in sorted(self.canonical):
            nid = self.kg.add_node(text=self.canonical[cid], label="concept",
                                   embedding=self._embed_one(self.canonical[cid]))
            assert nid == cid, "projection must reproduce dense concept ids"
            if hasattr(self.kg, "get_node_content_hash"):
                try:
                    h = self.kg.get_node_content_hash(nid)
                    if h:
                        self.concept_hashes[nid] = h
                        self._hash_to_node[h] = nid
                except Exception:
                    pass
            for alias in self.aliases_by_node.get(cid, []):
                self._register_alias(alias, cid)
        for e in self.relations:
            self.kg.add_edge(e["from_id"], e["to_id"], 1.0, {"rel": e["type"]})

    def save(self) -> None:
        """Persist truth FIRST (GraphStore), then rebuild + persist the projection."""
        st = self.to_state()
        with GraphStore(str(Path(self._dir) / "graph_store.db")) as store:
            store.save(st)
        self._load_state(st)
        self.kg.save_to_dir(str(Path(self._dir) / "graph"))

    @classmethod
    def open(cls, workdir: str, embedder, *, merge_threshold: float = 0.86,
             context_cap: int = 8) -> "WorkingGraph":
        """Open an existing durable graph (or a fresh one if absent)."""
        wg = cls(embedder, merge_threshold=merge_threshold,
                 context_cap=context_cap, workdir=workdir)
        store_path = Path(workdir) / "graph_store.db"
        with GraphStore(str(store_path)) as store:
            st = store.load()
        if st.concepts:
            wg._load_state(st)
        return wg

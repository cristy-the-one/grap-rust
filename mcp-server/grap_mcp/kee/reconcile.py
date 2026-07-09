"""Phase 4: reconcile candidate fragments into a deduped candidate ontology.

Near-duplicate concepts ("test plan" / "test plan document") are collapsed by
embedding similarity so the proposed ontology does not proliferate synonyms.

Core clustering is pure-numpy and deterministic (used by the pipeline). A
separate `stress_merge_nodes` materializes concepts as real pygrap nodes and
drives `vector_search` + `merge_nodes` directly — a deliberate exercise of the
grap-core paths most likely to harbour latent bugs.
"""

from __future__ import annotations

import hashlib
import math
import re
import tempfile
from typing import Any, Dict, List, Sequence, Tuple

_STOP = {"the", "and", "for", "with", "from", "a", "an", "of", "to", "in",
         "on", "by", "or", "as", "is", "are", "be"}


def tokenize(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def _content_hash(text: str) -> str:
    """Core-style content hash for exact dedup."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def keywords(text: str, cap: int = 12) -> List[str]:
    out: List[str] = []
    seen = set()
    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) > 3 and w not in _STOP and w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= cap:
            break
    return out


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def greedy_cluster(vectors: Sequence[Sequence[float]], threshold: float) -> List[List[int]]:
    """Deterministic single-pass clustering: each item joins the first existing
    cluster whose representative it is >= threshold similar to, else starts a new
    one. Returns clusters as lists of input indices (first member is the rep)."""
    clusters: List[List[int]] = []
    reps: List[Sequence[float]] = []
    for i, v in enumerate(vectors):
        placed = False
        for c, rep in enumerate(reps):
            if cosine(v, rep) >= threshold:
                clusters[c].append(i)
                placed = True
                break
        if not placed:
            clusters.append([i])
            reps.append(v)
    return clusters


def _dedup_strings(strings: List[str], embedder, threshold: float) -> List[Tuple[str, List[str]]]:
    """Cluster strings; return [(canonical, [members...])] preserving first-seen order.
    Canonical = the shortest member (usually the cleanest label)."""
    if not strings:
        return []
    vecs = embedder.embed(strings)
    clusters = greedy_cluster(vecs, threshold)
    out: List[Tuple[str, List[str]]] = []
    for cl in clusters:
        members = [strings[i] for i in cl]
        canonical = min(members, key=lambda s: (len(s), s))
        out.append((canonical, members))
    return out


def reconcile(fragments: Sequence[Dict[str, Any]], embedder, sim: float = 0.86,
              version: str = "kee-candidate") -> Dict[str, Any]:
    """Collapse fragment outcomes/characteristics into a candidate ontology dict
    in the tool's schema, with provenance."""
    # gather outcomes preserving first-seen order
    names: List[str] = []
    chars_by_name: Dict[str, List[str]] = {}
    sources_by_name: Dict[str, List[str]] = {}
    for frag in fragments:
        src = frag.get("source", "?")
        for o in frag.get("outcomes", []):
            name = o["name"]
            if name not in chars_by_name:
                names.append(name)
                chars_by_name[name] = []
                sources_by_name[name] = []
            chars_by_name[name].extend(o.get("characteristics", []))
            sources_by_name[name].append(src)

    # Highest-leverage: first pass exact dedup by content hash (core sha256 of name)
    # before embedding sim. Guarantees identical strings collapse perfectly using
    # the same hash primitive as the core graph nodes.
    hash_groups: Dict[str, List[str]] = {}
    for n in names:
        h = _content_hash(n)
        hash_groups.setdefault(h, []).append(n)
    deduped_names = []
    for members in hash_groups.values():
        # keep first seen within hash group (should be same anyway)
        deduped_names.append(members[0] if members else "")
    names = [n for n in deduped_names if n]  # refresh for clustering

    clusters = _dedup_strings(names, embedder, sim)

    process_outcomes: List[dict] = []
    mapping_hints: List[dict] = []
    provenance: Dict[str, List[str]] = {}
    used_ids: set = set()
    for canonical, members in clusters:
        # merge characteristics across the clustered outcome names, then dedup them
        raw_chars: List[str] = []
        sources: List[str] = []
        for m in members:
            raw_chars.extend(chars_by_name.get(m, []))
            sources.extend(sources_by_name.get(m, []))
        char_clusters = _dedup_strings(raw_chars, embedder, sim)
        char_tokens: List[str] = []
        seen_tok = set()
        for c_canonical, _members in char_clusters:
            tok = tokenize(c_canonical)
            if tok and tok not in seen_tok:
                seen_tok.add(tok)
                char_tokens.append(tok)

        base_id = tokenize(canonical) or "concept"
        oid = base_id
        n = 2
        while oid in used_ids:
            oid = f"{base_id}_{n}"
            n += 1
        used_ids.add(oid)

        process_outcomes.append({
            "id": oid,
            "name": canonical,
            "description": f"{canonical} (proposed by extraction; review before promotion).",
            "required_characteristics": char_tokens,
        })
        mapping_hints.append({
            "outcome": oid,
            "indicators": [{"keywords": keywords(canonical + " " + " ".join(raw_chars))}],
        })
        provenance[oid] = sorted(set(sources))

    # surface proposed relations (deduped, with provenance). Kept separate from
    # the loadable ontology schema — they enrich the knowledge graph view.
    relations: List[dict] = []
    rel_index: Dict[tuple, dict] = {}
    for frag in fragments:
        src = frag.get("source", "?")
        for r in frag.get("relations", []):
            f, t, ty = r.get("from"), r.get("to"), r.get("type")
            if not all(isinstance(x, str) and x.strip() for x in (f, t, ty)):
                continue
            key = (f.strip().lower(), ty.strip().lower(), t.strip().lower())
            if key not in rel_index:
                rel = {"from": f.strip(), "type": ty.strip(), "to": t.strip(),
                       "sources": []}
                rel_index[key] = rel
                relations.append(rel)
            if src not in rel_index[key]["sources"]:
                rel_index[key]["sources"].append(src)

    return {
        "version": version,
        "process_outcomes": process_outcomes,
        "mapping_hints": mapping_hints,
        "provenance": provenance,
        "relations": relations,
    }


def stress_merge_nodes(strings: Sequence[str], embedder, threshold: float = 0.9) -> Dict[str, Any]:
    """Materialize `strings` as pygrap nodes with embeddings, find near-duplicate
    pairs via vector_search, and collapse them with merge_nodes.

    This is a deliberate exercise of the grap-core vector + merge paths. Returns
    counts so callers/tests can assert consistency (final == initial - merged).
    """
    import pygrap

    tmp = tempfile.mkdtemp(prefix="kee-merge-")
    kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(tmp, 2)
    vecs = embedder.embed(list(strings))
    ids = [kg.add_node(text=s, label=f"concept:{i}", embedding=vecs[i])
           for i, s in enumerate(strings)]
    initial = kg.node_count()

    # union-find over near-duplicate pairs (computed before any merge mutates ids)
    parent = list(range(len(ids)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    for i in range(len(ids)):
        for nid, score in kg.vector_search(vecs[i], len(ids)):
            j = nid  # add_node returns sequential ids 0..n-1, so node id == index
            if j > i and score >= threshold:
                union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(len(ids)):
        groups.setdefault(find(i), []).append(i)

    merged = 0
    for root, members in groups.items():
        keep = ids[members[0]]
        for m in members[1:]:
            kg.merge_nodes(keep, ids[m])
            merged += 1

    final = kg.node_count()
    return {"initial": initial, "final": final, "merged": merged,
            "clusters": len(groups)}

"""Persistent concept registry for cross-run entity dedup.

A canonical-concept store backed by a pygrap KG: each canonical concept is a
node carrying its embedding. Resolving a concept is a vector_search top-1 — if
the nearest canonical is similar enough it is reused, otherwise a new canonical
is added. Because the KG persists to disk, dedup carries across runs: re-running
extraction (or running it on more data) merges into the same canonical set
instead of proliferating duplicates. This also dogfoods grap's vector index.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence


class ConceptRegistry:
    def __init__(self, kg_dir: str, embedder, threshold: float = 0.86) -> None:
        import pygrap
        self.dir = str(kg_dir)
        Path(self.dir).parent.mkdir(parents=True, exist_ok=True)
        # with_merge_policy loads an existing graph at this dir if present, so a
        # registry built by a previous run is reused (cross-run dedup).
        self.kg = pygrap.IndexedKnowledgeGraph.with_merge_policy(self.dir, 2)
        self.embedder = embedder
        self.threshold = threshold

    def stats(self) -> Dict[str, int]:
        return {"concepts": self.kg.node_count()}

    def resolve_many(self, names: Sequence[str], batch: int = 256,
                     on_progress: Optional[Callable[[int, int], None]] = None
                     ) -> Dict[str, dict]:
        """Map each input name to a canonical concept.

        Returns {name: {"id": node_id, "name": canonical_name, "new": bool}}.
        Embeds in batches; resolves each against the live index (so duplicates
        within this call also collapse, as earlier additions are searchable)."""
        uniq: List[str] = []
        seen = set()
        for n in names:
            s = (n or "").strip()
            if s and s not in seen:
                seen.add(s)
                uniq.append(s)

        result: Dict[str, dict] = {}
        done = 0
        for start in range(0, len(uniq), batch):
            slice_names = uniq[start:start + batch]
            vecs = self.embedder.embed(slice_names)
            for name, vec in zip(slice_names, vecs):
                hits = self.kg.vector_search(vec, 1)
                if hits and hits[0][1] >= self.threshold:
                    nid = hits[0][0]
                    try:
                        cname = self.kg.get_node_text(nid)
                    except Exception:
                        cname = name
                    result[name] = {"id": nid, "name": cname, "new": False}
                else:
                    nid = self.kg.add_node(text=name, label="concept", embedding=vec)
                    result[name] = {"id": nid, "name": name, "new": True}
            done += len(slice_names)
            if on_progress:
                on_progress(done, len(uniq))
        return result

    def save(self) -> None:
        self.kg.save_to_dir(self.dir)

"""Phase 3: LLM-driven information extraction.

Reads text chunks and asks the model to propose ontology fragments constrained
to our schema. The model only ever sees text and returns JSON; it never touches
grap internals. Output is treated as *proposals* — parsed defensively, malformed
items dropped, every fragment tagged with its source chunk for provenance.

Fragment shape:
  {"source": str,
   "outcomes":   [{"name": str, "characteristics": [str, ...]}],
   "relations":  [{"from": str, "type": str, "to": str}]}
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger("grap-mcp.kee")

SYSTEM_PROMPT = (
    "You are an information-extraction system that builds a process/quality "
    "ontology from documents. Read the text and identify the assessable "
    "OUTCOMES it describes (capabilities, requirements, or practices), and for "
    "each outcome the CHARACTERISTICS (concrete attributes or work products) "
    "that must be present as evidence. Also capture simple RELATIONS between "
    "named concepts.\n\n"
    "Respond with ONLY a single JSON object, no prose, in exactly this shape:\n"
    '{"outcomes":[{"name":"<short name>","characteristics":["<attr>", ...]}],'
    '"relations":[{"from":"<concept>","type":"<verb>","to":"<concept>"}]}\n'
    "Use empty arrays when nothing applies. Do not invent content not supported "
    "by the text."
)


def build_user_prompt(text: str) -> str:
    return f"Extract the ontology fragment from the following text:\n\n{text}"


CONTEXT_INSTRUCTION = (
    "You also have CONTEXT from a knowledge graph built from earlier text. "
    "Reuse the exact canonical names below when the same concept appears here, "
    "instead of inventing new names. Only assert relations grounded in THIS text."
)


def build_user_prompt_with_context(text: str, context) -> str:
    """Plain user prompt, optionally prefixed with a compact KNOWN-graph block.

    `context` is a kee.working_graph.Context. When it carries no entities the
    prompt is identical to `build_user_prompt(text)` (no empty block, so early
    chunks behave exactly like the stateless path)."""
    if not getattr(context, "entities", None):
        return build_user_prompt(text)

    lines = [CONTEXT_INSTRUCTION, "", "KNOWN ENTITIES:"]
    for name in context.entities:
        chars = context.characteristics.get(name) or []
        if chars:
            lines.append(f"- {name} (characteristics: {', '.join(chars)})")
        else:
            lines.append(f"- {name}")
    if context.relations:
        lines.append("")
        lines.append("KNOWN RELATIONS:")
        for f, ty, t in context.relations:
            lines.append(f"- {f} {ty} {t}")
    if context.relation_vocab:
        lines.append("")
        lines.append("RELATION VOCABULARY (reuse where apt): "
                     + ", ".join(context.relation_vocab))
    lines.append("")
    lines.append(build_user_prompt(text))
    return "\n".join(lines)


def _strip_fences(s: str) -> str:
    s = s.strip()
    if s.startswith("```"):
        # drop the opening fence line (``` or ```json) and any closing fence
        s = s.split("\n", 1)[1] if "\n" in s else ""
        if s.rstrip().endswith("```"):
            s = s.rstrip()[:-3]
    return s


def _first_json_object(s: str) -> Optional[str]:
    """Return the first balanced {...} substring, ignoring braces inside strings."""
    start = s.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return s[start:i + 1]
    return None


def extract_json(raw: str) -> Optional[Dict[str, Any]]:
    """Best-effort parse of a JSON object from a model response."""
    candidate = _strip_fences(raw)
    for text in (candidate, _first_json_object(candidate) or ""):
        if not text:
            continue
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj
        except (ValueError, TypeError):
            continue
    return None


def _clean_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for v in value:
        if isinstance(v, str) and v.strip():
            out.append(v.strip())
    return out


def normalize_fragment(obj: Dict[str, Any], source: str) -> Dict[str, Any]:
    """Coerce a parsed object into a valid Fragment, dropping malformed parts."""
    outcomes: List[Dict[str, Any]] = []
    for o in obj.get("outcomes", []) if isinstance(obj.get("outcomes"), list) else []:
        if not isinstance(o, dict):
            continue
        name = o.get("name")
        if not isinstance(name, str) or not name.strip():
            continue  # an outcome with no name is unusable
        outcomes.append({
            "name": name.strip(),
            "characteristics": _clean_str_list(o.get("characteristics")),
        })

    relations: List[Dict[str, str]] = []
    for r in obj.get("relations", []) if isinstance(obj.get("relations"), list) else []:
        if not isinstance(r, dict):
            continue
        f, t, ty = r.get("from"), r.get("to"), r.get("type")
        if all(isinstance(x, str) and x.strip() for x in (f, t, ty)):
            relations.append({"from": f.strip(), "type": ty.strip(), "to": t.strip()})

    return {"source": source, "outcomes": outcomes, "relations": relations}


def extract_fragments(chunks: Sequence[Tuple[str, str]], provider) -> List[Dict[str, Any]]:
    """Extract one fragment per (source, text) chunk. Skips chunks that yield no
    parseable JSON or no usable content."""
    fragments: List[Dict[str, Any]] = []
    for source, text in chunks:
        try:
            raw = provider.complete(SYSTEM_PROMPT, build_user_prompt(text))
        except Exception as exc:  # noqa: BLE001 - provider/network errors are non-fatal
            logger.warning("extractor: provider failed on %s: %s", source, exc)
            continue
        obj = extract_json(raw)
        if obj is None:
            logger.warning("extractor: no JSON parsed from response for %s", source)
            continue
        frag = normalize_fragment(obj, source)
        if frag["outcomes"] or frag["relations"]:
            fragments.append(frag)
    return fragments

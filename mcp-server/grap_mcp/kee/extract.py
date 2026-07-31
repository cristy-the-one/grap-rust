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
import os
import re
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
    "by the text.\n"
    "IMPORTANT: write every name, characteristic, and relation in the SAME "
    "LANGUAGE as the source text. Never translate — a translated name splits "
    "one concept into two."
)


def build_user_prompt(text: str) -> str:
    return f"Extract the ontology fragment from the following text:\n\n{text}"


CONTEXT_INSTRUCTION = (
    "You also have CONTEXT from a knowledge graph built from earlier text. "
    "Reuse the exact canonical names below when the same concept appears here, "
    "instead of inventing new names. Only assert relations grounded in THIS text."
)

# The context block exists to help the model reuse canonical names for dedup —
# it needs the entity NAMES and a few characteristics, not a concept's full
# history. Without these caps the block grows with the ontology (a concept can
# accumulate 100+ characteristics; the relation vocabulary hundreds of verbs),
# so extraction prompts inflate past the model's context window as the graph
# scales — a hidden wall that makes the factory slower and lossier the bigger it
# gets. These bounds keep an extraction prompt the same size at 3,000 outcomes
# as at 30.
MAX_CHARS_PER_ENTITY = 6         # characteristics shown per known entity
MAX_CONTEXT_CHARS = 24000        # hard ceiling on the whole KNOWN-graph block (~6k tokens)
MAX_NAMES = 24                   # known concept names shown in "names" mode

# Context mode. "names" is the measured-best format (law-ro distillation iteration
# 4b, arm C): class vocabulary + known concept NAMES only, no characteristics, no
# known relations, no relation vocabulary. It measured 0.28x the prompt size of
# "full" at 0.91x yield, with MORE relation-type diversity (37 vs 34) and zero
# schema violations.
#
# The reason is an asymmetry in what may go into an LLM's context:
#   listing INSTANCES is safe and useful  -> "reuse what you see" IS deduplication
#   listing PATTERNS  is unsafe           -> "reuse what you see" becomes "assert this"
# Given typed axioms as context the model fabricated instances of every one of
# them; given a closed list of permitted relation TYPES it overloaded them until
# relation objects became verb clauses and distinct types collapsed 34 -> 14.
# "full" mode still lists KNOWN RELATIONS and a RELATION VOCABULARY, i.e. patterns,
# and is kept only for comparison.
CONTEXT_MODE = os.environ.get("GRAP_KEE_CONTEXT_MODE", "names").strip().lower()

# Class vocabulary is DOMAIN content, so it is injected by configuration rather
# than hard-coded here (comma-separated). Empty -> the class line is omitted.
CLASS_VOCAB = [c.strip() for c in
               os.environ.get("GRAP_KEE_CLASS_VOCAB", "").split(",") if c.strip()]

NAMES_INSTRUCTION = (
    "Assert a relation ONLY if THIS text states it. Name concepts as the text "
    "names them (the actual act, body or person), never with a kind-name."
)


def build_names_context(context, class_vocab=None, max_names: int = MAX_NAMES) -> str:
    """The 'names' context block: class vocabulary + known concept names only.

    Deliberately contains no relations, no relation vocabulary and no
    characteristics — nothing the model can mistake for a fact to instantiate."""
    names = [n for n in (getattr(context, "entities", None) or [])][:max_names]
    vocab = class_vocab if class_vocab is not None else CLASS_VOCAB
    if not names and not vocab:
        return ""
    lines = []
    if vocab:
        lines.append("VOCABULARY for this corpus. Prefer these names for kinds of "
                     "thing when they apply: " + ", ".join(vocab) + ".")
    if names:
        lines.append("KNOWN CONCEPT NAMES (reuse the exact name if the same concept "
                     "appears here; this list is for naming consistency only and "
                     "asserts nothing):")
        lines.extend(f"- {n}" for n in names)
    lines.append(NAMES_INSTRUCTION)
    block = "\n".join(lines)
    if len(block) > MAX_CONTEXT_CHARS:
        block = block[:MAX_CONTEXT_CHARS].rstrip() + "\n… (context truncated)"
    return block


def build_user_prompt_with_context(text: str, context, mode: str = None) -> str:
    """Plain user prompt, optionally prefixed with a bounded context block.

    `context` is a kee.working_graph.Context. With no entities the prompt is
    identical to `build_user_prompt(text)`, so early chunks behave exactly like the
    stateless path. `mode` defaults to CONTEXT_MODE ("names"; see above)."""
    if (mode or CONTEXT_MODE) == "names":
        block = build_names_context(context)
        return f"{block}\n\n{build_user_prompt(text)}" if block else build_user_prompt(text)

    if not getattr(context, "entities", None):
        return build_user_prompt(text)

    lines = [CONTEXT_INSTRUCTION, "", "KNOWN ENTITIES:"]
    for name in context.entities:
        chars = (context.characteristics.get(name) or [])[:MAX_CHARS_PER_ENTITY]
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

    block = "\n".join(lines)
    if len(block) > MAX_CONTEXT_CHARS:      # robust ceiling, independent of graph size
        block = block[:MAX_CONTEXT_CHARS].rstrip() + "\n… (context truncated)"
    return f"{block}\n\n{build_user_prompt(text)}"


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


def _fold_name(s: str) -> str:
    """Normalisation used to compare a name against the class vocabulary.

    Must match `extract_quality.QualityMonitor.record`, or the detector and the
    filter would disagree about what a class name is."""
    return str(s or "").strip().lower().replace(" ", "")


def is_class_name(name: str, class_vocab=None) -> bool:
    """True if `name` IS one of the ontology's class names rather than an instance.

    A chunk dense with cross-references and no dominant subject can push the
    model into organising by TYPE: it emits `NormativeAct` as an outcome and
    files the actual acts underneath as characteristics, inverting instances and
    classes. Chunk 19811#3 did exactly that on 2026-07-28 — four class-named
    outcomes, sixteen real acts demoted to attributes, and nine edges anchored
    on a generic node.

    Such a node is also an embedding magnet: every future act in the corpus is a
    candidate to resolve into a node literally called "NormativeAct", which is
    the instance collapse the quality alarm has always warned about."""
    vocab = class_vocab if class_vocab is not None else CLASS_VOCAB
    folded = _fold_name(name)
    return bool(folded) and folded in {_fold_name(c) for c in vocab}


# Bare document-structure labels: "Articolul 2", "Anexa 1", "Cap. III".
# Anchored on purpose — "Articolul 2 din Legea nr. 9/1998" names a specific
# provision and must survive; "Articolul 2" alone does not name anything.
_STRUCTURAL_LABEL = re.compile(
    r"^(articolul|articol|art\.?|anexa|anexe|anexă|capitolul|cap\.?|"
    r"sec[țţt]iunea|titlul|alineatul|alin\.?|litera|lit\.?|punctul|pct\.?)"
    r"\s*[0-9ivxlIVXL]*[.)]?$", re.IGNORECASE)


def is_structural_label(name: str) -> bool:
    """True if `name` is a bare structural label rather than an entity.

    Almost every Romanian act has an "Articolul 2" and an "Anexa 1", so these
    resolve across documents into single super-nodes: measured at 1,629
    documents, `Articolul 2` was ONE concept fed by 116 different source
    documents and carrying 202 characteristics, against a median of 1 source
    chunk for an ordinary concept. 54 such nodes (0.6% of concepts) held 4.8% of
    all characteristics.

    They are also the dominant source of canary instability: of the 46 true
    regressions in the first canary failure (2026-07-31), 41 — 89% — were on
    `anexa_1` alone. Because nearly every incoming document feeds them, they
    churn on every batch, which is why regression counts tracked ontology SIZE
    (corr 0.42) rather than batch size (corr 0.22) and why the rate per outcome
    kept climbing.

    Dropping them is not a loss of knowledge. A provision filed under a node fed
    by 116 documents cannot be attributed to any of them — the text
    "(1) Creditul va fi garantat printr-un depozit bancar..." appears twice under
    `Articolul 2` from two different laws, distinguishable only by the amount.
    The information was already lost; this removes the bucket.
    """
    return bool(_STRUCTURAL_LABEL.match((name or "").strip()))


def normalize_fragment(obj: Dict[str, Any], source: str, class_vocab=None) -> Dict[str, Any]:
    """Coerce a parsed object into a valid Fragment, dropping malformed parts.

    Also drops class-named outcomes and any relation that touches one. This is a
    deterministic filter rather than a prompt rule on purpose: the measured
    instruction budget for this model is ONE extra rule (the short-characteristics
    experiment regressed when a second was added), and that slot is spent on the
    language pin. A filter costs nothing and does not depend on compliance.
    """
    rejected_class_named: List[str] = []
    rejected_structural: List[str] = []
    outcomes: List[Dict[str, Any]] = []
    for o in obj.get("outcomes", []) if isinstance(obj.get("outcomes"), list) else []:
        if not isinstance(o, dict):
            continue
        name = o.get("name")
        if not isinstance(name, str) or not name.strip():
            continue  # an outcome with no name is unusable
        if is_class_name(name, class_vocab):
            rejected_class_named.append(name.strip())
            continue
        if is_structural_label(name):
            rejected_structural.append(name.strip())
            continue
        outcomes.append({
            "name": name.strip(),
            "characteristics": _clean_str_list(o.get("characteristics")),
        })

    relations: List[Dict[str, str]] = []
    rejected_class_edges = 0
    for r in obj.get("relations", []) if isinstance(obj.get("relations"), list) else []:
        if not isinstance(r, dict):
            continue
        f, t, ty = r.get("from"), r.get("to"), r.get("type")
        if all(isinstance(x, str) and x.strip() for x in (f, t, ty)):
            # Endpoints matter as much as outcomes: an ungrounded relation to
            # "NormativeAct" is parked, and `promote_parked_endpoints` promotes
            # endpoints that recur — which would rebuild the very node the
            # outcome filter just refused.
            if (is_class_name(f, class_vocab) or is_class_name(t, class_vocab)
                    or is_structural_label(f) or is_structural_label(t)):
                rejected_class_edges += 1
                continue
            relations.append({"from": f.strip(), "type": ty.strip(), "to": t.strip()})

    return {"source": source, "outcomes": outcomes, "relations": relations,
            "rejected_class_named": rejected_class_named,
            "rejected_structural": rejected_structural,
            "rejected_class_edges": rejected_class_edges}


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

#!/usr/bin/env python3
"""Generate an ASPICE ontology (the grap tool's YAML schema) from the VDA
Automotive SPICE PAM PDF ("Blue-Gold book").

Offline authoring step: the live MCP/GUI surface is unchanged. The emitted YAML
is loaded through the existing `load_ontology` capability and is meant to be
hand-curatable. The PDF and generated YAML are licensed-derived and gitignored;
this script and its synthetic-data tests are what live in the repo.

Model (per-process, Output-Work-Product based):
  process_outcome      = an ASPICE process   (id "SYS.5", name "System Qualification Test")
  required_characteristics = that process's Output Work Products, tokenized
  mapping_hints.keywords   = significant words from the process name + BP titles

Usage:
  python scripts/extract_aspice_ontology.py <pam.pdf> -o docs/examples/aspice-vda-ontology.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Pure text helpers (unit-tested on synthetic ASPICE-shaped text; no PDF here)
# --------------------------------------------------------------------------- #

_QUOTES = {"»": '"', "«": '"', "“": '"', "”": '"',
           "„": '"', "‘": "'", "’": "'", "�": '"'}

_WATERMARK_MARKERS = (
    "Dokument wurde", "VDA-QMC", "Nur zur internen", "Continental",
    "bereitgestellt vom",
)

# A base practice: SYS.5.BP3 "Title" (quotes already normalized to ASCII ")
_BP_RE = re.compile(r'\b([A-Z]{3}\.\d+)\.BP(\d+)\s+"([^"]+)"')
# An output work product: Output WP 08-52 "Test plan"  /  WPC 13-19 "Review record"
_WP_RE = re.compile(r'\bWPC?\s+(\d\d-\d\d)\s+"([^"]+)"')
# Any output-WP id reference (for per-process association): "WP 08-52" / "WPC 13-19"
_WP_REF_RE = re.compile(r'\bWPC?\s+(\d\d-\d\d)\b')
# A process-definition / contents heading: "SYS.5 System Qualification Test"
_PROC_NAME_RE = re.compile(r'^([A-Z]{3}\.\d+)\s+([A-Z][A-Za-z][A-Za-z /\-]{2,46})$')

_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "including", "their", "its",
    "are", "all", "that", "this", "shall", "will", "been", "has", "have",
    "a", "an", "of", "to", "in", "on", "by", "or", "as", "is", "be",
}


def normalize_quotes(text: str) -> str:
    """Map mis-encoded / typographic quotes to ASCII so regexes can anchor."""
    for bad, good in _QUOTES.items():
        text = text.replace(bad, good)
    return text


def is_watermark(line: str) -> bool:
    return any(m in line for m in _WATERMARK_MARKERS)


def clean_text(text: str) -> str:
    """Strip watermark lines, normalize quotes, de-hyphenate line-break splits."""
    text = normalize_quotes(text)
    lines = [ln for ln in text.splitlines() if not is_watermark(ln)]
    text = "\n".join(lines)
    # join words hyphenated across a line break: "pro-\ncessing" -> "processing"
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    return text


def tokenize(s: str) -> str:
    """Lowercase snake_case token: 'Test specification' -> 'test_specification'."""
    s = normalize_quotes(s).lower()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


def parse_base_practices(text: str) -> Dict[str, List[Tuple[str, str]]]:
    """Return {process_id: [(bp_id, title), ...]} deduped by bp_id (first wins)."""
    by_proc: Dict[str, List[Tuple[str, str]]] = {}
    seen: set = set()
    for proc, num, title in _BP_RE.findall(text):
        bp_id = f"{proc}.BP{num}"
        if bp_id in seen:
            continue
        seen.add(bp_id)
        by_proc.setdefault(proc, []).append((bp_id, title.strip()))
    for bps in by_proc.values():
        bps.sort(key=lambda b: int(b[0].rsplit("BP", 1)[1]))
    return by_proc


def parse_wp_catalog(text: str) -> Dict[str, str]:
    """Return {wp_id: name} for Output Work Products (first occurrence wins)."""
    catalog: Dict[str, str] = {}
    for wp_id, name in _WP_RE.findall(text):
        catalog.setdefault(wp_id, name.strip())
    return catalog


def parse_process_names(text: str) -> Dict[str, str]:
    """Return {process_id: name} from heading/contents lines."""
    names: Dict[str, str] = {}
    for line in text.splitlines():
        m = _PROC_NAME_RE.match(line.strip())
        if m:
            names.setdefault(m.group(1), m.group(2).strip())
    return names


def keywords_for(name: str, bp_titles: List[str], cap: int = 14) -> List[str]:
    """Significant words from a process name + its base-practice titles."""
    words: List[str] = []
    seen: set = set()
    for source in [name] + bp_titles:
        for w in re.findall(r"[A-Za-z]+", source.lower()):
            if len(w) > 3 and w not in _STOPWORDS and w not in seen:
                seen.add(w)
                words.append(w)
            if len(words) >= cap:
                return words
    return words


def build_ontology(
    process_names: Dict[str, str],
    base_practices: Dict[str, List[Tuple[str, str]]],
    wp_catalog: Dict[str, str],
    proc_wps: Dict[str, List[str]],
    version: str = "vda-2017.1",
) -> dict:
    """Assemble the tool's ontology dict from parsed parts."""
    proc_ids = sorted(set(base_practices) | set(proc_wps) | set(process_names))
    outcomes: List[dict] = []
    hints: List[dict] = []
    for pid in proc_ids:
        bps = base_practices.get(pid, [])
        if not bps and pid not in proc_wps:
            continue  # nothing assessable for this id in VDA scope
        name = process_names.get(pid, pid)
        # required_characteristics = this process's output work products (tokens)
        chars: List[str] = []
        seen: set = set()
        for wp_id in proc_wps.get(pid, []):
            wp_name = wp_catalog.get(wp_id)
            if not wp_name:
                continue
            tok = tokenize(wp_name)
            if tok and tok not in seen:
                seen.add(tok)
                chars.append(tok)
        outcomes.append({
            "id": pid,
            "name": name,
            "description": f"{name} (ASPICE process {pid}); assessed via its output work products.",
            "required_characteristics": chars,
        })
        hints.append({
            "outcome": pid,
            "indicators": [{"keywords": keywords_for(name, [t for _, t in bps])}],
        })
    return {"version": version, "process_outcomes": outcomes, "mapping_hints": hints}


# --------------------------------------------------------------------------- #
# PDF layer
# --------------------------------------------------------------------------- #

def _associate_wps(page_texts: List[str], process_names: Dict[str, str],
                   toc_cutoff: int = 20) -> Dict[str, List[str]]:
    """Stream pages; attribute Output-WP id references to the current process
    definition region (between consecutive process-definition headings in the
    body, past the contents pages). Best-effort; curatable."""
    starts: Dict[str, int] = {}
    for i, t in enumerate(page_texts):
        if i < toc_cutoff:
            continue
        for line in t.splitlines():
            m = _PROC_NAME_RE.match(line.strip())
            if m and m.group(1) in process_names and m.group(1) not in starts:
                starts[m.group(1)] = i
    if not starts:
        return {}
    ordered = sorted(starts.items(), key=lambda kv: kv[1])  # [(pid, start_page)]
    proc_wps: Dict[str, List[str]] = {pid: [] for pid, _ in ordered}
    for idx, (pid, start) in enumerate(ordered):
        end = ordered[idx + 1][1] if idx + 1 < len(ordered) else len(page_texts)
        seen: set = set()
        for p in range(start, end):
            for wp_id in _WP_REF_RE.findall(page_texts[p]):
                if wp_id not in seen:
                    seen.add(wp_id)
                    proc_wps[pid].append(wp_id)
    return proc_wps


def extract(pdf_path: str) -> Tuple[dict, dict]:
    """Open the PDF, parse, and return (ontology_dict, stats)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise SystemExit("PyMuPDF is required: python -m pip install pymupdf")

    doc = fitz.open(pdf_path)
    page_texts = [clean_text(doc[i].get_text()) for i in range(doc.page_count)]
    full = "\n".join(page_texts)

    process_names = parse_process_names(full)
    base_practices = parse_base_practices(full)
    wp_catalog = parse_wp_catalog(full)
    proc_wps = _associate_wps(page_texts, process_names)

    ontology = build_ontology(process_names, base_practices, wp_catalog, proc_wps)

    associated = sum(1 for o in ontology["process_outcomes"] if o["required_characteristics"])
    used_wps = {w for ws in proc_wps.values() for w in ws}
    stats = {
        "pages": doc.page_count,
        "processes": len(ontology["process_outcomes"]),
        "processes_named": sum(1 for o in ontology["process_outcomes"]
                               if o["name"] != o["id"]),
        "base_practices": sum(len(v) for v in base_practices.values()),
        "wp_catalog": len(wp_catalog),
        "processes_with_wps": associated,
        "wp_catalog_unassociated": sorted(set(wp_catalog) - used_wps),
    }
    return ontology, stats


def to_yaml(ontology: dict) -> str:
    import yaml
    header = ("# ASPICE ontology generated by scripts/extract_aspice_ontology.py\n"
              "# Source: VDA Automotive SPICE PAM (licensed). Hand-curate as needed.\n\n")
    return header + yaml.safe_dump(ontology, sort_keys=False, allow_unicode=True,
                                   default_flow_style=False)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Extract an ASPICE ontology from the VDA PAM PDF.")
    ap.add_argument("pdf", help="path to the VDA ASPICE PAM PDF")
    ap.add_argument("-o", "--out", default="docs/examples/aspice-vda-ontology.yaml",
                    help="output YAML path")
    args = ap.parse_args(argv)

    ontology, stats = extract(args.pdf)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(to_yaml(ontology))

    print(f"Wrote {args.out}")
    print("Coverage summary:")
    for k, v in stats.items():
        if k == "wp_catalog_unassociated":
            print(f"  {k}: {len(v)}" + (f" ({', '.join(v)})" if v else ""))
        else:
            print(f"  {k}: {v}")
    if stats["processes_with_wps"] < stats["processes"]:
        print("  NOTE: some processes have no associated work products — "
              "curate required_characteristics by hand where needed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

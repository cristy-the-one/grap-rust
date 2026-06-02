# ASPICE Ontology Extractor — Design Spec

**Date:** 2026-06-02
**Status:** approved

## Goal

Generate a real ASPICE ontology (in the tool's existing YAML schema) from the
VDA Automotive SPICE PAM PDF ("Blue-Gold book"), so the gap reasoner assesses
project evidence against the actual ASPICE process model instead of a toy
ontology.

## Why an offline script (not a new MCP tool)

The PDF is a licensed document and extraction is a one-time authoring step. An
offline script keeps the live MCP/GUI surface unchanged, is fully reproducible,
and produces a hand-curatable YAML that is loaded through the existing
`load_ontology` capability. No runtime PDF dependency is added to the server.

## Modelling decision (chosen)

Per-process, Output-Work-Product–based:

- `process_outcome` = an ASPICE **process** (id e.g. `SYS.5`, name "System
  Qualification Test").
- `required_characteristics` = the process's **Output Work Products**,
  normalized to snake_case tokens (e.g. `test_specification`, `test_plan`).
  These are the assessable evidence items.
- `mapping_hints[].keywords` = significant words drawn from the process name and
  its base-practice titles, giving the lexical reasoner vocabulary to match
  project evidence against.

This mirrors a real ASPICE assessment: a process is evidenced by its output work
products.

## Extraction approach

Input: PDF path. Output: `aspice-vda-ontology.yaml`.

1. **Text cleaning** (`_clean_lines`): drop per-copy watermark/header lines
   ("Dokument wurde …", "VDA-QMC …", "Nur zur internen …", "Continental …"),
   normalize mis-encoded smart quotes (`» « " „ �`) to ASCII `"`,
   de-hyphenate words split across line breaks.
2. **Process names** (`_parse_process_names`): from the contents region, match
   `^([A-Z]{3}\.\d+)\s+([A-Z][A-Za-z …]+)$`. Processes without a parsed name
   fall back to a name derived from the id.
3. **Base practices** (`_parse_base_practices`): `([A-Z]{3}\.\d+)\.BP(\d+)\s+"(.+?)"`.
   The process↔BP link is the embedded id prefix (100% reliable). Deduplicate by
   BP id (titles repeat in shared sections — keep first occurrence).
4. **Work-product catalog** (`_parse_wp_catalog`): `WPC?\s+(\d\d-\d\d)\s+"(.+?)"`
   → `{wp_id: name}`.
5. **Process→WP association** (`_associate_wps`): segment the document into
   per-process definition regions (between consecutive process-definition
   headings in the body) and collect the `Output WP NN-NN` references inside each
   region, so the shared traceability/consistency WPs do not leak into every
   process. Best-effort; reported in the coverage summary.
6. **Emit YAML** (`build_ontology` + `to_yaml`): the tool's schema
   (`version`, `process_outcomes[]`, `mapping_hints[]`). Token normalization
   (`_tokenize`): lowercase, non-alphanumerics → `_`, collapse repeats, strip.

Each function is independently unit-testable on synthetic text.

## Output & licensing

- The PDF and the generated `aspice-vda-ontology.yaml` are **gitignored** (both
  derive from a licensed document). The script and synthetic-data tests are
  committed.
- The script prints a coverage summary: processes found / named, base practices,
  WP catalog size, processes with ≥1 associated WP, and any unassociated WPs —
  so the curator sees what needs review.

## Testing

`mcp-server/tests/test_extract_aspice_ontology.py` exercises each parser on a
small **synthetic** ASPICE-shaped snippet (no real PDF text), asserting:
- watermark/quote cleaning,
- BP id/title parsing + process grouping,
- WP catalog parsing,
- token normalization,
- end-to-end `build_ontology` produces the expected schema with WPs as
  `required_characteristics`.

## Usage

```
python scripts/extract_aspice_ontology.py docs/VDA_ASPICE_BlueGoldBook_Sept2017.pdf \
    -o docs/examples/aspice-vda-ontology.yaml
```
Then load `aspice-vda-ontology.yaml` via the GUI's *Load ontology* and run gap
detection against ingested project evidence.

## Out of scope (v1)

- Process capability levels / rating (the GP/PA dimension).
- Perfect per-BP (vs per-process) WP association.
- Committing the generated ontology or the PDF.

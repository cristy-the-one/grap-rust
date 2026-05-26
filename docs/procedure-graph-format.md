# Procedure Graph Text Format (v0.1 — POC)

## Goals

- Human-editable in plain text (offline friendly)
- Flat list of steps (no nesting in the source format)
- Explicit, unambiguous data flow between steps
- Clear references to Knowledge Graph elements
- Easy to parse and project into grap structures
- Pleasant to integrate with a future visual canvas (grouping, blocks, and connections can live in the UI layer)

## Design Principles

- One file = one Procedure Graph
- Steps are the atomic unit in the text
- "Procedure Blocks" / reusable groups are a **visual layer concern**, not a text format concern
- Verbosity is acceptable if it increases clarity and auditability
- The format is the **source of truth**; the grap representation is a **projection**

## File Structure

```yaml
name: <string>                    # Human readable name
description: <string>             # Optional longer description
version: <string>                 # Semantic or date-based version

steps:
  - id: <string>                  # Stable identifier (e.g. "step-01", "validate-signal")
    operation: <string>           # What this step does (can be a reference or short description)
    inputs:
      <input-name>: <reference>   # See Data Flow section below
    outputs:
      <output-name>: <type>       # Optional type hint (string, list, etc.)
    references:
      - <kg-reference>            # Links to Knowledge Graph elements
    guards: []                    # Optional conditions
    notes: <string>               # Optional human comments
```

## Data Flow (References)

Use explicit references:

```yaml
inputs:
  signal: "step-validate.outputs.validated_signal"
  threshold: 0.85
```

Supported reference forms:

- `step-xxx.outputs.yyy`
- `kg:some-concept-id`
- Literal values (strings, numbers, booleans)

## Knowledge Graph References

Use the `references` field:

```yaml
references:
  - kg:aspice.process_outcome.SYS.5.BP3
  - kg:signal.Brake_Pedal_Position
```

## Example (Minimal)

See `docs/examples/simple-aspice-gap-check.pg.yaml` for a realistic POC example.

## Future Extensions (Not in POC v0.1)

- File includes / modular composition
- Typed input/output schemas
- Step templates / macros
- Visual layout hints (position, color, etc.)

---

**Status**: This is the working format for the narrow-but-vertical ASPICE gap detection POC.

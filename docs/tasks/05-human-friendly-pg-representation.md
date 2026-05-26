# Task: Human-Friendly Procedure Graph Representation

## Objective
Define and implement the human-editable text format for the Procedure Graph (flat list of steps) along with the mechanisms to project it into grap structures for execution, while supporting future visual tooling.

## Architectural Driver
- Experts must be able to edit the PG offline in a practical format.
- The format must be "pleasant to integrate" with a visual canvas layer (even though the visual layer itself is out of scope).
- Detailed content lives in referenced files; grap nodes stay light.
- The format must support the full lifecycle (publish levels, activation, frozen weights, multiple activations, etc.).

## Key Deliverables

1. **Text Format Specification**
   - Schema for a flat list of steps.
   - How to express operations, inputs/outputs, data flow (explicit references), references to KG elements, guards, etc.
   - Support for comments, documentation, and stable identifiers.

2. **Projection / Compilation Layer**
   - How the text representation is turned into executable grap structures (nodes, edges, properties, bitmasks).
   - Handling of references to the Knowledge Graph.

3. **File & Storage Model**
   - How PG files are versioned, hashed, and linked from grap activations.
   - Integration with the "referenced files" approach used for detailed content.

4. **Tooling (MCP + Local)**
   - Capabilities for reading, writing, validating, and diffing PG text files.
   - Support for future visual editors (clear, stable serialization).

## Dependencies
- Task 01 (Versioning & Lifecycle)
- Task 02 (MCP / Tool Surface)
- Task 06 (Publish / Validation Pipeline) — validation must be able to operate on the text representation.

## Acceptance Criteria
- Domain experts can productively edit Procedure Graphs in the chosen text format offline.
- The format is clean enough that building a visual canvas on top would not be painful.
- Changes to the text files are correctly versioned and can participate in the publish/activation model.
- The projection into grap structures preserves the necessary information for execution, audit, and reproducibility.

## Decisions (Approved)

- **Text Format Syntax (POC)**: Structured YAML (or a very small YAML-based DSL). It offers good offline editability, diff/review properties, and is straightforward to parse and validate.
- **Data Flow References**: Use strong explicit references (e.g., `inputs: { foo: step-17.outputs.bar }`). Verbosity is acceptable in the source format for clarity and auditability.
- **Includes / Modular Composition**: Not included in the first POC. Keep the format strictly flat for simplicity. Composition can be added later based on real usage feedback.

## Open Questions
*(All resolved during clarification session.)*
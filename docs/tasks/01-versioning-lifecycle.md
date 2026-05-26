# Task: Versioning & Lifecycle Framework for KG and PG

## Objective
Design and implement a first-class versioning and lifecycle system for the Knowledge Graph (KG) and Procedure Graph (PG) that supports the deliberate, auditable, multi-level model required by the architecture.

## Architectural Driver
The system requires:
- Distinct concepts of **Publish** (with levels: Draft vs Promoted)
- **Activation** (separate from promotion, with frozen configuration)
- A **production flag** that can move between activations
- Ability to have multiple concurrent activations of the same underlying logic
- Hash-based change detection + automatic fallback to last known good

The current grap engine only provides basic `save_to_dir` / `load_from_dir` with no native concept of versions, activations, or governed state transitions.

## Key Deliverables

1. **Version Model**
   - Define what constitutes a "version" for PG vs KG.
   - Support for immutable, content-addressable versions.

2. **Activation Model**
   - Concept of an "activation" as a (PG version + frozen weights + KG snapshot reference).
   - Support for multiple activations of the same PG logic.
   - Explicit "production" flag with clear semantics (only one per PG logic at a time).

3. **Lifecycle State Machine**
   - States and transitions for PG versions (Draft → Promoted).
   - Activation lifecycle (created → activated → superseded).
   - Rules for moving the production flag.

4. **Storage & Referencing**
   - How versions and activations are stored in grap.
   - How the "frozen" state (weights, KG snapshot) is captured at activation time.

5. **API / MCP Surface**
   - Capabilities to create versions, publish, activate, and query the current production activation.

## Dependencies
- Task 02 (MCP / Tool Surface) — for exposing the lifecycle operations.
- Task 05 (Human-friendly PG Representation) — activations must reference specific PG file versions.
- Task 09 (Reproducibility Primitives) — activations must be able to freeze configuration.

## Acceptance Criteria
- It is possible to publish a PG version as Draft, promote it, and activate it with a specific set of frozen weights.
- The system can correctly report the current production activation and its frozen configuration.
- Multiple activations of the same PG logic can exist, with only one marked as production.
- Changing the production flag automatically generates an auditable Temporal Graph event.
- Hash-based detection can identify when the underlying files have changed since the last successful activation.

## Decisions (from clarification session)

- **KG Lifecycle Model**: The Knowledge Graph will follow a **similar but lighter** lifecycle model compared to the Procedure Graph (B). It will support versioning and publishing, but without the full "production flag" mechanics that the PG requires. The critical part is being able to snapshot the ontology state at PG activation time.

- **KG Versioning Granularity**: The **ontology** will be versioned separately from other KG content (B). This allows the reasoning layer to evolve independently while other project knowledge can follow a different cadence.

- **Metadata Frozen at Activation**: At minimum, every PG activation must freeze:
  - The exact PG version
  - The active weight set
  - A reference to the published KG/ontology version at the time of activation

  This is the baseline required for reproducibility.

## Open Questions (remaining)
*(None remaining in this group — all resolved during clarification.)*
# Task: Publish & Validation Pipeline

## Objective
Design and implement the deliberate publish workflow (with levels) and the associated validation mechanisms, including cross-graph checks between the Procedure Graph and Knowledge Graph.

## Architectural Driver
- Publish is a first-class, auditable, MCP-exposed action.
- Two levels: Draft (low friction) and Promoted (higher bar).
- Validation must run at publish time (fast/incremental is a first-class requirement).
- PG publish should be able to validate against the currently published KG/ontology.
- Mandatory rationale on publish actions.

## Key Deliverables

1. **Publish Workflow**
   - State machine and rules for Draft vs Promoted.
   - Mandatory rationale capture.
   - Integration with the file-based PG representation.

2. **Validation Engine**
   - Fast, incremental validation logic (leveraging work from Task 04).
   - Cross-KG/PG validation rules (e.g., ontology references, coverage expectations).
   - Clear reporting of validation results (including for Draft mode).

3. **Integration with Lifecycle**
   - How a successful Promoted publish enables activation.
   - How validation results are recorded in the Temporal Graph.

## Dependencies
- Task 01 (Versioning & Lifecycle)
- Task 04 (Ontology / Reasoning Layer) — validation depends on the inference engine.
- Task 05 (Human-friendly PG Representation)
- Task 02 (MCP / Tool Surface)

## Acceptance Criteria
- Publishing a PG as Draft is low-friction and always possible.
- Publishing as Promoted runs meaningful validation (including KG cross-checks) and can be done reasonably quickly.
- Validation results are clear and actionable.
- Every publish action produces proper audit events with the supplied rationale.

## Decisions (Approved)

- **Validation Rules (POC)**: Start with the minimum set needed to support meaningful ASPICE gap detection (ontology reference validity, basic cross-KG/PG consistency, and the specific gap-detection rules in scope). Expand iteratively.
- **Pre-computed / Cached Validation**: Invest in incremental and cached validation as a first-class requirement to keep the publish path fast.
- **Pre-validation on Save/Edit**: Provide background/on-save validation feedback (especially useful in Draft mode). It must never block saving — only inform the user.

## Open Questions
*(All resolved during clarification session.)*
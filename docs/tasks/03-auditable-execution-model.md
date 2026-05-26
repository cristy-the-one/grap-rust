# Task: Auditable Execution Model & Temporal Graph

## Objective
Design and implement the mechanisms for capturing execution context, run records, and immutable explanations so that "what happened" during any run (production or experimental) is fully traceable and reproducible.

## Architectural Driver
- Medium-rich run records (high-level chronological timeline + linked artifacts).
- Immutable explanation records containing raw signals + rendered snapshot.
- Strong auditability and "repeatable perfection" goals.
- Experiments must be fully visible in the audit trail even while quarantined from quality metrics.

The current engine has excellent persistence but no native concept of execution runs, explanation artifacts, or a dedicated Temporal Graph layer.

## Key Deliverables

1. **Run Record Model**
   - Schema for medium-rich execution records.
   - Timeline event types and granularity.
   - Linkage strategy to explanation records, version snapshots, and generated artifacts.

2. **Immutable Explanation Records**
   - Storage format (raw signals + rendered text snapshot).
   - Generation points (publish, automated analysis, experiments, on-demand).
   - Immutability guarantees.

3. **Temporal Graph Subsystem**
   - Event model and storage strategy.
   - Query capabilities needed for audit, investigation, and assisted discovery.
   - Integration with the three graph instantiations.

4. **Experiment Quarantine**
   - Technical mechanism to ensure experimental runs do not pollute production quality/repeatability metrics while still being fully audited.

## Dependencies
- Task 02 (MCP / Tool Surface) — many audit queries will be exposed via MCP.
- Task 01 (Versioning & Lifecycle) — run records must reference specific activations and frozen configurations.
- Task 07 (Experimentation & Quarantine)

## Acceptance Criteria
- Every production and experimental run produces a medium-rich run record.
- Explanation records are immutable and contain both raw data and the rendered explanation at the time.
- It is possible to fully reconstruct "what happened" for any significant run using only the audit data.
- Experimental runs are clearly marked and excluded from automated quality calculations.

## Decisions (Approved)

- **Timeline Event Granularity (POC)**: Medium granularity — one event per processed artifact (or small batch) + events for significant inferences, gaps, and decisions. Avoid both ultra-coarse and ultra-fine levels.
- **Storage of Explanation Records**: Hybrid model — the event lives in the Temporal Graph; the payload (raw signals + rendered snapshot) lives as an immutable file in the referenced-files system.
- **Retention Policy**: Run records and explanation records follow the same customer-configurable regulatory period. Experimental records may have a shorter configurable window.

## Open Questions
*(All resolved during clarification session.)*
# Task: Reproducibility Primitives

## Objective
Provide the low-level capabilities needed to achieve the "repeatable perfection" goal: freezing configuration at activation time, capturing relevant state during runs, and enabling faithful reconstruction of past executions.

## Architectural Driver
- Weights are frozen at activation.
- Runs record the relevant KG state.
- Full reproducibility is required even when the live KG or weights have changed since the run.
- Air-gapped operation and regulatory audit requirements.

## Key Deliverables

1. **Frozen Configuration Model**
   - How weights, KG references, and other configuration are captured and stored at activation time.
   - How an activation becomes a self-describing, reproducible unit.

2. **Run Context Capture**
   - What state is snapshotted or referenced for each run record.
   - Linkage between run records and the frozen activation state.

3. **Reconstruction Support**
   - Capabilities to re-execute (or simulate) a past run given its recorded context.
   - Support for auditors and investigators.

## Dependencies
- Task 01 (Versioning & Lifecycle)
- Task 03 (Auditable Execution Model)
- Task 08 (Configuration Governance)

## Acceptance Criteria
- Given an activation record and its associated run records, it is possible to determine exactly what logic, weights, and KG state were used.
- Reproducibility is not compromised by later changes to live weights or the ontology.
- The system can answer "what would this run have done with the weights/KG at the time?" even years later.

## Decisions (Approved)

- **Minimal Data for Reproducibility**: At minimum, capture the PG version + frozen weights + a reference (or snapshot) to the relevant published KG/ontology state at activation/run time. Everything else can be referenced unless it is expected to disappear.
- **Vanishing External References**: The system should clearly report “reproduction incomplete — referenced artifact X is missing” rather than failing silently or producing incorrect results.

## Open Questions
*(All resolved during clarification session.)*
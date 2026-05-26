# Task: Experimentation & Quarantine Model

## Objective
Define and implement the mechanisms that allow flexible, controlled experimentation while ensuring experimental activity is fully audited but strongly isolated from production quality, safety, and repeatability metrics.

## Architectural Driver
- Experiments must be possible and reasonably flexible ("experiments should be flexible").
- All experimental activity must be fully visible in the audit trail.
- Experiments must be strongly quarantined from automated quality/repeatability calculations and "last known good" logic.
- Same level of run records and explanation records as production.

## Key Deliverables

1. **Experiment Declaration & Execution**
   - How an experiment is explicitly started (capability, parameters, scope).
   - Linkage to a specific PG activation or ad-hoc version.

2. **Quarantine Mechanisms**
   - Technical enforcement that experimental runs do not affect production metrics, last-known-good calculations, or automated monitoring.
   - Clear labeling in all audit artifacts.

3. **Audit Parity**
   - Ensuring experimental runs generate the same quality of records and explanations as production runs.

4. **Transition Path**
   - How useful experimental results can be promoted into production (e.g., via normal publish/activation of improved PG or KG content).

## Dependencies
- Task 02 (MCP / Tool Surface)
- Task 03 (Auditable Execution Model)
- Task 01 (Versioning & Lifecycle)

## Acceptance Criteria
- It is possible to run experiments in a clearly marked, flexible way.
- Experimental runs are fully visible in the Temporal Graph and produce proper explanation records.
- No experimental data leaks into production quality metrics or safety mechanisms.
- There is a clear path to promote learnings from experiments into the main system.

## Decisions (Approved)

- **Boundary Definition**: An experiment is any execution that is explicitly declared as such (via MCP capability or flag). It is excluded from production quality metrics and last-known-good calculations, but still produces full audit records.
- **Retention for Experimental Data**: Shorter, separately configurable retention window (as decided in the clarification session).

## Open Questions
*(All resolved during clarification session.)*
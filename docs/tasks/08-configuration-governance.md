# Task: Configuration Governance (Weights, Policies, etc.)

## Objective
Define and implement the lightweight, auditable, MCP-exposed configuration surface for things like prioritization weights, retention policies, and other narrow tunable parameters.

## Architectural Driver
- Very open tuning for a small set of weights (with mandatory rationale).
- Customer/project-configurable retention policies.
- Low runtime tunability overall, but configuration must be changeable without heavy ceremony.
- Changes must be fully audited.

## Key Deliverables

1. **Configuration Model**
   - Identify the narrow set of configuration items that belong in this lightweight layer (vs. those that require full PG/KG publishes).
   - Define versioning and snapshotting behavior (especially weight freezing at activation).

2. **MCP Interface**
   - Simple capabilities to read and update the small set of weights/policies.
   - Mandatory rationale capture on changes.

3. **Governance Rules**
   - Who can change what (very open per earlier decision).
   - Audit and visibility requirements.
   - Interaction with the publish/activation model (weights are frozen on activation).

## Dependencies
- Task 01 (Versioning & Lifecycle)
- Task 02 (MCP / Tool Surface)
- Task 09 (Reproducibility Primitives)

## Acceptance Criteria
- A small, well-documented set of weights can be changed via a simple MCP interface with mandatory rationale.
- Weight changes are properly audited.
- The system correctly freezes the weights that were current at activation time.
- Retention policies are configurable per customer/project.

## Decisions (Approved)

- **Visibility for Weight Changes**: No mandatory pre-activation visibility or acknowledgment step is required (consistent with "very open" + low-ceremony tuning). The mandatory rationale + audit logging is considered sufficient.
- **Presenting Current Weights**: The activation capability should surface the current weights (and when they were last changed) as part of the confirmation/visibility step before activation.

## Open Questions
*(All resolved during clarification session.)*
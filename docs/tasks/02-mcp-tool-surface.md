# Task: MCP Tool / Capability Surface (grap-engine-mcp)

## Objective
Design and implement the primary interface layer (MCP) through which humans and agents interact with the three graphs (KG, PG, TG) and the lifecycle/operational controls.

## Architectural Driver
The vision treats the entire system as a collection of agents (human + AI) that intensively use the grap engine through MCP. Almost all meaningful actions — editing, publishing, activating, running, querying audit data, tuning configuration — must be exposed as first-class MCP capabilities.

The current project has no MCP layer. `pygrap` provides direct Python bindings, not a capability-oriented interface.

## Key Deliverables

1. **Capability Taxonomy**
   - Group capabilities into coherent domains (Procedure Graph Lifecycle, Execution, Audit, Configuration, etc.).
   - Define clear names, parameters, and return types for each capability.

2. **MCP Server Implementation**
   - Server that exposes the agreed capability set.
   - Authentication / authorization model (especially important for air-gapped + regulated environments).
   - Support for both interactive (human) and agent-driven usage.

3. **Capability Documentation**
   - Machine-readable + human-readable descriptions of every capability.
   - Examples for common workflows (publish a PG, run an experiment, query explanations, etc.).

4. **Error & Audit Semantics**
   - Every capability call that mutates state must produce appropriate Temporal Graph events.
   - Clear error semantics that support auditability.

## Dependencies
- Task 01 (Versioning & Lifecycle) — most lifecycle capabilities depend on the models defined there.
- Task 03 (Auditable Execution Model)
- Task 04 (Ontology / Reasoning Layer)
- Task 08 (Configuration Governance)

## Acceptance Criteria
- All major workflows defined in the architecture can be performed through MCP capabilities (no "secret" direct API usage required for normal operation).
- Every mutating capability produces proper audit events.
- The capability surface is small enough to be understandable but complete enough to support the vision.
- Experimental and production paths are clearly distinguishable at the capability level.

## Decisions (Approved)

- **MCP Server Architecture**: Start with a single well-structured MCP server for the POC. Split into specialized servers later if clear boundaries emerge.
- **Minimal Viable Capability Set (POC)**: Focus on the core lifecycle (PG/KG publish + activation), execution (run + experiment), and basic audit capabilities needed to support the narrow-but-vertical ASPICE gap-detection POC.
- **Authorization Granularity (Early Versions)**: Role-based access is sufficient for the POC. Fine-grained per-object permissions can be added later if real governance pain appears.

## Open Questions
*(All resolved during clarification session.)*
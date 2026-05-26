# Task: Ontology / Reasoning Layer (Grap-Native Lightweight)

## Objective
Design and implement a scoped, lightweight, grap-native ontology and reasoning capability that supports the primary POC goal (detecting ASPICE gaps) while meeting the constraints of speed, auditability, air-gapped operation, and evolvability.

## Architectural Driver
- "Scoped + lightweight reasoner" built on grap first.
- Domain fidelity first, but fast/incremental validation is a first-class requirement.
- Balanced hybrid inference (structured + text/embeddings).
- Explanations are mandatory ("why" is a must).
- Ontology must be evolvable.

The current engine has powerful traversal and hybrid search but no ontology or reasoning layer on top.

## Key Deliverables

1. **Ontology Modeling Approach**
   - How to represent classes, individuals, properties, and restrictions using grap primitives (nodes, rich edges, bitmasks, etc.).
   - Balance between grap-native idioms and OWL conceptual alignment.

2. **Inference Engine**
   - Lightweight classification and gap detection implementation.
   - Hybrid inference strategy (when structured vs. statistical signals win).
   - Performance characteristics (target: fast enough to support sub-2-minute validation in normal cases).

3. **Explanation Generation**
   - How to produce sectioned, light-to-moderate explanations for hybrid inferences.
   - Storage of raw signals + rendered snapshots.

4. **Integration Points**
   - How the PG execution layer invokes inference/gap detection.
   - How publish-time validation uses the reasoner.

## Dependencies
- Task 01 (Versioning & Lifecycle) — ontology versions must be publishable and snapshotable.
- Task 03 (Auditable Execution Model) — explanations must integrate with run records.
- Task 09 (Reproducibility Primitives)

## Acceptance Criteria
- The POC can detect meaningful ASPICE gaps using a combination of structured and statistical signals.
- Inferences can produce understandable explanations.
- Validation during publish can run fast enough to support the velocity goals.
- The ontology model can be extended over time without major rework.

## Decisions (Approved)

- **Minimal Viable Ontology Content (POC)**: Keep it small and focused — a handful of high-value ASPICE process outcomes + their key expected characteristics. Depth and usefulness over breadth.
- **Location of Inference Logic**: Push as much as possible into reusable grap structures (nodes, edges, properties, bitmasks, traversals) for auditability and evolvability. Only put truly complex or performance-critical logic into specialized code.
- **Granularity of Explanation Raw Signals**: Capture enough data to reproduce the decision later (which ontology version, which rules/patterns fired, key input facts), while staying balanced on storage cost.

## Open Questions
*(All resolved during clarification session.)*
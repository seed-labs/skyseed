# OpenSN Investigation Skill

Use this guidance before adopting OpenSN-inspired runtime architecture ideas in
`satellite/`.

## Goal

Run investigation first. Treat OpenSN as an architectural reference candidate,
not as code to copy.

## Required Investigation Structure

Any OpenSN investigation note must include the following sections.

### 1. Source Inspected

- repository, documentation, paper, or other source inspected
- exact paths, URLs, commits, tags, or document versions if available

### 2. License

- verified license
- any constraints relevant to reuse or inspiration

### 3. Architecture Summary

- high-level component layout
- where topology, planning, and runtime execution live

### 4. Runtime Update Workflow

- how updates are generated
- how updates are distributed
- how updates are applied on nodes

### 5. Node Daemon Behavior

- whether a node daemon exists
- what it stores in memory
- what it validates
- what it executes locally

### 6. KV / Config Store Behavior

- whether a KV store or config store is used
- what data is stored there
- whether nodes push or pull
- how versioning or watch behavior works if documented

### 7. Link Update Optimization

- how OpenSN reduces unnecessary link or runtime churn
- whether it avoids excessive container-runtime interactions
- any batching, diffing, or lazy-update behavior
- how runtime topology or link updates are represented

### 8. What To Adopt

- verified ideas worth adapting
- why they fit `satellite/`

### 9. What To Reject

- verified ideas that should not be copied
- why they do not fit `satellite/`

### 10. Why

- reasoning that connects the adoption or rejection decision back to
  Iteration 2 principles

## Verification Rule

Do not claim findings unless they are actually verified from source or
documentation. If something is inferred, mark it clearly as inference.

## Output Rule

Investigation output should distinguish:

- verified fact
- direct quote or close paraphrase
- inference
- open question

## Anti-Patterns

- Do not write "OpenSN does X" without citing the inspected source.
- Do not translate a vague impression into an architecture commitment.
- Do not skip the license check.

# Sylanne adapter boundary

Sylanne may own layered memory, retrieval, state injection, and relationship systems.
AstrContinuum does not duplicate those capabilities.

```text
Sylanne: long-term meaning, relationships, persona, life memory
AstrContinuum: current-call working set, durable context boundary, request assembly, token budget
```

The conceptual read-only boundary may include:

- long-term memory retrieval;
- importance hints;
- privacy level;
- formatted state fragments;
- provenance and confidence.

It must avoid:

- injecting the same memory twice;
- exposing internal or private content;
- copying Sylanne memory into AstrContinuum's permanent fact store;
- depending on Sylanne private storage layouts.

Current status: the local adapter is a compatibility boundary only. It returns no retrieved memory
or importance hint and is not wired into the normal AstrContinuum runtime. Do not describe it as
an available integration. If a verified adapter is introduced later, absence or incompatibility
must fail open to the standalone path without interrupting the main request flow.

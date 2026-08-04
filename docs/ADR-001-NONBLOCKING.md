# ADR-001: Compaction Never Enters the Reply Critical Path

Status: Accepted

User requests MUST be assembled immediately from the latest committed Snapshot, the
uncompacted Delta, and local retrieval. Any LLM-backed compaction runs only in the background.

Consequences: the system MUST tolerate a lagging Snapshot; the Delta remains available; and
emergency assembly, a background queue, and failure recovery are required.

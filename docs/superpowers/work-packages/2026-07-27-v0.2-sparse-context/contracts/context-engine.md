# Sparse Context Engine Contract

## Authority

- Journal, committed Capsules, Snapshot memberships, and active pointer remain authoritative.
- Graphs, operators, activations, reductions, and certificates are derived and request-local.
- No graph operation rewrites or deletes authoritative history.

## Public Types

The engine package is `astrcontinuum.context_graph`.

Required public names:

- `ContextGraphConfig`
- `ContextEngineMode`
- `EngineOutcome`
- `GraphCoordinate`
- `SparseRelation`
- `ConstraintRow`
- `ContextGraph`
- `QueryActivation`
- `ErrorCertificate`
- `EngineResult`
- `SparseContextEngine`

Content-bearing text stays in existing `CandidateBlock` instances and is excluded from graph
object representations.

## Numerical Model

The propagation operator is `Q = R.T @ W @ R + D + epsilon * I`.

The query solve minimizes `0.5 * a.T @ Q @ a - q.T @ a` subject to `C @ a = d`.

Supported constraints are coordinate fix-to-one, fix-to-zero, and pairwise equality. Constraint
support is always retained during state reduction. Linear systems are solved, never explicitly
inverted.

Every successful result recomputes stationarity, constraint, reconstruction, required-block, and
provenance checks. A nonfinite value, unsupported constraint, failed solve, or failed certificate
returns a stable code and cannot replace deterministic fallback output.

## Runtime Modes

- `ACTIVE`: verified graph selections may be used.
- `SHADOW`: graph selections are computed but discarded.
- `OFF`: graph computation is skipped.
- `DEGRADED_RAW` is an observed outcome, never a configured mode.

## Security

- No content, source span, entity, SessionKey, provider id, path, key material, ciphertext, or
  exception text enters graph metrics or stable errors.
- No plaintext graph file or process-global content cache.
- Storage authentication failures bypass the engine and lock the storage boundary.

## Dependency Boundary

- Required: `numpy>=1.26,<3`.
- Optional: `scipy>=1.12,<2` under the `sparse` extra.
- Imports are lazy.
- No wheel, DLL, shared object, or other third-party binary is vendored or packaged.
- The pure-Python reference backend is limited to tiny systems and validation.

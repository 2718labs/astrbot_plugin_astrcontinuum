# Evaluation plan and evidence boundary

> This page defines scenarios, metrics, and release thresholds. It is not an experiment result.
> The v0.3 Technical Preview has no admitted real semantic-model or Provider evaluation result.
> Frozen isolated research summaries, the data manifest, and figures are in
> [Evidence](EVIDENCE.md); the current production chain and CRM research target chain are in
> [Workflow](WORKFLOW.md).

## Three kinds of assurance

- **Storage-lossless:** raw events can be reconstructed exactly.
- **Source-verifiable:** important state can be traced to source.
- **Behaviorally near-lossless:** behavior after compression remains close to full history.

## Benchmark scenarios

- **Needle:** seed an early name, number, prohibition, and constraint.
- **Decision reversal:** `A → reject A → B → C`.
- **Open loop:** continue an unfinished task after a long interval.
- **Topic return:** return after several unrelated topics.
- **Pronoun:** resolve phrases such as “that one just now”, “the second option”, and “the
  original one”.
- **Tool overflow:** recover important content after an oversized tool result is externalized.
- **Concurrency:** continue requests while compaction is delayed.
- **Crash:** inject faults at compilation, audit, and commit stages.
- **Sylanne coexistence:** check duplicate injection and privacy only after a verified adapter is
  actually wired.

## Metrics

- `critical_anchor_recall`
- `constraint_recall`
- `active_decision_accuracy`
- `open_loop_recall`
- `exact_entity_accuracy`
- `stale_fact_pollution`
- `unsupported_claim_rate`
- `source_coverage`
- `compression_ratio`
- `assembly_latency_p95`
- `main_path_compaction_wait_count`
- `recovery_success`

## Future release thresholds

- `main_path_compaction_wait_count = 0`
- `critical_anchor_recall = 100%`
- coverage gap `= 0`
- unsupported critical claims `= 0`
- crash scenarios remain usable
- long-history token reduction `>= 75%`

## Evidence and release-claim boundary

The preceding thresholds are future evaluation and release gates, not results claimed here. The
ordinary Provider-bound/default AstrBot path leaves `reorganization_token_budget` unset and
supplies an empty reorganization-record tuple. The opt-in injected/fenced Gate A path can call
`reorganize_capsules()` with an explicit budget, but its synthetic receipts establish only wiring
and durable-publication behavior. No CRM, synthetic, or offline Summary record can replace
end-to-end evidence for the ordinary production chain or establish a comparison win.

The reviewable frozen research summary is in [Evidence](EVIDENCE.md). It distinguishes synthetic
R2 observations, deterministic CRM stress accounting, and the claims v0.3 may make. All numbers
are labeled with source hashes and a non-production boundary.

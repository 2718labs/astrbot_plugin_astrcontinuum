# v0.3 Evidence and RMB Workflow Documentation Design

Status: approved for documentation implementation

Date: 2026-08-04

## Goal

Make the v0.3 Technical Preview documentation honest and inspectable by
separating (1) the current production publication contract, (2) frozen
research evidence, and (3) the not-yet-wired CRM research path. The
documentation must include the requested experiment summaries, a restrained
RMB-inspired academic palette, and a readable workflow.

## Evidence boundary

The production worktree contains an evaluation plan and release thresholds,
not a completed semantic experiment. Therefore it must not claim semantic
quality, Provider performance, or v0.3 release readiness from the included
research observations.

Two independently scoped, local frozen sources are admitted as documentation
evidence:

| Source | What is admitted | What is not admitted |
| --- | --- | --- |
| Evidence-repair R2 | Frozen synthetic 12-scenario × 3-trial aggregate observations and their SHA-256 provenance | Production performance, population-level utility, or a Summary runtime fallback |
| CRM V21-008 stress run | Twelve deterministic, replay-matching stress rounds and their recorded byte/loss accounting | A production v0.3 result, semantic quality result, or release evidence |

The new repository extracts contain only aggregate, non-sensitive fields.
Their documents retain source roots and hashes so a local holder of the
frozen assets can check the extraction without importing a blind-request
bundle into this PR.

## Documentation units

| Path | Responsibility |
| --- | --- |
| docs/EVIDENCE.md | Claim boundary, aggregate observations, source provenance, and how to read the figures |
| docs/evidence/README.md | Data manifest for the two committed CSV extracts |
| docs/evidence/frozen-r2-outcomes.csv | Four-arm, 36-cell frozen synthetic outcome extract |
| docs/evidence/crm-v21-008-stress-rounds.csv | Twelve-round deterministic CRM stress extract |
| docs/assets/evidence-r2-outcomes-rmb.svg | Exact aggregate R2 observations in the requested palette |
| docs/WORKFLOW.md | Current v0.3 flow, CRM target flow, and failure boundaries |
| docs/assets/v030-workflow-rmb.svg | Dual-swimlane workflow visual |

README.md, docs/EVALUATION.md, and docs/ROADMAP.md act only as entry points;
they must link to the canonical evidence and workflow pages rather than
duplicate every metric.

## Visual language

The figures use a white, paper-like background and the verified restrained
palette from the frozen R2 renderer:

- dark red #A44742 for loss-aware or research-only paths;
- ink green #1F6B5B for valid/current publication paths;
- warm paper gray #DDD6CC and muted warm gray #786F63 for references and
  neutral status;
- ink #263238 for labels and outlines.

They contain no currency symbol, denomination, price imagery, cartoon
illustration, or decorative person/object. All interpretation-relevant text
is encoded in SVG text and repeated in adjacent Markdown.

## Current workflow truth

The solid lane documents the existing v0.3 contract:

Journal / Delta → fenced claim → compile → optional audit →
TX_PUBLISH_SNAPSHOT / CAS → committed Snapshot.

The standard CompactionWorker has not yet called reorganize_capsules();
normal publishing therefore supplies an empty reorganization-record tuple.
CAS loss is a SUPERSEDED candidate outcome; ledger-integrity failure rolls
back the transaction and cannot move the active pointer.

The dashed CRM lane is a research target only. It shows the intended
atomize/compose/matrix/optimize/gate/swap sequence, but must not be drawn or
described as a live worker capability. Summary remains an offline comparison
arm and is not a runtime fallback.

## Acceptance criteria

1. Every numerical observation is traceable to a committed CSV extract and a
   full source SHA-256 in docs/EVIDENCE.md.
2. Every source and figure is labelled synthetic, deterministic, research-only,
   or current-v0.3 as appropriate.
3. Both SVGs parse as XML, use the declared palette for semantic marks plus
   white/pale-background tints, and contain the required status labels.
4. Markdown links resolve from README, evaluation, and roadmap entry points.
5. No document claims semantic evaluation, production readiness, or a
   worker-integrated CRM/reorganization path that does not exist.

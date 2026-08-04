# Evidence data manifest

This directory contains bounded aggregate extracts for documentation. The
extracts deliberately exclude raw conversations, blinded prompts/responses,
and nonessential intermediate artifacts.

| Extract | Rows | Source status | Source location | Source SHA-256 |
| --- | ---: | --- | --- | --- |
| frozen-r2-outcomes.csv | 4 arms | Frozen synthetic 12-scenario × 3-trial aggregate; research-only | D:\bun\tmp\codex\AstrContinuum-evidence-repair-r2\data\results.json | E3394C2D8590BCFC4206B322D612DB2BE33DE1754715A9E144AB60283E1E0215 |
| crm-v21-008-stress-rounds.csv | 12 rounds | Deterministic replay-matching CRM stress record; research-only | D:\bun\tmp\codex\AstrContinuum-capsule-replacement-r3\evidence\v21-008-stress-data.json at af835babbd1ca07619251f83c4e0201264975a49 | 0C8BDC214588AA53F06DA830181D7EB9FF5D164063E8EC8FB74F75B7D0651760 |

Both sources are intentionally outside the v0.3 production worktree. They
are admitted here only as isolated experiment documentation; neither is
semantic model evidence, a Provider benchmark, or a v0.3 release gate.

For the R2 visual-language provenance, the frozen renderer SVG is
D:\bun\tmp\codex\AstrContinuum-evidence-repair-r2\figures\fig1_evidence_repair.svg
with SHA-256
F50B090888EFEE0A059F9C2593480F0E8FA1879B03E53B12B6E7DFB8C565D2FD.
It establishes the restrained dark-red/dark-green/warm-gray palette used by
the committed SVGs; it is not copied here as a new production result.

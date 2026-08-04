# Evidence data manifest

English | [简体中文](README.zh-CN.md)

This directory contains bounded aggregate extracts used by the public
documentation. It deliberately excludes raw conversations, blinded prompts and
responses, and nonessential intermediate artifacts.

| Extract | Records | Provenance scope | Immutable source identity | Committed extract SHA-256 |
| --- | ---: | --- | --- | --- |
| frozen-r2-outcomes.csv | 4 arms | Frozen synthetic 12-scenario × 3-trial aggregate; research-only | results.json; source SHA-256 E3394C2D8590BCFC4206B322D612DB2BE33DE1754715A9E144AB60283E1E0215 | 415B52EF538B2F7B76CA6815C45BC2C2F6580CC50BD9C16798D83296E8918B7F |
| crm-v21-008-stress-rounds.csv | 12 rounds | Deterministic replay-matching CRM stress record; research-only | revision af835babbd1ca07619251f83c4e0201264975a49; source SHA-256 0C8BDC214588AA53F06DA830181D7EB9FF5D164063E8EC8FB74F75B7D0651760 | 645943F5738320295580CAA62BC228694267251EF8AA01BFA13ECC5C2FFF2A01 |

Both sources are intentionally isolated from the v0.3 production worktree.
They are admitted here only as experiment documentation; neither is semantic
model evidence, a Provider benchmark, or a v0.3 release gate. The committed
extracts allow readers to recompute the figures and aggregates in this
repository, but do not recreate the withheld raw research inputs.

The SVG visual language is derived from a frozen renderer identified by SHA-256
F50B090888EFEE0A059F9C2593480F0E8FA1879B03E53B12B6E7DFB8C565D2FD.
It establishes the restrained dark-red, ink-green, warm-gray palette used by
the committed SVGs. It is provenance for the visual language, not a new
production result.

See [the evidence summary](../EVIDENCE.md) for interpretation boundaries.

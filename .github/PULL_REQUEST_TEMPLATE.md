## Summary / 变更摘要

<!-- Explain the problem and the resulting behavior. / 说明问题与变更后的行为。 -->

## Risk and architecture impact / 风险与架构影响

<!--
List affected INV-* entries, hooks, schemas, migrations, state transitions, and failure
boundaries. Write "None" only after checking docs/TEST_MATRIX.md.
列出受影响的 INV-*、Hook、Schema、迁移、状态迁移与故障边界。核对测试矩阵后才能写 None。
-->

## Verification evidence / 验证证据

<!-- Include exact commands, results, AstrBot versions, platforms/adapters, and durable-row assertions. -->

- [ ] `uv run ruff check .`
- [ ] `uv run ruff format --check .`
- [ ] `uv run mypy astrcontinuum main.py`
- [ ] `uv run pytest -q`
- [ ] `_conf_schema.json` and `metadata.yaml` validate
- [ ] Tested through a real `AstrBot/data/plugins` installation when host integration changed
- [ ] No undeclared AstrBot framework API or unsupported adapter claim was introduced
- [ ] Durable migrations, rollback, concurrency, and crash behavior are covered when applicable
- [ ] English-first and Simplified Chinese documentation remain consistent
- [ ] No conversation content, secrets, database files, logs, or local caches are included

## Compatibility / 兼容性

AstrBot version(s):

Python version(s):

Adapter/platform:

Operating system:

## Rollback / 回滚

<!-- Describe safe rollback and old-data handling. / 说明安全回滚与旧数据处理方式。 -->

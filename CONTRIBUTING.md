# Contributing to AstrContinuum

English | [简体中文](#简体中文)

Thank you for helping improve AstrContinuum. This plugin changes how conversation state is
captured, assembled, temporarily projected, restored, and eventually compacted. A change that
looks local can therefore affect durable history or AstrBot's native request graph. Please read
[Architecture](./docs/ARCHITECTURE.md) and the relevant ADRs before changing runtime behavior.

## Before opening an issue

- Search existing issues and discussions.
- Remove message content, tokens, account identifiers, database rows, and other private data
  from logs.
- For a bug, record the AstrContinuum version/commit, AstrBot version, Python version, operating
  system, adapter/platform, and a minimal reproduction.
- Use [private vulnerability reporting](./SECURITY.md) for security-sensitive findings.

## Development setup

AstrContinuum supports Python `>=3.10`; CI verifies Python 3.10 through 3.13 on Linux and
Python 3.12 on Windows.

```bash
git clone https://github.com/2718labs/astrbot_plugin_astrcontinuum
cd astrbot_plugin_astrcontinuum
uv sync --frozen --extra dev
```

Run the complete local gate:

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy astrcontinuum main.py
uv run pytest -q
```

Also load the plugin through a real AstrBot `PluginManager` when changing `main.py`, metadata,
configuration, hook decorators, request projection, or AstrBot adapter code.

## Branches, commits, and pull requests

- Branch from the latest `main`.
- Keep one coherent purpose per pull request.
- Prefer Conventional Commit subjects such as `fix:`, `feat:`, `docs:`, `test:`, or `chore:`.
- Update tests and documentation in the same pull request as behavior changes.
- Complete the pull request checklist and include exact verification commands and results.
- Do not include generated databases, logs, provider payloads, caches, local configuration, or
  secrets.

## Architecture-change evidence

A pull request changes architecture if it alters any durable schema, event authority, session
identity, hook ownership/priority, projection/restoration behavior, budget semantics, Snapshot
coverage, compaction state transition, lease/fencing behavior, or publication transaction.

Such a pull request must include:

1. **Affected invariants:** identify the `INV-*` rows in
   [Test matrix](./docs/TEST_MATRIX.md) and explain whether each remains unchanged.
2. **Durable compatibility:** provide a forward migration, rollback/recovery behavior, and the
   treatment of existing Journal/Snapshot/job rows.
3. **Failure semantics:** state the outcome at every exception and process-loss boundary.
4. **Concurrency evidence:** test stale leases, duplicate callbacks, competing publications, or
   other races affected by the change.
5. **AstrBot evidence:** identify the real host versions and adapters exercised.
6. **Documentation:** update architecture, data-flow, schema, ADR, configuration, and changelog
   material as applicable.

No test may prove correctness only from logs or return values when the behavior has a durable
database outcome. Assert the rows, pointer, coverage, and state transition.

## Non-negotiable runtime rules

- Live request hooks never wait for compaction, provider-backed audit, long network calls, or
  database-wide maintenance.
- The Journal is append-only and records only authoritative source-hook mappings.
- Readers see one committed Snapshot plus a contiguous post-coverage Delta.
- Candidate content is invisible until atomic publication succeeds.
- Projection appends only AstrContinuum-owned temporary objects.
- Restoration preserves AstrBot's exact native object identities and provider-added Delta.
- Missing private AstrBot capabilities cause fail-open degradation, never fabricated success.
- Logs contain codes and bounded metadata, not conversation content or object representations.

## Dependency changes

Runtime dependencies belong in both `requirements.txt` (AstrBot installation) and
`pyproject.toml` (local/project installation). Development-only tools belong in the `dev` extra.
Regenerate `uv.lock`, explain why the dependency is needed, and keep version bounds intentional.
Tokenizer changes must preserve the shared `tiktoken>=0.12,<0.14` contract and the pinned
offline-asset size/SHA-256 records in `THIRD_PARTY_NOTICES.md`; runtime downloads and mutable
tokenizer caches are not accepted.

## Documentation

The default landing page and primary technical documentation are English-first. When changing
README or architecture claims, update the linked Simplified Chinese mirror in the same pull
request. Code symbols, field names, states, and invariant identifiers must remain exact in both
languages.

## Release contract

`v0.2.1` has a deterministic AstrBot package contract. Before a release decision:

- run the frozen quality gate and the dedicated release tests;
- run the vendored 2718lab validator on the tracked tree and unpacked archive;
- probe the public AstrBot Provider/ProviderRequest path on `4.24.0` and `4.26.7`;
- build with `scripts/build_plugin_archive.py` and verify with
  `scripts/verify_plugin_archive.py`;
- confirm that the archive has one `astrbot_plugin_astrcontinuum/` top-level directory, only
  allowlisted files, pinned tokenizer assets, and a size below 16 MiB.

AstrBot-market distribution is a separate maintainer process. The CI archive job does not submit
to the market, create tags, or publish remote releases.

## 简体中文

感谢你参与 AstrContinuum。这个插件会影响会话事件如何落盘、组装、临时投影、恢复并最终
压缩，因此看似局部的修改也可能破坏持久历史或 AstrBot 原生请求对象。修改运行时前，请先
阅读[中文架构文档](./docs/ARCHITECTURE.zh-CN.md)和相关 ADR。

### 开发环境

```bash
git clone https://github.com/2718labs/astrbot_plugin_astrcontinuum
cd astrbot_plugin_astrcontinuum
uv sync --frozen --extra dev
uv run ruff check .
uv run ruff format --check .
uv run mypy astrcontinuum main.py
uv run pytest -q
```

凡是修改 `main.py`、元数据、配置、Hook、请求投影或 AstrBot 适配层，还必须通过真实
AstrBot `PluginManager` 加载验证。

### 架构变更必须提交的证据

以下任一内容变化都属于架构变更：持久化 Schema、事件权威来源、会话身份、Hook
职责/优先级、投影与恢复、预算语义、Snapshot 覆盖、压缩状态迁移、租约/fencing 或发布事务。

对应 PR 必须说明：

1. 影响了[测试矩阵](./docs/TEST_MATRIX.md)中的哪些 `INV-*`；
2. 旧数据的迁移、回滚和恢复策略；
3. 异常与进程崩溃边界下的持久结果；
4. 重复回调、并发发布冲突、过期租约等并发证据；
5. 验证过的真实 AstrBot 版本和适配器；
6. 同步更新的架构、数据流、数据库、ADR、配置和 Changelog 文档。

只看日志或返回值不算持久化正确性证据；必须断言数据库行、活动指针、覆盖范围和状态迁移。

### 发布规则

`v0.2.1` 具有确定性的 AstrBot 安装包契约。发布决策前必须通过
冻结依赖的完整质量门、发布契约测试、2718lab 校验器、AstrBot `4.24.0`/`4.26.7` 公共
Provider 探针，以及归档 allowlist、固定 tokenizer 资产和 `<16 MiB` 门禁。市场提交、稳定
标签或 GitHub Release 由维护者决定；AstrBot 市场分发是独立流程，CI 不会自动执行这些动作。

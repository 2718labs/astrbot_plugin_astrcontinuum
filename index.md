# Capsule Replacement R3 Work Index

## Shared Contracts

- `contracts/crm-experiment-design.md`
- `contracts/python-toolchain.md`
- `contracts/crm-data-model.md`

## Tasks

- `tasks/spec-001.md`: done
- `tasks/plan-001.md`: done
- `tasks/impl-001.md`: done
- `tasks/impl-002.md`: done
- `tasks/impl-003.md`: done
- `tasks/impl-004.md`: done
- `tasks/impl-005.md`: done
- `tasks/tooling-001.md`: done
- `tasks/impl-006.md`: done
- `tasks/impl-007.md`: done
- `tasks/impl-008.md`: done
- `tasks/impl-008a.md`: done
- `tasks/impl-008b.md`: done
- `tasks/impl-009.md`: held until Protocol v2 evidence exists
- `tasks/arch-001.md`: done (`cd5eaa5808f40bf942dfb31b4640041a8b77b229`)
- `tasks/arch-002.md`: completion candidate; all local, Luna, Spark, and Sol
  gates green; DevKit completion pending
- `tasks/arch-003.md`: DevKit-registered placeholder; card materializes on
  dispatch; depends on `arch-002`
- `tasks/protocol-v2.md`: DevKit-registered placeholder; card materializes on
  dispatch; depends on `arch-003`

## Dispatch

- 当前 wave：`arch-002`
- 写入冲突：无；始终仅允许一个实现写入代理。
- 生产仓库：只读，不修改。
- 下一门禁：精确 resident 字节、逻辑/物理分层与 kernel admission
  通过后，才实现冻结态可逆 packing；Protocol v2 与 `impl-009` 报告均不得
  越过三个架构任务。

## Runtime State

- 设计 workflow：`astrcontinuum-capsule-replacement-r3-spec-20260730`
- 实现 workflow：`astrcontinuum-capsule-replacement-r3-impl-20260730`
- 权威任务：`arch-002`
- 模式：独立实验实现；生产仓库只读。

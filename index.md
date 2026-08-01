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
- `tasks/arch-002.md`: done (`a7f1ba30d29cacbe52f843a694fea827cdfb58a9`)
- `tasks/arch-003.md`: done; bounded source optimizer, atomic recomposition,
  physical packing and release accounting accepted
- `tasks/protocol-v2.md`: DevKit-registered placeholder; card materializes on
  dispatch; depends on `arch-003`

## Dispatch

- 当前 wave：`arch-003` final ledger close
- 写入冲突：无；始终仅允许一个实现写入代理。
- 生产仓库：只读，不修改。
- 下一门禁：精确 resident 字节、逻辑/物理分层与 kernel admission
  通过后，才实现冻结态可逆 packing；Protocol v2 与 `impl-009` 报告均不得
  越过三个架构任务。

## Runtime State

- 设计 workflow：`astrcontinuum-capsule-replacement-r3-spec-20260730`
- 实现 workflow：`astrcontinuum-capsule-replacement-r3-impl-20260730`
- 权威任务：`arch-003` complete; Protocol v2 remains unimplemented
- 模式：独立实验实现；生产仓库只读。

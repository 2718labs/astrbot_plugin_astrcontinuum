# Codex 任务清单

## Phase 0：侦察
- [ ] 核对 AstrBot 插件 API、hook 顺序与锁语义
- [ ] 核对上下文持久化、Provider 和 tokenizer 接口
- [ ] 核对 Sylanne 稳定公共 API
- [ ] 写 ADR、风险和差距报告

## Phase 1：基础一致性
- [ ] SQLite Event Journal、WAL、migration
- [ ] Snapshot Store 与 atomic pointer
- [ ] Job lease 与 crash recovery
- [ ] Session key normalization

## Phase 2：主链路
- [ ] Real token counter
- [ ] Snapshot + Delta assembler
- [ ] Turn boundary preservation
- [ ] Emergency assembly
- [ ] Assembly Trace 和 P95 benchmark

## Phase 3：压缩
- [ ] Episode segmenter
- [ ] Exact anchor extractor
- [ ] Schema-constrained compiler
- [ ] Capsule merger 与 decision conflict resolver
- [ ] Dependency graph
- [ ] Loss auditor

## Phase 4：恢复
- [ ] Hybrid retrieval
- [ ] Query reference classifier
- [ ] Resolution selector
- [ ] Evidence Source Trace
- [ ] Tool artifact retrieval

## Phase 5：集成与发布
- [ ] AstrBot hooks
- [ ] custom compressor failsafe
- [ ] Sylanne adapter
- [ ] Commands / Web Inspector
- [ ] Million-token benchmark
- [ ] 30-second nonblocking demo
- [ ] Crash and decision reversal demos

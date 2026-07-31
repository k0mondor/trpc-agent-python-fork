# Replay Consistency Design Note

## 设计说明（150–300 字）

框架以轨迹驱动 InMemory、SQLite 和可选 Redis，Adapter 封装写入、重启及多 session 快照。归一化仅处理时间戳、自动 ID、字段顺序和摘要空白；state、工具 call ID 及 summary 归属、版本、覆盖关系严格比较，禁止加入 allowed-diff。白名单须说明原因并限定后端。10 条公开轨迹验证正常快照和精确路径注入，统计检出率与误报率；扩展轨迹覆盖 memory observation、摘要后重启和异常恢复。SQLite/Redis 可绕过 SDK 注入存储污染，schema v5 报告逐 case 结论、两端值、定位信息和六项验收证据。

## Issue 要求覆盖

| 原要求 | 对应轨迹或机制 | 报告证据 |
| --- | --- | --- |
| 单轮普通对话 | `single_turn_event_author_injection` | 精确到 `session.events[0].author` |
| 多轮对话 | `multi_turn_event_text_injection` | 精确 event index 与文本值 |
| 工具调用 | `tool_call_name_injection` | function call name 与 call ID 保留 |
| state 多次更新/覆盖 | `state_value_injection`、`runtime_state_corruption_fault` | 最终 state 字段路径 |
| memory 写入/读取 | `memory_result_loss_injection`、跨 session/user 扩展轨迹 | query、session alias、step index |
| summary 生成/更新 | `summary_text_injection`、`summary_version_injection` | 内容与 lineage 分开比较 |
| summary 与事件截断 | `summary_text_injection`、`restart_mid_replay_after_summary` | summary、历史/保留/后续事件共同快照 |
| 异常恢复 | duplicate、partial failure、raw SQLite/Redis corruption | 重复、丢失、脏 state/summary 精确差异 |
| 轻量/集成模式 | InMemory-only；默认 InMemory+SQLite；环境变量启用 Redis | `meta.supported_modes` 与 `backend_statuses` |

## 三个方案对比

对比口径为本仓库的原 PR 第一版、已经合并的 PR #178 方案与当前 v2；“部分”表示有基础覆盖，但未形成当前版本的独立验收证据。

| 能力 | 原 PR 第一版 | 已合并方案 | 当前 v2 |
| --- | --- | --- | --- |
| Adapter 与后端接入 | 强 | 有 | 保留并明确生命周期协议 |
| 持久化关闭/重开后读回 | 有 | 部分 | SQLite/Redis 每 case 固定执行 |
| summary 内容与 lineage | 强 | 部分 | 内容可归一化，归属/版本/覆盖严格比较 |
| 多 session / 多 user | 有 | 部分 | 全 alias 快照与隔离轨迹 |
| memory observation | 逐步保留 | 结果快照为主 | query + alias + step index，不被后读回填 |
| 10 条注入、误报及 summary 指标 | 有 | 部分 | 逐 case 状态 + AC2/AC3/AC4 精确指标 |
| 模块拆分与组件单测 | 较集中 | 强 | 采用 merged 的职责拆分并补契约测试 |
| JSON Schema | 无独立稳定契约 | 有 | schema v5，覆盖顶层结论与六项 AC |
| allowed-diff 治理 | 基础 | 强 | 原因、后端对、通配限制、比例上限、lineage 禁入 |
| 原始存储注入 | 无 | 有 | SQLite 默认验证，Redis 环境开启后验证 |
| SDK 修改隔离 | 与框架混合 | tests-only | SDK 修复与 tests-only 框架分为独立提交 |

当前 v2 的优势不是增加更多宽松归一化，而是组合两边长处：保留第一版对真实生命周期和复杂语义的覆盖，同时采用 merged 方案更容易审查、扩展和维护的工程结构。机器报告直接回答“是否达标、哪条未达标、证据在哪里”，避免只能人工翻阅 diff 数组。

## 运行方式

- InMemory-only：运行 `test_replay_inmemory_only_lightweight_mode`，不依赖数据库或网络。
- 默认轻量模式：运行 `test_replay_consistency_smoke_cases`，比较 InMemory 与临时文件 SQLite，目标小于 30 秒。
- Redis 集成模式：设置 `TRPC_AGENT_REPLAY_REDIS_URL` 后运行 Redis integration 测试；未设置时明确 skip。
- 报告：`session_memory_summary_diff_report.json`；契约：`replay_report.schema.json`。

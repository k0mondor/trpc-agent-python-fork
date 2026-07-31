# Replay Consistency Design

## 设计说明（150–300 字）

框架用统一 `ReplayCase` 驱动 InMemory、SQLite 和可选 Redis，Adapter 负责生命周期、持久化重启与快照读取。归一化只处理时间戳、自动 ID、字段顺序和摘要空白；state、工具 call ID 以及 summary 归属、版本、覆盖关系严格比较。allowed-diff 必须说明原因并限定后端，且不能屏蔽 summary lineage。公开轨迹分别执行正常比较和精确故障注入，扩展轨迹覆盖多 session、memory observation、摘要后重启和异常恢复。schema v5 报告给出逐 case 状态、左右值、定位信息及六项验收结论。

20 条轨迹的逐项输入、故障和预期结果见 [replay_cases.md](replay_cases.md)。

## 三个方案比较

| 维度 | 原 PR 第一版 | 已合并 PR #178 | 当前 v2 |
| --- | --- | --- | --- |
| 整体结构 | 能力完整但职责集中 | 模块拆分清楚 | merged 分层 + 独立 Adapter 生命周期 |
| 持久化验证 | 支持重启读回 | 基础后端比较 | SQLite/Redis 在快照前固定重启 |
| Summary | 内容、版本与 lineage 覆盖较深 | 内容及基础元数据 | 内容可归一化；归属、版本、覆盖严格比较 |
| Session/Memory | 多 session、逐步 observation | 主 session 快照为主 | 全 alias、多 user、query + alias + step index |
| 差异治理 | 基础 allowed-diff | schema、白名单、原始注入较强 | 保留 merged 优点并禁止 lineage 白名单 |
| 验收证据 | 有精确检出指标 | 有报告，指标较基础 | 逐 case 状态 + AC1–AC6 机器可读结果 |
| SDK 修改 | 与测试框架混合 | tests-only | SDK 修复、框架、报告分别提交 |

## 验收结果

数据来自 [session_memory_summary_diff_report.json](session_memory_summary_diff_report.json)。

| 指标 | Issue 门槛 | 当前结果 | 状态 |
| --- | --- | --- | --- |
| 支持后端 | InMemory + 至少一个持久化/模拟后端 | InMemory + 文件 SQLite；Redis 环境变量可选 | 通过 |
| 公开注入检出率 | 10 条、100% | 10/10，100% | 通过 |
| 正常 case 误报率 | ≤ 5% | 0/10，0% | 通过 |
| Summary 三类故障 | 丢失、覆盖、归属均为 100% | 3/3，100% | 通过 |
| 差异定位完整率 | session/path/两端值及 event 或 summary 标识齐全 | 17/17，100% | 通过 |
| 轻量模式时间 | ≤ 30 秒 | 3.086 秒 | 通过 |
| 全部报告 case | 无非预期差异 | 20/20 通过，unexpected diff = 0 | 通过 |
| 报告契约 | 可稳定解析 | JSON Schema v5 校验通过 | 通过 |

## 运行模式

| 模式 | 后端 | 外部依赖 |
| --- | --- | --- |
| InMemory-only | InMemory | 无 |
| 默认轻量 | InMemory + 临时文件 SQLite | 无 |
| 集成 | InMemory + SQLite + Redis | `TRPC_AGENT_REPLAY_REDIS_URL` |

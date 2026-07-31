# Replay Consistency Design Note

## 设计说明
框架用统一 `ReplayCase` 驱动 InMemory、文件型 SQLite 与可选 Redis，Adapter 负责服务生命周期、持久化重启和多 session 快照；归一化、比较、allowed-diff 治理、报告和原始存储注入拆为独立模块。快照只投影业务字段并保留工具 call ID；state 字符串严格比较，memory 文本与顺序做确定性收敛。summary 同时比较文本与 `session_id/summary_id/version/replaces` lineage，后四项禁止加入白名单。allowed diff 必须有理由，可限定 backend pair，仅允许列表下标通配，每 case 最多 8 条且实际放行字段不超过 10%。公开 10 条轨迹各回放一次，先比较原始快照统计误报率，再在副本注入精确路径漂移统计检出率；SQLite/Redis 抓取前关闭并重开服务。扩展测试覆盖 runtime fault、非活跃 session、多 user、memory observation，以及绕过 SDK 直接修改 SQLite/Redis 的真实存储污染。schema v4 报告记录字段路径、两端值、定位信息、运行上下文和质量指标。

## 官方 10 条验收 Case
| Case ID | 场景 | 验收点 |
| --- | --- | --- |
| `single_turn_event_author_injection` | 单轮普通对话 | event author 漂移 |
| `multi_turn_event_text_injection` | 多轮对话 | 指定 event index 文本漂移 |
| `tool_call_name_injection` | 工具调用对话 | function call 名称漂移 |
| `state_value_injection` | state 多次覆盖 | state 最终值漂移 |
| `memory_result_loss_injection` | memory 写入和检索 | memory 结果丢失 |
| `summary_text_injection` | summary 与事件截断 | summary 内容漂移 |
| `summary_version_injection` | summary 更新 | version 回退 |
| `summary_binding_mismatch_injection` | summary 归属错误 | `summary.session_id` 检出 |
| `summary_missing_injection` | summary 丢失 | `summary` 缺失检出 |
| `summary_lineage_corruption_injection` | summary 覆盖错误 | `summary.replaces` 检出 |

## 扩展 Case
- `duplicate_event_runtime_fault`：补充重复写入异常。
- `runtime_state_corruption_fault`：补充运行时 state 污染。
- `runtime_summary_loss_fault`：补充运行时 summary 丢失。
- `runtime_summary_overwrite_fault`：补充运行时 summary 覆盖关系污染。
- `partial_failure_event_loss_fault`：补充中途失败导致事件丢失。
- `non_active_session_summary_loss_fault`：补充非活跃 session 的 summary 损坏检测。
- `cross_session_memory_aggregation`：补充同一 app/user 下跨 session 的 memory 聚合语义。
- `restart_mid_replay_after_summary`：补充 summary 持久化后中途重启再续写的恢复语义。
- `state_namespace_roundtrip`：补充 `app:/user:/temp:` 状态命名空间在跨 session 和重启后的可见性语义。
- `cross_user_memory_isolation`：补充同一 app 下不同 user 的长期记忆隔离语义。
- `duplicate_memory_query_name_across_sessions`：用定向测试覆盖跨 alias 的同名 memory query 不应互相覆盖。
- `memory_query_observation_survives_restart`：用定向测试覆盖重启后 memory query 观测不得被后续结果回填。

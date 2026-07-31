# Replay Cases

框架生成的主报告固定包含 20 条 case：前 10 条用于 issue 的检出率和误报率验收，后 10 条补充异常恢复、持久化重启、跨 session/user 语义。每条 case 都用同一输入轨迹驱动各后端；“注入路径”为期望出现的精确差异，`无差异` 表示正常后端必须一致。

## 公开验收 Case（10 条）

| # | Case ID | 输入轨迹 | 注入或检查点 | 预期路径 |
| --- | --- | --- | --- | --- |
| 1 | `single_turn_event_author_injection` | user 输入后追加 assistant 文本 | 修改首个事件 author | `session.events[0].author` |
| 2 | `multi_turn_event_text_injection` | 连续两轮 user/assistant | 修改第三个事件文本，验证顺序和 index | `session.events[2].text` |
| 3 | `tool_call_name_injection` | user 请求、function call、function response | 修改工具名，call ID 仍参与比较 | `session.events[1].function_calls[0].name` |
| 4 | `state_value_injection` | state 初值后两次覆盖 preference | 修改最终 state 值 | `state.preference` |
| 5 | `memory_result_loss_injection` | 对话写入 memory 后按偏好检索 | 清空指定 step 的检索结果 | `memory.step_006:default:preference_search.entries.length` |
| 6 | `summary_text_injection` | 长对话生成 summary 并保留最近事件 | 替换摘要正文 | `summary.summary_text` |
| 7 | `summary_version_injection` | 生成 v1，追加事件后生成 v2 | 将最终版本回退到 v1 | `summary.version` |
| 8 | `summary_binding_mismatch_injection` | 对话压缩并保存 summary | 将摘要绑定到错误 session | `summary.session_id` |
| 9 | `summary_missing_injection` | 正常生成并保存 summary | 删除整个摘要 | `summary` |
| 10 | `summary_lineage_corruption_injection` | 连续生成两版 summary | 破坏新摘要的 replaces 指向 | `summary.replaces` |

## 扩展一致性 Case（10 条）

| # | Case ID | 输入轨迹 | 注入或检查点 | 预期路径/结果 |
| --- | --- | --- | --- | --- |
| 11 | `duplicate_event_runtime_fault` | 正常追加多轮事件 | 运行中重复写入最后事件 | `session.events.length` |
| 12 | `runtime_state_corruption_fault` | 多次更新同一 state | 运行中污染 preference | `state.preference` |
| 13 | `runtime_summary_loss_fault` | 生成 summary 后继续轨迹 | 运行中删除摘要 | `summary` |
| 14 | `runtime_summary_overwrite_fault` | 生成并更新 summary | 同时污染业务 lineage 与 metadata lineage | `summary.replaces`、`summary.metadata.replaces` |
| 15 | `partial_failure_event_loss_fault` | event 同时携带 state delta | 丢失最后事件但保留已写 state | `session.events.length` |
| 16 | `non_active_session_summary_loss_fault` | 创建 source 和 default 两个 session | 删除非活跃 source 的 summary | `sessions_by_alias.source.summary` |
| 17 | `cross_session_memory_aggregation` | source 保存记忆，default 检索 | 同 app/user 下跨 session 可见 | 无差异 |
| 18 | `restart_mid_replay_after_summary` | 生成 summary、重启服务、继续追加 | 验证摘要与后续事件均可恢复 | 无差异 |
| 19 | `state_namespace_roundtrip` | 跨 session 写 app/user/session/temp state 并重启 | 验证各 namespace 的可见范围 | 无差异 |
| 20 | `cross_user_memory_isolation` | user A 保存 memory，user B 检索 | 验证同 app 下用户隔离 | 无差异 |

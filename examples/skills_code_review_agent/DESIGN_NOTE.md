# 方案设计说明

本方案将自动代码评审拆为“主流程编排 + 可复用 Skill + 受控执行 + 结构化落库”四层。主流程由 `agent/agent.py` 负责，统一接收 diff、repo path 或 fixture，完成输入归一化、diff 解析、规则执行、Filter 决策、skill 脚本调度、报告生成和 SQLite 持久化。`skills/code-review/` 则承载正式的 `code-review` Skill，包括 `SKILL.md`、规则文档、使用文档、脚本契约与三个确定性脚本，用于承接可复用的评审知识与脚本执行面。

沙箱隔离策略采用“框架 `skill_run` 托管 + 显式本地回退”的实现方式。生产默认的 `container` 路径只提交结构化 tool payload，由 `SkillToolSet` 负责 Skill staging、workspace 创建、输入映射、runtime 执行与超时，不再由 Agent 手工创建 workspace 或启动容器命令。diff 文件通过 `inputs` 映射进入工作区，命令仅允许固定的 `python` 与仓库内脚本，避免路径拼接和 shell 注入。`local` 仅用于 dry-run/fake-model 开发验证，采用预解析脚本、argv 调用、剔除宿主 `PATH`/`PYTHONPATH` 的最小环境、超时和输出限制，并明确记录为非隔离运行；Filter 会同时检查 argv 与实际待执行脚本内容，未接入 resolver 的 `cube`、`e2b` 则转入人工复核。脚本失败或超时会写入 `sandbox_runs` 并转换为结构化 finding；主流程异常会生成 `FAILED` 任务、错误报告和 SQLite 审计记录。

数据库 schema 采用最小可查询设计，包含 `review_tasks`、`review_inputs`、`filter_decisions`、`sandbox_runs`、`findings` 和 `review_reports` 六张表，支持按 `task_id` 查询完整审查链路。报告输出同时生成 JSON 与 Markdown，两者都包含 findings 摘要、人工复核项、Filter 摘要、sandbox 摘要和监控指标。监控字段聚合总耗时、severity/category 分布、拦截次数和 sandbox 次数，便于回放和评测。

去重与降噪通过 `deduper.py` 实现：同类同文件同位置同证据的 finding 会被合并，低置信结果自动降级到 `needs_human_review` 或 `warning`。安全边界通过统一 `redactor.py` 落实，确保 API key、token、password、Bearer token 和私钥内容在报告与数据库中不出现明文。整体设计优先满足验收中的可验证性、可运行性、可审计性和 dry-run 可用性，并保持 runtime resolver、SQL 后端和模型审查器的扩展边界。

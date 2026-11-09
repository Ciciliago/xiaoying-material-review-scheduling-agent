# 验证与结果

自动化测试命令为 `python3 -m pytest -q`。本次提交前的实际结果为 [`examples/test-results.txt`](../examples/test-results.txt) 中记录的 `18 passed`。

## 真实模型动态决策 Trace

以下历史 Trace 的文件内容可核对 `mode=live`、模型版本、UTC 时间、Action、工具参数、Observation、下一动作与最终结果。它们声称由用户在本地 DeepSeek `deepseek-flash`（OpenAI-compatible Tool Calling）环境生成；当前仓库没有原始模型响应或服务商请求 ID，无法独立验证其运行来源，状态为 **NOT VERIFIED**。不应将其视为可自动复现的在线验收证据。

- 时段冲突后调整：输入 [`conflict.json`](../examples/inputs/conflict.json)，输出 [`conflict.live.json`](../examples/outputs/conflict.live.json)，Trace [`conflict.live.deepseek.json`](../examples/traces/conflict.live.deepseek.json)。模型在 18:00 冲突后，选择工具返回的 19:00，保存 `pending_confirmation` 草稿；最终为 `needs_confirmation`，从未发布。
- 素材信息不足后阻断：输入 [`missing_info.json`](../examples/inputs/missing_info.json)，输出 [`missing_info.live.json`](../examples/outputs/missing_info.live.json)，Trace [`missing_info.live.deepseek.json`](../examples/traces/missing_info.live.deepseek.json)。模型观察到缺失 `body` 和 `source` 后结束，不调用审核、时段查询或保存。

本次提交新增一条用户本地运行后提供的 DeepSeek `deepseek-chat` 审核阻断 Trace：[`review_blocked.live.deepseek.json`](../examples/traces/review_blocked.live.deepseek.json)。记录显示模型在 `get_material` 后选择 `check_rules`，观察到 `status=blocked` 后选择 `finish`，没有查询时段或保存草稿。该文件同样没有原始服务商响应或请求 ID，因此其外部调用来源仍标记为 **NOT VERIFIED**；它仅作为本地运行记录，不替代可独立复现的在线证据。

## 离线回归材料

Mock 材料只验证流程和 Runtime 约束，不宣称真实模型能力：

- 正常排期：[`normal.json`](../examples/inputs/normal.json)、[`normal.json`](../examples/outputs/normal.json)、[`normal.mock.json`](../examples/traces/normal.mock.json)。
- 审核风险阻断：[`rule_risk.json`](../examples/inputs/rule_risk.json)、[`rule_risk.mock.json`](../examples/outputs/rule_risk.mock.json)、[`rule_risk.mock.json`](../examples/traces/rule_risk.mock.json)。
- 保存失败：[`save_failure.json`](../examples/inputs/save_failure.json)、[`save_failure.json`](../examples/outputs/save_failure.json)、[`save_failure.mock.json`](../examples/traces/save_failure.mock.json)。
- 重复提交：输入 [`idempotency.json`](../examples/inputs/idempotency.json)，首次输出 [`idempotency.first.mock.json`](../examples/outputs/idempotency.first.mock.json)，重复输出 [`idempotency.repeat.mock.json`](../examples/outputs/idempotency.repeat.mock.json)，重复 Trace [`idempotency.repeat.mock.json`](../examples/traces/idempotency.repeat.mock.json)，以及仅含一条草稿的持久化状态 [`idempotency-drafts.json`](../examples/state/idempotency-drafts.json)。第二次保存返回同一 `draft_id` 且 `created=false`。

自动化测试还覆盖 hard 时间优先级、禁止替代、同一 `request_id` 的内容冲突、最大步数、未查询时段保存、规则阻断后保存、输入校验，以及模型在保存失败后声称成功时 Runtime 仍返回失败。

已知限制：Mock 不能证明真实模型决策效果；仓库中的 live Trace 仅作 **NOT VERIFIED** 的补充材料。新生成 Trace 同时输出兼容字段 `model.name` / `model.version` 与扁平字段 `model_name` / `model_version`，历史文件保持原样。JSON 草稿存储适用于单进程作业演示，不提供生产级多进程并发写入保障。

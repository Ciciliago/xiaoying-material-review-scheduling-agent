# 方案设计

执行闭环为：模型选择 Action → Runtime 校验并执行工具 → Observation 回到模型 → 模型继续或 `finish`。`src/agent.py` 不预先决定业务分支；它只运行循环、记录 Trace 和执行上限。

`src/runtime.py` 保存可信执行状态并强制以下规则：素材完整后才能审核，审核通过后才能查询时段，只有 `query_slots` 返回的可用时段才能保存；`hard` 优先于 `allow_alternative`；规则阻断、保存失败和未授权发布无法被模型覆盖。最终响应由 Runtime 根据 Observation 生成。非法工具动作安全拒绝、写入 Trace，并作为 Observation 返回模型；Trace 同时保存事实型简短决策依据，而非内部思维链。

`src/tools.py` 提供 `get_material`、`check_rules`、`query_slots`。时段冲突按时间区间而不是字符串判断。`src/store.py` 用规范化请求 hash 与 `request_id` 保护草稿幂等；相同键不同内容被拒绝。

使用原生 Python 而非 Agent 框架，原因是作业核心是展示模型决策与 Runtime 边界，而不是框架使用。live 模式使用 OpenAI-compatible Tool Calling；mock 只用于可重复的流程测试。

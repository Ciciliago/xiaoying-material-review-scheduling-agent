# 项目 1 Spec：素材审核与发布排期 Agent

> 版本：v1.1\
> 目标：在有限工程范围内，实现一个可运行、可验证的素材审核与发布排期
> Agent。\
> 项目边界：只生成和保存排期草稿，不执行真实发布。

------------------------------------------------------------------------

## 1. 项目目标

帮助内容运营人员处理待发布素材。Agent
读取素材及其元数据，检查审核规则，查询目标时段是否冲突，并在必要时根据工具返回结果调整下一步动作，最终生成可确认的排期草稿。

本项目重点验证：

1.  业务规则理解与约束处理。
2.  模型根据工具观察结果动态选择下一步动作。
3.  冲突、信息缺失、审核风险和工具异常处理。
4.  草稿保存的状态准确性与重复提交保护。
5.  使用测试和运行 Trace 证明行为符合预期。

### 1.1 非目标

本轮不实现以下内容：

-   真实发布、自动上线或调用第三方发布平台。
-   前端管理界面。
-   多 Agent 协作。
-   生产级任务调度系统。
-   复杂数据库、账号权限系统或分布式锁。
-   对审核规则进行机器学习训练。

------------------------------------------------------------------------

## 2. 用户输入与输出

### 2.1 输入

一次排期请求至少包含：

  字段                  类型        必需 说明
  --------------------- --------- ------ ---------------------------------
  `request_id`          string        是 请求唯一标识；重试时必须复用
  `material_id`         string        是 待处理素材 ID
  `target_date`         string        是 目标发布日期，格式 `YYYY-MM-DD`
  `requested_time`      string        是 用户要求的时间，格式 `HH:MM`
  `time_constraint`     enum          是 `hard` 或 `flexible`
  `allow_alternative`   boolean       是 是否允许 Agent 提出替代时段
  `user_note`           string        否 补充说明

约束语义：

-   `hard`：目标时间是硬性要求。发生冲突时不得自行更改，只能阻断并请求用户处理。
-   `flexible`：目标时间是偏好。发生冲突时可以查询并提出替代时段，但替代方案必须标记为待用户确认。
-   `allow_alternative=false`：即使时间为
    `flexible`，也不主动提出替代时间；应说明冲突并请求用户指示。
-   约束优先级：`time_constraint=hard` 优先于 `allow_alternative`；即使 `allow_alternative=true`，也不得提出或保存替代时段。
-   素材时长从素材元数据的 `duration_minutes` 获取，不由模型猜测；缺少或非法时按素材信息缺失处理。
-   日期、时间、枚举和布尔字段在进入 Agent 循环前完成格式校验；输入非法时直接返回明确错误，不消耗模型调用次数。

### 2.2 输出

每次执行返回一个结构化结果：

  ------------------------------------------------------------------------------
  字段                      类型                    说明
  ------------------------- ----------------------- ----------------------------
  `request_id`              string                  原请求 ID

  `overall_status`          enum                    `completed` /
                                                    `needs_confirmation` /
                                                    `blocked` / `incomplete` /
                                                    `failed`

  `material_id`             string                  素材 ID

  `review_status`           enum                    `passed` / `needs_info` /
                                                    `blocked` / `not_checked`

  `risk_reasons`            string\[\]              风险原因；无风险时为空数组

  `warnings`                string\[\]              非阻断性审核提示；无提示时为空数组

  `missing_fields`          string\[\]              缺失的必要素材字段

  `requested_slot`          object                  用户原始日期和时间

  `suggested_slot`          object/null             建议时段；没有建议时为 null

  `conflict_reason`         string/null             原时段冲突原因

  `confirmation_required`   boolean                 是否需要用户批准一个已提出的替代时段

  `confirmation_reason`     string/null             需要批准该替代时段的原因

  `draft_status`            enum                    `not_saved` / `saved` /
                                                    `save_failed`

  `draft_id`                string/null             成功保存的草稿 ID

  `draft_error`             string/null             草稿保存失败或幂等冲突原因

  `publication_status`      string                  固定为 `not_published`

  `message`                 string                  面向用户的简要说明
  ------------------------------------------------------------------------------

状态必须准确区分：

-   `saved` 只表示草稿保存成功，不代表排期已最终确认。
-   `needs_confirmation` 表示已经提出替代时段，正在等待用户批准。
-   `blocked` 表示当前条件下不能继续生成可执行排期。
-   `incomplete` 表示达到执行上限或流程未完成。
-   `failed` 表示关键工具失败，且未能完成目标操作。
-   任何情况下都不得返回"已发布"。

### 2.3 场景状态映射

为避免实现、测试和文档使用不同语义，核心场景固定采用以下状态：

  场景                 `overall_status`       `review_status`   `draft_status`   `confirmation_required`
  -------------------- ---------------------- ----------------- ---------------- -------------------------
  正常排期             `completed`            `passed`          `saved`          `false`
  素材信息缺失         `blocked`              `needs_info`      `not_saved`      `false`
  硬性审核风险         `blocked`              `blocked`         `not_saved`      `false`
  弹性时段冲突且有替代 `needs_confirmation`   `passed`          `saved`          `true`
  硬性时段冲突         `blocked`              `passed`          `not_saved`      `false`
  无可用替代或不允许替代 `blocked`              `passed`          `not_saved`      `false`
  保存失败             `failed`               `passed`          `save_failed`    `false`
  幂等键内容冲突       `failed`               `not_checked`     `not_saved`      `false`
  输入格式非法         `failed`               `not_checked`     `not_saved`      `false`

`confirmation_required` 只表示是否需要用户批准一个已经提出的替代时段。有替代建议时为 `true`；没有候选时段、需要补充素材或需要用户重新提出要求时为 `false`，且 `confirmation_reason=null`，这些情况通过 `overall_status` 和 `message` 表达。

非阻断性 warning 不改变 `review_status=passed`，但必须写入 `warnings` 并展示给用户。重复运行整个 Agent 可以重新执行读取、审核和时段检查，不要求缓存或复用首次最终结果；幂等保证只要求不产生重复草稿，再次调用保存工具时返回同一 `draft_id` 并标记 `created=false`。幂等键内容冲突时返回 `draft_error=idempotency_conflict` 和 `draft_id=null`，且不创建或覆盖草稿。

------------------------------------------------------------------------

## 3. 工具定义

工具可使用本地 Python 函数和合成 JSON 数据实现。

### 3.1 `get_material(material_id)`

读取素材及元数据。

建议返回：

``` json
{
  "material_id": "M01",
  "title": "新品介绍",
  "body": "产品功能介绍文案",
  "source": "品牌资料库",
  "owner": "content_team",
  "duration_minutes": 60,
  "required_fields": ["title", "body", "source", "duration_minutes"]
}
```

异常或缺失情况：

-   找不到素材：返回明确的 `not_found`。
-   必要字段缺失或 `duration_minutes` 非法：返回缺失/非法字段列表，不得伪造素材内容或时长。

### 3.2 `check_rules(material_id)`

检查素材是否满足审核规则。

建议返回：

``` json
{
  "material_id": "M01",
  "status": "passed",
  "risks": [],
  "warnings": []
}
```

`status` 枚举固定为 `passed | blocked`：

-   `blocked`：违反硬性审核规则，不允许进入排期。
-   `passed`：没有阻断性规则问题，可以继续；即使存在 warning，状态仍为 `passed`。
-   非阻断性问题只写入 `warnings`，不得将 `warning` 作为第三种 `status`；阻断原因写入 `risks`。

规则检查结果应来自工具数据或明确实现的规则，不得由模型凭空编造。

### 3.3 `query_slots(target_date, time, duration_minutes)`

查询指定时段及可用时段。

建议返回：

``` json
{
  "requested_slot": {
    "date": "2026-11-10",
    "time": "18:00"
  },
  "available": false,
  "conflict_reason": "该时段已被素材 M02 占用",
  "alternatives": [
    {"date": "2026-11-10", "time": "19:00"},
    {"date": "2026-11-10", "time": "20:00"}
  ]
}
```

要求：

-   返回结果必须来自当前排期数据。
-   替代时段必须确实可用。
-   不得把未查询的时段描述为可用。
-   如果没有可用时段，应明确返回空结果。
-   冲突按 `[start_time, start_time + duration_minutes)` 时间区间判断，不能只比较开始时间字符串。
-   每个 alternative 都必须经过同一份当前排期数据校验；工具返回结果是时段可用性的唯一事实来源。

### 3.4 `save_draft(request_id, material_id, proposed_slot)`

保存排期草稿，不触发真实发布。

建议返回：

``` json
{
  "success": true,
  "draft_id": "DRAFT-001",
  "created": true,
  "status": "pending_confirmation"
}
```

以上是暴露给模型的参数。运行时另外注入可信执行上下文，包括原始请求、素材检查结果、规则检查结果和已查询的可用时段；这些字段不能由模型覆盖。

要求：

-   保存失败必须返回失败状态及原因。
-   不得在保存失败时返回成功草稿 ID。
-   必须支持幂等：同一 `request_id` 重复调用只产生一份草稿。
-   重复调用应返回已有草稿信息，且 `created=false`。
-   同一 `request_id` 且请求内容相同，再次调用保存工具时返回已有草稿；重新运行整个 Agent 时允许重新执行前置检查，不要求建设最终结果缓存。
-   同一 `request_id` 携带任一不同的规范化请求字段时，返回 `idempotency_conflict`，不得静默复用旧结果、创建新草稿或覆盖原草稿。
-   幂等比较使用除 `request_id` 外全部规范化请求字段的稳定 hash；草稿和幂等键必须持久化，使两个独立 CLI 进程仍返回同一 `draft_id`。
-   保存失败不创建成功幂等记录；修复故障后可使用相同请求重试。若草稿已存在，应先返回已有草稿，再处理故障注入。
-   草稿记录必须保留用户原始要求、建议时段和确认状态。
-   `status` 和 `confirmation_required` 由运行时推导：原时段可用时为 `ready`；替代时段为 `pending_confirmation`。模型不能自行传入 `confirmed`。
-   `proposed_slot` 只能是当前执行中 `query_slots` 明确返回为可用的原时段或 alternative；未经查询或查询为不可用的时段必须拒绝保存。
-   工具只保存草稿，绝不发布。

------------------------------------------------------------------------

## 4. Agent 决策与执行流程

### 4.1 ReAct 要求

必须实现真实的模型决策循环：

`模型选择 Action → 执行工具 → 返回 Observation → 模型选择下一步 Action 或结束`

模型必须能够根据工具实际返回结果改变下一步动作。例如：

-   素材不存在或信息缺失：停止排期并请求补充。
-   审核规则阻断：停止，不查询或保存排期。
-   目标时段冲突且时间为 `hard`：不得擅自更改，等待用户处理。
-   目标时段冲突且时间为
    `flexible`、允许替代：根据查询结果选择替代时段，并标记待确认。
-   保存失败：向用户报告失败，不得声称保存成功。
-   工具返回的信息不足：模型可以选择补查；无法查明时停止并说明缺口。

不能用固定的 Python `if/else`
流程预先决定全部工具调用顺序，再把结果包装成模型 Trace。Python
可以负责工具执行、参数校验、权限约束、状态持久化和步数限制；下一步业务动作必须由模型根据
Observation 选择。

模型负责选择下一步工具或 `finish`，但最终结构化结果由运行时根据已验证的 Observation 和持久化结果组装。模型可以生成面向用户的简短说明，但不能覆盖 `review_status`、`draft_status`、`draft_id`、确认状态或发布状态等事实字段。

运行时维护一个轻量状态机，只限制当前允许调用的动作，不预先替模型选择下一步。例如：未读取素材时不能审核，未通过素材和规则检查时不能查询时段，未得到可用时段时不能保存。被拒绝的非法动作写入 Trace、计入步数，并将错误 Observation 返回模型，使其有机会调整动作。

模型只有在运行时已经具备 `completed`、`needs_confirmation`、`blocked` 或 `failed` 的事实依据时才能正常结束；若模型提前 `finish`，运行时返回 `incomplete`，而不是把未完成流程包装成成功。

### 4.2 建议的工具调用顺序

以下是典型路径，不是强制固定流程：

1.  模型选择 `get_material`。
2.  模型根据素材结果选择 `check_rules`，或因信息缺失而结束。
3.  规则通过后，模型选择 `query_slots`。
4.  模型根据时段查询结果决定：
    -   直接保存原时段草稿；
    -   查询替代时段；
    -   提出待确认建议；
    -   因硬性约束或无可用时段而阻断。
5.  条件满足时，模型选择 `save_draft`。
6.  模型根据保存结果准确结束，或报告保存失败。

### 4.3 硬约束

以下约束由程序层强制执行，不依赖模型自觉：

-   不允许调用真实发布接口；项目中不提供该工具。
-   `time_constraint=hard` 时，保存的时段不得与用户要求不同。
-   未经用户确认，替代时段不得标记为 `confirmed`。
-   审核结果为 `blocked` 时，不允许保存可执行排期草稿。
-   素材信息缺失或时长非法时，不允许检查时段或保存草稿。
-   只能保存 `query_slots` 在当前执行中明确验证为可用的时段。
-   保存工具返回失败时，最终 `draft_status` 必须为 `save_failed`。
-   保存工具未返回成功前，运行时不得生成非空 `draft_id` 或 `draft_status=saved`。
-   所有最终结果的 `publication_status` 固定为 `not_published`。
-   工具调用次数和总执行步数必须有上限。
-   未知工具、非法参数和违反调用前置条件的动作必须安全拒绝，不能绕过上述约束。

### 4.4 执行上限

建议配置：

``` python
MAX_STEPS = 8
MAX_TOOL_CALLS = 8
```

达到任一上限且任务尚未完成时，返回：

``` json
{
  "overall_status": "incomplete",
  "message": "已达到最大执行步数，排期尚未完成。",
  "draft_status": "not_saved",
  "publication_status": "not_published"
}
```

不得把达到上限描述为成功。

------------------------------------------------------------------------

## 5. 核心业务场景与验收标准

### Case 01：正常排期

输入：

-   素材信息完整。
-   审核规则通过。
-   用户要求时段可用。

预期：

-   `review_status=passed`。
-   建议时段等于用户要求时段。
-   草稿保存成功。
-   `draft_status=saved`。
-   `overall_status=completed`。
-   `publication_status=not_published`。

### Case 02：素材信息缺失

输入：

-   素材缺少必要字段，例如正文或来源。

预期：

-   `review_status=needs_info`。
-   明确列出缺失字段。
-   不得编造素材信息。
-   不得保存排期草稿。
-   `overall_status=blocked`。
-   `confirmation_required=false`；补充素材信息属于解除阻断，不是确认候选时段。

### Case 03：审核规则风险

输入：

-   素材触发一条硬性审核规则。

预期：

-   展示具体风险原因。
-   `review_status=blocked`。
-   不保存排期草稿。
-   不得通过调整发布时间绕过审核风险。

### Case 04：目标时段冲突

输入：

-   素材无风险。
-   用户要求 18:00。
-   18:00 已占用，19:00 可用。
-   `time_constraint=flexible` 且 `allow_alternative=true`。

预期：

-   模型观察到 18:00 冲突。
-   模型选择查询或使用工具返回的替代时段。
-   推荐 19:00。
-   `confirmation_required=true`。
-   保存 19:00 的待确认草稿，`draft_status=saved` 且草稿内部状态为 `pending_confirmation`。
-   `overall_status=needs_confirmation`。
-   不得声称 19:00 已获用户确认。

补充边界测试：

-   将 `time_constraint` 改为 `hard`，预期不得提出或保存 19:00，返回 `overall_status=blocked`、`draft_status=not_saved`、`confirmation_required=false`；由 `message` 请求用户重新提出要求。
-   将 `allow_alternative` 改为 `false`，预期不得主动替换时段，返回 `overall_status=blocked`、`draft_status=not_saved`、`confirmation_required=false`。
-   工具没有返回可用 alternative 时，不得编造时段，返回 `overall_status=blocked` 和 `suggested_slot=null`。

### Case 05：重复提交

输入：

-   对同一素材、同一排期请求，使用相同 `request_id`
    重复执行或重复调用保存工具。

预期：

-   最终只存在一份草稿。
-   第二次调用返回第一次创建的草稿 ID。
-   第二次保存工具结果为 `created=false`。
-   不产生第二条草稿记录。
-   Trace 或工具结果能证明幂等行为。
-   重新运行整个 Agent 时可以重新读取素材、检查规则和查询时段；本轮不要求缓存第一次的完整最终结果。
-   若相同 `request_id` 携带不同请求内容，必须返回 `idempotency_conflict`，且原草稿保持不变。

### Case 06：保存失败

输入：

-   前置检查通过。
-   保存工具模拟返回失败。

预期：

-   `draft_status=save_failed`。
-   `draft_id=null`。
-   `overall_status=failed`。
-   明确说明保存失败。
-   不得声称草稿已保存或已经发布。

------------------------------------------------------------------------

## 6. 测试与运行记录

### 6.1 自动化测试

至少覆盖：

-   正常排期。
-   信息缺失。
-   审核风险阻断。
-   时段冲突及替代建议。
-   硬性时间约束不被更改。
-   重复提交幂等。
-   相同 `request_id`、不同请求内容的幂等冲突。
-   保存失败。
-   最大步数限制。
-   未经查询保存时段被拒绝。
-   模型尝试在规则阻断后保存被拒绝。
-   模型尝试修改硬性时间被拒绝。
-   保存失败后模型声称成功时，最终事实字段仍为失败。

建议使用 `pytest`。测试中可注入 Mock Model 和 Mock
Tools，以稳定验证状态、工具执行和异常处理。

注意：Mock Model
测试只能证明程序流程和边界约束正确，不能证明真实模型具备动态决策能力。

### 6.2 至少两条模型动态决策 Trace

必须提供至少两条真实模型运行记录，展示模型根据不同工具结果调整动作。建议：

-   Trace A：目标时段冲突，模型选择可用替代时段，保存待确认草稿并结束。
-   Trace B：规则检查阻断或保存工具失败，模型观察结果后停止且准确报告状态。

这两条路径具有明显分叉，更能证明模型根据 Observation 调整行为；正常成功路径仍作为第三组完整验证材料提交。

每条 Trace 至少记录：

  字段                 内容
  -------------------- --------------------------------
  `step`               步骤序号
  `action`             模型选择的工具或结束动作
  `arguments`          工具参数
  `observation`        工具返回摘要
  `next_action`        模型下一步选择
  `decision_summary`   简短决策依据，不记录内部思维链

Trace 文件还应记录 `run_id`、运行模式（`live`/`mock`）、模型名称、模型版本或快照、时间戳、最终状态，以及运行时拒绝的非法动作。Observation 只保留验证所需摘要，不记录密钥、隐私数据或模型内部思维过程。

如果模型服务不可用，可以提供离线 Mock Trace 供流程调试，但必须明确标注为
Mock，不得冒充真实模型运行记录。

### 6.3 至少三组完整验证材料

`examples/` 至少提交三组输入、实际输出和 Trace，覆盖：

1.  正常路径。
2.  信息不足或审核风险路径。
3.  工具失败或时段冲突路径。

可以在同一组用例中覆盖多个验收点。`docs/03-evaluation.md`
必须引用具体文件，并区分预期结果与实际结果。

------------------------------------------------------------------------

## 7. 推荐工程结构

``` text
project/
├── README.md
├── requirements.txt
├── .env.example
├── src/
│   ├── agent.py
│   ├── model.py
│   ├── runtime.py
│   ├── tools.py
│   └── store.py
├── data/
│   ├── materials.json
│   ├── rules.json
│   └── schedule.json
├── tests/
│   ├── test_normal.py
│   ├── test_missing_info.py
│   ├── test_risk.py
│   ├── test_conflict.py
│   ├── test_idempotency.py
│   ├── test_failure.py
│   └── test_runtime_guards.py
├── examples/
│   ├── inputs/
│   ├── outputs/
│   └── traces/
└── docs/
    ├── 01-requirements.md
    ├── 02-solution.md
    ├── 03-evaluation.md
    ├── 04-ai-usage.md
    └── 05-retrospective.md
```

------------------------------------------------------------------------

## 8. 技术方案建议

建议使用 Python 原生实现工具层和 Agent 执行循环，配合支持 Tool Calling
的模型 API。

推荐职责划分：

-   `agent.py`：维护消息历史、调用模型、执行 ReAct 循环、控制步数。
-   `model.py`：封装真实模型调用与离线 Mock Model。
-   `tools.py`：实现素材读取、规则检查、时段查询和模型可见的工具接口。
-   `store.py`：管理合成数据、持久化草稿、请求 hash 和幂等键。
-   `runtime.py`（可合并进 `agent.py`）：维护可信执行状态、校验动作前置条件，并从 Observation 组装最终事实字段。

不要求使用 Agent 框架。若使用 LangGraph、Google ADK 或
AgentScope，必须说明版本、使用功能及框架承担的职责。

### 8.1 模型与离线模式

至少提供两种运行模式：

-   `live`：调用真实模型，生成真实模型决策 Trace。
-   `mock`：使用预设模型响应验证程序流程，明确标注其不代表真实模型决策能力。

`.env.example` 只列出变量名和示例占位值，不得提交真实密钥。

------------------------------------------------------------------------

## 9. 文档交付要求

### `README.md`

说明环境、安装、运行命令、配置方式、模式切换和评审入口。

### `docs/01-requirements.md`

说明目标用户、业务目标、输入输出、范围和成功标准。

### `docs/02-solution.md`

说明系统结构、工具接口、ReAct 循环、动态决策、状态管理和技术取舍。

### `docs/03-evaluation.md`

说明测试用例、预期结果、实际结果、Trace 文件引用、失败情况和已知限制。

### `docs/04-ai-usage.md`

如实说明：

-   AI 辅助了哪些工作（需求分析、代码生成、调试、测试或文档等）。
-   哪些设计和代码由本人判断、修改或补充。
-   如何运行测试、检查模型决策和验证结果。
-   哪些部分尚未验证。

### `docs/05-retrospective.md`

说明最难的问题、关键取舍、学习收获和下一步改进方向。

------------------------------------------------------------------------

## 10. 完成判定与范围边界

本项目达到以下条件即可视为本轮交付完成：

-   [ ] CLI、HTTP API 或 Notebook 至少一种方式可以运行。
-   [ ] 提供可复制的运行命令。
-   [ ] 实现模型选择工具、执行工具、观察结果、再次决策的循环。
-   [ ] 至少两条真实模型 Trace 展示不同的动态决策路径。
-   [ ] 覆盖六项必验收场景。
-   [ ] 具备执行步数或工具调用次数上限。
-   [ ] 重复提交不会产生重复草稿。
-   [ ] 相同 `request_id` 但不同请求内容会被明确拒绝。
-   [ ] 硬性发布时间不会被 Agent 擅自更改。
-   [ ] 未经时段工具确认可用的时段无法保存。
-   [ ] 保存失败不会被报告为成功。
-   [ ] 不提供真实发布能力，输出始终明确未发布。
-   [ ] 提供至少三组输入、实际输出和运行记录。
-   [ ] README 与五份交付文档齐全。
-   [ ] AI 使用范围、个人贡献和验证情况如实记录。
-   [ ] GitHub 仓库可供评审读取，并记录最终 commit SHA。

### 本轮明确不扩展

以下内容默认进入后续改进，不纳入本轮完成条件：

-   多素材批量排期。
-   多平台发布策略。
-   复杂优先级调度算法。
-   用户账号与角色权限。
-   Web UI、可视化 Trace 面板。
-   生产级数据库和并发控制。
-   多 Agent、长期记忆和复杂工作流编排。

**退出条件：** 核心 ReAct
闭环、关键业务边界、六项验收场景和交付材料均有可验证证据后，停止扩展，进行最终运行检查并提交。不要因为发现非关键优化空间而持续扩大本轮范围。

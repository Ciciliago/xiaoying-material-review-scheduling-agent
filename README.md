# 素材审核与发布排期 Agent

项目 1 的可运行实现：读取单个素材、审核规则、检查发布时段，并只保存排期草稿。它不会调用或提供任何真实发布接口。

## 快速开始

需要 Python 3.9+。

```bash
python3 -m pip install -r requirements.txt
python3 -m pytest -q
```

用离线 mock 模式运行正常用例，并把临时草稿、结果和 Trace 放入 `/tmp`：

```bash
python3 -m src.cli \
  --input examples/inputs/normal.json \
  --mode mock \
  --draft-store /tmp/material-review-drafts.json \
  --output /tmp/normal-result.json \
  --trace /tmp/normal-trace.json
```

冲突替代时段示例：

```bash
python3 -m src.cli \
  --input examples/inputs/conflict.json \
  --mode mock \
  --draft-store /tmp/material-review-conflict-drafts.json \
  --output /tmp/conflict-result.json \
  --trace /tmp/conflict-trace.json
```

保存失败验证会以退出码 `2` 结束，这是预期结果：

```bash
python3 -m src.cli \
  --input examples/inputs/save_failure.json \
  --mode mock \
  --simulate-save-failure \
  --draft-store /tmp/material-review-failure-drafts.json
```

## 模式

- `mock`：默认模式。`MockDecisionModel` 根据上一步 Observation 选择下一工具，适合稳定测试与离线演示；它不证明真实模型决策能力。
- `live`：通过 OpenAI-compatible Chat Completions Tool Calling 调用真实模型。复制 `.env.example` 的变量到本地环境后运行 `--mode live`。不得提交 API key。

```bash
export OPENAI_API_KEY=...
export OPENAI_MODEL=...
export OPENAI_BASE_URL=https://api.openai.com/v1
python3 -m src.cli --input examples/inputs/conflict.json --mode live --trace /tmp/live-conflict-trace.json
```

仓库归档了两条标记为 DeepSeek `deepseek-flash` 的 live Trace：时段冲突后选择待确认替代时段，以及素材信息不足后阻断。当前工作区无法独立验证其真实运行来源（**NOT VERIFIED**）；Mock Trace 仍保留用于离线回归。详细证据边界见 `docs/03-evaluation.md`。

## 设计要点

模型选择动作；`runtime.py` 决定动作是否允许、核验工具事实并组装最终状态；`tools.py` 只执行确定性业务工具。这样模型不能绕过审核、篡改 hard 时间、保存未查询的时段，或在保存失败后虚报成功。

草稿保存在 JSON 文件中，并用 `request_id + 请求内容 hash` 实现幂等。相同请求重复保存只返回原草稿；同一 `request_id` 的不同请求会返回 `idempotency_conflict`。

## 评审入口

- 需求与设计：[docs/01-requirements.md](docs/01-requirements.md)、[docs/02-solution.md](docs/02-solution.md)
- 验证结果：[docs/03-evaluation.md](docs/03-evaluation.md)
- 示例输入、输出与 Trace：[`examples/`](examples/)
- 自动化测试：[`tests/`](tests/)

## 已知限制

- 当前实现处理单个素材请求，不做批量调度或真实发布。
- 本地 JSON 存储适用于作业演示，不提供多进程并发写入保障。
- live 模式需要用户自行配置模型服务；未配置时使用 mock 模式。

# 示例会话：Ticketd 智能摘要改造选型

这是一个**完整跑完并归档**的研究会话，用来演示 NexusSearch 从
`input/spec.md`（中文研究规范）到 `output/report.md`（带证据链的选型报告）的全过程。
所有外部事实均来自 2026-09-12 当天可访问的一手来源，可用下方命令复核。

```
输入 spec ──▶ plan ──▶ runtime loop(1 次迭代) ──▶ analyst 补强(3 轮) ──▶ gate PASS 0.925 ──▶ report
   4 问题        16 facet      88 条证据(多数噪声)      25 条一手证据            9 claim/3 risk/1 decision
```

## 输入

`input/spec.md`：在 LangGraph / AutoGen(AG2) / OpenAI Agents SDK / MCP 自建循环之间做选型，
约束为「只用本地 MCP、禁止上传源码、单来源不得支撑关键结论、需给 6 周迁移与回滚路线」。
被研究对象是 `examples/demo-project`（其 `docs/DECISIONS.md` 的 ADR-001 就是这条 open question 的出处）。

## 实际发生了什么（不美化）

**阶段 A — 自动循环（`nexus run`，预算 `--max-reads 2`）**

- 规划器把目标拆成 4 个研究问题 × 4 个 facet（landscape / architecture / practice / tradeoff）= 16 个 topic 节点。
- context-agent 用 `filesystem` 读了 79 行 demo 代码；researcher 用 `searxng` 跑了 5 轮检索。
- **检索质量很差是真实的**：88 条证据里 34 条被相关性过滤器判为 `unrelated`
  （developer.mozilla.org ×17、learn.microsoft.com ×15、hackage ×1、gentoo ×1），
  因为中文整句被直接当作查询词、且本地 SearXNG 只放通了 IT/package 类引擎。
- 迭代因 `budget_exhausted` 停止（143 次工具调用），此时 gate FAIL 0.625。

**阶段 B — 宿主 Agent 的 analyst 补强（3 轮 `graph apply-update` + `evidence add`）**

- 25 条**带原文引用**的一手证据：LangGraph 概览/README、Microsoft Agent Framework 概览、
  OpenAI Agents SDK 文档、MCP 2025-06-18 规范、AutoGen stable 文档、4 个包的 PyPI 注册表数据、
  以及 5 处本地文件引用（`summariser.py`、`DECISIONS.md`、`test_smoke.py`、`SKILL.md`、`mcp_router.py`、`loop_controller.py`）。
- 9 个 claim、3 个 risk、1 个带 3 条被否决备选的 decision、2 个新 gap（其中 1 个是**负结果**）。
- 交叉验证的关键一步：厂商叙述「Agent Framework 是 AutoGen 的直接后继」+
  注册表事实「autogen-agentchat 0.7.5 末次上传 2025-09-30，agent-framework 1.18.0 上传 2026-09-10」
  互相印证，并被显式标成一条 `contradicts`（AutoGen 文档仍按独立框架发布 vs. 谱系合并）。

**结论（报告正文）**：以 LangGraph 图编排为控制面 + 本地 MCP 为工具面；
否决 AutoGen（谱系收敛中）、Agents SDK（sessions 是会话记忆而非可回溯图执行）、MCP 自建循环（要自建同意/权限/检查点/审计）。

## 这份示例故意保留的诚实缺陷

| 缺陷 | 位置 | 为什么不算「已解决」 |
| --- | --- | --- |
| 16 个 facet 中 8 个仍为 0 覆盖 | `graph gaps` | 小预算 demo；gate 测的是证据质量，facet 覆盖由 loop 的 `node_coverage=0.7` 驱动，需再跑 `nexus run` |
| 无法给出延迟/成本数字 | `g-benchmark-missing` | 工作区只有一个 `assert True` 的冒烟测试，缺基准即不写百分比 |
| 未找到一手事故复盘 | `g-no-postmortems` | 5 个 provider 全部执行后 0 候选：写成「未检索到」而非「风险已排除」 |
| 1 条趋势预测置信度 0.37 | `c7` | 厂商能力外扩 → 自研 planner 可能被吸收，属推断，带 `uncertain` 标签 |
| 1/9 claim 只有二手来源 | `primary_source_bias 0.89` | 就是上面那条推断，未强行包装成一手 |

## 复现

```bash
cd NexusSearch-Agent-Skill
./nexus -w examples/sessions/ai-agent-framework status      # gate PASS score=0.925 · confidence 0.916
./nexus -w examples/sessions/ai-agent-framework loop --show # 迭代 1：86 条新证据，stop=budget_exhausted
./nexus -w examples/sessions/ai-agent-framework gate        # 8 项检查 + 可读的下一步动作
./nexus -w examples/sessions/ai-agent-framework report      # 重新生成 output/report.md
./nexus -w examples/sessions/ai-agent-framework audit       # 7 PASS / 0 FAIL
./nexus -w examples/sessions/ai-agent-framework graph tree  # 图结构
```

从零重跑（需要本地 SearXNG 可达）：

```bash
./nexus -w /tmp/nx-repro init --templates
cp examples/sessions/ai-agent-framework/input/spec.md /tmp/nx-repro/input/spec.md
./nexus -w /tmp/nx-repro intake --target examples/demo-project
./nexus -w /tmp/nx-repro run --iterations 2 --max-reads 2 --verify
./nexus -w /tmp/nx-repro gate && ./nexus -w /tmp/nx-repro report
```

再往下就是 analyst 该做的事：`nexus gate` 的 `next actions` 会逐条点名缺证据的 facet 节点
（例如 `research q2-landscape (...)`），按提示补 `evidence add` 与 `graph apply-update` 即可。

## 目录

- `input/spec.md` 原始研究规范（唯一人工输入）
- `state/` 计划、循环状态、MCP 缓存
- `graph/research.graph.json` 159 节点 / 192 边
- `evidence/sources.json` 113 条证据（含原文引用与 quality_flags）
- `agents/logs/` `loop.jsonl` 与 `tool-calls.jsonl`（含 arguments/result_count 审计）
- `output/` `report.md`、`evidence.json`、`graph.json`、`graph.mmd`、`manifest.json`

## 这次运行反过来修好了框架的哪些问题

本目录不只是结果展示，它是一次回归测试的现场：

1. `quality_gate.check_source_diversity` 迭代 `store.records`（id→Evidence 映射）拿到的是 `str`，
   异常被逐检查项的 `except` 吞掉 → **多样性恒为 0**。修复后 FAIL 0.45 → 0.625。回归测试：
   `tests/test_gate_loop.py::test_no_gate_check_crashes_on_a_populated_store`。
2. 审计日志不记录「查的是什么」：`mcp_call` 事件只有 capability/ok/耗时 → 负结果无法复查；
   现在带 `arguments`（query/url/engines，已 redact）与 `result_count`。
3. `Session.for_tools()` 对真实工作区也置 `bare=True` → `nexus mcp call` **完全不写审计**。
4. provider 链尾端的 `fetch json`（缺 `url`）把「全线 0 候选」伪装成 `invalid_input` →
   现在归一为 `error_class=no_results` 并给出「N providers tried, M returned no usable candidates」。
5. `Evidence.independence_key`：本地文件统一计入 `workspace` 桶，四个仓库文件不再冒充 4 个独立域。
6. `graph apply-update` 合并已有节点时静默丢弃 `title` → analyst 无法修正自己写的 claim。
7. `loop --show` 渲染空表：`LoopController.create()` 不恢复持久化状态，续跑还会拿到全新的
   `max_iterations` 预算。现在从 `state/state.json` 恢复 iteration/tool_calls/history，
   回归测试 `tests/test_gate_loop.py::test_loop_state_survives_a_fresh_controller`。
8. `nexus audit` 把整个证据存储塞进**单条记录**的 schema 校验 → 恒 FAIL；改为逐条校验，
   现在报 `all 113 records satisfy evidence.schema.json`。
9. `nexus init --templates` 留下的 `input/RESEARCH.md` 会**劫持 intake**：`"目标" in "非目标"`
   被当成标题命中，模板里的「不做供应商比价」变成了研究目标，两条示例问题也混进了计划。
   现在负面范围标题会被显式关闭、`（必填…）` 这类脚手架标签被识别、与模板逐字节相同的文件直接跳过，
   `nexus intake` 也会报 `spec files: 1/2 used`。
10. 相对路径参数原样透传给本地 MCP server：`mcp call code.local_structure --args
    '{"path":"examples/demo-project"}'` 被 filesystem server 按它自己的根目录解析成
    `~/examples/demo-project` 而 `ENOENT`。现在路由层按调用者 cwd 先绝对化再下发。

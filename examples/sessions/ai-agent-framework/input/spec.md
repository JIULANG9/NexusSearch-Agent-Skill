# Research Spec — Ticketd 智能摘要改造

## 研究目标
评估把 `examples/demo-project` 中手写的阻塞式 LLM 调用改造为 Agent 编排的可行性，
并在 LangGraph、AutoGen (AG2)、OpenAI Agents SDK、MCP 自建循环四个方案中做出选型。

## 背景
`src/summariser.py` 目前用 `urllib` 直连 chat/completions：无重试、无流式、无工具调用。
`docs/DECISIONS.md` 的 ADR-001 已标记为"待复审"。

## 约束
1. 只能使用本地 MCP 服务获取外部资料，禁止上传任何源码或数据。
2. 结论必须有可追溯来源与置信度，单个来源不得支撑关键结论。
3. 需给出 6 周内的迁移路线与回滚方案。

## 研究问题
1. 各框架在"少量确定性步骤 + 工具调用"这类工作流上的抽象差异是什么？
2. 流式输出与失败重试在四个方案中的成熟度如何？
3. 迁移到编排框架后，延迟与运维成本会增加多少？
4. 有哪些公开的生产事故或反例说明不应引入编排框架？

## 期望交付物
带证据表格的中文技术选型报告 + 分阶段实施计划 + 风险清单。

## mode
standard

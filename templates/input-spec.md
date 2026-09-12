<!--
Drop this file (or your own notes/papers/exports) into a research workspace's input/
folder, then run:  nexus intake --target <project-under-study>
Only the H1 (研究目标) and 研究问题 are required; everything else sharpens the run.
-->

# 研究目标：（在这里写一句话说明要研究什么，例如"为本地项目选择合适的检索增强方案"）

## 背景

（可选）当前状态、已有结论、为什么现在要研究。

## 研究问题

- （必填，2–8 个可验证的问题）2026 年主流开源 Deep Research 框架有哪些？
- 如何用图结构管理多 Agent 的研究状态？
- 本地 MCP 相比云端 API 的风险与收益？

## 约束

- 禁止把用户源码上传到外部服务
- 全部能力必须可离线降级
- 预算：≤ 5 次迭代，≤ 120 次工具调用

## 非目标

- 不做供应商比价
- 不评估商业 SaaS 托管方案

## 数据源

- 本地：docs/、README、config/*.yaml
- 外部：https://modelcontextprotocol.io/specification

## 验收标准

- 每个关键结论至少 2 个独立来源
- 报告包含风险分析与被否决的备选方案
- 所有本地结论均引用具体文件路径

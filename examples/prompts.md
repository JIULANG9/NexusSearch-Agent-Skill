# Prompt cookbook

Copy-paste entry points. Each one is enough to start a run; the skill fills in the
rest from `input/` and the workspace scan.

## 1. One-line research request (agent-driven)
```
用 nexus-deep-research 调研：本地 MCP 生态里有哪些可复用的深度研究框架？
输出带来源与置信度的对比报告。
```

## 2. Spec folder in an existing repo (runtime-driven)
```bash
mkdir -p runs/ticketd/input && cp docs/REQUIREMENTS.md runs/ticketd/input/spec.md
./nexus -w runs/ticketd init
./nexus -w runs/ticketd intake --target . --mode deep
./nexus -w runs/ticketd run --verify --iterations 4
./nexus -w runs/ticketd gate && ./nexus -w runs/ticketd report
```

## 3. Tech selection with hard constraints
```
/nexus-deep-research
目标：为 FastAPI 服务选择异步任务方案（Celery / RQ / Dramatiq / pgqueuer）
约束：只用 PostgreSQL，不引入 Redis；必须支持幂等重试；团队 <5 人
交付：对比表 + 推荐 + 迁移计划 + 风险
```

## 4. Codebase comprehension
```
研究这个仓库如何升级为 AI Agent 系统，先读代码再谈外部方案，
所有关于本仓库的结论必须引用具体文件路径。
```

## 5. Academic / standards survey
```
调研 2024-2026 年多模态检索在代码库理解上的公开研究，
优先论文与官方规范，标注每个结论的证据等级。
```

## 6. Adversarial fact check of a draft
```
你是 critic + verifier：下面这段结论里，哪些断言缺少两个独立来源？
哪些数据可疑？给出反例和需要补充的查询。
"""
MCP 会在 2026 年统一所有 Agent 框架。
"""
```

## 7. Resume a half-finished run
```bash
./nexus -w runs/ticketd status        # where we stopped and why
./nexus -w runs/ticketd tasks         # remaining gaps as assignments
./nexus -w runs/ticketd loop          # iteration history + stop reason
```

## 8. Ask the memory what worked before
```bash
./nexus remember recall "技术选型 框架对比"
./nexus -w runs/ticketd remember sync --push   # mirror into the memory MCP server
```

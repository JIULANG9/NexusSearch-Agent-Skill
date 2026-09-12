
# PRP.md

# NexusSearch-Agent-Skill

## Graph-Driven Multi-Agent Deep Research Skill Framework

Version: v1.0
Status: Development Specification
Target Agent: Claude Code / Codex / Qoder / GitHub Copilot Agent

---

# 1. 项目概述

## 1.1 项目名称

```
NexusSearch-Agent-Skill
```

---

## 1.2 项目定位

NexusSearch-Agent-Skill 是一个：

> 基于本地 MCP 服务、多 Agent 协同、Graph 推理、Loop 自优化机制构建的可复用 Deep Research Skill。

目标是构建一个：

```
AI Agent Research Operating System
```

使开发者能够在 IDE 环境中：

通过一句自然语言指令：

```
/nexus-research

分析当前项目的技术架构，
寻找最佳实践，
验证方案可行性，
输出完整技术报告
```

自动触发：

```
需求解析
 ↓
上下文理解
 ↓
Research Graph生成
 ↓
Agent任务拆解
 ↓
MCP工具调用
 ↓
证据收集
 ↓
真实性验证
 ↓
知识融合
 ↓
研究报告生成
```

---

# 2. 核心目标

## 2.1 必须实现能力

系统必须支持：

### 目标1：自动研究任务规划

输入：

```
用户研究目标
+
workspace上下文
+
research规范
```

输出：

Research Plan

例如：

```json
{
 "goal":
 "分析AI Agent框架",

 "questions":[

 "当前主流架构是什么",

 "如何本地部署",

 "有哪些风险"

 ]

}
```

---

# 目标2：Graph驱动研究

系统必须使用图结构管理研究过程。

Graph类型：

## Research Knowledge Graph

节点：

```
Goal

Question

Topic

Entity

Evidence

Claim

Decision

Risk

```

关系：

```
depends_on

supports

contradicts

derived_from

implements

```

示例：

```
        Goal

          |

       Question

          |

      Technology

       /      \

    MCP      Agent

      |

 Evidence

```

---

# 目标3：Multi-Agent协作

必须实现以下 Agent：

## Research Agent

职责：

* 信息搜索
* 文档获取
* GitHub分析
* 资料整理

工具：

```
searxng
github
fetch
chrome-devtools
```

---

## Context Agent

职责：

理解当前项目环境。

工具：

```
filesystem
memory
codegraph
```

输出：

Project Context Graph

---

## Analyst Agent

职责：

* 技术分析
* 架构比较
* 优缺点分析
* 风险分析

---

## Verification Agent

职责：

验证：

```
Claim
Evidence
Source
```

确保：

* 多来源一致
* 标注可信度
* 发现冲突

---

## Synthesizer Agent

职责：

生成最终报告。

---

# 3. 系统总体架构

实现：

```
                  User Prompt


                      |

                      v


             NexusSearch Skill


                      |

              Research Controller


                      |

        +-------------+-------------+

        |                           |

        v                           v


 Research Graph              Context Loader


        |

        v


+--------------------------------------+

          Agent Team Runtime


+--------------------------------------+

 |          |          |          |

Search   Context   Verify   Writer


 |          |          |          |


 +---------- MCP Layer -----------+


              |

              v


        Local MCP Services


              |

              v


       Knowledge Repository


              |

              v


        Final Report

```

---

# 4. 项目目录结构要求

必须生成：

```
NexusSearch-Agent-Skill/


├── SKILL.md

├── README.md

├── LICENSE


├── config/

│
├── agents/

│   ├── researcher.md

│   ├── context.md

│   ├── analyst.md

│   ├── verifier.md

│   └── writer.md


├── workflows/

│
│   ├── research-loop.md

│   ├── graph-planning.md

│   └── verification.md


├── schemas/


│   ├── task.schema.json

│   ├── graph.schema.json

│   └── evidence.schema.json


├── runtime/


│   ├── graph_engine.py

│   ├── agent_router.py

│   ├── mcp_router.py

│   └── quality_gate.py


├── templates/


│   ├── report.md

│   └── executive-summary.md


├── examples/


│   └── demo-project/


└── tests/


```

---

# 5. Skill入口设计

文件：

```
SKILL.md
```

必须包含：

## Skill Metadata

```yaml
name:

NexusSearch-Agent-Skill


description:

Graph-driven multi-agent deep research framework


trigger:

deep-research

nexus-search

```

---

# Skill执行流程

```
1.
Load research specification


2.
Analyze workspace


3.
Build research graph


4.
Create agent tasks


5.
Execute MCP tools


6.
Collect evidence


7.
Verification loop


8.
Generate report

```

---

# 6. Research Loop设计

必须实现：

## Agent Reasoning Loop

状态：

```python
ResearchState:

{
goal,

graph,

tasks,

evidence,

confidence,

iteration

}

```

循环：

```python

while not completed:


    analyze_gap()


    generate_tasks()


    dispatch_agents()


    collect_results()


    update_graph()


    verify_claims()


```

---

停止条件：

满足任意：

```
confidence >= 0.85


OR


max_iteration >= 5


OR


quality_gate_pass

```

---

# 7. MCP适配要求

系统必须支持：

当前环境：

```
chrome-devtools

context7

memory

filesystem

classschedule

playwright

fetch

searxng

github

codegraph

```

---

# MCP配置文件

生成：

```
config/mcp-map.yaml

```

格式：

```yaml

search:


 searxng:

   role:
    web discovery



 github:

   role:
    repository intelligence



context:


 filesystem:

    role:
      workspace analysis


 memory:

    role:
      long term learning



analysis:


 codegraph:

    role:
      dependency analysis


browser:


 playwright:

    role:
      automation


 chrome-devtools:

    role:
      dynamic inspection

```

---

# 8. MCP异常处理

必须实现：

## Retry机制

```
Tool Call

 |

Failure

 |

Retry x3

 |

Fallback

 |

Mark unavailable

```

---

错误分类：

| 错误      | 处理       |
| ------- | -------- |
| Timeout | Retry    |
| 权限错误    | Fallback |
| 不存在     | Skip     |
| 数据质量低   | 重新搜索     |

---

# 9. Knowledge Graph设计

最小实现：

JSON Graph。

格式：

```json
{
"id":"mcp",

"type":"technology",

"relations":[

{
"type":"implements",

"target":"agent"

}

]

}

```

---

# 10. Evidence系统

所有研究结果必须保存：

```
evidence/


source.json


```

结构：

```json
{

"source":

"github.com/example",


"type":

"repository",


"claim":

"MCP supports tool abstraction",


"confidence":

0.92

}

```

---

# 11. 输出规范

最终生成：

目录：

```
output/

```

包含：

## report.md

结构：

```
# Executive Summary


# Research Objective


# Current Landscape


# Architecture Analysis


# Implementation Plan


# Risk Analysis


# Recommendation


# References

```

---

# 12. IDE集成要求

必须支持：

## Claude Code

安装：

```
.claude/skills/

NexusSearch-Agent-Skill

```

调用：

```
/nexus-search

```

---

## Codex

支持：

```
.codex/skills/

```

---

## Qoder

支持：

```
.agent/skills/

```

---

## GitHub Copilot

支持：

```
.github/copilot-instructions.md

```

---

# 13. 开发任务拆解

## Phase 1：基础Skill

Agent必须完成：

* 创建目录结构
* 编写SKILL.md
* 创建Agent定义
* 创建配置文件

验收：

```
Skill可以被IDE识别

```

---

# Phase 2：Runtime

实现：

```
graph_engine

agent_router

mcp_router

quality_gate

```

---

# Phase 3：MCP集成

完成：

```
filesystem

github

searxng

context7

memory

codegraph

```

---

# Phase 4：Research Loop

实现：

* 自动规划
* Agent调度
* 结果合并
* 循环优化

---

# Phase 5：测试

创建：

```
tests/

```

测试：

## Test Case 1

输入：

```
分析当前项目架构

```

期望：

生成：

```
Architecture Report

```

---

## Test Case 2

输入：

```
比较LangGraph和AutoGen

```

期望：

产生：

```
Research Graph

Evidence

Comparison

```

---

# 14. 质量验收标准

## 功能验收

必须满足：

| 项目      | 要求 |
| ------- | -- |
| Skill加载 | 通过 |
| Agent调用 | 通过 |
| MCP调用   | 通过 |
| Graph生成 | 通过 |
| Loop运行  | 通过 |
| 报告生成    | 通过 |

---

# 15. 安全要求

禁止：

* 上传用户代码到外部服务
* 使用未知云API
* 保存敏感数据

必须：

* 本地执行优先
* MCP隔离
* 日志审计

---

# 16. 最终交付物

开发 Agent 必须输出：

```
NexusSearch-Agent-Skill/


1.
完整Skill源码


2.
README


3.
安装说明


4.
示例


5.
测试报告


6.
架构说明


7.
使用Demo

```

---

# 17. Agent执行规则

开发 Agent 必须遵循：

```
先理解需求

↓

检查现有环境

↓

设计方案

↓

实现代码

↓

运行测试

↓

修复问题

↓

输出总结

```

禁止：

* 简化架构
* 删除Agent角色
* 绕过MCP
* 使用外部云服务替代本地能力

---

# 18. 最终成功标准

NexusSearch-Agent-Skill 完成后：

用户可以在任意支持 Skill 的 IDE 中：

输入：

```
/nexus-search

研究这个项目如何升级为AI Agent系统

```

系统自动：

```
读取项目

↓

构建Graph

↓

启动多个Agent

↓

调用本地MCP

↓

验证资料

↓

生成专业研究报告

```

即完成一个：

> 可持续进化的本地 AI Deep Research Agent Skill。

---

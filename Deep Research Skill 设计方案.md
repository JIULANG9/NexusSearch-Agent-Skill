# 基于本地 MCP + 多 Agent + Graph Loop 的可复用 Deep Research Skill 设计方案

## 0. 方案定位

目标不是开发一个「搜索工具」，而是开发一个**AI Agent 深度研究操作系统级 Skill（Research Operating Skill）**：

> 用户只需在 IDE 中输入：
>
> ```
> /deep-research 
> 分析 xxx 技术方案，并结合当前项目代码给出实施建议
> ```
>
> Agent 自动：
>
> 1. 读取研究规范；
> 2. 理解项目上下文；
> 3. 建立研究任务图；
> 4. 调度多个专业 Agent；
> 5. 调用本地 MCP 工具；
> 6. 反复验证；
> 7. 构建知识图谱；
> 8. 输出结构化研究报告。

该设计借鉴当前 Agent Skills、MCP、Agent Loop、Graph Reasoning 的最佳实践。当前趋势已经从「一个万能 Agent」转向：

```
Skill = 专业知识 + 工作流
MCP   = 工具能力
Agent = 推理执行单元
Graph = 任务关系模型
Loop  = 持续优化机制
```

Anthropic 官方也明确区分：

* Skill：提供可复用流程和领域能力；
* MCP：连接外部工具；
* Subagent：隔离执行复杂任务。([Claude][1])

类似的 Deep Research Skill 已开始采用：

* 多 Agent 并行调查；
* Graph Controller；
* 多阶段质量门控；
* 结构化输出。([DeepWiki][2])

---

# 一、总体架构设计

## 1.1 总体架构

```
                    User Prompt
                         |
                         v
              +-------------------+
              | Deep Research Skill|
              |    Orchestrator    |
              +-------------------+
                         |
          +--------------+--------------+
          |                             |
          v                             v

   Research Graph                 Context Loader
   研究任务图                     上下文解析

          |
          |
          v

+------------------------------------------------+

              Multi-Agent Research Team

+------------------------------------------------+

 |           |             |            |
 v           v             v            v

Search     Analyst     Validator    Synthesizer
Agent      Agent       Agent        Agent


 |           |             |            |

 MCP       MCP           MCP          Local FS
 Tools     Tools         Tools        Memory


 |           |
 v           v

Knowledge Graph + Evidence Store


                |
                v

          Final Report Generator


                |
                v

       Markdown / JSON / HTML / PDF
```

---

# 二、Skill 文件结构设计

推荐：

```
deep-research-skill/

├── SKILL.md                 # 主入口
│
├── config/
│   ├── agents.yaml          # Agent配置
│   ├── mcp-map.yaml         # MCP映射
│   ├── quality.yaml         # 质量规则
│
├── workflows/
│   ├── research-loop.md
│   ├── graph-planning.md
│   ├── verification.md
│
├── agents/
│
│   ├── researcher.md
│   ├── analyst.md
│   ├── verifier.md
│   ├── architect.md
│   └── writer.md
│
├── schemas/

│   ├── research-task.json
│   ├── evidence.json
│   ├── graph.json
│
├── scripts/

│   ├── build_graph.py
│   ├── evidence_check.py
│   ├── merge_results.py
│
├── templates/

│   ├── report.md
│   ├── executive-summary.md
│
└── memory/

    └── research-patterns.json
```

---

# 三、核心 Skill 工作流程

## Phase 0：Research Intake

输入：

```
用户Prompt

+
workspace/

+
research-config/

+
context/
```

解析：

```
目标
|
范围
|
限制条件
|
已有资料
|
输出格式
```

生成：

```json
{
 "objective":
 "研究AI Agent框架",

 "constraints":
 [
 "必须本地运行",
 "不能依赖云服务"
 ],

 "sources":
 [
 "github",
 "local docs",
 "codebase"
 ]
}
```

---

# Phase 1：自动构建 Research Graph

核心创新：

不是线性搜索。

而是：

## Research Graph

```
                 Goal

                  |
                  v


              Topic Node


        +---------+---------+

        |         |         |

   Technology  History  Implementation

        |         |         |

      Agent    MCP      Code


        |
        v

     Evidence
```

节点类型：

| Node     | 说明   |
| -------- | ---- |
| Goal     | 最终目标 |
| Question | 研究问题 |
| Entity   | 技术实体 |
| Evidence | 证据   |
| Claim    | 结论   |
| Risk     | 风险   |
| Decision | 决策   |

---

例如：

用户：

> "设计本地AI Agent研究系统"

Graph：

```
AI Agent Research System


├── Architecture
│
├── MCP
│
├── Agent Framework
│
├── Memory
│
├── Evaluation
│
└── Deployment


```

---

# 四、多 Agent 协作设计

## Agent Team

## 1. Research Agent

职责：

* 搜索资料
* 获取论文
* GitHub分析
* 文档阅读

工具：

```
searxng
fetch
github
context7
chrome-devtools
```

输出：

Evidence Package

```json
{
source:"",
summary:"",
confidence:0.82
}
```

---

## 2. Context Agent

职责：

理解本地环境。

调用：

```
filesystem
memory
codegraph
```

能力：

扫描：

```
/
├── docs
├── src
├── config
├── README
```

生成：

```
Project Knowledge Graph
```

---

## 3. Analyst Agent

负责：

* 横向比较
* 架构推理
* 找漏洞

例如：

发现：

```
方案A:

优点:
+
缺点:
-

方案B:

优点:
+
缺点:
-
```

---

## 4. Verification Agent

核心。

负责：

真实性验证。

机制：

Claim:

```
Claude Agent SDK 支持Loop
```

验证：

```
Source A
+
Source B
+
Code Evidence
```

计算：

```
confidence score
```

公式：

```
Confidence

=
Source Reliability
*
Evidence Count
*
Consistency
```

---

## 5. Synthesizer Agent

负责：

最终输出。

生成：

```
Executive Summary

Architecture

Implementation

Risk

Roadmap

References
```

---

# 五、Graph + Loop 推理机制

## 5.1 Research Loop

不是：

```
Search
|
Answer
```

而是：

```
Observe

 ↓

Plan

 ↓

Execute

 ↓

Evaluate

 ↓

Refine

 ↓

Repeat
```

类似 Claude Agent Loop：

Agent 根据目标调用工具，然后根据结果继续下一轮。([Claude][3])

---

## 5.2 Loop Controller

状态：

```python
ResearchState:

{
goal,

graph,

evidence,

confidence,

open_questions,

iteration
}

```

循环：

```python
while confidence < threshold:

    analyze_gap()

    create_tasks()

    dispatch_agents()

    merge_results()

    verify()

```

停止条件：

```
confidence > 0.85

AND

unanswered_question = 0

AND

quality_gate PASS

```

---

# 六、本地 MCP 适配设计

你的环境：

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

建议建立：

## mcp-map.yaml

```yaml
search:

 searxng:
   purpose:
     web_search


 github:
   purpose:
     repository_analysis


browser:

 chrome-devtools:
   purpose:
     dynamic_page


 playwright:
   purpose:
     automation


knowledge:

 context7:
   purpose:
     documentation


 memory:
   purpose:
     long_term_memory


filesystem:

 filesystem:
   purpose:
     local_context


analysis:

 codegraph:
   purpose:
     dependency_analysis

```

---

# 七、MCP 调度策略

## Tool Router Agent

根据任务自动选择。

例如：

问题：

> "React Server Component最新实践"

调用：

```
context7
+
github
+
searxng
```

问题：

> "当前项目为什么build失败"

调用：

```
filesystem

+

codegraph

+

github
```

---

# 八、MCP错误处理机制

## Retry

```
Tool Failure

      |

      v

Retry(3)


      |

      v

Fallback Tool


      |

      v

Human Review

```

---

例如：

github失败：

```
github MCP

↓

searxng

↓

chrome

↓

manual source
```

---

# 九、知识图谱存储设计

推荐：

轻量：

```
JSON Graph
```

高级：

```
Neo4j
+
Vector DB
```

节点：

```json
{
"id":"mcp",

"type":"technology",

"relations":

[
{
"type":"implements",
"target":"agent"
}
]

}
```

---

# 十、多模态检索能力

支持：

## 文档

```
PDF
Markdown
Word
Code
```

## 图片

```
架构图

流程图

截图
```

## 网页

```
HTML

Dynamic SPA

API Docs

```

Pipeline：

```
Input

 |

Parser

 |

Embedding

 |

Graph Entity Extraction

 |

Knowledge Graph

```

---

# 十一、IDE 集成方案

## Claude Code

目录：

```
~/.claude/skills/

   deep-research/
       SKILL.md

```

调用：

```
/deep-research

分析这个项目
```

Claude Code 会加载 Skill，并通过 MCP 调工具。([Claude][1])

---

## Codex

结构：

```
.codex/

 skills/

   deep-research/

```

Prompt:

```
Use skill:
deep-research

Task:
Analyze architecture
```

---

## Qoder

配置：

```
.agent/

skills/

deep-research

```

---

## GitHub Copilot

通过：

```
.github/

copilot-instructions.md

+
skill package

```

触发：

```
@workspace

/deep-research
```

---

# 十二、自动生成工作区设计

用户目录：

```
research-workspace/


├── input/

│    ├── requirements.md

│    ├── docs/

│    └── files/


├── graph/

│    └── research.graph.json


├── evidence/

│    └── sources.json


├── agents/

│    └── logs/


├── output/

│    └── final-report.md


└── memory/

     └── learned-pattern.json

```

---

# 十三、质量控制系统

## Quality Gate

最终报告必须满足：

| 检查   | 规则    |
| ---- | ----- |
| 来源数量 | >=5   |
| 关键结论 | >=2来源 |
| 代码验证 | 需要时   |
| 风险分析 | 必须    |
| 反例   | 必须    |
| 不确定性 | 必须标注  |

---

# 十四、高级能力设计

## 1. Self Improving Skill

利用 memory MCP：

保存：

```
研究模式

最佳搜索路径

常用来源

失败案例

```

下一次自动优化。

---

## 2. Research Replay

保存：

```
task graph

agent trace

tool calls

evidence

```

可以：

```
重新运行

比较结果

审计过程
```

---

## 3. Agent Debate

增加：

```
Critic Agent
```

流程：

```
Researcher

↓

Architect

↓

Critic

↓

Revision
```

类似 AI Council。

---

# 十五、开发路线图

## Phase 1 MVP（1-2周）

实现：

* SKILL.md
* Agent Router
* MCP Mapping
* Markdown输出

---

## Phase 2 Professional（3-6周）

增加：

* Graph Engine
* Evidence Store
* Verification Agent
* Memory

---

## Phase 3 Enterprise（2-3个月）

增加：

* 多模型协作
* Knowledge Graph
* 自动评测
* Research Replay
* Skill Marketplace

---

# 十六、最终推荐技术栈

| 模块            | 技术                                   |
| ------------- | ------------------------------------ |
| Skill         | Claude Skill Format                  |
| Agent Runtime | Claude Code Agent Loop / Codex Agent |
| Graph         | NetworkX / LangGraph                 |
| Memory        | MCP Memory                           |
| Search        | SearxNG                              |
| Docs          | Context7                             |
| Code Analysis | CodeGraph                            |
| Automation    | Playwright                           |
| Storage       | JSON + SQLite                        |
| Embedding     | 本地Embedding模型                        |
| Report        | Markdown + Mermaid                   |

---

# 十七、最终形态

最终你的系统会类似：

```
                 User


                  |

                  v


          Deep Research Skill


                  |

          Research Graph Engine


                  |

 ------------------------------------------------

 |          |          |          |              |

Search   Context   Analyst   Verify   Writer


 |          |          |          |

 MCP      MCP       MCP       MCP


 ------------------------------------------------


                  |

            Knowledge Graph


                  |

            Research Report


```

---

## 结论

针对你的本地 MCP 环境，最佳路线不是开发一个普通 `/deep-search` 命令，而是开发：

> **一个 Graph-driven Multi-Agent Deep Research Skill Framework**

核心设计原则：

1. **Skill负责方法论和流程**
2. **MCP负责工具连接**
3. **Agent负责专业任务**
4. **Graph负责任务关系**
5. **Loop负责持续优化**
6. **Memory负责长期进化**

该架构完全适合：

* Claude Code
* Codex
* Qoder
* GitHub Copilot Workspace

并且可以逐步演化成为个人 AI Research OS。

如果继续深化，我建议下一步可以：

1. **生成正式 AGENTS.md + SPEC.md 开发规范文档（直接交给 Claude Code 实现）**
2. **设计完整 Skill 目录、SKILL.md 模板和 Agent Prompt 模板**
3. **绘制该系统的 Mermaid 架构图 + MCP 调用时序图**

[1]: https://code.claude.com/docs/en/features-overview?utm_source=chatgpt.com "Extend Claude Code - Claude Code Docs"
[2]: https://deepwiki.com/liangdabiao/Claude-Code-Deep-Research-main/3.4-multi-agent-architecture?utm_source=chatgpt.com "Multi-Agent Architecture | liangdabiao/Claude-Code-Deep-Research-main | DeepWiki"
[3]: https://code.claude.com/docs/en/agent-sdk/agent-loop?utm_source=chatgpt.com "How the agent loop works - Claude Code Docs"

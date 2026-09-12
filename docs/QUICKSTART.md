# 五分钟上手（小白版）

> 目标：从「一句中文问题」到「带来源和置信度的研究报告」。
> 全程可以在**断网**状态下跑通，不需要任何云端 API key。

---

## 0. 先搞懂三件事

| 问题 | 答案 |
| --- | --- |
| 我提的需求写在哪？ | 工作区的 `input/RESEARCH.md`（`nexus quickstart` 会自动生成模板） |
| 中间状态在哪？ | 同一工作区里的 `graph/`、`evidence/`、`state/`、`agents/logs/` |
| 最终产出在哪？ | 工作区的 `output/report.md`（另有 `output/evidence.json`、`output/graph.json`、`output/manifest.json`） |

一个「工作区」= 一次研究 = 一个文件夹，默认建在 `runs/<问题标题>/`。
删掉这个文件夹就等于彻底清理，系统里不留任何隐藏状态。

---

## 1. 安装（三选一）

```bash
# A. 安装器（推荐：只装该装的，装完自动做规范自检）
bash install.sh                        # -> ~/.agents/skills/nexus-deep-research
./nexus install --dry-run              # 先看会装什么（103 个文件，不动磁盘）
./nexus install --list                 # 看支持哪些宿主目录
./nexus install --dest ~/.codex/skills # 装到 Codex CLI

# B. 手工拷贝（也行，但会把 runs/、本地记忆、私有文档一起带过去）
cp -R . ~/.agents/skills/nexus-deep-research

# C. 只在当前仓库里用（不安装）
./nexus --version
```

`nexus install` 与 `cp -R` 的区别值得记一句：安装器**只放 skill 载荷**，并把上一次装多的
文件清理掉（你自己的 `memory/` 与 `runs/` 永不删除）。`cp -R` 会把研究中间产物、本地记忆
和没写的文档一起塞进 skills 目录——自己用没问题，但别把那个目录分享给别人。

只需要 **Python 3.10+**。`PyYAML` / `jsonschema` 装了更好，没装也能跑。

验证装好了：

```bash
cd ~/.agents/skills/nexus-deep-research
./nexus skill-check   # 全绿 = 宿主 agent 能正确加载这个 skill
./nexus doctor        # 看你本机 MCP 服务哪些通、哪些不通
```

`doctor` 里出现 `DISABLED` / `unreachable` **不是错误**——没配置的服务器本来就不该通，
技能会自动只用能用的那几个。

**宿主 MCP 自动检测**：如果你在 Codex / Claude Desktop / Cursor 里运行，`doctor` 会自动
检测宿主已配置的 MCP 服务并标注 `(via codex)` 等。已配置的服务**无需额外安装**——
`quickstart` 也会跳过「MCP 未就绪」提示。

---

## 2. 第一条命令：把你的问题丢进去

```bash
./nexus quickstart "调研 2026 年本地 MCP 深度搜索方案的技术选型"
```

它会一次做完：建工作区 → 写需求模板 → 解析需求生成研究计划 → 拆出研究问题与子主题 →
列出检索任务 → 打印接下来该复制哪几条命令。

```
  [1/5] 创建工作区 runs/调研-2026-年本地-mcp-深度搜索方案的技术选型
  [2/5] 需求文件已就绪 .../input/RESEARCH.md
  [3/5] 解析需求，生成研究计划与研究图
- graph seeded: {'questions': 6, 'topics': 24, 'constraints': 0}
  [4/5] 列出将要执行的检索任务（不联网、不调用 MCP）
task   agent       capability     node             objective
-      researcher  web.discovery  q1-landscape     Close evidence gaps for q1-landscape…
```

带 MCP 的那次运行还会多一行 `[5/5] 本地 MCP 自检：11/13 个服务可达`。

> 没网 / 不想联网：`./nexus --offline quickstart "…"`。
> 规律：**全局开关（`--offline`、`-w`）永远写在子命令前面**，后面所有命令同理。

---

## 3. 把需求写清楚（这一步决定报告质量）

编辑 `input/RESEARCH.md`。**只有两项必填**：

```markdown
# 研究目标：为本地项目选择合适的检索增强方案     <- 必填，一行

## 研究问题                                        <- 必填，2–8 条
- 2026 年主流开源 Deep Research 框架有哪些？
- 本地 MCP 相比云端 API 的风险与收益？

## 约束          ## 非目标        ## 数据源
## 验收标准      ## 背景          ## 交付物
```

- 六个可选标题（约束 / 非目标 / 数据源 / 验收标准 / 背景 / 交付物）写多少算多少，中文标题
  即可，`intake` 按标题词识别；全不写也能出计划，只是问题会偏通用。
- 需求规范的正本在 `templates/input-spec.md`（`quickstart` 生成的 `input/RESEARCH.md` 就是它），
  团队要统一口径就改这一个文件。
- 已有的资料（PDF、笔记、导出的 JSON）直接扔进同一个 `input/` 目录，`intake` 会自动读取。
- 改完需求重跑：`./nexus intake --force`。

---

## 4. 跑研究 + 拿报告

```bash
cd runs/<你的问题>
../../nexus run --verify    # 检索 → 取证 → 交叉验证 → 循环，直到达标或撞预算
../../nexus gate            # 质量门：来源多样性、证据覆盖、矛盾检测、置信度
../../nexus report          # 生成 output/report.md
```

懒人一行版（在工作区外也能跑，且把 gate、report 串起来）：

```bash
./nexus -w runs/<你的问题> run --verify && cat runs/<你的问题>/output/report.md
```

跑起来在做什么：

- 图结构（`graph/research.graph.json`）承载目标 / 问题 / 子主题 / 证据 / 结论，缺口驱动任务；
- Agent 分工：planner、context-agent、researcher、analyst、critic、verifier、synthesizer；
- 循环有硬预算：默认 `confidence_threshold=0.85`、`max_iteration=5`，不会无限烧 token；
- **过不了 gate 的结论不会出现在报告里**——报告只写有来源、有置信度的东西。

随时看进度：`./nexus status`、`./nexus evidence`、`./nexus graph`、`./nexus audit`。

---

## 5. 在 AI 客户端里用（不用记命令）

装好后直接在对话里说触发语，宿主 agent 自己读 `SKILL.md` 并按流程调它的 MCP 工具：

```
用 nexus-deep-research 调研 examples/demo-project 是否该换 Agent 编排框架，
约束：只用本地 MCP；结论要有来源和置信度；交付中文选型报告。
```

中文触发词：`深度搜索`、`调研`、`技术选型`、`对比 X 和 Y`、`研究这个项目`；
英文：`deep research`、`investigate`、`tech selection`。

两种执行模式（`SKILL.md` 开头有对照表）：

- **Agent 驱动**（默认）：宿主 agent 用自带 MCP 工具取数，适合聊天里边看边问；
- **Runtime 驱动**：`nexus run` 由 CLI 自己调 MCP，适合批量、复现、CI。
  一次研究只用一种模式，别在同一迭代里混用。

---

## 6. 报告怎么读

`output/report.md` 结构固定：执行摘要 → 研究范围 → 架构/现状 → 证据 → 分析 → 建议 →
风险 → 参考来源。每个关键结论后面带置信度和来源列表；`output/manifest.json` 记录本次 run
的迭代数、工具调用数、gate 结果，便于复盘。

没证据的部分会**明确写“未证实”**，而不是编一个肯定答案——这是本 skill 的核心契约。

---

## 7. 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| `not a nexus workspace` | 当前目录不是工作区：`./nexus -w <目录> init`，或直接 `./nexus quickstart "…"` |
| `doctor` 里 `chrome-devtools` 不可达 | 需要图形界面 / 浏览器实例；没有时链路自动走 `fetch`，不影响结论 |
| `github` 报 `auth_missing` | 导出 `GITHUB_PERSONAL_ACCESS_TOKEN` 即可；没有 token 也能用，链路退到公开 raw 抓取。**token 只放宿主 MCP 配置或环境变量，绝不写进仓库**（见 `SECURITY.md`） |
| 检索结果集中在一个站点 | SearXNG 返回哪些引擎取决于你的部署：在 `config/mcp-map.yaml` 固定 `servers.searxng.search.engines`，或用 `NEXUS_SEARXNG_ENGINES` 覆盖 |
| gate 一直卡在 `source_diversity` | 你只有一个站点而不是多个来源：`./nexus tasks` 会显示还缺哪些角度 |
| 报告里没有证据表 | 证据没挂到结论上：`./nexus graph propagate` 然后 `./nexus audit` |
| 想完全离线演示 | `./nexus --offline quickstart "…"` → `./nexus --offline run --iterations 1` → `./nexus --offline report` |
| 想把 workspace 发给别人 | 先 `./nexus audit`（扫密钥），再 `./nexus open-source check`（扫私有路径）；对外发布用 `./nexus open-source build` |

---

## 8. 再进一步

```bash
./nexus remember recall "中文 检索 引擎"   # 取回历史有效的查询套路（自进化）
./nexus skill-check --release              # 这个 skill 现在能不能对外发布
./nexus open-source build                  # 生成脱敏安装包 dist/nexus-deep-research + zip
bash install.sh --dest ~/.claude/skills    # 换个宿主再装一遍
```

想改行为：新增 MCP 服务器改 `config/mcp-map.yaml`；新增 Agent 角色加 `agents/<name>.md`
并在 `config/agents.yaml` 授权；改质量门槛改 `config/quality.yaml`；改报告样式改
`templates/report.md`。原理见 `docs/ARCHITECTURE.md`，贡献约定见 `CONTRIBUTING.md`。

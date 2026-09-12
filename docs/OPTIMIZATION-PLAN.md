# NexusSearch-Agent-Skill 优化方案（对齐官方 Agent Skills 规范）

版本：1.2.0 提案 · 编写日期：2026-09-12
规范来源（本次用 GitHub MCP + 原文抓取核对）：

- 规范正文 <https://agentskills.io/specification.md>（Agent Skills 官方规范，由 `anthropics/skills` 指向）
- 权威仓库 <https://github.com/anthropics/skills>（`template/SKILL.md`、`skills/*`、根 `README.md`）
- 参考校验器 <https://github.com/agentskills/agentskills/tree/main/skills-ref>（`skills_ref/validator.py`，即 `skills-ref validate ./my-skill`）
- 创作最佳实践 <https://agentskills.io/skill-creation/best-practices.md>
- 脚本约定 <https://agentskills.io/skill-creation/using-scripts.md>

---

## 1. 官方规范到底要求什么（硬性）

| 项 | 规范要求 | 参考实现常量 |
| --- | --- | --- |
| 目录 | 一个 skill = 一个目录，**最少只要 `SKILL.md`**；`scripts/`、`references/`、`assets/` 为推荐约定 | `find_skill_md()` |
| frontmatter 键 | **只允许** `name` `description` `license` `compatibility` `metadata` `allowed-tools` | `ALLOWED_FIELDS` |
| `name` | ≤64 字符；小写字母/数字/连字符；不得以 `-` 开头或结尾；不得有 `--`；**必须与目录名相同** | `MAX_SKILL_NAME_LENGTH = 64` |
| `description` | 必填非空，≤1024 字符，须同时说明"做什么"和"何时用" | `MAX_DESCRIPTION_LENGTH = 1024` |
| `compatibility` | 可选，≤500 字符，仅在有环境要求时写 | `MAX_COMPATIBILITY_LENGTH = 500` |
| `metadata` | 字符串→字符串映射，版本号等自定义字段放这里 | — |
| 体量 | `SKILL.md` **< 500 行 / 建议 < 5000 token**，细节移到被引用文件 | progressive disclosure |
| 引用 | skill 内部一律用**相对 skill 根**的路径；引用链**只下探一层** | — |
| 脚本 | 不得交互式提问（agent 无 TTY，会永久挂起）；必须有 `--help`；结构化输出走 stdout、诊断走 stderr；幂等、可 `--dry-run`、退出码有意义、输出体量可预期 | using-scripts |
| 校验 | `skills-ref validate ./my-skill` | — |

**规范里没有的东西**：`skill.json`、把 `version:`/`trigger:`/`author:` 写成顶层 frontmatter 键、`.github/`、CI、`AGENTS.md`。它们属于工程增强，可以保留，但**不能与本仓库的规范字段自相矛盾**。

## 2. 差距清单与修复方案

严重度：`P0` 阻断发布/误导用户；`P1` 影响可信度与可维护性；`P2` 打磨项。

| ID | 级别 | 问题（已核实） | 修复 | 验收 |
| --- | --- | --- | --- | --- |
| S1 | **P0** | `skill.json` 的 `homepage` 指向别人的仓库 `nextlevelbuilder/ui-ux-pro-max-skill`（模板残留）；`package` 与 `name` 不一致 | 改为本项目占位仓库，并在 README `致谢` 段落标注真正的参考来源；删除歧义字段 | `nexus skill-check` 断言 homepage 不属于他人仓库；`grep -c nextlevelbuilder skill.json` = 0 |
| S2 | **P0** | `Documentation/` 三份自有业务文档（LTX 微调、带货视频脚本、SKU/付款信息）躺在 MIT 许可证仓库里，且在 `runs/*/input/context/` 与已安装 skill 中被复制 | **不删用户文件**：`.gitignore` + 发布器排除 + `dist/` 产物不含；`runs/` 同样排除出安装包 | `nexus open-source check` 命中 `Documentation/` 即失败；`build` 产物内不再出现业务文档 |
| S3 | **P0** | 隐私泄漏面：`examples/sessions/*` 含 48 处 `/home/jiulang` 绝对路径，`TimeLine.md` 4 处 | 示例会话去绝对路径（掩码为 `$HOME`/相对路径）；`TimeLine.md` 归为工作日志并 gitignore；发布器统一掩码 | `open-source check` 在发布集合内扫描 `/home/`、`C:\Users\` 命中为 0 |
| S4 | P1 | `memory/research-patterns.json` 带着真实 run 名（`ltx`、`nexus-zh-2`、微调）被打包 | 发布包内以空模板替换，运行期各自生成 | 产物中该文件不含私有 run 名 |
| S5 | P1 | 缺少 `CONTRIBUTING.md` / `SECURITY.md` / `CODE_OF_CONDUCT.md` / `NOTICE` / `CODEOWNERS` / CI | 全部补齐；CI 跑 `pytest + skill-check + open-source check` | `.github/workflows/ci.yml` 存在且本地等价命令全绿 |
| S6 | P1 | 6 处版本号需手工同步（`config/settings.yaml`、`runtime/__init__.py`、`runtime/mcp_router.py` CLIENT_INFO、`pyproject.toml`、`skill.json`、`SKILL.md`） | 版本以 `config/settings.yaml` 为单一真源，漂移由 `skill-check` 直接报错 | 任一处不同步 → 校验失败并列出差异 |
| S7 | P1 | `.gitignore` 缺 `.ruff_cache/`、`build/`、`*.egg-info/`、`dist/`；仓库里有 `build/lib/` 与 egg-info 垃圾 | 补全忽略项；发布器排除 | `open-source check` 对未忽略的构建产物报 fail |
| S8 | P1 | `pyproject.toml` 用已废弃的 `license = { text = "MIT" }`，无 `[project.urls]` 与 Python 版本 classifiers；打包把顶层 `config/ agents/ schemas/` 灌进 site-packages，且**不含 `SKILL.md`** | PEP 639 `license = "MIT"` + `license-files`；补 classifiers/urls；数据文件随包安装且 `SKILL.md` 必须在内 | `python -m build` 后 wheel 含 `SKILL.md`，无裸 `config/` 顶层目录 |
| S9 | P1 | 规范字段缺失：无 `compatibility`（本 skill 确有 Python + MCP 环境要求）、无 `license` frontmatter；`SKILL.md` 引用 `workflows/*.md` 等但未说明按需加载 | 补 `license` + `compatibility`；文件引用保持相对 skill 根并标注"按需读取" | `skill-check` 对字段白名单、长度、引用文件存在性全通过 |
| S10 | P2 | `AGENTS.md`（开发规范）与 `SKILL.md`（运行规范）易混淆 | README 明确两者分工并给出「谁是给 agent 读的、谁是给贡献者读的」 | 文档互链可达 |
| S11 | P2 | `scripts/*.py` 是 CLI 便捷壳，需保证无交互、有 `--help`、退出码清晰（对齐 using-scripts） | 补 `--help` 文案与非零退出码；`skill-check` 抽查 | `python3 scripts/build_graph.py --help` 即时返回 |

**保留的优点**（不要动）：零硬编码密钥、`--offline` 全链路可跑、`nexus doctor`、provider 显式开关、日志脱敏、149 个测试（1.2.0 后 189）、schema 契约、CHANGELOG/SemVer 纪律、README 三模式说明。

## 3. 小白上手旅程（本次实现的核心目标）

设计约束：**第一条命令就能看见产出**；任何情况下不出现 traceback；没网也能走完一次完整闭环。

| 步骤 | 命令 | 新手会得到什么 | 兜底 |
| --- | --- | --- | --- |
| 0 装 | `bash install.sh` | 拷进 `~/.agents/skills/nexus-deep-research` 并自检 | 无写权限时提示替代路径 |
| 1 起 | `./nexus quickstart "研究 X"` | 建工作区 + 写 `input/RESEARCH.md` 模板 + 跑 intake + 无网自动 `--offline --dry-run`，最后打印报告落点 | 全程离线可用 |
| 2 写 | 编辑 `input/RESEARCH.md` | 中文模板带注释，标题词表被 `intake` 识别 | 缺标题也能出计划 |
| 3 跑 | `./nexus run` → `./nexus report` | `output/report.md` + `evidence.json` + `graph.json` | 工具失败→降级继续 |
| 4 查 | `./nexus doctor` / `./nexus skill-check` | MCP 通不通、skill 合不合规一目了然 | 不可达服务标 `DISABLED` 而非报错 |

配套交付：`docs/QUICKSTART.md`（5 分钟复制粘贴版）、README 顶部「三步上手」、`install.sh`、`nexus quickstart`。

## 4. 双发布目标（为什么不用删文件）

同一份源码，两个产物，职责不同：

1. **Git 仓库（完整）**：含设计文档 `PRP.md`、`Deep Research Skill 设计方案.md`，供贡献者理解取舍；隐私与业务材料靠 `.gitignore` 挡住。
2. **`dist/` skill bundle（可安装）**：`nexus open-source build` 生成的净化包（只含 skill 载荷，缓存/运行产物/业务文档剔除，路径掩码），是给用户 `cp -R` 进 skills 目录的东西。

这样"开源就绪"不再依赖"手工删文件"，而是**一条可复现命令 + 一个会失败的门禁**。

## 5. 验收结果（1.2.0 已落地）

命令：`./nexus skill-check` · `./nexus open-source check` · `./nexus open-source build` · `bash install.sh`
测试：`python3 -m pytest tests/ -q` → **189 passed**（本次新增 40：`tests/test_skill_spec.py` 覆盖规范规则、
发布门禁、`quickstart`/`install`/`open-source` 三条新手链路，以及两条守卫本仓库的回归门）。

| ID | 状态 | 实测证据 |
| --- | --- | --- |
| S1 | ✅ | `skill.json` 无 `nextlevelbuilder`（`grep -c` = 0），`homepage` 指向本项目，`package` 字段删除；`audit_release` 新增 foreign-homepage 断言并有测试 |
| S2 | ✅ | `Documentation/`、`TimeLine.md` 进 `.gitignore` + `RELEASE_EXCLUDE_DIRS/FILES`；`open-source build` 产物内两者均不存在；本地文件一份未删 |
| S3 | ✅ | `examples/sessions/**` 就地掩码 72 处绝对路径；`privacy: no /home, /Users or drive-letter user paths in the release set` |
| S4 | ✅ | `memory/research-patterns.json` 移出发布集与安装包（`runtime-state` 检查：本地 29 条学习记录留在本机），安装/打包写入空模板 |
| S5 | ✅ | `CONTRIBUTING.md`/`SECURITY.md`/`CODE_OF_CONDUCT.md`/`NOTICE`/`docs/QUICKSTART.md`/`.github/workflows/ci.yml`/`.github/copilot-instructions.md` 齐备；CI 跑 pytest + skill-check + 离线闭环 + 安装器 + 发布门禁 + 产物上传 |
| S6 | ✅ | 6 处版本统一 1.2.0；`version_map` + `skill-check` 会把漂移判为 FAIL（有测试） |
| S7 | ✅ | `.gitignore` 补 `.ruff_cache/ build/ dist/ *.egg-info/ Documentation/ TimeLine.md`；`gitignore` 检查不再报警 |
| S8 | ◐ | PEP 639 `license = "MIT"` + `license-files` + classifiers + `[project.urls]` 已完成；**wheel 载荷问题未重构**（`package-data` 仍把 `config/ agents/ schemas/` 带进 wheel，且不含 `SKILL.md`）——改为在 `pyproject.toml` 与 README 明写「skill 载荷用 `install.sh`/`cp -R` 安装，pip 只装 CLI」，避免假装有 wheel 分发 |
| S9 | ✅ | frontmatter 增 `license` + `compatibility`（291 字符 < 500）；`skill-check` 白名单/长度/引用解析全绿，180 行 < 500 |
| S10 | ✅ | README 新增 Governance 表，区分 `SKILL.md`（执行）与 `AGENTS.md`（实现本仓库的 agent） |
| S11 | ✅ | `scripts/*.py` 三个脚本 `--help` 均即时返回、无交互、退出码可用；CI 有专门 job 守这条 |

新手链路实测（`/tmp` 空目录、`--offline`）：`quickstart` → 建工作区 + 写模板 + 6 问 24 主题 + 任务表 +
下一步命令；`run --iterations 1` → `report` → `output/report.md`（≈11 KB）。四个曾让新手撞 traceback
的口子已修：无目标启动、`intake` 面对未编辑模板只回一句"no spec files"、下一步命令在未建计划时指向
`run`、`install --out`/`--dest` 目录名与 `name` 不一致导致装完加载不了。

门禁当前输出：`skill-check` 7 pass / 0 warn / 0 fail（源码 + 已安装副本），
`open-source check` 15 pass / 0 warn / 0 fail，`open-source build` 102 文件 / 0 泄漏 / 自检通过。

## 6. 落地顺序

1. `runtime/skill_spec.py`：官方规则 + 发布卫生规则的单一校验实现（stdlib only）。
2. CLI：`nexus skill-check`、`nexus quickstart`、`nexus open-source check|build`。
3. 治理文件与 CI；`skill.json` / `pyproject.toml` / `.gitignore` 修复；示例去隐私。
4. 测试（新增命令逐条覆盖）→ 全量回归 → 版本号统一到 1.2.0 → CHANGELOG → 重新同步已安装副本。

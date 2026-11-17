# 主动思考型智能体：开源调研

> 调研时间：2026-11-17
> 问题：现在有没有“能自己主动思考、有长期记忆、能主动开口”的开源软件？它们是怎么设计的？

## 1. 结论先行

**没有开箱即用、与本需求完全对口的成熟产品**，但可复用的设计已经相当成熟：

1. **常驻心跳**（heartbeat / tick）正在取代纯请求-响应：Will、openAGI、OpenKairos、Permafrost、Headlong 都把 agent 做成“一直在跑”的进程。
2. **记忆流**（memory stream）+ **反思**（reflection）是长期记忆的事实标准：Generative Agents 提出，AI Town 等实现验证；Letta 用后台 “Dreaming / sleeptime” 做同类的事。
3. **主动发言必须门控**：模型提议 + 冷却/上限/重要性阈值，几乎没有项目让模型单独决定是否打扰用户。
4. **成本是第一约束**：主流做法是“便宜模型先想，必要时升级”；多数项目把后台思考当成持续计费，需要退避与预算面板。
5. **可观察性决定可用性**：能看见它“正在想什么”，比多聪明的思考都重要。

因此本项目选择：**自己写一个最小但完整的“心跳 + 记忆流 + 反思 + 门控 + SSE 可视化”实现**，而不是套用某个框架。第一版不引入工具与向量库，先把“持续存在感”做扎实。

## 2. 什么算“主动思考”

调研中项目对“主动”的定义差异很大，可以分成四档：

| 档位 | 行为 | 代表 |
| --- | --- | --- |
| L0 请求-响应 | 只有用户说话才动 | 普通 Chatbot |
| L1 定时任务 | 到点执行预设流程（晨报、监控） | OpenDuet routines、cron agent |
| L2 持续认知 | 无输入时也产生想法、形成记忆、更新自我模型 | Generative Agents、AI Town、本项目 |
| L3 自主行动 | 思考后调用工具改变外部世界 | AutoGPT 类、openAGI、Permafrost |

本项目瞄准 **L2**：思考可以连续，行动只限于“记笔记”和“对用户说话”。这是风险/价值比最好的一档，也是“像一个人”与“像一个定时脚本”的分水岭。

## 3. 代表项目逐个看

### 3.1 Generative Agents（Stanford, UIST'23）

- 仓库：<https://github.com/joonspk-research/generative_agents>（研究代码，Django + Smallville 模拟，2023-08 后基本停更）
- 核心：**memory stream** 追加记录 `observation / reflection / plan`，每条带时间戳、描述、1–10 重要性、embedding。
- 检索：`recency × importance × relevance` 加权；论文中 recency = `0.995^小时`，relevance = 向量余弦，重要性由 LLM 写入时打分。
- 反思：累计重要性超过阈值（论文约 150）触发；先生成 3 个高层问题，再各提炼约 5 条带引用的洞察，洞察写回同一记忆流，可对反思再反思，形成树。
- **可借鉴**：记忆条目必须带重要性；检索是三因子而非纯相似度；反思是“问题 → 证据 → 洞察”的两段式，而不是“总结一下”。
- **要避开**：官方实现把检索权重硬编码、无 token 预算、无测试；社区分析指出其 recency 排序存在方向性错误（最久未访问反而得分最高）。不能照抄。

### 3.2 AI Town / ai-town-clone

- 基于 Convex 的 Generative Agents 复刻，`convex/memory.ts` 实现向量检索、`shouldReflect` 阈值 150、`touchMemory` 更新访问时间。
- **可借鉴**：检索命中后更新 `last_access`，让“最近被想起的事”更容易再被想起；反思用累计重要性计数，而不是固定时间。
- **要避开**：依赖向量库与前端框架，demo 体积远大于本项目需要。

### 3.3 Letta（原 MemGPT）

- 仓库：<https://github.com/letta-ai/letta>；当前主开发转向 `letta-ai/letta-code`，旧 V1 API server 归档在 `archive` 分支。
- 定位：**有状态 agent 平台**——记忆、身份、对话跨会话/跨设备保留。
- 记忆分层：core memory blocks（常驻上下文）、recall memory（对话历史）、archival memory（长期存档）。
- 后台整理：文档中的 “Dreaming”（CLI `/sleeptime`）会在后台子代理中回顾近期对话、提炼值得长期保留的经验并更新记忆；可选“Agent reviews before applying”二次审阅。记忆在 agent 的各对话之间共享。
- 成本/容量约束：二次审阅消耗额外 token 且不请求用户批准；云端记忆仓库免费 5 MiB / Pro 50 MiB。
- **可借鉴**：把“后台整理”做成与主对话解耦的独立过程；记忆有容量上限，必须压缩；自我审阅能减少脏记忆。
- **要避开**：完整 memory-block 机制对本项目过重，第一版用表结构 + 检索即可。

### 3.4 Mem0

- 仓库：<https://github.com/mem0ai/mem0>
- 新版架构（官方 README）：**ADD-only 抽取**，不再执行 UPDATE/DELETE；新增事实直接追加，依靠实体链接、多信号检索和时间推理提升命中。
- 检索：语义 + BM25 + 实体匹配三路并行融合，时间感知排序；官方称约 6.7K–7.0K tokens 完成检索。
- 指标（**厂商自测，需谨慎**）：LoCoMo 71.4 → 92.5，LongMemEval 67.8 → 94.4，p50 延迟约 0.88–1.09s。
- **可借鉴**：写入时抽取“实体/关键词”并建链接；检索用多信号而非单一向量；上下文预算要显式。
- **要避开**：ADD-only 会放大重复记忆，需要本项目在写入侧做相似度去重。

### 3.5 Will（mindot-ai/will）

- 仓库：<https://github.com/mindot-ai/will>（Apache-2.0）
- 设计非常“认知科学”：**确定性 1 秒 tick 时钟**，40+ 认知引擎（调控、感知、情感、记忆、执行、元认知、社会关系），行为是“感知 → affordance → 动作竞争 → 执行 → 反馈”。
- **LLM 只在模糊或高风险时被招募**；多数 tick 由确定性引擎完成，每 N tick 一次主 LLM 调用，合成 `[ACTIONS] / [BELIEFS] / [GOALS]`。
- 主动性：ExecutiveEngine 周期性主动运行，不依赖用户输入；GoalManager 动态增删目标；**沉默是合法结果**。
- 记忆：快照 + PMA（Persistent Mind Artifact，约 10–50 KB JSON）携带自我模型、信念、关系、情感基线、技能；hibernate/wake 可跨重启、跨机器、跨模型恢复；含情节巩固、遗忘曲线、间隔重复、梦境回放、可选向量召回。
- **可借鉴**：心智状态要能序列化成一个小文件；不是每次思考都值得调用大模型；主动发言允许“想了但不说话”。
- **要避开**：40+ 引擎对一个 demo 是过度设计；确定性引擎的调参成本很高。

### 3.6 openAGI（Spshulem/openAGI）

- 仓库：<https://github.com/Spshulem/openAGI>
- 本地常驻 daemon：`abi-runtime` 串起信号、审视、记忆、传播；`agent-host` 驱动对话与工具。
- HTTP + **SSE `/events`** 推送会话/任务/审批状态；cron 与后台任务在 daemon 内触发。
- 分层记忆（短/中/长期）带衰减；JSONL 审计 + 原子快照；夜间压缩、纠正覆盖、按作用域隔离。
- 主动观察：macOS 约每 30 秒采集窗口标题与屏幕 OCR（图像留本地，文本进 daemon），生成提醒与建议技能。
- 成本：Credits 面板显示日上限、按模型/活动拆分、30 天账本与逐次审计；base/mini/nano 模型分层。
- **可借鉴**：SSE 是这类产品最合适的实时通道；成本面板是必需品而非附属品；`base/mini/nano` 分层调度。
- **要避开**：屏幕采集属于高隐私风险，本项目第一版不做感知外部世界。

### 3.7 OpenKairos / Permafrost / Chimera-agent

- **OpenKairos**（PyPI）：后台 daemon，`kairos watch` 做项目观察、简报、AutoDream 记忆巩固、任务模式。
- **Permafrost**（dead1786/permafrost）：24/7 自主大脑框架，64 个工具、L1–L6 记忆、调度器、多渠道。
- **Chimera-agent**（PyPI）：带治理与自进化的 agent，有调度/主动性，并公布了相对诚实的 benchmark。
- 三者均来自搜索结果摘要，**未逐行深读**，仅用于确认“常驻 + 分层记忆 + 调度”是普遍形态。

### 3.8 Headlong（Laude × MIT，2026-08）

- 媒体摘要（正文付费墙）：把“持续思考循环”做成 agent harness，消息只是连续思考流里的观察；核心 < 10,000 行 Bash；后台持续计费（摘要提到约 $1–2/小时，未核实）；作者承认存在“熟悉的安全问题”；尚无独立 benchmark。
- **可借鉴**：产品叙事——“你不再提问之后，它还在想”——正是本项目要验证的体验。
- **要避开**：未经验证的性能与安全声明；不把“持续调用大模型”当成默认。

> 说明：搜索摘要中还出现了 OpenDuet（CopilotKit routines）等“定时机器人”项目，它们属于 L1，不作为核心参考。

## 4. 横向对比

| 项目 | 常驻形态 | 记忆 | 反思/巩固 | 主动开口 | 可观察性 | 依赖 |
| --- | --- | --- | --- | --- | --- | --- |
| Generative Agents | 模拟 tick | 记忆流 + 向量 | 累计重要性触发 | 模拟内对话 | 研究日志 | Django/向量 |
| AI Town | 游戏 tick | 向量 + access 更新 | 阈值 150 | 角色对话 | 前端 | Convex |
| Letta | 平台/API | core/recall/archival | Dreaming 后台 | 取决于应用 | 平台 | 较重 |
| Mem0 | 记忆层 | ADD-only + 多信号 | 无（只管存取） | 否 | 一般 | 向量库 |
| Will | 1s tick | PMA + 情节/语义 | 梦境回放/间隔重复 | 是（可沉默） | 快照可回放 | 自研引擎 |
| openAGI | daemon + cron | 三层 + 衰减 | 夜间压缩 | 观察后提醒 | SSE + 面板 | 本地服务 |
| Headlong | 连续思考循环 | 思考图（摘要） | 上下文压缩 | 是 | 日志 | Bash 栈 |
| **思流（本项目）** | 30s tick + 退避 | SQLite 记忆表 + 三因子检索 | 每 N tick 反思 + 自我模型 | 是（六重门控） | SSE 三栏实时 | 仅 FastAPI + SQLite |

## 5. 六个可复用的设计模式

1. **心跳而非请求**：状态机每秒/每几十秒推进一次；用户输入只是“一个高优先级事件”。
2. **写入即打分**：记忆在写入时由模型给重要性，之后不再改；检索时重要性是独立一维。
3. **三因子检索**：相关性、重要性、时间衰减缺一不可；纯向量相似度会让“重要但久远”的记忆永远沉底。
4. **两段式反思**：先问“最近最重要的问题是什么”，再找证据得洞察；比“总结最近对话”产出更具体。
5. **模型提议、规则裁决**：主动发言由模型判断“想不想说”，由代码判断“能不能说”。
6. **成本与沉默都是一等公民**：空闲退避、预算面板、允许“这轮什么都不说”。

## 6. 它们没解决好的问题

- **评估**：几乎没有项目给出“主动消息有用率”或“单位有用思考成本”的公开指标；Headlong 也只有定性日志。
- **记忆污染**：ADD-only 与自动反思都会积累重复/错误记忆；写入去重、来源引用、可撤销是普遍缺口。
- **骚扰边界**：多数项目把发言频率做成配置，而不是学习用户的接受度。
- **长跑稳定性**：daemon 连续跑数天的内存/成本漂移，公开数据很少。
- **安全**：持续运行 + 工具权限 + 外部观察，三者叠加会放大风险；Headlong 的“familiar security problems”并未展开。

## 7. 对本项目的决策

| 决策 | 依据 |
| --- | --- |
| 自研最小内核，不套 Letta/Will | 目标是验证“主动思考的体验”，不是复用平台；依赖越少越容易讲清楚 |
| tick 默认 30s，空闲退避到 5min | 演示节奏与成本折中；Will 的 1s tick 对 LLM 调用没有意义 |
| SQLite + bigram 检索 | 第一版零依赖、可解释；Mem0 的多信号思路用“关键词 tags + 内容”近似 |
| 写入侧去重 | 直接针对 Mem0 ADD-only 与自动反思的重复问题 |
| 反思先按 tick 数 | 比累计重要性阈值更好演示；接口保留，V2 可切换 |
| 六重主动门控 | 综合 Generative Agents 的“重要性”与 openAGI 的成本/频率控制 |
| SSE + 三栏 UI | openAGI 已验证的形态；把“正在想什么”作为第一屏 |
| 不做外部工具/屏幕感知 | 控制安全边界；主动性的价值先在“对话与记忆”里验证 |

## 8. 参考资料

- Generative Agents 论文与代码：<https://github.com/joonspk-research/generative_agents>（arXiv:2304.03442）
- 机制拆解与源码问题：<https://github.com/neoneye/agent-memory-atlas/blob/main/content/systems/generative-agents.md>
- Letta：<https://github.com/letta-ai/letta>；Dreaming/sleeptime：<https://docs.letta.com/guides/agents/sleep-time-agents>
- Mem0：<https://github.com/mem0ai/mem0>
- Will：<https://github.com/mindot-ai/will>
- openAGI：<https://github.com/Spshulem/openAGI>
- Headlong 报道：<https://runtimewire.com/article/laude-headlong-persistent-agent-microharness>（正文付费墙，仅摘要可核实）
- OpenKairos：<https://pypi.org/project/openkairos/>
- Permafrost：<https://github.com/dead1786/permafrost>
- Chimera-agent：<https://pypi.org/project/chimera-agent/>

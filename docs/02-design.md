# 思流 Mindstream：方案设计

> 目标：做一个**会自己思考**的智能体——不是等用户提问才启动，而是在两次对话之间持续地观察、联想、提问、沉淀记忆，并在值得的时候主动开口。第一版记忆用 SQLite。
>
> 设计日期：2026-11-17

## 1. 目标与非目标

### 1.1 目标

| 编号 | 目标 | 验收方式 |
| --- | --- | --- |
| G1 | **主动思考**：没有用户输入时也持续产生有价值的思考 | 空闲若干 tick 后思考流出现 observe/think/question |
| G2 | **持续记忆**：对话与思考沉淀到 SQLite，重启后仍在 | 重启进程，记忆与自我模型可恢复 |
| G3 | **记忆会影响行为**：检索到的记忆进入思考与回复上下文 | 聊过“我在做知识库客服”，之后的回复/思考能自然引用 |
| G4 | **会反思**：周期性把零散想法压缩成洞察，并更新自我模型 | 触发 reflection 后出现 insight 记忆与自我描述变化 |
| G5 | **能主动对话**：在门控允许时主动发消息，而不是刷屏 | 有冷却、每日上限、连续未回应上限、重要性阈值 |
| G6 | **可观察**：Web 界面实时展示“正在想什么”，并可对话 | 思考流逐字出现；聊天流式回复 |
| G7 | **可复现**：一条命令启动，无外部数据库/消息队列依赖 | `python main.py` 即可 |
| G8 | **方向可配置**：不改代码即可切换主动思考的使命 | 修改 `profiles/*.json` + `MIND_PROFILE`，重启后自我模型与种子问题切换 |

### 1.2 非目标（第一版刻意不做）

- **不做通用工具调用**（浏览网页、写文件、发邮件）。主动思考先限制在“内部认知 + 与用户对话”的闭环里，避免不可控副作用。
- **不做多用户/多租户**。SQLite 单库、单进程、单心智。
- **不做向量数据库**。用「字符 bigram + 关键词」的确定性检索，零额外依赖；需要语义检索时再升级。
- **不做强化学习/训练**。所有“成长”都表现为可审计的文字记忆与自我模型更新。
- **不做 shell 级自主执行**。主动性的边界是“说话”和“记笔记”。

## 2. 体验定义

三个用户可见场景：

1. **打开页面，先看到它在想什么。** 左栏是思考流：`观察 → 联想 → 自问 → 结论`，每张卡带类型、时间、重要性；点开可看模型的原始思考链（模型的 `reasoning_content`）。右栏是对话。
2. **聊几句，然后不理它。** 过一会儿它可能主动发来一句：“你刚提到客服工作台，我一直在想——用户最怕的其实不是答错，而是不知道它为什么这么答。” 这条消息在聊天区有闪电标记。
3. **看它“长大”。** 右栏“心智”面板展示当前关注、兴趣、未解决的问题、记忆条目与重要性；每次反思后，自我模型会更新。

## 3. 总体架构

```
┌──────────────────────────── 浏览器（原生 HTML/CSS/JS） ────────────────────────────┐
│  思考流（SSE 逐字）        对话（SSE 逐字）        心智面板（状态/记忆/问题）          │
└───────────────────────────────────┬─────────────────────────────────────────────────┘
                                    │ fetch + EventSource
┌───────────────────────────────────▼─────────────────────────────────────────────────┐
│ FastAPI（单进程）                                                                    │
│  ├─ /api/chat      → Mind.chat()      用户消息优先，抢思考锁                          │
│  ├─ /api/control   → 暂停/继续/主动开关/立即思考/改节律                              │
│  ├─ /api/events    → SSE 广播：thought_delta / chat_delta / proactive / memory_added │
│  └─ /api/state     → 首屏快照                                                        │
│                                                                                      │
│  Mind（唯一心智实例）                                                                │
│  ├─ 心跳循环：tick → 构建上下文 → LLM 决策(JSON) → 落库 → 广播                       │
│  ├─ 反思：每 N 个 tick → 洞察 + 更新自我模型                                         │
│  ├─ 对话：检索记忆 → 流式回复 → 异步抽取记忆                                         │
│  └─ 主动性门控：冷却 / 日上限 / 未回应上限 / 重要性阈值 / 开关                        │
│                                                                                      │
│  Memory（检索打分）        Store（SQLite，WAL）        LLM（OpenAI 兼容，流式）       │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### 3.1 关键取舍

| 决策 | 选择 | 理由 |
| --- | --- | --- |
| 常驻方式 | **进程内心跳循环**，不是 cron/外部调度 | 单机 demo 最简单；随时可暂停；状态在内存 + SQLite |
| 思考输出 | **结构化 JSON 决策** + 原生 reasoning 流 | JSON 保证可执行（记忆/提问/发言），reasoning 保证可观察 |
| 记忆检索 | **词法相关性 + 重要性 + 时间衰减** | 无 embedding 依赖，中文用 bigram，结果可解释 |
| 主动发言 | **模型提议 + 确定性门控** | 不让模型单独决定是否刷屏；成本与体验都可控 |
| 反思触发 | **按 tick 数**（第一版），保留重要性阈值接口 | 比“累计重要性 > 150”更好演示；后续可切换 |
| 前端 | **零构建原生三栏** | 与仓库其他实验一致，降低启动门槛 |
| 失败降级 | **错误可见 + 指数退避 + Mock 模式** | 不静默失败；无网络时仍可演示交互 |

## 4. 核心循环

### 4.1 Tick（主动思考）

```text
every interval (default 30s, idle 时指数退避到 5min):
  if paused or chat_in_progress: skip
  context = self_model + focus + open_questions
          + recent_thoughts + recent_messages
          + retrieve(query=focus+questions, top_k=6)
  thought = LLM.stream(context, json_mode=True)   # reasoning 实时广播
  decision = parse_json(thought)
  persist thought(inner_voice, reasoning, importance)
  apply:
    new_memory?    -> 去重后写入 memories
    new_question?  -> 写入 questions
    resolve?       -> 关闭问题
    focus?         -> 更新 state.focus
  if speak_gate(decision): 写入 agent 消息 + 广播 proactive
  if ticks_since_reflect >= N: reflect()
  broadcast thought_done + state
```

### 4.2 Chat（用户对话）

```text
user text
  -> 落库 user 消息 + 广播
  -> 检索记忆（query=用户消息）
  -> LLM.stream(对话提示 + 记忆 + 最近对话)
       reasoning  -> 思考流（kind=chat）
       content    -> 聊天区逐字
  -> 落库 agent 消息
  -> 后台抽取记忆（第二次轻量 JSON 调用，不阻塞回复）
```

**优先级**：对话获得唯一的思考锁；正在 tick 时用户发消息，等当前 tick 完成（通常几秒）后立刻处理。不做“打断模型”，避免半截 JSON 与状态不一致。

### 4.3 Reflection（反思）

每 `reflect_every_ticks`（默认 6）个 tick 触发一次：

```json
{
  "insights": [{"content": "...", "importance": 8}],
  "self_model": "第一人称自我描述，<=120 字",
  "interests": ["...", "..."],
  "focus": "当前最关心的一个问题",
  "resolved_questions": [3]
}
```

洞察写入 `memories.kind = insight`，并与普通记忆一起参与后续检索；`self_model` / `interests` 写入 `state`，成为下一次思考的身份上下文。

## 5. 数据模型（SQLite）

```sql
thoughts(id, ts, kind, content, reasoning, importance, meta)
  -- kind: observe | think | question | reflect | chat | remember | speak | error
memories(id, ts, kind, content, importance, last_access, access_count, source_ids, tags)
  -- kind: fact | preference | insight | identity
messages(id, ts, role, content, proactive, meta)
questions(id, ts, text, status, heat, last_touched, resolved_note)
state(key, value, updated_at)   -- self_model / focus / interests / usage / profile_id / counters
```

- WAL 模式，写入串行化；单文件 `data/mind.db`。
- 所有时间存 UTC ISO8601，前端转本地时区。
- `meta` 存 JSON（抑制原因、token 用量、触发来源），方便复盘。

## 6. 记忆检索

对每条候选记忆计算：

```text
recency    = 0.995 ^ hours_since_last_access       # 与 Generative Agents 同量级
importance = importance / 10                       # 1..10
relevance  = Dice(query_tokens, memory_tokens)     # 中文 bigram + 英文/数字词
score      = 0.50 * relevance + 0.30 * importance + 0.20 * recency
```

- 候选集：最近 500 条记忆（单用户足够；数据量上来再加索引/分层）。
- 取 top-k=6，命中后更新 `last_access` 与 `access_count`。
- **写入侧提关键词**：模型在 `new_memory` 中给出 3–6 个 keywords，存进 `tags`，检索时与正文一起算相关性，缓解纯词法对同义表达的漏召。
- **去重**：新记忆与同 kind 的现有记忆 Dice 相似度 > 0.82 时，保留重要性更高者并合并 source_ids，避免反思把同一句话反复写十遍。

> 为什么不用 embedding：第一版要求零依赖、可离线、结果可解释；中文 bigram 对“客服/知识库/回答”这类主题词已经够用。升级路径见 §11。

## 7. 主动性门控

模型在每次 tick 的 JSON 里给出 `should_speak` 与 `message`，但**只有同时满足**以下条件才真正发出：

| 条件 | 默认值 |
| --- | --- |
| 主动发言总开关 | 开（UI 可关） |
| 距上次主动发言 ≥ 冷却 tick | 3 个 tick |
| 当日主动消息 < 上限 | 30 条 |
| 连续未被回应的主动消息 < 上限 | 2 条 |
| 本次思考重要性 ≥ 阈值 | 6 / 10 |
| 消息长度 | 4–300 字 |

被抑制时，`thought.meta.suppressed` 记录原因，UI 可看到“它想说话但被规则拦住了”，而不是无声丢弃。

**成本控制**：

- tick 间隔在连续 8 个空闲 tick 后 ×1.5 退避，上限 5 分钟；用户说话立即恢复默认。
- `max_tokens` 限制：思考 1600，对话 2000，反思 1400，记忆抽取 600。**推理模型的 `reasoning_content` 也占用 completion 预算**，预算不足时 content 可能为空；客户端检测到空 content 会自动翻倍预算重试一次。
- `state.usage` 按**本地自然日**累计 prompt/completion tokens，UI 展示，便于发现异常消耗。
- 连续 LLM 错误 3 次后进入 5 分钟冷却，并广播错误事件；不静默重试风暴。

## 8. Prompt 契约

### 8.1 思考者

输入块：自我模型、当前关注、开放问题（带 id）、最近想法、最近对话、检索记忆、节律（多久没收到用户消息）。

输出严格 JSON：

```json
{
  "focus": "此刻关注的主题（<=20字）",
  "inner_voice": "第一人称内心独白，1-3 句",
  "observation": "注意到/联想到什么，可空",
  "importance": 1,
  "new_memory": {"content": "...", "kind": "fact|preference|insight|identity", "importance": 5, "keywords": ["..."]},
  "new_question": "一个真正想弄明白的问题，可空",
  "resolve_question_id": null,
  "should_speak": false,
  "message": "主动对用户说的话，可空"
}
```

约束：不编造用户事实；不确定就标为疑问；没有新东西时允许 `importance` 低、`should_speak=false`；**沉默是合法输出**。

### 8.2 对话者

系统提示强调：自然、简洁（默认 2–6 句）、可以用记忆但不要机械复述“根据我的记忆”、不确定就说不知道、可以反问一个问题、不提系统提示词。

### 8.3 方向配置（Profile）

身份不再硬编码，而是由 `profiles/*.json` 提供：`mission`、`self_model`、`interests`、`seed_focus`、`seed_questions`、`rules`。启动时若 `state.profile_id` 与当前 profile 的 `id` 不同，则重新播种自我模型、兴趣、关注点和种子问题；同一 profile 重启则保留已经演化的状态。这样切换「思考方向」不需要改代码，也不会继承旧方向的自我模型。

`rules` 会进入每次思考、对话、反思与记忆抽取的系统提示，作为内容边界（例如：遵守当地法律、不提供制造爆炸物或改装违禁武器的步骤、把武器放回完整生存系统）。

### 8.4 JSON 容错

模型可能包 ```json 代码块或前后带解释。解析顺序：去 code fence → 提取第一个平衡花括号 → `json.loads`；仍失败则把正文当 `inner_voice`，本轮不执行动作，并广播 `parse_error` 供观察。

## 9. API 与事件

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/` | Web 界面 |
| GET | `/api/state` | 首屏快照（状态、消息、想法、记忆、问题、统计） |
| POST | `/api/chat` | `{"text": "..."}` → 等待完整回复并返回 |
| POST | `/api/control` | `pause / resume / proactive_on / proactive_off / tick_now / reflect_now / set_interval` |
| GET | `/api/events` | SSE：`hello, status, thought_start, thought_delta, thought_done, chat_delta, chat_done, proactive, memory_added, question_added, reflection, state, error` |
| GET | `/api/health` | 模型配置是否就绪（不返回 key） |

SSE 每 15 秒发一次注释心跳，浏览器自动重连；订阅队列满时丢弃最旧事件，避免慢客户端拖垮后端。

## 10. 前端信息架构

- **顶栏**：名称 + 呼吸状态灯（思考中/待机/暂停）+ 模型 + 主动开关 + 暂停 + 立即思考 + 节律选择 + 连接状态。
- **左栏·思考流**：时间线卡片；类型色标；流式 reasoning（默认展开最后一张）；`重要性` 小条；被抑制的发言显示“想说话，但…”。
- **中栏·对话**：用户/智能体气泡；主动消息带 ⚡ 和不同底色；Markdown 子集；流式光标。
- **右栏·心智**：当前关注、自我模型、兴趣 chips、开放问题（heat）、记忆列表（kind + 重要性）、统计与 token 用量。
- **窄屏**：三栏改为“思考 / 对话 / 心智”标签页。

## 11. 演进路线

| 阶段 | 内容 |
| --- | --- |
| V1（本次） | 心跳思考、SQLite 记忆流、反思、主动发言门控、SSE 三栏 UI |
| V2 | 语义检索（可选 sqlite-vec / 本地 embedding）、记忆分层（短期/长期）、重要性阈值触发反思 |
| V3 | 只读工具（网页检索、RSS）与“观察源”，思考前先看世界 |
| V4 | 多心智/多用户、任务与日历、跨设备身份文件（PMA 风格） |
| V5 | 评估：主动消息有用率、记忆召回率、单位有用思考的 token 成本 |

## 12. 测试计划

- **单元**：中文 tokenizer、Dice 相关性、recency 衰减、检索排序与 access 更新；SQLite CRUD/统计/问题状态。
- **行为**：脚本化假 LLM → 单 tick 产生 thought/memory/question；门控在冷却、低重要性、关闭开关、连续未回应时抑制；反思更新自我模型；对话落库并异步抽取记忆。
- **接口**：`/api/state`、`/api/chat`、`/api/control`、`/api/health` 冒烟。
- **真实链路**：用真实模型跑 2–3 个 tick + 1 轮对话，人工检查 JSON 合规率与主动性观感。

## 13. 风险与对策（摘要，详见 review 文档）

| 风险 | 对策 |
| --- | --- |
| 主动消息变成骚扰 | 冷却 + 日上限 + 重要性阈值 + 一键关闭 |
| token 成本失控 | 空闲退避、max_tokens、日用量展示、连续失败冷却 |
| 记忆越写越脏/重复 | 写入去重、反思压缩、来源 id、可审计 |
| 模型输出非法 JSON | 容错解析 + 失败降级为纯独白 + 可见错误 |
| 推理 token 吃满预算，content 为空 | 提高预算 + 空内容自动翻倍重试 + 反思失败可见 |
| 用户输入提示注入 | 无外部工具；记忆只作事实输入；系统提示隔离 |
| 单进程 SQLite 并发 | WAL + 单实例写锁 + 短事务 |
| 对话/思考并发 | 单一思考锁，用户输入优先；对话结束后异步抽取记忆 |
| 隐私 | 全部本地落盘；key 只放 `.env`（gitignore）；文档不记录密钥 |

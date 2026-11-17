(() => {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const KINDS = {
    think: { label: "思考", color: "var(--amber)" },
    observe: { label: "观察", color: "var(--blue)" },
    question: { label: "自问", color: "var(--violet)" },
    reflect: { label: "反思", color: "var(--violet)" },
    chat: { label: "对话思考", color: "var(--teal)" },
    remember: { label: "记忆", color: "var(--teal)" },
    speak: { label: "开口", color: "var(--amber)" },
    error: { label: "错误", color: "var(--rose)" },
  };
  const MEMORY_COLORS = {
    fact: "var(--teal)",
    preference: "var(--blue)",
    insight: "var(--violet)",
    identity: "var(--amber)",
  };
  const FILTERS = [
    [null, "全部"],
    ["think", "只看思考"],
    ["reflect", "只看反思"],
    ["chat", "只看对话"],
    ["remember", "只看记忆"],
    ["error", "只看错误"],
  ];

  const els = {
    thoughts: $("#thoughts"),
    thoughtCount: $("#thoughtCount"),
    filterBtn: $("#filterBtn"),
    chat: $("#chat"),
    chatCount: $("#chatCount"),
    form: $("#chatForm"),
    input: $("#chatInput"),
    sendBtn: $("#sendBtn"),
    mission: $("#mission"),
    tagline: $("#tagline"),
    focus: $("#focus"),
    selfModel: $("#selfModel"),
    interests: $("#interests"),
    questions: $("#questions"),
    questionCount: $("#questionCount"),
    memories: $("#memories"),
    memoryCount: $("#memoryCount"),
    stats: $("#stats"),
    usage: $("#usage"),
    statusPill: $("#statusPill"),
    statusText: $("#statusText"),
    modelChip: $("#modelChip"),
    proactiveBtn: $("#proactiveBtn"),
    pauseBtn: $("#pauseBtn"),
    tickBtn: $("#tickBtn"),
    intervalSel: $("#intervalSel"),
    mindState: $("#mindState"),
    toasts: $("#toasts"),
  };

  const state = {
    messages: new Map(),
    thoughts: new Map(),
    memories: new Map(),
    questions: new Map(),
    streams: new Map(),
    flags: { thinking: false, paused: false, proactive_enabled: true, ticks: 0 },
    stats: {},
    usage: {},
    filter: null,
    connected: false,
    sending: false,
    introShown: false,
    reconnectTimer: null,
    noteShown: false,
  };

  // ---------- 工具 ----------

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function renderMarkdown(text) {
    let src = escapeHtml(text);
    const codes = [];
    src = src.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      codes.push(`<pre><code>${code.replace(/\n$/, "")}</code></pre>`);
      return `\u0000${codes.length - 1}\u0000`;
    });
    src = src.replace(/`([^`\n]+)`/g, "<code>$1</code>");
    src = src.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    src = src.replace(/^#{1,3}\s+(.+)$/gm, "<h3>$1</h3>");
    src = src.replace(/^\s*[-*]\s+(.+)$/gm, "<li>$1</li>");
    src = src.replace(/(<li>[\s\S]*?<\/li>)(?!\s*<li>)/g, "<ul>$1</ul>");
    src = src
      .split(/\n{2,}/)
      .map((block) => {
        if (/^<(ul|pre|h3)/.test(block.trim())) return block;
        return `<p>${block.replace(/\n/g, "<br>")}</p>`;
      })
      .join("");
    src = src.replace(/\u0000(\d+)\u0000/g, (_, i) => codes[Number(i)]);
    return src;
  }

  function timeLabel(ts) {
    if (!ts) return "";
    const date = new Date(ts);
    if (Number.isNaN(date.getTime())) return "";
    return date.toLocaleTimeString("zh-CN", { hour12: false });
  }

  function formatNumber(value) {
    const n = Number(value || 0);
    if (n >= 1000000) return (n / 1000000).toFixed(1) + "M";
    if (n >= 1000) return (n / 1000).toFixed(1) + "k";
    return String(n);
  }

  function isNearBottom(el, margin = 110) {
    return el.scrollHeight - el.scrollTop - el.clientHeight < margin;
  }

  function scrollIfSticky(el) {
    if (isNearBottom(el)) el.scrollTop = el.scrollHeight;
  }

  function toast(message) {
    const node = document.createElement("div");
    node.className = "toast";
    node.textContent = message;
    els.toasts.appendChild(node);
    setTimeout(() => {
      node.style.opacity = "0";
      node.style.transition = "opacity .3s";
      setTimeout(() => node.remove(), 320);
    }, 5200);
  }

  // ---------- 快照与状态 ----------

  function applySnapshot(snapshot) {
    if (!snapshot) return;
    updateFlags(snapshot.flags || {});
    if (snapshot.identity) {
      if (snapshot.identity.tagline && els.tagline) els.tagline.textContent = snapshot.identity.tagline;
      if (snapshot.identity.mission && els.mission) els.mission.textContent = snapshot.identity.mission;
      document.title = `${snapshot.identity.name || "思流"} · 会主动思考的智能体`;
    }
    if (snapshot.settings) {
      els.modelChip.textContent = snapshot.settings.model || "—";
      if (snapshot.settings.llm_mode === "mock") els.modelChip.textContent += " · mock";
      setIntervalValue(snapshot.flags?.interval || snapshot.settings.tick_interval);
    }
    els.focus.textContent = snapshot.focus || "—";
    els.selfModel.textContent = snapshot.self_model || "—";
    renderChips(snapshot.interests || []);
    renderStats(snapshot.stats || {});
    renderUsage(snapshot.usage || {});

    state.messages.clear();
    state.thoughts.clear();
    state.memories.clear();
    state.questions.clear();
    state.streams.clear();
    state.introShown = false;
    els.chat.innerHTML = "";
    els.thoughts.innerHTML = "";
    els.memories.innerHTML = "";
    els.questions.innerHTML = "";
    els.chatCount.textContent = "";

    (snapshot.messages || []).forEach(addMessage);
    (snapshot.thoughts || []).forEach((thought) => {
      addThought(thought);
      finishThought(thought, { quiet: true });
    });
    (snapshot.memories || []).forEach(addMemory);
    (snapshot.questions || []).forEach(addQuestion);

    if (!snapshot.messages || snapshot.messages.length === 0) showIntro();
    updateCounts();
    requestAnimationFrame(() => {
      els.thoughts.scrollTop = els.thoughts.scrollHeight;
      els.chat.scrollTop = els.chat.scrollHeight;
    });
  }

  function updateState(event) {
    if (event.flags) updateFlags(event.flags);
    if (event.mission && els.mission) els.mission.textContent = event.mission;
    if (event.focus !== undefined) els.focus.textContent = event.focus || "—";
    if (event.self_model !== undefined) els.selfModel.textContent = event.self_model || "—";
    if (event.interests) renderChips(event.interests);
    if (event.questions) {
      els.questions.innerHTML = "";
      state.questions.clear();
      event.questions.forEach(addQuestion);
    }
    if (event.stats) renderStats(event.stats);
    if (event.usage) renderUsage(event.usage);
  }

  function updateFlags(flags = {}) {
    state.flags = { ...state.flags, ...flags };
    const f = state.flags;
    document.body.classList.toggle("is-thinking", Boolean(f.thinking) && !f.paused);
    els.statusPill.classList.toggle("offline", !state.connected);
    els.statusPill.classList.toggle("thinking", Boolean(f.thinking) && !f.paused && state.connected);
    els.statusPill.classList.toggle("paused", Boolean(f.paused));
    if (!state.connected) els.statusText.textContent = "重连中";
    else if (f.paused) els.statusText.textContent = "已暂停";
    else if (f.thinking) els.statusText.textContent = "正在思考";
    else els.statusText.textContent = "待机";

    els.pauseBtn.textContent = f.paused ? "继续思考" : "暂停思考";
    els.pauseBtn.classList.toggle("active", Boolean(f.paused));
    els.proactiveBtn.classList.toggle("on", Boolean(f.proactive_enabled));
    els.proactiveBtn.setAttribute("aria-pressed", String(Boolean(f.proactive_enabled)));
    els.mindState.textContent = `tick #${f.ticks || 0} · ${f.thinking ? "思考中" : f.paused ? "暂停" : "待机"}`;
    if (flags.interval) setIntervalValue(flags.interval);
  }

  function setIntervalValue(seconds) {
    if (!seconds) return;
    if (document.activeElement === els.intervalSel) return;
    const options = Array.from(els.intervalSel.options).map((o) => Number(o.value));
    const closest = options.reduce((a, b) => (Math.abs(b - seconds) < Math.abs(a - seconds) ? b : a));
    els.intervalSel.value = String(closest);
  }

  function renderChips(interests) {
    els.interests.innerHTML = "";
    interests.forEach((interest) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = interest;
      els.interests.appendChild(chip);
    });
  }

  function renderStats(stats) {
    const rows = [
      ["思考", stats.thoughts],
      ["记忆", stats.memories],
      ["对话", stats.messages],
      ["开放问题", stats.open_questions],
      ["今日主动", stats.proactive_today],
      ["库大小", stats.db_bytes ? `${Math.max(1, Math.round(stats.db_bytes / 1024))} KB` : "—"],
    ];
    els.stats.innerHTML = rows
      .map(([label, value]) => `<div class="stat"><span>${label}</span><b>${value ?? 0}</b></div>`)
      .join("");
  }

  function renderUsage(usage) {
    const total = formatNumber(usage.total_tokens);
    const calls = usage.calls || 0;
    els.usage.textContent = `今日 ${calls} 次调用 · ${total} tokens`;
    els.usage.title = `prompt ${usage.prompt_tokens || 0} / completion ${usage.completion_tokens || 0}`;
  }

  function updateCounts() {
    els.thoughtCount.textContent = String(state.thoughts.size);
    els.chatCount.textContent = state.messages.size ? `${state.messages.size} 条` : "";
    els.memoryCount.textContent = String(state.memories.size);
    els.questionCount.textContent = String(state.questions.size);
  }

  // ---------- 对话 ----------

  function showIntro() {
    if (state.introShown || state.messages.size) return;
    state.introShown = true;
    const intro = document.createElement("div");
    intro.className = "chat-intro";
    intro.innerHTML = `
      <h3>我一直在。</h3>
      <p>你不说话的时候，我也会继续想。可以从一句话开始，也可以什么都不说，先看看我在想什么。</p>
      <div class="suggestions">
        <button class="suggestion" type="button">你刚才在想什么？</button>
        <button class="suggestion" type="button">记住：我在做一个会主动思考的智能体实验。</button>
        <button class="suggestion" type="button">你觉得主动和打扰的边界在哪？</button>
      </div>`;
    intro.querySelectorAll(".suggestion").forEach((button) => {
      button.addEventListener("click", () => {
        els.input.value = button.textContent;
        autoGrow();
        sendMessage();
      });
    });
    els.chat.appendChild(intro);
  }

  function addMessage(message) {
    if (!message || message.id === undefined) return;
    if (state.messages.has(message.id)) return;
    const intro = $(".chat-intro", els.chat);
    if (intro) intro.remove();

    const wrap = document.createElement("div");
    wrap.className = `msg ${message.role === "user" ? "user" : "agent"}${message.proactive ? " proactive" : ""}`;
    wrap.dataset.id = message.id;

    const meta = document.createElement("div");
    meta.className = "msg-meta";
    if (message.role === "agent") {
      meta.innerHTML = `<span class="who">思流</span>${message.proactive ? '<span class="bolt">⚡ 主动</span>' : ""}<span>${timeLabel(message.ts)}</span>`;
    } else {
      meta.innerHTML = `<span>${timeLabel(message.ts)}</span><span class="who">你</span>`;
    }

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.innerHTML = renderMarkdown(message.content);

    wrap.append(meta, bubble);
    const stick = isNearBottom(els.chat);
    els.chat.appendChild(wrap);
    state.messages.set(message.id, wrap);
    updateCounts();
    if (stick) els.chat.scrollTop = els.chat.scrollHeight;
    if (message.proactive) markChatUnread();
  }

  function startAgentStream(streamId, replyTo) {
    if (!streamId || state.streams.has(streamId)) return;
    const intro = $(".chat-intro", els.chat);
    if (intro) intro.remove();
    const wrap = document.createElement("div");
    wrap.className = "msg agent";
    wrap.innerHTML = `
      <div class="msg-meta"><span class="who">思流</span><span>回复中</span></div>
      <div class="bubble"><span class="typing"><i></i><i></i><i></i></span></div>`;
    els.chat.appendChild(wrap);
    state.streams.set(streamId, { node: wrap, raw: "", replyTo, started: false });
    els.chat.scrollTop = els.chat.scrollHeight;
  }

  function appendChatDelta(streamId, text) {
    const stream = state.streams.get(streamId);
    if (!stream) return;
    stream.raw += text;
    const bubble = $(".bubble", stream.node);
    bubble.innerHTML = renderMarkdown(stream.raw);
    if (!stream.started) {
      stream.started = true;
      $(".msg-meta span:last-child", stream.node).textContent = timeLabel(new Date().toISOString());
    }
    scrollIfSticky(els.chat);
  }

  function finishChat(streamId, event) {
    const stream = state.streams.get(streamId);
    if (!stream) {
      if (event.message) addMessage(event.message);
      return;
    }
    if (event.error) {
      const bubble = $(".bubble", stream.node);
      bubble.innerHTML = `<p style="color:var(--rose)">这次回复没有完成：${escapeHtml(event.error)}</p>`;
      $(".msg-meta span:last-child", stream.node).textContent = "失败";
      state.streams.delete(streamId);
      return;
    }
    stream.node.remove();
    state.streams.delete(streamId);
    if (event.message) addMessage(event.message);
    else if (stream.raw) addMessage({ id: `local-${Date.now()}`, role: "agent", content: stream.raw, ts: new Date().toISOString() });
  }

  async function sendMessage() {
    const text = els.input.value.trim();
    if (!text || state.sending) return;
    state.sending = true;
    els.sendBtn.disabled = true;
    els.input.value = "";
    autoGrow();
    try {
      const response = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
          const data = await response.json();
          detail = data.detail || detail;
        } catch (_) {}
        toast("发送失败：" + detail);
      } else {
        const data = await response.json();
        if (data.message) addMessage(data.message);
      }
    } catch (error) {
      toast("网络错误，消息可能没有送达。");
    } finally {
      state.sending = false;
      els.sendBtn.disabled = false;
      els.input.focus();
    }
  }

  // ---------- 思考流 ----------

  function thoughtNode(thought) {
    const data = KINDS[thought.kind] || { label: thought.kind || "想法", color: "var(--muted)" };
    const article = document.createElement("article");
    article.className = `thought kind-${thought.kind || "think"}${thought.streaming ? " streaming" : ""}`;
    article.dataset.id = thought.id;
    article.dataset.kind = thought.kind || "think";
    article.style.setProperty("--k", data.color);
    article.innerHTML = `
      <div class="thought-body">
        <div class="thought-header">
          <span class="kind">${escapeHtml(data.label)}</span>
          <time>${timeLabel(thought.ts)}</time>
          <span class="imp" title="重要性"><i style="--v:${Math.round((thought.importance || 5) * 10)}%"></i><span>${Math.round(thought.importance || 5)}</span></span>
        </div>
        <p class="voice"></p>
        <div class="obs" hidden></div>
        <div class="reasoning-wrap">
          <button class="reasoning-toggle" type="button">思考链</button>
          <pre class="reasoning"></pre>
        </div>
        <div class="tags"></div>
      </div>`;
    const voice = $(".voice", article);
    voice.textContent = thought.content || (thought.streaming ? "正在想…" : "");
    const pre = $(".reasoning", article);
    if (thought.reasoning) pre.textContent = thought.reasoning;
    const toggle = $(".reasoning-toggle", article);
    toggle.addEventListener("click", () => {
      pre.classList.toggle("open");
      toggle.textContent = pre.classList.contains("open") ? "收起思考链" : "思考链";
    });
    const body = $(".thought-body", article);
    body.addEventListener("dblclick", () => {
      pre.classList.toggle("open");
      toggle.textContent = pre.classList.contains("open") ? "收起思考链" : "思考链";
    });
    return article;
  }

  function addThought(thought) {
    if (!thought || thought.id === undefined) return null;
    if (state.thoughts.has(thought.id)) return state.thoughts.get(thought.id);
    const node = thoughtNode(thought);
    els.thoughts.appendChild(node);
    state.thoughts.set(thought.id, node);
    applyFilter(node);
    updateCounts();
    scrollIfSticky(els.thoughts);
    return node;
  }

  function appendThoughtReasoning(id, text) {
    const node = state.thoughts.get(id);
    if (!node) return;
    const pre = $(".reasoning", node);
    pre.textContent += text;
    if (!pre.classList.contains("open")) {
      pre.classList.add("open");
      $(".reasoning-toggle", node).textContent = "收起思考链";
    }
    pre.scrollTop = pre.scrollHeight;
    scrollIfSticky(els.thoughts);
  }

  function finishThought(thought, options = {}) {
    if (!thought) return;
    let node = state.thoughts.get(thought.id);
    if (!node) node = addThought({ ...thought, streaming: false });
    if (!node) return;
    node.classList.remove("streaming");
    node.dataset.kind = thought.kind || "think";
    const data = KINDS[thought.kind] || { label: thought.kind || "想法", color: "var(--muted)" };
    node.style.setProperty("--k", data.color);
    $(".kind", node).textContent = data.label;
    $(".kind", node).style.color = data.color;
    const voice = $(".voice", node);
    voice.textContent = thought.content || "（这一轮没有留下文字）";
    const pre = $(".reasoning", node);
    if (!pre.textContent && thought.reasoning) pre.textContent = thought.reasoning;
    const imp = $(".imp", node);
    if (imp) {
      const value = Math.round(thought.importance || 5);
      $("i", imp).style.setProperty("--v", `${value * 10}%`);
      $("span", imp).textContent = String(value);
    }
    const observation = thought.meta?.decision?.observation || thought.meta?.observation;
    if (observation) {
      const obs = $(".obs", node);
      obs.hidden = false;
      obs.textContent = observation;
    }
    renderAppliedTags(node, thought);
    if (thought.kind === "error") {
      pre.classList.add("open");
      $(".reasoning-toggle", node).textContent = "收起思考链";
    } else {
      pre.classList.remove("open");
      $(".reasoning-toggle", node).textContent = "思考链";
    }
    if (!options.quiet) updateCounts();
    if (!options.quiet) scrollIfSticky(els.thoughts);
  }

  function renderAppliedTags(node, thought) {
    const tags = $(".tags", node);
    const applied = thought.meta?.applied || thought.meta?.memories || [];
    tags.innerHTML = "";
    applied.forEach((entry) => {
      const text = String(entry);
      let label = "";
      let cls = "tag";
      if (text === "spoke") label = "已开口";
      else if (text.startsWith("suppressed:")) {
        const reasons = {
          cooldown: "想说话 · 冷却中",
          daily_cap: "想说话 · 今日上限",
          unanswered: "想说话 · 上一条还没被回应",
          low_importance: "想说话 · 但不够重要",
          off: "想说话 · 主动已关",
          bad_length: "想说话 · 消息太短",
          declined: "决定不说",
        };
        label = reasons[text.split(":")[1]] || "想说话 · 被抑制";
        if (text !== "suppressed:declined") cls += " warn";
      } else if (text.startsWith("memory:")) {
        const mode = text.split(":")[2];
        label = mode === "merged" ? "并入已有记忆" : "记下一条";
        cls += " ok";
      } else if (text.startsWith("question:")) {
        label = text.endsWith(":touched") ? "旧问题被重新想起" : "提出问题";
      } else if (text.startsWith("resolved:")) {
        label = "想通了一个问题";
      }
      if (!label) return;
      const tag = document.createElement("span");
      tag.className = cls;
      tag.textContent = label;
      tags.appendChild(tag);
    });
    if (thought.meta?.parse_error) {
      const tag = document.createElement("span");
      tag.className = "tag warn";
      tag.textContent = "未返回合法 JSON";
      tags.appendChild(tag);
    }
  }

  function applyFilter(node) {
    if (!state.filter) {
      node.hidden = false;
      return;
    }
    node.hidden = node.dataset.kind !== state.filter;
  }

  function cycleFilter() {
    const index = FILTERS.findIndex(([key]) => key === state.filter);
    const next = FILTERS[(index + 1) % FILTERS.length];
    state.filter = next[0];
    els.filterBtn.textContent = next[1];
    $$(".thought", els.thoughts).forEach(applyFilter);
  }

  // ---------- 心智 ----------

  function addMemory(memory) {
    if (!memory || memory.id === undefined) return;
    if (state.memories.has(memory.id)) {
      const existing = state.memories.get(memory.id);
      $("p", existing).textContent = memory.content;
      return;
    }
    const color = MEMORY_COLORS[memory.kind] || "var(--teal)";
    const node = document.createElement("div");
    node.className = "memory";
    node.style.setProperty("--mc", color);
    node.innerHTML = `
      <p>${escapeHtml(memory.content)}</p>
      <div class="mem-meta"><em>${escapeHtml(memory.kind || "fact")}</em><span>重要性 ${Math.round(memory.importance || 5)}</span></div>`;
    const stick = isNearBottom($(".mind-scroll"));
    els.memories.prepend(node);
    state.memories.set(memory.id, node);
    if (state.memories.size > 60) {
      const last = Array.from(state.memories.entries()).pop();
      if (last) {
        last[1].remove();
        state.memories.delete(last[0]);
      }
    }
    updateCounts();
    if (stick) $(".mind-scroll").scrollTop = 0;
  }

  function addQuestion(question) {
    if (!question || question.id === undefined) return;
    if (state.questions.has(question.id)) return;
    const li = document.createElement("li");
    li.dataset.id = question.id;
    li.innerHTML = `${escapeHtml(question.text)}<span class="heat">热度 ${Math.round(question.heat || 1)}</span>`;
    els.questions.prepend(li);
    state.questions.set(question.id, li);
    updateCounts();
  }

  function markChatUnread() {
    if (window.innerWidth > 1040) return;
    const tab = $('.tab[data-panel="chat"]');
    if (tab && !tab.classList.contains("active")) tab.classList.add("has-new");
  }

  // ---------- 控制 ----------

  async function control(action, value = null) {
    try {
      const response = await fetch("/api/control", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, value }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        toast(data.detail || "操作失败");
        return;
      }
      const data = await response.json();
      updateFlags(data.flags || {});
    } catch (_) {
      toast("网络错误，操作没有生效。");
    }
  }

  function autoGrow() {
    els.input.style.height = "auto";
    els.input.style.height = Math.min(els.input.scrollHeight, 150) + "px";
  }

  // ---------- SSE ----------

  function handleEvent(event) {
    switch (event.type) {
      case "hello":
        applySnapshot(event.state);
        break;
      case "snapshot":
        applySnapshot(event.state);
        break;
      case "status":
        updateFlags(event);
        break;
      case "state":
        updateState(event);
        break;
      case "thought_start":
        addThought({ ...event.thought, streaming: true });
        break;
      case "thought_delta":
        if (event.text) appendThoughtReasoning(event.id, event.text);
        break;
      case "thought_done":
        finishThought(event.thought);
        break;
      case "chat_start":
        startAgentStream(event.id, event.reply_to);
        break;
      case "chat_delta":
        appendChatDelta(event.id, event.text);
        break;
      case "chat_done":
        finishChat(event.id, event);
        break;
      case "user_message":
        addMessage(event.message);
        break;
      case "proactive":
        addMessage(event.message);
        break;
      case "memory_added":
        addMemory(event.memory);
        break;
      case "question_added":
        addQuestion(event.question);
        break;
      case "reflection":
        toast("刚刚完成一次反思，自我模型已更新。");
        if (event.self_model) els.selfModel.textContent = event.self_model;
        if (event.focus) els.focus.textContent = event.focus;
        break;
      case "parse_error":
        toast("模型这一轮没有返回合法 JSON，只保留了独白。");
        break;
      case "error":
        toast(event.message || "发生了一个错误");
        break;
      default:
        break;
    }
  }

  function connect() {
    if (state.source) state.source.close();
    const source = new EventSource("/api/events");
    state.source = source;
    source.onopen = () => {
      state.connected = true;
      els.statusPill.classList.remove("offline");
      updateFlags(state.flags);
    };
    source.onmessage = (message) => {
      if (!message.data) return;
      try {
        handleEvent(JSON.parse(message.data));
      } catch (error) {
        console.error("bad event", error, message.data);
      }
    };
    source.onerror = () => {
      state.connected = false;
      els.statusPill.classList.add("offline");
      els.statusText.textContent = "重连中";
      document.body.classList.remove("is-thinking");
    };
  }

  // ---------- 初始化 ----------

  function bind() {
    els.form.addEventListener("submit", (event) => {
      event.preventDefault();
      sendMessage();
    });
    els.input.addEventListener("input", autoGrow);
    els.input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
        event.preventDefault();
        sendMessage();
      }
    });
    els.pauseBtn.addEventListener("click", () => control(state.flags.paused ? "resume" : "pause"));
    els.proactiveBtn.addEventListener("click", () =>
      control(state.flags.proactive_enabled ? "proactive_off" : "proactive_on")
    );
    els.tickBtn.addEventListener("click", async () => {
      els.tickBtn.disabled = true;
      await control("tick_now");
      setTimeout(() => (els.tickBtn.disabled = false), 1200);
    });
    els.intervalSel.addEventListener("change", () => control("set_interval", Number(els.intervalSel.value)));
    els.filterBtn.addEventListener("click", cycleFilter);
    $$(".tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        $$(".tab").forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        $$(".panel").forEach((panel) => panel.classList.remove("active"));
        const panel = $(`#panel-${tab.dataset.panel}`);
        if (panel) panel.classList.add("active");
        if (tab.dataset.panel === "chat") {
          tab.classList.remove("has-new");
          els.chat.scrollTop = els.chat.scrollHeight;
        }
      });
    });
  }

  async function boot() {
    bind();
    showIntro();
    connect();
    try {
      const response = await fetch("/api/state", { cache: "no-store" });
      if (response.ok && !state.messages.size) {
        const snapshot = await response.json();
        if (!state.thoughts.size && !state.messages.size) applySnapshot(snapshot);
      }
    } catch (_) {}
  }

  boot();
})();

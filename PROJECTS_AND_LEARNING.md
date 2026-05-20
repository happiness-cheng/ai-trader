# AI Trader + 知识库 — 项目总结 & Agent/LLM 学习路线

## 一、项目总结

### 项目 1：知识库应用 (knowledge-base)

**一句话**：个人知识管理平台，支持知识图谱可视化 + AI 智能问答。

| 维度 | 内容 |
|------|------|
| 后端 | Python FastAPI + SQLite + SQLAlchemy |
| 前端 | React + Vite + react-force-graph-2d + Zustand |
| AI | Claude API（Anthropic SDK） |
| 桌面化 | PyInstaller + tkinter |
| 路径 | `C:\Users\陈独秀\knowledge-base\` |

**核心功能**：
- 知识条目的 CRUD（创建/读取/更新/删除）
- 知识图谱可视化（react-force-graph-2d 力导向图）
- AI 问答（调 Claude API 基于知识库内容回答）
- 实体关系管理（entities + relations）
- 搜索节点（模糊匹配）
- 桌面应用打包（PyInstaller）

**技术亮点（面试用）**：
- 知识图谱的数据建模（实体-关系模型）
- React 状态管理（Zustand 轻量方案）
- FastAPI 异步 API 设计
- Claude API 集成（Prompt 设计 + 上下文注入）

---

### 项目 2：AI Trader 自动交易系统

**一句话**：基于 LLM Agent 的 A 股自动分析与交易系统，Rule Engine + LLM 双层决策 + GUI 自动化执行。

| 维度 | 内容 |
|------|------|
| 数据采集 | curl + 新浪财经/东方财富 API |
| 技术指标 | ta 库（MACD/RSI/均线/布林带） |
| AI 决策 | MiMo API（Claude 兼容接口） |
| GUI 自动化 | pywinauto + UIA backend → 同花顺 v9.50.90 |
| Web 仪表盘 | FastAPI + 原生 HTML/JS |
| 推送通知 | 飞书 Webhook |
| 图表 | matplotlib (Agg 后端) |
| 调度 | Windows Task Scheduler + Python 后台进程 |
| 路径 | `C:\Users\陈独秀\ai-trader\` |

**核心功能**：
- 全市场扫描（沪深300 40只股票，5分钟一轮）
- 双层决策（Rule Engine 筛选 → LLM 深度分析）
- 自动下单（pywinauto 操控同花顺 GUI）
- 条件预警（价格到达目标自动通知）
- 预测追踪（记录 AI 决策，自动对比实际结果）
- K 线图生成（matplotlib）
- 飞书推送（止损/止盈/买卖/日报）
- Web 仪表盘（持仓/大盘/图表/分析）

**技术亮点（面试用）**：
- Agent 设计模式：Rule(筛选) + LLM(判断) + Tool(下单)
- Prompt Engineering：注入历史准确率实现 Agent 自我校准
- RAG 思想：每次决策前检索历史预测作为上下文
- GUI 自动化：pywinauto UIA 探查 + 控件定位
- 绕过代理 TLS 问题：curl subprocess 替代 Python requests

---

### 项目组合（简历上怎么写）

**项目名**：基于 LLM Agent 的智能投研系统

**一句话**：知识图谱驱动的投资分析平台，Agent 自动采集行情、分析技术指标、通过 LLM 做决策、自动执行交易，并将决策过程存入知识图谱用于持续学习。

**架构**：

```
                    ┌──────────────────────┐
                    │   知识库 (项目1)       │
                    │  知识图谱 + AI 问答    │
                    └──────────┬───────────┘
                               │
           ┌───────────────────┼───────────────────┐
           │                   │                   │
     交易日志自动归档      决策经验沉淀       历史经验检索
           │                   │                   │
           └───────────────────┼───────────────────┘
                               │
                    ┌──────────▼───────────┐
                    │   AI Trader (项目2)    │
                    │  Agent 自动分析+交易   │
                    └──────────────────────┘
```

**简历话术**：
基于 LLM Agent 的智能投研系统。知识图谱层（React+FastAPI+SQLite）管理实体关系与决策经验；Agent 层采用双层决策架构——Rule Engine（MACD/RSI/均线/成交量）全市场快速筛选，LLM（MiMo API）对候选标的深度分析并输出结构化决策（买/卖/观望+置信度+推理链）；执行层通过 pywinauto UIA 自动化操控同花顺 v9.50.90 完成委托提交。Agent 具备自校准能力：每次决策记录到预测追踪系统，自动对比实际表现，下次分析时注入历史准确率调整置信度。飞书 Webhook 实时推送关键事件。沪深300 覆盖，5 分钟分析周期。

---

## 二、Agent/LLM 学习路线

### 你现在的位置

```
✅ 已掌握：后端开发（FastAPI/Python/数据库）
✅ 已掌握：前端基础（React/Vite）
⚠️ 部分了解：Claude API 调用（用过但没深入）
❌ 零基础：Agent 架构、Prompt Engineering、RAG、向量数据库、模型评估
```

### 学习路线（6周，与秋招时间线对齐）

#### 第 1 周：LLM 基础 + Prompt Engineering

**目标**：理解大模型是怎么工作的，能写出高质量的 prompt

| 天 | 学什么 | 怎么练 | 对应项目 |
|----|--------|--------|---------|
| 1-2 | Transformer 架构基础 | 看图理解 self-attention、位置编码、KV cache | 面试八股 |
| 3-4 | Token、Temperature、Top-P | 在 Claude API 里调参数看输出变化 | AI Trader 的 ai_analyzer.py |
| 5-6 | Prompt Engineering 核心技巧 | 重写 AI Trader 的分析 prompt | 项目实战 |
| 7 | 结构化输出（JSON mode） | 练习让 LLM 返回稳定的 JSON | ai_analyzer.py 的 JSON 解析 |

**推荐资料**：
- Anthropic 官方 Prompt Engineering 指南：https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering
- OpenAI Prompt Engineering Guide：https://platform.openai.com/docs/guides/prompt-engineering
- 3Blue1Brown - Transformers 可视化：https://www.youtube.com/watch?v=wjZofJX0v4M

**本周检查点**：能解释 attention 机制、temperature 的作用、few-shot vs zero-shot 的区别

---

#### 第 2 周：Agent 架构（核心）

**目标**：理解 Agent 是什么，怎么设计一个能自主决策的系统

| 天 | 学什么 | 怎么练 | 对应项目 |
|----|--------|--------|---------|
| 1-2 | ReAct 模式（推理+行动） | 分析 AI Trader 的决策流程是否符合 ReAct | 项目实战 |
| 3-4 | Tool Use（函数调用） | 给 Claude 定义 tool schema，让它自己选择调用 | 扩展 AI Trader |
| 5-6 | Agent 循环（Plan→Act→Observe） | 设计一个完整的 Agent 循环 | 面试手写 |
| 7 | Multi-Agent（多 Agent 协作） | 理解 CrewAI/AutoGen 的设计思路 | 面试加分项 |

**推荐资料**：
- Lilian Weng - LLM Powered Autonomous Agents：https://lilianweng.github.io/posts/2023-06-23-agent/
- ReAct 论文（精读）：https://arxiv.org/abs/2210.03629
- Anthropic Tool Use 文档：https://docs.anthropic.com/en/docs/build-with-claude/tool-use
- LangChain Agent 文档（看架构，不一定用）：https://python.langchain.com/docs/concepts/agents

**本周检查点**：能在白板上画出 Agent 的完整循环图、解释 ReAct 和 Chain-of-Thought 的区别

---

#### 第 3 周：RAG（检索增强生成）

**目标**：掌握让 LLM 基于外部知识回答问题的技术

| 天 | 学什么 | 怎么练 | 对应项目 |
|----|--------|--------|---------|
| 1-2 | Embedding + 向量数据库 | 用 sentence-transformers 生成 embedding | 知识库项目 |
| 3-4 | 文档分块策略 | 实验不同 chunk size 对检索质量的影响 | 知识库项目 |
| 5-6 | 检索 + 生成 pipeline | 实现完整的 RAG pipeline | 知识库扩展 |
| 7 | RAG 评估（召回率/准确率） | 写测试用例验证 RAG 效果 | 面试考点 |

**推荐资料**：
- LangChain RAG 教程（看流程，不一定用框架）：https://python.langchain.com/docs/tutorials/rag/
- Pinecone 学习中心：https://www.pinecone.io/learn/
- Chunking 策略综述：https://www.pinecone.io/learn/chunking-strategies/
- 向量数据库对比（Milvus vs Qdrant vs Chroma）：https://benchmark.vectorview.ai/

**本周检查点**：能从零实现一个 RAG pipeline（chunk → embed → store → retrieve → generate）

---

#### 第 4 周：知识图谱 + GraphRAG

**目标**：理解知识图谱怎么和 LLM 结合

| 天 | 学什么 | 怎么练 | 对应项目 |
|----|--------|--------|---------|
| 1-2 | 知识图谱基础（三元组、实体、关系） | 分析你的知识库项目的实体关系模型 | 知识库项目 |
| 3-4 | Neo4j / 图数据库入门 | 装一个 Neo4j，跑 Cypher 查询 | 扩展知识库 |
| 5-6 | GraphRAG（微软） | 理解 GraphRAG 的 Local/Global 搜索 | 面试加分项 |
| 7 | 知识图谱 + LLM 联合推理 | 设计一个能用图谱回答多跳问题的系统 | 项目合并 |

**推荐资料**：
- Neo4j 入门教程：https://neo4j.com/developer/get-started/
- GraphRAG 论文：https://arxiv.org/abs/2404.16130
- 知识图谱与大模型结合综述：https://arxiv.org/abs/2310.07581

**本周检查点**：能解释什么是三元组、Cypher 基本查询、GraphRAG 和传统 RAG 的区别

---

#### 第 5 周：模型评估 + Fine-tuning 基础

**目标**：知道怎么评估 LLM 效果，了解 fine-tuning 的适用场景

| 天 | 学什么 | 怎么练 | 对应项目 |
|----|--------|--------|---------|
| 1-2 | LLM 评估方法（人工/自动/BLEU/ROUGE） | 给 AI Trader 的分析结果写评估脚本 | 项目实战 |
| 3-4 | Fine-tuning 原理（LoRA/QLoRA） | 理解什么场景需要 fine-tuning | 面试八股 |
| 5-6 | RLHF / DPO 基础 | 理解模型对齐（alignment）的概念 | 面试八股 |
| 7 | 成本优化（Prompt Caching/Batching） | 给 AI Trader 加 prompt caching | 项目优化 |

**推荐资料**：
- Hugging Face PEFT 文档：https://huggingface.co/docs/peft
- Anthropic Prompt Caching：https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching
- LLM 评估综述：https://arxiv.org/abs/2307.03109

**本周检查点**：能解释 LoRA 的原理、什么时候该 fine-tuning 什么时候该用 RAG

---

#### 第 6 周：项目整合 + 面试准备

**目标**：把两个项目串起来，准备好面试话术

| 天 | 做什么 |
|----|--------|
| 1-2 | 把 AI Trader 日志接入知识库，实现自动归档 |
| 3-4 | 写项目 README + 架构图 + demo 视频 |
| 5-6 | 准备面试问答（见下方高频问题） |
| 7 | 模拟面试 |

**Agent/LLM 面试高频问题**：

| 问题 | 你的回答要点 |
|------|-------------|
| Agent 和普通 LLM 调用有什么区别？ | Agent 有自主决策循环（Plan→Act→Observe），能调用工具，普通调用是单次问答 |
| 你怎么设计 Agent 的决策流程？ | 双层：Rule 快速筛选减少 LLM 调用成本，LLM 做最终判断并输出结构化决策 |
| RAG 的 chunk size 怎么选？ | 看场景，金融文本需要保留完整段落（500-1000 token），代码按函数分块 |
| 怎么评估 LLM 输出质量？ | 结构化输出验证（JSON schema）+ 准确率追踪 + 人工抽样 |
| Agent 的 hallucination 怎么处理？ | 限制输出格式（JSON）+ 引用数据源 + 温度调低 + 二次验证 |
| Fine-tuning 和 RAG 怎么选？ | RAG 适合知识密集型（事实查询），Fine-tuning 适合风格/格式调整 |
| 你的项目怎么处理错误恢复？ | Agent 设有降级策略（AI 失败时用规则兜底），Circuit Breaker 模式 |

---

### 每周时间分配建议

| 时段 | 做什么 |
|------|--------|
| 周一-周五 每天 1-2 小时 | 学习理论 + 代码练习 |
| 周六 3-4 小时 | 项目实战（改进 AI Trader 或知识库） |
| 周日 2 小时 | 复盘本周所学 + 更新简历 |

---

### 学完后你的简历竞争力

| 技能 | 你的项目能证明 |
|------|---------------|
| Agent 设计 | ✅ 双层决策架构，有完整代码 |
| Prompt Engineering | ✅ 优化过的分析 prompt + 自我校准机制 |
| RAG | ✅ 知识库的检索增强问答 |
| 知识图谱 | ✅ 实体关系模型 + 可视化 |
| 工程能力 | ✅ 后端/前端/自动化/调度/日志全栈 |
| 系统设计 | ✅ 高可用（降级/重试/缓存） |

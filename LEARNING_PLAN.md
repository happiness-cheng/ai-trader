# Agent/LLM 学习路线（6周计划）

## 目标：秋招投 AI Agent / 大模型 岗位

---

## 第1周：LLM 基础 + Prompt Engineering

### Day 1-2：Transformer 架构
- [ ] 看 3Blue1Brown Transformers 视频：https://www.youtube.com/watch?v=wjZofJX0v4M
- [ ] 理解 self-attention 计算过程（Q·K^T / √d_k → softmax → ·V）
- [ ] 理解 KV Cache 为什么能加速推理
- [ ] 理解 positional encoding 的作用

### Day 3-4：Prompt Engineering
- [ ] 读 Anthropic 官方指南：https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering
- [ ] 练习：system prompt / few-shot / Chain-of-Thought
- [ ] 练习：让 Claude 稳定输出 JSON
- [ ] 练习：给 AI Trader 的分析 prompt 做优化

### Day 5-6：Token / Temperature / 参数
- [ ] 理解 token 是什么（tokenizer 原理）
- [ ] 理解 temperature（随机性）和 top_p（采样范围）
- [ ] 在代码里调参数看输出变化
- [ ] 理解 context window 和计费方式

### Day 7：本周复习
- [ ] 写 3 个面试问答："Transformer 的核心创新是什么" / "Attention 的计算过程" / "KV Cache 为什么快"
- [ ] 能讲清 AI Trader 的 prompt 是怎么设计的
- [ ] 笔记存入知识库

---

## 第2周：Agent 架构 + MCP 协议（核心）

### Day 1-2：Agent 基础概念
- [ ] 读 Lilian Weng 博客：https://lilianweng.github.io/posts/2023-06-23-agent/
- [ ] 理解 Agent = LLM + Tool + Memory + Loop
- [ ] 理解 ReAct 模式（Reasoning + Acting）
- [ ] 对比 ReAct 和 Chain-of-Thought

### Day 3-4：Tool Calling / Function Calling
- [ ] 读 Anthropic Tool Use 文档：https://docs.anthropic.com/en/docs/build-with-claude/tool-use
- [ ] 练习：定义 tools schema，让 Claude 自动选择调用
- [ ] 练习：处理工具调用失败的情况
- [ ] 对照 AI Trader 的执行层代码理解

### Day 5-6：MCP 协议 + Agent 循环设计
- [ ] 读 MCP 官方文档：https://modelcontextprotocol.io/docs
- [ ] 理解 MCP = 通用协议层（让 Agent 能调用任意外部服务）
- [ ] 理解 MCP Server / Client / Resource / Tool 的关系
- [ ] 动手：给你的知识库项目写一个 MCP Server（暴露搜索工具）
- [ ] 理解 Plan → Act → Observe → Repeat 循环
- [ ] 理解怎么防止 Agent 死循环（max_steps / timeout / 变化检测）
- [ ] 读 ReAct 论文：https://arxiv.org/abs/2210.03629

### Day 7：本周复习
- [ ] 写 3 个面试问答："Agent 循环怎么设计" / "MCP 解决什么问题" / "怎么防止死循环"
- [ ] 能在白板上画 Agent 循环图 + MCP 架构图
- [ ] 面试模拟：讲一遍"你的 Agent 怎么做决策"

---

## 第3周：RAG（必学）

### Day 1-2：RAG 基础
- [ ] 理解 RAG 流程：文档 → chunk → embed → store → retrieve → generate
- [ ] 理解 embedding 是什么（文本 → 向量）
- [ ] 理解 cosine similarity 相似度计算
- [ ] 读 LangChain RAG 教程：https://python.langchain.com/docs/tutorials/rag/

### Day 3-4：向量数据库
- [ ] 装 Chroma（最简单的向量数据库）
- [ ] 练习：存入文档 → 查询相似文档
- [ ] 对比 Milvus / FAISS / Chroma / pgvector
- [ ] 理解 HNSW 索引原理（面试可能问）

### Day 5-6：Chunk 策略 + 检索优化
- [ ] 实验不同 chunk size（256/512/1024 token）对效果的影响
- [ ] 理解 chunk overlap 的作用
- [ ] 理解 hybrid search（向量 + 关键词）
- [ ] 理解 rerank 的作用和原理

### Day 7：本周复习
- [ ] 从零实现一个 RAG pipeline
- [ ] 写 3 个面试问答："chunk 怎么选大小" / "embedding 模型怎么选" / "hybrid search 怎么做"
- [ ] 把知识库项目升级为真正的 RAG

---

## 第4周：Multi-Agent + LangGraph

### Day 1-2：Multi-Agent 基础
- [ ] 理解为什么需要 Multi-Agent（任务分解 + 专家分工）
- [ ] 对比主流框架：CrewAI / AutoGen / LangGraph
- [ ] 理解 Agent 间通信模式：顺序执行 / 并行 / 辩论 / 监督者
- [ ] 读 LangGraph 官方教程：https://langchain-ai.github.io/langgraph/

### Day 3-4：LangGraph 实战
- [ ] 动手：用 LangGraph 搭建一个 3-Agent 协作系统（研究员+写手+审稿人）
- [ ] 理解 StateGraph 的节点和边
- [ ] 理解条件分支（conditional edges）和人工干预（human-in-the-loop）
- [ ] 理解 checkpoint 和状态持久化

### Day 5-6：知识图谱基础（轻量化）
- [ ] 理解三元组（实体-关系-实体）
- [ ] 装 Neo4j Desktop，跑基本查询
- [ ] 理解 GraphRAG 和传统 RAG 的区别
- [ ] 知道什么时候用 GraphRAG（多跳推理问题）

### Day 7：本周复习
- [ ] 写 3 个面试问答："Multi-Agent 怎么协作" / "LangGraph 的核心概念" / "GraphRAG vs RAG"
- [ ] 面试模拟：画一个 Multi-Agent 架构图并讲解

---

## 第5周：工程能力 + 评估（实用周）

### Day 1-2：工程能力（重点）
- [ ] token streaming（SSE 实现，理解增量输出原理）
- [ ] Prompt Caching（Anthropic 支持，减少延迟和成本）
- [ ] 并发工具调用（asyncio.gather）
- [ ] 错误处理和降级策略（重试 + fallback + 超时）
- [ ] context 压缩（摘要 + 滑动窗口，防止 token 爆炸）

### Day 3-4：LLM 评估
- [ ] 理解评估方法：人工 / 自动 / BLEU / ROUGE / LLM-as-Judge
- [ ] 给 AI Trader 写评估脚本（预测准确率）
- [ ] 理解 A/B testing 在 LLM 应用中的作用
- [ ] 面试考点："你怎么评估 LLM 输出质量"

### Day 5-6：Fine-tuning 基础（轻量化）
- [ ] 理解 Fine-tuning vs RAG 的选择（什么时候该 fine-tune）
- [ ] 理解 LoRA（低秩适配）原理
- [ ] 理解 RLHF / DPO（模型对齐）
- [ ] 知道 fine-tuning 的适用场景（风格/格式/领域微调）

### Day 7：本周复习
- [ ] 写 3 个面试问答："token streaming 怎么实现" / "Fine-tuning vs RAG 怎么选" / "怎么做缓存减少延迟"
- [ ] 工程代码整理到 GitHub

---

## 第6周：项目整合 + 面试准备

### Day 1-2：项目整合
- [ ] AI Trader 日志自动归档到知识库
- [ ] 知识库历史经验反哺 AI Trader 决策
- [ ] 生成 HTML 每日报告
- [ ] 写完整的项目 README

### Day 3-4：简历 + 话术
- [ ] 更新简历：两个项目都写上
- [ ] 准备 3 分钟项目介绍
- [ ] 准备"你遇到的最大挑战是什么"
- [ ] 准备"你的 Agent 怎么防止死循环"

### Day 5-6：模拟面试
- [ ] 找同学/朋友模拟面试
- [ ] 练习手写 Agent 循环伪代码
- [ ] 练习画系统架构图
- [ ] 练习回答"你的项目有什么创新点"

### Day 7：最终检查
- [ ] GitHub 代码整理完毕
- [ ] README 和文档齐全
- [ ] 简历定稿
- [ ] 开始投递

---

## 每日时间分配

| 时段 | 做什么 |
|------|--------|
| 早上 | 看行情（AI Trader 自动跑，你看飞书通知） |
| 上午/下午 | 学习（按上面计划） |
| 15:05 | 看日报 + 学习复盘 |
| 晚上 1-2h | 项目实战（改进代码） |

## 面试前检查清单

- [ ] 能画出 Agent 循环图
- [ ] 能解释 Transformer / Attention
- [ ] 能讲清 RAG 流程和 chunk 策略
- [ ] 能讲清 MCP 协议和它的作用
- [ ] 能讲清 Multi-Agent 协作模式
- [ ] 能讲清你的双层决策架构
- [ ] 能讲清怎么防止 Agent 死循环
- [ ] 能讲清 token streaming 怎么实现
- [ ] 能讲清 Fine-tuning vs RAG 怎么选
- [ ] 能在白板上画出系统架构图

# Agent/LLM 学习路线（9周计划）

## 基础信息
- 起点：C++ 基础，SQL Server 用过，Python 靠自己看代码过渡
- 目标：2026年9-10月秋招，投 AI Agent / 大模型 岗位
- 时间：2026年5月 → 7月底完成

---

## 第1周：Python 速通 + FastAPI 入门

### Day 1-3：Python 语法（C++→Python 自学过渡）
- [ ] 自学 Python 基础语法（变量、循环、函数、class）
- [ ] 理解 Python 特有：列表推导式、装饰器、with 语句
- [ ] 读 ai-trader 的 main.py，能看懂每一行
- [ ] 遇到不懂的查资料，不理解的记下来

### Day 4-7：FastAPI + 数据库
- [ ] FastAPI 官方教程（第一个应用）：https://fastapi.tiangolo.com/zh/tutorial/first-steps/
- [ ] 理解路由、请求/响应模型、依赖注入
- [ ] 读 ai-trader 的 dashboard.py，理解每个路由的作用
- [ ] 练习：给 dashboard.py 加一个新接口

---

## 第2周：FastAPI 进阶 + 项目实战

### Day 1-4：FastAPI 核心
- [ ] Pydantic 数据验证（请求体校验）
- [ ] 异步编程（async/await，对比 C++20 协程）
- [ ] 中间件、CORS、错误处理
- [ ] 静态文件、模板渲染（Jinja2）

### Day 5-7：项目实战
- [ ] 改造 ai-trader 的 dashboard.py，加一个"查看持仓"页面
- [ ] 理解 ai-trader 的完整请求链路（前端→路由→策略→返回）
- [ ] 写笔记：FastAPI 和你之前用过的框架有什么不同

---

## 第3周：LLM 基础 + Prompt Engineering

### Day 1-2：Transformer 架构
- [ ] 看 3Blue1Brown Transformers 系列：https://www.youtube.com/watch?v=wjZofJX0v4M
- [ ] 理解 self-attention（Q·K^T / √d_k → softmax → ·V）
- [ ] 理解 KV Cache 为什么能加速推理
- [ ] 理解 positional encoding 的作用

### Day 3-4：Prompt Engineering
- [ ] 读 Anthropic 官方指南：https://docs.anthropic.com/en/docs/build-with-claude/prompt-engineering
- [ ] 练习：system prompt / few-shot / Chain-of-Thought
- [ ] 练习：让 Claude 稳定输出 JSON
- [ ] 优化 ai-trader 的分析 prompt

### Day 5-7：Token / 参数 + 复习
- [ ] 理解 token（tokenizer 原理）、temperature、top_p
- [ ] 理解 context window 和计费方式
- [ ] 写 3 个面试问答存入知识库
- [ ] 能讲清 AI Trader 的 prompt 是怎么设计的

---

## 第4周：Agent 架构 + MCP 协议（核心）

### Day 1-2：Agent 基础概念
- [ ] 读 Lilian Weng 博客：https://lilianweng.github.io/posts/2023-06-23-agent/
- [ ] 理解 Agent = LLM + Tool + Memory + Loop
- [ ] 理解 ReAct 模式（Reasoning + Acting）
- [ ] 读 ReAct 论文：https://arxiv.org/abs/2210.03629

### Day 3-4：Tool Calling / Function Calling
- [ ] 读 Anthropic Tool Use 文档：https://docs.anthropic.com/en/docs/build-with-claude/tool-use
- [ ] 练习：定义 tools schema，让 Claude 自动选择调用
- [ ] 练习：处理工具调用失败的情况
- [ ] 对照 ai-trader 的执行层代码理解

### Day 5-7：MCP 协议 + Agent 循环
- [ ] 读 MCP 官方文档：https://modelcontextprotocol.io/docs
- [ ] 理解 MCP = 通用协议层（让 Agent 能调用任意外部服务）
- [ ] 理解 Plan → Act → Observe → Repeat 循环
- [ ] 理解怎么防止 Agent 死循环（max_steps / timeout / 变化检测）
- [ ] 写 3 个面试问答：Agent 循环 / MCP 解决什么问题 / 防死循环

---

## 第5周：RAG（必学）

### Day 1-2：RAG 基础
- [ ] 理解流程：文档 → chunk → embed → store → retrieve → generate
- [ ] 理解 embedding（文本→向量）和 cosine similarity
- [ ] 读 LangChain RAG 教程：https://python.langchain.com/docs/tutorials/rag/

### Day 3-4：向量数据库
- [ ] 装 Chroma，练习：存入文档 → 查询相似文档
- [ ] 对比 Milvus / FAISS / Chroma / pgvector
- [ ] 理解 HNSW 索引原理

### Day 5-7：Chunk 策略 + 检索优化
- [ ] 实验不同 chunk size（256/512/1024）对效果的影响
- [ ] 理解 hybrid search（向量 + 关键词）
- [ ] 理解 rerank 的作用
- [ ] 从零实现一个 RAG pipeline
- [ ] 写 3 个面试问答存入知识库

---

## 第6周：RAG 进阶 + 知识库项目

### Day 1-3：RAG 工程化
- [ ] chunk overlap 的作用和调优
- [ ] 多轮对话中的上下文管理
- [ ] 评估 RAG 效果（召回率 / 准确率）
- [ ] 理解 embedding 模型怎么选

### Day 4-7：知识库项目升级
- [ ] 把知识库项目升级为真正的 RAG
- [ ] 加入向量检索替代关键词搜索
- [ ] 写 3 个面试问答存入知识库
- [ ] 面试模拟：讲清 RAG 流程和 chunk 策略

---

## 第7周：Multi-Agent + LangGraph

### Day 1-2：Multi-Agent 基础
- [ ] 理解为什么需要 Multi-Agent（任务分解 + 专家分工）
- [ ] 对比主流框架：CrewAI / AutoGen / LangGraph
- [ ] 理解 Agent 间通信模式：顺序 / 并行 / 辩论 / 监督者

### Day 3-5：LangGraph 实战
- [ ] 读 LangGraph 官方教程：https://langchain-ai.github.io/langgraph/
- [ ] 动手：用 LangGraph 搭建 3-Agent 协作系统（研究员+写手+审稿人）
- [ ] 理解 StateGraph 的节点和边、条件分支、human-in-the-loop

### Day 6-7：知识图谱 + 复习
- [ ] 理解三元组（实体-关系-实体）
- [ ] 理解 GraphRAG 和传统 RAG 的区别
- [ ] 写 3 个面试问答：Multi-Agent 协作 / LangGraph 核心概念 / GraphRAG vs RAG

---

## 第8周：工程能力 + 评估

### Day 1-3：工程能力（重点）
- [ ] token streaming（SSE 实现）
- [ ] Prompt Caching（减少延迟和成本）
- [ ] 并发工具调用（asyncio.gather）
- [ ] 错误处理和降级策略（重试 + fallback + 超时）
- [ ] context 压缩（摘要 + 滑动窗口）

### Day 4-5：LLM 评估
- [ ] 理解评估方法：人工 / 自动 / BLEU / ROUGE / LLM-as-Judge
- [ ] 给 ai-trader 写评估脚本
- [ ] 理解 Fine-tuning vs RAG 的选择（什么时候该 fine-tune）
- [ ] 理解 LoRA / RLHF / DPO 概念

### Day 6-7：复习
- [ ] 写 3 个面试问答：token streaming / Fine-tuning vs RAG / 缓存策略
- [ ] 工程代码整理到 GitHub

---

## 第9周：项目整合 + 面试准备

### Day 1-2：项目整合
- [ ] ai-trader 日志自动归档到知识库
- [ ] 知识库历史经验反哺 ai-trader 决策
- [ ] 写完整的项目 README

### Day 3-4：简历 + 话术
- [ ] 更新简历
- [ ] 准备 3 分钟项目介绍
- [ ] 准备"你遇到的最大挑战是什么"
- [ ] 准备"你的 Agent 怎么防止死循环"

### Day 5-7：模拟面试
- [ ] 练习手写 Agent 循环伪代码
- [ ] 练习画系统架构图
- [ ] GitHub 代码整理完毕
- [ ] 简历定稿，开始投递

---

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

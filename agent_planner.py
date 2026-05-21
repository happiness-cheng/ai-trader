"""Agent 规划模块（ReAct 风格）
AI 自主决定调用哪些工具、以什么顺序、根据中间结果调整策略
不是固定流水线，而是"思考→行动→观察→再思考"的循环
"""
import json
import logging
import time

from agent_tools import ToolRegistry, create_default_registry, parse_tool_calls
import config

logger = logging.getLogger(__name__)

# 复用 ai_analyzer 的 Claude 调用
from ai_analyzer import _call_claude


# ========== 数据结构 ==========

class AgentStep:
    """Agent 单步记录"""
    def __init__(self, thought, tool_name=None, tool_params=None):
        self.thought = thought
        self.tool_name = tool_name
        self.tool_params = tool_params or {}
        self.result = None

    def to_dict(self):
        d = {"thought": self.thought}
        if self.tool_name:
            d["tool"] = self.tool_name
            d["params"] = self.tool_params
            d["result"] = self.result
        return d


class AgentTrace:
    """Agent 执行轨迹"""
    def __init__(self, goal):
        self.goal = goal
        self.steps = []
        self.final_answer = ""
        self.total_tool_calls = 0
        self.start_time = time.time()
        self.end_time = 0

    def summary(self):
        elapsed = self.end_time - self.start_time if self.end_time else 0
        return (f"目标: {self.goal[:50]}...\n"
                f"步骤: {len(self.steps)}, 工具调用: {self.total_tool_calls}\n"
                f"耗时: {elapsed:.1f}s\n"
                f"结论: {self.final_answer[:200]}")


# ========== Agent 规划器 ==========

AGENT_SYSTEM_PROMPT = """你是一个A股投资分析Agent。你可以使用工具获取实时数据，然后基于数据给出分析结论。

## 工作方式
1. 先理解用户的问题或任务
2. 逐步调用工具获取需要的数据
3. 综合所有数据给出结论

## 工具调用格式
要调用工具，使用以下格式（单独一行）:
TOOL_CALL: tool_name(param1="value1", param2=123)

## 注意事项
- 每次只调用必要的工具，不要一次获取所有数据
- 如果大盘环境很差，可以提前给出"不建议操作"的结论
- 分析要通俗易懂，适合零经验的大学生理解
- 调完所有需要的工具后，给出最终结论（不再包含 TOOL_CALL）
- 最终结论要包含具体的操作建议（买入/卖出/观望 + 价格 + 原因）

## 可用工具
{tools_text}"""


class AgentPlanner:
    """ReAct 风格的 Agent 规划器"""

    def __init__(self, registry=None, max_steps=10):
        self.registry = registry or create_default_registry()
        self.max_steps = max_steps

    def plan(self, goal, context=None):
        """执行 Agent 循环

        Args:
            goal: 自然语言目标，如 "分析贵州茅台是否值得买入"
            context: 可选的预加载数据

        Returns:
            AgentTrace 完整执行轨迹
        """
        trace = AgentTrace(goal=goal)

        # 构建初始消息
        tools_text = self.registry.to_prompt_text()
        system = AGENT_SYSTEM_PROMPT.format(tools_text=tools_text)

        user_msg = f"## 任务\n{goal}\n"
        if context:
            user_msg += f"\n## 已有信息\n{json.dumps(context, ensure_ascii=False, indent=2)}\n"

        messages = [{"role": "user", "content": user_msg}]

        for step_num in range(self.max_steps):
            # 构建完整 prompt
            prompt = system + "\n\n"
            for msg in messages:
                role = "用户" if msg["role"] == "user" else "助手"
                prompt += f"## {role}\n{msg['content']}\n\n"
            prompt += "## 助手\n"

            # 调用 AI
            logger.info(f"Agent 步骤 {step_num + 1}/{self.max_steps}")
            response = _call_claude(prompt, max_tokens=2000)
            if not response:
                logger.warning("AI 返回为空，结束循环")
                trace.final_answer = "AI 返回为空，请重试"
                break

            # 解析工具调用
            tool_calls = parse_tool_calls(response)

            if not tool_calls:
                # 没有工具调用 = 最终结论
                trace.final_answer = response.strip()
                logger.info(f"Agent 完成，共 {trace.total_tool_calls} 次工具调用")
                break

            # 执行工具调用
            tool_results = []
            for tc in tool_calls:
                step = AgentStep(
                    thought=f"调用 {tc['name']}",
                    tool_name=tc['name'],
                    tool_params=tc['params'],
                )

                result = self.registry.execute(tc['name'], tc['params'])
                step.result = result
                trace.steps.append(step)
                trace.total_tool_calls += 1

                # 截断过长的结果
                result_str = json.dumps(result, ensure_ascii=False)
                if len(result_str) > 1000:
                    result_str = result_str[:1000] + "...(已截断)"

                tool_results.append(f"**{tc['name']}** 返回:\n{result_str}")

            # 把工具结果喂回给 AI
            results_msg = "\n\n".join(tool_results)
            messages.append({"role": "assistant", "content": response})
            messages.append({"role": "user", "content": f"工具返回结果:\n{results_msg}\n\n请基于这些数据继续分析，或给出最终结论。"})

        else:
            # 达到最大步数
            trace.final_answer = response if 'response' in dir() else "达到最大步骤数"
            logger.warning(f"Agent 达到最大步数 {self.max_steps}")

        trace.end_time = time.time()
        return trace


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

    planner = AgentPlanner()
    trace = planner.plan(
        goal="获取当前大盘概况，判断市场环境是否适合买入",
    )

    print(f"\n{'='*60}")
    print(f"目标: {trace.goal}")
    print(f"工具调用次数: {trace.total_tool_calls}")
    print(f"耗时: {trace.end_time - trace.start_time:.1f}s")
    print(f"\n执行步骤:")
    for i, step in enumerate(trace.steps):
        print(f"  {i+1}. {step.tool_name}({step.tool_params})")
    print(f"\n最终结论:\n{trace.final_answer[:500]}")

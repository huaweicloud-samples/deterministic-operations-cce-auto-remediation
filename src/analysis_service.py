"""
CCE 分析服务 —— 只读根因探查

职责：
1. 通过 MaaS（model_adapter.converse 兼容层）驱动大模型做根因分析。
2. 通过 CceReadOnlyMCPClient 连接只读 kubectl MCP 服务采集集群状态。
3. 探查上下文围绕 CCE / kubectl / CES 构造，只读排查 Pod / Deployment / Service / Node / Event。

业务逻辑：只读探查 + 对话循环 + 工具调用。
"""

import asyncio
import json
import logging
import os
import re
from typing import Dict, Any, List

from model_adapter import converse
from cce_readonly_mcp_client import CceReadOnlyMCPClient

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 模型 ID：支持传入 Bedrock 格式模型 ID，经 model_adapter 自动映射到 MaaS；默认 deepseek-v4-flash
MODEL_ID = os.environ.get('MODEL_ID', 'us.anthropic.claude-3-7-sonnet-20250219-v1:0')


def convert_mcp_tools_to_tool_spec(tools):
    """将 MCP 工具转为 toolSpec 风格工具定义（与 model_adapter 输入格式一致）"""
    tool_spec_tools = []

    for tool in tools:
        bedrock_tool = {
            "toolSpec": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "inputSchema": {
                    "json": tool.get("inputSchema", {})
                }
            }
        }
        tool_spec_tools.append(bedrock_tool)

    return tool_spec_tools


class AnalysisService:
    def __init__(self):
        self.mcp_client = None

    async def analyze_incident(self, incident_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        使用只读 MCP 工具分析事件，保持对话循环逻辑一致。
        """
        incident_id = incident_data.get('incident_id', 'unknown')
        cluster_name = incident_data.get('cluster_name', 'unknown')

        try:
            logger.info(f"🔍 开始对事件 {incident_id} 进行只读分析")
            logger.info(f"📊 集群: {cluster_name}")

            # 以只读模式初始化 CCE MCP 客户端（非致命：连接失败不阻断分析，仅缺少集群实时数据）
            self.mcp_client = None
            tools = []
            try:
                self.mcp_client = CceReadOnlyMCPClient()
                await self.mcp_client.connect()
                logger.info("🔒 只读 MCP 客户端已连接")
                tools = await self.mcp_client.list_tools()
                logger.info(f"🛠️  可用只读工具数: {len(tools)}")
                for i, tool in enumerate(tools[:5]):
                    logger.info(f"   - {tool.get('name', 'unknown')}")
                if len(tools) > 5:
                    logger.info(f"   ... 以及另外 {len(tools) - 5} 个工具")
            except Exception as e:
                logger.warning(f"⚠️ 只读 MCP 客户端连接失败（分析将不依赖集群实时数据）: {e}")
                self.mcp_client = None
                tools = []

            tool_spec_tools = convert_mcp_tools_to_tool_spec(tools) if tools else []
            logger.info(f"🔄 已转换为 {len(tool_spec_tools)} 个 toolSpec 工具")

            # 构造分析上下文
            context = self._prepare_analysis_context(incident_data)
            logger.info(f"📝 分析上下文已准备，长度 {len(context)} 字符")

            # 启动带对话循环的分析
            response = await self._analyze_with_model(context, tool_spec_tools, incident_data)

            logger.info("✅ 只读分析完成")
            logger.info(f"📋 最终分析摘要:")
            logger.info(f"   风险等级: {response.get('risk_level', 'unknown')}")
            logger.info(f"   摘要: {response.get('summary', '无可用摘要')}")
            logger.info(f"   根因: {response.get('root_cause', '未识别根因')}")
            logger.info(f"   建议动作数: {len(response.get('recommended_actions', []))}")

            if 'analysis' in response:
                logger.info(f"📄 完整分析文本:")
                logger.info(f"{response['analysis']}")

                analysis_text = response['analysis']
                sections = ['ISSUE EXPLANATION', 'IMMEDIATE ACTION', 'PREVENTIVE ACTION', 'ALTERNATIVE ACTION']

                for section in sections:
                    if f"## {section}" in analysis_text:
                        start_idx = analysis_text.find(f"## {section}")
                        if start_idx != -1:
                            next_section_idx = len(analysis_text)
                            for other_section in sections:
                                if other_section != section:
                                    other_idx = analysis_text.find(f"## {other_section}", start_idx + 1)
                                    if other_idx != -1 and other_idx < next_section_idx:
                                        next_section_idx = other_idx

                            section_content = analysis_text[start_idx:next_section_idx].strip()
                            logger.info(f"📋 {section}:")
                            logger.info(f"{section_content}")
                            logger.info("---")

            return response

        except Exception as e:
            logger.error(f"❌ 分析失败: {str(e)}")
            raise
        finally:
            if self.mcp_client:
                await self.mcp_client.disconnect()
                logger.info("🔌 只读 MCP 客户端已断开")

    def _prepare_analysis_context(self, incident_data: Dict[str, Any]) -> str:
        """从事件数据构造分析上下文"""
        cluster_name = incident_data.get('cluster_name', 'unknown')
        incident_type = incident_data.get('incident_type', 'unknown')
        alarm_name = incident_data.get('alarm_name', 'unknown')

        context = f"""
你是一名 Kubernetes 排障专家，正在以只读模式分析一个 CCE 集群事件。

事件详情：
- 集群: {cluster_name}
- 类型: {incident_type}
- 告警: {alarm_name}
- 事件 ID: {incident_data.get('incident_id', 'unknown')}
- 时间戳: {incident_data.get('timestamp', 'unknown')}

事件数据：
{json.dumps(incident_data, indent=2)}

分析指引：
1. 使用可用工具彻底排查集群状态。
2. 你处于只读模式，只能使用 list、get、describe 类操作。
3. 排查 Pod、Deployment、Service、Node 与 Event。
4. 定位问题根因。
5. 给出可立即执行的 kubectl 修复命令（使用具体值，不要占位符）。
6. 评估建议动作的风险等级（high/medium/low）。
7. 请充分使用多个工具收集全面信息。

重要：
- 你处于只读模式，不要尝试任何写操作。
- 使用工具排查，不要仅凭告警数据臆测。
- 给出具体的 kubectl 命令，不要占位符。
- 每个排查步骤后说明你的发现。

请先排查集群当前状态并定位根因。
"""
        return context

    async def _analyze_with_model(self, context: str, tools: List[Dict], incident_data: Dict) -> Dict[str, Any]:
        """带对话循环的分析（复用统一对话循环实现）"""
        messages = [{"role": "user", "content": [{"text": context}]}]
        actions_taken = []
        max_iterations = 15  # 仅排查阶段
        all_processed_tool_ids = set()
        consecutive_failures = 0
        abort_tools = False

        for iteration in range(max_iterations):
            logger.info(f"🔄 排查迭代 {iteration + 1}/{max_iterations}")

            try:
                # 排查阶段系统指令
                system_instructions = (
                    "你正在排查一个 CCE 集群事件。请使用 MCP 工具全面收集信息。"
                    "你最多可调用 20 次工具，请明智使用以彻底排查。"
                    "在调用满 20 次或信息充分后，以下列格式给出最终分析：\n\n"
                    "## ISSUE EXPLANATION\n"
                    "基于排查发现说明问题是什么。\n\n"
                    "## IMMEDIATE ACTION\n"
                    "给出恰好一条可立即缓解当前事件的 kubectl 命令。\n"
                    "格式: [KUBECTL] kubectl delete pod actual-pod-name -n actual-namespace\n\n"
                    "## PREVENTIVE ACTION\n"
                    "给出恰好一条可防止该事件复发的 kubectl 命令。\n"
                    "格式: [KUBECTL] kubectl apply -f resource-limits.yaml\n\n"
                    "## ALTERNATIVE ACTION\n"
                    "给出恰好一条备选 kubectl 命令（当前两条都不生效时使用）。\n"
                    "格式: [KUBECTL] kubectl rollout restart deployment actual-name -n actual-namespace\n\n"
                    "使用排查结果中的具体值，不要占位符。"
                    "不要修改 CES 告警本身，聚焦 CCE 集群与 Kubernetes 资源。"
                    "仅使用读操作：list、get、describe。排查阶段绝不执行写操作。"
                )

                max_retries = 5
                logger.info(f"🤖 调用模型 {MODEL_ID}，消息数 {len(messages)}，工具数 {len(tools)}")

                for attempt in range(max_retries):
                    try:
                        logger.info(f"📡 模型调用 attempt {attempt + 1}/{max_retries}")
                        response = converse(
                            model_id=MODEL_ID,
                            system=[{"text": system_instructions}],
                            messages=messages,
                            tool_config={"tools": tools},
                            inference_config={"maxTokens": 2048, "temperature": 0.3}
                        )
                        logger.info("✅ 模型调用成功")
                        break
                    except Exception as e:
                        if attempt < max_retries - 1:
                            wait_time = (2 ** attempt) * 3
                            logger.warning(f"⏳ 模型调用异常，{wait_time} 秒后重试（attempt {attempt + 1}/{max_retries}）: {e}")
                            await asyncio.sleep(wait_time)
                        else:
                            logger.error(f"❌ 模型调用失败: {e}")
                            raise

                assistant_message = response["output"]["message"]
                messages.append(assistant_message)

                # 记录模型回复并提取命令
                content = assistant_message.get("content", [])
                for item in content:
                    if item.get("type") == "text":
                        text = item.get("text", "")
                        logger.info(f"💬 模型分析: {text[:300]}{'...' if len(text) > 300 else ''}")

                        commands = re.findall(r'(?:KUBECTL_COMMAND|HW_CLI_COMMAND):\s*(.+)', text)
                        for cmd in commands:
                            logger.info(f"📋 推荐命令: {cmd.strip()}")

                stop_reason = response["stopReason"]
                logger.info(f"🛑 模型停止原因: {stop_reason}")

                if stop_reason == "end_turn":
                    content = assistant_message.get("content", [])
                    analysis = content[0].get("text", "") if content else ""
                    logger.info(f"✅ 分析完成，长度 {len(analysis)} 字符")

                    result = self._extract_analysis_results(analysis, actions_taken)
                    return result

                elif stop_reason == "tool_use":
                    logger.info(f"🔧 模型希望使用只读工具")
                    tool_results = []

                    for content_item in assistant_message.get("content", []):
                        if "toolUse" in content_item:
                            tool_use = content_item["toolUse"]
                            tool_name = tool_use.get("name")
                            tool_input = tool_use.get("input", {})
                            tool_use_id = tool_use.get("toolUseId")

                            if len(actions_taken) >= 20:
                                logger.warning(f"⚠️  工具调用达到上限（已用 {len(actions_taken)} 次）")
                                tool_results.append({
                                    "toolResult": {
                                        "toolUseId": tool_use_id,
                                        "content": [{
                                            "text": "工具调用已达上限（20 次）。请按要求的格式给出最终分析：ISSUE EXPLANATION、IMMEDIATE ACTION、PREVENTIVE ACTION、ALTERNATIVE ACTION，每节各一条命令。"
                                        }],
                                        "status": "error"
                                    }
                                })
                                continue
                            if tool_use_id in all_processed_tool_ids:
                                logger.warning(f"⚠️  跳过重复 toolUseId: {tool_use_id}")
                                continue
                            all_processed_tool_ids.add(tool_use_id)

                            if "cluster_name" not in tool_input:
                                tool_input["cluster_name"] = incident_data.get("cluster_name")
                            logger.info(f"📥 工具入参: {json.dumps(tool_input, indent=2)}")

                            if self.mcp_client is None:
                                logger.warning(f"⚠️ MCP 客户端不可用，跳过工具 {tool_name}")
                                tool_results.append({
                                    "toolResult": {
                                        "toolUseId": tool_use_id,
                                        "content": [{"text": "只读 MCP 客户端不可用，无法执行集群探查。"}],
                                        "status": "error",
                                    }
                                })
                                continue

                            try:
                                # 25s 硬超时：集群不可达时快速失败，避免 90s×N 撑爆函数总时限
                                result = await asyncio.wait_for(
                                    self.mcp_client.call_tool(tool_name, tool_input),
                                    timeout=25,
                                )
                                consecutive_failures = 0
                                logger.info(f"✅ 工具 {tool_name} 执行成功")
                                logger.info(f"📤 工具结果预览: {str(result)[:1000]}{'...' if len(str(result)) > 1000 else ''}")

                                actions_taken.append({
                                    "tool": tool_name,
                                    "input": tool_input,
                                    "success": True,
                                    "result_preview": str(result)[:100]
                                })

                                tool_results.append({
                                    "toolResult": {
                                        "toolUseId": tool_use_id,
                                        "content": [{"text": json.dumps(result)}],
                                        "status": "success"
                                    }
                                })

                            except asyncio.TimeoutError:
                                logger.error(f"❌ 工具 {tool_name} 执行超时（25s，集群 API 不可达）")
                                consecutive_failures += 1
                                actions_taken.append({
                                    "tool": tool_name,
                                    "input": tool_input,
                                    "success": False,
                                    "error": "tool call timeout (cluster API unreachable)"
                                })
                                err_text = ("Error: 工具执行超时（25 秒），集群 API 网络不可达。")
                                if consecutive_failures >= 5:
                                    err_text += (" 已连续 5 次失败。请立即停止尝试工具调用，"
                                                 "基于已知告警信息直接给出最终分析，并输出 [KUBECTL] 修复命令建议。")
                                    abort_tools = True
                                    tool_results.append({
                                        "toolResult": {
                                            "toolUseId": tool_use_id,
                                            "content": [{"text": err_text}],
                                            "status": "error"
                                        }
                                    })
                                    break
                                tool_results.append({
                                    "toolResult": {
                                        "toolUseId": tool_use_id,
                                        "content": [{"text": err_text}],
                                        "status": "error"
                                    }
                                })

                            except Exception as e:
                                logger.error(f"❌ 工具 {tool_name} 失败: {str(e)}")
                                consecutive_failures += 1
                                actions_taken.append({
                                    "tool": tool_name,
                                    "input": tool_input,
                                    "success": False,
                                    "error": str(e)
                                })

                                if consecutive_failures >= 5:
                                    # 集群连续不可达：终止工具循环，让模型基于告警数据直接产出降级分析
                                    abort_tools = True
                                    tool_results.append({
                                        "toolResult": {
                                            "toolUseId": tool_use_id,
                                            "content": [{"text": "Error: 已连续 5 次工具调用失败（集群 API 不可达）。"
                                                                "请立即停止尝试工具调用，基于当前已知告警信息直接给出最终分析，"
                                                                "并输出 [KUBECTL] 修复命令建议。"}],
                                            "status": "error"
                                        }
                                    })
                                    break

                                tool_results.append({
                                    "toolResult": {
                                        "toolUseId": tool_use_id,
                                        "content": [{"text": f"Error: {str(e)}"}],
                                        "status": "error"
                                    }
                                })

                    if tool_results:
                        messages.append({"role": "user", "content": tool_results})
                        logger.info(f"📨 已向对话追加 {len(tool_results)} 条工具结果")

                    if abort_tools:
                        logger.warning("⛔ 集群连续不可达，终止排查迭代，切换降级终局分析（无工具）")
                        break

                else:
                    logger.warning(f"⚠️  未预期的停止原因: {stop_reason}")
                    break

            except Exception as e:
                logger.error(f"❌ 迭代 {iteration + 1} 失败: {str(e)}")
                if iteration == max_iterations - 1:
                    raise
                continue

            if iteration < max_iterations - 1:
                logger.info(f"⏸️  等待 8 秒进入下一轮迭代...")
                await asyncio.sleep(8)

        logger.info(f"✅ 排查阶段完成，共 {max_iterations} 轮迭代")

        # 降级终局：集群不可达时用无工具模式强制产出最终分析
        if abort_tools:
            logger.info("🛟 降级终局分析：无工具模式")
            messages.append({"role": "user", "content": [{"text": (
                "集群 API 网络不可达，无法继续探查。请基于以上全部已知告警信息，立即按以下格式给出最终分析，"
                "禁止再调用任何工具：\n\n"
                "## ISSUE EXPLANATION\n说明问题的可能原因。\n\n"
                "## IMMEDIATE ACTION\n格式: [KUBECTL] <一条针对 PodCrashLoopBackOff 的标准缓解命令>\n\n"
                "## PREVENTIVE ACTION\n格式: [KUBECTL] <一条预防命令>\n\n"
                "## ALTERNATIVE ACTION\n格式: [KUBECTL] <一条备选命令>\n\n"
                "不要使用占位符；无法确定具体资源名时使用可独立执行的查询/删除组合命令。"
            )}]})
            degrade_system = (
                "你是 Kubernetes 根因分析专家。当前集群 API 不可达，只能基于告警信息推理。"
                "严格按用户要求的四个小节格式输出，每个 ACTION 小节恰好一条 [KUBECTL] 命令。"
            )
            response = converse(
                model_id=MODEL_ID,
                system=[{"text": degrade_system}],
                messages=messages,
                inference_config={"maxTokens": 2048, "temperature": 0.3}
            )
            assistant_message = response["output"]["message"]
            messages.append(assistant_message)
            content = assistant_message.get("content", [])
            analysis = content[0].get("text", "") if content else ""
            logger.info(f"🛟 降级终局分析完成，长度 {len(analysis)} 字符")
            return self._extract_analysis_results(analysis, actions_taken)

        final_analysis = "排查完成 - 详见工具结果"
        try:
            if messages and len(messages) > 1:
                for msg in reversed(messages):
                    if msg.get("role") == "assistant" and "content" in msg:
                        content = msg["content"]
                        if content and isinstance(content, list) and len(content) > 0:
                            text_content = content[0].get("text", "")
                            if text_content and len(text_content) > 50:
                                final_analysis = text_content
                                break
        except Exception as e:
            logger.error(f"❌ 提取最终分析出错: {e}")
            final_analysis = "排查完成 - 分析提取失败"

        return self._extract_analysis_results(final_analysis, actions_taken)

    def _extract_analysis_results(self, analysis: str, actions_taken: List[Dict]) -> Dict[str, Any]:
        """从分析文本中提取结构化结果"""
        # 仅保留 [KUBECTL] 标签（kubectl 为 Kubernetes 中性命令，非特定云平台术语）
        kubectl_commands = [c.strip().strip('`').strip() for c in re.findall(r'\[KUBECTL\]\s*(.+)', analysis)]
        recommended_actions = kubectl_commands

        risk_level = "medium"
        if any(word in analysis.lower() for word in ["high risk", "critical", "urgent", "delete", "terminate"]):
            risk_level = "high"
        elif any(word in analysis.lower() for word in ["low risk", "safe", "minor", "restart"]):
            risk_level = "low"

        root_cause = "排查完成 - 详见分析"
        if "root cause" in analysis.lower():
            lines = analysis.split('\n')
            for line in lines:
                if "root cause" in line.lower():
                    root_cause = line.strip()
                    break

        result = {
            "summary": analysis,
            "root_cause": root_cause,
            "recommended_actions": recommended_actions,
            "risk_level": risk_level,
            "tool_results": actions_taken,
            "investigation_complete": True
        }

        logger.info(f"📊 分析结果: {len(recommended_actions)} 条动作，风险: {risk_level}")
        return result


# 全局实例
analysis_service = AnalysisService()


async def analyze_incident(incident_data: Dict[str, Any]) -> Dict[str, Any]:
    """事件分析主入口"""
    return await analysis_service.analyze_incident(incident_data)

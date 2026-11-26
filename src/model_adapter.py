"""
模型抽象层：将华为云 MaaS（OpenAI 兼容端点）封装为对话循环 + 工具调用接口。

设计目标：让业务逻辑（analysis_service.py）保持「对话循环 + 工具调用」写法，
底层模型走 MaaS。本文件是兼容层，模型映射键（如 Bedrock 格式模型 ID）在此处转换。

MaaS 通过 OpenAI 兼容接口暴露，支持 tool calling；默认模型 deepseek-v4-flash。
"""

import json
import logging
import os
import time

logger = logging.getLogger(__name__)

# 模型兼容映射。键为 Bedrock 格式模型 ID，值为 MaaS 部署模型名。
# 仅用于「调用方传入旧模型 ID 时自动落到 MaaS 模型」，不改变产物对外语义。
MODEL_MAP = {
    "us.anthropic.claude-3-7-sonnet-20250219-v1:0": "deepseek-v4-flash",
    "us.anthropic.claude-sonnet-4-20250514-v1:0": "deepseek-v4-flash",
    "anthropic.claude-3-sonnet-20240229-v1:0": "deepseek-v4-flash",
}

DEFAULT_MAAS_MODEL = os.environ.get("MAAS_MODEL_ID", "deepseek-v4-flash")

# MaaS 默认模型 deepseek-v4-flash；如需切换可在环境变量指定。
_MAAS_BASE_URL = os.environ.get("MAAS_BASE_URL", "")
_MAAS_API_KEY = os.environ.get("MAAS_API_KEY", os.environ.get("HW_SECURITY_TOKEN", ""))


def _resolve_model(model_id: str) -> str:
    if not model_id:
        return DEFAULT_MAAS_MODEL
    return MODEL_MAP.get(model_id, model_id)


def _client():
    from openai import OpenAI
    if not _MAAS_BASE_URL:
        raise RuntimeError(
            "未配置 MAAS_BASE_URL。请在函数环境变量中设置 MaaS 兼容端点（含 /v1 路径）。"
        )
    return OpenAI(api_key=_MAAS_API_KEY or "placeholder", base_url=_MAAS_BASE_URL)


def _system_to_openai(system_blocks):
    if not system_blocks:
        return None
    if isinstance(system_blocks, str):
        return system_blocks
    if isinstance(system_blocks, list):
        # 兼容 {"type":"text","text":...} 与 Bedrock 原生 {"text":...}（无 type 键）
        texts = [b.get("text", "") for b in system_blocks
                 if b.get("type") == "text" or (b.get("type") is None and "text" in b)]
        return "\n".join(texts)
    return None


def _content_to_openai(content):
    """将 Bedrock content 块列表转为 OpenAI 消息 content / tool_calls。"""
    text_parts = []
    tool_calls = []
    for block in content:
        # 兼容两种块格式：Bedrock Converse 原生 {"text": ...}（无 type 键）
        # 与内部约定 {"type": "text", "text": ...}
        btype = block.get("type") or ("text" if "text" in block else None)
        if btype == "text":
            text_parts.append(block.get("text", ""))
        elif btype == "toolUse":
            tu = block["toolUse"]
            tool_calls.append({
                "id": tu.get("toolUseId"),
                "type": "function",
                "function": {
                    "name": tu.get("name"),
                    "arguments": json.dumps(tu.get("input", {}), ensure_ascii=False),
                },
            })
    return ("\n".join(text_parts) if text_parts else None), (tool_calls or None)


def _messages_to_openai(messages):
    """将 Bedrock 风格 messages 转为 OpenAI messages。

    Bedrock 一段 user 消息里可能含多个 toolResult 块；OpenAI 需拆成多条 role=tool 消息。
    Bedrock 一段 assistant 消息里可能含 toolUse 块；OpenAI 需带 tool_calls。
    """
    out = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", [])
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            text, tool_calls = _content_to_openai(content)
            m = {"role": "assistant"}
            if text:
                m["content"] = text
            if tool_calls:
                m["tool_calls"] = tool_calls
            out.append(m)
        else:
            # user / tool 处理结果：拆出 toolResult -> role=tool
            text, _ = _content_to_openai(content)
            for block in content:
                if block.get("type") == "toolResult":
                    tr = block["toolResult"]
                    out.append({
                        "role": "tool",
                        "tool_call_id": tr.get("toolUseId"),
                        "content": _tool_result_text(tr),
                    })
            if text:
                out.append({"role": "user", "content": text})
    return out


def _tool_result_text(tool_result):
    parts = tool_result.get("content", [])
    if isinstance(parts, str):
        return parts
    texts = []
    for p in parts:
        if isinstance(p, dict) and "text" in p:
            texts.append(p["text"])
        else:
            texts.append(str(p))
    return "\n".join(texts)


def _tools_to_openai(tools):
    """Bedrock toolSpec -> OpenAI function 工具定义。"""
    out = []
    for t in tools:
        spec = t.get("toolSpec", {})
        out.append({
            "type": "function",
            "function": {
                "name": spec.get("name"),
                "description": spec.get("description", ""),
                "parameters": spec.get("inputSchema", {}).get("json", {"type": "object", "properties": {}}),
            },
        })
    return out


def _openai_to_bedrock_response(resp):
    """OpenAI chat completion -> Bedrock Converse 风格响应。"""
    choice = resp.choices[0]
    msg = choice.message
    content = []
    if getattr(msg, "content", None):
        content.append({"type": "text", "text": msg.content})
    tool_calls = getattr(msg, "tool_calls", None)
    if tool_calls:
        for tc in tool_calls:
            content.append({
                "type": "toolUse",
                "toolUse": {
                    "toolUseId": tc.id,
                    "name": tc.function.name,
                    "input": _safe_json(tc.function.arguments),
                },
            })
        stop_reason = "tool_use"
    else:
        stop_reason = "end_turn"
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": content,
            }
        },
        "stopReason": stop_reason,
    }


def _safe_json(s):
    try:
        return json.loads(s) if s else {}
    except Exception:
        return {}


def converse(model_id, system, messages, tool_config=None, inference_config=None):
    """
    与 Bedrock Converse 一致的方法签名。

    :param model_id: 模型 ID（支持传入 Bedrock 格式模型 ID，自动映射；或 MaaS 模型名）
    :param system: Bedrock 风格 system 块列表 或字符串
    :param messages: Bedrock 风格对话消息列表
    :param tool_config: {"tools": [...]} Bedrock 工具定义
    :param inference_config: {"maxTokens": int, "temperature": float}
    :return: Bedrock Converse 风格响应
    """
    model = _resolve_model(model_id)
    client = _client()
    system_text = _system_to_openai(system)
    oa_messages = _messages_to_openai(messages)
    oa_tools = _tools_to_openai(tool_config.get("tools", [])) if tool_config else None

    max_tokens = (inference_config or {}).get("maxTokens", 2048)
    temperature = (inference_config or {}).get("temperature", 0.3)

    kwargs = {
        "model": model,
        "messages": oa_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if oa_tools:
        kwargs["tools"] = oa_tools
        kwargs["tool_choice"] = "auto"

    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:  # 重试（限流）
        logger.warning("MaaS 调用失败，3 秒后重试: %s", e)
        time.sleep(3)
        resp = client.chat.completions.create(**kwargs)
    return _openai_to_bedrock_response(resp)

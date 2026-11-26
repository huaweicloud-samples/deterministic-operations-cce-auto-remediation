#!/usr/bin/env python3
"""
只读 kubectl MCP 服务（stdio JSON-RPC）。

以子进程方式拉起，通过标准输入输出与调用方通信，暴露一组「只读」kubectl 工具供大模型
做集群根因探查。本服务对传入的 kubeconfig（CCE 或其他标准 Kubernetes 集群）执行
kubectl get/list/describe/logs 等只读命令，适用于任意标准 Kubernetes 集群。

仅暴露只读动词，绝不执行 create/apply/edit/delete 等写操作 —— 安全边界由本服务强制。
"""

import json
import os
import subprocess
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [mcp-server] %(levelname)s %(message)s")
logger = logging.getLogger("cce-mcp-server")

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "cce-readonly-mcp-server", "version": "1.0.0"}

# 强制只读动词白名单
ALLOWED_VERBS = {"get", "describe", "logs", "top", "api-resources", "api-versions", "version"}


def _kubeconfig_args():
    kc = os.environ.get("KUBECONFIG") or os.environ.get("CCE_KUBECONFIG", "/tmp/kubeconfig")
    context = os.environ.get("KUBE_CONTEXT")
    args = ["--kubeconfig", kc]
    if context:
        args += ["--context", context]
    return args


def _read_only_tool(name, cmd_args, timeout=60):
    """执行一条只读 kubectl 命令，返回结构化结果。"""
    if cmd_args and cmd_args[0] not in ALLOWED_VERBS and cmd_args[0] != "kubectl":
        # 防御：第一个实参必须是只读动词
        if len(cmd_args) >= 2 and cmd_args[1] not in ALLOWED_VERBS:
            return {"error": f"拒绝非只读命令: {cmd_args}"}
    try:
        full = ["kubectl"] + _kubeconfig_args() + cmd_args
        logger.info("执行只读命令: %s", " ".join(full))
        proc = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
        if proc.returncode != 0:
            return {"exit_code": proc.returncode, "stderr": proc.stderr.strip()}
        return {"exit_code": 0, "stdout": proc.stdout.strip()}
    except subprocess.TimeoutExpired:
        return {"error": "命令执行超时"}
    except Exception as e:  # pragma: no cover
        return {"error": str(e)}


# ---- 工具定义 ----
TOOLS = [
    {
        "name": "list_pods",
        "description": "列出指定命名空间下的 Pod（不含 -A 时默认 default）。用于发现异常 Pod。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "命名空间，空表示全部命名空间"},
                "label_selector": {"type": "string", "description": "可选 label 选择器，如 app=nginx"},
            },
        },
    },
    {
        "name": "get_pod",
        "description": "获取单个 Pod 详情（describe），含事件与状态，定位 Pending/CrashLoopBackOff 根因。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Pod 名称"},
                "namespace": {"type": "string", "description": "命名空间"},
            },
            "required": ["name", "namespace"],
        },
    },
    {
        "name": "list_deployments",
        "description": "列出 Deployment，检查副本数与可用副本。",
        "inputSchema": {
            "type": "object",
            "properties": {"namespace": {"type": "string", "description": "命名空间，空表示全部"}},
        },
    },
    {
        "name": "get_deployment",
        "description": "获取单个 Deployment 详情，含滚动更新状态与历史事件。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Deployment 名称"},
                "namespace": {"type": "string", "description": "命名空间"},
            },
            "required": ["name", "namespace"],
        },
    },
    {
        "name": "list_nodes",
        "description": "列出集群节点及其状态、角色、条件（Ready/Pressure）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "get_node",
        "description": "获取单个节点 describe，含资源压力、驱逐条件、kubelet 事件。",
        "inputSchema": {
            "type": "object",
            "properties": {"name": {"type": "string", "description": "节点名称"}},
            "required": ["name"],
        },
    },
    {
        "name": "list_events",
        "description": "列出指定命名空间的最近事件（按时间），发现 Warning 与失败调度。",
        "inputSchema": {
            "type": "object",
            "properties": {"namespace": {"type": "string", "description": "命名空间，空表示全部"}},
        },
    },
    {
        "name": "get_logs",
        "description": "获取 Pod 容器日志（最近 200 行），定位应用层错误。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Pod 名称"},
                "namespace": {"type": "string", "description": "命名空间"},
                "container": {"type": "string", "description": "容器名，可选"},
                "previous": {"type": "boolean", "description": "是否取上一次崩溃日志"},
            },
            "required": ["name", "namespace"],
        },
    },
    {
        "name": "list_services",
        "description": "列出 Service，检查端点就绪与类型。",
        "inputSchema": {
            "type": "object",
            "properties": {"namespace": {"type": "string", "description": "命名空间，空表示全部"}},
        },
    },
    {
        "name": "describe_resource",
        "description": "对任意资源类型执行 describe（只读），如 describe pvc / hpa / statefulset。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "description": "资源类型，如 pvc / hpa / statefulset"},
                "name": {"type": "string", "description": "资源名称"},
                "namespace": {"type": "string", "description": "命名空间"},
            },
            "required": ["kind", "name"],
        },
    },
]


def _dispatch(tool_name, args):
    ns = args.get("namespace")
    ns_flag = ["-n", ns] if ns else []
    if tool_name == "list_pods":
        cmd = ["get", "pods"] + ns_flag
        if args.get("label_selector"):
            cmd += ["-l", args["label_selector"]]
        return _read_only_tool(tool_name, cmd + ["-o", "wide"])
    if tool_name == "get_pod":
        return _read_only_tool(tool_name, ["describe", "pod", args["name"]] + ns_flag)
    if tool_name == "list_deployments":
        return _read_only_tool(tool_name, ["get", "deployments"] + ns_flag + ["-o", "wide"])
    if tool_name == "get_deployment":
        return _read_only_tool(tool_name, ["describe", "deployment", args["name"]] + ns_flag)
    if tool_name == "list_nodes":
        return _read_only_tool(tool_name, ["get", "nodes", "-o", "wide"])
    if tool_name == "get_node":
        return _read_only_tool(tool_name, ["describe", "node", args["name"]])
    if tool_name == "list_events":
        cmd = ["get", "events"] + ns_flag
        if not ns:
            cmd += ["-A"]
        return _read_only_tool(tool_name, cmd + ["--sort-by=.lastTimestamp"])
    if tool_name == "get_logs":
        cmd = ["logs", args["name"]] + ns_flag
        if args.get("container"):
            cmd += ["-c", args["container"]]
        if args.get("previous"):
            cmd += ["--previous"]
        return _read_only_tool(tool_name, cmd + ["--tail", "200"])
    if tool_name == "list_services":
        return _read_only_tool(tool_name, ["get", "svc"] + ns_flag + ["-o", "wide"])
    if tool_name == "describe_resource":
        return _read_only_tool(tool_name, ["describe", args["kind"], args["name"]] + ns_flag)
    return {"error": f"未知工具: {tool_name}"}


def _handle(message):
    method = message.get("method")
    mid = message.get("id")
    params = message.get("params", {})
    if method == "initialize":
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "serverInfo": SERVER_INFO},
        }
    if method == "notifications/initialized":
        return None
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments", {})
        result = _dispatch(name, arguments)
        is_error = "error" in result
        if is_error:
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
        else:
            text = result.get("stdout")
            if text is None:
                text = json.dumps({k: v for k, v in result.items() if k != "stdout"}, ensure_ascii=False)
            content = [{"type": "text", "text": text}]
        return {
            "jsonrpc": "2.0", "id": mid,
            "result": {"content": content, "isError": is_error},
        }
    # 未知方法
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def main():
    logger.info("只读 kubectl MCP 服务启动（stdio）")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("method") == "notifications/initialized":
            continue
        response = _handle(message)
        if response is None:
            continue
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

"""
只读 MCP 客户端：在分析函数内拉起 cce_mcp_server 子进程并与之通信。

以子进程方式启动只读 kubectl MCP 服务，通过 JSON-RPC stdio 协议列出工具、调用工具，
供大模型做集群根因探查。
"""

import asyncio
import json
import logging
import os
import sys
from typing import Dict, List, Any, Optional

logger = logging.getLogger(__name__)


class CceReadOnlyMCPClient:
    def __init__(self, kubeconfig_path: str = "/tmp/kubeconfig"):
        self.process = None
        self.reader = None
        self.writer = None
        self.request_id = 0
        self.kubeconfig_path = kubeconfig_path

    async def connect(self):
        env = os.environ.copy()
        env.update({
            "PYTHONUNBUFFERED": "1",
            "KUBECONFIG": self.kubeconfig_path,
            "UV_CACHE_DIR": "/tmp/.uv-cache",
            "UV_TOOL_DIR": "/tmp/.uv-tools",
            "XDG_CACHE_HOME": "/tmp/.cache",
        })
        # 子进程以 `python -m cce_mcp_server` 启动，需把本模块所在目录加入 PYTHONPATH，
        # 否则 FunctionGraph 实际工作目录非 /app/src 时会报 No module named cce_mcp_server
        _module_dir = os.path.dirname(os.path.abspath(__file__))
        env["PYTHONPATH"] = os.pathsep.join([_module_dir, env.get("PYTHONPATH", "")])

        # 优先使用环境变量指定的服务路径，否则回退到同目录模块
        server_path = os.environ.get("MCP_SERVER_PATH")
        if server_path and os.path.exists(server_path):
            cmd = [sys.executable, server_path]
        else:
            cmd = [sys.executable, "-m", "cce_mcp_server"]

        logger.info("启动只读 MCP 服务: %s", " ".join(cmd))
        self.process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        await asyncio.sleep(1)
        if self.process.returncode is not None:
            err = await self.process.stderr.read()
            raise Exception(f"MCP 服务启动即退出: {err.decode()}")

        self.reader = self.process.stdout
        self.writer = self.process.stdin

        resp = await self._send_message({
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "analysis-service", "version": "1.0.0"},
            },
        }, timeout=30.0)
        if resp.get("result"):
            await self._send_message({"jsonrpc": "2.0", "method": "notifications/initialized"})
            logger.info("只读 MCP 客户端连接成功")
            return
        raise Exception(f"MCP 初始化失败: {resp}")

    async def disconnect(self):
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
        if self.process:
            try:
                self.process.terminate()
                await self.process.wait()
            except Exception:
                pass

    def _next_id(self) -> int:
        self.request_id += 1
        return self.request_id

    async def _send_message(self, message: Dict, timeout: float = 90.0) -> Dict:
        if not self.process or self.process.returncode is not None:
            await self.connect()
        self.writer.write((json.dumps(message) + "\n").encode())
        await self.writer.drain()
        if "id" not in message:
            return {"status": "notification_sent"}

        data = b""
        while True:
            try:
                chunk = await asyncio.wait_for(self.reader.read(8192), timeout=timeout)
            except asyncio.TimeoutError:
                raise Exception("等待 MCP 服务响应超时")
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                for line in data.split(b"\n")[:-1]:
                    if not line.strip():
                        continue
                    try:
                        resp = json.loads(line.decode().strip())
                    except json.JSONDecodeError:
                        continue
                    if resp.get("id") == message.get("id"):
                        return resp
                data = data.split(b"\n")[-1]
        raise Exception("MCP 服务未返回有效响应")

    async def list_tools(self) -> List[Dict[str, Any]]:
        resp = await self._send_message({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/list",
        })
        if "result" in resp and "tools" in resp["result"]:
            return resp["result"]["tools"]
        return []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        resp = await self._send_message({
            "jsonrpc": "2.0", "id": self._next_id(), "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        if "result" in resp:
            return resp["result"]
        return {"content": [{"type": "text", "text": json.dumps(resp.get("error", {}), ensure_ascii=False)}], "isError": True}

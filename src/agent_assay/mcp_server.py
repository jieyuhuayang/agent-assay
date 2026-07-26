"""MCP server（FP10）——registry 的第二消费通道（D2/R7）。

specs/10 契约：
- 用低层 Server API（不用 FastMCP），工具列表纯反射自 registry，不存在第二份 schema；
- call_tool 永不 raise（沿用 registry「结构化错误」契约）；InvariantViolation 例外：
  mcp SDK 会把 handler 的一切 Exception 吞成 isError 响应继续服务，账本损坏不能靠
  上抛——handler 自行写 stderr 后终止进程（specs/10 §3，M3 审查修复）；
- mandate 经 server ``instructions`` 注入（D-g，D3 形态①的 MCP 对应物）；
- MCP 模式无模拟用户（D-f）：ask_user 返回固定提示语，request_confirmation 默认
  denied（fail-safe），``--auto-approve`` 翻转为恒 approved。
"""

from __future__ import annotations

import json
import os
import sys
from typing import TYPE_CHECKING, Any

from .env.base import InvariantViolation

from .agent.prompt import assemble_system_prompt
from .env.base import BaseEnv
from .tasks.schema import AnyMandate
from .tools.registry import ToolContext, all_tools, execute_tool

if TYPE_CHECKING:  # 仅类型标注；运行时在函数体内 lazy import，保持启动轻量
    import mcp.types as types

MCP_NO_USER_REPLY = (
    "[mcp-mode] There is no simulated user in this session. "
    "Ask the human operator directly in the chat conversation."
)


def build_mcp_tools(profile: str = "exchange") -> list[types.Tool]:
    """反射 registry → MCP Tool 列表（AC-10a/AC-14h 的被测对象）。纯函数，无 IO。

    缺省 exchange：v0.1 客户端面字节不变（R4 承重）。"""
    import mcp.types as types

    return [
        types.Tool(name=t.name, description=t.description, inputSchema=t.json_schema())
        for t in all_tools(profile)
    ]


def _make_context(env: BaseEnv, *, auto_approve: bool) -> ToolContext:
    decision = "approved" if auto_approve else "denied"
    return ToolContext(
        env=env,
        ask_user=lambda question: MCP_NO_USER_REPLY,
        request_confirmation=lambda action_summary: decision,
    )


def _make_call_tool(ctx: ToolContext):
    """call_tool handler 工厂（模块级，便于单测 InvariantViolation 护栏）。"""

    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> list[Any]:
        import mcp.types as types

        try:
            invocation = execute_tool(name, arguments or {}, ctx)
        except InvariantViolation as exc:
            # SDK 的 call_tool wrapper 会把 handler 的一切 Exception 吞成 isError 响应
            # 并继续服务；账本损坏必须炸出（specs/10 §3）——唯一可靠通道是自行终止进程
            print(f"FATAL InvariantViolation: {exc}", file=sys.stderr, flush=True)
            os._exit(70)
        payload = {
            "ok": invocation.ok,
            "result": invocation.result,
            "error_code": invocation.error_code,
            "error_kind": invocation.error_kind,
            "error_message": invocation.error_message,
        }
        return [types.TextContent(type="text", text=json.dumps(payload, ensure_ascii=False))]

    return _call_tool


async def _serve_async(env: BaseEnv, mandate: AnyMandate, *, auto_approve: bool) -> None:
    import mcp.types as types
    from mcp.server.lowlevel import Server
    from mcp.server.stdio import stdio_server

    server = Server("agent-assay", instructions=assemble_system_prompt(mandate))
    ctx = _make_context(env, auto_approve=auto_approve)
    profile = getattr(mandate, "kind", "exchange")  # D-o：kind 是唯一分派源

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        return build_mcp_tools(profile)

    _call_tool = _make_call_tool(ctx)

    try:
        # 校验只做一层：registry 是唯一校验点，SDK 侧关闭 input 校验以保住结构化错误契约
        server.call_tool(validate_input=False)(_call_tool)
    except TypeError:  # 旧版 SDK 无 validate_input 参数
        server.call_tool()(_call_tool)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def serve(env: BaseEnv, mandate: AnyMandate, *, auto_approve: bool = False) -> None:
    """阻塞运行 stdio MCP server，直至客户端断开（stdin EOF）。"""
    import asyncio

    asyncio.run(_serve_async(env, mandate, auto_approve=auto_approve))

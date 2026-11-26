"""
审批函数 —— FunctionGraph 入口（由 APIG 触发）

用户通过 APIG 审批端点做出决定：
- GET  /approval?request_id=<id>                     → 返回 HTML 审批页面（含 Approve / Reject 按钮）
- POST /approval  body {request_id, decision, approver} → 执行审批动作（HTML 表单或程序化调用）

审批请求持久化于 OBS（OBSStore，非 VPC 函数即可公网可达）。
批准后会直接异步调用执行函数。
"""

import os
import sys
import json
import time
import logging
from urllib.parse import parse_qs

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from approval_request_handler import get_approval_request, update_approval_request  # noqa: E402
from fg_invoke import invoke_function_async  # noqa: E402

logger = logging.getLogger()
logger.setLevel(logging.INFO)

EXECUTION_FUNCTION_URN = os.environ.get('EXECUTION_FUNCTION_URN', '')


def _extract_params(event):
    """从 GET query 或 POST body 中解析审批参数，返回 (request_id, decision, approver)。"""
    request_id = None
    decision = None
    approver = None

    # 1) GET：queryStringParameters（APIG 转发后位于 event 内）
    qs = event.get('queryStringParameters') or event.get('queryString') or {}
    if isinstance(qs, dict):
        request_id = qs.get('request_id') or qs.get('requestId')
        decision = qs.get('decision')
        approver = qs.get('approver')

    # 2) POST：body（JSON 或 application/x-www-form-urlencoded）
    body = event.get('body')
    if body is not None:
        parsed = None
        if isinstance(body, dict):
            parsed = body
        elif isinstance(body, str):
            s = body.strip()
            if s.startswith('{'):
                try:
                    parsed = json.loads(s)
                except Exception:
                    parsed = None
            if parsed is None:
                try:
                    parsed = {k: (v[0] if isinstance(v, list) and v else v)
                              for k, v in parse_qs(s).items()}
                except Exception:
                    parsed = None
        if isinstance(parsed, dict):
            request_id = parsed.get('request_id') or parsed.get('requestId') or request_id
            if parsed.get('decision') is not None:
                decision = parsed.get('decision')
            if parsed.get('approver') is not None:
                approver = parsed.get('approver')

    if isinstance(decision, str):
        decision = decision.lower()
    return request_id, decision, approver


def _render_approval_page(request_id, record):
    """返回 HTML 审批页面（GET 且无 decision 时）。"""
    if not record:
        html = (
            "<h2>审批请求不存在</h2>"
            f"<p>request_id: <code>{request_id}</code></p>"
            "<p>该请求可能已过期（TTL 24h）或从未创建。</p>"
        )
    elif record.get('status') != 'pending':
        html = (
            "<h2>该审批请求已处理</h2>"
            f"<p>request_id: <code>{request_id}</code></p>"
            f"<p>当前状态：<b>{record.get('status')}</b></p>"
        )
    else:
        summary = (record.get('analysis_summary') or '（无分析摘要）').replace('<', '&lt;').replace('>', '&gt;')
        risk = (record.get('risk_level') or 'medium').upper()
        html = f"""
        <h2>CCE 事件审批</h2>
        <p><b>事件 ID：</b> {record.get('incident_id', 'unknown')}</p>
        <p><b>集群：</b> {record.get('cluster_name', 'unknown')}</p>
        <p><b>告警：</b> {record.get('alarm_name', 'unknown')}</p>
        <p><b>风险等级：</b> <span style="color:#e67e22">{risk}</span></p>
        <hr/>
        <pre style="white-space:pre-wrap;background:#f5f5f5;padding:12px;border-radius:6px;">{summary}</pre>
        <form method="POST" action="">
          <input type="hidden" name="request_id" value="{request_id}"/>
          <button type="submit" name="decision" value="approve"
                  style="background:#27ae60;color:#fff;border:0;padding:10px 20px;border-radius:6px;cursor:pointer;margin-right:12px;">
            ✅ 批准并执行
          </button>
          <button type="submit" name="decision" value="reject"
                  style="background:#c0392b;color:#fff;border:0;padding:10px 20px;border-radius:6px;cursor:pointer;">
            ⛔ 拒绝
          </button>
        </form>
        """

    page = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>CCE 事件审批</title>
<body style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;color:#222;">
<h1 style="color:#2c3e50;">🔧 CCE 自动修复审批</h1>
{html}
<hr style="margin-top:32px;"/>
<p style="color:#999;font-size:12px;">本页面由 CCE 事件自动响应系统生成。批准后将自动执行修复命令。</p>
</body></html>"""
    return page


def _respond(status_code, body, content_type="application/json", is_base64=False):
    """构造 FunctionGraph 自定义镜像响应信封（shim 会原样 JSON 序列化返回）。"""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": content_type},
        "body": body,
        "isBase64Encoded": is_base64,
    }


def _render_result_page(title, message, ok=True):
    """审批动作后的结果展示页（POST 批准后浏览器展示）。"""
    color = "#27ae60" if ok else "#c0392b"
    page = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"/>
<title>审批结果</title>
<body style="font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:640px;margin:40px auto;padding:0 16px;color:#222;">
<h1 style="color:{color};">{'✅' if ok else '⛔'} {title}</h1>
<p>{message}</p>
<hr style="margin-top:32px;"/>
<p style="color:#999;font-size:12px;">CCE 事件自动响应系统</p>
</body></html>"""
    return page


def handler(event, context):
    """
    APIG 后端：GET 渲染审批页面，POST 执行审批决定。

    POST 期望请求体（JSON 或表单）：
    {
        "request_id": "<uuid>",
        "decision": "approve" | "reject",
        "approver": "<email 或姓名，可选>"
    }
    """
    try:
        logger.info(f"📥 收到审批请求: {json.dumps(event, default=str)[:1000]}")

        request_id, decision, approver = _extract_params(event)

        # GET 且无 decision → 渲染审批页面（邮件链接点击后的默认行为）
        if not decision:
            if not request_id:
                return _respond(400, _render_result_page("参数缺失", "缺少 request_id", ok=False),
                               content_type="text/html; charset=utf-8")
            record = get_approval_request(request_id)
            html = _render_approval_page(request_id, record)
            return _respond(200, html, content_type="text/html; charset=utf-8")

        if not request_id:
            return _respond(400, _render_result_page("参数缺失", "缺少 request_id", ok=False),
                           content_type="text/html; charset=utf-8")

        record = get_approval_request(request_id)
        if not record:
            return _respond(404, _render_result_page("未找到", f"审批请求不存在：{request_id}", ok=False),
                           content_type="text/html; charset=utf-8")

        if record.get('status') != 'pending':
            return _respond(409, _render_result_page(
                "已处理", f"该请求已处理，当前状态：{record.get('status')}", ok=False),
                content_type="text/html; charset=utf-8")

        approver = approver or "email-approver"

        if decision == 'reject':
            update_approval_request(
                request_id, status='rejected', approver=approver, decided_at=time.time())
            logger.info(f"⛔ 请求 {request_id} 已被 {approver} 拒绝")
            return _respond(200, _render_result_page("已拒绝", f"请求 {request_id} 已被拒绝。", ok=False),
                           content_type="text/html; charset=utf-8")

        if decision != 'approve':
            return _respond(400, _render_result_page("参数错误", "decision 必须为 approve 或 reject", ok=False),
                           content_type="text/html; charset=utf-8")

        # 标记为已批准
        update_approval_request(
            request_id, status='approved', approver=approver, decided_at=time.time())
        logger.info(f"✅ 请求 {request_id} 已由 {approver} 批准")

        # 直接异步调用执行函数（重设计方案：审批直调执行）
        ok = invoke_function_async(EXECUTION_FUNCTION_URN, record)
        if ok:
            return _respond(200, _render_result_page(
                "已批准", f"请求 {request_id} 已批准并触发执行。", ok=True),
                content_type="text/html; charset=utf-8")
        return _respond(500, _render_result_page(
            "执行触发失败", f"已批准，但执行函数异步调用失败：{request_id}", ok=False),
            content_type="text/html; charset=utf-8")

    except Exception as e:
        logger.error(f"❌ 审批函数异常: {str(e)}")
        return _respond(500, json.dumps({"error": str(e)}))

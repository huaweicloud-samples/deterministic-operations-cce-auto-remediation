"""
审批通知处理器 —— 基于 SMN（消息通知服务）

审批链接指向 APIG 审批端点（APIG_APPROVAL_URL 环境变量）。
"""

import os
import json
import logging
from typing import Dict, Any

logger = logging.getLogger(__name__)

# 初始化 SMN 客户端（华为云 Python SDK）
from huaweicloudsdksmn.v2 import SmnClient, PublishMessageRequest, PublishMessageRequestBody
from huaweicloudsdksmn.v2.region.smn_region import SmnRegion

from common import _basic_creds, region as _common_region

_APPROVAL_TOPIC_URN = os.environ.get('APPROVAL_TOPIC_URN')
_REGION = _common_region()
_APIG_APPROVAL_URL = os.environ.get('APIG_APPROVAL_URL', 'https://<your-apig-host>/approval')


def _client():
    from huaweicloudsdkcore.http.http_config import HttpConfig

    cred = _basic_creds()
    return SmnClient.new_builder() \
        .with_http_config(HttpConfig.get_default_config()) \
        .with_credentials(cred) \
        .with_region(SmnRegion.value_of(_REGION)) \
        .build()


def parse_analysis_sections(analysis_text: str) -> Dict[str, str]:
    """将模型分析拆分为结构化小节"""
    sections = {}

    section_names = [
        'ISSUE EXPLANATION',
        'IMMEDIATE ACTION',
        'PREVENTIVE ACTION',
        'ALTERNATIVE ACTION'
    ]

    for section in section_names:
        marker = f"## {section}"
        if marker in analysis_text:
            start_idx = analysis_text.find(marker)
            if start_idx != -1:
                next_section_idx = len(analysis_text)
                for other_section in section_names:
                    if other_section != section:
                        other_idx = analysis_text.find(f"## {other_section}", start_idx + 1)
                        if other_idx != -1 and other_idx < next_section_idx:
                            next_section_idx = other_idx

                section_content = analysis_text[start_idx:next_section_idx].strip()
                section_content = section_content.replace(marker, "").strip()
                sections[section] = section_content

    return sections


def format_approval_email(incident_data: Dict[str, Any], analysis_result: Dict[str, Any], approval_request: Dict[str, Any]) -> str:
    """将分析结果格式化为可读邮件，并附带审批链接"""

    analysis_text = analysis_result.get('summary', '')
    sections = parse_analysis_sections(analysis_text)

    incident_id = incident_data.get('incident_id', 'unknown')
    cluster_name = incident_data.get('cluster_name', 'unknown')
    alarm_name = incident_data.get('alarm_name', 'unknown')
    risk_level = analysis_result.get('risk_level', 'medium')
    request_id = approval_request.get('request_id', 'unknown')

    # 审批链接指向 APIG 审批端点
    approval_link = f"{_APIG_APPROVAL_URL}?request_id={request_id}"

    email_body = f"""
🚨 CCE 事件分析完成 🚨

事件详情：
• 事件 ID: {incident_id}
• 集群: {cluster_name}
• 告警: {alarm_name}
• 风险等级: {risk_level.upper()}
• 请求 ID: {request_id}

{sections.get('ISSUE EXPLANATION', '未提供问题说明')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

建议动作（按顺序执行）：

🔴 1. 立即动作（优先尝试）：
{sections.get('IMMEDIATE ACTION', '未提供立即动作')}

🟡 2. 预防动作（立即动作失败时）：
{sections.get('PREVENTIVE ACTION', '未提供预防动作')}

🔵 3. 备选动作（预防动作失败时）：
{sections.get('ALTERNATIVE ACTION', '未提供备选动作')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

需要审批：
请审阅上述分析，并在确认后批准执行这些动作。

🔗 审批链接：{approval_link}

本邮件由 CCE 事件自动响应系统自动发出。
"""

    return email_body.strip()


def send_approval_notification(incident_data: Dict[str, Any], analysis_result: Dict[str, Any], approval_request: Dict[str, Any]) -> bool:
    """通过 SMN 主题发送审批通知"""

    try:
        if not _APPROVAL_TOPIC_URN:
            logger.error("❌ 未设置 APPROVAL_TOPIC_URN 环境变量")
            return False

        email_body = format_approval_email(incident_data, analysis_result, approval_request)

        cluster_name = incident_data.get('cluster_name', 'unknown')
        alarm_name = incident_data.get('alarm_name', 'unknown')
        risk_level = analysis_result.get('risk_level', 'medium')
        request_id = approval_request.get('request_id', 'unknown')

        subject = f"🚨 CCE 事件 [{risk_level.upper()}] - {cluster_name} - {alarm_name} - {request_id[:8]}"

        client = _client()
        req = PublishMessageRequest(topic_urn=_APPROVAL_TOPIC_URN)
        # SDK 要求 body 为 PublishMessageRequestBody 对象（直接赋字符串会导致
        # 服务端收到空 message 报 SMN.0009 "Message is invalid"）
        req.body = PublishMessageRequestBody(message=email_body, subject=subject)
        client.publish_message(req)

        logger.info(f"✅ 审批通知发送成功 - 请求 ID: {request_id}")
        return True

    except Exception as e:
        logger.error(f"❌ 发送审批通知失败: {str(e)}")
        return False

"""
审批请求处理器 —— 基于 OBS 持久化存储。

非 VPC 的 FunctionGraph 函数经 OBS 公网端点即可读写，无需为函数配置 VPC/NAT。
每条审批请求以 JSON 对象存储于 OBS：<OBS_KEY_PREFIX><request_id>.json
对外函数签名（create/get/update/extract_command_from_section）在本服务内保持统一，供各调用方复用。
"""

import os
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, Any

logger = logging.getLogger(__name__)

# OBS 存储桶与键前缀（由函数运行环境提供；缺失时仅日志、不写入）
OBS_BUCKET = os.environ.get('OBS_BUCKET')
OBS_KEY_PREFIX = os.environ.get('OBS_KEY_PREFIX', 'cce-approval/')

from common import OBSStore

_store = OBSStore(bucket=OBS_BUCKET, prefix=OBS_KEY_PREFIX)


def create_approval_request(incident_data: Dict[str, Any], analysis_result: Dict[str, Any]) -> Dict[str, Any]:
    """创建审批请求记录（写入 OBS）。返回记录或 None。"""
    try:
        logger.info(f"🔍 为事件创建审批请求: {incident_data.get('incident_id', 'unknown')}")

        request_id = str(uuid.uuid4())
        logger.info(f"🆔 生成请求 ID: {request_id}")

        analysis_text = analysis_result.get('summary', '')
        immediate_action = extract_command_from_section(analysis_text, 'IMMEDIATE ACTION')
        preventive_action = extract_command_from_section(analysis_text, 'PREVENTIVE ACTION')
        alternative_action = extract_command_from_section(analysis_text, 'ALTERNATIVE ACTION')

        logger.info(f"🔴 立即动作: {immediate_action}")
        logger.info(f"🟡 预防动作: {preventive_action}")
        logger.info(f"🔵 备选动作: {alternative_action}")

        approval_request = {
            'request_id': request_id,
            'incident_id': incident_data.get('incident_id', 'unknown'),
            'cluster_name': incident_data.get('cluster_name', 'unknown'),
            'cluster_id': incident_data.get('cluster_id') or os.environ.get('CLUSTER_ID', ''),
            'alarm_name': incident_data.get('alarm_name', 'unknown'),
            'status': 'pending',
            'risk_level': analysis_result.get('risk_level', 'medium'),
            'analysis_summary': analysis_text,
            'immediate_action_command': immediate_action,
            'preventive_action_command': preventive_action,
            'alternative_action_command': alternative_action,
            'created_at': datetime.utcnow().isoformat(),
            'ttl': int(datetime.utcnow().timestamp()) + (24 * 60 * 60),  # 24 小时 TTL
        }

        if _store.put(request_id, approval_request):
            logger.info(f"✅ 审批请求创建成功: {request_id}")
            return approval_request
        logger.error("❌ 审批请求写入 OBS 失败")
        return None
    except Exception as e:  # noqa: BLE001
        logger.error(f"❌ 创建审批请求失败: {str(e)}")
        return None


def get_approval_request(request_id: str) -> Dict[str, Any]:
    """按 request_id 读取审批请求（供审批函数调用）。"""
    return _store.get(request_id)


def update_approval_request(request_id: str, **fields) -> bool:
    """更新审批请求字段（状态、执行结果、审批人等）。"""
    try:
        if _store.update(request_id, **fields):
            logger.info(f"✅ 审批请求 {request_id} 已更新: {list(fields.keys())}")
            return True
        return False
    except Exception as e:  # noqa: BLE001
        logger.error(f"❌ 更新审批请求失败: {str(e)}")
        return False


def extract_command_from_section(analysis_text: str, section_name: str) -> str:
    """从指定小节提取命令（支持 [KUBECTL] / [HW_CLI] 标签）。"""
    marker = f"## {section_name}"
    if marker in analysis_text:
        start_idx = analysis_text.find(marker)
        if start_idx != -1:
            section_names = ['ISSUE EXPLANATION', 'IMMEDIATE ACTION', 'PREVENTIVE ACTION', 'ALTERNATIVE ACTION']
            next_section_idx = len(analysis_text)
            for other_section in section_names:
                if other_section != section_name:
                    other_idx = analysis_text.find(f"## {other_section}", start_idx + 1)
                    if other_idx != -1 and other_idx < next_section_idx:
                        next_section_idx = other_idx

            section_content = analysis_text[start_idx:next_section_idx].strip()

            import re
            kubectl_match = re.search(r'\[KUBECTL\]\s*(.+?)(?=\n\n|\n##|$)', section_content, re.DOTALL)
            hw_cli_match = re.search(r'\[HW_CLI\]\s*(.+?)(?=\n\n|\n##|$)', section_content, re.DOTALL)

            if kubectl_match:
                return kubectl_match.group(1).strip()
            elif hw_cli_match:
                return hw_cli_match.group(1).strip()

    return "No command found"

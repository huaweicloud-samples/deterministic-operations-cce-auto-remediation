"""
探测函数 —— FunctionGraph 入口

事件来源为 CES 告警（经 SMN 主题触发或 CES 直接触发 FunctionGraph）。
职责：解析告警、采集 CCE 集群信息、组装事件体，再异步调用分析函数。

触发器：CES 告警 → SMN 主题 → 本函数（或 CES 告警动作直接指向本函数）。
下游：异步调用 ANALYSIS_FUNCTION_URN 指向的分析函数。
"""

import os
import sys
import json
import logging

# 保证可导入 src 根目录下的共享模块
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from fg_invoke import invoke_function_async  # noqa: E402

logger = logging.getLogger()
logger.setLevel(logging.INFO)
# 确保存在 stdout/stderr handler，否则 FunctionGraph 不会采集 INFO 级应用日志
logging.basicConfig(level=logging.INFO)

ANALYSIS_FUNCTION_URN = os.environ.get('ANALYSIS_FUNCTION_URN', '')
REGION = os.environ.get('HW_REGION', 'cn-north-4')


def _cce_client():
    # 凭据统一走 common（agency 临时凭据 / HW_* 环境变量 / 元数据服务）
    from huaweicloudsdkcore.http.http_config import HttpConfig
    from huaweicloudsdkcce.v3 import CceClient
    from huaweicloudsdkcce.v3.region.cce_region import CceRegion
    from common import _basic_creds, region as _common_region

    return CceClient.new_builder() \
        .with_http_config(HttpConfig.get_default_config()) \
        .with_credentials(_basic_creds()) \
        .with_region(CceRegion.value_of(_common_region())) \
        .build()


def get_cluster_details(cluster_id):
    """采集 CCE 集群详情（状态、版本、节点池）"""
    try:
        client = _cce_client()
        from huaweicloudsdkcce.v3 import ShowClusterRequest, ListNodePoolsRequest

        cluster = client.show_cluster(ShowClusterRequest(cluster_id=cluster_id)).to_dict()
        status = cluster.get('status', {}).get('phase') or cluster.get('status', {}).get('state')
        version = cluster.get('spec', {}).get('version')

        node_pools = []
        try:
            np_resp = client.list_node_pools(ListNodePoolsRequest(cluster_id=cluster_id)).to_dict()
            for np in np_resp.get('nodePools', []) or []:
                np_meta = np.get('metadata', {})
                np_spec = np.get('spec', {})
                node_pools.append({
                    'name': np_meta.get('name'),
                    'status': np.get('status', {}).get('phase'),
                    'flavor': np_spec.get('nodeTemplate', {}).get('flavor'),
                    'initial_node_count': np_spec.get('initialNodeCount'),
                })
        except Exception as e:
            logger.warning(f"采集节点池信息失败: {e}")

        return {
            'status': status,
            'version': version,
            'node_pools': node_pools,
        }
    except Exception as e:
        logger.error(f"采集集群详情失败: {e}")
        return {'error': str(e)}


def _unwrap_event(event):
    """递归展开 SMN / FunctionGraph / CES 在事件外层包裹的信封。

    实测 SMN 触发器投递形态：
      {"record": [{"event_version":"1.0",
                   "smn": {"type":"notification", "topic_urn":"...",
                           "message":"<alarm JSON 字符串>", ...}}]}
    也兼容：
      - 直接测试 {"message": "<alarm JSON>"}
      - 已预解析为 dict 的 {"message": {...}}
      - 双层信封 {"message": "{\\"smn\\":{\\"message\\":\\"<alarm JSON>\\"}}"}
    逐层 unwrap 直到找到 alarm_name/cluster_name 等告警字段。
    """
    detail = event
    for _ in range(8):
        if isinstance(detail, list):
            detail = detail[0] if detail else {}
            continue
        if not isinstance(detail, dict):
            break
        if detail.get('alarm_name') or detail.get('cluster_name') \
                or detail.get('alarmName') or detail.get('clusterName'):
            break
        # 先展开 record / smn / data 等包裹层
        descended = False
        for env_key in ('record', 'smn', 'data', 'Records', 'resources', 'resource'):
            nxt = detail.get(env_key)
            if isinstance(nxt, list) and nxt:
                detail = nxt[0]
                descended = True
                break
            if isinstance(nxt, dict):
                detail = nxt
                descended = True
                break
        if descended:
            continue
        # 没有包裹层，再尝试 message/Message/body/event 字符串或 dict
        nxt = detail.get('message', detail.get('Message',
                  detail.get('body', detail.get('event', detail.get('Event')))))
        if isinstance(nxt, dict):
            detail = nxt
            continue
        if isinstance(nxt, str):
            try:
                detail = json.loads(nxt)
                continue
            except Exception:
                break
        break
    return detail


def extract_alarm(event):
    """从多种事件形态中提取告警信息，返回 (alarm_name, cluster_id, raw_detail)"""
    detail = _unwrap_event(event)

    alarm_name = (
        detail.get('alarm_name')
        or detail.get('alarmName')
        or detail.get('name')
        or 'unknown'
    )
    cluster_id = detail.get('cluster_name') or detail.get('clusterName')

    # CES 告警 resources/dimension 中可能带集群 ID
    if not cluster_id:
        resources = detail.get('resources') or detail.get('resource') or {}
        if isinstance(resources, dict):
            cluster_id = (
                resources.get('cluster_id')
                or resources.get('clusterId')
                or resources.get('resource_id')
            )
        dims = detail.get('dimension') or detail.get('dimensions') or []
        if not cluster_id and isinstance(dims, list):
            for d in dims:
                if isinstance(d, dict) and 'cluster' in str(d.get('name', '')).lower():
                    cluster_id = d.get('value')
                    break

    return alarm_name, cluster_id, detail


def handler(event, context):
    try:
        logger.info(f"收到事件: {json.dumps(event, default=str)[:1000]}")

        alarm_name, cluster_id, raw = extract_alarm(event)

        # 仅处理处于告警态的事件
        status = str(raw.get('alarm_status', raw.get('status', 'alarm'))).lower()
        if status and status not in ('alarm', 'triggered', ''):
            return {"statusCode": 200, "body": f"非告警态: {status}"}

        logger.info(f"🚨 提取告警: {alarm_name}, 集群: {cluster_id}")

        cluster_info = get_cluster_details(cluster_id) if cluster_id else {}
        incident_id = f"alarm-{alarm_name}-{raw.get('trigger_time', raw.get('timestamp', 'unknown'))}"

        incident_data = {
            "alarm_name": alarm_name,
            "alarm_reason": raw.get('alarm_reason') or raw.get('reason', ''),
            "cluster_name": cluster_id or raw.get('cluster_name', 'unknown'),
            "timestamp": raw.get('trigger_time') or raw.get('timestamp'),
            "incident_id": incident_id,
            "incident_type": "ces-alarm",
            "cluster_info": cluster_info,
            "raw_event": event,
        }

        logger.info(f"📨 异步调用分析函数: {ANALYSIS_FUNCTION_URN}")
        ok = invoke_function_async(ANALYSIS_FUNCTION_URN, incident_data)
        if ok:
            return {"statusCode": 200, "body": json.dumps({"message": "事件已提交分析", "incident_id": incident_id})}
        return {"statusCode": 500, "body": json.dumps({"error": "分析函数异步调用失败"})}

    except Exception as e:
        logger.error(f"探测函数异常: {str(e)}")
        return {"statusCode": 500, "body": json.dumps({"error": str(e)})}

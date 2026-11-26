"""
分析函数 —— FunctionGraph 入口

由探测函数异步调用，运行只读分析（model_adapter + CCE 只读 MCP），
随后在 OBS 创建审批请求，并通过 SMN 发送审批通知。
"""

import os
import sys
import json
import logging
import asyncio

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from analysis_service import analyze_incident  # noqa: E402
from approval_request_handler import create_approval_request  # noqa: E402
from notification_handler import send_approval_notification  # noqa: E402

logger = logging.getLogger()
logger.setLevel(logging.INFO)


def handler(event, context):
    """分析函数入口，接收 incident_data"""
    try:
        logger.info(f"📥 收到分析请求: {json.dumps(event, default=str)[:1000]}")

        if 'body' in event:
            incident_data = json.loads(event['body'])
        else:
            incident_data = event

        # 生成目标 CCE 集群的 kubeconfig（只读探查前置条件）；失败不阻断分析，仅缺少集群实时数据
        try:
            from cce_kubeconfig import get_cce_kubeconfig, write_kubeconfig
            _cid = incident_data.get('cluster_name') or incident_data.get('cluster_id') or os.environ.get('CLUSTER_ID')
            if _cid:
                _kc = get_cce_kubeconfig(_cid)
                write_kubeconfig(_kc)
                logger.info(f"📝 已写入 CCE kubeconfig（集群 {_cid}）用于只读探查")
            else:
                logger.warning("⚠️ 缺少集群 ID，跳过 kubeconfig 生成")
        except Exception as e:
            logger.warning(f"⚠️ 生成 kubeconfig 失败（只读探查将缺少集群实时数据）: {e}")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            analysis_result = loop.run_until_complete(analyze_incident(incident_data))
            logger.info("📊 分析完成")

            logger.info("💾 在 OBS 创建审批请求...")
            approval_request = create_approval_request(incident_data, analysis_result)

            if approval_request:
                logger.info(f"✅ 审批请求已创建: {approval_request['request_id']}")

                logger.info("📧 发送审批通知...")
                notification_sent = send_approval_notification(incident_data, analysis_result, approval_request)

                if notification_sent:
                    return {
                        'statusCode': 200,
                        'headers': {'Content-Type': 'application/json'},
                        'body': json.dumps({
                            'message': '分析完成，已创建审批请求并发送通知',
                            'request_id': approval_request['request_id'],
                            'analysis_result': analysis_result,
                        }),
                    }
                else:
                    return {
                        'statusCode': 500,
                        'headers': {'Content-Type': 'application/json'},
                        'body': json.dumps({
                            'message': '分析完成，审批请求已创建，但通知发送失败',
                            'request_id': approval_request['request_id'],
                            'analysis_result': analysis_result,
                        }),
                    }
            else:
                return {
                    'statusCode': 500,
                    'headers': {'Content-Type': 'application/json'},
                    'body': json.dumps({
                        'message': '分析完成，但审批请求创建失败',
                        'analysis_result': analysis_result,
                    }),
                }
        finally:
            loop.close()

    except Exception as e:
        logger.error(f"❌ 分析失败: {str(e)}")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json'},
            'body': json.dumps({'error': '分析失败', 'message': str(e)}),
        }

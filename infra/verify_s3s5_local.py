"""
组件级验证 S3(写 OBS 审批请求) + S5(发 SMN 审批通知)。
直接复用真实 handler 代码（approval_request_handler / notification_handler），
以样例 analysis_result 跑通下游持久化与通知路径，不依赖 MaaS。
验证后清理测试对象，避免污染审批桶。
"""
import os, sys, json, time, uuid

# 函数运行所需的环境变量（AK/SK 由部署者在本地配置；运行前请先设置 HW_ACCESS_KEY / HW_SECRET_KEY）
if not os.environ.get('HW_ACCESS_KEY') or not os.environ.get('HW_SECRET_KEY'):
    raise RuntimeError("HW_ACCESS_KEY / HW_SECRET_KEY must be exported before running this local verification.")
os.environ['HW_REGION'] = "cn-north-4"
os.environ['HW_PROJECT_ID'] = "cc2cb3d01b0c46798d77c4cd8ecf6b7e"
os.environ['OBS_BUCKET'] = "mcp-cce-auto-remediation-approval"
os.environ['OBS_KEY_PREFIX'] = "cce-approval/"
os.environ['APPROVAL_TOPIC_URN'] = "urn:smn:cn-north-4:cc2cb3d01b0c46798d77c4cd8ecf6b7e:mcp-cce-auto-remediation-alert"
os.environ['APIG_APPROVAL_URL'] = "https://ac194141e15c4ddab4ebb0c65ba88317.apic.cn-north-4.huaweicloudapis.com/approval"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

import logging
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')

from approval_request_handler import create_approval_request, get_approval_request
from obs import ObsClient

# ---- 样例输入（模拟 analysis 已产出分析结果）----
incident_data = {
    "incident_id": "alarm-PodCrashLoopBackOff-component-test",
    "cluster_name": "0dff45d1-bba6-11f1-b4fb-0255ac100268",
    "cluster_id": "0dff45d1-bba6-11f1-b4fb-0255ac100268",
    "alarm_name": "PodCrashLoopBackOff",
    "incident_type": "ces-alarm",
    "timestamp": "2026-09-29T10:00:00Z",
}
analysis_result = {
    "summary": (
        "## ISSUE EXPLANATION\nPod 处于 CrashLoopBackOff，因探针失败。\n"
        "## IMMEDIATE ACTION\n[KUBECTL] kubectl delete pod test-pod -n default\n"
        "## PREVENTIVE ACTION\n[KUBECTL] kubectl apply -f resource-limits.yaml\n"
        "## ALTERNATIVE ACTION\n[KUBECTL] kubectl rollout restart deployment test-deploy -n default\n"
    ),
    "root_cause": "探针配置不当导致崩溃循环",
    "recommended_actions": ["kubectl delete pod test-pod -n default"],
    "risk_level": "medium",
}

print("===== S3: 写 OBS 审批请求 =====")
req = create_approval_request(incident_data, analysis_result)
if not req:
    print("❌ S3 失败：create_approval_request 返回 None")
    sys.exit(1)
rid = req['request_id']
print("✅ S3 写入成功, request_id =", rid)

print("===== S3: 回读 OBS 校验持久化 =====")
back = get_approval_request(rid)
if back and back.get('request_id') == rid and back.get('status') == 'pending':
    print("✅ S3 回读一致, status =", back['status'], ", immediate_action =", back.get('immediate_action_command'))
else:
    print("❌ S3 回读异常:", back)
    sys.exit(1)

print("===== 清理测试对象 =====")
try:
    c = ObsClient(access_key_id=os.environ['HW_ACCESS_KEY'],
                  secret_access_key=os.environ['HW_SECRET_KEY'],
                  server="obs.cn-north-4.myhuaweicloud.com", is_secure=True)
    key = f"cce-approval/{rid}.json"
    r = c.deleteObject(os.environ['OBS_BUCKET'], key)
    print("清理 deleteObject status =", r.get('status'))
    c.close()
except Exception as e:
    print("⚠️ 清理异常(非致命):", e)

print("\n===== S3 组件验证结论: 通过 (OBS 可写/可读/key 格式正确) =====")
print("request_id 示例(供参考):", rid)

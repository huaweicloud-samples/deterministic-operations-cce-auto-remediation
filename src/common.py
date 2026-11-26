"""
公共模块：华为云 FunctionGraph 自定义镜像运行时共享依赖。

集中解决两件事：
1. 凭据 / 项目 ID 解析 —— 真实函数通过「函数执行委托(agency)」获得临时凭据，而非明文 AK/SK。
   解析优先级：
     a) 环境变量 HUAWEICLOUD_AGENCY_ACCESS_KEY_ID / SECRET_ACCESS_KEY / SECURITY_TOKEN
        （FunctionGraph 为绑定了 agency 的自定义镜像注入的临时凭据）
     b) 环境变量 HW_ACCESS_KEY / HW_SECRET_KEY / HW_SECURITY_TOKEN（本地调试用）
     c) 元数据服务 http://169.254.169.254/openstack/latest/securitykey
2. 用上述凭据签名调用华为云服务：SMN 发布、OBS 持久化（审批请求存储）、FunctionGraph 异步调用。

所有与华为云 SDK 的交互均延迟导入，避免镜像部分安装时模块加载失败。
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_METADATA_SECURITYKEY = "http://169.254.169.254/openstack/latest/securitykey"
_METADATA_META_DATA = "http://169.254.169.254/openstack/latest/meta_data"


def get_credentials():
    """返回 (ak, sk, token)。优先 agency 注入，其次显式 env，最后元数据服务。"""
    ak = (
        os.environ.get("HUAWEICLOUD_AGENCY_ACCESS_KEY_ID")
        or os.environ.get("HW_ACCESS_KEY")
    )
    sk = (
        os.environ.get("HUAWEICLOUD_AGENCY_SECRET_ACCESS_KEY")
        or os.environ.get("HW_SECRET_KEY")
    )
    token = (
        os.environ.get("HUAWEICLOUD_AGENCY_SECURITY_TOKEN")
        or os.environ.get("HW_SECURITY_TOKEN")
    )
    if ak and sk:
        return ak, sk, token

    # 元数据服务（函数已绑定具备相应权限的 agency 时可用）
    try:
        import requests

        r = requests.get(_METADATA_SECURITYKEY, timeout=2)
        if r.ok:
            d = r.json()
            if "accessKeyId" in d:
                return d["accessKeyId"], d.get("secretKey"), d.get("securitytoken")
            if "securitykey" in d:
                skd = d["securitykey"]
                return skd.get("access"), skd.get("secret"), skd.get("securitytoken")
    except Exception as e:  # noqa: BLE001
        logger.debug("元数据获取临时凭据失败: %s", e)

    return None, None, None


def get_project_id():
    """返回 project_id，优先环境变量，其次元数据服务。"""
    pid = os.environ.get("HW_PROJECT_ID") or os.environ.get("HUAWEICLOUD_AGENCY_PROJECT_ID")
    if pid:
        return pid
    try:
        import requests

        r = requests.get(_METADATA_META_DATA, timeout=2)
        if r.ok:
            d = r.json()
            return d.get("project_id") or d.get("tenant_id")
    except Exception as e:  # noqa: BLE001
        logger.debug("元数据获取 project_id 失败: %s", e)
    return None


def region():
    return os.environ.get("HW_REGION", "cn-north-4")


def _basic_creds():
    """构造 BasicCredentials（含 agency 临时 token）。"""
    from huaweicloudsdkcore.auth.credentials import BasicCredentials

    ak, sk, token = get_credentials()
    pid = get_project_id()
    if not ak or not sk:
        raise RuntimeError(
            "未获取到华为云访问凭据。请确认函数已绑定具备相应权限的 IAM 委托(agency)，"
            "且 FunctionGraph 已注入 HUAWEICLOUD_AGENCY_* 临时凭据；或在本地调试时设置 HW_ACCESS_KEY/SK。"
        )
    cred = BasicCredentials(ak, sk, pid)
    if token:
        cred = cred.with_security_token(token)
    return cred


def smn_publish(topic_urn, subject, message):
    """向 SMN 主题发布消息（文本）。返回 True/False。"""
    try:
        from huaweicloudsdkcore.http.http_config import HttpConfig
        from huaweicloudsdksmn.v2 import SmnClient, PublishMessageRequest
        from huaweicloudsdksmn.v2.region.smn_region import SmnRegion

        client = (
            SmnClient.new_builder()
            .with_http_config(HttpConfig.get_default_config())
            .with_credentials(_basic_creds())
            .with_region(SmnRegion.value_of(region()))
            .build()
        )
        req = PublishMessageRequest(topic_urn=topic_urn)
        req.subject = subject
        req.body = message
        client.publish_message(req)
        logger.info("✅ SMN 消息发布成功 topic=%s", topic_urn)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("❌ SMN 消息发布失败: %s", e)
        return False


def fgs_async_invoke(function_urn, payload):
    """异步调用目标 FunctionGraph 函数（X-Cff-Invoke-Mode: async）。返回 True/False。"""
    if not function_urn:
        logger.error("❌ 未提供目标函数 URN，无法异步调用")
        return False
    try:
        from huaweicloudsdkcore.http.http_config import HttpConfig
        from huaweicloudsdkfunctiongraph.v2 import (
            FunctionGraphClient,
            AsyncInvokeFunctionRequest,
        )
        from huaweicloudsdkfunctiongraph.v2.region.functiongraph_region import FunctionGraphRegion

        client = (
            FunctionGraphClient.new_builder()
            .with_http_config(HttpConfig.get_default_config())
            .with_credentials(_basic_creds())
            .with_region(FunctionGraphRegion.value_of(region()))
            .build()
        )
        req = AsyncInvokeFunctionRequest(function_urn=function_urn, body=payload)
        # v2 异步调用：方法本身访问 /invocations-async 端点，自动以异步模式提交
        client.async_invoke_function(req)
        logger.info("✅ 已异步调用函数: %s", function_urn)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error("❌ 异步调用函数失败 %s: %s", function_urn, e)
        return False


class OBSStore:
    """基于 OBS 的审批请求持久化存储。

    非 VPC 的 FunctionGraph 函数可经 OBS 公网端点读写，无需 VPC/NAT。
    每条审批请求以 JSON 对象存储：<prefix><request_id>.json
    """

    def __init__(self, bucket=None, prefix="cce-approval/"):
        self.bucket = bucket or os.environ.get("OBS_BUCKET")
        self.prefix = prefix or os.environ.get("OBS_KEY_PREFIX", "cce-approval/")

    def _client(self):
        from obs import ObsClient

        ak, sk, token = get_credentials()
        if not ak or not sk:
            raise RuntimeError("未获取到 OBS 访问凭据（agency 或 HW_* 环境变量）。")
        server = f"obs.{region()}.myhuaweicloud.com"
        return ObsClient(
            access_key_id=ak,
            secret_access_key=sk,
            server=server,
            security_token=token or "",
            is_secure=True,
        )

    def _key(self, request_id):
        return f"{self.prefix}{request_id}.json"

    def put(self, request_id, data):
        if not self.bucket:
            logger.warning("⚠️ 未配置 OBS_BUCKET，跳过审批请求持久化（仅日志）。data=%s", request_id)
            return False
        try:
            body = json.dumps(data, ensure_ascii=False)
            client = self._client()
            try:
                resp = client.putContent(self.bucket, self._key(request_id), body)
                ok = resp.get("status") in (200, 201, 204)
            finally:
                client.close()
            if ok:
                logger.info("✅ 审批请求已写入 OBS: %s", request_id)
            else:
                logger.error("❌ OBS 写入失败: %s status=%s", request_id, resp.get("status"))
            return ok
        except Exception as e:  # noqa: BLE001
            logger.error("❌ OBS 写入异常: %s", e)
            return False

    def get(self, request_id):
        if not self.bucket:
            logger.warning("⚠️ 未配置 OBS_BUCKET，无法读取审批请求。")
            return None
        try:
            client = self._client()
            try:
                resp = client.getObject(self.bucket, self._key(request_id))
                if resp.get("status") != 200:
                    logger.warning("OBS 读取未命中 request_id=%s status=%s", request_id, resp.get("status"))
                    return None
                body = resp.get("body", {}) or {}
                # esdk-obs-python 旧版：内容在 body["content"]
                content = body.get("content")
                # esdk-obs-python >=3.23：内容在 body["response"]（HTTPResponse 或其包装，可 read()）
                if content is None:
                    _resp = body.get("response")
                    if _resp is not None:
                        if hasattr(_resp, "read"):
                            content = _resp.read()
                        elif hasattr(_resp, "result") and hasattr(_resp.result, "read"):
                            content = _resp.result.read()
                if content is None:
                    logger.warning("OBS 读取内容为空 request_id=%s", request_id)
                    return None
                if hasattr(content, "read"):
                    content = content.read()
                if isinstance(content, bytes):
                    content = content.decode("utf-8")
                return json.loads(content)
            finally:
                client.close()
        except Exception as e:  # noqa: BLE001
            logger.error("❌ OBS 读取异常: %s", e)
            return None

    def update(self, request_id, **fields):
        data = self.get(request_id) or {}
        data.update(fields)
        return self.put(request_id, data)

"""
获取 CCE 集群 kubeconfig 的辅助模块。

通过华为云 CCE SDK 调用 kubeconfig 接口，返回一段可直接被 kubectl 使用的
kubeconfig 文本（内嵌有效期内的访问令牌）。执行 / 分析函数据此对目标 CCE
集群进行只读探查或修复操作。

凭据来源（按优先级）：
  - 环境变量 HW_ACCESS_KEY / HW_SECRET_KEY / HW_SECURITY_TOKEN / HW_PROJECT_ID / HW_REGION
  - 未配置访问凭据时回退到函数所绑定 IAM 委托（agency）提供的临时凭据（通过元数据获取）

该模块是华为云原生实现。
"""

import json
import logging
import os

from common import get_credentials, get_project_id, region as _common_region

logger = logging.getLogger(__name__)


def _build_credential():
    """构造华为云访问凭据（优先 agency 临时凭据，其次显式 env）。"""
    ak, sk, token = get_credentials()
    pid = get_project_id()
    if not ak or not sk:
        # 未配置访问凭据：交由托管式 IAM 委托凭据处理，调用方需确保函数已绑定具备
        # CCE Admin 权限的 agency；此处返回 None，由上层 SDK 走默认凭据解析。
        return None
    try:
        from huaweicloudsdkcore.auth.credentials import BasicCredentials

        cred = BasicCredentials(ak, sk, pid)
        if token:
            cred = cred.with_security_token(token)
        return cred
    except Exception as e:  # pragma: no cover - 依赖缺失场景
        logger.error("构造华为云凭据失败: %s", e)
        return None


def get_cce_kubeconfig(cluster_id: str, region: str = None, duration_days: int = 1) -> str:
    """
    获取指定 CCE 集群的 kubeconfig 文本（JSON 格式，kubectl 可直接使用）。

    使用「获取集群证书」API（CreateKubernetesClusterCert，SDK 3.1.2xx 可用），
    响应即 kubeconfig 结构（clusters/users/contexts/current-context）。
    注意：SDK 3.1.215 无 ShowKubeConfigRequest，不要回退到该类。

    :param cluster_id: CCE 集群 ID（如 cce-xxxxxxxx）
    :param region: 区域，默认取 HW_REGION
    :param duration_days: 证书有效期（天，1-1827；验证期用 1 天最小值）
    :return: kubeconfig JSON 文本
    """
    region = region or _common_region()

    try:
        from huaweicloudsdkcore.exceptions import exceptions
        from huaweicloudsdkcce.v3 import (
            CceClient,
            CreateKubernetesClusterCertRequest,
            ClusterCertDuration,
        )
        from huaweicloudsdkcore.http.http_config import HttpConfig
    except Exception as e:
        raise RuntimeError(
            "缺少华为云 CCE SDK（huaweicloudsdkcce）。请在镜像中安装 huaweicloudsdkcore / huaweicloudsdkcce。"
        ) from e

    cred = _build_credential()
    client = CceClient.new_builder() \
        .with_http_config(HttpConfig.get_default_config()) \
        .with_credentials(cred) \
        .with_region(_cce_region(region)) \
        .build()

    request = CreateKubernetesClusterCertRequest(cluster_id=cluster_id)
    request.body = ClusterCertDuration(duration=duration_days)
    try:
        response = client.create_kubernetes_cluster_cert(request)
    except exceptions.ClientRequestException as e:  # pragma: no cover
        logger.error("获取 CCE 集群证书失败: %s", e)
        raise

    cfg = response.to_json_object()  # 含 attribute_map 键名（如 current-context）
    if not cfg or not cfg.get("clusters"):
        raise RuntimeError("集群证书接口未返回 kubeconfig clusters 字段")
    return json.dumps(cfg, ensure_ascii=False)


def _cce_region(region: str):
    """映射区域到 CCE 服务端点区域对象。"""
    from huaweicloudsdkcce.v3.region.cce_region import CceRegion
    return CceRegion.value_of(region)


def write_kubeconfig(text: str, path: str = "/tmp/kubeconfig") -> str:
    """将 kubeconfig 文本写入临时文件并返回路径。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path

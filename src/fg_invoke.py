"""
FunctionGraph 异步调用辅助 —— 函数间直接异步触发

审批通过后由审批函数直接异步调用执行函数；同时用于「探测函数 → 分析函数」的异步链路。

实现：优先使用 FunctionGraph SDK（huaweicloudsdkfunctiongraph）异步调用；若环境中已配置
函数调用 URL（FG_INVOKE_URL + 目标函数名），则回退为 HTTPS POST（需自行带鉴权头）。
"""

import json
import logging
import os

from common import _basic_creds, region as _common_region

logger = logging.getLogger(__name__)


def _fg_client():
    from huaweicloudsdkcore.http.http_config import HttpConfig
    from huaweicloudsdkfunctiongraph.v2 import FunctionGraphClient
    from huaweicloudsdkfunctiongraph.v2.region.functiongraph_region import FunctionGraphRegion

    cred = _basic_creds()
    return FunctionGraphClient.new_builder() \
        .with_http_config(HttpConfig.get_default_config()) \
        .with_credentials(cred) \
        .with_region(FunctionGraphRegion.value_of(_common_region())) \
        .build()


def invoke_function_async(function_urn: str, payload: dict) -> bool:
    """
    异步调用目标 FunctionGraph 函数。

    :param function_urn: 目标函数 URN（形如 urn:fss:<region>:<project_id>:function:<name>:<version>）
    :param payload: 传给目标函数的事件体（dict）
    :return: 是否成功提交异步调用
    """
    if not function_urn:
        logger.error("❌ 未提供目标函数 URN，无法异步调用")
        return False

    try:
        from huaweicloudsdkfunctiongraph.v2 import AsyncInvokeFunctionRequest

        client = _fg_client()
        req = AsyncInvokeFunctionRequest(function_urn=function_urn, body=payload)
        # v2 异步调用：方法本身访问 /invocations-async 端点，自动以异步模式提交
        client.async_invoke_function(req)
        logger.info(f"✅ 已异步调用函数: {function_urn}")
        return True
    except Exception as e:
        logger.error(f"❌ 异步调用函数失败 {function_urn}: {str(e)}")
        return False

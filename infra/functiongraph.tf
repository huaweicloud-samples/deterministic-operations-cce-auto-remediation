# 四个 FunctionGraph 函数（自定义容器镜像，v1.97.1 资源名 huaweicloud_fgs_function）
# 说明：
# - 探测函数由 CES 告警 → SMN 主题触发
# - 分析函数由探测函数异步调用（ANALYSIS_FUNCTION_URN）
# - 审批函数由 APIG（或函数内置 HTTP 触发器）触发，批准后再异步调用执行函数（EXECUTION_FUNCTION_URN）
# - 执行函数拿到已批准命令后，经 CCE kubeconfig 执行 kubectl 修复
#
# 凭据策略：函数均绑定 IAM 委托（agency），不将访问凭据明文写入函数环境变量。
# 环境变量通过 user_data（JSON 字符串）注入。
# 日志：绑定 LTS 日志组/流。

locals {
  swr_registry = "swr.${var.region}.myhuaweicloud.com"
  # 单函数镜像 tag 覆盖（验证期只重建某几个函数时用，避免牵动其他函数）
  image_tag_overrides = {
    analysis  = coalesce(var.analysis_image_tag, var.image_tag)
    execution = coalesce(var.execution_image_tag, var.image_tag)
  }
  image_for = {
    for k in local.function_names :
    k => "${local.swr_registry}/${huaweicloud_swr_organization.this.name}/${var.project_name}-${k}:${lookup(local.image_tag_overrides, k, var.image_tag)}"
  }
}

# ---------- 探测函数 ----------
resource "huaweicloud_fgs_function" "detection" {
  name        = "${var.project_name}-detection"
  app         = "mcp_cce_auto_remediation"
  description = "CCE auto-remediation: alarm detection (v2 SDK, rebuild 20260929-2)"
  runtime     = "Custom Image"
  handler     = "-"
  memory_size = 512
  timeout     = 60
  agency      = huaweicloud_identity_agency.fg.name
  user_data = jsonencode(merge(local.function_env, {
    ANALYSIS_FUNCTION_URN = huaweicloud_fgs_function.analysis.urn
    CLUSTER_ID            = local.cluster_id
  }))
  # 不显式绑定 LTS 流：改用 FunctionGraph 默认日志组，规避 FSS.1212（手建 LTS 流企业项目不一致导致函数创建报"流不存在"）

  custom_image {
    url         = local.image_for["detection"]
    command     = "python"
    args        = "/app/src/runtime_shim.py"
    working_dir = "/"
  }
}

# 探测函数触发器：SMN 告警主题 → 触发本函数
resource "huaweicloud_fgs_function_trigger" "detection_smn" {
  function_urn = huaweicloud_fgs_function.detection.urn
  type         = "SMN"
  event_data   = jsonencode({ topic_urn = huaweicloud_smn_topic.alarm.id })
}

# ---------- 分析函数 ----------
resource "huaweicloud_fgs_function" "analysis" {
  name        = "${var.project_name}-analysis"
  app         = "mcp_cce_auto_remediation"
  description = "CCE auto-remediation: LLM analysis (rebuild4: NameError fix + MCP non-fatal + OBS get() SDK-compat)"
  runtime     = "Custom Image"
  handler     = "-"
  memory_size = 1024
  timeout     = 600 # LLM 多轮 + MCP 工具循环需 >300s（20260929 实测 300s 不够）
  agency      = huaweicloud_identity_agency.fg.name
  user_data = jsonencode(merge(local.function_env, {
    APPROVAL_TOPIC_URN = huaweicloud_smn_topic.alert.id
    APIG_APPROVAL_URL  = var.apig_approval_url != "" ? var.apig_approval_url : (var.apig_enabled ? "https://${huaweicloud_apig_api.approval[0].id}.apigw.${var.region}.myhuaweicloud.com/approval" : "https://<fg-http-trigger-host>/approval")
  }))
  # 不显式绑定 LTS 流：改用 FunctionGraph 默认日志组，规避 FSS.1212

  custom_image {
    # rebuild4：含 analysis_service NameError 修复 + MCP 连接非致命化 + common.OBSStore.get() 新 SDK 兼容
    url         = local.image_for["analysis"]
    command     = "python"
    args        = "/app/src/runtime_shim.py"
    working_dir = "/"
  }
}

# ---------- 审批函数 ----------
resource "huaweicloud_fgs_function" "approval" {
  name        = "${var.project_name}-approval"
  app         = "mcp_cce_auto_remediation"
  description = "CCE auto-remediation: approval portal (v2 SDK)"
  runtime     = "Custom Image"
  handler     = "-"
  memory_size = 512
  timeout     = 60
  agency      = huaweicloud_identity_agency.fg.name
  user_data = jsonencode(merge(local.function_env, {
    EXECUTION_FUNCTION_URN = huaweicloud_fgs_function.execution.urn
  }))
  # 不显式绑定 LTS 流：改用 FunctionGraph 默认日志组，规避 FSS.1212

  custom_image {
    url         = local.image_for["approval"]
    command     = "python"
    args        = "/app/src/runtime_shim.py"
    working_dir = "/"
  }
}

# 审批函数 HTTP 入口：
# 自定义容器镜像函数（runtime="Custom Image"）既不支持内置 "HTTP" 触发器（FSS.0400），
# 也不支持 "APIG" 类型触发器（FSS.0400: Trigger type 'APIG' not usable）。
# 专用 APIG 实例在本账号/区域创建持续报 APIC.9999 内部错误。
# 故审批端点由用户在控制台手动创建（APIG 专享实例 → 指向 approval 函数），不在 terraform 内管理。
resource "huaweicloud_fgs_function_trigger" "approval_http" {
  count        = 0
  function_urn = huaweicloud_fgs_function.approval.urn
  type         = "HTTP"
  event_data   = jsonencode({ auth_type = "NONE" })
}

# ---------- 执行函数 ----------
resource "huaweicloud_fgs_function" "execution" {
  name        = "${var.project_name}-execution"
  app         = "mcp_cce_auto_remediation"
  description = "CCE auto-remediation: kubectl execution (v2 SDK)"
  runtime     = "Custom Image"
  handler     = "-"
  memory_size = 1024
  timeout     = 300
  agency      = huaweicloud_identity_agency.fg.name
  # execution 需在 VPC 内访问 CCE 集群 API（集群仅有内网端点 10.0.1.185:5443）；
  # OBS/SMN/LTS 在 VPC 内走内网解析可达；本函数不调 MaaS，无需公网出口
  vpc_id     = huaweicloud_vpc.this.id
  network_id = huaweicloud_vpc_subnet.this.id
  user_data = jsonencode(merge(local.function_env, {
    CLUSTER_ID = local.cluster_id
  }))
  # 不显式绑定 LTS 流：改用 FunctionGraph 默认日志组，规避 FSS.1212

  custom_image {
    url         = local.image_for["execution"]
    command     = "python"
    args        = "/app/src/runtime_shim.py"
    working_dir = "/"
  }
}

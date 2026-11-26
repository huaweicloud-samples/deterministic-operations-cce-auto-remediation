variable "region" {
  description = "华为云区域（如 cn-north-4）"
  type        = string
  default     = "cn-north-4"
}

variable "project_name" {
  description = "项目名称，用于资源命名"
  type        = string
  default     = "deterministic-operations-cce-auto-remediation"
}

variable "environment" {
  description = "环境标识（dev/test/prod）"
  type        = string
  default     = "dev"
}

# ---------- 网络 ----------
variable "vpc_cidr" {
  description = "VPC CIDR"
  type        = string
  default     = "10.0.0.0/16"
}

variable "subnet_cidr" {
  description = "子网 CIDR"
  type        = string
  default     = "10.0.1.0/24"
}

variable "subnet_az" {
  description = "子网所在可用区"
  type        = string
  default     = "cn-north-4a"
}

# ---------- CCE 集群（可选创建）----------
variable "create_cce_cluster" {
  description = "为 true 时新建 CCE 集群作为演示目标；为 false 时复用已有集群（cce_cluster_id）"
  type        = bool
  default     = false
}

variable "cce_cluster_id" {
  description = "已有 CCE 集群 ID（create_cce_cluster 为 false 时使用）"
  type        = string
  default     = ""
}

variable "cce_cluster_flavor" {
  description = "CCE 集群规格（集群版本对应的 flavor id）"
  type        = string
  default     = "cce.s1.small"
}

variable "cce_node_flavor" {
  description = "CCE 节点池的节点规格类型"
  type        = string
  default     = "s6.large.2"
}

variable "cce_node_count" {
  description = "节点池初始节点数"
  type        = number
  default     = 1
}

variable "cce_node_password" {
  description = "CCE 节点登录密码（敏感；建议通过 CI / KMS 注入，不要明文提交）"
  type        = string
  default     = ""
  sensitive   = true
}

# ---------- 函数与镜像 ----------
variable "swr_organization" {
  description = "SWR 组织名（用于存放 4 个函数镜像）"
  type        = string
  default     = "mcp-cce"
}

variable "image_tag" {
  description = "函数镜像 tag"
  type        = string
  default     = "latest"
}

variable "approver_email" {
  description = "审批人邮箱，用于接收 SMN 审批通知"
  type        = string
  default     = "ops@example.com"
}

# ---------- 模型（MaaS）----------
variable "maas_base_url" {
  description = "MaaS OpenAI 兼容端点（含 /v1 路径）"
  type        = string
  default     = ""
}

variable "maas_api_key" {
  description = "MaaS API Key（建议通过敏感变量 / KMS 注入，不要明文落盘）"
  type        = string
  default     = ""
  sensitive   = true
}

variable "maas_model_id" {
  description = "MaaS 部署模型名（默认 deepseek-v4-flash）"
  type        = string
  default     = "deepseek-v4-flash"
}

# ---------- APIG 审批端点（可选）----------
variable "apig_enabled" {
  description = "为 true 时创建 APIG 实例并暴露审批端点；为 false 时仅使用函数内置 HTTP 触发器"
  type        = bool
  default     = true
}

variable "apig_approval_url" {
  description = "手动创建的 APIG 专享实例审批 URL。本账号/区域经 terraform 创建 APIG 实例持续报 APIC.9999，故审批端点改由控制台手动建，并将真实 URL 回填此变量（优先于 apig_enabled 推导值）"
  type        = string
  default     = ""
}

# ---------- 审批请求持久化（OBS）----------
variable "obs_bucket_name" {
  description = "审批请求存储桶名（OBS 桶名全局唯一，需符合 DNS 命名：小写字母/数字/-，3-63 字符）"
  type        = string
  default     = "mcp-cce-auto-remediation-approval"
}

variable "obs_key_prefix" {
  description = "审批请求对象键前缀"
  type        = string
  default     = "cce-approval/"
}

# ---------- 函数委托临时凭据配置 ----------
# 说明：本栈设计上函数凭据走 agency 委托（HUAWEICLOUD_AGENCY_* 临时凭据），
# 不将永久 AK/SK 写入源码或仓库。自定义镜像函数在 agency 临时凭据未正确注入时，
# 业务代码 common.get_credentials() 会回退读取 HW_ACCESS_KEY/HW_SECRET_KEY。
# 此二变量默认空；仅在业务验证期临时经 user_data（函数环境变量）注入，验证通过后置空并 apply 一次移除。
variable "hw_access_key" {
  description = "验证期临时注入的函数访问 AK（仅经 user_data 注入，不落源码；验证后移除）"
  type        = string
  default     = ""
  sensitive   = true
}

variable "hw_secret_key" {
  description = "验证期临时注入的函数访问 SK（仅经 user_data 注入，不落源码；验证后移除）"
  type        = string
  default     = ""
  sensitive   = true
}

locals {
  # 实际使用的 CCE 集群 ID：新建或复用已有
  cluster_id = var.create_cce_cluster ? huaweicloud_cce_cluster.demo[0].id : var.cce_cluster_id

  common_tags = {
    Project     = var.project_name
    Environment = var.environment
    ManagedBy   = "terraform"
  }

  # 函数通用环境变量（凭据走 agency，不在此明文注入 AK/SK）
  function_env = merge(
    {
      HW_REGION      = var.region
      MAAS_BASE_URL  = var.maas_base_url
      MAAS_API_KEY   = var.maas_api_key
      MAAS_MODEL_ID  = var.maas_model_id
      MODEL_ID       = "openpangu-2.0-flash" # MaaS 模型直通（deepseek-v4-flash 被限流后切换；openpangu 独立配额）
      OBS_BUCKET     = var.obs_bucket_name
      OBS_KEY_PREFIX = var.obs_key_prefix
    },
    var.hw_access_key != "" ? { HW_ACCESS_KEY = var.hw_access_key } : {},
    var.hw_secret_key != "" ? { HW_SECRET_KEY = var.hw_secret_key } : {},
  )
}

# 资源说明：本栈涉及 CCE 节点、OBS 存储桶、APIG 实例、4 个函数。
# 部署前请先评估资源用量与规格。

variable "analysis_image_tag" {
  description = "analysis 函数专用镜像 tag 覆盖（空则回落到 var.image_tag）"
  type        = string
  default     = ""
}

variable "execution_image_tag" {
  description = "execution 函数专用镜像 tag 覆盖（空则回落到 var.image_tag）"
  type        = string
  default     = ""
}

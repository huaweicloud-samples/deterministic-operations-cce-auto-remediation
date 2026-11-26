# KMS 密钥 —— 用于加密敏感配置（如 MaaS API Key、审批 Webhook 密钥）
# 用法：将敏感值用该密钥加密后注入函数环境变量；此处仅声明密钥，密文注入在 CI 中完成。
# 注意：重部署时旧密钥处于 7 天「待删除」保留期会占用同名 alias，故此处 alias 带 -kms 后缀避免冲突。
resource "huaweicloud_kms_key" "this" {
  # 日期后缀避免与重部署时处于 7 天「待删除」保留期的同名 alias 冲突
  key_alias    = "${var.project_name}-kms-20260929"
  pending_days = 7
  tags         = local.common_tags
}

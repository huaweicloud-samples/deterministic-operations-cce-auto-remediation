# SMN 主题 —— 承担两个职责：
# 1) CES 告警经 SMN 触发「探测函数」
# 2) 分析完成后向审批人发送审批通知邮件
resource "huaweicloud_smn_topic" "alert" {
  name = "${var.project_name}-alert"
  tags = local.common_tags
}

# 告警流专用主题（CES 告警 → 探测函数）
resource "huaweicloud_smn_topic" "alarm" {
  name = "${var.project_name}-alarm"
  tags = local.common_tags
}

# 审批人邮箱订阅（用于接收审批通知）
resource "huaweicloud_smn_subscription" "approver" {
  topic_urn = huaweicloud_smn_topic.alert.id
  protocol  = "email"
  endpoint  = var.approver_email
}

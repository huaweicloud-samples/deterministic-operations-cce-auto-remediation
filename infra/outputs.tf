output "cce_cluster_id" {
  description = "实际使用的 CCE 集群 ID（新建或复用）"
  value       = local.cluster_id
}

output "detection_function_urn" {
  description = "探测函数 URN"
  value       = huaweicloud_fgs_function.detection.urn
}

output "analysis_function_urn" {
  description = "分析函数 URN"
  value       = huaweicloud_fgs_function.analysis.urn
}

output "approval_function_urn" {
  description = "审批函数 URN"
  value       = huaweicloud_fgs_function.approval.urn
}

output "execution_function_urn" {
  description = "执行函数 URN"
  value       = huaweicloud_fgs_function.execution.urn
}

output "alarm_topic_urn" {
  description = "告警 SMN 主题 URN（CES 告警 action 指向此处）"
  value       = huaweicloud_smn_topic.alarm.id
}

output "approval_topic_urn" {
  description = "审批通知 SMN 主题 URN"
  value       = huaweicloud_smn_topic.alert.id
}

output "apig_approval_url" {
  description = "审批端点 URL（写入分析函数环境变量，供通知邮件使用）"
  value       = var.apig_approval_url != "" ? var.apig_approval_url : (var.apig_enabled ? "https://${huaweicloud_apig_api.approval[0].id}.apigw.${var.region}.myhuaweicloud.com/approval" : "https://<fg-http-trigger-host>/approval")
}

output "obs_bucket_name" {
  description = "审批请求存储桶名（OBS）"
  value       = huaweicloud_obs_bucket.approval.bucket
}

# CES 告警规则 —— 触发告警后通过 SMN 主题（alarm 主题）触发「探测函数」
# 此为示例告警：CCE 集群节点（ECS）CPU 利用率超过阈值。实际指标/阈值请按业务调整。
# 示例维度使用占位 instance_id，部署后请改为真实 CCE 节点 ID，或自建针对集群的告警。
#
# CES 告警 action 指向 SMN 主题 huaweicloud_smn_topic.alarm，
# 该主题已绑定探测函数的 SMN 触发器。
resource "huaweicloud_ces_alarmrule" "demo" {
  alarm_name           = "${var.project_name}-node-cpu-high"
  alarm_action_enabled = true

  metric {
    namespace   = "SYS.ECS"
    metric_name = "cpu_util"
    dimensions {
      name  = "instance_id"
      value = "00000000-0000-0000-0000-000000000000"
    }
  }

  condition {
    metric_name         = "cpu_util"
    comparison_operator = ">="
    value               = 80
    count               = 3
    period              = 300
    filter              = "average"
    unit                = "%"
  }

  alarm_actions {
    type              = "notification"
    notification_list = [huaweicloud_smn_topic.alarm.id]
  }
}

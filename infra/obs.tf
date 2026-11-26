# 安全组（供 APIG 实例使用）
resource "huaweicloud_networking_secgroup" "data" {
  name        = "${var.project_name}-data-sg"
  description = "安全组：用于 APIG 实例"
  tags        = local.common_tags
}

# APIG 专用实例需要安全组允许入向流量才能初始化/对外提供服务
resource "huaweicloud_networking_secgroup_rule" "data_ingress_all" {
  security_group_id = huaweicloud_networking_secgroup.data.id
  direction         = "ingress"
  ethertype         = "IPv4"
  protocol          = "tcp"
  port_range_min    = 1
  port_range_max    = 65535
  remote_ip_prefix  = "0.0.0.0/0"
  description       = "APIG ingress allow"
}

# 审批请求持久化存储
# 非 VPC 的 FunctionGraph 函数经 OBS 公网端点即可读写，无需为函数配置 VPC / NAT。
# 每条审批请求以 JSON 对象存储：<OBS_KEY_PREFIX><request_id>.json
resource "huaweicloud_obs_bucket" "approval" {
  bucket = var.obs_bucket_name
  tags   = local.common_tags
}

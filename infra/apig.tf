# APIG 审批端点（APIG 实例 + 分组 + API，后端指向审批函数）
# 仅当 var.apig_enabled = true 时创建。否则审批函数使用内置 HTTP 触发器。
resource "huaweicloud_apig_instance" "this" {
  count             = var.apig_enabled ? 1 : 0
  name              = "${var.project_name}-apig"
  edition           = "BASIC"
  vpc_id            = huaweicloud_vpc.this.id
  subnet_id         = huaweicloud_vpc_subnet.this.id
  security_group_id = huaweicloud_networking_secgroup.data.id
  tags              = local.common_tags
}

resource "huaweicloud_apig_group" "this" {
  count       = var.apig_enabled ? 1 : 0
  name        = "${var.project_name}-group"
  instance_id = huaweicloud_apig_instance.this[0].id
}

resource "huaweicloud_apig_api" "approval" {
  count       = var.apig_enabled ? 1 : 0
  name        = "approval"
  instance_id = huaweicloud_apig_instance.this[0].id
  group_id    = huaweicloud_apig_group.this[0].id
  type        = "Public"
  # ANY：邮件审批链接使用 GET（渲染审批页面），页面内 Approve/Reject 按钮使用 POST，两者共用同一端点
  request_method   = "ANY"
  request_path     = "/approval"
  request_protocol = "HTTPS"

  func_graph {
    function_urn = huaweicloud_fgs_function.approval.urn
  }
}

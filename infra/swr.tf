# SWR 镜像仓库 —— 四个函数各自的容器镜像仓库
resource "huaweicloud_swr_organization" "this" {
  name = var.swr_organization
}

locals {
  function_names = ["detection", "analysis", "approval", "execution"]
}

resource "huaweicloud_swr_repository" "functions" {
  for_each     = toset(local.function_names)
  organization = huaweicloud_swr_organization.this.name
  name         = "${var.project_name}-${each.key}"
  is_public    = false
}

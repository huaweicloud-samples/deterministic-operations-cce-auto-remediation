# VPC 与子网（基础网络，始终创建；APIG 与可选 CCE 集群共用）
resource "huaweicloud_vpc" "this" {
  name = "${var.project_name}-vpc"
  cidr = var.vpc_cidr
  tags = local.common_tags
}

resource "huaweicloud_vpc_subnet" "this" {
  name              = "${var.project_name}-subnet"
  cidr              = var.subnet_cidr
  gateway_ip        = cidrhost(var.subnet_cidr, 1)
  vpc_id            = huaweicloud_vpc.this.id
  availability_zone = var.subnet_az
  tags              = local.common_tags
}

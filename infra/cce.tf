# CCE 集群（可选创建，作为自动修复的演示目标）
resource "huaweicloud_cce_cluster" "demo" {
  count                  = var.create_cce_cluster ? 1 : 0
  name                   = "${var.project_name}-cluster"
  cluster_type           = "VirtualMachine"
  flavor_id              = var.cce_cluster_flavor
  vpc_id                 = huaweicloud_vpc.this.id
  subnet_id              = huaweicloud_vpc_subnet.this.id
  container_network_type = "vpc-router"
  tags                   = local.common_tags
}

resource "huaweicloud_cce_node_pool" "demo" {
  count              = var.create_cce_cluster ? 1 : 0
  cluster_id         = huaweicloud_cce_cluster.demo[0].id
  name               = "${var.project_name}-nodepool"
  os                 = "Huawei Cloud EulerOS 2.0"
  flavor_id          = var.cce_node_flavor
  initial_node_count = var.cce_node_count
  availability_zone  = var.subnet_az
  password           = var.cce_node_password
  root_volume {
    size       = 40
    volumetype = "SAS"
  }
  data_volumes {
    size       = 100
    volumetype = "SAS"
  }
  tags = local.common_tags
}

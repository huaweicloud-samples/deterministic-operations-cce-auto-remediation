# IAM 委托（agency）—— 函数运行时以该委托身份访问 CCE / SMN / 函数 / KMS / OBS / LTS
# 避免将访问凭据明文写入函数环境变量。
resource "huaweicloud_identity_agency" "fg" {
  name        = "${var.project_name}-fg-agency"
  description = "deterministic-operations-cce-auto-remediation 函数执行委托"
  # FunctionGraph 的可委托服务域名为 op_svc_cff（华为云 IAM 委托「云服务」类型下拉项）
  delegated_service_name = "op_svc_cff"

  project_role {
    project = var.region
    roles = [
      "CCE Administrator",
      "SMN FullAccess",
      "FunctionGraph FullAccess",
      "KMS Administrator",
      "OBS OperateAccess",
      "LTS FullAccess",
      # 自定义镜像函数首次执行需由 FunctionGraph 拉取私有 SWR 镜像，
      # 拉镜像用的就是本委托，必须带 SWR Admin（华为云官方硬性要求）。
      "SWR Admin",
    ]
  }
}

# LTS 日志组 / 日志流 —— 函数运行日志归集
resource "huaweicloud_lts_group" "fg" {
  group_name  = "${var.project_name}-fg-log"
  ttl_in_days = 7
}

resource "huaweicloud_lts_stream" "functions" {
  for_each    = toset(local.function_names)
  group_id    = huaweicloud_lts_group.fg.id
  stream_name = "${var.project_name}-${each.key}"
}

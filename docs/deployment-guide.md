# 部署指导

本指导说明如何将 `deterministic-operations-cce-auto-remediation` 部署到华为云。全流程基于 Terraform + 自定义镜像，分「构建镜像 → 部署基础设施 → 串联异步链路」三步。

> 部署前请确保已满足 README [第 6 节「前置条件」](README.md#6-前置条件) 的账号、权限、工具与凭据要求。

## 1. 准备访问凭据

在「我的凭证」页面获取 Access Key 与 Secret Access Key，配置到环境变量：

```bash
export HW_ACCESS_KEY="<your-access-key>"
export HW_SECRET_KEY="<your-secret-key>"
```

请勿将凭据写入代码或配置文件。

## 2. 构建并推送函数镜像到 SWR

四个函数各自对应 `docker/<name>/Dockerfile`（`<name>` ∈ detection / analysis / approval / execution）。以 detection 为例：

```bash
cd deterministic-operations-cce-auto-remediation

# 登录 SWR（地址见控制台，格式如 swr.cn-north-4.myhuaweicloud.com）
docker build -f docker/detection/Dockerfile -t swr.cn-north-4.myhuaweicloud.com/<swr_org>/mcp-cce-detection:latest .
docker push swr.cn-north-4.myhuaweicloud.com/<swr_org>/mcp-cce-detection:latest
```

重复上述步骤构建并推送 analysis / approval / execution 三个镜像，将 `<swr_org>` 与镜像名替换为对应值。

**预期结果**：四个镜像均推送成功，控制台 SWR 仓库可见对应镜像与版本。

## 3. 初始化 Terraform

```bash
cd deterministic-operations-cce-auto-remediation/infra
terraform init
```

**预期输出**：`Terraform has been successfully initialized!`

## 4. 填写变量

复制示例变量文件并按实际环境修改：

```bash
cp terraform.tfvars.example terraform.tfvars
```

关键变量：

| 变量 | 说明 |
|---|---|
| `region` | 区域，如 `cn-north-4` |
| `agency_name` | 函数绑定的 IAM 委托名称 |
| `swr_org` | SWR 组织名 |
| `swr_image_detection` / `analysis` / `approval` / `execution` | 四个镜像地址 |
| `maas_base_url` / `maas_api_key` / `maas_model_id` | MaaS 端点与模型 |
| `apig_enabled` | 是否部署 APIG 审批端点（默认 true） |
| `create_cce_cluster` | 是否由本栈创建 CCE 集群（否则填写已有 `cce_cluster_id`） |

## 5. 预演与部署

```bash
terraform plan
terraform apply -auto-approve
```

**预期输出**：`Apply complete! Resources: N added, 0 changed, 0 destroyed.`

## 6. 串联异步调用链路

`terraform output` 会列出四个函数 URN、SMN 主题 URN、APIG 审批 URL、OBS 存储桶名。将以下 URN 回填到对应函数的环境变量，并再次 `apply` 以使异步调用链生效：

| 回填目标 | 变量 | 取值来源 |
|---|---|---|
| 探测函数 | `ANALYSIS_FUNCTION_URN` | 分析函数 URN |
| 审批函数 | `EXECUTION_FUNCTION_URN` | 执行函数 URN |
| 分析 / 审批函数 | `APPROVAL_TOPIC_URN` | SMN 主题 URN |
| 通知处理器 | `APIG_APPROVAL_URL` | APIG 审批 URL |
| 分析 / 审批函数 | `OBS_BUCKET` / `OBS_KEY_PREFIX` | OBS 存储桶名与键前缀（默认 `cce-approval/`） |
| 执行函数 | `CLUSTER_ID` | 目标 CCE 集群 ID |

回填后在 `infra/` 重新执行：

```bash
terraform apply -auto-approve
```

## 7. 验证

部署完成后按 README [第 9 节「使用方法 / 验证」](README.md#9-使用方法--验证) 执行：

1. 本地冒烟测试确认函数入口与共享模块可加载；
2. 在 CES 创建测试告警，确认 SMN 收到待审批邮件；
3. 通过 APIG 审批链接打开审批页面，点击「批准并执行」，观察执行函数按序执行 kubectl 命令并回写 OBS 审批状态。

## 8. 资源释放

```bash
cd deterministic-operations-cce-auto-remediation/infra
terraform destroy -auto-approve
```

> 控制台手动创建的测试告警规则、SWR 镜像需另行手动释放，避免残留。

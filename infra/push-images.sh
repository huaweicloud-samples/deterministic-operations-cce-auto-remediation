#!/usr/bin/env bash
# 推送 deterministic-operations-cce-auto-remediation 四个函数镜像到 SWR（cn-north-4 / 组织 mcp-cce）
# 前置：先在本机执行从 SWR 控制台复制的登录指令
#   docker login -u <xxx> -p <临时密码> swr.cn-north-4.myhuaweicloud.com
set -euo pipefail

# 禁用 BuildKit 默认 attestation（SBOM/来源清单），避免 SWR 注册表报
# "Invalid image, fail to parse 'manifest.json'"（SWR 不兼容 attestation manifest）。
export BUILDX_NO_DEFAULT_ATTESTATIONS=1

REGION=cn-north-4
ORG=mcp-cce
PROJECT=deterministic-operations-cce-auto-remediation
REGISTRY="swr.${REGION}.myhuaweicloud.com"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

NAMES=(detection analysis approval execution)

# 构建已改为走国内镜像源（DaoCloud 基础镜像 + 清华 pip 镜像），无需宿主代理；
# 若你本地网络仍需代理，可 export BUILD_PROXY=http://host.docker.internal:27056 再运行本脚本。
BUILD_PROXY="${BUILD_PROXY:-}"

for name in "${NAMES[@]}"; do
  IMG="${REGISTRY}/${ORG}/${PROJECT}-${name}:latest"
  echo "==> build ${name} -> ${IMG}"
  docker build -f "docker/${name}/Dockerfile" \
    --platform linux/amd64 \
    --provenance=false \
    --sbom=false \
    --build-arg "HTTP_PROXY=${BUILD_PROXY}" \
    --build-arg "HTTPS_PROXY=${BUILD_PROXY}" \
    -t "$IMG" .
  echo "==> push ${IMG}"
  docker push "$IMG"
done

echo "✅ 四个镜像已全部推送: ${REGISTRY}/${ORG}/${PROJECT}-{detection,analysis,approval,execution}:latest"

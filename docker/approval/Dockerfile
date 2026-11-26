# 审批函数镜像：APIG 触发，GET 渲染审批页 / POST 执行审批并触发执行函数
FROM docker.m.daocloud.io/library/python:3.10-slim

ARG HTTP_PROXY
ARG HTTPS_PROXY

ENV PYTHONUNBUFFERED=1 \
    APP_ROOT=/app/src \
    PORT=8000 \
    FUNC_HANDLER=functions.approval_function.handler

WORKDIR /app/src

# 安装 kubectl（只读 MCP 服务、执行函数依赖）
# 说明：基础镜像走 DaoCloud 国内镜像源，pip 走清华镜像，kubectl 直连
# storage.googleapis.com（沙箱直连可达）；均不依赖宿主代理。
RUN HTTPS_PROXY=$HTTPS_PROXY python - <<'PY'
import os, urllib.request
proxy = os.environ.get('HTTPS_PROXY') or os.environ.get('HTTP_PROXY')
handlers = []
if proxy:
    from urllib.request import ProxyHandler
    handlers.append(ProxyHandler({'https': proxy, 'http': proxy}))
opener = urllib.request.build_opener(*handlers)
url = "https://storage.googleapis.com/kubernetes-release/release/v1.29.0/bin/linux/amd64/kubectl"
with opener.open(url, timeout=120) as r, open('/usr/local/bin/kubectl', 'wb') as f:
    f.write(r.read())
os.chmod('/usr/local/bin/kubectl', 0o755)
print("kubectl installed")
PY

COPY src/requirements.txt /app/src/requirements.txt
RUN pip install --no-cache-dir -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn -r requirements.txt

COPY src/ /app/src/

EXPOSE 8000
CMD ["python", "runtime_shim.py"]

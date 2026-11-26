"""同步调用 FunctionGraph 函数（确定性诊断 / 验证用）。

用法:
  python invoke_function.py <function> [sync|async]

<function> 取值: detection | analysis | approval | execution
默认 sync（同步，直接拿返回值）；async 走 X-Fgs-Async-Invoke 头。

该脚本用与 hw_api.py 相同的 HMAC-SHA256 签名，直接打 FunctionGraph REST API，
绕过函数间 SDK 异步链路，用于确认目标函数能否真正启动并执行。
"""
import hashlib, hmac, datetime, json, urllib.request, ssl, urllib.parse, sys, time, os

# AK/SK 由部署者在本地运行环境配置，代码不保存明文凭据；缺失则直接报错退出。
AK = os.environ.get("HW_ACCESS_KEY")
SK = os.environ.get("HW_SECRET_KEY")
if not AK or not SK:
    raise RuntimeError("HW_ACCESS_KEY / HW_SECRET_KEY environment variables must be set (do not hardcode credentials).")
REGION = "cn-north-4"
PROJECT_ID = "cc2cb3d01b0c46798d77c4cd8ecf6b7e"

URN = {
    "detection": "urn:fss:%s:%s:function:mcp_cce_auto_remediation:mcp-cce-auto-remediation-detection:latest" % (REGION, PROJECT_ID),
    "analysis":  "urn:fss:%s:%s:function:mcp_cce_auto_remediation:mcp-cce-auto-remediation-analysis:latest" % (REGION, PROJECT_ID),
    "approval":  "urn:fss:%s:%s:function:mcp_cce_auto_remediation:mcp-cce-auto-remediation-approval:latest" % (REGION, PROJECT_ID),
    "execution": "urn:fss:%s:%s:function:mcp_cce_auto_remediation:mcp-cce-auto-remediation-execution:latest" % (REGION, PROJECT_ID),
}

def sha256_hex(b):
    return hashlib.sha256(b).hexdigest()

def sign(method, host, path, query, body_bytes, extra_headers):
    t = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    headers = {'host': host, 'x-sdk-date': t}
    headers.update(extra_headers)
    skeys = sorted(headers.keys())
    canonical_headers = ''.join('%s:%s\n' % (k.lower(), str(headers[k]).strip()) for k in skeys)
    signed_headers = ';'.join(k.lower() for k in skeys)
    payload_hash = sha256_hex(body_bytes)
    canonical_path = path if path.endswith('/') else path + '/'
    canonical_request = method + '\n' + canonical_path + '\n' + query + '\n' + canonical_headers + '\n' + signed_headers + '\n' + payload_hash
    string_to_sign = 'SDK-HMAC-SHA256\n' + t + '\n' + sha256_hex(canonical_request.encode('utf-8'))
    signature = hmac.new(SK.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    headers['Authorization'] = 'SDK-HMAC-SHA256 Access=%s, SignedHeaders=%s, Signature=%s' % (AK, signed_headers, signature)
    return headers

def call(method, host, path, query='', body=b'', extra_headers=None, timeout=350):
    extra = dict(extra_headers or {})
    extra['Content-Type'] = 'application/json'
    headers = sign(method, host, path, query, body, extra)
    url = 'https://' + host + path + ('?' + query if query else '')
    req = urllib.request.Request(url, data=body if body else None, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=timeout) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')

def main():
    fn = sys.argv[1] if len(sys.argv) > 1 else "analysis"
    mode = sys.argv[2] if len(sys.argv) > 2 else "sync"
    urn = URN[fn]

    # 模拟 detection 发给 analysis 的 incident_data（detection handler 直接把 dict 作为 event 传入）
    if fn == "analysis":
        payload = {
            "alarm_name": "PodCrashLoopBackOff",
            "alarm_reason": "container restart loop detected",
            "cluster_name": "0dff45d1-bba6-11f1-b4fb-0255ac100268",
            "timestamp": "2026-09-29T10:00:00Z",
            "incident_id": "alarm-PodCrashLoopBackOff-2026-09-29T10:00:00Z",
            "incident_type": "ces-alarm",
            "cluster_info": {},
            "raw_event": {}
        }
    else:
        payload = {"diagnostic": True, "ts": datetime.datetime.utcnow().isoformat()}

    body = json.dumps(payload).encode('utf-8')
    # 路径中的 URN 必须 URL 编码（冒号 -> %3A），否则服务端归一化后签名校验失败（APIGW.0301）
    u = urllib.parse.quote(urn, safe='')
    path = "/v2/%s/fgs/functions/%s/invocations" % (PROJECT_ID, u)
    extra = {}
    if mode == "async":
        extra['X-Fgs-Async-Invoke'] = 'true'
    print("== mode=%s ==" % mode)
    print("POST %s" % path)
    t0 = time.time()
    st, resp = call('POST', 'functiongraph.%s.myhuaweicloud.com' % REGION, path, body=body, extra_headers=extra)
    dt = time.time() - t0
    print("status=%s  elapsed=%.1fs" % (st, dt))
    print("resp=%s" % resp[:4000])
    return

if __name__ == '__main__':
    main()

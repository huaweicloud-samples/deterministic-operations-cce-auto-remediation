import hashlib, hmac, datetime, json, urllib.request, ssl, urllib.parse, time, sys, os

# AK/SK 由部署者在本地运行环境配置，代码不保存明文凭据；缺失则直接报错退出。
AK = os.environ.get("HW_ACCESS_KEY")
SK = os.environ.get("HW_SECRET_KEY")
if not AK or not SK:
    raise RuntimeError("HW_ACCESS_KEY / HW_SECRET_KEY environment variables must be set (do not hardcode credentials).")
REGION = "cn-north-4"
PROJECT_ID = "cc2cb3d01b0c46798d77c4cd8ecf6b7e"

def sha256_hex(b):
    return hashlib.sha256(b).hexdigest()

def sign(ak, sk, method, host, path, query, body_bytes, extra_headers):
    t = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    headers = {'host': host, 'x-sdk-date': t}
    headers.update(extra_headers)
    skeys = sorted(headers.keys())
    canonical_headers = ''.join('%s:%s\n' % (k.lower(), str(headers[k]).strip()) for k in skeys)
    signed_headers = ';'.join(k.lower() for k in skeys)
    payload_hash = sha256_hex(body_bytes)
    # 服务端 APIGW 对 CanonicalURI 统一补尾斜杠做归一化，故 canonical 路径必须带 '/'
    canonical_path = path if path.endswith('/') else path + '/'
    canonical_request = method + '\n' + canonical_path + '\n' + query + '\n' + canonical_headers + '\n' + signed_headers + '\n' + payload_hash
    string_to_sign = 'SDK-HMAC-SHA256\n' + t + '\n' + sha256_hex(canonical_request.encode('utf-8'))
    signature = hmac.new(sk.encode('utf-8'), string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
    headers['Authorization'] = 'SDK-HMAC-SHA256 Access=%s, SignedHeaders=%s, Signature=%s' % (ak, signed_headers, signature)
    return headers

def call(method, host, path, query='', body=b'', extra_headers=None):
    extra = extra_headers or {}
    extra['Content-Type'] = 'application/json'
    headers = sign(AK, SK, method, host, path, query, body, extra)
    url = 'https://' + host + path + ('?' + query if query else '')
    req = urllib.request.Request(url, data=body if body else None, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30) as r:
            return r.status, r.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode('utf-8', 'replace')

if __name__ == '__main__':
    # S1: publish SMN message to alarm topic
    topic_urn = "urn:smn:%s:%s:mcp-cce-auto-remediation-alarm" % (REGION, PROJECT_ID)
    encoded_urn = urllib.parse.quote(topic_urn, safe='')
    path = "/v2/%s/notifications/topics/%s/publish" % (PROJECT_ID, encoded_urn)
    msg = json.dumps({
        "alarm_name": "PodCrashLoopBackOff",
        "cluster_name": "0dff45d1-bba6-11f1-b4fb-0255ac100268",
        "alarm_status": "alarm",
        "alarm_reason": "container restart loop detected",
        "trigger_time": "2026-09-29T10:00:00Z"
    })
    body = json.dumps({"subject": "verify-PodCrash-S1", "message": msg}).encode('utf-8')
    st, resp = call('POST', 'smn.%s.myhuaweicloud.com' % REGION, path, body=body)
    print("S1 publish status=%s" % st)
    print("S1 publish resp=%s" % resp)

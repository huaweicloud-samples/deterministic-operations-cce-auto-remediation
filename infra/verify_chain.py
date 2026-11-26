import sys, json, time, re, datetime
sys.path.insert(0, "D:/workbuddy/huaweicloud/deterministic-operations-cce-auto-remediation/infra")
from hw_api import call, REGION, PROJECT_ID

LOG_GROUP = "279a97a9-7644-4158-8485-82be9a10e2b4"
STREAMS = [
    "ee399c48-7185-4866-9cb4-b354364b8e39",
    "22344451-688b-4026-9edd-217341e083fb",
    "d7357f24-db31-428b-860e-839f909e948e",
    "97aa4748-98e7-41c6-883f-76297f7ce836",
]

def query_lts(start_ms, end_ms, stream=None, keywords=""):
    host = "lts.%s.myhuaweicloud.com" % REGION
    if not stream:
        stream = STREAMS[0]
    path = "/v2/%s/groups/%s/streams/%s/content/query" % (PROJECT_ID, LOG_GROUP, stream)
    body = json.dumps({
        "start_time": start_ms,
        "end_time": end_ms,
        "keywords": keywords,
        "highlight": False,
        "limit": 100,
        "is_desc": True,
    }).encode("utf-8")
    st, resp = call("POST", host, path, body=body)
    return st, resp

def main():
    now = int(time.time() * 1000)
    start = now - 5 * 60 * 1000
    end = now + 60 * 1000
    uuid_re = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    found = []
    for attempt in range(6):
        print("=== LTS attempt %d ===" % attempt)
        hit = False
        for stream in STREAMS:
            st, resp = query_lts(start, end, stream=stream, keywords="PodCrashLoopBackOff")
            if st == 200:
                try:
                    data = json.loads(resp)
                except Exception as e:
                    print("  stream %s parse err %s" % (stream[:8], e)); continue
                logs = data.get("logs") or []
                for rec in logs:
                    line = rec.get("content") if isinstance(rec, dict) else str(rec)
                    if any(k in line for k in ["PodCrashLoopBackOff", "提取告警", "异步调用分析", "分析完成", "生成请求 ID", "审批请求创建成功", "审批通知发送成功", "收到分析请求", "收到事件", "已异步调用"]):
                        found.append(line); hit = True
            else:
                if attempt == 0:
                    print("  stream %s status=%s resp=%s" % (stream[:8], st, resp[:200]))
        if hit:
            break
        time.sleep(12)
    print("\n=== KEY LOG LINES (filtered) ===")
    seen = set()
    for line in found:
        if line not in seen:
            seen.add(line)
            print(line)
    # extract request_id if present
    rid = None
    for line in found:
        m = uuid_re.search(line)
        if m and ("生成请求 ID" in line or "审批请求创建成功" in line):
            rid = m.group(0); break
    if rid:
        print("\n>>> request_id = %s" % rid)
    else:
        print("\n>>> request_id not found in logs yet (check console S3)")

if __name__ == "__main__":
    main()

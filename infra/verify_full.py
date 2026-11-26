import sys, json, time, urllib.parse
sys.path.insert(0, "D:/workbuddy/huaweicloud/deterministic-operations-cce-auto-remediation/infra")
from hw_api import call, REGION, PROJECT_ID

GID = "279a97a9-7644-4158-8485-82be9a10e2b4"
STREAMS = {
    "detection": "d7357f24-db31-428b-860e-839f909e948e",
    "analysis":  "ee399c48-7185-4866-9cb4-b354364b8e39",
}

def publish_s1():
    topic_urn = "urn:smn:%s:%s:mcp-cce-auto-remediation-alarm" % (REGION, PROJECT_ID)
    encoded = urllib.parse.quote(topic_urn, safe='')
    path = "/v2/%s/notifications/topics/%s/publish" % (PROJECT_ID, encoded)
    msg = json.dumps({
        "alarm_name": "PodCrashLoopBackOff",
        "cluster_name": "0dff45d1-bba6-11f1-b4fb-0255ac100268",
        "alarm_status": "alarm",
        "alarm_reason": "container restart loop detected",
        "trigger_time": "2026-09-29T10:00:00Z"
    })
    body = json.dumps({"subject": "verify-PodCrash-S1-full", "message": msg}).encode('utf-8')
    st, resp = call('POST', 'smn.%s.myhuaweicloud.com' % REGION, path, body=body)
    print("S1 publish status=%s resp=%s" % (st, resp[:160]))
    return st == 200

def query_stream(name, sid, limit=60):
    end = int(time.time()*1000); start = end - 6*60*1000
    path = "/v2/%s/groups/%s/streams/%s/content/query" % (PROJECT_ID, GID, sid)
    body = json.dumps({"start_time": start, "end_time": end, "highlight": False,
                       "limit": limit, "is_desc": True}).encode()
    st, resp = call('POST', 'lts.%s.myhuaweicloud.com' % REGION, path, body=body)
    out = []
    if st == 200:
        d = json.loads(resp); logs = d.get('logs') or []
        for l in logs:
            c = l.get('content') or ''
            if 'invoke request' in c or 'initialize request' in c:
                continue
            out.append(c)
    else:
        print("  [%s] query status=%s" % (name, st))
    return out

if __name__ == '__main__':
    if not publish_s1():
        print("S1 failed"); sys.exit(1)
    # poll up to ~5 minutes
    found_rid = None
    for i in range(20):
        time.sleep(15)
        det = query_stream("detection", STREAMS["detection"], limit=30)
        ana = query_stream("analysis", STREAMS["analysis"], limit=60)
        # show newest analysis app log lines
        print("\n----- poll %d (t+%ds) -----" % (i+1, (i+1)*15))
        for c in ana[:6]:
            print("  A>", c[:200])
        # detect key milestones
        joined = "\n".join(ana)
        if "审批请求已创建" in joined and found_rid is None:
            # extract request id
            for c in ana:
                if "审批请求已创建" in c:
                    # format: ✅ 审批请求已创建: <uuid>
                    try:
                        found_rid = c.split("审批请求已创建:")[-1].strip().split()[0]
                    except Exception:
                        pass
        if "审批通知发送成功" in joined:
            print("  >>> EMAIL SENT")
        if found_rid:
            print("  >>> REQUEST_ID = %s" % found_rid)
            break
    print("\n===== FINAL request_id=%s =====" % found_rid)

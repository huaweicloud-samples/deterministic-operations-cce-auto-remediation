import sys, json, time, urllib.parse
sys.path.insert(0, "D:/workbuddy/huaweicloud/deterministic-operations-cce-auto-remediation/infra")
from hw_api import call, REGION, PROJECT_ID, AK, SK

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
    body = json.dumps({"subject": "verify-PodCrash-S1-rerun", "message": msg}).encode('utf-8')
    st, resp = call('POST', 'smn.%s.myhuaweicloud.com' % REGION, path, body=body)
    print("S1 publish status=%s resp=%s" % (st, resp[:200]))
    return st == 200

def query_stream(name, sid, keywords="", limit=30):
    end = int(time.time()*1000); start = end - 10*60*1000
    path = "/v2/%s/groups/%s/streams/%s/content/query" % (PROJECT_ID, GID, sid)
    body = json.dumps({"start_time": start, "end_time": end, "keywords": keywords,
                       "highlight": False, "limit": limit, "is_desc": True}).encode()
    st, resp = call('POST', 'lts.%s.myhuaweicloud.com' % REGION, path, body=body)
    out = []
    if st == 200:
        try:
            d = json.loads(resp); logs = d.get('logs') or []
            for l in logs:
                c = l.get('content') or ''
                if 'invoke request' in c or 'initialize request' in c:
                    continue
                out.append(c)
        except Exception as e:
            print("  parse err %s" % e)
    else:
        print("  [%s] query status=%s resp=%s" % (name, st, resp[:200]))
    return out

if __name__ == '__main__':
    ok = publish_s1()
    if not ok:
        print("S1 failed, abort"); sys.exit(1)
    time.sleep(8)
    print("\n===== detection app logs (last 10 min) =====")
    det = query_stream("detection", STREAMS["detection"], limit=40)
    for c in det[:25]:
        print("  D>", c[:300])
    print("\n===== analysis app logs (last 10 min) =====")
    ana = query_stream("analysis", STREAMS["analysis"], limit=40)
    for c in ana[:25]:
        print("  A>", c[:300])

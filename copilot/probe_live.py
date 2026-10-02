"""Probe which Meetily Pro routes expose transcript text DURING a recording.
Start a recording, say a distinctive word a few times (default: "banana"),
then run:  python probe_live.py banana
"""
import json, os, sys, urllib.parse, urllib.request, urllib.error

BASE = "http://127.0.0.1:8420"
TOK = os.environ.get("MEETILY_PRO_TOKEN") or open(
    os.path.join(os.environ["APPDATA"], "pro.meetily.ai", "gateway-token")).read().strip()

def get(path):
    r = urllib.request.Request(BASE + path, headers={"Authorization": "Bearer " + TOK})
    try:
        with urllib.request.urlopen(r, timeout=10) as x:
            return x.status, json.loads(x.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:300]

word = sys.argv[1] if len(sys.argv) > 1 else "banana"
_, rec = get("/v1/recording")
mid = rec.get("active_meeting_id") if isinstance(rec, dict) else None
print("recording:", rec)
if not mid:
    sys.exit("Start a recording first.")

def show(name, code, body, n=600):
    print(f"\n=== {name} -> {code}")
    print(json.dumps(body, indent=1)[:n] if not isinstance(body, str) else body)

show("meeting", *get(f"/v1/meetings/{mid}"))
show("export json", *get(f"/v1/meetings/{mid}/export?format=json"))
for q in (word, "the", "a"):
    code, body = get("/v1/search?" + urllib.parse.urlencode({"q": q, "limit": 50}))
    hits = [r for r in body.get("results", []) if r.get("meeting_id") == mid] if isinstance(body, dict) else []
    print(f"\n=== search q={q!r} -> {code}; {len(hits)} hit(s) in the ACTIVE meeting")
    for h in hits[:5]:
        print("   ", h.get("audio_start_time"), "|", h.get("match_context"))
    if not isinstance(body, dict):
        print(body)

#!/usr/bin/env python3
"""Herdr app layout: the herdr-app document in herdr-core (doc.get/doc.set with version).
  layout.py show
  layout.py find-tab SPACE NAME            -> tab id, or nothing
  layout.py add-tab SPACE NAME SPEC        -> tab id (leaves an existing tab alone)
  layout.py add-space FOLDER TAB SPEC      -> "space_id tab_id"
  layout.py rm-folder PATH                 -> remove a worktree's space
  layout.py swap OLD NEW                   -> replace a terminal in place
SPEC: terminals in a row separated by commas, rows separated by '/', e.g. "t33,t32/t10".
Backs up to $FB_STATE/layout-backups/ before every write (the last 20 are kept)."""
import glob, json, os, secrets, subprocess, sys, time

STATE = os.environ.get("FB_STATE", os.path.expanduser("~/.cache/leha-loop"))
BACKUPS = os.path.join(STATE, "layout-backups")
os.makedirs(BACKUPS, exist_ok=True)

def rpc(method, params):
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) + "\n"
    try:
        out = subprocess.run(["hc", "connect"], input=req, capture_output=True, text=True,
                             encoding="utf-8", timeout=15).stdout
    except FileNotFoundError:
        sys.exit("layout.py: hc not found")
    except subprocess.TimeoutExpired:
        sys.exit("layout.py: hc connect did not answer in 15 s (%s)" % method)
    try:
        return json.loads(out.strip().splitlines()[-1])
    except (IndexError, ValueError):
        sys.exit("layout.py: no answer from hc connect (%s)" % method)

def get():
    r = rpc("doc.get", {"name": "herdr-app"})
    if "result" not in r: sys.exit("layout.py: doc.get failed: " + json.dumps(r.get("error")))
    return r["result"]

def backup(d):
    path = os.path.join(BACKUPS, time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2) + ".json")
    with open(path, "w", encoding="utf-8") as f: json.dump(d, f)
    for old in sorted(glob.glob(os.path.join(BACKUPS, "*.json")))[:-20]: os.remove(old)

def nid(p): return p + secrets.token_hex(16)
def block(t): return {"content": {"terminal": t}, "id": nid("p"), "type": "block"}

def tree(spec, tid):
    rows = [r.split(",") for r in spec.split("/")]
    if len(rows) == 1:
        return {"children": [block(t) for t in rows[0]], "id": tid, "type": "row"}
    col = [block(r[0]) if len(r) == 1 else {"children": [block(t) for t in r], "id": nid("s"), "type": "row"} for r in rows]
    return {"children": [{"children": col, "id": nid("s"), "type": "column"}], "id": tid, "type": "row"}

def terms(x, acc):
    if isinstance(x, dict):
        c = x.get("content")
        if isinstance(c, dict) and "terminal" in c: acc.append(c["terminal"])
        for y in x.values(): terms(y, acc)
    elif isinstance(x, list):
        for y in x: terms(y, acc)
    return acc

def edit(fn):
    for _ in range(5):
        d = get(); orig = json.dumps(d); v = d["value"]
        res = fn(v)
        if res is False: return None
        backup(json.loads(orig))
        r = rpc("doc.set", {"name": "herdr-app", "value": v, "version": d["version"]})
        if "result" in r: return res
    sys.exit("doc.set failed: " + json.dumps(r.get("error")))

def space(v, sid):
    for s in v["spaces"]:
        if s["id"] == sid: return s
    sys.exit("no such space: " + sid)

cmd, a = sys.argv[1], sys.argv[2:]
if cmd == "show":
    for s in get()["value"]["spaces"]:
        print(s["id"], s.get("name") or "-", s.get("folder") or "-")
        for t in s["tabs"]: print("   ", t["id"], t.get("name"), " ".join(terms(t["root"], [])))
elif cmd == "find-tab":
    for t in space(get()["value"], a[0])["tabs"]:
        if t.get("name") == a[1]: print(t["id"]); break
elif cmd == "add-tab":
    def f(v):
        s = space(v, a[0])
        for t in s["tabs"]:
            if t.get("name") == a[1]: print(t["id"]); return False
        tid = nid("tb"); s["tabs"].append({"id": tid, "name": a[1], "root": tree(a[2], tid)}); return tid
    r = edit(f)
    if r: print(r)
elif cmd == "add-space":
    def f(v):
        sid, tid = nid("spd"), nid("tb")
        v["spaces"].append({"folder": a[0], "id": sid, "name": "", "tabs": [{"id": tid, "name": a[1], "root": tree(a[2], tid)}]})
        return sid + " " + tid
    print(edit(f))
elif cmd == "rm-folder":
    def f(v):
        n = len(v["spaces"]); v["spaces"] = [s for s in v["spaces"] if s.get("folder") != a[0]]
        return False if n == len(v["spaces"]) else "ok"
    print(edit(f) or "no such space")
elif cmd == "swap":
    def f(v):
        hit = []
        def w(x):
            if isinstance(x, dict):
                c = x.get("content")
                if isinstance(c, dict) and c.get("terminal") == a[0]: c["terminal"] = a[1]; hit.append(1)
                for y in x.values(): w(y)
            elif isinstance(x, list):
                for y in x: w(y)
        w(v); return "ok" if hit else False
    print(edit(f) or "terminal " + a[0] + " not found in the layout")
else:
    sys.exit(__doc__)

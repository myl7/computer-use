import json
import os
import subprocess
import time
from pathlib import Path


HOSTS = {
    "cs659b": "/home/yulong/app/guiexp/android/experimental-results/trace_verifier_20260925/orchestration",
    "cs11369a-desktop": "/home/myl/app/guiexp/osworld/experimental-results/trace_verifier_20260925/orchestration",
    "cs11369a-web": "/home/myl/app/guiexp/webarena/experimental-results/trace_verifier_20260925/orchestration",
}

REMOTE = r'''
import json, os, pathlib, sys
root=pathlib.Path(sys.argv[1])
items=[]
for pid_file in sorted(root.rglob("*.pid")) if root.exists() else []:
    try: pid=int(pid_file.read_text().strip())
    except Exception: continue
    proc=pathlib.Path("/proc")/str(pid)
    live=proc.exists()
    row={"pid_file":str(pid_file),"pid":pid,"live":live}
    if live:
        try: row["command"]=((proc/"cmdline").read_bytes().replace(b"\0",b" ").decode(errors="replace").strip())
        except Exception: row["command"]=None
        try: row["start_ticks"]=(proc/"stat").read_text().split()[21]
        except Exception: row["start_ticks"]=None
        try: row["cwd"]=os.readlink(proc/"cwd")
        except Exception: row["cwd"]=None
    items.append(row)
print(json.dumps(items))
'''


def main() -> None:
    records = []
    for label, root in HOSTS.items():
        host = label.split("-", 1)[0]
        result = subprocess.run(["ssh", host, "python3", "-", root], input=REMOTE,
                                text=True, capture_output=True, check=True)
        for row in json.loads(result.stdout):
            row["host"] = host
            row["surface"] = label
            records.append(row)
    payload = {"captured_at_unix": time.time(), "records": records}
    path = Path(__file__).with_name("live_jobs.json")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n")
    os.replace(tmp, path)
    print(json.dumps({"path": str(path), "records": len(records),
                      "live": sum(bool(x["live"]) for x in records)}))


if __name__ == "__main__":
    main()

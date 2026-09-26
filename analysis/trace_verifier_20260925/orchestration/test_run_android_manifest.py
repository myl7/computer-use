import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


LAUNCHER = Path(__file__).with_name("run_android_manifest.sh").resolve()


def write_fake_runner(path: Path) -> None:
    path.write_text(
        """import argparse, json, os, time
from pathlib import Path
p=argparse.ArgumentParser()
for name in ('source-cell','model','family','attempt-id','attempt-role','code-bundle-id','private-env-file','price-sheet-id','out','console-port','grpc-port','avd'):
    p.add_argument('--'+name, required=True)
a=p.parse_args()
with open(os.environ['FAKE_INVOCATIONS'],'a') as f: f.write(a.attempt_id+'\\n')
time.sleep(float(os.environ.get('FAKE_SLEEP','0')))
out=Path(a.out); out.mkdir(parents=True,exist_ok=True)
r={'platform':'android','model':a.model,'family':a.family,'attempt_id':a.attempt_id,
   'attempt_role':a.attempt_role,'terminal_status':'complete','status':'complete',
   'admitted':False,'deployment':{'status':'skipped'}}
tmp=out/'result.json.tmp'; tmp.write_text(json.dumps(r)); tmp.replace(out/'result.json')
"""
    )


def env(root: Path, runner: Path, invocations: Path) -> dict[str, str]:
    value = os.environ.copy()
    value.update({
        "TRACE_VERIFIER_ANDROID_WORKSPACE": str(root),
        "TRACE_VERIFIER_ANDROID_BUNDLE": str(root / "bundle"),
        "TRACE_VERIFIER_ANDROID_BUNDLE_ID": "test-bundle",
        "TRACE_VERIFIER_ANDROID_PYTHON": sys.executable,
        "TRACE_VERIFIER_ANDROID_RUNNER": str(runner),
        "TRACE_VERIFIER_ANDROID_CLAIM_ROOT": str(root / "claims"),
        "FAKE_INVOCATIONS": str(invocations),
    })
    return value


def manifest(path: Path, rows: list[tuple[str, str]]) -> None:
    lines = ["# source|model_slug|model|family|attempt_id|role|out"]
    for attempt_id, out in rows:
        lines.append(f"source|model_slug|test/model|Family|{attempt_id}|repeat|{out}")
    path.write_text("\n".join(lines) + "\n")


def run_tests() -> None:
    with tempfile.TemporaryDirectory(prefix="android-manifest-test-") as td:
        root = Path(td)
        runner = root / "fake_runner.py"
        invocations = root / "invocations.txt"
        write_fake_runner(runner)
        common_env = env(root, runner, invocations)

        rows = [(f"attempt{i}", str(root / f"out{i}")) for i in range(3)]
        three = root / "three.tsv"
        manifest(three, rows)
        subprocess.run([str(LAUNCHER), str(three), "1", "2", "avd", "lane"],
                       env=common_env, check=True, stdin=subprocess.DEVNULL)
        assert invocations.read_text().splitlines() == ["attempt0", "attempt1", "attempt2"]

        complete = root / "complete"
        complete.mkdir()
        result = complete / "result.json"
        record = {"platform": "android", "model": "test/model", "family": "Family",
                  "attempt_id": "done", "attempt_role": "repeat",
                  "terminal_status": "complete", "status": "complete"}
        result.write_text(json.dumps(record))
        before = hashlib.sha256(result.read_bytes()).hexdigest()
        invocations.write_text("")
        done_manifest = root / "done.tsv"
        manifest(done_manifest, [("done", str(complete))])
        subprocess.run([str(LAUNCHER), str(done_manifest), "1", "2", "avd", "lane"],
                       env=common_env, check=True, stdin=subprocess.DEVNULL)
        assert hashlib.sha256(result.read_bytes()).hexdigest() == before
        assert invocations.read_text() == ""

        concurrent_out = root / "concurrent"
        concurrent_manifest = root / "concurrent.tsv"
        manifest(concurrent_manifest, [("race", str(concurrent_out))])
        invocations.write_text("")
        race_env = dict(common_env, FAKE_SLEEP="1")
        command = [str(LAUNCHER), str(concurrent_manifest), "1", "2", "avd", "race"]
        first = subprocess.Popen(command, env=race_env, stdin=subprocess.DEVNULL)
        second = subprocess.Popen(command, env=race_env, stdin=subprocess.DEVNULL)
        codes = sorted([first.wait(), second.wait()])
        assert codes == [0, 13]
        assert invocations.read_text().splitlines() == ["race"]


if __name__ == "__main__":
    run_tests()
    print("3 launcher safety tests passed")

"""Frozen diagnostic of strong GUI programs before proposing recovery methods.

Programs mutate the benchmark only through GUI actions. Read-only public file
inspection is available to programs and reactive agents for effect checks.
The benchmark evaluator is called only after a policy declares termination.
"""

from __future__ import annotations

import argparse
import ast
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import subprocess
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "experimental-results/recovery_validation_20260916"
ADB = ROOT / "third-party/android-sdk/platform-tools/adb"
MODELS = ["z-ai/glm-5.3-flash", "deepseek/deepseek-v4.1-flash"]
FAMILIES = ["MarkorDeleteNote", "FilesMoveFile"]
PUBLIC_DIRS = ["Alarms", "Audiobooks", "DCIM", "Documents", "Download", "Movies",
               "Music", "Notifications", "Pictures", "Podcasts", "Recordings", "Ringtones"]
ARMS = ["clean", "font_large", "notification_after_selection", "focus_after_confirmation"]
SOURCES = {
    f: ROOT / f"experimental-results/guiexp_android/selective_20260915/training/{f}/s915101_a0/trajectory.jsonl"
    for f in FAMILIES
}
PARAMETERS = {
    "MarkorDeleteNote": {"file_name": "Exact note filename from the goal, including extension"},
    "FilesMoveFile": {"file_name": "Exact filename", "source_folder": "Source public storage directory", "destination_folder": "Destination public storage directory"},
}


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n")
    temp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def slug(value):
    return value.replace("/", "_")


def adb(*args, timeout=30, binary=False):
    r = subprocess.run([str(ADB), "-s", "emulator-5554", *args],
                       capture_output=True, timeout=timeout)
    if r.returncode:
        raise RuntimeError(f"ADB failed ({r.returncode}): {r.stderr.decode(errors='replace')[:200]}")
    return r.stdout if binary else r.stdout.decode(errors="replace").strip()


def shell(*args, **kwargs):
    return adb("shell", shlex.join(str(a) for a in args), **kwargs)


def storage_manifest():
    text=shell("find","/storage/emulated/0","-type","f","-exec","sha256sum","{}",";",timeout=120)
    return sorted(line for line in text.splitlines() if line.strip())


def image_part(path):
    return {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(Path(path).read_bytes()).decode()}}


def observation_parts(obs):
    return [
        {"type": "text", "text": json.dumps({"activity": obs["activity"],
            "action_elements": obs["action_elements"], "ax_forest": obs["ax_forest"]},
            ensure_ascii=False, separators=(",", ":"), default=str)},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + obs["screenshot_b64"]}},
    ]


def content(response):
    return response["choices"][0]["message"].get("content") or ""


def json_reply(text):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    decoder = json.JSONDecoder()
    for i, c in enumerate(text):
        if c == "{":
            try:
                obj, _ = decoder.raw_decode(text[i:])
                return obj
            except json.JSONDecodeError:
                pass
    raise ValueError("No JSON object in model reply")


PROGRAM_API = r"""
Write ordinary Python with functions program(device, params) and verify(device, params).
program performs the task through GUI actions and may raise on failed checks.
verify must inspect the actual result and return True only when the goal is fulfilled.
The harness calls verify after program; it also uses verify after reactive recovery.
No task oracle, hidden task parameters, app database, filesystem write or shell is exposed.
Read-only public shared-storage inspection is allowed to every policy for assertions.

Device API:
  elements() -> current full action-element list; each dict has index,text,hint,description,
    resource_id,class_name,clickable,editable,selected,checked,enabled,bbox/bbox_pixels.
  find(text=None,contains=None,hint=None,description=None,resource_id=None,
       clickable=None,editable=None,scrollable=None) -> UNIQUE index or None;
    ambiguous matches raise. For alternative matching inspect elements() yourself.
  click(index=...), long_press(index=...), input_text(text,index=...) replaces text and presses Enter.
  execute({"action_type":"input_text","index":i,"text":value,"clear_text":True})
  scroll(direction="down"), open_app(app_name), navigate_back(), navigate_home(), wait(), settle(seconds)
  current_activity() -> activity name. observe() -> screenshot and complete AX.
  list_files(path), read_file(path,max_bytes=65536), exists(path) are READ ONLY.
  Public paths: /storage/emulated/0 (list only), and its Alarms,Audiobooks,DCIM,Documents,
    Download,Movies,Music,Notifications,Pictures,Podcasts,Recordings,Ringtones subtrees.
    /sdcard is an alias. list_files returns entries with name,path,is_dir. No glob/shell commands.
  check(condition,label,details=None) logs a VERIFIED condition or raises AssertionError.

Use bounded loops, fresh observations, stable attributes and exact goal-bound filenames.
Handle common transient obstructions, notification shade, changed app focus, non-visible
targets, text wrappers, changed list contents and already-completed effects. A program
can inspect its present state, branch, search and wait; it is not a flat action recording.
Distinguish issued actions from verified effects. Safe retries are allowed after inspecting
the effect. Do not repeat an already-completed deletion/move. Do not declare success merely
because the editor/dialog closed. Verify target identity, source/destination state as relevant.
Avoid deleting unrelated items. All file mutation must be through the GUI.
Use no external/model calls. Imports allowed: re, posixpath, datetime, math.
Do not access private attributes, eval/exec/open, environment variables or subprocesses.
Return only Python source in one python code fence, normally fewer than 250 lines.
"""

AGENT_PROMPT = """You operate an Android benchmark through current screenshots and complete accessibility trees.
Use the goal and verified progress to finish the task from the CURRENT environment state.
All mutations must use GUI actions. Read-only public shared-storage inspections are allowed
for checking effects. Do not access private files, application internals, or a task evaluator.
Re-check uncertain effects before retrying; an issued action is not proof of completion.
Use indices only from the current observation. Return JSON {"reason":"brief reason", "action":{...}}.
Actions: click/long_press with index; input_text with index,text,clear_text (default true,
replaces text and presses Enter); scroll with direction; open_app with app_name;
navigate_back; navigate_home; wait; inspect_files with path; inspect_file with path;
status with goal_status complete or infeasible. Public file roots are standard shared-storage
folders under /storage/emulated/0 or /sdcard. Inspection does not modify files.
Do not stop merely because a button was clicked. Inspect that the requested result occurred.
"""


def training_parts(family):
    path = SOURCES[family]
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    parts = [{"type": "text", "text": "Compile this rich reactive execution. Parameter contract: " + json.dumps(PARAMETERS[family])}]
    seen = {}
    image_manifest = []
    for row in rows:
        kind = row.get("record_type")
        if kind == "model_call":
            parts.append({"type": "text", "text": "Original model action/reasoning response:\n" + json.dumps(row.get("response", {}), ensure_ascii=False)})
        elif kind == "initial":
            parts.append({"type": "text", "text": "Original goal: " + row.get("goal_text", "")})
        elif kind == "step":
            parts.append({"type": "text", "text": json.dumps({k:row.get(k) for k in ("step","action","action_issued","pre_obs","post_obs")}, ensure_ascii=False)})
            for phase in ("pre", "post"):
                name = row.get("screenshot_files", {}).get(phase, {}).get("raw")
                if not name:
                    continue
                image_path = path.parent / name
                key = sha(image_path)
                if key in seen:
                    parts.append({"type": "text", "text": f"{phase} screenshot identical to image {seen[key]}."})
                else:
                    number = len(seen) + 1
                    seen[key] = number
                    image_manifest.append({"image":number, "path":str(image_path), "sha256":key})
                    parts.append({"type": "text", "text": f"Image {number}: step {row['step']} {phase}."})
                    parts.append(image_part(image_path))
        elif kind == "final":
            parts.append({"type": "text", "text": "Recorded outcome: " + json.dumps(row, ensure_ascii=False)})
    dump(OUT / "training" / f"{family}-images.json", image_manifest)
    return parts


def load_program(source):
    source = re.sub(r"^```(?:python)?\s*|\s*```$", "", source.strip())
    tree = ast.parse(source)
    forbidden = {"open","eval","exec","compile","globals","locals","getattr","setattr","vars","input","breakpoint"}
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and (n.id in forbidden or n.id.startswith("__")):
            raise ValueError(f"Forbidden program name: {n.id}")
        if isinstance(n, ast.Attribute) and n.attr.startswith("_"):
            raise ValueError("Private attribute access is forbidden")
        if isinstance(n, (ast.Import, ast.ImportFrom)):
            modules = [a.name for a in n.names] if isinstance(n, ast.Import) else [n.module]
            if any(m not in {"re","posixpath","datetime","math"} for m in modules):
                raise ValueError("Unsupported program import")
    namespace = {}
    exec(compile(tree, "generated_program.py", "exec"), namespace)
    if not callable(namespace.get("program")) or not callable(namespace.get("verify")):
        raise ValueError("Both program and verify functions are required")
    namespace["__source_sha256"] = hashlib.sha256(source.encode()).hexdigest()
    return source, namespace


@contextlib.contextmanager
def deadline(seconds):
    def expired(*_):
        raise TimeoutError("Program wall-clock limit reached")
    old = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


class Perturbation:
    def __init__(self, arm, family):
        self.arm, self.family = arm, family
        self.triggered = False
        self.details = None
        self.original_font = None

    def begin(self):
        if self.arm == "font_large":
            self.original_font = shell("settings", "get", "system", "font_scale")
            shell("settings", "put", "system", "font_scale", "1.3")
            self.triggered = True

    def hook(self, device, action):
        if self.triggered:
            return
        target = getattr(device, "last_action_target", None) or {}
        names = {str(target.get(key) or "").strip().lower() for key in ("text", "description", "content_description")}
        cls = str(target.get("class_name", "")).lower()
        if self.arm == "notification_after_selection" and action.get("action_type") == "long_press":
            shell("cmd", "statusbar", "expand-notifications")
            self.triggered = True
        elif self.arm == "focus_after_confirmation" and action.get("action_type") == "click":
            before=getattr(device,"last_action_pre_observation",None) or {}
            before_elements=before.get("action_elements",[])
            has_cancel=any(str(e.get("text") or "").strip().lower()=="cancel" for e in before_elements)
            delete_commit = self.family == "MarkorDeleteNote" and has_cancel and "button" in cls and bool(names & {"delete", "ok", "yes"})
            move_commit = self.family == "FilesMoveFile" and (bool(names & {"paste", "move here", "copy here"}) or ("button" in cls and "move" in names))
            if delete_commit or move_commit:
                shell("input", "keyevent", "3")
                self.triggered = True
        if self.triggered:
            self.details = {"after_action": action, "target": target, "action_count": device.actions}

    def end(self):
        shell("cmd", "statusbar", "collapse")
        if self.original_font is not None:
            if self.original_font == "null":
                shell("settings", "delete", "system", "font_scale")
            else:
                shell("settings", "put", "system", "font_scale", self.original_font)


def model_call(client, model, messages, max_tokens, episode):
    from .selective_budget import require_host_ready
    from .recovery_validation_budget import BudgetError
    try:
        require_host_ready()
    except Exception as exc:
        raise BudgetError(f"Host readiness check failed: {type(exc).__name__}") from None
    response = client.complete(model=model, messages=messages, max_tokens=max_tokens,
                               episode=episode, temperature=0, reasoning_effort="low")
    if response["choices"][0].get("finish_reason") == "length":
        raise RuntimeError("Truncated model response; preserve and stop this attempt")
    return response


def extract_params(client, model, family, goal, obs, episode):
    messages = [{"role":"system", "content":"Extract only the named parameters from the goal. Return one JSON object. Do not infer parameters from existing unrelated UI records."},
                {"role":"user", "content":[{"type":"text", "text":json.dumps({"goal":goal,"parameters":PARAMETERS[family]})}] + observation_parts(obs)}]
    response = model_call(client, model, messages, 1024, episode + "/binding")
    params = json_reply(content(response))
    if set(params) != set(PARAMETERS[family]) or not all(isinstance(v,str) and v for v in params.values()):
        raise ValueError("Incorrect extraction schema")
    return params


def fallback(client, model, goal, device, params, verify, episode, max_calls=20):
    history = []
    tool_result = None
    for step in range(max_calls):
        obs = device.observe()
        progress = [x for x in device.events if x.get("event") in {"effect_verified","check_failed","effect_unknown","intent","issued"}][-20:]
        text = json.dumps({"goal":goal,"progress":progress,"recent_actions":history[-6:],
                           "last_inspection":tool_result, "remaining_model_calls":max_calls-step}, ensure_ascii=False, default=str)
        response = model_call(client, model, [{"role":"system","content":AGENT_PROMPT},
                    {"role":"user","content":[{"type":"text","text":text}] + observation_parts(obs)}],
                    1536, episode + f"/recovery/{step:02d}")
        parsed = json_reply(content(response))
        action = parsed.get("action", parsed)
        tool_result = None
        if action.get("action_type") == "status":
            if action.get("goal_status") == "complete":
                with deadline(60):
                    if verify(device, params) is not True:
                        raise AssertionError("Agent declared completion but generated verifier rejected it")
                return True
            return False
        try:
            if action.get("action_type") == "inspect_files":
                tool_result = device.list_files(action["path"])
            elif action.get("action_type") == "inspect_file":
                tool_result = device.read_file(action["path"])
            else:
                device.execute(action)
            history.append({"action":action,"reason":parsed.get("reason","")})
        except Exception as exc:
            history.append({"action":action,"error":str(exc)[:500]})
        dump(device.episode_dir / "recovery-history.json", history)
    return False


def run_episode(env, client, model, family, seed, arm, program, treatment, episode):
    from .android_env import get_task, goal_text
    from .recovery_validation_device import RecoveryDevice
    from .recovery_validation_budget import BudgetError
    path = OUT / "episodes" / episode
    identity = {"spec_sha256":sha(OUT/"spec.json"),"program_sha256":program["__source_sha256"]}
    if (path / "started.json").exists():
        if (path / "result.json").exists():
            cached=json.loads((path / "result.json").read_text())
            if any(cached.get(k)!=v for k,v in identity.items()):
                raise RuntimeError("Cached episode belongs to a different frozen experiment")
            return cached
        raise RuntimeError(f"Incomplete episode is terminal and cannot be replayed: {episode}")
    dump(path / "started.json", {"episode":episode,"seed":seed,"arm":arm,"model":model,"family":family,"time":time.time(),**identity})
    task = get_task(family, "discover", seed)
    env.reset(task)
    goal = goal_text(task).replace("sdk_gphone_x86_64", shell("getprop", "ro.product.model"))
    perturbation = Perturbation(arm, family)
    device = RecoveryDevice(env, path, max_actions=60, after_action_hook=perturbation.hook)
    result = {"episode":episode,"family":family,"model":model,"seed":seed,"arm":arm,"treatment":treatment,"goal":goal,
              "program_verified":False,"recovery_used":False,"policy_complete":False,"error":None,**identity}
    started = time.time()
    fatal_error = None
    try:
        perturbation.begin()
        obs = device.observe()
        params = extract_params(client, model, family, goal, obs, episode)
        result["extracted_params"] = params
        if treatment != "reactive":
            try:
                with deadline(180):
                    program["program"](device, params)
                    if program["verify"](device, params) is not True:
                        raise AssertionError("Generated final verifier returned false")
                result["program_verified"] = result["policy_complete"] = True
            except Exception as exc:
                result["program_error"] = f"{type(exc).__name__}: {exc}"
                result["program_traceback"] = traceback.format_exc(limit=5)
        if treatment != "deterministic" and not result["program_verified"]:
            result["recovery_used"] = True
            result["policy_complete"] = fallback(client, model, goal, device, params,
                program["verify"], episode, max_calls=20)
        # Independent evaluator. Its result never controls the executed policy.
        result["oracle_success"] = float(env.reward()) >= 1
    except BudgetError as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["infrastructure_error"] = True
        result["oracle_success"] = None
        fatal_error = exc
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["traceback"] = traceback.format_exc(limit=5)
        # Measurement follows the policy's terminal error, and is not fed back.
        try:
            result["oracle_success"] = float(env.reward()) >= 1
        except Exception:
            result["oracle_success"] = None
    finally:
        result.update({"perturbation_triggered":perturbation.triggered,"injection":perturbation.details,
                       "actions":device.actions,"wall_seconds":time.time()-started})
        perturbation.end()
        dump(path / "result.json", result)
    print(json.dumps({k:result.get(k) for k in ("episode","program_verified","recovery_used","oracle_success","error")}),flush=True)
    if fatal_error is not None:
        raise fatal_error
    # Unknown or invalid bills prohibit any continuation of paid work.
    summary = client.ledger.summary()
    if summary.get("blocked") or summary.get("unknown_calls",0) or summary.get("overrun_calls",0):
        raise RuntimeError("Budget ledger blocked after episode")
    return result


def build_programs(env, client):
    outcomes = []
    for model in MODELS:
        for family in FAMILIES:
            folder = OUT / "build" / slug(model) / family
            if (folder / "admission.json").exists():
                cached=json.loads((folder / "admission.json").read_text())
                if cached.get("spec_sha256")!=sha(OUT/"spec.json"):
                    raise RuntimeError("Admission belongs to another frozen specification")
                if cached["admitted"] and cached.get("program_sha256")!=sha(folder/"program.py"):
                    raise RuntimeError("Admitted program changed")
                outcomes.append(cached)
                continue
            messages = [{"role":"system","content":PROGRAM_API}, {"role":"user","content":training_parts(family)}]
            admitted = False
            all_development = []
            for attempt in range(3):
                episode = f"build/{slug(model)}/{family}/v{attempt}"
                response = model_call(client, model, messages, 12288, episode)
                dump(folder / f"response-v{attempt}.json", response)
                raw = content(response)
                try:
                    source, namespace = load_program(raw)
                    (folder / f"program-v{attempt}.py").write_text(source + "\n")
                    dev = []
                    for i in range(2):
                        seed = 916110 + i + (100 if family=="FilesMoveFile" else 0)
                        dev.append(run_episode(env, client, model, family, seed, "clean", namespace,
                            "deterministic", f"development/{slug(model)}/{family}/v{attempt}/d{i}"))
                    all_development.extend(dev)
                    admitted = all(r.get("program_verified") and r.get("oracle_success") for r in dev)
                    if admitted:
                        shutil.copyfile(folder / f"program-v{attempt}.py", folder / "program.py")
                        break
                    feedback = json.dumps(dev, ensure_ascii=False)
                    obs = env.aw_env.get_state(wait_to_stabilize=False)
                    from .android_env import _encode_png_b64
                    from dataclasses import asdict
                    from google.protobuf.json_format import MessageToDict
                    packet={"activity":env.aw_env.foreground_activity_name,"screenshot_b64":_encode_png_b64(obs.pixels),
                            "action_elements":[{"index":i,**asdict(e)} for i,e in enumerate(obs.ui_elements)],
                            "ax_forest":MessageToDict(obs.forest,preserving_proto_field_name=True)}
                    messages += [{"role":"assistant","content":raw}, {"role":"user","content":[{"type":"text","text":"Development feedback only. Repair the program and all its checks; do not weaken the goal.\n"+feedback}] + observation_parts(packet)}]
                except (SyntaxError, ValueError) as exc:
                    messages += [{"role":"assistant","content":raw},{"role":"user","content":[{"type":"text","text":"Fix this compile error: "+str(exc)}] + training_parts(family)}]
            record={"model":model,"family":family,"admitted":admitted,"last_attempt":attempt,"development":all_development,
                    "spec_sha256":sha(OUT/"spec.json"),"program_sha256":sha(folder/"program.py") if admitted else None}
            dump(folder / "admission.json", record)
            outcomes.append(record)
    dump(OUT / "admission.json", outcomes)
    return outcomes


def screen(env, client, admissions):
    rows=[]
    for item in admissions:
        if not item["admitted"]:
            continue
        model,family=item["model"],item["family"]
        _,program=load_program((OUT/"build"/slug(model)/family/"program.py").read_text())
        for i in range(3):
            seed=916310+i+(100 if family=="FilesMoveFile" else 0)
            for arm in ARMS:
                rows.append(run_episode(env,client,model,family,seed,arm,program,"deterministic",
                    f"screen/{slug(model)}/{family}/s{seed}/{arm}"))
    dump(OUT/"screen-results.json",rows)
    return rows


def confirm(env, client, screened):
    # Adaptive diagnostic selection is declared before screening. New bindings
    # assess the first realized failing perturbation in fixed arm order per family.
    selected={}
    for family in FAMILIES:
        for arm in ARMS:
            if any(r["family"]==family and r["arm"]==arm and (arm=="clean" or r["perturbation_triggered"])
                   and not (r.get("program_verified") and r.get("oracle_success")) for r in screened):
                selected[family]=arm
                break
    dump(OUT/"confirmation-selection.json",selected)
    rows=[]
    for family,arm in selected.items():
        for model in MODELS:
            path=OUT/"build"/slug(model)/family/"program.py"
            if not path.exists():
                continue
            _,program=load_program(path.read_text())
            for i in range(2):
                seed=916610+i+(100 if family=="FilesMoveFile" else 0)
                order=["fallback","reactive"] if i%2==0 else ["reactive","fallback"]
                for treatment in order:
                    rows.append(run_episode(env,client,model,family,seed,arm,program,treatment,
                        f"confirm/{slug(model)}/{family}/s{seed}/{arm}/{treatment}"))
    dump(OUT/"confirmation-results.json",rows)
    paired=[]
    for model in MODELS:
        keys={(r["family"],r["seed"],r["arm"]) for r in rows if r["model"]==model}
        for family,seed,arm in sorted(keys):
            members=[r for r in rows if r["model"]==model and (r["family"],r["seed"],r["arm"])==(family,seed,arm)]
            exposure_matched=len(members)==2 and (arm=="clean" or all(r["perturbation_triggered"] for r in members))
            paired.append({"model":model,"family":family,"seed":seed,"arm":arm,"exposure_matched":exposure_matched,
                           "episodes":[r["episode"] for r in members]})
    dump(OUT/"confirmation-pairing.json",paired)
    return rows


def prepare():
    from .android_env import get_task,goal_text
    if (OUT/"spec.json").exists() or (OUT/"budget.sqlite3").exists() or list((OUT/"episodes").glob("**/started.json")):
        raise RuntimeError("Existing freeze or execution evidence cannot be overwritten; use a new version")
    selected={MODELS[0]:["z-ai/fp8","fireworks","siliconflow/fp8","baseten/fp8"],
              MODELS[1]:["wafer","together","novita/fp8","siliconflow/fp8","parasail/fp8"]}
    locks={}
    for model,tags in selected.items():
        path=OUT/(model.replace("/","-")+"-endpoints.json")
        record=json.loads(path.read_text())
        endpoints=[]
        for e in record["response"]["data"]["endpoints"]:
            if e["tag"] in tags:
                e=dict(e)
                if e.get("pricing_tiers") not in (None, [], {}):
                    raise ValueError("Raw endpoint has price tiers")
                for fee in ("request", "image"):
                    if float(e["pricing"].get(fee) or 0) != 0:
                        raise ValueError("Raw endpoint has additive fees")
                e["supports_image"]=True
                e.setdefault("pricing_tiers",[])
                e["pricing"]={**e["pricing"],"request":"0","image":"0"}
                endpoints.append(e)
        if set(e["tag"] for e in endpoints)!=set(tags):
            raise ValueError("Incomplete provider pool")
        locks[model]={"model_id":model,"context_length":min(e["context_length"] for e in endpoints),"endpoints":endpoints,
                      "metadata_path":str(path),"metadata_sha256":sha(path),"normalization":"Image modality from official model catalog; zero additive fees enforced by request max_price. Raw endpoint data retained."}
    dump(OUT/"price-locks.json",locks)
    source_paths=[Path(__file__),Path(__file__).with_name("recovery_validation_budget.py"),Path(__file__).with_name("recovery_validation_device.py"),
                  Path(__file__).with_name("android_env.py"),Path(__file__).with_name("selective_budget.py"),
                  ROOT/"third-party/android_world/android_world/env/actuation.py"]
    source_hashes={}
    for p in source_paths:
        source_hashes[str(p)]=sha(p)
        dest=OUT/"source-snapshot"/p.relative_to(ROOT)
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(p,dest)
    task_ids={}
    training_hashes={}
    for family in FAMILIES:
        seeds=[916110+i for i in range(2)]+[916310+i for i in range(3)]+[916610+i for i in range(2)]
        if family=="FilesMoveFile":seeds=[s+100 for s in seeds]
        goals=[goal_text(get_task(family,"discover",s)) for s in seeds]
        if len(set(goals))!=len(goals):raise ValueError("Duplicate goal bindings")
        task_ids[family]=dict(zip(map(str,seeds),goals))
        for p in SOURCES[family].parent.glob("*.png"):
            training_hashes[str(p)]=sha(p)
    spec={"models":MODELS,"families":FAMILIES,"arms":ARMS,"task_goals":task_ids,
          "program_builds_per_model_family":3,"clean_development_bindings":2,"screen_bindings_per_family":3,
          "confirmation":"First realized failing arm in fixed ARMS order per family; two new bindings; matched compact reactive vs program+fallback; no local-method implementation.",
          "max_actions":60,"max_recovery_calls":20,"new_budget_usd":30,
          "goal_correction":"Replace benchmark's x86 storage label with device ro.product.model uniformly.",
          "permissions":"All writes GUI-only. Same generic read-only public storage inspections for program and reactive checks. Hidden evaluator terminal only.",
          "training_sources":{f:{"path":str(p),"sha256":sha(p)} for f,p in SOURCES.items()},
          "training_image_hashes":training_hashes,
          "source_hashes":source_hashes,"price_locks_sha256":sha(OUT/"price-locks.json"),"prepared_at":time.time()}
    dump(OUT/"spec.json",spec)
    (OUT/"spec.sha256").write_text(sha(OUT/"spec.json")+"\n")
    print(json.dumps({"spec_sha256":sha(OUT/"spec.json"),"tasks":task_ids}),flush=True)


def run(stage):
    import fcntl
    from .android_env import AndroidWorldEnv
    from .recovery_validation_budget import BudgetClient,BudgetLedger
    from .recovery_validation_device import RecoveryDevice
    spec=json.loads((OUT/"spec.json").read_text())
    if sha(OUT/"spec.json") != (OUT/"spec.sha256").read_text().strip():
        raise RuntimeError("Frozen specification changed")
    if sha(OUT/"price-locks.json") != spec["price_locks_sha256"]:
        raise RuntimeError("Price locks changed after freeze")
    for p,h in spec["source_hashes"].items():
        if sha(p)!=h:raise RuntimeError(f"Source changed after freeze: {p}")
    for record in spec["training_sources"].values():
        if sha(record["path"]) != record["sha256"]:
            raise RuntimeError("Training trajectory changed after freeze")
    for p,h in spec["training_image_hashes"].items():
        if sha(p)!=h:raise RuntimeError("Training image changed after freeze")
    lock_path=ROOT/"experimental-results/guiexp_android/revision_20260913/run.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        ledger=BudgetLedger(OUT/"budget.sqlite3",budget_usd=30)
        client=BudgetClient(ledger,OUT/"price-locks.json",ROOT.parent/".env")
        env=AndroidWorldEnv(boot_if_needed=False)
        if stage=="smoke":
            device=RecoveryDevice(env,OUT/"smoke/observation",max_actions=5)
            obs=device.observe()
            messages=[{"role":"user","content":[{"type":"text","text":"Read both image and tree. Return JSON with app_title, floating_button_color, floating_button_symbol. The color must come from the image."}]+observation_parts(obs)}]
            for model in MODELS:
                for i in range(2):
                    response=model_call(client,model,messages,1024,f"smoke/{slug(model)}/{i}")
                    dump(OUT/"smoke"/f"{slug(model)}-{i}.json",response)
                    print(model,i,content(response),flush=True)
            dump(OUT/"budget-summary.json",ledger.summary())
            return
        # Preserve pre-existing public benchmark storage, including another task's
        # fixtures, before task initializers clear their data directories.
        backup=OUT/f"storage-before-{stage}.tar"
        if backup.exists():raise RuntimeError("A stage with an existing backup must not be replayed")
        before_manifest=storage_manifest()
        backup.write_bytes(adb("exec-out","tar","-cf","-","-C","/storage/emulated/0",".",binary=True,timeout=120))
        dump(OUT/f"storage-before-{stage}.json",{"scope":"all shared storage files","files":before_manifest,"sha256":sha(backup)})
        try:
            if stage=="build":build_programs(env,client)
            elif stage=="screen":screen(env,client,json.loads((OUT/"admission.json").read_text()))
            elif stage=="confirm":confirm(env,client,json.loads((OUT/"screen-results.json").read_text()))
        finally:
            try:
                if env._task_impl is not None:env._task_impl.tear_down(env.aw_env)
            finally:
                remote=f"/data/local/tmp/recovery-validation-{stage}.tar"
                adb("push",str(backup),remote,timeout=120)
                shell("find","/storage/emulated/0","-mindepth","1","-type","f","-delete",timeout=120)
                shell("tar","-xf",remote,"-C","/storage/emulated/0",timeout=120)
                shell("rm",remote)
                after_manifest=storage_manifest()
                dump(OUT/f"storage-restored-{stage}.json",{"archive_sha256":sha(backup),"restored_at":time.time(),
                       "file_hashes_match":after_manifest==before_manifest,"files":after_manifest})
                dump(OUT/"budget-summary.json",ledger.summary())
                if after_manifest!=before_manifest:raise RuntimeError("Shared storage restoration needs inspection")


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("stage",choices=["prepare","smoke","build","screen","confirm"])
    args=parser.parse_args()
    if args.stage=="prepare":prepare()
    else:run(args.stage)

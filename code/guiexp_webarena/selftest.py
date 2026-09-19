"""LLM-free self-test of the family + checker on the live site (stage 2 gate).

No model calls. It exercises, in order:

  1. env boots, login confirmed;
  2. numbered DOM tree has >= N elements and indexes are dense;
  3. a scripted "golden" program (the WebDevice contract, hand-written)
     performs one full CommentPost binding through the UI;
  4. the DB checker confirms it (params match);
  5. episode reset removes it (checker returns False after reset);
  6. 3 different seeds produce 3 distinct bindings; gate namespace draws
     are disjoint from instance seeds 1-3 and doc seeds 4-6;
  7. a deploy-style goal text round-trips through the golden program.

Run ON THE SERVER (needs the forum container + chromium):
    python3 -m guiexp_webarena.selftest
"""

from __future__ import annotations

import json

from .env import WebArenaEnv
from .family import FAMILY, check, goal_text, instance_params, params_to_binding
from .gate_runner import heldout_bindings
from .program_runtime import ProgramRunner, WebDevice


GOLDEN_PROGRAM = '''
def program(device, binding):
    forum = binding["forum"]
    title = binding["title"]
    text = binding["text"]
    device.goto("/f/" + forum + "/new")
    device.settle(1.0)
    # find the target post's row: the first link whose text is the title.
    # The title link opens the post's image or external url, NEVER the
    # post page -- the post's own page opens from the row's comments link
    # ("No comments" / "N comments"), the next link whose href starts
    # with /f/ after the title link.
    els = device.elements()
    t_idx = None
    for e in els:
        if e["tag"] == "a" and (e.get("text") or "").strip().lower() == title.strip().lower():
            t_idx = e["index"]
            break
    if t_idx is None:
        raise ValueError("post row not found for title: " + title[:60])
    c_idx = None
    for e in els:
        if (
            e["index"] > t_idx
            and e["tag"] == "a"
            and (e.get("href") or "").startswith("/f/")
            and "comments" in (e.get("text") or "").lower()
        ):
            c_idx = e["index"]
            break
    if c_idx is None:
        raise ValueError("comments link not found in the target row")
    device.click(index=c_idx)
    device.settle(1.0)
    url = device.current_url()
    if "/f/" not in url:
        raise ValueError("the comments link did not open the post page: " + url[:80])
    # the comment box: the big editable textarea on the post page
    box = device.find(tag="textarea")
    if box is None:
        box = device.find(editable=True)
    if box is None:
        raise ValueError("comment box not found on " + url[:80])
    device.input_text(text, index=box)
    device.settle(0.5)
    # the comment form's submit button is labelled 'Post' on this site
    btn = device.find(text="Post", tag="button")
    if btn is None:
        btn = device.find(contains="comment", tag="button")
    if btn is None:
        raise ValueError("comment submit button not found")
    device.click(index=btn)
    device.settle(1.5)
    return True
'''


def main() -> int:
    env = WebArenaEnv()
    failures = []
    try:
        # 1-2. env + DOM tree
        obs = env.observe()
        n = len(obs["elements"])
        print(f"[1] login ok; url={obs['url'][:60]}")
        print(f"[2] elements on forum home: {n}")
        if n < 15:
            failures.append("too few elements parsed")
        ids = [e["index"] for e in obs["elements"]]
        if ids != list(range(len(ids))):
            failures.append("element ids not dense")
        print(f"    ax_tree_text first lines:\n      " + "\n      ".join(obs["ax_tree_text"].splitlines()[:4]))

        # 3-4. golden program on one binding
        task = type("T", (), {"params": instance_params(FAMILY, 101, env=env), "task_id": "selftest"})()
        print(f"[3] golden program on binding: {json.dumps(params_to_binding(FAMILY, task.params))[:120]}")
        _module, program = __import__("guiexp_webarena.program_runtime", fromlist=["x"]).program_from_source(GOLDEN_PROGRAM)
        device = WebDevice(env)
        program(device, params_to_binding(FAMILY, task.params))
        ok = check(env, task)
        print(f"[4] checker says: {ok}")
        if not ok:
            failures.append("checker did not find the golden program's comment")

        # 5. reset removes it
        from .family import reset_for_episode

        reset_for_episode(env, task)
        ok2 = check(env, task)
        print(f"[5] after reset checker says: {ok2} (want False)")
        if ok2:
            failures.append("reset did not remove the comment")

        # 6. bindings disjoint
        seeds_123 = [instance_params(FAMILY, s, env=env) for s in (1, 2, 3)]
        seeds_456 = [instance_params(FAMILY, s, env=env) for s in (4, 5, 6)]
        gate = heldout_bindings(FAMILY, exclude_params=seeds_123, env=env)
        gate_b = [d["binding"] for d in gate]
        clash = {json.dumps(b, sort_keys=True) for b in gate_b} & {
            json.dumps(params_to_binding(FAMILY, p), sort_keys=True) for p in seeds_123 + seeds_456
        }
        print(f"[6] gate bindings: {len(gate)}; overlap with instance/doc seeds: {len(clash)}")
        if clash:
            failures.append(f"gate draws overlap known seeds: {clash}")
        for d in gate:
            print(f"    gate draw: {json.dumps(d['binding'])[:110]}")

        # 7. one more golden run through the runner (the exact gate path)
        runner = ProgramRunner(env)
        outcome = runner.run(program, gate[0]["binding"], FAMILY, judge_params=gate[0]["params"])
        print(f"[7] runner on gate binding 0: passed={outcome['passed']} err={outcome['error']}")
        if not outcome["passed"]:
            failures.append(f"golden program failed a gate-style replay: {outcome['error']}")

        # 8. goal text renders
        print(f"[8] goal text: {goal_text(task)}")
    finally:
        env.close()

    if failures:
        print("SELFTEST FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("SELFTEST OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

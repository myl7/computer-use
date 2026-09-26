"""Prompt conditions for the CommentPost family.

Same ladder as guiexp_android/conditions.py, reduced to what the build
needs (discover for exploration, doc for the doc arm, floor for the
harness floor probe):

    discover  goal only (the intent template filled with binding values)
    told      + the exact interface procedure (NOT used by the build; kept
              for comparability -- steps at form granularity)
    doc       + the compiled operation document, injected at exactly the
              same position as told injects its procedure
    floor     no task; one trivial observation, exit

All conditions share the same goal block.
"""

from __future__ import annotations

CONDITIONS = ("discover", "told", "doc", "floor")

FAMILIES = ("CommentPost",)

COMMON_TEMPLATE = (
    "You are operating a website in a desktop browser.\n"
    "\n"
    "{goal}\n"
    "\n"
    "Rules:\n"
    "- Complete the task by driving the website's UI with the JSON actions\n"
    "  provided (click / input_text / scroll / goto / status).\n"
    "- Only interaction through the site's own UI counts; do not try to\n"
    "  reach the site's files or databases by other means.\n"
    "- You are already logged in where needed.\n"
    "- When the task is finished, reply with the status action with\n"
    "  goal_status complete.\n"
)

TOLD_PROCEDURE = {
    "CommentPost": (
        "The interaction procedure for this site is known. Perform exactly\n"
        "these steps, in this order:\n"
        "1. Go to the forum's page: its URL is /f/<forum-name> under the\n"
        "   site's address (e.g. /f/books).\n"
        "2. On the forum page, open the New tab (it lists the newest posts\n"
        "   first; the target post is always among the newest). Find the\n"
        "   row of the post whose title is the one named in the task.\n"
        "3. Do NOT click the post's title itself: on this site the title\n"
        "   opens the post's image or an external page, not the post. Open\n"
        "   the post's own page by clicking the row's comments link -- the\n"
        "   small link next to the title that reads 'No comments' or\n"
        "   'N comments'.\n"
        "4. On the post's page, scroll down to the comment area. Click in\n"
        "   the comment text box (the large editable area under the post).\n"
        "5. Type the comment text given in the task into the box.\n"
        "6. Click the comment submit button (the button under the box,\n"
        "   labelled 'Post' on this site).\n"
        "7. Check the comment now appears under the post before reporting\n"
        "   success.\n"
        "You do not need to explore the interface; the sequence above is\n"
        "complete and correct."
    ),
}

FLOOR_PROMPT = (
    'Reply with exactly {"action_type": "status", "goal_status": "complete"}'
    " and nothing else. Do not take any other action."
)


def build_prompt(
    condition: str,
    family: str,
    params: dict,
    doc_text: str | None = None,
    task=None,
) -> str:
    if condition == "floor":
        return FLOOR_PROMPT
    if condition not in CONDITIONS:
        raise ValueError(f"unknown condition {condition!r}")
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}")
    if condition == "doc" and not (doc_text or "").strip():
        raise ValueError("condition 'doc' needs a compiled document (doc_text)")
    if task is not None:
        from .family import goal_text

        goal = goal_text(task)
    else:  # offline fallback for tests
        goal = (
            f"Leave a comment on the post titled \"{params.get('title')}\" in the "
            f"{params.get('forum')} forum, saying \"{params.get('text')}\"."
        )
    prompt = COMMON_TEMPLATE.format(goal=goal.strip("\n"))
    if condition == "told":
        prompt += "\n" + TOLD_PROCEDURE[family] + "\n"
    elif condition == "doc":
        prompt += "\n" + doc_text.strip("\n") + "\n"
    return prompt

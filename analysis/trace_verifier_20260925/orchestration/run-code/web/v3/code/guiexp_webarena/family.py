"""The Reddit intent-template family: CommentPost.

One Reddit/postmill intent template (the AutoRPA WebArena Reddit comment
domain):

    "Leave a comment on the post titled '<title>' in the <forum> forum,
     saying '<text>'."

Parameter generator: seed-deterministic bindings; forum drawn by hash from
the forums that exist (DB), target post drawn from the forum's real post
corpus (DB, the 25 newest posts -- the first page of the forum's "new"
listing), comment text carries a unique marker (collision-free across
bindings and checkable).

Site-shape note (wave-4 finding): in postmill the title link of a listing
row NEVER opens the post's own page -- it opens the post's image
(/submission_images/...) or its external url. The post's own page (the one
with the comment box) opens from the row's comments link ("No comments" /
"N comments", href /f/<forum>/<id>/<slug>). The checker does not care which
post type it is; programs and agents reach the post page through the
comments link.

Functional checker (DB truth inside the postmill container): the account
left a comment whose body equals the bound text on a post whose title
equals the bound title. Parameter matching = exact post-title match +
exact comment-body match; the marker token makes it exact.
"""

from __future__ import annotations

import hashlib

FAMILY = "CommentPost"
STEP_CAP_DEFAULT = 20

# The postmill account the env logs in (webarena's standard forum user).
USERNAME = "MarvelsGrantMan136"

_CACHE: dict = {"posts": {}, "forums": None}


# ----------------------------------------------------------- goal template

def goal_text(task) -> str:
    p = task.params
    return (
        f"Leave a comment on the post titled \"{p['title']}\" in the "
        f"{p['forum']} forum, saying \"{p['text']}\"."
    )


def goal_template(binding: dict) -> str:
    return (
        "Leave a comment on the post titled \"<title>\" in the <forum> "
        "forum, saying \"<text>\"."
    )


def task_for(family: str, condition: str, seed: int, env=None):
    if family != FAMILY:
        raise ValueError(f"unknown family {family!r}")
    from .env import WebArenaTask

    params = instance_params(family, seed, env=env)
    return WebArenaTask(family=family, condition=condition, seed=seed, params=params)


# ------------------------------------------------------- parameter generator

def _live_forums(env) -> list[str]:
    """Forums that exist, once per process (deterministic: by forum id)."""
    if _CACHE["forums"] is None:
        rows = env.db_rows_typed(
            "select name from forums order by id",
            ["name"],
        )
        if not rows:
            raise RuntimeError("no forums found in the site DB")
        _CACHE["forums"] = [r["name"] for r in rows]
    return _CACHE["forums"]


def corpus_posts(env, forum: str) -> list[dict]:
    """(id, title) of the forum's 25 NEWEST posts (the first page of the
    forum's new listing), cached per run. DB channel. Every drawn post is on
    /f/<forum>/new, page 1."""
    if forum in _CACHE["posts"]:
        return _CACHE["posts"][forum]
    rows = env.db_rows_typed(
        "select id, title from submissions "
        f"where forum_id = (select id from forums where name = '{_sql_esc(forum)}') "
        "order by id desc limit 25",
        ["id", "title"],
    )
    posts = [{"id": r["id"], "title": r["title"]} for r in rows]
    if not posts:
        raise ValueError(f"no posts found for forum {forum}")
    _CACHE["posts"][forum] = posts
    return posts


def instance_params(family: str, seed: int, env=None) -> dict:
    """A seed-deterministic binding: forum, a real post title, a unique
    comment text. Needs a live env (the post corpus is the site's own)."""
    if family != FAMILY:
        raise ValueError(f"unknown family {family!r}")
    if env is None:
        raise RuntimeError("instance_params needs a live env (post corpus)")
    forums = _live_forums(env)
    # forum by seed hash: stable across stages AND across corpus changes
    h = hashlib.sha256(f"guiexp_webarena:instance:{seed}".encode()).digest()
    forum = forums[h[0] % len(forums)]
    posts = corpus_posts(env, forum)
    # post draw: a second slice of the same digest -> deterministic
    post = posts[int.from_bytes(h[1:9], "big") % len(posts)]
    text = f"guiexp-{seed}-{h[9:13].hex()}"
    return {"forum": forum, "title": post["title"], "text": text}


# ----------------------------------------------------- binding <-> params

BINDING_FIELDS = ("forum", "title", "text")
INT_FIELDS = ()


def params_to_binding(family: str, params: dict) -> dict:
    return {field: str(params.get(field, "")) for field in BINDING_FIELDS}


def binding_to_params(family: str, binding: dict) -> dict:
    return {field: str(binding.get(field, "")) for field in BINDING_FIELDS}


def binding_fields(family: str) -> tuple:
    return BINDING_FIELDS


# ------------------------------------------------------ functional checker

def check(env, task) -> bool:
    """DB truth: our account left a comment with exactly the bound text on
    exactly the post with the bound title."""
    p = task.params
    sql = (
        "select c.body from comments c "
        "join users u on u.id = c.user_id "
        "join submissions s on s.id = c.submission_id "
        f"where u.username = '{USERNAME}' "
        f"and s.title = '{_sql_esc(p['title'])}' "
        f"and c.body = '{_sql_esc(p['text'])}' "
        "limit 1"
    )
    try:
        rows = env.db_rows_typed(sql, ["body"])
    except RuntimeError:
        return False
    return bool(rows)


def _sql_esc(text: str) -> str:
    return text.replace("\\", "\\\\").replace("'", "''")


# ------------------------------------------------------------ episode reset

def reset_for_episode(env, task) -> None:
    """Remove the account's own guiexp-* comments (all forums), so a retry
    episode starts clean. DB channel; the agent never sees this. The site
    auto-upvotes a freshly posted comment (a comment_votes row), so the
    votes are deleted first or the FK blocks the comment delete."""
    sql = (
        "select c.id from comments c "
        "join users u on u.id = c.user_id "
        f"where u.username = '{USERNAME}' and c.body like 'guiexp-%'"
    )
    rows = env.db_rows_typed(sql, ["id"])
    ids = ",".join(row["id"] for row in rows)
    if ids:
        # FK order: replies -> auto-upvote -> notification -> the comment
        env.db_rows(f"delete from comments where parent_id in ({ids})")
        env.db_rows(f"delete from comment_votes where comment_id in ({ids})")
        env.db_rows(f"delete from notifications where comment_id in ({ids})")
        env.db_rows(f"delete from comments where id in ({ids})")
    try:
        env.page.goto(f"{env.reddit_url}/f/{task.params['forum']}", timeout=30000)
        env.page.wait_for_load_state("domcontentloaded")
    except Exception:  # noqa: BLE001
        pass

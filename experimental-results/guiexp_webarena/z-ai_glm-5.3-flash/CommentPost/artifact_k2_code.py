def program(device, binding: dict) -> bool:
    """
    Leave a comment on the post titled binding['title'] in the binding['forum']
    forum, with body binding['text'].

    Flow (shared by all recordings of this family):
      1. Search the site for the post by its title.
      2. On the search results page, open the post page via the row's
         comments link ('N comments' / 'No comments', href under /f/...).
         (The row's title link opens the image/external URL, never the
         post page, so it is deliberately not used.)
      3. Type the comment into the comment textarea, click the
         'Formatting help' toggle, re-enter the text (editor is refreshed),
         then submit with the 'Post' button.
      4. Verify the comment text is rendered on the page.
    """
    import re
    from urllib.parse import quote

    forum = str(binding["forum"]).strip()
    title = str(binding["title"]).strip()
    text = str(binding["text"])
    if not title:
        raise RuntimeError("binding['title'] is required")
    if not forum:
        raise RuntimeError("binding['forum'] is required")

    def _norm(s):
        return re.sub(r"\s+", " ", (s or "")).strip()

    def _slug(s):
        return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50].rstrip("-")

    # ---------- 1. find the search box and search for the post ----------
    def _find_search_box():
        return (device.find(tag="input", name="q", editable=True)
                or device.find(tag="input", aria="Search query", editable=True)
                or device.find(tag="input", placeholder="Search", editable=True))

    box = _find_search_box()
    tries = 0
    while box is None and tries < 3:
        device.scroll()
        box = _find_search_box()
        tries += 1

    if box is not None:
        device.input_text(title, index=box)
        btn = (device.find(tag="button", aria="Search")
               or device.find(tag="button", text="Search")
               or device.find(tag="button", contains="Search"))
        if btn is not None:
            device.click(index=btn)
        else:
            device.keyboard_enter()
        device.settle(2)

    # Make sure we really are on the search results page for this title.
    cur = device.current_url() or ""
    if "/search" not in cur:
        device.goto("/search?q=" + quote(title))
        device.settle(2)

    # ---------- 2. locate the post's comments link on the results page ----------
    def _comments_link():
        els = device.elements()
        anchors = [e for e in els if e.get("tag") == "a"]
        tnorm = _norm(title).lower()
        probes = [tnorm]
        for cut in (80, 60, 40, 25, 15):
            if len(tnorm) > cut:
                probes.append(tnorm[:cut].strip())

        # positions of anchors whose text matches the title (or a prefix)
        title_pos = []
        for i, e in enumerate(anchors):
            tx = _norm(e.get("text")).lower()
            if not tx:
                continue
            if any(p and p in tx for p in probes):
                title_pos.append(i)

        # (a) walk the row: the comments link sits shortly after the title link
        for t in title_pos:
            for j in range(t + 1, min(t + 11, len(anchors))):
                e = anchors[j]
                href = e.get("href") or ""
                tx = _norm(e.get("text")).lower()
                if "comment" in tx and "/f/" in href:
                    return e

        # (b) fallback: score every comments link by forum/slug hints
        slug = _slug(title)
        slug_probes = [s for s in (slug, slug[:40], slug[:25]) if len(s) >= 8]
        best, best_score = None, -1
        for e in anchors:
            href = (e.get("href") or "").lower()
            tx = _norm(e.get("text")).lower()
            if "comment" not in tx or "/f/" not in href:
                continue
            score = 1
            if forum and ("/f/%s/" % forum) in href:
                score += 2
            if any(sp in href for sp in slug_probes):
                score += 3
            if score > best_score:
                best, best_score = e, score
        return best

    link = _comments_link()
    if link is None:
        # retry the search with progressively shorter queries
        for q in (_norm(title)[:60], _norm(title)[:40], _norm(title)[:25]):
            q = q.strip()
            if len(q) < 8:
                continue
            device.goto("/search?q=" + quote(q))
            device.settle(2)
            link = _comments_link()
            if link is not None:
                break
    if link is None:
        raise LookupError(
            "Comments link for the post titled %r not found on the search "
            "results page (%s)" % (title, device.current_url() or "")
        )
    device.click(index=link["index"])
    device.settle(2)

    # ---------- 3. fill in the comment textarea ----------
    def _comment_box():
        return (device.find(tag="textarea", aria="Comment", editable=True)
                or device.find(tag="textarea", name="comment", editable=True)
                or device.find(tag="textarea", editable=True))

    ta = _comment_box()
    if ta is None:
        raise LookupError(
            "Comment textarea (aria='Comment') not found on the post page (%s)"
            % (device.current_url() or "")
        )
    device.input_text(text, index=ta)

    # ---------- 4. 'Formatting help' toggle (present in both recordings) ----------
    fh = (device.find(tag="label", text="Formatting help")
          or device.find(tag="label", contains="Formatting help")
          or device.find(tag="a", contains="Formatting help"))
    if fh is not None:
        device.click(index=fh)
        device.settle(1)
        # the editor may be refreshed by the toggle; make sure the text is in it
        ta2 = _comment_box()
        if ta2 is None:
            raise LookupError(
                "Comment textarea disappeared after clicking 'Formatting help' "
                "on %s" % (device.current_url() or "")
            )
        device.input_text(text, index=ta2)

    # ---------- 5. submit the comment with the 'Post' button ----------
    btn = (device.find(tag="button", text="Post")
           or device.find(tag="button", aria="Post")
           or device.find(tag="button", text="Post comment")
           or device.find(tag="button", contains="Post"))
    if btn is None:
        raise LookupError(
            "Comment submit button ('Post') not found on the post page (%s)"
            % (device.current_url() or "")
        )
    device.click(index=btn)
    device.settle(3)

    # ---------- 6. verify the comment is rendered on the page ----------
    page = _norm(device.page_text() or "")
    needles = [_norm(text)]
    for ln in text.splitlines():
        ln = _norm(ln)
        if ln:
            needles.append(ln)
    needles = [n for n in needles if n]
    if not any(n in page for n in needles):
        raise RuntimeError(
            "Comment %r is not visible on the page after posting (%s)"
            % (text, device.current_url() or "")
        )
    return True

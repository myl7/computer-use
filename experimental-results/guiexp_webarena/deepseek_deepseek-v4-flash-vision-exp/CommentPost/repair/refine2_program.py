def program(device, binding: dict) -> bool:
    from urllib.parse import urlparse

    forum = binding["forum"]
    title = binding["title"]
    text = binding["text"]
    title_norm = title.strip()

    def base_url():
        cur = device.current_url() or ""
        p = urlparse(cur)
        if p.scheme and p.netloc and "localhost" not in p.netloc and "127.0.0.1" not in p.netloc:
            return "http://localhost:9999"
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
        return "http://localhost:9999"

    def is_title_match(t):
        if not t:
            return False
        t_clean = t.strip().rstrip('…').rstrip('.').strip()
        if not t_clean:
            return False
        if t_clean == title_norm:
            return True
        # Listings may truncate long titles.  A truncated title is a
        # non-trivial substring/prefix of the full title.
        if len(t_clean) >= 10 and t_clean in title_norm:
            return True
        return False

    def find_comments_link():
        els = device.elements()
        best_idx = None
        best_len = -1

        # Find the best title-link match (prefer the longest matching text).
        for i, e in enumerate(els):
            if e.get("tag") != "a":
                continue
            t = (e.get("text") or "").strip()
            if is_title_match(t):
                if len(t) > best_len:
                    best_len = len(t)
                    best_idx = i

        if best_idx is None:
            return None

        # The row's title link may open the image/external URL. The comments
        # link after it opens the post page with the comment box.
        for i in range(best_idx, len(els)):
            e = els[i]
            if e.get("tag") == "a":
                t = (e.get("text") or "").lower()
                href = e.get("href") or ""
                if "comment" in t and (href.startswith("/f/") or f"/f/{forum}/" in href):
                    return e.get("index", i)

        return None

    base = base_url()
    comments_idx = None

    # Try the forum's default page, then its /new listing.
    for path in (f"/f/{forum}", f"/f/{forum}/new"):
        device.goto(f"{base}{path}")
        device.wait()
        device.settle(1)
        comments_idx = find_comments_link()
        if comments_idx is not None:
            break

    # If the post isn't in the listings, search for it using the site's search box.
    if comments_idx is None:
        search_idx = device.find(tag="input", aria="Search query")
        if search_idx is None:
            search_idx = device.find(tag="input", name="q")
        if search_idx is None:
            raise RuntimeError("Could not find search box to search for the post")
        device.input_text(title, index=search_idx)
        device.keyboard_enter()
        device.wait()
        device.settle(1)
        comments_idx = find_comments_link()
        if comments_idx is None:
            raise RuntimeError(f"Could not find post titled {title!r} in forum {forum!r}")

    if comments_idx is None:
        raise RuntimeError(f"Could not find post titled {title!r} in forum {forum!r}")

    device.click(index=comments_idx)
    device.wait()
    device.settle(1)

    textarea_idx = device.find(tag="textarea", aria="Comment", editable=True)
    if textarea_idx is None:
        textarea_idx = device.find(tag="textarea", placeholder="Comment", editable=True)
    if textarea_idx is None:
        textarea_idx = device.find(tag="textarea", editable=True)
    if textarea_idx is None:
        raise RuntimeError("Could not find the comment textarea on the post page")

    device.input_text(text, index=textarea_idx)

    # The live preview re-renders as you type, shifting element indices.
    # Wait for it to settle before locating the submit button so the index
    # we click is not stale.
    device.settle(1)

    post_idx = device.find(tag="button", text="Post")
    if post_idx is None:
        post_idx = device.find(tag="button", contains="Post")
    if post_idx is None:
        post_idx = device.find(text="Post")
    if post_idx is None:
        raise RuntimeError("Could not find the 'Post' button to submit the comment")

    device.click(index=post_idx)
    device.wait()
    device.settle(1)

    return True

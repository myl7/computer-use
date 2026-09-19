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

    def find_comments_link():
        els = device.elements()
        start = None

        # Prefer an exact title-link match; fall back to a containing match.
        for i, e in enumerate(els):
            if e.get("tag") == "a":
                t = (e.get("text") or "").strip()
                if t == title_norm:
                    start = i
                    break

        if start is None:
            for i, e in enumerate(els):
                if e.get("tag") == "a":
                    t = (e.get("text") or "").strip()
                    if title_norm in t:
                        start = i
                        break

        if start is None:
            return None

        # The row's title link opens the external/image URL. The comments
        # link after it opens the post page with the comment box.
        for i in range(start, len(els)):
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

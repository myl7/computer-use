def program(device, binding: dict) -> bool:
    forum = binding["forum"]
    title = binding["title"]
    text = binding["text"]

    # Navigate to the forum page
    device.goto(f"/f/{forum}")
    device.wait()

    # Find the post row: locate the title link in the listing
    els = device.elements()
    title_idx = None
    for i, e in enumerate(els):
        if e.get("tag") == "a" and title in (e.get("text") or ""):
            title_idx = i
            break
    if title_idx is None:
        # Try exact match as a fallback
        for i, e in enumerate(els):
            if e.get("tag") == "a" and (e.get("text") or "").strip() == title:
                title_idx = i
                break
    if title_idx is None:
        raise LookupError(f"Could not find post titled {title!r} on /f/{forum}")

    # Find the comments link in the same row (after the title link)
    comments_idx = None
    for i in range(title_idx + 1, len(els)):
        e = els[i]
        if e.get("tag") == "a":
            text_lower = (e.get("text") or "").lower()
            href = e.get("href") or ""
            if "comment" in text_lower and href.startswith("/f/"):
                comments_idx = i
                break
    if comments_idx is None:
        raise LookupError(f"Could not find comments link for post {title!r}")

    # Click the comments link to open the post page
    device.click(index=comments_idx)
    device.wait()

    # Find the comment textarea
    textarea_idx = None
    try:
        textarea_idx = device.find(aria="Comment", editable=True)
    except Exception:
        pass
    if textarea_idx is None:
        try:
            textarea_idx = device.find(placeholder="Comment", editable=True)
        except Exception:
            pass
    if textarea_idx is None:
        try:
            textarea_idx = device.find(tag="textarea", editable=True)
        except Exception:
            pass
    if textarea_idx is None:
        raise LookupError("Could not find comment textarea on post page")

    # Enter the comment text
    device.input_text(text, index=textarea_idx)

    # Optionally toggle the "Formatting help" panel (present in recordings, not strictly required)
    try:
        help_idx = device.find(tag="label", contains="Formatting help", editable=False)
        if help_idx is not None:
            device.click(index=help_idx)
            device.settle(1)
    except Exception:
        pass

    # Find and click the "Post" submit button
    post_idx = None
    try:
        post_idx = device.find(tag="button", text="Post")
    except Exception:
        pass
    if post_idx is None:
        try:
            post_idx = device.find(tag="button", contains="Post")
        except Exception:
            pass
    if post_idx is None:
        raise LookupError("Could not find 'Post' submit button on post page")

    device.click(index=post_idx)
    device.wait()

    return True

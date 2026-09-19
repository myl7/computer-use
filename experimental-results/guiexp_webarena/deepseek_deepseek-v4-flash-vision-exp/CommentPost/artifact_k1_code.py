def program(device, binding: dict) -> bool:
    forum = binding["forum"]
    title = binding["title"]
    text = binding["text"]

    # Navigate to the forum page
    device.goto(f"/f/{forum}")
    device.wait()

    def find_comments_link():
        """Walk the current page's element list to find the comments link
        that belongs to the row containing the target post title."""
        els = device.elements()
        start = next((i for i, e in enumerate(els) if title in (e.get("text") or "")), None)
        if start is None:
            return None
        for i in range(start, len(els)):
            e = els[i]
            if e.get("tag") == "a":
                link_text = (e.get("text") or "").lower()
                href = e.get("href") or ""
                if "comment" in link_text and href.startswith("/f/"):
                    return i
        return None

    # Try the default forum page first, then the newest posts page
    idx = find_comments_link()
    if idx is None:
        device.goto(f"/f/{forum}/new")
        device.wait()
        idx = find_comments_link()
        if idx is None:
            raise RuntimeError(
                f"Could not find the comments link for post titled {title!r} "
                f"in forum {forum!r}"
            )

    device.click(index=idx)
    device.wait()

    # Locate the comment textarea on the post page
    textarea_id = device.find(tag="textarea", aria="Comment", editable=True)
    if textarea_id is None:
        raise RuntimeError("Could not find the comment textarea on the post page")
    device.input_text(text, index=textarea_id)

    # Locate and click the Post button
    post_button = device.find(text="Post", tag="button")
    if post_button is None:
        raise RuntimeError("Could not find the Post button on the comment form")
    device.click(index=post_button)
    device.wait()

    return True

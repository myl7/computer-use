def program(device, binding: dict) -> bool:
    """
    Leave a comment on the post titled binding['title'] in the
    binding['forum'] forum, saying binding['text'].

    Strategy (matches the site's quirks):
      * Find the post through the site search (or its forum listing).
      * In the matching listing row, NEVER click the title anchor (it opens
        the post's image/external url). Instead click the row's comments
        link ('No comments' / 'N comments', href starting with /f/), which
        opens the post's own page.
      * Type the comment into the big comment textarea and submit it with
        the 'Post' button.
    """
    from urllib.parse import quote

    forum = str(binding.get('forum') or '').strip()
    title = str(binding.get('title') or '').strip()
    text = str(binding.get('text') or '')
    if not title:
        raise ValueError("binding['title'] (post title) is required")
    if not text:
        raise ValueError("binding['text'] (comment body) is required")

    # ------------------------------------------------------------------ #
    # helpers                                                            #
    # ------------------------------------------------------------------ #
    def attr(el, key):
        """Read an attribute from an element-list entry (dict or object)."""
        if isinstance(el, dict):
            return el.get(key)
        return getattr(el, key, None)

    def elem_id(el, pos=None):
        """Element id suitable for device.click(index=...)."""
        i = attr(el, 'index')
        if isinstance(i, int):
            return i
        if pos is not None:
            return pos
        return el

    def title_needles():
        """Anchors matching the post title: full title first, then shorter
        fragments in case a listing truncates long titles."""
        needles = [title]
        words = title.split()
        if len(words) > 6:
            needles.append(' '.join(words[:6]))
        if len(words) > 3:
            needles.append(' '.join(words[:3]))
        seen, out = set(), []
        for n in needles:
            if n and n not in seen:
                seen.add(n)
                out.append(n)
        return out

    def locate_comments_link():
        """Locate the target post's row on the current listing/search page
        and return that row's comments link (or (None, None))."""
        # Preferred: if the binding carries the post url, pin the comments
        # link via the post id it contains.
        url = binding.get('url')
        if url:
            post_id = str(url).rstrip('/').split('/')[-1]
            if post_id:
                el = device.find(tag='a', href_contains=post_id,
                                 contains='comment')
                if el is not None:
                    return el, None

        els = device.elements()
        entries = [(elem_id(el, p), el) for p, el in enumerate(els)]

        # 1) the row's title anchor (full title first, then fragments)
        title_idx = None
        for needle in title_needles():
            for i, el in entries:
                if attr(el, 'tag') == 'a' and needle in (attr(el, 'text') or ''):
                    title_idx = i
                    break
            if title_idx is not None:
                break
        if title_idx is None:
            return None, None

        # 2) the comments link of that row: first /f/... anchor after the
        #    title whose label mentions 'comment' ('N comments'/'No comments')
        prefixes = []
        if forum:
            prefixes.append('/f/' + forum)
        prefixes.append('/f/')
        for pfx in prefixes:
            for i, el in entries:
                if i <= title_idx or attr(el, 'tag') != 'a':
                    continue
                href = attr(el, 'href') or ''
                label = (attr(el, 'text') or '')
                if pfx in href and 'comment' in label.lower():
                    return el, i
        return None, None

    def open_post_page():
        el, i = locate_comments_link()
        if el is None:
            return False
        device.click(index=elem_id(el, i))
        return True

    # ------------------------------------------------------------------ #
    # 1. reach the post's own page                                       #
    # ------------------------------------------------------------------ #
    attempts = ['/search?q=' + quote(q) for q in title_needles()]
    if forum:
        attempts += ['/f/%s/new' % forum,
                     '/f/%s/new?page=2' % forum,
                     '/f/%s' % forum]

    opened = False
    for url in attempts:
        device.goto(url)
        device.settle(2)
        if open_post_page():
            opened = True
            break
    if not opened:
        raise LookupError(
            "Could not find the post titled %r (forum=%r) on any "
            "search/listing page" % (title, forum))

    device.settle(1)

    # ------------------------------------------------------------------ #
    # 2. write the comment and submit it                                 #
    # ------------------------------------------------------------------ #
    def comment_box():
        box = device.find(tag='textarea', aria='Comment', editable=True)
        if box is None:
            box = device.find(tag='textarea', editable=True)
        return box

    def submit_button():
        btn = device.find(tag='button', text='Post')
        if btn is None:
            btn = device.find(tag='button', contains='Post')
        return btn

    def try_submit():
        box = comment_box()
        if box is None:
            raise LookupError('Comment textarea not found on the post page')
        device.input_text(text, index=box)
        btn = submit_button()
        if btn is None:
            raise LookupError(
                "Comment submit button ('Post') not found on the post page")
        device.click(index=btn)
        device.settle(2)
        return text in (device.page_text() or '')

    if try_submit():
        return True

    # The recorded run shows the form sometimes needs the text re-entered
    # before the 'Post' click takes effect: retry once.
    device.settle(1)
    if text in (device.page_text() or ''):
        return True
    if try_submit():
        return True
    return False

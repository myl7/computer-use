import re
from urllib.parse import quote


def program(device, binding: dict) -> bool:
    forum = str(binding.get('forum') or '').strip()
    title = str(binding.get('title') or '').strip()
    text = str(binding.get('text') or '')

    if not forum:
        raise ValueError("binding['forum'] (forum short name) is required")
    if not title:
        raise ValueError("binding['title'] (post title) is required")

    def norm(s):
        return re.sub(r'\s+', '', (s or '')).lower()

    ntitle = norm(title)
    ntitle_prefix = ntitle[:40]
    base_slug = re.sub(r'[^a-z0-9]+', '-', title.lower()).strip('-')
    slugs = [base_slug[:50].rstrip('-'), base_slug[:40].rstrip('-'),
             base_slug[:25].rstrip('-')]
    slugs = [s for s in slugs if len(s) >= 5]
    forum_l = forum.lower()

    def first(*lookups):
        for lk in lookups:
            try:
                el = device.find(**lk)
            except Exception:
                el = None
            if el is not None:
                return el
        return None

    def href_path(href):
        href = href or ''
        i = href.find('/f/')
        return (href[i:] if i >= 0 else href).lower()

    def post_prefix_of(href):
        """'/f/<forum>/<post id>' prefix identifying a link to the post page."""
        m = re.match(r'^/f/%s/\d+' % re.escape(forum_l), href_path(href))
        return m.group(0) if m else None

    def slug_hit(href):
        h = href_path(href)
        return any(s in h for s in slugs)

    def comment_box():
        return first(
            dict(tag='textarea', aria='Comment', editable=True),
            dict(tag='textarea', name='comment[comment]', editable=True),
            dict(tag='textarea', editable=True),
        )

    def locate_comments_link():
        """Walk the element list, find the row whose title matches the target
        post, and return (element, post_prefix) for that row's comments link
        ('No comments' / 'N comments', href '/f/<forum>/<id>/...').  The row's
        title link opens the image/external URL and is never used."""
        els = device.elements()
        anchors = [e for e in els if e.get('tag') == 'a']

        title_idx = None
        for e in anchors:
            txt = norm(e.get('text'))
            if ntitle and (ntitle in txt or (ntitle_prefix and ntitle_prefix in txt)):
                title_idx = e.get('index')
                break

        cands = []
        for e in anchors:
            href = e.get('href') or ''
            prefix = post_prefix_of(href)
            if prefix is None:
                continue
            txt = norm(e.get('text'))
            comment_hit = 'comment' in txt          # 'No comments' / 'N comments'
            slug_matched = slug_hit(href)
            if not (comment_hit or slug_matched):
                continue
            score = 0
            if title_idx is not None and (e.get('index') or 0) > title_idx:
                score += 2                          # link of the matching row
            if slug_matched:
                score += 2                          # href slug from the title
            if comment_hit:
                score += 1
            cands.append((-score, e.get('index') or 0, e, prefix))

        if not cands:
            return None
        cands.sort(key=lambda c: (c[0], c[1]))
        best = cands[0]
        if best[0] > -2 and len(cands) > 1:
            return None  # several rows, none convincingly the target
        return best[2], best[3]

    def open_post_page(hit):
        """Open the post's own page via its comments link.  Clicks always use
        a freshly resolved element id (or the snapshot index only while the
        page is provably unchanged) — never a stale snapshot dict, whose
        recorded attributes (e.g. a truncated href) can fail to re-match
        after the page (re)loads and make the click raise
        'no element with id'."""
        e, prefix = hit
        txt = (e.get('text') or '').strip()
        specs = []
        if txt:
            specs.append(dict(tag='a', text=txt, href_contains=prefix))
        specs.append(dict(tag='a', contains='comment', href_contains=prefix))
        specs.append(dict(tag='a', href_contains=prefix))

        def box_present():
            try:
                return comment_box() is not None
            except Exception:
                return False

        for _ in range(3):
            el = None
            for spec in specs:
                try:
                    el = device.find(**spec)
                except Exception:
                    el = None
                if el is not None:
                    break
            if el is None:
                el = e.get('index')  # no navigation yet: index still valid
            url0 = device.current_url()
            try:
                device.click(index=el)
            except Exception:
                device.settle(1)
                continue
            device.settle(2)
            if box_present():
                return True
            device.settle(2)
            if box_present():
                return True
            if device.current_url() != url0:
                break  # navigated away; further clicks would use stale ids

        if box_present():
            return True

        # Last resort: the post's id URL; the site resolves it to the post.
        try:
            device.goto(prefix)
            device.settle(2)
        except Exception:
            pass
        return box_present()

    # 1) Locate the post via the site search (full title first, then
    #    progressively shorter prefixes as fallbacks).
    hit = None
    queries = []
    for q in (title, title[:60].strip(), title[:40].strip(), title[:25].strip()):
        if q and q not in queries:
            queries.append(q)
    for q in queries:
        try:
            device.goto('/search?q=' + quote(q))
            device.settle(2)
        except Exception:
            continue
        hit = locate_comments_link()
        if hit:
            break

    # 2) Fallback: scan the forum's own listings for the post row.
    if hit is None:
        for path in ('/f/%s/new' % forum, '/f/%s' % forum):
            try:
                device.goto(path)
                device.settle(2)
            except Exception:
                continue
            hit = locate_comments_link()
            if hit:
                break
    if hit is None:
        raise LookupError(
            'Post titled %r not found via site search or /f/%s listings'
            % (title, forum))

    # 3) Open the post page through the row's comments link.
    if not open_post_page(hit):
        raise LookupError('Could not open the post page for %r (url=%r)'
                          % (title, device.current_url()))
    post_url = device.current_url()

    # 4) Enter the comment and submit it with the 'Post' button.
    ntext = norm(text)
    for attempt in range(3):
        box = comment_box()
        if box is None:
            try:
                device.goto(post_url)
                device.settle(2)
            except Exception:
                pass
            box = comment_box()
        if box is None:
            raise LookupError('Comment textarea not found on post page (url=%r)'
                              % (device.current_url(),))

        device.input_text(text, index=box)

        # Mirror the recorded flow: toggle 'Formatting help', then re-enter
        # the text before submitting (toggle skipped if the label is absent).
        help_label = first(dict(tag='label', text='Formatting help'))
        if help_label is not None:
            try:
                device.click(index=help_label)
                device.settle(1)
            except Exception:
                pass
            box = comment_box()
            if box is not None:
                device.input_text(text, index=box)

        btn = first(dict(tag='button', text='Post'),
                    dict(tag='button', contains='Post'))
        if btn is None:
            raise LookupError('Comment submit button labelled "Post" not found')
        try:
            device.click(index=btn)
        except Exception:
            # stale button reference: re-resolve it once and retry the click
            btn = first(dict(tag='button', text='Post'),
                        dict(tag='button', contains='Post'))
            if btn is None:
                raise LookupError(
                    'Comment submit button labelled "Post" not found')
            device.click(index=btn)
        device.settle(2)

        if ntext and ntext in norm(device.page_text()):
            return True
        device.settle(2)
        if ntext and ntext in norm(device.page_text()):
            return True

    raise RuntimeError('Could not verify the comment was posted (url=%r)'
                       % (device.current_url(),))

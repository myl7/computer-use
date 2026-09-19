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
    slugs = [base_slug[:50].rstrip('-'), base_slug[:40].rstrip('-'), base_slug[:25].rstrip('-')]
    slugs = [s for s in slugs if len(s) >= 5]

    def first(*lookups):
        for lk in lookups:
            try:
                el = device.find(**lk)
            except Exception:
                el = None
            if el is not None:
                return el
        return None

    def is_forum_href(href):
        href = (href or '').lower()
        return ('/f/%s/' % forum.lower()) in href or ('/f/%s' % forum.lower()) in href

    def locate_post_link():
        """Walk the element list, find the row whose title matches the target
        post, and return (element_id, href) of that row's comments link
        (never the title link, which opens the image/external URL)."""
        els = device.elements()
        anchors = [e for e in els if e.get('tag') == 'a']

        title_idx = None
        for e in anchors:
            txt = norm(e.get('text'))
            if ntitle and (ntitle in txt or (ntitle_prefix and ntitle_prefix in txt)):
                title_idx = e.get('index')
                break

        after, anywhere = [], []
        for e in anchors:
            href = e.get('href') or ''
            if not is_forum_href(href):
                continue
            txt = norm(e.get('text'))
            comment_hit = 'comment' in txt          # 'No comments' / 'N comments'
            slug_hit = any(s in href.lower() for s in slugs)
            if e.get('index') == title_idx:
                # the row's title link; only usable if it stays inside /f/
                if slug_hit or comment_hit:
                    anywhere.append((e, href))
                continue
            if not (comment_hit or slug_hit):
                continue
            item = (e, href)
            anywhere.append(item)
            if title_idx is not None and (e.get('index') or 0) > title_idx:
                after.append(item)

        for lst in (after, anywhere):
            if lst:
                return lst[0]
        return None

    def comment_box():
        return first(
            dict(tag='textarea', aria='Comment', editable=True),
            dict(tag='textarea', name='comment[comment]', editable=True),
            dict(tag='textarea', editable=True),
        )

    # 1) Locate the post via the site search (full title first, then
    #    progressively shorter prefixes as fallbacks).
    hit = None
    queries = []
    for q in (title, title[:60].strip(), title[:40].strip(), title[:25].strip()):
        if q and q not in queries:
            queries.append(q)
    for q in queries:
        device.goto('/search?q=' + quote(q))
        device.settle(2)
        hit = locate_post_link()
        if hit:
            break

    # 2) Fallback: scan the forum's own listings for the post row.
    if hit is None:
        for path in ('/f/%s/new' % forum, '/f/%s' % forum):
            device.goto(path)
            device.settle(2)
            hit = locate_post_link()
            if hit:
                break
    if hit is None:
        raise LookupError(
            'Post titled %r not found via site search or /f/%s listings' % (title, forum))

    # 3) Open the post page through the row's comments link.
    el_id, href = hit
    device.click(index=el_id)
    device.settle(2)

    box = comment_box()
    if box is None and href:
        # Recovery: go straight to the post page URL from the row.
        device.goto(href)
        device.settle(2)
        box = comment_box()
    if box is None:
        raise LookupError('Comment textarea not found on post page (url=%r)'
                          % (device.current_url(),))

    device.input_text(text, index=box)

    # Mirror the recorded flow: toggle 'Formatting help', then re-enter the
    # text before submitting (toggle skipped if the label is absent).
    help_label = first(dict(tag='label', text='Formatting help'))
    if help_label is not None:
        device.click(index=help_label)
        device.settle(1)
        box = comment_box()
        if box is None:
            raise LookupError('Comment textarea missing after toggling formatting help')

    device.input_text(text, index=box)

    btn = first(dict(tag='button', text='Post'), dict(tag='button', contains='Post'))
    if btn is None:
        raise LookupError('Comment submit button labelled "Post" not found')
    device.click(index=btn)
    device.settle(2)

    page = norm(device.page_text())
    if norm(text) and norm(text) in page:
        return True
    raise RuntimeError('Could not verify the comment was posted (url=%r)'
                       % (device.current_url(),))

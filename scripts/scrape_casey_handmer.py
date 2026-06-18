#!/usr/bin/env python3
"""Scrape Casey Handmer's WordPress blog into a single organized markdown file.

Uses the public WordPress.com REST API (v1.1), which returns structured JSON
for every post (title, date, URL, categories, tags, excerpt). This avoids
fragile HTML scraping. Designed to run from an environment with open internet
access (e.g. a GitHub Actions runner).

Output: casey-handmer-blog.md
  - Metadata header (post count, date range)
  - Category index with counts
  - Full chronological index of every post (oldest -> newest)
  - Posts grouped by category, chronological, with excerpts
"""

import json
import re
import sys
import time
import urllib.request
import urllib.error
from collections import defaultdict
from datetime import datetime, timezone
from html import unescape

SITE = "caseyhandmer.wordpress.com"
API = f"https://public-api.wordpress.com/rest/v1.1/sites/{SITE}/posts/"
OUT = "casey-handmer-blog.md"
PER_PAGE = 100
FIELDS = "ID,date,modified,title,URL,short_URL,excerpt,categories,tags,slug,word_count,author"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            wait = 2 ** attempt
            print(f"  request failed ({e}); retrying in {wait}s", file=sys.stderr)
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch after retries: {url}")


def get_all_posts():
    """Fetch every published post using offset pagination."""
    posts = []
    offset = 0
    found = None
    while True:
        url = f"{API}?number={PER_PAGE}&offset={offset}&order=ASC&order_by=date&fields={FIELDS}"
        data = fetch(url)
        if found is None:
            found = data.get("found", 0)
            print(f"API reports {found} total posts", file=sys.stderr)
        batch = data.get("posts", [])
        if not batch:
            break
        posts.extend(batch)
        print(f"  fetched {len(posts)}/{found}", file=sys.stderr)
        offset += len(batch)
        if found and offset >= found:
            break
        if len(batch) < PER_PAGE:
            break
        time.sleep(0.3)
    return posts


TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def clean(text):
    if not text:
        return ""
    text = TAG_RE.sub(" ", text)
    text = unescape(text)
    text = WS_RE.sub(" ", text).strip()
    return text


def cat_names(post):
    cats = post.get("categories") or {}
    names = [c.get("name", "") for c in cats.values()]
    names = [n for n in names if n and n.lower() != "uncategorized"]
    return sorted(names) or ["Uncategorized"]


def tag_names(post):
    tags = post.get("tags") or {}
    return sorted(t.get("name", "") for t in tags.values() if t.get("name"))


def fmt_date(iso):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return (iso or "")[:10]


def main():
    posts = get_all_posts()
    # De-dupe by ID and sort chronologically (oldest first)
    seen = {}
    for p in posts:
        seen[p["ID"]] = p
    posts = sorted(seen.values(), key=lambda p: p.get("date", ""))
    print(f"Total unique posts: {len(posts)}", file=sys.stderr)

    by_cat = defaultdict(list)
    for p in posts:
        for c in cat_names(p):
            by_cat[c].append(p)

    dates = [fmt_date(p.get("date", "")) for p in posts if p.get("date")]
    date_range = f"{dates[0]} to {dates[-1]}" if dates else "n/a"
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines = []
    lines.append("# Casey Handmer's Blog — Complete Archive\n")
    lines.append(
        "_Space, Travel, Technology, 3D Printing, Energy, Writing_\n"
    )
    lines.append(f"- **Source:** <https://{SITE}/>")
    lines.append(f"- **Total posts:** {len(posts)}")
    lines.append(f"- **Date range:** {date_range}")
    lines.append(f"- **Categories:** {len(by_cat)}")
    lines.append(f"- **Generated:** {generated} via the WordPress.com REST API")
    lines.append("")
    lines.append("---\n")

    # Category index
    lines.append("## Categories\n")
    for cat in sorted(by_cat, key=lambda c: (-len(by_cat[c]), c.lower())):
        anchor = re.sub(r"[^a-z0-9]+", "-", cat.lower()).strip("-")
        lines.append(f"- [{cat}](#cat-{anchor}) — {len(by_cat[cat])} posts")
    lines.append("")
    lines.append("---\n")

    # Chronological master index
    lines.append("## Chronological Index (all posts, oldest → newest)\n")
    lines.append("| # | Date | Title | Categories |")
    lines.append("| --- | --- | --- | --- |")
    for i, p in enumerate(posts, 1):
        title = clean(p.get("title")) or "(untitled)"
        url = p.get("URL", "")
        cats = ", ".join(cat_names(p))
        lines.append(f"| {i} | {fmt_date(p.get('date',''))} | [{title}]({url}) | {cats} |")
    lines.append("")
    lines.append("---\n")

    # Posts grouped by category
    lines.append("## Posts by Category\n")
    for cat in sorted(by_cat, key=lambda c: (-len(by_cat[c]), c.lower())):
        anchor = re.sub(r"[^a-z0-9]+", "-", cat.lower()).strip("-")
        lines.append(f'<a id="cat-{anchor}"></a>')
        lines.append(f"### {cat} ({len(by_cat[cat])} posts)\n")
        for p in sorted(by_cat[cat], key=lambda p: p.get("date", "")):
            title = clean(p.get("title")) or "(untitled)"
            url = p.get("URL", "")
            date = fmt_date(p.get("date", ""))
            lines.append(f"#### {date} — [{title}]({url})")
            tags = tag_names(p)
            if tags:
                lines.append(f"_Tags: {', '.join(tags)}_")
            wc = p.get("word_count")
            excerpt = clean(p.get("excerpt"))
            if excerpt:
                lines.append("")
                lines.append(f"> {excerpt}")
            if wc:
                lines.append("")
                lines.append(f"_~{wc} words_")
            lines.append("")
        lines.append("---\n")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {OUT} ({len(posts)} posts, {len(by_cat)} categories)", file=sys.stderr)


if __name__ == "__main__":
    main()

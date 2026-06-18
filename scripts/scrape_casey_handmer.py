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
  - Posts grouped by category, chronological, with full post bodies
"""

import json
import re
import sys
import time
import urllib.request
import urllib.error

try:
    import html2text as _html2text
except ImportError:  # pragma: no cover - converter is optional but expected in CI
    _html2text = None
from collections import defaultdict
from datetime import datetime, timezone
from html import unescape

SITE = "caseyhandmer.wordpress.com"
API = f"https://public-api.wordpress.com/rest/v1.1/sites/{SITE}/posts/"
OUT = "casey-handmer-blog.md"
PER_PAGE = 100
FIELDS = "ID,date,modified,title,URL,short_URL,excerpt,content,categories,tags,slug,word_count,author"

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


# The blog tags only ~12% of posts and uses no real WordPress categories, so we
# derive a topic for every post from its title, tags, and excerpt. Categories are
# checked by weighted keyword scoring (tags weigh most, then title, then excerpt).
# Order here is also the tie-break priority (earlier = higher priority).
CATEGORIES = [
    ("Space & Spaceflight", [
        "space", "spacex", "nasa", "rocket", "launch", "orbit", "orbital",
        "satellite", "starship", "falcon", "lunar", "moon", "sls", "orion",
        "spaceflight", "propulsion", "reentry", "re-entry", "payload", "mass driver",
        "astronaut", "spacecraft", "rover", "telescope", "asteroid", "interstellar",
        "cislunar", "launch vehicle", "heavy lift",
    ]),
    ("Mars & Terraforming", [
        "mars", "martian", "terraform", "terraforming",
    ]),
    ("Energy & Climate", [
        "energy", "solar", "renewable", "climate", "hydrogen", "synthetic fuel",
        "electricity", "grid", "battery", "power plant", "decarbon", "carbon",
        "methane", "natural gas", "wind power", "nuclear", "geothermal", "fuel",
        "emissions", "electrolysis", "photovoltaic", "data center", "kardashev",
        "sustainability", "wildfire", "water",
    ]),
    ("AI & Software", [
        "ai", "artificial intelligence", "machine-learning", "machine learning",
        "software", "programming", "python", "algorithm", "neural", "gpt", "llm",
        "computer", "code", "coding", "deep learning", "automation", "robot",
    ]),
    ("Economics, Policy & Society", [
        "economics", "economy", "economic", "policy", "politics", "political", "tax",
        "taxation", "immigration", "finance", "inflation", "regulation", "housing",
        "government", "defense", "defence", "war", "geopolitics", "dynamism",
        "productivity", "law", "leadership", "history", "manufacturing", "industry",
        "trade", "capitalism", "abundance",
    ]),
    ("Physics & Science", [
        "physics", "quantum", "relativity", "astronomy", "astrophysics", "math",
        "mathematics", "exoplanet", "exoplanets", "theory", "cosmology", "science",
        "experiment", "thermodynamics", "engineering",
    ]),
    ("Startups & Building", [
        "startup", "founder", "company", "factory", "hardware", "production",
        "terraform industries", "business", "venture", "team", "hiring", "scaling",
    ]),
    ("Travel & Photography", [
        "travel", "photo", "photos", "photograph", "photography", "trip", "mongolia",
        "tasmania", "hike", "hiking", "mountain", "glacier", "road", "journey",
        "border", "russia", "russian", "city", "salton-sea", "nature", "camping",
    ]),
    ("Books & Writing", [
        "book", "books", "writing", "review", "fiction", "novel", "essay",
        "science-fiction", "reading", "author",
    ]),
]


def classify_post(post):
    """Assign a single primary topic by weighted keyword scoring."""
    title = clean(post.get("title")).lower()
    excerpt = clean(post.get("excerpt")).lower()
    tags = " ".join(tag_names(post)).lower().replace("-", " ")
    title_words = title.replace("-", " ")

    scores = {}
    for name, keywords in CATEGORIES:
        score = 0
        for kw in keywords:
            k = kw.replace("-", " ")
            if k in tags:
                score += 3
            if k in title_words:
                score += 2
            if k in excerpt:
                score += 1
        if score:
            scores[name] = score

    if not scores:
        return "Personal & Miscellaneous"
    best = max(scores.values())
    # tie-break by CATEGORIES priority order
    for name, _ in CATEGORIES:
        if scores.get(name) == best:
            return name
    return "Personal & Miscellaneous"


def _make_converter():
    if _html2text is None:
        return None
    h = _html2text.HTML2Text()
    h.body_width = 0          # don't hard-wrap paragraphs
    h.ignore_images = False   # keep image references
    h.ignore_links = False    # keep hyperlinks
    h.ignore_emphasis = False
    h.protect_links = True
    h.unicode_snob = True
    h.single_line_break = False
    return h


_CONVERTER = _make_converter()
_BLANKS_RE = re.compile(r"\n{3,}")


def body_markdown(post):
    """Convert a post's HTML content to clean Markdown, demoting headings so
    they nest under the per-post #### heading used in the document."""
    html = post.get("content") or ""
    if not html.strip():
        return ""
    if _CONVERTER is not None:
        md = _CONVERTER.handle(html)
    else:  # fallback: strip tags
        md = clean(html)
    # Demote any in-body headings (#, ##, ...) so they sit below the post's ####.
    md = re.sub(r"(?m)^(#{1,6})\s", lambda m: "#" * min(len(m.group(1)) + 4, 6) + " ", md)
    md = _BLANKS_RE.sub("\n\n", md).strip()
    return md


def cat_names(post):
    return [classify_post(post)]


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
            lines.append(f"#### {date} — {title}")
            meta = [f"[Original post]({url})"]
            tags = tag_names(p)
            if tags:
                meta.append(f"Tags: {', '.join(tags)}")
            wc = p.get("word_count")
            if wc:
                meta.append(f"~{wc} words")
            lines.append("_" + " · ".join(meta) + "_")
            lines.append("")
            body = body_markdown(p)
            if body:
                lines.append(body)
            else:
                excerpt = clean(p.get("excerpt"))
                if excerpt:
                    lines.append(f"> {excerpt}")
            lines.append("")
            lines.append("<br>")
            lines.append("")
        lines.append("---\n")

    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {OUT} ({len(posts)} posts, {len(by_cat)} categories)", file=sys.stderr)


if __name__ == "__main__":
    main()

import os
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from playwright.sync_api import sync_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "data" / "config.json"
SEEN_PATH = BASE_DIR / "data" / "seen.json"
FEEDS_DIR = BASE_DIR / "feeds"

FEEDS_DIR.mkdir(parents=True, exist_ok=True)

SITE_BASE = "https://x.com/i/lists/"


def load_json(path, default):
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def slug_list(list_id: str) -> str:
    return f"list-{list_id}.xml"


def iso_now():
    return datetime.now(timezone.utc).isoformat()


def build_rss(list_id: str, items: list[dict]) -> str:
    feed_title = f"X List {list_id}"
    feed_link = f"{SITE_BASE}{list_id}"
    feed_desc = f"Custom RSS for X List {list_id}"

    xml_items = []
    for item in items:
        title = escape((item.get("author") or "Post") + ": " + (item.get("text") or "")[:120])
        description = escape(item.get("text") or "")
        link = escape(item.get("url") or "")
        guid = escape(item.get("id") or item.get("url") or "")
        pub_date = escape(item.get("published") or iso_now())

        xml_items.append(f"""
    <item>
      <title>{title}</title>
      <description>{description}</description>
      <link>{link}</link>
      <guid>{guid}</guid>
      <pubDate>{pub_date}</pubDate>
    </item>""")

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>{escape(feed_title)}</title>
    <link>{escape(feed_link)}</link>
    <description>{escape(feed_desc)}</description>
    {''.join(xml_items)}
  </channel>
</rss>
"""


def extract_list_items(page, list_id: str):
    page.goto(f"{SITE_BASE}{list_id}", wait_until="networkidle", timeout=120000)
    page.wait_for_timeout(5000)

    items = []
    articles = page.locator("article")
    count = articles.count()

    for i in range(min(count, 30)):
        article = articles.nth(i)
        text = article.inner_text(timeout=5000).strip()

        links = article.locator("a").evaluate_all(
            """els => els.map(a => a.href).filter(Boolean)"""
        )

        tweet_url = None
        author = None

        for link in links:
            if re.search(r"x\\.com/.+/status/\\d+", link):
                tweet_url = link
                break

        if tweet_url:
            m = re.search(r"x\\.com/([^/]+)/status/(\\d+)", tweet_url)
            if m:
                author = m.group(1)
                tweet_id = m.group(2)
            else:
                tweet_id = tweet_url
        else:
            tweet_id = f"{list_id}-{i}-{hash(text)}"

        if not text:
            continue

        items.append({
            "id": tweet_id,
            "author": author or "unknown",
            "text": text,
            "url": tweet_url or f"{SITE_BASE}{list_id}",
            "published": iso_now()
        })

    return items


def main():
    config = load_json(CONFIG_PATH, {"lists": []})
    seen = load_json(SEEN_PATH, {})

    session_json = os.getenv("X_SESSION_JSON")
    if not session_json:
        raise RuntimeError("Missing X_SESSION_JSON secret")

    cookies = json.loads(session_json)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        context.add_cookies(cookies)
        page = context.new_page()

        for list_id in config["lists"]:
            try:
                items = extract_list_items(page, list_id)

                prev_seen = set(seen.get(list_id, []))
                merged = []

                for item in items:
                    merged.append(item)

                seen[list_id] = [item["id"] for item in merged[:100]]

                rss = build_rss(list_id, merged[:30])
                out_path = FEEDS_DIR / slug_list(list_id)
                out_path.write_text(rss, encoding="utf-8")

                print(f"Built feed for {list_id}: {out_path}")
            except Exception as e:
                print(f"Error on list {list_id}: {e}")

        browser.close()

    save_json(SEEN_PATH, seen)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
bandori.fans → knowledge base 增量同步脚本
- sitemap 拉取：bands / characters / songs / events / releases / voice-actors / venues / setlists / news
- RSS 拉取：news / events / releases（辅助 sitemap，覆盖短时效内容）
- 每日运行，增量 only，no_agent=true
"""

import json, os, re, sys, time, logging
from pathlib import Path
from datetime import datetime, timezone
from xml.etree import ElementTree as ET
from urllib.parse import urlparse, unquote
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import Counter

# ── 配置 ──────────────────────────────────────────
SITEMAP_INDEX = "https://bandori.fans/sitemap.xml"
BASE_URL = "https://bandori.fans"
LOCALE = "zh-Hans"
KB_ROOT = Path(os.path.expanduser("~/ai/ai/knowledge/bandori_fans"))
STATE_FILE = KB_ROOT / "_sync_state.json"
OUTPUT_DIR = KB_ROOT

MAX_PAGES_PER_RUN = 100

RSS_FEEDS = {
    "news":     f"{BASE_URL}/{LOCALE}/rss/news.xml",
    "events":   f"{BASE_URL}/{LOCALE}/rss/events.xml",
    "releases": f"{BASE_URL}/{LOCALE}/rss/releases.xml",
}

PRIORITY_BANDS = [
    "mygo", "ave-mujica", "poppin-party", "roselia", "afterglow",
    "morfonica", "raise-a-suilen", "hello-happy-world", "pastel-palettes",
    "ikka-dumb-rock", "yumemita",
]

CATEGORY_ORDER = {
    "bands": 0, "characters": 1, "members": 2, "news": 3,
    "songs": 4, "events": 5, "releases": 6,
    "voice_actors": 7, "venues": 8, "setlists": 9, "other": 10,
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("bandori_sync")


# ── 工具函数 ──────────────────────────────────────

def ensure_dir(path: Path):
    path.mkdir(parents=True, exist_ok=True)


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"lastmod": {}, "rss": {}, "last_sync": None, "version": 3}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def fetch(url: str, timeout: int = 30) -> str:
    import subprocess
    result = subprocess.run(
        ["curl", "-s", "-L", "--max-time", str(timeout), url],
        capture_output=True, text=True, timeout=timeout + 10,
    )
    return result.stdout


# ── Sitemap 解析（保留原有逻辑）──────────────────

def _parse_sitemap_xml(content: str) -> ET.Element | None:
    try:
        return ET.fromstring(content.encode())
    except ET.ParseError as e:
        log.warning(f"XML parse error (will try iterative): {e}")
        try:
            from io import StringIO
            buf = StringIO(content)
            root = None
            for event, elem in ET.iterparse(buf, events=("end",)):
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag in ("urlset", "sitemapindex"):
                    root = elem
                    break
            return root
        except Exception as e2:
            log.error(f"Iterative parse also failed: {e2}")
            return None


def _parse_sitemap_content(content: str, source: str = "") -> list[tuple[str, str]]:
    if not content:
        return []
    urls = []
    try:
        root = _parse_sitemap_xml(content)
        if root is None:
            return urls
        ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}

        if root.tag.split("}")[-1] == "sitemapindex":
            child_urls = []
            for sm in root.findall("sm:sitemap", ns):
                loc = sm.find("sm:loc", ns)
                if loc is not None and loc.text:
                    child_urls.append(loc.text)
            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(fetch, u, 30): u for u in child_urls}
                for future in as_completed(futures):
                    u = futures[future]
                    try:
                        child_content = future.result(timeout=35)
                        urls.extend(_parse_sitemap_content(child_content, u))
                    except Exception as e:
                        log.warning(f"Failed to fetch sub-sitemap {u}: {e}")
            return urls

        try:
            from io import StringIO
            buf = StringIO(content)
            for event, elem in ET.iterparse(buf, events=("end",)):
                tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if tag == "url":
                    loc = elem.find("sm:loc", ns)
                    if loc is not None and loc.text:
                        lastmod_elem = elem.find("sm:lastmod", ns)
                        lastmod = lastmod_elem.text if lastmod_elem is not None else ""
                        urls.append((loc.text, lastmod))
                    elem.clear()
        except Exception:
            for url_elem in root.findall("sm:url", ns):
                loc = url_elem.find("sm:loc", ns)
                lastmod_elem = url_elem.find("sm:lastmod", ns)
                if loc is not None and loc.text:
                    lastmod = lastmod_elem.text if lastmod_elem is not None else ""
                    urls.append((loc.text, lastmod))
    except Exception as e:
        log.error(f"Failed to parse sitemap {source}: {e}")
    return urls


def parse_sitemap(url: str) -> list[tuple[str, str]]:
    content = fetch(url, timeout=60)
    return _parse_sitemap_content(content, url)


def filter_relevant_urls(urls: list[tuple[str, str]], locale: str) -> list[tuple[str, str, str]]:
    result = []
    for url, lastmod in urls:
        parsed = urlparse(url)
        path = parsed.path
        if not path.startswith(f"/{locale}/"):
            continue
        relevant_patterns = [
            f"/{locale}/bands/",
            f"/{locale}/members/",
            f"/{locale}/songs/song%3A",
            f"/{locale}/events/",
            f"/{locale}/releases/",
            f"/{locale}/voice-actors/",
            f"/{locale}/venues/",
            f"/{locale}/character/",
            f"/{locale}/setlist/",
            f"/{locale}/news/",
        ]
        if any(pattern in path for pattern in relevant_patterns):
            name = path.replace(f"/{locale}/", "").replace("/", "_")
            name = unquote(name)
            if len(name) > 100:
                name = name[:100]
            result.append((url, lastmod, name))
    return result


def get_changed_urls(all_urls: list[tuple[str, str, str]], state: dict) -> list[tuple[str, str, str]]:
    previous = state.get("lastmod", {})
    changed = []
    for url, lastmod, name in all_urls:
        if url not in previous or previous[url] != lastmod:
            changed.append((url, lastmod, name))
    return changed


# ── 页面内容提取 ──────────────────────────────────

def extract_page_content(url: str) -> str:
    import subprocess
    try:
        result = subprocess.run(
            ["chromium-browser", "--headless", "--disable-gpu", "--dump-dom", url],
            capture_output=True, text=True, timeout=45,
        )
        html = result.stdout
        if not html or len(html) < 200:
            log.warning(f"Short HTML from {url}, fallback curl")
            html = fetch(url)
            if not html or len(html) < 200:
                return ""
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "form", "aside"]):
            tag.decompose()
        for tag in soup.find_all(class_=lambda c: c and any(x in c for x in ["utility", "search", "foot", "mast", "skip", "dark-toggle", "locale"])):
            tag.decompose()
        main = soup.find("main")
        if main:
            for nav in main.find_all("nav"):
                nav.decompose()
            text = main.get_text(separator="\n", strip=True)
        else:
            text = soup.get_text(separator="\n", strip=True)
        lines = [l.strip() for l in text.split("\n") if l.strip() and len(l.strip()) > 1]
        lines = [l for l in lines if "订正" not in l and "提交" not in l]
        clean_text = "\n".join(lines)
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else url.split("/")[-1]
        return f"# {title}\n\n{clean_text}"
    except Exception as e:
        log.error(f"Failed to extract {url}: {e}")
        return ""


def categorize_page(name: str, url: str = "") -> str:
    name = unquote(name)
    name = name.replace("character:", "").replace("character%3A", "")
    if "bands_" in name or "/bands/" in url:
        slug = name.replace("bands_", "").split("?")[0].split("&")[0]
        return f"bands/{slug}"
    elif "character_" in name or "/character/" in url:
        slug = name.replace("character_", "").replace("character:", "").replace("character%3A", "").split("?")[0]
        return f"characters/{slug}"
    elif "news_" in name or "/news/" in url:
        nid = name.replace("news_", "").split("?")[0][:40]
        return f"news/{nid}"
    elif name.startswith("songs_"):
        song_id = name.replace("songs_song%3A", "").split("%3A")[0][:20]
        return f"songs/{song_id}"
    elif "events_" in name or "/events/" in url:
        event_id = name.replace("events_", "").split("?")[0][:40]
        return f"events/{event_id}"
    elif "releases_" in name or "/releases/" in url:
        rel_id = name.replace("releases_", "").split("?")[0][:40]
        return f"releases/{rel_id}"
    elif "voice-actors_" in name or "/voice-actors/" in url:
        return f"voice_actors/{name.replace('voice-actors_', '')}"
    elif "venues_" in name or "/venues/" in url:
        return f"venues/{name.replace('venues_', '')}"
    elif "setlist_" in name or "/setlist/" in url:
        return f"setlists/{name.replace('setlist_', '')}"
    else:
        return f"other/{name[:40]}"


def extract_metadata(text: str, url: str, extra: dict[str, str] | None = None) -> str:
    lines = []
    lines.append("---")
    lines.append("source: bandori.fans")
    lines.append(f"url: {url}")
    lines.append(f"synced_at: {datetime.now(timezone.utc).isoformat()}")
    if extra:
        for k, v in extra.items():
            lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + text


# ── Feed 拉取 (Atom / RSS) ───────────────────────

_ATOM_NS = "http://www.w3.org/2005/Atom"


def parse_feed(url: str) -> list[dict]:
    """Parse Atom or RSS feed, return list of {title, link, pub_date, description}."""
    content = fetch(url, timeout=30)
    if not content:
        return []
    items = []
    try:
        root = ET.fromstring(content.encode())
        tag = root.tag.split("}")[-1] if "}" in root.tag else root.tag

        if tag == "feed":
            ns = {"a": _ATOM_NS}
            for entry in root.findall("a:entry", ns):
                title = _text(entry, "a:title", ns)
                link = _atom_link(entry, ns)
                pub_date = _text(entry, "a:published", ns) or _text(entry, "a:updated", ns)
                summary = _text(entry, "a:summary", ns) or ""
                if not link:
                    continue
                if not urlparse(link).path.startswith(f"/{LOCALE}/"):
                    continue
                items.append({
                    "title": title or "Untitled",
                    "link": link,
                    "pub_date": pub_date or "",
                    "description": _strip_html(summary)[:500],
                })
        elif tag == "rss":
            for item_elem in root.iter("item"):
                title = _text(item_elem, "title")
                link = _text(item_elem, "link")
                pub_date = _text(item_elem, "pubDate")
                description = _text(item_elem, "description") or ""
                if not link:
                    continue
                if not urlparse(link).path.startswith(f"/{LOCALE}/"):
                    continue
                items.append({
                    "title": title or "Untitled",
                    "link": link,
                    "pub_date": pub_date or "",
                    "description": _strip_html(description)[:500],
                })
    except Exception as e:
        log.warning(f"Feed parse failed for {url}: {e}")
    return items


def _atom_link(entry: ET.Element, ns: dict) -> str:
    for link_elem in entry.findall("a:link", ns):
        rel = link_elem.get("rel", "")
        if rel == "alternate" and link_elem.get("type") != "application/atom+xml":
            return link_elem.get("href", "")
    return ""


def _text(elem: ET.Element, tag: str, ns: dict | None = None) -> str:
    if ns:
        child = elem.find(tag, ns)
    else:
        child = elem.find(tag)
    return child.text.strip() if child is not None and child.text else ""


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html).strip()


def sync_rss_feed(feed_name: str, feed_url: str, state: dict) -> int:
    """Sync items from an RSS feed.  Returns number of new items saved."""
    rss_state = state.setdefault("rss", {})
    seen = rss_state.get(feed_name, {})
    items = parse_feed(feed_url)
    if not items:
        return 0

    saved = 0
    for item in items:
        link = item["link"]
        if link in seen and seen[link] == item.get("pub_date", ""):
            continue
        name = link.split(f"/{LOCALE}/")[-1].replace("/", "_")[:80]
        name = unquote(name)
        category = categorize_page(name, link)
        output_path = OUTPUT_DIR / f"{category}.md"
        ensure_dir(output_path.parent)

        body = f"# {item['title']}\n\n"
        if item["pub_date"]:
            body += f"**发布时间:** {item['pub_date']}\n\n"
        if item["description"]:
            body += f"{item['description']}\n\n"
        body += f"**来源链接:** {link}\n"

        extra = {
            "pub_date": item.get("pub_date", ""),
            "sync_method": "rss",
        }
        full_text = extract_metadata(body, link, extra)
        output_path.write_text(full_text, encoding="utf-8")
        seen[link] = item.get("pub_date", "")
        saved += 1

    rss_state[feed_name] = seen
    return saved


# ── 排序 ──────────────────────────────────────────

def _category_of(url: str) -> str:
    if "/bands/" in url:
        return "bands"
    if "/character/" in url:
        return "characters"
    if "/members/" in url:
        return "members"
    if "/news/" in url:
        return "news"
    if "/songs/" in url:
        return "songs"
    if "/events/" in url:
        return "events"
    if "/releases/" in url:
        return "releases"
    if "/voice-actors/" in url:
        return "voice_actors"
    if "/venues/" in url:
        return "venues"
    if "/setlist/" in url:
        return "setlists"
    return "other"


def sort_pages(pages: list[tuple[str, str, str]]) -> list[tuple[str, str, str]]:
    def sort_key(item):
        url = item[0]
        cat = _category_of(url)
        return (CATEGORY_ORDER.get(cat, 99), url)
    return sorted(pages, key=sort_key)


# ── 主流程 ────────────────────────────────────────

def main():
    log.info("=== bandori.fans sync start ===")
    ensure_dir(OUTPUT_DIR)
    state = load_state()

    # ── Step 1: RSS feeds (news / events / releases) ──
    rss_saved = 0
    for feed_name, feed_url in RSS_FEEDS.items():
        try:
            n = sync_rss_feed(feed_name, feed_url, state)
            rss_saved += n
            if n > 0:
                log.info(f"RSS [{feed_name}]: {n} new items")
        except Exception as e:
            log.warning(f"RSS [{feed_name}] failed: {e}")

    # ── Step 2: Sitemap sync (bands / characters / songs / etc.) ──
    log.info("Fetching sitemap...")
    all_sitemap_urls = parse_sitemap(SITEMAP_INDEX)
    log.info(f"Found {len(all_sitemap_urls)} URLs in sitemaps")

    relevant = filter_relevant_urls(all_sitemap_urls, LOCALE)
    log.info(f"Filtered to {len(relevant)} relevant pages")

    changed = get_changed_urls(relevant, state)
    log.info(f"Changed/new pages: {len(changed)}")

    if not changed and rss_saved == 0:
        log.info("No changes detected. Sync complete.")
        state["last_sync"] = datetime.now(timezone.utc).isoformat()
        save_state(state)
        return

    # ── Prioritize: band + character pages first, rest sorted by category ──
    priority_changed = []
    other_changed = []
    for item in changed:
        url, lastmod, name = item
        is_priority = any(
            f"/{LOCALE}/bands/{b}" in url or f"/{LOCALE}/character/character%3A" in url
            for b in PRIORITY_BANDS
        )
        if is_priority:
            priority_changed.append(item)
        else:
            other_changed.append(item)

    priority_changed = sort_pages(priority_changed)
    other_changed = sort_pages(other_changed)

    pages_to_process = priority_changed + other_changed
    pages_to_process = pages_to_process[:MAX_PAGES_PER_RUN]

    synced_count = 0
    error_count = 0

    for url, lastmod, name in pages_to_process:
        log.info(f"Syncing: {name}")
        try:
            text = extract_page_content(url)
            if not text or len(text.strip()) < 50:
                log.warning(f"  Empty/short content for {name}, retrying curl...")
                html = fetch(url)
                text = re.sub(r"<[^>]+>", " ", html)
                text = re.sub(r"\s+", " ", text).strip()
                if len(text) < 100:
                    log.warning(f"  Still no good content for {name}, skipping")
                    error_count += 1
                    continue
                text = f"# {name}\n\n{text}"

            rel_path = categorize_page(name, url)
            output_path = OUTPUT_DIR / f"{rel_path}.md"
            ensure_dir(output_path.parent)
            full_text = extract_metadata(text, url)
            output_path.write_text(full_text, encoding="utf-8")
            state["lastmod"][url] = lastmod
            synced_count += 1
            log.info(f"  ✓ {output_path.relative_to(OUTPUT_DIR)}")
        except Exception as e:
            log.error(f"  ✗ Failed to sync {name}: {e}")
            error_count += 1

    state["last_sync"] = datetime.now(timezone.utc).isoformat()
    state["total_pages_in_sitemap"] = len(relevant)
    state["synced_this_run"] = synced_count
    state["rss_synced_this_run"] = rss_saved
    save_state(state)

    log.info(
        "=== Sync complete: %d sitemap + %d RSS = %d total, %d errors, %d remaining ===",
        synced_count, rss_saved, synced_count + rss_saved,
        error_count, max(0, len(changed) - synced_count - error_count),
    )

    if synced_count > 0 or rss_saved > 0:
        parts = []
        if synced_count > 0:
            parts.append(f"更新了 {synced_count} 页")
        if rss_saved > 0:
            parts.append(f"RSS {rss_saved} 条")
        if error_count > 0:
            parts.append(f"{error_count} 页失败")
        print(f"[bandori.fans 同步] {', '.join(parts)}")


if __name__ == "__main__":
    main()

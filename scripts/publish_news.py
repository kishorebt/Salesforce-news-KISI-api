#!/usr/bin/env python3
"""Discover -> verify -> select -> caption -> render.

Writes candidate.json when exactly one story passes every gate, otherwise logs
NO_NEW_STORY and exits 0. Never publishes; submit_candidate.py does that.

Env:
  KISI_DRY_RUN=1      allow rendering without the logo asset (preview only)
  KISI_NOW=<iso>      override "now" (tests)
  KISI_FEED_FILE_<n>  local feed file instead of URL (tests)
"""
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

sys.path.insert(0, os.path.dirname(__file__))
from kisi_common import (CANDIDATE, clean_url, LOGO, POSTS_DIR, blocking_entries, canonical_url,
                         headline_key, http, load_state, log, now_utc, warn)

MAX_AGE = timedelta(hours=48)
MIN_SCORE = 4

# (feed url, source label, trust weight). Official Salesforce sources only.
FEEDS = [
    ("https://www.salesforce.com/news/feed/", "Salesforce Newsroom", 3),
    ("https://developer.salesforce.com/blogs/feed", "Salesforce Developers Blog", 2),
    ("https://admin.salesforce.com/blog/feed", "Salesforce Admins Blog", 2),
    ("https://engineering.salesforce.com/feed/", "Salesforce Engineering", 1),
]
# Third-party coverage republished by Salesforce, and non-news formats.
EXCLUDE_URL = re.compile(r"/news/linked-content/|/podcast|/webinar|/events?/", re.I)
EXCLUDE_TITLE = re.compile(r"\b(podcast|webinar|quiz|recap of the week|this week in)\b", re.I)

SIGNALS = [  # (regex, points)
    (r"\bagentforce\b|\bagentic\b|\bai agents?\b|\bagents?\b", 3),
    (r"\bdata cloud\b|\bdata 360\b|\bslack\b|\bmulesoft\b|\btableau\b|\bheroku\b", 2),
    (r"\bannounc|\blaunch|\bgenerally available\b|\bnow available\b|\bintroduc|\bunveil", 3),
    (r"\bacqui|\bpartnership\b|\bdreamforce\b|\brelease\b|\bwinter '\d\d|\bspring '\d\d|\bsummer '\d\d", 2),
    (r"\bapex\b|\blwc\b|\blightning web\b|\bflow\b|\bapi\b|\bmcp\b|\bcli\b|\bdevops\b|\bdeveloper", 2),
    (r"\bsecurity\b|\btrust\b|\bgovernance\b|\bcompliance\b|\bmfa\b|\bretire|\bdeprecat|\benforce", 2),
    (r"\barchitect|\bintegration\b|\bplatform\b|\bcrm\b|\bsalesforce\b", 1),
]

TOPICS = [  # (regex, why-it-matters, hashtags)
    (r"agent|agentic|\bai\b|autonom",
     "Agentic AI changes how we design data access, permissions, and human hand-offs. Worth reviewing against your org's trust and governance model.",
     "#Salesforce #Agentforce #AI #SalesforceArchitects #SalesforceDevelopers"),
    (r"data cloud|data 360|\bdata\b|analytics|tableau",
     "Your data foundations decide what agents and analytics can actually do. Check the impact on your data model and integration patterns.",
     "#Salesforce #DataCloud #SalesforceArchitects #Data"),
    (r"apex|lwc|lightning web|\bapi\b|mcp|cli|devops|developer|code",
     "This may affect code, APIs, or tooling. Read the release notes and validate in a sandbox before it reaches production.",
     "#Salesforce #SalesforceDevelopers #Apex #LWC"),
    (r"admin|flow|release|setup|permission",
     "Admins should confirm feature availability, licensing, permissions, and rollout timing before switching it on for users.",
     "#Salesforce #SalesforceAdmins #Flow #Trailhead"),
    (r"acqui|partner|strategy|invest|earnings",
     "Strategic moves like this shape product roadmaps and the architecture choices customers make next.",
     "#Salesforce #CRM #SalesforceArchitects"),
]
TOPICS.insert(0, (r"\bsecurity\b|\bmfa\b|compliance|\bretir|deprecat|enforcement",
     "Changes like this usually come with enforcement dates. Check what your org has to change, and by when.",
     "#Salesforce #SalesforceSecurity #SalesforceAdmins #SalesforceArchitects"))
DEFAULT_TOPIC = ("Worth a read for anyone designing or delivering on the Salesforce platform.",
                 "#Salesforce #SalesforceOhana #SalesforceArchitects")


def clean(s):
    s = html.unescape(re.sub(r"<[^>]+>", " ", s or ""))
    s = re.sub(r"The post .{0,300}? appeared first on .*$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def read_feed(i, url):
    local = os.environ.get(f"KISI_FEED_FILE_{i}")
    if local:
        return open(local, "rb").read()
    status, _, body = http(url, headers={"Accept": "application/rss+xml, application/xml, */*"})
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    return body


def discover():
    items, failures = [], 0
    atom = {"a": "http://www.w3.org/2005/Atom"}
    for i, (url, label, weight) in enumerate(FEEDS):
        try:
            root = ET.fromstring(read_feed(i, url))
        except Exception as e:  # report, keep going with other feeds
            failures += 1
            log("FEED_FAILED", f"{label} ({url}): {e}")
            continue
        n = 0
        for node in root.findall(".//item"):
            items.append(dict(title=clean(node.findtext("title")), url=clean(node.findtext("link")),
                              description=clean(node.findtext("description")),
                              published=parse_date(clean(node.findtext("pubDate"))),
                              source=label, weight=weight))
            n += 1
        for node in root.findall(".//a:entry", atom):
            link = node.find("a:link", atom)
            items.append(dict(title=clean(node.findtext("a:title", "", atom)),
                              url=link.attrib.get("href", "") if link is not None else "",
                              description=clean(node.findtext("a:summary", "", atom)),
                              published=parse_date(node.findtext("a:published", "", atom)
                                                   or node.findtext("a:updated", "", atom)),
                              source=label, weight=weight))
            n += 1
        log("FEED_OK", f"{label}: {n} items")
    if failures == len(FEEDS):
        raise SystemExit("ALL_FEEDS_FAILED: no source reachable; nothing published.")
    return items


def score(item):
    text = f"{item['title']} {item['description']}".lower()
    return item["weight"] + sum(p for rx, p in SIGNALS if re.search(rx, text))


def page_date(body):
    h = body.decode("utf-8", "ignore")
    for rx in (r'property=["\']article:published_time["\']\s+content=["\']([^"\']+)',
               r'content=["\']([^"\']+)["\']\s+property=["\']article:published_time',
               r'"datePublished"\s*:\s*"([^"]+)"'):
        m = re.search(rx, h)
        if m:
            return parse_date(m.group(1))
    return None


def page_canonical(body, fallback):
    m = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)', body.decode("utf-8", "ignore"))
    return m.group(1) if m and m.group(1).startswith("https://") else fallback


def verify(item, now):
    """Fetch the article itself. Returns (ok, reason, item)."""
    if os.environ.get("KISI_SKIP_PAGE_FETCH"):
        return True, "page fetch skipped (test)", item
    try:
        status, final, body = http(item["url"], headers={"Accept": "text/html"})
    except Exception as e:
        return False, f"page unreachable: {e}", item
    if status in (404, 410):
        return False, f"page HTTP {status}", item
    if status in (401, 403, 429):
        # Salesforce sometimes bot-blocks CI IPs. The item already came from an
        # official Salesforce feed with a dated entry, so accept on that basis.
        return True, f"page HTTP {status} (bot-blocked); relying on official feed date", item
    if status != 200:
        return False, f"page HTTP {status}", item
    if "salesforce.com" not in final:
        return False, f"redirected off Salesforce: {final}", item
    item["url"] = clean_url(page_canonical(body, final))
    pd = page_date(body)
    if pd:
        if now - pd > MAX_AGE:
            return False, f"page says published {pd.isoformat()} (older than 48h)", item
        item["published"] = min(item["published"], pd)
    return True, "page verified" + (f", page date {pd.date()}" if pd else ""), item


def select(items, state, now):
    blocked = blocking_entries(state)
    seen_urls = {e["canonical_url"] for e in blocked}
    seen_titles = {e.get("title_key") for e in blocked if e.get("title_key")}
    pool, dedupe = [], set()
    for it in items:
        if not it["title"] or not it["url"].startswith("https://"):
            continue
        it["url"] = clean_url(it["url"])
        it["canonical_url"] = canonical_url(it["url"])
        it["title_key"] = headline_key(it["title"])
        why = None
        if it["published"] is None:
            why = "no publication date"
        elif it["published"] > now + timedelta(hours=1):
            why = "dated in the future"
        elif now - it["published"] > MAX_AGE:
            continue  # stale: silently ignored, this is the common case
        elif "salesforce.com" not in it["canonical_url"]:
            why = "not an official Salesforce URL"
        elif EXCLUDE_URL.search(it["url"]) or EXCLUDE_TITLE.search(it["title"]):
            why = "excluded format (third-party/podcast/webinar)"
        elif it["canonical_url"] in seen_urls or it["title_key"] in seen_titles:
            why = "already published"
        elif (it["canonical_url"], it["title_key"]) in dedupe:
            continue
        else:
            it["score"] = score(it)
            if it["score"] < MIN_SCORE:
                why = f"low relevance score {it['score']}"
        dedupe.add((it["canonical_url"], it["title_key"]))
        if why:
            log("SKIP", f"{why}: {it['title'][:90]}")
        else:
            pool.append(it)
    pool.sort(key=lambda x: (x["score"], x["published"]), reverse=True)
    for it in pool:
        ok, reason, it = verify(it, now)
        log("VERIFY_OK" if ok else "VERIFY_FAILED", f"{reason}: {it['url']}")
        if ok and now - it["published"] <= MAX_AGE:
            it["canonical_url"] = canonical_url(it["url"])
            if it["canonical_url"] in seen_urls:
                continue
            return it
    return None


def summarise(desc, limit=420):
    if not desc:
        return ""
    sents = re.split(r"(?<=[.!?])\s+", desc)
    out = ""
    for s in sents:
        if len(out) + len(s) > limit:
            break
        out = (out + " " + s).strip()
    return out or desc[:limit].rsplit(" ", 1)[0] + "…"


def topic(item):
    text = f"{item['title']} {item['description']}".lower()
    for rx, why, tags in TOPICS:
        if re.search(rx, text):
            return why, tags
    return DEFAULT_TOPIC


def caption_for(item):
    why, tags = topic(item)
    summary = summarise(item["description"])
    date = item["published"].strftime("%-d %b %Y")
    parts = [f"📰 {item['title']}"]
    if summary:
        parts.append(summary)
    parts += [f"Why it matters for Salesforce builders:\n{why}",
              f"Source: {item['source']}, {date}\n{item['url']}", tags]
    return "\n\n".join(parts)[:2900]


# ---------- graphic ----------
W, H = 1200, 1350
CHARCOAL, PANEL = (18, 20, 23), (30, 33, 38)
MUSTARD, WHITE, MUTED = (242, 183, 5), (246, 246, 242), (178, 183, 190)


def font(bold, size):
    from PIL import ImageFont
    for path in ((["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else
                  ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]) +
                 ["/usr/share/fonts/truetype/liberation/LiberationSans-%s.ttf" % ("Bold" if bold else "Regular")]):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


def wrap(d, text, f, width):
    lines, line = [], ""
    for w in text.split():
        t = f"{line} {w}".strip()
        if d.textlength(t, font=f) > width and line:
            lines.append(line)
            line = w
        else:
            line = t
    return lines + ([line] if line else [])


def place_logo(im, dry_run):
    """Paste the exact supplied logo bytes, scaled proportionally. Never drawn."""
    from PIL import Image, ImageDraw
    box_w, box_h, x, y = 350, 200, 80, 60
    if not LOGO.exists():
        if not dry_run:
            raise SystemExit("LOGO_MISSING: assets/kisi-logo.png not found; refusing to publish without the exact KISI logo.")
        ImageDraw.Draw(im).rectangle((x, y, x + box_w, y + box_h), outline=MUSTARD, width=3)
        ImageDraw.Draw(im).text((x + 20, y + 45), "LOGO ASSET MISSING (preview only)", font=font(True, 22), fill=MUSTARD)
        log("LOGO_MISSING", "rendered preview placeholder; publishing is blocked")
        return
    logo = Image.open(LOGO).convert("RGBA")
    scale = min(box_w / logo.width, box_h / logo.height)
    logo = logo.resize((max(1, round(logo.width * scale)), max(1, round(logo.height * scale))), Image.LANCZOS)
    im.paste(logo, (x, y + (box_h - logo.height) // 2), logo)  # exact asset, alpha-safe


def render(item, path, dry_run):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (W, H), CHARCOAL)
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, W, 14), fill=MUSTARD)
    for r, wdt in ((330, 4), (250, 3), (170, 2)):  # subtle accent rings
        d.ellipse((W - 120 - r, 120 - r, W - 120 + r, 120 + r), outline=(44, 48, 54), width=wdt)
    place_logo(im, dry_run)

    chip = f"{item['source'].upper()}  ·  {item['published'].strftime('%-d %b %Y').upper()}"
    fc = font(True, 24)
    cw = d.textlength(chip, font=fc)
    d.rounded_rectangle((80, 300, 80 + cw + 48, 352), radius=26, fill=(58, 47, 12))
    d.text((104, 312), chip, font=fc, fill=MUSTARD)

    size = 78
    while True:
        ft = font(True, size)
        lines = wrap(d, item["title"], ft, W - 160)
        if len(lines) <= 4 or size <= 46:
            break
        size -= 4
    y = 400
    for ln in lines[:6]:
        d.text((80, y), ln, font=ft, fill=WHITE)
        y += int(size * 1.2)

    d.rectangle((80, y + 30, 200, y + 38), fill=MUSTARD)
    summary = summarise(item["description"], 260)
    if summary:
        fs = font(False, 32)
        yy = y + 74
        for ln in wrap(d, summary, fs, W - 160)[:4]:
            if yy > H - 230:
                break
            d.text((80, yy), ln, font=fs, fill=MUTED)
            yy += 46

    d.rectangle((0, H - 150, W, H), fill=PANEL)
    d.rectangle((0, H - 150, W, H - 146), fill=MUSTARD)
    d.text((80, H - 112), "Why it matters for Salesforce builders  →  read the post",
           font=font(True, 28), fill=WHITE)
    d.text((80, H - 66), item["canonical_url"].removeprefix("https://")[:70],
           font=font(False, 24), fill=MUTED)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG", optimize=True)


def main():
    dry_run = os.environ.get("KISI_DRY_RUN") == "1"
    now = datetime.fromisoformat(os.environ["KISI_NOW"]) if os.environ.get("KISI_NOW") else now_utc()
    CANDIDATE.unlink(missing_ok=True)
    state = load_state()
    item = select(discover(), state, now)
    if not item:
        log("NO_NEW_STORY", "no fresh (<48h), verified, high-relevance story; nothing published")
        return
    path = POSTS_DIR / f"{item['published']:%Y%m%d}-{item['title_key']}.png"
    render(item, path, dry_run)
    cand = {k: item[k] for k in ("title", "url", "canonical_url", "title_key", "source", "description", "score")}
    cand.update(published=item["published"].isoformat(), caption=caption_for(item),
                image_path=str(path.relative_to(CANDIDATE.parent)))
    CANDIDATE.write_text(json.dumps(cand, indent=2, ensure_ascii=False))
    log("CANDIDATE_READY", f"score={item['score']} published={cand['published']} {item['url']}")
    print("----- CAPTION -----\n" + cand["caption"] + "\n-------------------")


if __name__ == "__main__":
    main()

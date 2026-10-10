#!/usr/bin/env python3
"""Find a relevant Salesforce story, render a KISI graphic, and publish via Buffer."""
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import textwrap
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "published.json"
OUT_DIR = ROOT / "posts"
CANDIDATE = ROOT / "candidate.json"
CHANNEL_ID = "6aca818a6a5c39ccb674bdb3"
ORG_ID = "5b48f24de341a149319d3ecf"
COMPANY_URL = "https://www.linkedin.com/company/143983556"
FEEDS = [
    "https://admin.salesforce.com/blog/feed",
    "https://developer.salesforce.com/blogs/feed",
    "https://www.salesforce.com/blog/feed/",
]
KEYWORDS = ("salesforce", "agentforce", "data cloud", "einstein", "sales cloud",
            "service cloud", "apex", "lightning", "developer", "architecture",
            "artificial intelligence", " ai ", "crm", "release")
UA = "KISI-Salesforce-News-Bot/1.0"

def get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()

def clean(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()

def load_items():
    items = []
    for feed in FEEDS:
        try:
            root = ET.fromstring(get(feed))
            for node in root.findall(".//item"):
                title = clean(node.findtext("title"))
                link = clean(node.findtext("link"))
                desc = clean(node.findtext("description"))
                date = clean(node.findtext("pubDate"))
                if title and link and link.startswith("https://"):
                    items.append({"title": title, "url": link, "description": desc, "date": date, "feed": feed})
            # Atom feeds
            ns = {"a": "http://www.w3.org/2005/Atom"}
            for node in root.findall(".//a:entry", ns):
                title = clean(node.findtext("a:title", default="", namespaces=ns))
                link_node = node.find("a:link", ns)
                link = link_node.attrib.get("href", "") if link_node is not None else ""
                desc = clean(node.findtext("a:summary", default="", namespaces=ns))
                date = clean(node.findtext("a:updated", default="", namespaces=ns))
                if title and link.startswith("https://"):
                    items.append({"title": title, "url": link, "description": desc, "date": date, "feed": feed})
        except Exception as exc:
            print(f"Feed unavailable (skipping): {feed}: {exc}", file=sys.stderr)
    # preserve feed order while deduplicating
    unique = {}
    for item in items:
        unique[item["url"].split("#")[0]] = item
    return list(unique.values())

def make_graphic(title, outpath):
    from PIL import Image, ImageDraw, ImageFont
    W, H = 1200, 1200
    im = Image.new("RGB", (W, H), (24, 27, 32))
    d = ImageDraw.Draw(im)
    mustard = (255, 196, 55)
    white = (247, 247, 242)
    muted = (191, 195, 201)
    # simple premium editorial card; avoid fake Salesforce branding
    d.rectangle((0, 0, W, 22), fill=mustard)
    d.ellipse((840, -160, 1320, 320), outline=(55, 61, 69), width=5)
    d.ellipse((900, -100, 1260, 260), outline=(55, 61, 69), width=3)
    regular = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    bold = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    f_brand = ImageFont.truetype(bold, 48)
    f_label = ImageFont.truetype(bold, 24)
    f_title = ImageFont.truetype(bold, 64)
    f_body = ImageFont.truetype(regular, 28)
    f_footer = ImageFont.truetype(bold, 24)
    d.text((76, 76), "KISI", font=f_brand, fill=mustard)
    d.text((78, 150), "SALESFORCE SIGNAL", font=f_label, fill=muted)
    d.rounded_rectangle((78, 230, 360, 282), radius=18, fill=(55, 47, 28))
    d.text((98, 242), "THE LATEST", font=f_label, fill=mustard)
    words = title.split()
    lines, line = [], ""
    for word in words:
        test = (line + " " + word).strip()
        if d.textbbox((0,0), test, font=f_title)[2] > 1040 and line:
            lines.append(line); line = word
        else:
            line = test
    if line: lines.append(line)
    y = 330
    for ln in lines[:5]:
        d.text((76, y), ln, font=f_title, fill=white)
        y += 82
    d.line((78, 820, 1122, 820), fill=(75, 80, 88), width=2)
    d.text((78, 860), "Salesforce news. Architecture context.", font=f_body, fill=muted)
    d.text((78, 1080), "KISI  •  CURATED FOR BUILDERS", font=f_footer, fill=mustard)
    d.text((78, 1130), "Independent community coverage", font=f_body, fill=muted)
    im.save(outpath, "JPEG", quality=90, optimize=True)

def buffer_post(text, image_url):
    key = os.environ.get("BUFFER_API_KEY")
    if not key:
        raise RuntimeError("Missing BUFFER_API_KEY secret")
    # First verify that the target remains the intended, connected KISI LinkedIn page.
    verify = {"query": 'query { channels(input: { organizationId: "' + ORG_ID + '" }) { id displayName service externalLink isDisconnected isLocked } }'}
    req = urllib.request.Request("https://api.buffer.com", data=json.dumps(verify).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read())
    channels = payload.get("data", {}).get("channels", [])
    matches = [c for c in channels if c.get("id") == CHANNEL_ID and c.get("externalLink") == COMPANY_URL
               and c.get("service") == "linkedin" and not c.get("isDisconnected") and not c.get("isLocked")]
    if payload.get("errors") or len(matches) != 1:
        raise RuntimeError("KISI LinkedIn channel verification failed; refusing to publish")
    mutation = """mutation CreateKisiNews {
      createPost(input: {
        text: %s
        channelId: "%s"
        schedulingType: automatic
        mode: addToQueue
        assets: [{ image: { url: "%s" } }]
      }) {
        ... on PostActionSuccess { post { id text status dueAt assets { id mimeType } } }
        ... on MutationError { message }
      }
    }""" % (json.dumps(text), CHANNEL_ID, image_url)
    req = urllib.request.Request("https://api.buffer.com", data=json.dumps({"query": mutation}).encode(),
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=45) as resp:
        result = json.loads(resp.read())
    if result.get("errors"):
        raise RuntimeError("Buffer GraphQL error: " + json.dumps(result["errors"])[:1000])
    action = result.get("data", {}).get("createPost", {})
    if action.get("message"):
        raise RuntimeError("Buffer rejected the post: " + action["message"])
    post = action.get("post")
    if not post or not post.get("id"):
        raise RuntimeError("Buffer did not confirm post creation: " + json.dumps(result)[:1000])
    return post

def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (ROOT / "data").mkdir(parents=True, exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {"published_urls": []}
    seen = set(state.get("published_urls", []))
    candidates = []
    for item in load_items():
        title = item["title"]
        lower = " " + (title + " " + item["description"]).lower() + " "
        if item["url"] in seen or not any(k in lower for k in KEYWORDS):
            continue
        if len(title) < 18:
            continue
        candidates.append(item)
    if not candidates:
        print("No new relevant Salesforce stories found. Nothing published.")
        return
    # Prefer newest feed order (the feed itself generally provides newest first).
    item = candidates[0]
    description = item["description"][:420].strip()
    caption = (f"📰 Salesforce Signal: {item['title']}\n\n"
               f"{description + chr(10) + chr(10) if description else ''}"
               f"Why it matters: Salesforce teams should assess the practical impact on architecture, delivery, and user experience—not just the announcement.\n\n"
               f"Read the source: {item['url']}\n\n"
               f"#Salesforce #Agentforce #SalesforceDevelopers #SalesforceArchitects #AI")
    caption = caption[:2900]
    slug = hashlib.sha256(item["url"].encode()).hexdigest()[:12]
    image_path = OUT_DIR / f"{slug}.jpg"
    make_graphic(item["title"], image_path)
    CANDIDATE.write_text(json.dumps({"item": item, "caption": caption, "image_path": str(image_path.relative_to(ROOT))}, indent=2))
    print("CANDIDATE_READY " + str(CANDIDATE))
    print("Source: " + item["url"])

if __name__ == "__main__":
    main()

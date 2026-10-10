"""Shared helpers for the KISI Salesforce news pipeline (stdlib only)."""
import hashlib
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "data" / "published.json"
CANDIDATE = ROOT / "candidate.json"
POSTS_DIR = ROOT / "posts"
LOGO = ROOT / "assets" / "kisi-logo.png"

REPO = "kishorebt/Salesforce-news-KISI-api"
BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/129.0 Safari/537.36 KISI-News-Bot/2.0")
TRANSIENT = {408, 425, 429, 500, 502, 503, 504}
TRACKING = re.compile(r"^(utm_|mc_|gclid|fbclid|trk|cid$|sf\d*$|d$)", re.I)


def log(status, msg=""):
    """Single-line, grep-friendly log. Never pass secrets here."""
    print(f"{status} {msg}".strip(), flush=True)


def warn(msg):
    print(f"WARN {msg}", file=sys.stderr, flush=True)


def http(url, *, data=None, headers=None, method=None, timeout=25, retries=3):
    """Return (status, final_url, body bytes). Retries transient failures with backoff.
    Non-transient HTTP errors are returned, not raised, so callers can decide."""
    hdrs = {"User-Agent": BROWSER_UA, "Accept": "*/*"}
    hdrs.update(headers or {})
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.geturl(), r.read()
        except urllib.error.HTTPError as e:
            if e.code in TRANSIENT and attempt < retries - 1:
                last = e
            else:
                return e.code, url, e.read() if e.fp else b""
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            if attempt == retries - 1:
                raise
        time.sleep(2 ** attempt * 2)
    raise RuntimeError(f"HTTP failed after retries: {url}: {last}")


def clean_url(url):
    """Public-facing URL: tracking params and fragment removed, host kept as-is."""
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not TRACKING.match(k)]
    return urllib.parse.urlunsplit(("https", p.netloc, p.path, urllib.parse.urlencode(q), ""))


def canonical_url(url):
    p = urllib.parse.urlsplit(url.strip())
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query) if not TRACKING.match(k)]
    path = re.sub(r"/{2,}", "/", p.path).rstrip("/") or "/"
    return urllib.parse.urlunsplit(("https", p.netloc.lower().removeprefix("www."),
                                    path, urllib.parse.urlencode(q), ""))


def headline_key(title):
    t = re.sub(r"[^a-z0-9 ]", " ", title.lower().replace("’", "'"))
    t = re.sub(r"\s+", " ", t).strip()
    return hashlib.sha256(t.encode()).hexdigest()[:16]


def now_utc():
    return datetime.now(timezone.utc)


def load_state():
    if STATE.exists():
        s = json.loads(STATE.read_text())
    else:
        s = {}
    s.setdefault("published", [])
    # Back-compat: older format kept a flat URL list.
    known = {e["canonical_url"] for e in s["published"]}
    for u in s.pop("published_urls", []):
        cu = canonical_url(u)
        if cu not in known:
            s["published"].append({"canonical_url": cu, "title_key": None,
                                   "status": "legacy", "source_url": u})
            known.add(cu)
    return s


def blocking_entries(state):
    """Entries that block re-posting: anything except failed deliveries
    that have not yet used up their retry budget."""
    out = []
    for e in state["published"]:
        if e.get("status") == "error" and e.get("attempts", 1) < 2:
            continue
        out.append(e)
    return out


def save_state(state):
    state["published"] = state["published"][-1000:]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n")

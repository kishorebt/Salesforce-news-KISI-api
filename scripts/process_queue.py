#!/usr/bin/env python3
"""Manual post queue: publish a custom image + caption through the normal pipeline.

Drop a folder into queue/, e.g.

    queue/2026-10-11-agent-readiness/
        post.json      {"caption": "...", "image": "image.png",
                        "source_url": "https://... (optional)",
                        "logo_position": "top-right" (optional; top-left|top-right|bottom-left|bottom-right)}
        image.png      the artwork. It must NOT contain a KISI logo of its own.

The oldest pending folder (by name) is turned into candidate.json, and the
exact assets/kisi-logo.png is stamped onto the image. Queue items always take
priority over news, at one post per run. Folders already recorded in
data/published.json are deleted, so a finished item can't be posted twice.
Publishing still happens only on scheduled or manual runs (never on push).
"""
import hashlib
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(__file__))
from kisi_common import (CANDIDATE, LOGO, POSTS_DIR, ROOT, blocking_entries, canonical_url,
                         load_state, log, now_utc)

QUEUE = ROOT / "queue"
MAX_BYTES = 8 * 1024 * 1024
POSITIONS = {"top-left", "top-right", "bottom-left", "bottom-right"}


def item_key(name):
    return "manual:" + name


def stamp_logo(src, dst, position):
    from PIL import Image
    im = Image.open(src)
    im.load()
    im = im.convert("RGB")
    if min(im.size) < 600:
        raise ValueError(f"image too small {im.size}; need at least 600px on each side")
    logo = Image.open(LOGO).convert("RGBA")          # exact asset, proportional scale only
    target_w = round(im.width * 0.26)
    logo = logo.resize((target_w, round(logo.height * target_w / logo.width)), Image.LANCZOS)
    pad = round(im.width * 0.04)
    x = pad if "left" in position else im.width - logo.width - pad
    y = pad if "top" in position else im.height - logo.height - pad
    im.paste(logo, (x, y), logo)
    dst.parent.mkdir(parents=True, exist_ok=True)
    im.save(dst, "PNG", optimize=True)


def main():
    CANDIDATE.unlink(missing_ok=True)
    if not QUEUE.exists():
        return
    state = load_state()
    done = {e["canonical_url"] for e in blocking_entries(state)}
    seen_sources = done | {canonical_url(e["source_url"]) for e in blocking_entries(state)
                           if e.get("source_url", "").startswith("http")}
    for folder in sorted(p for p in QUEUE.iterdir() if p.is_dir()):
        key = item_key(folder.name)
        if key in done:
            shutil.rmtree(folder)                     # already published: clean up
            log("QUEUE_CLEANED", folder.name)
            continue
        try:
            meta = json.loads((folder / "post.json").read_text())
            caption = (meta.get("caption") or "").strip()
            if not caption or len(caption) > 2900:
                raise ValueError("caption missing or longer than 2900 characters")
            img = folder / meta.get("image", "image.png")
            if not img.exists() or img.stat().st_size > MAX_BYTES:
                raise ValueError(f"image {img.name} missing or over 8 MB")
            pos = meta.get("logo_position", "top-right")
            if pos not in POSITIONS:
                raise ValueError(f"logo_position must be one of {sorted(POSITIONS)}")
            src_url = (meta.get("source_url") or "").strip()
            if src_url and canonical_url(src_url) in seen_sources:
                raise ValueError(f"source_url already published: {src_url}")
            if not LOGO.exists():
                raise SystemExit("LOGO_MISSING: assets/kisi-logo.png not found; refusing to publish.")
            out = POSTS_DIR / f"manual-{folder.name}.png"
            stamp_logo(img, out, pos)
        except SystemExit:
            raise
        except Exception as e:
            log("QUEUE_INVALID", f"{folder.name}: {e} (left in queue, skipped)")
            continue
        cand = {"title": caption.splitlines()[0][:200], "url": src_url, "canonical_url": key,
                "title_key": hashlib.sha256(key.encode()).hexdigest()[:16], "source": "manual queue",
                "description": "", "score": None, "published": now_utc().isoformat(timespec="seconds"),
                "caption": caption, "image_path": str(out.relative_to(ROOT)), "queue_item": folder.name}
        CANDIDATE.write_text(json.dumps(cand, indent=2, ensure_ascii=False))
        log("QUEUE_CANDIDATE_READY", folder.name)
        print("----- CAPTION -----\n" + caption + "\n-------------------")
        return


if __name__ == "__main__":
    main()

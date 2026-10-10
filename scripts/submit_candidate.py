#!/usr/bin/env python3
"""Publish candidate.json to the KISI LinkedIn page via Buffer, then confirm delivery.

Requires env BUFFER_API_KEY and IMAGE_URL (public raw URL of the committed image).
State is written only after Buffer confirms the post was created.
Exit codes: 0 ok / nothing to do, 1 failure (visible in Actions).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
from kisi_common import CANDIDATE, blocking_entries, http, load_state, log, save_state

API = os.environ.get("KISI_BUFFER_API", "https://api.buffer.com")  # override for tests only
ORG_ID = "5b48f24de341a149319d3ecf"
CHANNEL_ID = "6aca818a6a5c39ccb674bdb3"
COMPANY_URL = "https://www.linkedin.com/company/143983556"
DELIVERED = {"sent", "published", "posted"}
FAILED = {"error", "failed"}


def gql(key, query):
    status, _, body = http(API, data=json.dumps({"query": query}).encode(), method="POST", timeout=45,
                           headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"})
    if status != 200:
        raise RuntimeError(f"Buffer HTTP {status}: {body[:300]!r}")
    return json.loads(body)


def verify_channel(key):
    r = gql(key, 'query { channels(input: { organizationId: "%s" }) '
                 '{ id service externalLink isDisconnected isLocked } }' % ORG_ID)
    ok = [c for c in r.get("data", {}).get("channels", []) or []
          if c.get("id") == CHANNEL_ID and c.get("service") == "linkedin"
          and c.get("externalLink") == COMPANY_URL and not c.get("isDisconnected") and not c.get("isLocked")]
    if r.get("errors") or len(ok) != 1:
        raise RuntimeError("CHANNEL_VERIFY_FAILED: KISI LinkedIn channel missing, disconnected or locked")


def create_post(key, text, image_url):
    r = gql(key, """mutation {
      createPost(input: { text: %s, channelId: "%s", schedulingType: automatic, mode: shareNow,
                          assets: [{ image: { url: %s } }] }) {
        ... on PostActionSuccess { post { id status } }
        ... on MutationError { message }
      }}""" % (json.dumps(text), CHANNEL_ID, json.dumps(image_url)))
    if r.get("errors"):
        raise RuntimeError("Buffer GraphQL error: " + json.dumps(r["errors"])[:800])
    a = (r.get("data") or {}).get("createPost") or {}
    if a.get("message"):
        raise RuntimeError("Buffer rejected the post: " + a["message"])
    if not (a.get("post") or {}).get("id"):
        raise RuntimeError("Buffer did not confirm creation: " + json.dumps(r)[:800])
    return a["post"]


def post_status(key, post_id):
    """Best-effort follow-up. Tries a rich query, falls back to minimal."""
    for fields in ("id status sentAt externalLink error { message }", "id status"):
        try:
            r = gql(key, 'query { post(input: { id: "%s" }) { %s } }' % (post_id, fields))
        except Exception as e:
            return {"check_error": str(e)[:200]}
        if not r.get("errors"):
            return (r.get("data") or {}).get("post") or {}
    return {"check_error": json.dumps(r.get("errors"))[:200]}


def main():
    if not CANDIDATE.exists():
        log("NO_NEW_STORY", "no candidate")
        return 0
    key, image_url = os.environ.get("BUFFER_API_KEY"), os.environ.get("IMAGE_URL")
    if not key:
        log("FAILED", "BUFFER_API_KEY secret is not set")
        return 1
    if not image_url:
        log("FAILED", "IMAGE_URL not provided")
        return 1
    c = json.loads(CANDIDATE.read_text())
    state = load_state()
    if any(e["canonical_url"] == c["canonical_url"] or e.get("title_key") == c["title_key"]
           for e in blocking_entries(state)):
        log("ALREADY_PUBLISHED", c["canonical_url"])
        return 0

    verify_channel(key)
    post = create_post(key, c["caption"], image_url)
    log("POST_CREATED", f"buffer_id={post['id']} status={post.get('status')} source={c['url']}")

    # Record immediately: Buffer has accepted it, so a rerun must not post again.
    prior = [e for e in state["published"] if e["canonical_url"] == c["canonical_url"]]
    entry = {"canonical_url": c["canonical_url"], "title_key": c["title_key"], "source_url": c["url"],
             "title": c["title"], "article_published": c["published"], "buffer_post_id": post["id"],
             "image_url": image_url, "status": post.get("status") or "created",
             "attempts": 1 + sum(e.get("attempts", 1) for e in prior),
             "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    state["published"] = [e for e in state["published"] if e["canonical_url"] != c["canonical_url"]] + [entry]
    save_state(state)

    # Follow-up: shareNow normally sends within seconds; poll for ~3 minutes.
    final = {}
    for _ in range(12):
        time.sleep(float(os.environ.get("KISI_POLL_SECONDS", "15")))
        final = post_status(key, post["id"])
        st = str(final.get("status", "")).lower()
        if st in DELIVERED | FAILED or "check_error" in final:
            break
    st = str(final.get("status", "")).lower()
    entry["status"] = st or entry["status"]
    for k in ("sentAt", "externalLink"):
        if final.get(k):
            entry[k] = final[k]
    save_state(state)

    if st in DELIVERED:
        log("DELIVERED", f"buffer_id={post['id']} linkedin={final.get('externalLink') or 'n/a'}")
        return 0
    if st in FAILED:
        log("DELIVERY_FAILED", f"buffer_id={post['id']} reason={json.dumps(final.get('error'))[:300]}")
        return 1  # recorded as error: one automatic retry allowed on a later run
    if "check_error" in final:
        log("DELIVERY_UNCONFIRMED", f"buffer_id={post['id']} status check unavailable: {final['check_error']}")
    else:
        log("DELIVERY_PENDING", f"buffer_id={post['id']} status={st or 'unknown'} after 3 min")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        log("FAILED", str(e)[:800])
        sys.exit(1)

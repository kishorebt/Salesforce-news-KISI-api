# KISI Salesforce News → LinkedIn: Handoff for the next agent

Read this in full before changing anything. The pipeline is **live and working**.
Your job is to keep it working, not to rebuild it.

- Repo: https://github.com/kishorebt/Salesforce-news-KISI-api (branch `main`)
- Target: KISI Salesforce Intelligence LinkedIn page, https://www.linkedin.com/company/143983556
- Publishing route: GitHub Actions → Buffer API (free plan) → LinkedIn page
- Cost: ₹0 (GitHub Actions + Pillow + Buffer free plan)
- Owner: Kishore. He is on an iPhone and has no always-on computer. Do not ask him to run commands.

---

## 1. Current status (as of 11 Oct 2026)

- First live post succeeded. Buffer reported `sent` at 2026-10-10T21:15:59Z.
  - Story: "Your Customer's Personal Agent Is Knocking. Is Your Business Ready to Answer?" (Salesforce Newsroom, 9 Oct 2026)
  - LinkedIn: https://www.linkedin.com/feed/update/urn:li:share:7514795893821435904
  - Buffer post ID: `6acaaaf9c4de2d2b123e3991`
- Scheduled workflow is **active**: `.github/workflows/salesforce-news.yml`, cron `17 */4 * * *` (every 4 h at :17 UTC).
- The old `publish-koa-now.yml` workflow was **deleted**. Do not recreate it or anything like it (see §5).
- The LinkedIn Community Management API is **not available** to the owner. Do not propose direct company-page posting through LinkedIn's API. Buffer stays.

---

## 2. Repository layout

```
.github/workflows/salesforce-news.yml   the only workflow
scripts/kisi_common.py                  HTTP with retries, URL canonicalisation, state I/O
scripts/publish_news.py                 discover → verify → select → caption → render image
scripts/submit_candidate.py             Buffer channel check → createPost → delivery check → record state
assets/kisi-logo.png                    EXACT original KISI logo (1659×948 PNG)
assets/README.md
data/published.json                     duplicate-prevention state (committed by the workflow)
posts/                                  published images, served via raw.githubusercontent.com
docs/HANDOFF.md                         this file
```

Logo SHA-256: `281543bef44cb06b4acd0106f5c21c400d0f58fcbcfcb9fb3394a5f7c5a7112a`.
**Never redraw, regenerate, recolour or approximate the logo.** Code may only scale it proportionally and paste it.

---

## 3. How a run works

1. **Discover.** Read official feeds only:
   - Salesforce Newsroom `https://www.salesforce.com/news/feed/` (primary, weight 3)
   - Developers Blog `https://developer.salesforce.com/blogs/feed` (2)
   - Admins Blog `https://admin.salesforce.com/blog/feed` (2)
   - Engineering `https://engineering.salesforce.com/feed/` (1)

   A failed feed logs `FEED_FAILED` and the run continues. If all feeds fail, it exits with `ALL_FEEDS_FAILED`.
2. **Filter.**
   - Strict rolling **48 h** freshness. Future-dated or undated items are rejected.
   - The URL must be on salesforce.com.
   - Excluded: `/news/linked-content/` (third-party coverage), podcasts and webinars.
3. **Deduplicate.** Match on canonical URL (no www, no tracking params, no trailing slash) and on a normalised headline hash, both checked against `data/published.json`.
4. **Score.** Keyword signals plus source weight. The minimum score is 4. One story per run, highest score first, then newest. No padding.
5. **Verify.** Fetch the article page.
   - 404/410 → reject.
   - 401/403/429 (bot-block) → accept on the official feed date and log it.
   - If the page date is older than 48 h → reject.
6. **Render.** A 1200×1350 PNG card: charcoal background, mustard accents, the exact logo top-left, a source/date chip, the headline, the summary, and a footer with the URL.
7. **Caption.** Headline, the official summary (from the feed, no invented claims), a "Why it matters for Salesforce builders" line chosen by topic, the source and date, the URL, and hashtags.
8. **Host.** Commit the image to `posts/` and wait until its raw URL returns 200.
9. **Publish.**
   - Verify the Buffer channel: id `6aca818a6a5c39ccb674bdb3`, org `5b48f24de341a149319d3ecf`, service linkedin, externalLink = the KISI page, connected and unlocked.
   - Call `createPost` with `mode: shareNow`.
10. **Record.** Write to state as soon as Buffer confirms creation, then poll the post status. The result is `DELIVERED`, `DELIVERY_PENDING`, `DELIVERY_FAILED` (one automatic retry on a later run) or `DELIVERY_UNCONFIRMED`.
11. **Commit state** with rebase-and-retry.

Log keywords to look for: `NO_NEW_STORY`, `CANDIDATE_READY`, `POST_CREATED`, `DELIVERED`, `ALREADY_PUBLISHED`, `LOGO_MISSING`, `CHANNEL_VERIFY_FAILED`, `FAILED ...`.

### Trigger rules (important)
| Event | Behaviour |
|---|---|
| `schedule` | **Publishes** |
| `workflow_dispatch` with `dry_run=false` (default) | **Publishes** |
| `workflow_dispatch` with `dry_run=true` | Preview only (artifact `kisi-post-preview`) |
| `push` to `scripts/**`, `assets/**`, the workflow file | **Preview only, never posts** |

Pushes to `docs/**`, `data/**` or `posts/**` do not trigger the workflow at all.

---

## 4. How to get a post on LinkedIn

- **Normal path:** do nothing. The schedule posts the next fresh, qualifying story.
- **Post now:** run the workflow manually. Use Actions → Salesforce News Automation → Run workflow → `dry_run` unchecked. Or use the REST API:
  `POST /repos/kishorebt/Salesforce-news-KISI-api/actions/workflows/salesforce-news.yml/dispatches` with body `{"ref":"main","inputs":{"dry_run":"false"}}`.
  If no story is fresh, it correctly posts nothing (`NO_NEW_STORY`). This is expected behaviour, not a bug.
- **One-off custom post** (e.g. an image you generated): there is no mechanism for this yet. If you build one:
  - Make it a **separate workflow triggered only by `workflow_dispatch`**, never by `push`.
  - Reuse `verify_channel()` and `create_post()` from `scripts/submit_candidate.py`.
  - Commit the image to `posts/` first and use the commit-SHA raw URL.
  - Composite the exact `assets/kisi-logo.png` onto the image. Don't trust a logo drawn by an image model.
  - Record the result in `data/published.json` in the same entry format.

---

## 5. Hard rules

1. Never redraw the logo. Use `assets/kisi-logo.png` bytes only.
2. Never publish an article older than 48 h. Never publish the same URL or headline twice.
3. Never mark something as published before Buffer confirms creation. Never fake a status.
4. Never create a workflow that publishes on `push`. That is how the old Koa post kept re-firing.
5. Never log or commit secrets. `BUFFER_API_KEY` lives only in GitHub Actions secrets.
6. Don't claim LinkedIn delivery unless Buffer returned `sent` (with `externalLink`) or someone checked the page.
7. Test changes with `dry_run=true` or a push (preview only) before running a live dispatch.
8. Don't scrape or automate a LinkedIn browser login. It breaks LinkedIn's terms and risks the page.

---

## 6. Buffer free plan limits that matter

- API: 100 requests / 15 min, 250 / 24 h, 3,000 / 30 days.
- Each post uses ≤14 requests (1 channel check, 1 create, ≤12 status polls). Worst case of 6 posts a day ≈ 2,500 a month, which is close to the cap.
  **Pending improvement:** reduce polling in `submit_candidate.py` to 4 polls spaced ~45 s apart.
- 10 queued posts per channel does not matter, because we use `shareNow`.
- If LinkedIn disconnects the channel, runs fail with `CHANNEL_VERIFY_FAILED`. The owner must reconnect it in the Buffer app.

---

## 7. Local testing (no network needed)

`publish_news.py` honours these environment variables:
- `KISI_FEED_FILE_<n>`: a local feed file used instead of feed `n`.
- `KISI_NOW=<ISO datetime>`: overrides "now".
- `KISI_SKIP_PAGE_FETCH=1`: skips the article fetch.
- `KISI_DRY_RUN=1`: allows rendering without the logo.

`submit_candidate.py` honours:
- `KISI_BUFFER_API=<url>`: a mock Buffer endpoint.
- `KISI_POLL_SECONDS`: the polling interval.

Tests already run offline:
- A stale story (>48 h) is rejected.
- A missing logo blocks publishing.
- Duplicate URL and headline are skipped.
- A failed delivery gets exactly one retry.
- A rerun after `POST_CREATED` does not double-post.

---

## 8. Ideas the owner is interested in (not built)

- Richer "informative" cards: 3 key takeaways, an audience tag (Admins/Devs/Architects) and simple icons, rendered in code so the text stays exact.
  - Takeaways could come from GitHub Models (free, rate-limited) via `GITHUB_TOKEN` with `models: read`. Verify current limits first.
  - It must fall back to the current card if the model is unavailable.
- Optional AI background art, only behind real text and the real logo. Most image APIs cost money, so disclose cost before adding one.

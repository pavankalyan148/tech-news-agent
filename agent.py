#!/usr/bin/env python3
"""
Dhairyam Daily Viral Agent (v2) — runs once a day on GitHub Actions (~10 AM IST).

Gives you 3 scripts a day: 1 Instagram, 1 YouTube (long video), 1 major tech news.
It follows the same method you use by hand:

INSTAGRAM (your manual method, automated)
  1. Find reels posted in the last 24 h with 100K+ likes, OR "fast" reels
     (lots of likes per hour since posting).
  2. Keep only reels that are educational AND fit the niche (tech, AI, science facts,
     defence, geopolitics, India stories). Gemini checks this.
  3. Note the like count, WAIT (default 75 min), check again.
  4. Keep reels that gained 5,000+ likes in that gap. The fastest grower wins.

YOUTUBE (long videos, small channels only)
  - Search windows 12 h -> 24 h -> 48 h (stops at the first window that finds one).
  - Long videos only (4 min+), channel must be SMALL (<= 300K subs by default) and
    the video must be an OUTLIER (views >= subscribers), 100K+ views, and 3+ comments
    from viewers in the last 3 h. Big channels / news networks (CNN, BBC, NDTV...) are
    never picked, even as a fallback.
  - Views are checked twice (before and after the wait) so you also see the speed.

MAJOR TECH NEWS
  - Windows 12 h -> 24 h -> 48 h. A story is "booming" when 3+ different outlets
    cover it (plus Hacker News reaction). The most-covered story wins.

SCRIPTS
  - The original video is used for the TOPIC only. Gemini researches the topic on
    Google Search, adds new facts and an India angle, and writes a fresh Tenglish
    script. It never says "according to CNN / this channel" and never copies lines.

OUTPUT
  - Google Doc: 3 scripts on top (with reference links for checking).
  - Google Sheet: "Creatives" tab = today's picks + all the numbers;
    "History" tab = every day's picks appended, so you can re-check the AI.
  - Gmail summary.
"""

import json
import os
import re
import smtplib
import time
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any

import feedparser
import requests
from google import genai
from google.oauth2 import service_account
from googleapiclient.discovery import build

# =============================================================================
# SECRETS (GitHub Actions secrets)
# =============================================================================
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
APIFY_API_TOKEN = os.environ["APIFY_API_TOKEN"]
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL") or GMAIL_ADDRESS

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID") or "1GEwWnaDXbYmGelRp8X4Q-XsLo6uT6WQyc-_DOnC__Iw"
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB") or "Creatives"
GOOGLE_HISTORY_TAB = os.getenv("GOOGLE_HISTORY_TAB") or "History"
GOOGLE_DOC_ID = os.getenv("GOOGLE_DOC_ID") or "1siaaj87QPg1_M_h8F3tftZYDFthMvtCJjQiQwDhUgPs"
SHEET_URL = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/edit"
DOC_URL = f"https://docs.google.com/document/d/{GOOGLE_DOC_ID}/edit"


# =============================================================================
# RULES (change any of these as GitHub "Variables" without touching code)
# =============================================================================
def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name) or default)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name) or default)
    except ValueError:
        return default


WAIT_MINUTES = env_int("WAIT_MINUTES", 75)            # gap between 1st and 2nd check

IG_MIN_LIKES = env_int("IG_MIN_LIKES", 100_000)        # viral in 24 h
IG_FAST_LIKES_PER_HOUR = env_int("IG_FAST_LIKES_PER_HOUR", 4_000)  # or "fast likes"
IG_MIN_GAIN = env_int("IG_MIN_GAIN", 5_000)            # likes gained during the wait
IG_RESULTS_PER_HASHTAG = env_int("IG_RESULTS_PER_HASHTAG", 10)
IG_RESULTS_PER_HANDLE = env_int("IG_RESULTS_PER_HANDLE", 2)
IG_MAX_TRACKED = env_int("IG_MAX_TRACKED", 8)          # reels re-checked after the wait
# Apify free plan ~ $0.0026 per result. 90/day ~ $7/month (under the $9 free credit).
APIFY_MAX_RESULTS_PER_DAY = env_int("APIFY_MAX_RESULTS_PER_DAY", 90)
_apify_used = 0

YT_MIN_VIEWS = env_int("YT_MIN_VIEWS", 100_000)
YT_MAX_SUBS = env_int("YT_MAX_SUBS", 300_000)          # "small channel"
YT_MIN_OUTLIER = env_float("YT_MIN_OUTLIER", 1.0)      # views / subscribers
YT_MIN_RECENT_COMMENTS = env_int("YT_MIN_RECENT_COMMENTS", 3)  # viewer comments in last 3 h
YT_MIN_DURATION_SEC = env_int("YT_MIN_DURATION_SEC", 240)      # long video = 4 min+

NEWS_MIN_SOURCES = env_int("NEWS_MIN_SOURCES", 3)
WINDOWS_HOURS = [12, 24, 48]

IST = timezone(timedelta(hours=5, minutes=30))
NOW_UTC = datetime.now(timezone.utc)
NOW_IST = NOW_UTC.astimezone(IST)

# What fits the pages (learned from @dr.dhairyam's top reels: phone/tech facts,
# defence & geopolitics, India real-hero stories, Telugu audience).
NICHE = """
Dhairyam pages (Telugu audience, India first):
- Tech & AI: new AI tools/models, big tech company moves, gadgets, how-tech-works facts
  (e.g. "how your phone knows you touched it"), cybersecurity, space & science breakthroughs.
- Defence & geopolitics: Indian armed forces, weapons tech, India vs world, US/China/Russia moves
  that affect India, global events that change everyday life (flights grounded, sanctions, wars).
- Educational facts & India real-hero stories (a doctor, police officer or common person doing
  something big), money/economy facts that matter to Indians.
NOT a fit: memes, dance, comedy skits, celebrity gossip, movie promotions, product ads,
religious/political party content, pure entertainment, sports highlights.
"""

# Big media / news networks: never treated as a "small channel" outlier.
BIG_MEDIA_BLOCKLIST = re.compile(
    r"\b(cnn|bbc|fox|nbc|abc news|cbs|msnbc|reuters|bloomberg|cnbc|al jazeera|sky news|"
    r"wion|ndtv|india today|aaj tak|times of india|republic|zee|abp|tv9|ntv|sakshi|etv|"
    r"news18|hindustan times|the hindu|firstpost|dw news|france 24|wsj|wall street journal|"
    r"new york times|washington post|guardian|vice|insider|the verge|wired|cnet|mkbhd|"
    r"linus tech tips|unbox therapy|mrwhosetheboss)\b",
    re.I,
)

YT_BASE_QUERIES = [
    "AI news", "new AI tool", "tech news today", "how it works technology",
    "India defence technology", "geopolitics India", "science breakthrough", "tech explained",
]

INSTA_HASHTAGS = [
    "technews", "ainews", "techfacts",
    "geopolitics", "indiandefence", "telugunews",
]
INSTA_STARTER_HANDLES = [
    "varunmayya", "thinkschool", "abhiandniyu", "techburner", "trakintech",
    "beebomco", "ai.for.everyone", "johnnyharris", "vaibhavsisinty", "ishansharma7390",
]

NEWS_FEEDS = [
    ("Google News Tech (US)", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en"),
    ("Google News Tech (IN)", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en"),
    ("Google News Science", "https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=en-US&gl=US&ceid=US:en"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("Wired", "https://www.wired.com/feed/rss"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
]
NEWS_SEARCH_TERMS = ["AI", "OpenAI OR Google OR Nvidia OR Apple OR Microsoft", "India technology", "cyber attack OR data breach"]


def log(msg: str) -> None:
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] {msg}", flush=True)


# =============================================================================
# GEMINI (auto-picks a model that exists today, so a renamed model never breaks the run)
# =============================================================================
gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def discover_gemini_models() -> list[str]:
    preferred = [m.strip() for m in (os.getenv("GEMINI_MODELS") or "").split(",") if m.strip()]
    found: list[str] = []
    try:
        for m in gemini_client.models.list():
            name = (m.name or "").replace("models/", "")
            actions = getattr(m, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            low = name.lower()
            if any(bad in low for bad in ("image", "tts", "live", "embed", "audio", "vision", "robotics", "nano")):
                continue
            if "gemini" in low and ("flash" in low or "pro" in low):
                found.append(name)
    except Exception as exc:
        log(f"[GEMINI WARN] could not list models: {exc}")

    def rank(n: str) -> tuple:
        nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)", n)[:1]] or [0.0]
        return (nums[0], "flash" in n and "lite" not in n, "preview" not in n and "exp" not in n)

    found.sort(key=rank, reverse=True)
    fallback = ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-pro"]
    models = list(dict.fromkeys(preferred + found[:6] + fallback))
    log(f"[GEMINI] model order: {models}")
    return models


GEMINI_MODELS = discover_gemini_models()


def call_gemini(prompt: str, want_json: bool = True, search: bool = False) -> Any:
    """Call Gemini with model fallback. search=True lets Gemini use Google Search (research)."""
    last_err: Exception | None = None
    for model_name in GEMINI_MODELS:
        for attempt in range(2):
            try:
                config: dict[str, Any] = {}
                if search:
                    config["tools"] = [{"google_search": {}}]
                elif want_json:
                    config["response_mime_type"] = "application/json"
                resp = gemini_client.models.generate_content(model=model_name, contents=prompt, config=config)
                text = (resp.text or "").strip()
                if not want_json:
                    return text
                text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
                return json.loads(text)
            except Exception as exc:
                last_err = exc
                log(f"[GEMINI WARN] {model_name} try {attempt + 1}: {str(exc)[:160]}")
                time.sleep(4)
    raise RuntimeError(f"All Gemini models failed: {last_err}")


def niche_filter(items: list[dict[str, Any]], text_key: str, kind: str) -> dict[str, dict[str, Any]]:
    """Ask Gemini which items fit the niche AND are educational. Returns {id: {fit, reason}}."""
    if not items:
        return {}
    payload = [{"id": it["id"], "text": str(it.get(text_key, ""))[:300]} for it in items]
    prompt = f"""You screen {kind} for these pages:
{NICHE}
For EACH item decide: is it in the niche AND educational/informative (teaches, explains or reports
something real), not entertainment? Be strict.
Return JSON: {{"results": [{{"id": "...", "fit": true/false, "topic": "3-6 word topic", "reason": "max 12 words"}}]}}
ITEMS: {json.dumps(payload, ensure_ascii=False)}"""
    try:
        data = call_gemini(prompt)
        return {str(r.get("id")): r for r in data.get("results", [])}
    except Exception as exc:
        log(f"[NICHE WARN] Gemini screening failed ({exc}); keeping all {kind}.")
        return {it["id"]: {"fit": True, "topic": "", "reason": "not screened (Gemini down)"} for it in items}


# =============================================================================
# HELPERS
# =============================================================================
def parse_iso8601_duration(duration_str: str) -> int:
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def hours_since(dt: datetime | None) -> float:
    if not dt:
        return 24.0
    return max((NOW_UTC - dt).total_seconds() / 3600, 0.25)


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def yt_get(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    params = {**params, "key": YOUTUBE_API_KEY}
    r = requests.get(f"https://www.googleapis.com/youtube/v3/{endpoint}", params=params, timeout=20)
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", "YouTube API error"))
    return data


# =============================================================================
# 1. MAJOR TECH NEWS (12 h -> 24 h -> 48 h, booming = covered by many outlets)
# =============================================================================
def collect_news(hours: int) -> list[dict[str, Any]]:
    cutoff = NOW_UTC - timedelta(hours=hours)
    feeds = list(NEWS_FEEDS)
    for term in NEWS_SEARCH_TERMS:
        q = requests.utils.quote(f"{term} when:{hours}h")
        feeds.append((f"Google News search: {term}", f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"))

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for feed_name, url in feeds:
        try:
            parsed = feedparser.parse(url)
            for entry in parsed.entries[:25]:
                pp = entry.get("published_parsed") or entry.get("updated_parsed")
                if not pp:
                    continue
                pub = datetime(*pp[:6], tzinfo=timezone.utc)
                if pub < cutoff:
                    continue
                title = (entry.get("title") or "").strip()
                link = (entry.get("link") or "").strip()
                outlet = (entry.get("source") or {}).get("title") or feed_name
                if title and link and title not in seen:
                    seen.add(title)
                    items.append({"id": f"n{len(items)}", "outlet": outlet, "title": title, "url": link,
                                  "hours_ago": round(hours_since(pub), 1)})
        except Exception as exc:
            log(f"[RSS WARN] {feed_name}: {exc}")

    try:
        cutoff_ts = int(cutoff.timestamp())
        r = requests.get("https://hn.algolia.com/api/v1/search", timeout=15, params={
            "tags": "story", "numericFilters": f"created_at_i>{cutoff_ts},points>100", "hitsPerPage": 25})
        for hit in r.json().get("hits", []):
            if hit.get("url") and hit.get("title"):
                items.append({"id": f"n{len(items)}", "outlet": "Hacker News",
                              "title": hit["title"], "url": hit["url"],
                              "hn_points": hit.get("points", 0), "hn_comments": hit.get("num_comments", 0)})
    except Exception as exc:
        log(f"[HN WARN] {exc}")
    return items


def run_news_waterfall() -> tuple[dict[str, Any] | None, str]:
    last_story: dict[str, Any] | None = None
    for hours in WINDOWS_HOURS:
        items = collect_news(hours)
        log(f"[NEWS] {hours}h window: {len(items)} headlines")
        if not items:
            continue
        prompt = f"""You are the news editor for Dhairyam Tech (Telugu, India-first audience).
Below are tech/science headlines from the last {hours} hours from many outlets.
1. Group headlines that are about the SAME story.
2. A story is "booming" if many DIFFERENT outlets cover it (count distinct outlets) and/or it has big
   Hacker News points. Skip deals, reviews, listicles, minor app updates and stock-price noise.
3. Prefer stories an Indian viewer would care about. Pick the ONE biggest story.
Return JSON: {{"title": "...", "summary": "2 sentences of what happened", "outlets": ["distinct outlet names"],
"outlet_count": N, "urls": ["2-4 exact URLs from the list"], "why_booming": "1 sentence"}}
HEADLINES: {json.dumps(items[:150], ensure_ascii=False)}"""
        try:
            story = call_gemini(prompt)
        except Exception as exc:
            log(f"[NEWS WARN] {exc}")
            continue
        count = int(story.get("outlet_count") or len(story.get("outlets", [])))
        valid_urls = {it["url"] for it in items}
        story["urls"] = [u for u in story.get("urls", []) if u in valid_urls] or [items[0]["url"]]
        story["outlet_count"] = count
        if count >= NEWS_MIN_SOURCES:
            return story, f"{hours}h window, covered by {count} outlets"
        log(f"[NEWS] best story in {hours}h only has {count} outlets; widening window.")
        last_story = story
    if last_story:
        return last_story, f"48h best available ({last_story.get('outlet_count')} outlets, below {NEWS_MIN_SOURCES})"
    return None, "No news found"


# =============================================================================
# 2. YOUTUBE: SMALL-CHANNEL LONG-VIDEO OUTLIERS (12 h -> 24 h -> 48 h)
# =============================================================================
def trending_queries() -> list[str]:
    prompt = f"""Give 6 short YouTube search queries (2-5 words) for what is trending RIGHT NOW
({NOW_IST.strftime('%d %b %Y')}) in this niche: {NICHE}
Return JSON: {{"queries": ["..."]}}"""
    try:
        return [q for q in call_gemini(prompt).get("queries", []) if isinstance(q, str)][:6]
    except Exception:
        return []


def fetch_videos(video_ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(video_ids), 50):
        data = yt_get("videos", {"part": "snippet,statistics,contentDetails,liveStreamingDetails",
                                 "id": ",".join(video_ids[i:i + 50])})
        for item in data.get("items", []):
            sn, st = item.get("snippet", {}), item.get("statistics", {})
            out[item["id"]] = {
                "id": item["id"],
                "title": sn.get("title", ""),
                "description": (sn.get("description") or "")[:400],
                "channel": sn.get("channelTitle", ""),
                "channel_id": sn.get("channelId", ""),
                "published": parse_dt(sn.get("publishedAt")),
                "views": int(st.get("viewCount", 0)),
                "comments": int(st.get("commentCount", 0)),
                "likes": int(st.get("likeCount", 0)),
                "duration_sec": parse_iso8601_duration(item.get("contentDetails", {}).get("duration", "")),
                "is_live": sn.get("liveBroadcastContent") != "none" or "liveStreamingDetails" in item,
                "url": f"https://www.youtube.com/watch?v={item['id']}",
            }
    return out


def fetch_subscribers(channel_ids: list[str]) -> dict[str, int]:
    subs: dict[str, int] = {}
    ids = list(dict.fromkeys(c for c in channel_ids if c))
    for i in range(0, len(ids), 50):
        data = yt_get("channels", {"part": "statistics", "id": ",".join(ids[i:i + 50])})
        for item in data.get("items", []):
            st = item.get("statistics", {})
            subs[item["id"]] = -1 if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0))
    return subs


def recent_viewer_comments(video_id: str, creator_id: str, hours: int = 3) -> int:
    try:
        data = yt_get("commentThreads", {"part": "snippet", "videoId": video_id, "order": "time", "maxResults": 100})
    except Exception:
        return 0
    cutoff = NOW_UTC - timedelta(hours=hours)
    n = 0
    for item in data.get("items", []):
        top = item["snippet"]["topLevelComment"]["snippet"]
        if (top.get("authorChannelId") or {}).get("value") == creator_id:
            continue
        if (parse_dt(top.get("publishedAt")) or NOW_UTC) >= cutoff:
            n += 1
    return n


def youtube_candidates(queries: list[str], hours: int) -> list[dict[str, Any]]:
    after = (NOW_UTC - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids: list[str] = []
    for q in queries:
        for extra in ({}, {"regionCode": "IN"}):
            try:
                data = yt_get("search", {"part": "id", "type": "video", "order": "viewCount", "maxResults": 50,
                                         "publishedAfter": after, "q": q, **extra})
                ids += [it["id"]["videoId"] for it in data.get("items", []) if it["id"].get("videoId")]
            except Exception as exc:
                log(f"[YT SEARCH WARN] {q}: {exc}")
                if "quota" in str(exc).lower():
                    return []
    ids = list(dict.fromkeys(ids))
    videos = fetch_videos(ids)
    subs = fetch_subscribers([v["channel_id"] for v in videos.values()])

    out = []
    for v in videos.values():
        v["subscribers"] = subs.get(v["channel_id"], -1)
        v["hours_live"] = round(hours_since(v["published"]), 1)
        v["outlier"] = round(v["views"] / v["subscribers"], 2) if v["subscribers"] > 0 else 0
        v["views_per_hour"] = int(v["views"] / v["hours_live"])
        if v["is_live"] or v["duration_sec"] < YT_MIN_DURATION_SEC:
            continue                      # long videos only
        if v["subscribers"] <= 0 or v["subscribers"] > YT_MAX_SUBS:
            continue                      # small channels only (hidden subs skipped)
        if BIG_MEDIA_BLOCKLIST.search(v["channel"]):
            continue                      # never news networks / big media
        out.append(v)
    log(f"[YT] {hours}h: {len(ids)} found -> {len(out)} small-channel long videos")
    return out


def run_youtube_waterfall() -> tuple[list[dict[str, Any]], str]:
    queries = list(dict.fromkeys(trending_queries() + YT_BASE_QUERIES))[:10]
    log(f"[YT] queries: {queries}")
    # One 48h search (saves API quota), then check 12h -> 24h -> 48h windows locally.
    cands = youtube_candidates(queries, max(WINDOWS_HOURS))
    screen = niche_filter(cands, "title", "YouTube videos")
    all_fits = []
    for v in cands:
        s = screen.get(v["id"], {})
        if s.get("fit"):
            v["topic"], v["fit_reason"] = s.get("topic", ""), s.get("reason", "")
            all_fits.append(v)
    relaxed_pool: list[dict[str, Any]] = all_fits
    checked: set[str] = set()
    for hours in WINDOWS_HOURS:
        fits = [v for v in all_fits if v["hours_live"] <= hours]
        strict = [v for v in fits if v["views"] >= YT_MIN_VIEWS and v["outlier"] >= YT_MIN_OUTLIER
                  and v["id"] not in checked]
        strict.sort(key=lambda v: (v["outlier"], v["views_per_hour"]), reverse=True)
        winners = []
        for v in strict[:8]:
            checked.add(v["id"])
            v["recent_comments"] = recent_viewer_comments(v["id"], v["channel_id"])
            if v["recent_comments"] >= YT_MIN_RECENT_COMMENTS:
                winners.append(v)
        if winners:
            return winners[:3], (f"{hours}h: small channel (<= {YT_MAX_SUBS:,} subs), long video, "
                                 f"{YT_MIN_VIEWS:,}+ views, views >= {YT_MIN_OUTLIER}x subs, "
                                 f"{YT_MIN_RECENT_COMMENTS}+ viewer comments in 3h")

    # Relaxed (still small channels + niche): best outlier ratio. Clearly labelled in the sheet.
    seen, relaxed = set(), []
    for v in sorted(relaxed_pool, key=lambda v: (v["outlier"], v["views"]), reverse=True):
        if v["id"] not in seen:
            seen.add(v["id"])
            if "recent_comments" not in v:
                v["recent_comments"] = recent_viewer_comments(v["id"], v["channel_id"])
            relaxed.append(v)
        if len(relaxed) >= 3:
            break
    return relaxed, "RELAXED 48h: no video passed all rules; best small-channel outliers shown"


def recheck_youtube(videos: list[dict[str, Any]]) -> None:
    if not videos:
        return
    fresh = fetch_videos([v["id"] for v in videos])
    for v in videos:
        f = fresh.get(v["id"])
        if f:
            v["view_gain"] = f["views"] - v["views"]
            v["comment_gain"] = f["comments"] - v["comments"]
            v["views_after"] = f["views"]


# =============================================================================
# 3. INSTAGRAM: SNAPSHOT -> WAIT -> RE-CHECK (your manual method)
# =============================================================================
def apify_run(actor: str, payload: dict[str, Any], max_items: int) -> list[dict[str, Any]]:
    """Run an Apify actor with a hard cap on paid results (daily budget guard)."""
    global _apify_used
    max_items = min(max_items, APIFY_MAX_RESULTS_PER_DAY - _apify_used)
    if max_items <= 0:
        log(f"[APIFY] daily budget of {APIFY_MAX_RESULTS_PER_DAY} results reached; skipping {actor}")
        return []
    try:
        r = requests.post(
            f"https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items",
            params={"token": APIFY_API_TOKEN, "maxItems": max_items, "maxTotalChargeUsd": round(max_items * 0.003, 2)},
            json=payload, timeout=300)
        if r.status_code in (200, 201) and isinstance(r.json(), list):
            items = r.json()[:max_items]
            _apify_used += len(items)
            log(f"[APIFY] {actor}: {len(items)} results (used {_apify_used}/{APIFY_MAX_RESULTS_PER_DAY} today)")
            return items
        log(f"[APIFY WARN] {actor}: HTTP {r.status_code} {r.text[:200]}")
    except Exception as exc:
        log(f"[APIFY WARN] {actor}: {exc}")
    return []


def reel_record(item: dict[str, Any]) -> dict[str, Any] | None:
    code = item.get("shortCode") or ""
    url = item.get("url") or (f"https://www.instagram.com/reel/{code}/" if code else "")
    if not url:
        return None
    if item.get("type") not in (None, "Video") and not item.get("videoUrl") and item.get("productType") != "clips":
        return None
    likes = int(item.get("likesCount") or 0)
    return {
        "id": code or url,
        "url": url,
        "owner": item.get("ownerUsername", ""),
        "caption": (item.get("caption") or "").replace("\n", " ")[:300],
        "likes": max(likes, 0),
        "plays": int(item.get("videoPlayCount") or item.get("videoViewCount") or 0),
        "comments": int(item.get("commentsCount") or 0),
        "published": parse_dt(item.get("timestamp")),
    }


def instagram_discover() -> list[dict[str, Any]]:
    raw = apify_run("apify~instagram-hashtag-scraper",
                    {"hashtags": INSTA_HASHTAGS, "resultsType": "reels", "resultsLimit": IG_RESULTS_PER_HASHTAG},
                    len(INSTA_HASHTAGS) * IG_RESULTS_PER_HASHTAG)
    raw += apify_run("apify~instagram-reel-scraper",
                     {"username": INSTA_STARTER_HANDLES, "resultsLimit": IG_RESULTS_PER_HANDLE},
                     len(INSTA_STARTER_HANDLES) * IG_RESULTS_PER_HANDLE)
    reels: dict[str, dict[str, Any]] = {}
    for item in raw:
        rec = reel_record(item)
        if rec:
            reels[rec["id"]] = rec
    return list(reels.values())


def run_instagram_snapshot() -> tuple[list[dict[str, Any]], str]:
    reels = instagram_discover()
    cutoff = NOW_UTC - timedelta(hours=24)
    fresh = [r for r in reels if r["published"] and r["published"] >= cutoff]
    for r in fresh:
        r["hours_live"] = round(hours_since(r["published"]), 1)
        r["likes_per_hour"] = int(r["likes"] / r["hours_live"])
    viral = [r for r in fresh if r["likes"] >= IG_MIN_LIKES or r["likes_per_hour"] >= IG_FAST_LIKES_PER_HOUR]
    log(f"[IG] {len(reels)} reels scraped, {len(fresh)} in last 24h, {len(viral)} viral/fast")

    label = f"24h, {IG_MIN_LIKES:,}+ likes or {IG_FAST_LIKES_PER_HOUR:,}+ likes/hour"
    if not viral:   # keep the fastest few so the second check still has something to watch
        viral = sorted(fresh, key=lambda r: r["likes_per_hour"], reverse=True)[:IG_MAX_TRACKED]
        label = "RELAXED: no reel hit the like rule; tracking fastest reels"

    screen = niche_filter(viral, "caption", "Instagram reels")
    tracked = []
    for r in sorted(viral, key=lambda r: r["likes_per_hour"], reverse=True):
        s = screen.get(r["id"], {})
        if s.get("fit"):
            r["topic"], r["fit_reason"] = s.get("topic", ""), s.get("reason", "")
            tracked.append(r)
    return tracked[:IG_MAX_TRACKED], label


def run_instagram_recheck(tracked: list[dict[str, Any]], label: str) -> tuple[list[dict[str, Any]], str]:
    if not tracked:
        return [], "No niche reel found in the last 24h"
    raw = apify_run("apify~instagram-scraper",
                    {"directUrls": [r["url"] for r in tracked], "resultsType": "details", "resultsLimit": 1},
                    len(tracked))
    now_by_id: dict[str, dict[str, Any]] = {}
    for item in raw:
        rec = reel_record(item)
        if rec:
            now_by_id[rec["id"]] = rec

    for r in tracked:
        after = now_by_id.get(r["id"])
        r["likes_after"] = after["likes"] if after else None
        r["like_gain"] = (after["likes"] - r["likes"]) if after else None
    measured = [r for r in tracked if r["like_gain"] is not None]
    passed = sorted([r for r in measured if r["like_gain"] >= IG_MIN_GAIN], key=lambda r: r["like_gain"], reverse=True)
    if passed:
        return passed[:3], f"{label}; +{IG_MIN_GAIN:,} likes in {WAIT_MINUTES} min (confirmed)"
    best = sorted(measured or tracked, key=lambda r: (r.get("like_gain") or 0, r["likes_per_hour"]), reverse=True)
    return best[:3], f"{label}; NOTE no reel gained {IG_MIN_GAIN:,} likes in {WAIT_MINUTES} min — best growers shown"


# =============================================================================
# 4. SCRIPTS: research the topic, then write OUR version (never copy, never "according to X")
# =============================================================================
STYLE = """DHAIRYAM SCRIPT STYLE (Tenglish = Telugu written in English letters, natural spoken Telugu).
Write it exactly like the EXAMPLE below: same flow, same energy, same line breaks.

STRUCTURE:
1. Line 1 = HOOK TITLE: shock question + the biggest number. e.g. "FIFA Hack aindhaa? 12 GB sensitive data leak!"
2. What happened: 2-3 lines with EXACT names, numbers, dates (who did it, what, how much).
3. Twist line starting "Kani ikkada shock ayye vishayam enti ante —" with the most surprising fact.
4. 1 line on the official response / real scale.
5. "Deeni nunchi manam nerchukovalsindi enti?" then a NUMBERED list 1. 2. 3. of points the original
   post did NOT say: a related recent case, how it works, and the India angle (our apps, jobs, money, safety).
6. One practical tip for the viewer + "follow avvandi" + ask for their opinion in comments.
- 900-1,500 characters. Short paragraphs separated by line breaks. Tech words stay in English.

HARD RULES:
- NEVER write "according to <channel/creator/outlet>", "ee video lo", "CNN cheppindi", "prakaram",
  or name the creator/channel/page we got the idea from. State facts directly; we are the source.
- NEVER copy sentences from the original post. Use it only for the TOPIC.
- Use ONLY facts from the research. Every number/name must come from it. No vague filler like
  "entho data" or "chala mandi" when the research has the real number.
- No greeting ("Hello Dhairyam Tech family" etc.) before the hook."""

STYLE_EXAMPLE = """FIFA Hack aindhaa? 12 GB sensitive data leak!
World's biggest football body FIFA paina oka pedda cyberattack jarigindi ani official ga confirm chesaru! Handsome Boy Hacking Club ane hacking group, FIFA nunchi 12 GB internal files steal chesamu ani claim chestondi. Indulo identity documents, financial records, mariyu executive info unnayi.
Kani ikkada shock ayye vishayam enti ante — hackers FIFA systems ni break cheyaledu, FIFA staff laaga natinchi (impersonation) internal files access chesaru!
Idi millions of fans data kaakapoyina, limited personal & business info leak ayyindi ani FIFA cheppindi.
Deeni nunchi manam nerchukovalsindi enti?

1. FIFA lanti global giant ke social engineering attacks nunchi tappinchukovadam kashtam aindi.
2. Recent ga Spain lo LaLiga fans data kuda hack ayyindi — ante sports bodies ippudu hackers main target!
3. India lo kuda manam use chese sports & ticketing apps lo mana data entha safe? Strong cybersecurity audits mariyu awareness chala avasaram.

Mee passwords ni regular ga change cheyandi, unknown emails & links ni nammakandi! Ilanti genuine tech & cyber updates kosam follow avvandi, and mee opinion kinda comments lo cheppandi!"""


def research(topic_hint: str, ref_urls: list[str]) -> tuple[str, list[str]]:
    """Google-Search-grounded research. Returns (facts text, real source links from grounding)."""
    prompt = f"""Use Google Search to research this story (today is {NOW_IST.strftime('%d %b %Y')}).
STORY: {topic_hint}
Known links about it: {', '.join(ref_urls[:3]) or 'none'}

Give hard facts only, as bullet points:
- WHO exactly (company, group, person names), WHAT happened, WHEN, and every NUMBER (GB, users, $, %, dates)
- HOW it happened (the method / the twist most people miss)
- Official response and real scale
- 1-2 similar RECENT cases (with names and dates)
- Why it matters for India / Indian users (apps, jobs, money, safety)
- 3 surprising facts
If something is not confirmed, say "unconfirmed"."""
    last_err: Exception | None = None
    models = [m for m in GEMINI_MODELS if "lite" not in m] or GEMINI_MODELS  # lite models research badly
    for model_name in models:
        try:
            resp = gemini_client.models.generate_content(
                model=model_name, contents=prompt, config={"tools": [{"google_search": {}}]})
            text = (resp.text or "").strip()
            sources: list[str] = []
            try:
                gm = resp.candidates[0].grounding_metadata
                for ch in (gm.grounding_chunks or []):
                    if ch.web and ch.web.uri:
                        sources.append(f"{ch.web.title or ''} {ch.web.uri}".strip())
            except Exception:
                pass
            if len(text) > 400:
                log(f"[RESEARCH] {model_name}: {len(text)} chars, {len(sources)} sources")
                return text, list(dict.fromkeys(sources))[:5]
            log(f"[RESEARCH WARN] {model_name} returned only {len(text)} chars")
        except Exception as exc:
            last_err = exc
            log(f"[RESEARCH WARN] {model_name}: {str(exc)[:160]}")
            time.sleep(3)
    log(f"[RESEARCH FAIL] {last_err}")
    return "", []


def parse_script_reply(text: str) -> dict[str, Any]:
    def section(name: str) -> str:
        m = re.search(rf"===\s*{name}\s*===\s*(.*?)(?====\s*[A-Z ]+\s*===|\Z)", text, re.S)
        return m.group(1).strip() if m else ""
    title = section("TITLE")
    added = [l.strip(" -*•\t") for l in section("ADDED").splitlines() if l.strip(" -*•\t")]
    return {"title": title.splitlines()[0] if title else "", "script": section("SCRIPT"), "new_points_added": added}


def script_problems(script: str) -> list[str]:
    probs = []
    if len(script) < 800:
        probs.append(f"too short ({len(script)} chars, need 900-1,500)")
    if len(re.findall(r"\d", script)) < 3:
        probs.append("not enough exact numbers/facts")
    if script.count("\n") < 4:
        probs.append("no line breaks / numbered list")
    if re.search(r"(?i)\b(according to|prakaram|ee video lo|ee reel lo)\b", script):
        probs.append("mentions the source/creator (not allowed)")
    return probs


def write_script(kind: str, topic_hint: str, ref_urls: list[str] | None = None) -> dict[str, Any]:
    ref_urls = ref_urls or []
    facts, sources = research(topic_hint, ref_urls)
    base = f"""You write {kind} scripts for the Dhairyam pages (Telugu audience, India first).
{STYLE}

EXAMPLE OF A PERFECT SCRIPT (copy this style, NOT its topic):
<<<
{STYLE_EXAMPLE}
>>>

TOPIC (from a viral post — use only the topic, do NOT copy it, do NOT mention who posted it):
{topic_hint}

RESEARCH (the only facts you may use):
{facts or "(research failed — use only facts stated in the topic above, keep it short)"}

Reply in EXACTLY this format, nothing else:
=== TITLE ===
<hook title, under 80 chars>
=== SCRIPT ===
<the full Tenglish script with line breaks, starting with the hook title line>
=== ADDED ===
- <each point we added that the original post did not have>"""
    out: dict[str, Any] = {}
    prompt = base
    for attempt in range(3):
        try:
            out = parse_script_reply(call_gemini(prompt, want_json=False))
        except Exception as exc:
            log(f"[SCRIPT WARN] {exc}")
            break
        probs = script_problems(out.get("script", ""))
        if not probs:
            break
        log(f"[SCRIPT] retry {attempt + 1}: {probs}")
        prompt = base + "\n\nYOUR LAST DRAFT HAD PROBLEMS: " + "; ".join(probs) + ". Fix them and reply again in the same format."
    if not out.get("script"):
        out = {"title": topic_hint[:80], "script": "[Gemini failed to write this script today]", "new_points_added": []}
    out["script"] = re.sub(r"(?i)\b(according to|as per)\s+[A-Z][\w .&-]{1,40}[,:]?\s*", "", out["script"])
    out["fact_sources"] = sources
    return out


# =============================================================================
# 5. GOOGLE SHEET + DOC + EMAIL
# =============================================================================
def google_creds():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    return service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/documents"])


SHEET_HEADER = ["Date (IST)", "Type", "Rank", "Title / Caption", "Creator / Outlet", "Subs", "Hours live",
                "Views / Likes (1st check)", "2nd check", f"Gain in {WAIT_MINUTES} min", "Outlier / Speed",
                "Recent comments", "Rule passed", "Why it fits (AI)", "Link", "Used for script?"]


def sheet_rows(yt, yt_rule, ig, ig_rule, news, news_rule) -> list[list[Any]]:
    date = NOW_IST.strftime("%d-%b-%Y %I:%M %p")
    rows = []
    for i, r in enumerate(ig):
        rows.append([date, "Instagram", i + 1, r["caption"][:200], "@" + r["owner"], "", r.get("hours_live", ""),
                     r["likes"], r.get("likes_after", ""), r.get("like_gain", ""), f"{r.get('likes_per_hour', 0):,} likes/h",
                     r["comments"], ig_rule, r.get("fit_reason", ""), r["url"], "YES" if i == 0 else ""])
    for i, v in enumerate(yt):
        rows.append([date, "YouTube", i + 1, v["title"], v["channel"], v["subscribers"], v.get("hours_live", ""),
                     v["views"], v.get("views_after", ""), v.get("view_gain", ""),
                     f"{v['outlier']}x subs | {v['views_per_hour']:,} views/h", v.get("recent_comments", ""),
                     yt_rule, v.get("fit_reason", ""), v["url"], "YES" if i == 0 else ""])
    if news:
        rows.append([date, "Tech News", 1, news.get("title", ""), ", ".join(news.get("outlets", []))[:200], "", "",
                     "", "", "", f"{news.get('outlet_count', '')} outlets", "", news_rule,
                     news.get("why_booming", ""), "\n".join(news.get("urls", [])), "YES"])
    if not rows:
        rows.append([date, "-", "", "Nothing qualified today", "", "", "", "", "", "", "", "", "", "", "", ""])
    return rows


def update_sheet(creds, rows: list[list[Any]]) -> None:
    sheets = build("sheets", "v4", credentials=creds).spreadsheets()
    tabs = {s["properties"]["title"] for s in sheets.get(spreadsheetId=GOOGLE_SHEET_ID).execute()["sheets"]}
    adds = [{"addSheet": {"properties": {"title": t}}} for t in (GOOGLE_SHEET_TAB, GOOGLE_HISTORY_TAB) if t not in tabs]
    if adds:
        sheets.batchUpdate(spreadsheetId=GOOGLE_SHEET_ID, body={"requests": adds}).execute()

    # Today's view
    sheets.values().clear(spreadsheetId=GOOGLE_SHEET_ID, range=f"'{GOOGLE_SHEET_TAB}'!A1:Z100").execute()
    sheets.values().update(spreadsheetId=GOOGLE_SHEET_ID, range=f"'{GOOGLE_SHEET_TAB}'!A1",
                           valueInputOption="USER_ENTERED",
                           body={"values": [[f"Today's picks — {NOW_IST.strftime('%d %b %Y %I:%M %p IST')}"],
                                            SHEET_HEADER] + rows}).execute()
    # History (append forever)
    existing = sheets.values().get(spreadsheetId=GOOGLE_SHEET_ID, range=f"'{GOOGLE_HISTORY_TAB}'!A1:A1").execute()
    body = rows if existing.get("values") else [SHEET_HEADER] + rows
    sheets.values().append(spreadsheetId=GOOGLE_SHEET_ID, range=f"'{GOOGLE_HISTORY_TAB}'!A1",
                           valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS",
                           body={"values": body}).execute()
    log("[SHEET] Creatives + History updated.")


def doc_section(n: int, label: str, s: dict[str, str] | None, ref_links: list[str], rule: str) -> str:
    if not s:
        return f"{n}. {label}\nNothing qualified today ({rule}).\n\n"
    added = "\n".join(f"  + {p}" for p in s.get("new_points_added", []))
    facts = "\n".join(f"  - {u}" for u in s.get("fact_sources", [])[:4])
    refs = "\n".join(f"  - {u}" for u in ref_links)
    return (f"{n}. {label}: {s.get('title', '')}  ({len(s.get('script', ''))} chars)\n"
            f"{s.get('script', '').strip()}\n\n"
            f"Our added points:\n{added}\n"
            f"Fact sources:\n{facts}\n"
            f"Original viral post (to re-check, NOT to read out):\n{refs}\n"
            f"Rule passed: {rule}\n\n")


def prepend_doc(creds, block: str) -> None:
    """Wipe the WHOLE Doc first, check it is empty, then write only today's scripts
    (old days stay in the Sheet's History tab)."""
    docs = build("docs", "v1", credentials=creds).documents()
    end = docs.get(documentId=GOOGLE_DOC_ID).execute()["body"]["content"][-1]["endIndex"]
    if end > 2:
        docs.batchUpdate(documentId=GOOGLE_DOC_ID, body={"requests": [
            {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end - 1}}}]}).execute()
    left = docs.get(documentId=GOOGLE_DOC_ID).execute()["body"]["content"][-1]["endIndex"]
    if left > 2:
        raise RuntimeError(f"Doc not empty after clearing (endIndex {left}); not writing so nothing gets mixed")
    log("[DOC] whole Doc cleared")
    docs.batchUpdate(documentId=GOOGLE_DOC_ID, body={"requests": [
        {"insertText": {"location": {"index": 1}, "text": block}}]}).execute()
    log("[DOC] today's scripts written")


def send_email(lines: list[str]) -> None:
    today = NOW_IST.strftime("%d %b %Y")
    items = "".join(f"<p>{l}</p>" for l in lines)
    html = (f"<div style='font-family:Arial;line-height:1.6;color:#111'><h2>Daily Viral Brief — {today}</h2>{items}"
            f"<hr/><p><a href='{DOC_URL}'>Open scripts (Doc)</a> · <a href='{SHEET_URL}'>Check links &amp; numbers (Sheet)</a></p></div>")
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = f"Daily Viral Brief — {today}", GMAIL_ADDRESS, RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [RECIPIENT_EMAIL], msg.as_string())
    log(f"[EMAIL] Sent to {RECIPIENT_EMAIL}.")


# =============================================================================
# MAIN
# =============================================================================
def safe(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception as exc:
        log(f"[ERROR] {fn.__name__}: {exc}")
        return default


def main() -> None:
    log(f"[START] {NOW_IST.isoformat()} | wait {WAIT_MINUTES} min between checks")

    # First check (snapshot)
    ig_tracked, ig_label = safe(run_instagram_snapshot, default=([], "Instagram step failed"))
    yt, yt_rule = safe(run_youtube_waterfall, default=([], "YouTube step failed"))
    news, news_rule = safe(run_news_waterfall, default=(None, "News step failed"))

    # Wait, then second check (like you do by sending it to your other account)
    if WAIT_MINUTES > 0 and (ig_tracked or yt):
        log(f"[WAIT] sleeping {WAIT_MINUTES} min before the 2nd check...")
        time.sleep(WAIT_MINUTES * 60)
    ig, ig_rule = safe(run_instagram_recheck, ig_tracked, ig_label, default=(ig_tracked[:3], ig_label))
    safe(recheck_youtube, yt)
    if yt:  # fastest-growing small-channel video first
        yt.sort(key=lambda v: (v.get("view_gain") or 0, v["outlier"]), reverse=True)

    # Scripts (topic only -> research -> our own version)
    s_ig = write_script("Instagram Reel", f"{ig[0].get('topic', '')}: {ig[0]['caption']}") if ig else None
    s_yt = write_script("YouTube long video", f"{yt[0].get('topic', '')}: {yt[0]['title']}. {yt[0]['description']}") if yt else None
    s_news = (write_script("tech news reel", f"{news['title']}. {news.get('summary', '')}", news.get("urls", []))
              if news else None)

    block = (f"==================================================\n"
             f"DATE: {NOW_IST.strftime('%d %b %Y — %I:%M %p IST')}\n"
             f"==================================================\n\n"
             + doc_section(1, "INSTAGRAM", s_ig, [ig[0]["url"]] if ig else [], ig_rule)
             + doc_section(2, "YOUTUBE", s_yt, [yt[0]["url"]] if yt else [], yt_rule)
             + doc_section(3, "MAJOR TECH NEWS", s_news, news.get("urls", []) if news else [], news_rule)
             + "\n")

    creds = google_creds()
    safe(update_sheet, creds, sheet_rows(yt, yt_rule, ig, ig_rule, news, news_rule))
    safe(prepend_doc, creds, block)
    safe(send_email, [
        f"<b>1. Instagram:</b> {(s_ig or {}).get('title', 'nothing qualified')} <i>({ig_rule})</i>",
        f"<b>2. YouTube:</b> {(s_yt or {}).get('title', 'nothing qualified')} <i>({yt_rule})</i>",
        f"<b>3. Tech news:</b> {(s_news or {}).get('title', 'nothing qualified')} <i>({news_rule})</i>",
    ])
    log("[DONE]")


if __name__ == "__main__":
    main()

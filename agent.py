#!/usr/bin/env python3
"""
Daily Viral Tech, AI, Geopolitics & Educational Outlier Agent (10:00 AM IST)
- YouTube Outlier + Cross-Upload Validation:
    * Small channel (<100K subs) with >100K views in 12-24h (Tech, AI, Global Viral, India Geopolitics)
    * >=10 comments in last 3 hours IGNORING creator's own comments
    * Cross-check if another creator uploaded the same topic with >=10K views (Long) or >=50K views (Short)
- Instagram Educational & Viral Reels (Any Follower Count, Last 12h):
    * No follower limit (1 to any followers), discovered via hashtags + curated handles
    * Prioritizes high-like/play educational, tech, AI, geopolitics reels in last 12h
- Major Tech & Breakthrough News (Past 3 Days / 72h, Any Website with High Reaction):
    * Hacker News top-reacted stories + Google News + Major Tech Blogs
- Updates Google Sheet ("Gemini Tech" -> Creatives tab) with 3 links per table
- Prepends 3 single-topic @dr.dhairyam-style Tenglish scripts (~1,500 chars + Source URL) to Google Doc
- Sends short 3-point summary + Sheet/Doc links to Gmail
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
# CONFIGURATION (Pre-filled with your Sheet & Doc IDs; secrets come from Env)
# =============================================================================
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
APIFY_API_TOKEN = os.environ["APIFY_API_TOKEN"]
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = os.environ["GMAIL_APP_PASSWORD"]
RECIPIENT_EMAIL = os.getenv("RECIPIENT_EMAIL", GMAIL_ADDRESS)

GOOGLE_SHEET_ID = os.getenv("GOOGLE_SHEET_ID", "1GEwWnaDXbYmGelRp8X4Q-XsLo6uT6WQyc-_DOnC__Iw")
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "Creatives")
GOOGLE_DOC_ID = os.getenv("GOOGLE_DOC_ID", "1siaaj87QPg1_M_h8F3tftZYDFthMvtCJjQiQwDhUgPs")

SHEET_URL = f"https://docs.google.com/spreadsheets/d/{GOOGLE_SHEET_ID}/edit"
DOC_URL = f"https://docs.google.com/document/d/{GOOGLE_DOC_ID}/edit"

IST = timezone(timedelta(hours=5, minutes=30))
NOW_UTC = datetime.now(timezone.utc)
NOW_IST = NOW_UTC.astimezone(IST)

# Major Tech, AI & Breakthrough News RSS (Past 3 Days / 72h)
MAJOR_TECH_NEWS_FEEDS = [
    ("Google News Tech", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en"),
    ("Google News India Tech", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en"),
    ("Google News AI & Science", "https://news.google.com/rss/search?q=AI+breakthrough+OR+Tesla+FSD+OR+Nvidia+OR+OpenAI+OR+Anthropic&hl=en-US&gl=US&ceid=US:en"),
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Wired", "https://www.wired.com/feed/rss"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
]

# Instagram Hashtags (discovers ANY creator from 1 follower to millions) + Starter Handles
INSTA_HASHTAGS = ["ainews", "technews", "geopolitics", "didyouknow", "sciencefacts"]
INSTA_STARTER_HANDLES = [
    "techburner",
    "trakintech",
    "beebomco",
    "varunmayya",
    "thinkschool",
    "mkbhd",
    "johnnyharris",
    "ai.for.everyone",
]

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3-flash-preview",
    "gemini-4-hydrogen",
]


# =============================================================================
# HELPERS
# =============================================================================
def parse_iso8601_duration(duration_str: str) -> int:
    """Convert YouTube ISO-8601 duration (e.g. PT1M30S) to total seconds."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration_str or "")
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = int(match.group(3) or 0)
    return hours * 3600 + minutes * 60 + seconds


def parse_rss_window(feeds: list[tuple[str, str]], hours_back: int = 72, max_per_feed: int = 8) -> list[dict[str, str]]:
    """Fetch RSS entries published within the last `hours_back` hours."""
    cutoff = NOW_UTC - timedelta(hours=hours_back)
    items: list[dict[str, str]] = []
    for source_name, url in feeds:
        try:
            parsed = feedparser.parse(url)
            count = 0
            for entry in parsed.entries:
                published_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
                if published_parsed:
                    pub_dt = datetime(*published_parsed[:6], tzinfo=timezone.utc)
                    if pub_dt < cutoff:
                        continue
                title = (entry.get("title") or "").strip()
                link = (entry.get("link") or "").strip()
                if title and link:
                    items.append({"source": source_name, "title": title, "url": link})
                    count += 1
                    if count >= max_per_feed:
                        break
        except Exception as exc:
            print(f"[RSS WARN] Failed to parse {source_name}: {exc}")
    return items


def fetch_hacker_news_viral_72h() -> list[dict[str, Any]]:
    """Fetch high-reaction stories from any website over the past 3 days via Hacker News Algolia API."""
    cutoff_ts = int((NOW_UTC - timedelta(hours=72)).timestamp())
    url = "https://hn.algolia.com/api/v1/search"
    params = {
        "tags": "story",
        "numericFilters": f"created_at_i>{cutoff_ts},points>150",
        "hitsPerPage": 20,
    }
    results: list[dict[str, Any]] = []
    try:
        resp = requests.get(url, params=params, timeout=15)
        for hit in resp.json().get("hits", []):
            story_url = hit.get("url")
            title = hit.get("title")
            if story_url and title:
                results.append(
                    {
                        "source": f"HN ({hit.get('points', 0)} pts, {hit.get('num_comments', 0)} comments)",
                        "title": title,
                        "url": story_url,
                        "points": int(hit.get("points", 0)),
                    }
                )
    except Exception as exc:
        print(f"[HN WARN] {exc}")
    return results


def call_gemini_json(prompt: str) -> Any:
    """Ask Gemini for strict JSON with automatic retry & active model fallback."""
    last_err: Exception | None = None
    for model_name in GEMINI_MODELS:
        for attempt in range(2):
            try:
                response = gemini_client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={"response_mime_type": "application/json"},
                )
                return json.loads(response.text)
            except Exception as exc:
                last_err = exc
                print(f"[GEMINI WARN] {model_name} attempt {attempt + 1} failed: {exc}")
                time.sleep(3)
    raise last_err


# =============================================================================
# 1. MAJOR TECH & BREAKTHROUGH NEWS (PAST 3 DAYS, HIGH REACTION, ANY WEBSITE)
# =============================================================================
def fetch_major_news_and_queries() -> tuple[list[dict[str, str]], list[str]]:
    """Pull past 72h high-reaction tech/AI/breakthrough news from any website + YT queries."""
    hn_items = fetch_hacker_news_viral_72h()
    rss_items = parse_rss_window(MAJOR_TECH_NEWS_FEEDS, hours_back=72, max_per_feed=7)
    combined = hn_items + rss_items

    prompt = f"""
You are a senior tech & AI editor for @dr.dhairyam.
From the past 3 days (72h) of stories below, pick ONLY the TOP 3 MAJOR tech, AI, science, or breakthrough stories that people are heavily reacting to (think: CEO changes at Nvidia/OpenAI, Tesla FSD V15, major math/AI breakthroughs like WSJ/Anthropic, etc. — skip minor gadget deals or routine listicles).

Return JSON with two keys:
1. "major_news": array of the top 3 major stories. Each object must have:
   - "title": clean headline
   - "source": publication/source
   - "url": exact URL copied verbatim from the input list
   - "why_viral": 1 short sentence explaining why people are reacting
2. "tech_queries": array of 6 short YouTube search queries (3-5 words each) covering Tech, AI, Global Viral News, and India Geopolitics right now.

CANDIDATE STORIES (PAST 72H):
{json.dumps(combined[:55], ensure_ascii=False)}
"""
    try:
        data = call_gemini_json(prompt)
        major_news = data.get("major_news", [])[:3]
        tech_queries = data.get("tech_queries", [])[:6]
    except Exception as exc:
        print(f"[FALLBACK] Gemini unavailable for news curation ({exc}); using top HN/RSS items.")
        major_news = [
            {
                "title": item["title"],
                "source": item["source"],
                "url": item["url"],
                "why_viral": "High reaction story from the past 72 hours.",
            }
            for item in combined[:3]
        ]
        tech_queries = [
            "AI news today",
            "India geopolitics news",
            "global viral news today",
            "tech breakthrough AI",
            "OpenAI Nvidia Tesla",
            "India tech news",
        ]

    if not tech_queries:
        tech_queries = ["AI news today", "India geopolitics", "global viral news", "tech breakthrough"]
    return major_news, tech_queries


# =============================================================================
# 2. YOUTUBE OUTLIER + CROSS-UPLOAD VALIDATION (IGNORING CREATOR COMMENTS)
# =============================================================================
def fetch_channel_subscribers(channel_ids: list[str]) -> dict[str, int]:
    """Batch-fetch subscriber counts for channel IDs (1 unit per 50 channels)."""
    subs_map: dict[str, int] = {}
    unique_ids = list(dict.fromkeys([c for c in channel_ids if c]))
    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i : i + 50]
        params = {"part": "statistics", "id": ",".join(batch), "key": YOUTUBE_API_KEY}
        try:
            r = requests.get("https://www.googleapis.com/youtube/v3/channels", params=params, timeout=15)
            for item in r.json().get("items", []):
                stats = item.get("statistics", {})
                subs_map[item["id"]] = -1 if stats.get("hiddenSubscriberCount") else int(stats.get("subscriberCount", 0))
        except Exception as exc:
            print(f"[YT CHANNEL WARN] {exc}")
    return subs_map


def count_non_creator_comments(video_id: str, creator_channel_id: str, hours: int = 3) -> int:
    """Count top-level comments in the last `hours` hours, IGNORING the creator's own comments."""
    url = "https://www.googleapis.com/youtube/v3/commentThreads"
    params = {
        "part": "snippet",
        "videoId": video_id,
        "order": "time",
        "maxResults": 40,
        "key": YOUTUBE_API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return 0
        cutoff = NOW_UTC - timedelta(hours=hours)
        recent_non_creator = 0
        for item in resp.json().get("items", []):
            top = item["snippet"]["topLevelComment"]["snippet"]
            author_id = (top.get("authorChannelId") or {}).get("value", "")
            if creator_channel_id and author_id == creator_channel_id:
                continue  # Ignore creator's own comments
            pub_dt = datetime.fromisoformat(top["publishedAt"].replace("Z", "+00:00"))
            if pub_dt >= cutoff:
                recent_non_creator += 1
        return recent_non_creator
    except Exception:
        return 0


def search_youtube_candidates(queries: list[str], hours_back: int) -> list[dict[str, Any]]:
    """Search YouTube for candidate videos published within `hours_back` hours."""
    published_after = (NOW_UTC - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    video_ids: list[str] = []
    seen: set[str] = set()

    all_queries = list(dict.fromkeys(queries + ["AI tech breakthrough", "India geopolitics global news"]))[:6]
    for q in all_queries:
        params = {
            "part": "snippet",
            "type": "video",
            "order": "viewCount",
            "maxResults": 10,
            "publishedAfter": published_after,
            "q": q,
            "key": YOUTUBE_API_KEY,
        }
        try:
            r = requests.get("https://www.googleapis.com/youtube/v3/search", params=params, timeout=15)
            for item in r.json().get("items", []):
                vid = item["id"].get("videoId")
                if vid and vid not in seen:
                    seen.add(vid)
                    video_ids.append(vid)
        except Exception as exc:
            print(f"[YT SEARCH WARN] {q}: {exc}")

    if not video_ids:
        return []

    raw_videos: list[dict[str, Any]] = []
    channel_ids: list[str] = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        params = {"part": "snippet,statistics,contentDetails", "id": ",".join(batch), "key": YOUTUBE_API_KEY}
        r = requests.get("https://www.googleapis.com/youtube/v3/videos", params=params, timeout=15)
        for item in r.json().get("items", []):
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            ch_id = snippet.get("channelId", "")
            channel_ids.append(ch_id)
            dur_sec = parse_iso8601_duration(item.get("contentDetails", {}).get("duration", ""))
            title = snippet.get("title", "")
            is_short = dur_sec <= 60 or "#shorts" in title.lower()
            raw_videos.append(
                {
                    "id": item["id"],
                    "title": title,
                    "channel": snippet.get("channelTitle", ""),
                    "channel_id": ch_id,
                    "views": int(stats.get("viewCount", 0)),
                    "total_comments": int(stats.get("commentCount", 0)),
                    "duration_sec": dur_sec,
                    "is_short": is_short,
                    "url": f"https://www.youtube.com/shorts/{item['id']}" if is_short else f"https://www.youtube.com/watch?v={item['id']}",
                }
            )

    subs_map = fetch_channel_subscribers(channel_ids)
    for v in raw_videos:
        v["subscribers"] = subs_map.get(v["channel_id"], 0)
    raw_videos.sort(key=lambda x: x["views"], reverse=True)
    return raw_videos


def check_cross_upload_traction(topic_title: str, exclude_video_id: str) -> dict[str, Any] | None:
    """
    Check if another creator uploaded the same topic in the last 24h and is getting
    fast engagement: >=10K views (Long) or >=50K views (Short).
    """
    published_after = (NOW_UTC - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    short_query = " ".join(re.findall(r"[A-Za-z0-9]+", topic_title)[:5])
    if not short_query:
        return None
    try:
        r = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "type": "video",
                "order": "viewCount",
                "maxResults": 6,
                "publishedAfter": published_after,
                "q": short_query,
                "key": YOUTUBE_API_KEY,
            },
            timeout=15,
        )
        ids = [
            it["id"]["videoId"]
            for it in r.json().get("items", [])
            if it["id"].get("videoId") and it["id"]["videoId"] != exclude_video_id
        ]
        if not ids:
            return None
        vr = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet,statistics,contentDetails", "id": ",".join(ids), "key": YOUTUBE_API_KEY},
            timeout=15,
        )
        for item in vr.json().get("items", []):
            views = int(item.get("statistics", {}).get("viewCount", 0))
            dur = parse_iso8601_duration(item.get("contentDetails", {}).get("duration", ""))
            title = item.get("snippet", {}).get("title", "")
            is_short = dur <= 60 or "#shorts" in title.lower()
            if (is_short and views >= 50_000) or (not is_short and views >= 10_000):
                return {
                    "id": item["id"],
                    "title": title,
                    "channel": item.get("snippet", {}).get("channelTitle", ""),
                    "views": views,
                    "format": "Short (50K+)" if is_short else "Long (10K+)",
                    "url": f"https://www.youtube.com/shorts/{item['id']}" if is_short else f"https://www.youtube.com/watch?v={item['id']}",
                }
    except Exception:
        pass
    return None


def run_youtube_waterfall(queries: list[str]) -> tuple[list[dict[str, Any]], str]:
    """
    Outlier rule:
      - Channel <100K subs getting >100K views in 12-24h window (48h fallback)
      - >=10 non-creator comments in last 3 hours
      - Cross-upload check: another creator on same topic getting >=10K (Long) or >=50K (Short)
    """
    candidates_24h = search_youtube_candidates(queries, hours_back=24)
    outliers: list[dict[str, Any]] = []

    for v in candidates_24h:
        if 0 <= v["subscribers"] < 100_000 and v["views"] > 100_000 and v["total_comments"] >= 10:
            non_creator_3h = count_non_creator_comments(v["id"], v["channel_id"], hours=3)
            if non_creator_3h >= 10:
                v["recent_comments"] = f"{non_creator_3h} non-creator in 3h"
                v["format"] = "Short" if v["is_short"] else "Long Video"
                cross = check_cross_upload_traction(v["title"], v["id"])
                v["cross_match"] = f"Yes: {cross['format']}" if cross else "Primary Outlier"
                outliers.append(v)
                if cross and len(outliers) < 3:
                    cross["subscribers"] = 0
                    cross["recent_comments"] = "Cross-upload match"
                    cross["cross_match"] = f"Matches #{len(outliers)}"
                    outliers.append(cross)
                if len(outliers) >= 3:
                    break

    if outliers:
        return outliers[:3], "24h Outlier (<100K subs, >100K views, 10+ non-creator comments/3h)"

    # 48h fallback with <100K subs prioritized
    candidates_48h = search_youtube_candidates(queries, hours_back=48)
    small_48h = [v for v in candidates_48h if 0 <= v["subscribers"] < 100_000][:3]
    chosen = small_48h if small_48h else candidates_48h[:3]
    for v in chosen:
        v["recent_comments"] = f"{v['total_comments']} total"
        v["format"] = "Short" if v["is_short"] else "Long Video"
        v["cross_match"] = "48h Fallback"
    return chosen, "48h Small-Channel Fallback"


# =============================================================================
# 3. INSTAGRAM EDUCATIONAL & VIRAL REELS (ANY FOLLOWER COUNT, LAST 12H)
# =============================================================================
def run_instagram_pipeline() -> tuple[list[dict[str, Any]], str]:
    """
    Any follower count (1 to millions), last 12h window (24h fallback),
    discovered via hashtags + starter handles, picking the 3 best educational/viral reels.
    """
    items: list[dict[str, Any]] = []

    # 1. Hashtag discovery (finds ANY creator regardless of follower count)
    try:
        r1 = requests.post(
            f"https://api.apify.com/v2/acts/apify~instagram-hashtag-scraper/run-sync-get-dataset-items?token={APIFY_API_TOKEN}",
            json={"hashtags": INSTA_HASHTAGS, "resultsType": "reels", "resultsLimit": 8},
            timeout=180,
        )
        if r1.status_code in (200, 201) and isinstance(r1.json(), list):
            items.extend(r1.json())
    except Exception as exc:
        print(f"[APIFY HASHTAG WARN] {exc}")

    # 2. Starter handles
    try:
        r2 = requests.post(
            f"https://api.apify.com/v2/acts/apify~instagram-reel-scraper/run-sync-get-dataset-items?token={APIFY_API_TOKEN}",
            json={"username": INSTA_STARTER_HANDLES, "resultsLimit": 2},
            timeout=180,
        )
        if r2.status_code in (200, 201) and isinstance(r2.json(), list):
            items.extend(r2.json())
    except Exception as exc:
        print(f"[APIFY REEL WARN] {exc}")

    cutoff_12h = NOW_UTC - timedelta(hours=12)
    cutoff_24h = NOW_UTC - timedelta(hours=24)
    pool_12h: list[dict[str, Any]] = []
    pool_24h: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for item in items:
        reel_url = item.get("url") or f"https://www.instagram.com/reel/{item.get('shortCode', '')}/"
        if not reel_url or reel_url in seen_urls:
            continue
        seen_urls.add(reel_url)

        ts = item.get("timestamp")
        pub_dt = None
        if ts:
            try:
                pub_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            except ValueError:
                pass

        likes = int(item.get("likesCount") or 0)
        plays = int(item.get("videoPlayCount") or item.get("videoViewCount") or 0)
        comments = int(item.get("commentsCount") or 0)
        rec = {
            "owner": item.get("ownerUsername", ""),
            "caption": (item.get("caption") or "").replace("\n", " ")[:220],
            "likes": likes,
            "plays": plays,
            "comments": comments,
            "url": reel_url,
        }
        if pub_dt is None or pub_dt >= cutoff_12h:
            pool_12h.append(rec)
        elif pub_dt >= cutoff_24h:
            pool_24h.append(rec)

    pool = pool_12h if len(pool_12h) >= 3 else (pool_12h + pool_24h)
    if not pool:
        return [], "No Instagram Reels found in last 24h"

    pool.sort(key=lambda x: (x["likes"], x["plays"]), reverse=True)
    label = "12h Educational/Viral Reels (Any Follower Count)" if len(pool_12h) >= 3 else "24h Educational/Viral Reels"

    if len(pool) <= 3:
        return pool, label

    prompt = f"""
Pick the best 3 Instagram Reels from the candidates below (NO follower restriction):
- Prioritize educational, tech, AI, science, or geopolitics Reels with massive likes/plays (e.g., 100K+ likes or highest engagement).
- Avoid pure memes or unrelated entertainment.
Return JSON: {{"selected_urls": ["url1", "url2", "url3"]}}

CANDIDATES:
{json.dumps(pool[:15], ensure_ascii=False)}
"""
    try:
        chosen = call_gemini_json(prompt).get("selected_urls", [])
        by_url = {p["url"]: p for p in pool}
        ranked = [by_url[u] for u in chosen if u in by_url]
        if ranked:
            return ranked[:3], label
    except Exception:
        pass
    return pool[:3], label


# =============================================================================
# 4. GENERATE 3 SINGLE-TOPIC @DR.DHAIRYAM TENGLISH SCRIPTS (~1,500 CHARS + SOURCE)
# =============================================================================
def generate_scripts_and_titles(
    yt_videos: list[dict[str, Any]],
    insta_reels: list[dict[str, Any]],
    major_news: list[dict[str, Any]],
) -> dict[str, Any]:
    """Write 3 separate single-topic @dr.dhairyam scripts (no mixing) + Source URLs."""
    best_yt = yt_videos[0] if yt_videos else None
    best_insta = insta_reels[0] if insta_reels else None
    best_news = major_news[0] if major_news else None

    prompt = f"""
You are the lead scriptwriter for the @dr.dhairyam page (Dhairyam Tech).
CRITICAL RULE: DO NOT MIX MULTIPLE STORIES!
- "script_insta" must be ONLY about the single #1 Instagram Reel below.
- "script_yt" must be ONLY about the single #1 YouTube Video below.
- "script_news" must be ONLY about the single #1 Major Tech News story below.

Return JSON with these exact keys:
- "yt_short_title": crisp YouTube-title-style headline (under 85 chars) for #1 YouTube story.
- "insta_short_title": crisp YouTube-title-style headline (under 85 chars) for #1 Instagram story.
- "news_short_title": crisp YouTube-title-style headline (under 85 chars) for #1 Major Tech News story.
- "script_insta": @dr.dhairyam Tenglish script (1,450-1,550 chars including spaces) ending with "\nSource: <exact URL>"
- "script_yt": @dr.dhairyam Tenglish script (1,450-1,550 chars including spaces) ending with "\nSource: <exact URL>"
- "script_news": @dr.dhairyam Tenglish script (1,450-1,550 chars including spaces) ending with "\nSource: <exact URL>"

@DR.DHAIRYAM STYLE:
- Conversational Telugu written strictly in English alphabet (Tenglish).
- Curiosity-driven opening hook, clear keyword-focused storytelling, signature @dr.dhairyam CTA, and exact Source URL at the bottom.

#1 YOUTUBE: {json.dumps(best_yt, ensure_ascii=False)}
#1 INSTAGRAM: {json.dumps(best_insta, ensure_ascii=False)}
#1 MAJOR TECH NEWS: {json.dumps(best_news, ensure_ascii=False)}
"""
    try:
        return call_gemini_json(prompt)
    except Exception as exc:
        print(f"[FALLBACK] Gemini unavailable for scripts ({exc}).")
        return {
            "yt_short_title": best_yt["title"][:85] if best_yt else "No qualifying YouTube video",
            "insta_short_title": best_insta["caption"][:85] if best_insta else "No qualifying Instagram Reel",
            "news_short_title": best_news["title"][:85] if best_news else "Top Major Tech News",
            "script_insta": f"[Gemini API busy (503).]\nSource: {best_insta['url'] if best_insta else 'N/A'}",
            "script_yt": f"[Gemini API busy (503).]\nSource: {best_yt['url'] if best_yt else 'N/A'}",
            "script_news": f"[Gemini API busy (503).]\nSource: {best_news['url'] if best_news else 'N/A'}",
        }


# =============================================================================
# 5. UPDATE GOOGLE SHEET & GOOGLE DOC
# =============================================================================
def get_google_credentials():
    info = json.loads(GOOGLE_SERVICE_ACCOUNT_JSON)
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/documents",
    ]
    return service_account.Credentials.from_service_account_info(info, scopes=scopes)


def update_google_sheet(
    creds,
    yt_videos: list[dict[str, Any]],
    yt_pass: str,
    insta_reels: list[dict[str, Any]],
    insta_pass: str,
    major_news: list[dict[str, Any]],
) -> None:
    sheets = build("sheets", "v4", credentials=creds)
    today_str = NOW_IST.strftime("%d-%b-%Y %I:%M %p IST")

    rows: list[list[Any]] = [
        [f"Daily Viral Tech & Outlier Tracker — Updated: {today_str}"],
        [],
        ["Youtube", f"Rule: {yt_pass}", "Subs / Format", "Views", "Comments / Cross-Match", "Link"],
    ]
    for idx in range(3):
        if idx < len(yt_videos):
            v = yt_videos[idx]
            sub_fmt = f"{v.get('subscribers', 0):,} subs | {v.get('format', '')}"
            cmt_info = f"{v.get('recent_comments', '')} | {v.get('cross_match', '')}"
            rows.append([idx + 1, v["title"], sub_fmt, v["views"], cmt_info, v["url"]])
        else:
            rows.append([idx + 1, "No qualifying video found", "", "", "", ""])

    rows.append([])
    rows.append(["Insta", f"Rule: {insta_pass}", "Account", "Likes", "Plays / Comments", "Link"])
    for idx in range(3):
        if idx < len(insta_reels):
            r = insta_reels[idx]
            pc = f"{r['plays']:,} plays / {r['comments']} cmts"
            rows.append([idx + 1, r["caption"], f"@{r['owner']}", r["likes"], pc, r["url"]])
        else:
            rows.append([idx + 1, "No qualifying reel found", "", "", "", ""])

    rows.append([])
    rows.append(["News", "Major Tech & Breakthrough News (Past 3 Days)", "Source", "Why Viral", "", "Link"])
    for idx in range(3):
        if idx < len(major_news):
            n = major_news[idx]
            rows.append([idx + 1, n["title"], n["source"], n.get("why_viral", ""), "", n["url"]])
        else:
            rows.append([idx + 1, "No item", "", "", "", ""])

    range_name = f"{GOOGLE_SHEET_TAB}!A1:F20"
    sheets.spreadsheets().values().clear(spreadsheetId=GOOGLE_SHEET_ID, range=range_name).execute()
    sheets.spreadsheets().values().update(
        spreadsheetId=GOOGLE_SHEET_ID,
        range=f"{GOOGLE_SHEET_TAB}!A1",
        valueInputOption="USER_ENTERED",
        body={"values": rows},
    ).execute()
    print("[SHEET] Updated Creatives tab successfully.")


def prepend_scripts_to_google_doc(creds, generated: dict[str, Any]) -> None:
    docs = build("docs", "v1", credentials=creds)
    today_header = NOW_IST.strftime("%d %b %Y — 10:00 AM IST")

    s_insta = generated.get("script_insta", "").strip()
    s_yt = generated.get("script_yt", "").strip()
    s_news = generated.get("script_news", "").strip()

    block = (
        f"==================================================\n"
        f"DATE: {today_header} (@dr.dhairyam Style)\n"
        f"==================================================\n\n"
        f"1. INSTAGRAM REEL SCRIPT (Single Topic | {len(s_insta)} chars)\n"
        f"{s_insta}\n\n"
        f"2. YOUTUBE SCRIPT (Single Topic | {len(s_yt)} chars)\n"
        f"{s_yt}\n\n"
        f"3. MAJOR TECH NEWS SCRIPT (Single Topic | {len(s_news)} chars)\n"
        f"{s_news}\n\n\n"
    )

    docs.documents().batchUpdate(
        documentId=GOOGLE_DOC_ID,
        body={"requests": [{"insertText": {"location": {"index": 1}, "text": block}}]},
    ).execute()
    print("[DOC] Prepended 3 @dr.dhairyam Tenglish scripts to Tech Gemini Doc.")


# =============================================================================
# 6. SEND DAILY 10:00 AM IST EMAIL SUMMARY (GMAIL SMTP)
# =============================================================================
def send_daily_email(generated: dict[str, Any]) -> None:
    today_label = NOW_IST.strftime("%d %b %Y")
    subject = f"Daily Viral Tech & Outlier Brief — {today_label}"

    yt_line = generated.get("yt_short_title", "No major YouTube outlier today")
    insta_line = generated.get("insta_short_title", "No major Instagram Reel today")
    news_line = generated.get("news_short_title", "Top Major Tech News updated")

    html_body = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111;">
      <h2 style="margin-bottom: 12px;">Daily Viral Brief ({today_label} — 10:00 AM IST)</h2>
      <p><strong>1. YouTube Outlier:</strong> {yt_line}</p>
      <p><strong>2. Instagram Educational/Viral:</strong> {insta_line}</p>
      <p><strong>3. Major Tech News (72h):</strong> {news_line}</p>
      <hr style="border: none; border-top: 1px solid #ddd; margin: 16px 0;" />
      <p>
        <strong>Google Sheet (All 3 Tables &amp; Links):</strong>
        <a href="{SHEET_URL}">Open Gemini Tech Sheet</a><br/>
        <strong>Google Doc (3 @dr.dhairyam Scripts + Sources):</strong>
        <a href="{DOC_URL}">Open Tech Gemini Doc</a>
      </p>
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = GMAIL_ADDRESS
    msg["To"] = RECIPIENT_EMAIL
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [RECIPIENT_EMAIL], msg.as_string())
    print(f"[EMAIL] Sent daily brief to {RECIPIENT_EMAIL}.")


# =============================================================================
# MAIN
# =============================================================================
def main() -> None:
    print(f"[START] Running Daily Tech & Outlier Agent at {NOW_IST.isoformat()}")
    major_news, tech_queries = fetch_major_news_and_queries()
    yt_videos, yt_pass = run_youtube_waterfall(tech_queries)
    insta_reels, insta_pass = run_instagram_pipeline()
    generated = generate_scripts_and_titles(yt_videos, insta_reels, major_news)

    creds = get_google_credentials()
    update_google_sheet(creds, yt_videos, yt_pass, insta_reels, insta_pass, major_news)
    prepend_scripts_to_google_doc(creds, generated)
    send_daily_email(generated)
    print("[DONE] Pipeline finished cleanly.")


if __name__ == "__main__":
    main()

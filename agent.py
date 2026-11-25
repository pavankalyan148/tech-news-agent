#!/usr/bin/env python3
"""
Daily Viral AI/Tech, Geopolitics & India News Agent (10:00 AM IST)
- YouTube Small-Channel Outlier Waterfall:
    <100K subscribers, >=200K views, >=10 comments in last 3h:
    24h Long -> 48h Long -> 24h Shorts -> 48h Shorts
- Instagram 6-8h Viral Filter (US & India, Talking Head / Faceless Voice):
    Tech / AI / Viral News (India/US) / Geopolitics, >70K plays/likes, >=10 comments
- Major News (Blogs/RSS Only):
    Indian politics, major viral news, major tech news, innovations for humanity
- Updates Google Sheet ("Gemini Tech" -> Creatives tab) with 3 links per table
- Prepends 3 single-topic @dr.dhairyam-style Tenglish scripts (~1,500 chars + Source URL)
  to Google Doc ("Tech Gemini") — NO mixing of multiple stories
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

# Curated RSS Feeds: Indian Politics, Major Viral News, Tech & Innovations for Humanity
INDIA_WORLD_NEWS_FEEDS = [
    ("Google News India", "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"),
    ("Google News India Politics", "https://news.google.com/rss/search?q=Indian+politics+parliament+government&hl=en-IN&gl=IN&ceid=IN:en"),
    ("Google News Innovations", "https://news.google.com/rss/search?q=breakthrough+innovation+science+humanity+medicine+energy&hl=en-IN&gl=IN&ceid=IN:en"),
    ("Google News India Tech", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en"),
    ("The Hindu National", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("Indian Express", "https://indianexpress.com/feed/"),
]

TECH_BLOG_FEEDS = [
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Wired", "https://www.wired.com/feed/rss"),
    ("MIT Tech Review", "https://www.technologyreview.com/feed/"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
    ("Inc42", "https://inc42.com/feed/"),
    ("ET Tech", "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms"),
]

# US & India Talking-Head / Faceless-Voice Tech, AI, Geopolitics & Viral News Accounts
INSTA_ACCOUNTS = [
    # India Tech / Talking Head / News
    "techburner",
    "trakintech",
    "beebomco",
    "varunmayya",
    "rajshamani",
    "thinkschool",
    "abhiandniyu",
    "ndtv",
    "indiatoday",
    # US Tech / AI / Geopolitics / Faceless & Talking Head
    "mkbhd",
    "ai.for.everyone",
    "theverge",
    "techcrunch",
    "johnnyharris",
    "reuters",
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


def parse_rss_last_24h(feeds: list[tuple[str, str]], max_per_feed: int = 8) -> list[dict[str, str]]:
    """Fetch RSS entries published in the last 24 hours."""
    cutoff = NOW_UTC - timedelta(hours=24)
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
# 1. MAJOR NEWS (INDIAN POLITICS, VIRAL NEWS, TECH, INNOVATIONS FOR HUMANITY)
# =============================================================================
def fetch_major_news_and_queries() -> tuple[list[dict[str, str]], list[str]]:
    """Pull last 24h RSS feeds and pick top 3 stories + YT search queries."""
    news_items = parse_rss_last_24h(INDIA_WORLD_NEWS_FEEDS, max_per_feed=8)
    tech_items = parse_rss_last_24h(TECH_BLOG_FEEDS, max_per_feed=6)

    prompt = f"""
You are a senior news editor for an Indian tech & current affairs creator (@dr.dhairyam).
From the last 24 hours of RSS items below, return JSON with two keys:
1. "major_news": array of the top 3 biggest viral stories across these exact categories:
   - Indian politics
   - Major viral news (India or World)
   - Major tech news
   - Innovations for humanity (science, medicine, clean energy, space, or life-changing tech)
   Pick the 3 strongest stories (preferring variety across these categories, with #1 being the single best story for a standalone script).
   Each object must have:
   - "title": clean headline
   - "category": one of ["Indian Politics", "Major Viral News", "Major Tech News", "Innovations for Humanity"]
   - "source": outlet name
   - "url": exact URL copied verbatim from the input list
   - "why_viral": 1 short sentence
2. "tech_queries": array of 6 short YouTube search queries (3-5 words each) covering the biggest AI, Tech, Innovation, or IT stories right now.

INDIA & INNOVATIONS NEWS RSS:
{json.dumps(news_items[:42], ensure_ascii=False)}

TECH BLOGS RSS:
{json.dumps(tech_items[:35], ensure_ascii=False)}
"""
    try:
        data = call_gemini_json(prompt)
        major_news = data.get("major_news", [])[:3]
        tech_queries = data.get("tech_queries", [])[:6]
    except Exception as exc:
        print(f"[FALLBACK] Gemini unavailable for RSS curation ({exc}); using direct RSS top items.")
        major_news = [
            {
                "title": item["title"],
                "category": "Major Viral News",
                "source": item["source"],
                "url": item["url"],
                "why_viral": "Top trending headline from last 24h RSS.",
            }
            for item in news_items[:3]
        ]
        tech_queries = [
            "AI news today",
            "new AI tool launch",
            "tech news today",
            "innovation technology",
            "OpenAI Google AI",
            "India tech startup news",
        ]

    if not tech_queries:
        tech_queries = ["AI news today", "new AI tool launch", "tech news today", "innovation technology"]
    return major_news, tech_queries


# =============================================================================
# 2. YOUTUBE SMALL-CHANNEL OUTLIER WATERFALL (<100K SUBS, >=200K VIEWS, >=10 COMMENTS/3H)
# =============================================================================
def fetch_channel_subscribers(channel_ids: list[str]) -> dict[str, int]:
    """Batch-fetch subscriber counts for channel IDs (1 unit per 50 channels)."""
    subs_map: dict[str, int] = {}
    unique_ids = list(dict.fromkeys([c for c in channel_ids if c]))
    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i : i + 50]
        params = {
            "part": "statistics",
            "id": ",".join(batch),
            "key": YOUTUBE_API_KEY,
        }
        try:
            r = requests.get("https://www.googleapis.com/youtube/v3/channels", params=params, timeout=15)
            for item in r.json().get("items", []):
                stats = item.get("statistics", {})
                if stats.get("hiddenSubscriberCount"):
                    subs_map[item["id"]] = -1
                else:
                    subs_map[item["id"]] = int(stats.get("subscriberCount", 0))
        except Exception as exc:
            print(f"[YT CHANNEL WARN] {exc}")
    return subs_map


def count_recent_yt_comments(video_id: str, hours: int = 3) -> int:
    """Count top-level comments posted within the last `hours` hours."""
    url = "https://www.googleapis.com/youtube/v3/commentThreads"
    params = {
        "part": "snippet",
        "videoId": video_id,
        "order": "time",
        "maxResults": 30,
        "key": YOUTUBE_API_KEY,
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return 0
        cutoff = NOW_UTC - timedelta(hours=hours)
        recent = 0
        for item in resp.json().get("items", []):
            pub_str = item["snippet"]["topLevelComment"]["snippet"]["publishedAt"]
            pub_dt = datetime.fromisoformat(pub_str.replace("Z", "+00:00"))
            if pub_dt >= cutoff:
                recent += 1
        return recent
    except Exception:
        return 0


def search_youtube_candidates(queries: list[str], hours_back: int) -> list[dict[str, Any]]:
    """Search YouTube for candidate videos published within `hours_back` hours."""
    published_after = (NOW_UTC - timedelta(hours=hours_back)).strftime("%Y-%m-%dT%H:%M:%SZ")
    video_ids: list[str] = []
    seen: set[str] = set()

    all_queries = list(dict.fromkeys(queries + ["AI news", "tech innovation"]))[:6]
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
        params = {
            "part": "snippet,statistics,contentDetails",
            "id": ",".join(batch),
            "key": YOUTUBE_API_KEY,
        }
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


def run_youtube_waterfall(queries: list[str]) -> tuple[list[dict[str, Any]], str]:
    """
    Exact Small-Channel Outlier Waterfall (<100K subs, >=200K views, >=10 comments in last 3h):
      1. 24h Long Videos (>60s)
      2. 48h Long Videos (>60s)
      3. 24h Shorts (<=60s)
      4. 48h Shorts (<=60s)
    """
    candidates_24h = search_youtube_candidates(queries, hours_back=24)
    candidates_48h = search_youtube_candidates(queries, hours_back=48)

    stages = [
        (candidates_24h, False, "24h Long (<100K subs, 200K+ views, 10+ comments/3h)"),
        (candidates_48h, False, "48h Long (<100K subs, 200K+ views, 10+ comments/3h)"),
        (candidates_24h, True, "24h Shorts (<100K subs, 200K+ views, 10+ comments/3h)"),
        (candidates_48h, True, "48h Shorts (<100K subs, 200K+ views, 10+ comments/3h)"),
    ]

    for pool, want_short, label in stages:
        matches: list[dict[str, Any]] = []
        for v in pool:
            subs = v["subscribers"]
            is_small_channel = 0 <= subs < 100_000
            if (
                v["is_short"] == want_short
                and is_small_channel
                and v["views"] >= 200_000
                and v["total_comments"] >= 10
            ):
                recent_3h = count_recent_yt_comments(v["id"], hours=3)
                if recent_3h >= 10:
                    v["recent_comments"] = f"{recent_3h} in last 3h"
                    v["format"] = "Short" if want_short else "Long Video"
                    matches.append(v)
                    if len(matches) >= 3:
                        break
        if matches:
            return matches, label

    # Fallback: prioritize small-channel (<100K subs) highest views in 48h, else overall top 3
    small_fallback = [v for v in candidates_48h if 0 <= v["subscribers"] < 100_000][:3]
    chosen = small_fallback if small_fallback else candidates_48h[:3]
    for v in chosen:
        v["recent_comments"] = f"{v['total_comments']} total"
        v["format"] = "Short" if v["is_short"] else "Long Video"
    return chosen, "48h Fallback (No video met full <100K subs + 200K views + 10/3h)"


# =============================================================================
# 3. INSTAGRAM 6-8H VIRAL FILTER (US & INDIA, >70K, >=10 COMMENTS, TALKING HEAD / FACELESS)
# =============================================================================
def scrape_insta_reels_apify(usernames: list[str], results_per_user: int = 2) -> list[dict[str, Any]]:
    """Run Apify Instagram Reel Scraper synchronously and return dataset items."""
    url = (
        "https://api.apify.com/v2/acts/apify~instagram-reel-scraper/"
        f"run-sync-get-dataset-items?token={APIFY_API_TOKEN}"
    )
    payload = {
        "username": usernames,
        "resultsLimit": results_per_user,
    }
    try:
        resp = requests.post(url, json=payload, timeout=180)
        if resp.status_code not in (200, 201):
            print(f"[APIFY WARN] HTTP {resp.status_code}: {resp.text[:200]}")
            return []
        return resp.json() if isinstance(resp.json(), list) else []
    except Exception as exc:
        print(f"[APIFY ERROR] {exc}")
        return []


def run_instagram_pipeline() -> tuple[list[dict[str, Any]], str]:
    """
    Exact Instagram rule:
      - Last 6-8 hours (8h cutoff)
      - Tech / AI / Viral News in India or US / Geopolitics
      - > 70,000 plays or likes AND >= 10 comments
      - Prioritize US & India talking-head or faceless-voice reels
    """
    raw_items = scrape_insta_reels_apify(INSTA_ACCOUNTS, results_per_user=2)
    cutoff_8h = NOW_UTC - timedelta(hours=8)
    cutoff_24h = NOW_UTC - timedelta(hours=24)

    strict_8h: list[dict[str, Any]] = []
    fallback_24h: list[dict[str, Any]] = []

    for item in raw_items:
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
        traction = max(likes, plays)

        record = {
            "owner": item.get("ownerUsername", ""),
            "caption": (item.get("caption") or "").replace("\n", " ")[:220],
            "likes": likes,
            "plays": plays,
            "comments": comments,
            "url": item.get("url") or f"https://www.instagram.com/reel/{item.get('shortCode', '')}/",
        }

        if traction > 70_000 and comments >= 10:
            if pub_dt is None or pub_dt >= cutoff_8h:
                strict_8h.append(record)
            elif pub_dt >= cutoff_24h:
                fallback_24h.append(record)

    pool = strict_8h if strict_8h else fallback_24h
    label = (
        "6-8h US/India (>70K & 10+ comments)"
        if strict_8h
        else "24h Fallback (>70K & 10+ comments; none in last 8h)"
    )

    if not pool:
        return [], "No Insta reel >70K & 10+ comments found"

    pool.sort(key=lambda x: max(x["plays"], x["likes"]), reverse=True)
    if len(pool) <= 3:
        return pool, label

    # Let Gemini rank for US/India Tech, AI, Geopolitics, Talking Head / Faceless Voice
    prompt = f"""
Select the top 3 Instagram Reels from the candidates below that best match:
- Topics: Tech, AI, Geopolitics, or major Viral News in India or US
- Format preference: Talking-head or faceless-voice explainers/news (avoid pure memes or unrelated entertainment)
Return JSON: {{"selected_urls": ["url1", "url2", "url3"]}}

CANDIDATES:
{json.dumps(pool[:12], ensure_ascii=False)}
"""
    try:
        chosen_urls = call_gemini_json(prompt).get("selected_urls", [])
        by_url = {p["url"]: p for p in pool}
        ranked = [by_url[u] for u in chosen_urls if u in by_url]
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
    """
    Pick ONLY the #1 best item per platform (NO mixing) and write 3 separate
    @dr.dhairyam-style Tenglish scripts (~1,500 chars each) with Source URL at the bottom.
    """
    best_yt = yt_videos[0] if yt_videos else None
    best_insta = insta_reels[0] if insta_reels else None
    best_news = major_news[0] if major_news else None

    prompt = f"""
You are the lead scriptwriter for the @dr.dhairyam Instagram/YouTube page (Dhairyam Tech).
CRITICAL RULE: DO NOT MIX MULTIPLE TOPICS!
- Write Script 1 ("script_insta") strictly about ONLY the single BEST INSTAGRAM REEL below.
- Write Script 2 ("script_yt") strictly about ONLY the single BEST YOUTUBE VIDEO below.
- Write Script 3 ("script_news") strictly about ONLY the single BEST MAJOR NEWS STORY below.

Return JSON with these exact keys:
- "yt_short_title": 1 crisp YouTube-title-style headline (under 85 chars) for the #1 YouTube story.
- "insta_short_title": 1 crisp YouTube-title-style headline (under 85 chars) for the #1 Instagram story.
- "news_short_title": 1 crisp YouTube-title-style headline (under 85 chars) for the #1 Major News story.
- "script_insta": @dr.dhairyam style Tenglish script (1,450-1,550 chars INCLUDING spaces) about ONLY the #1 Instagram Reel, ending with a new line: "Source: <exact URL>"
- "script_yt": @dr.dhairyam style Tenglish script (1,450-1,550 chars INCLUDING spaces) about ONLY the #1 YouTube Video, ending with a new line: "Source: <exact URL>"
- "script_news": @dr.dhairyam style Tenglish script (1,450-1,550 chars INCLUDING spaces) about ONLY the #1 Major News story, ending with a new line: "Source: <exact URL>"

@DR.DHAIRYAM SCRIPT STYLE RULES:
- Language: Natural conversational Telugu written strictly in English alphabet (Tenglish).
- Hook: Bold, curiosity-driven 1-2 line opening hook ("Meeku telusa...", "Idi chala pedda shock...").
- Body: Crisp, keyword-rich storytelling breaking down WHY it matters for the viewer, using only verified facts from that single item.
- Closing: Signature @dr.dhairyam call-to-action + "Source: <exact URL>" at the very bottom.

#1 BEST YOUTUBE VIDEO:
{json.dumps(best_yt, ensure_ascii=False)}

#1 BEST INSTAGRAM REEL:
{json.dumps(best_insta, ensure_ascii=False)}

#1 BEST MAJOR NEWS:
{json.dumps(best_news, ensure_ascii=False)}
"""
    try:
        return call_gemini_json(prompt)
    except Exception as exc:
        print(f"[FALLBACK] Gemini unavailable for script generation ({exc}); sending links & headlines.")
        return {
            "yt_short_title": best_yt["title"][:85] if best_yt else "No qualifying YouTube video today",
            "insta_short_title": best_insta["caption"][:85] if best_insta else "No qualifying Instagram Reel today",
            "news_short_title": best_news["title"][:85] if best_news else "Top India & World News updated",
            "script_insta": f"[Gemini API busy (503).]\nSource: {best_insta['url'] if best_insta else 'N/A'}",
            "script_yt": f"[Gemini API busy (503).]\nSource: {best_yt['url'] if best_yt else 'N/A'}",
            "script_news": f"[Gemini API busy (503).]\nSource: {best_news['url'] if best_news else 'N/A'}",
        }


# =============================================================================
# 5. UPDATE GOOGLE SHEET (3 TABLES IN "Creatives" TAB) & GOOGLE DOC
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
    """Write the 3 clean tables (Youtube, Insta, News) into the Creatives tab."""
    sheets = build("sheets", "v4", credentials=creds)
    today_str = NOW_IST.strftime("%d-%b-%Y %I:%M %p IST")

    rows: list[list[Any]] = [
        [f"Daily Viral Tech & India News Tracker — Updated: {today_str}"],
        [],
        ["Youtube", f"Rule: {yt_pass}", "Subs / Format", "Views", "Recent Comments", "Link"],
    ]
    for idx in range(3):
        if idx < len(yt_videos):
            v = yt_videos[idx]
            sub_fmt = f"{v.get('subscribers', 0):,} subs | {v.get('format', '')}"
            rows.append([idx + 1, v["title"], sub_fmt, v["views"], v.get("recent_comments", ""), v["url"]])
        else:
            rows.append([idx + 1, "No qualifying video found", "", "", "", ""])

    rows.append([])
    rows.append(["Insta", f"Rule: {insta_pass}", "Account", "Plays / Likes", "Comments", "Link"])
    for idx in range(3):
        if idx < len(insta_reels):
            r = insta_reels[idx]
            metrics = f"{r['plays']:,} plays / {r['likes']:,} likes"
            rows.append([idx + 1, r["caption"], f"@{r['owner']}", metrics, r["comments"], r["url"]])
        else:
            rows.append([idx + 1, "No qualifying reel found", "", "", "", ""])

    rows.append([])
    rows.append(["News", "Indian Politics, Viral, Tech & Innovations for Humanity", "Category", "Source", "Why Viral", "Link"])
    for idx in range(3):
        if idx < len(major_news):
            n = major_news[idx]
            rows.append([idx + 1, n["title"], n.get("category", ""), n["source"], n.get("why_viral", ""), n["url"]])
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
    """Insert today's 3 single-topic @dr.dhairyam scripts at the top of Tech Gemini Doc."""
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
        f"3. MAJOR NEWS SCRIPT (Single Topic | {len(s_news)} chars)\n"
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
    """Send short YouTube-title-style 3-point summary + Sheet & Doc links."""
    today_label = NOW_IST.strftime("%d %b %Y")
    subject = f"Daily Viral Tech & India News Brief — {today_label}"

    yt_line = generated.get("yt_short_title", "No major YouTube tech viral today")
    insta_line = generated.get("insta_short_title", "No major Instagram Reel viral today")
    news_line = generated.get("news_short_title", "Top India & World News updated")

    html_body = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.6; color: #111;">
      <h2 style="margin-bottom: 12px;">Daily Viral Brief ({today_label} — 10:00 AM IST)</h2>
      <p><strong>1. YouTube Major News:</strong> {yt_line}</p>
      <p><strong>2. Instagram Reel:</strong> {insta_line}</p>
      <p><strong>3. Major News:</strong> {news_line}</p>
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
    print(f"[START] Running Daily Tech & India News Agent at {NOW_IST.isoformat()}")
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

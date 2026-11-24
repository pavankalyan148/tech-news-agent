#!/usr/bin/env python3
"""
Daily Viral AI/Tech & India News Agent (10:00 AM IST)
- Scans Major World & India News (RSS/Blogs)
- Runs YouTube Waterfall Search (24h Long 1L+ & 10+ recent comments -> 24h Shorts 250K+ & 20+ comments -> 48h fallback)
- Runs Instagram Reels Search via Apify (24h Tech/AI 50K-100K+ Likes -> India Viral News 100K+ fallback)
- Updates Google Sheet ("Gemini Tech" -> Creatives tab) with 3 tables
- Appends 3 Telugu-transliterated (~1,500 chars each) scripts to Google Doc ("Tech Gemini")
- Sends short YouTube-title-style 3-point summary + Sheet/Doc links to Gmail
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

# Curated RSS Feeds: World & Majorly India News + Global Tech Blogs
INDIA_WORLD_NEWS_FEEDS = [
    ("Google News India", "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"),
    ("Google News India Tech", "https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en"),
    ("Google News World", "https://news.google.com/rss/headlines/section/topic/WORLD?hl=en-IN&gl=IN&ceid=IN:en"),
    ("The Hindu National", "https://www.thehindu.com/news/national/feeder/default.rss"),
    ("Indian Express", "https://indianexpress.com/feed/"),
    ("Mint", "https://www.livemint.com/rss/news"),
]

TECH_BLOG_FEEDS = [
    ("TechCrunch", "https://techcrunch.com/feed/"),
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Wired", "https://www.wired.com/feed/rss"),
    ("Ars Technica", "https://feeds.arstechnica.com/arstechnica/index"),
    ("VentureBeat AI", "https://venturebeat.com/category/ai/feed/"),
    ("Inc42", "https://inc42.com/feed/"),
    ("ET Tech", "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms"),
]

# Editable starter Instagram handles for Tech/AI/Innovation & India News fallback
INSTA_TECH_ACCOUNTS = [
    "techburner",
    "trakintech",
    "mkbhd",
    "beebomco",
    "mrwhosetheboss",
    "theverge",
    "techcrunch",
    "openai",
    "ai.for.everyone",
    "wealth",
]

INSTA_INDIA_NEWS_ACCOUNTS = [
    "ndtv",
    "indiatoday",
    "thehindu",
    "indianexpress",
    "timesofindia",
    "ani_trending",
    "bbcnewsindia",
]

gemini_client = genai.Client(api_key=GEMINI_API_KEY)
GEMINI_MODELS = [
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
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
                time.sleep(2)
    raise last_err


# =============================================================================
# 1. MAJOR NEWS (WORLD, MAJORLY INDIA) + TECH SEARCH QUERIES
# =============================================================================
def fetch_major_news_and_queries() -> tuple[list[dict[str, str]], list[str]]:
    """Pull last 24h RSS feeds and pick top viral World/India news + YT queries."""
    news_items = parse_rss_last_24h(INDIA_WORLD_NEWS_FEEDS, max_per_feed=8)
    tech_items = parse_rss_last_24h(TECH_BLOG_FEEDS, max_per_feed=6)

    prompt = f"""
You are a news editor for an Indian tech & current affairs creator.
From the last 24 hours of RSS items below, return JSON with two keys:
1. "major_news": array of the top 3 biggest viral news stories happening in the world, MAJORLY INDIA (from internet news/blogs only). Each object must have:
   - "title": clean headline
   - "source": outlet name
   - "url": exact URL copied verbatim from the input list
   - "why_viral": 1 short sentence
2. "tech_queries": array of 6 short, crisp YouTube search queries (3-5 words each) covering the biggest AI, Tech, Innovation, or IT stories from the tech blogs below.

INDIA & WORLD NEWS RSS:
{json.dumps(news_items[:40], ensure_ascii=False)}

TECH BLOGS RSS:
{json.dumps(tech_items[:35], ensure_ascii=False)}
"""
    data = call_gemini_json(prompt)
    major_news = data.get("major_news", [])[:3]
    tech_queries = data.get("tech_queries", [])[:6]
    if not tech_queries:
        tech_queries = ["AI news today", "new AI tool launch", "tech news today", "innovation technology"]
    return major_news, tech_queries


# =============================================================================
# 2. YOUTUBE WATERFALL SEARCH (EXACT USER RULES)
# =============================================================================
def count_recent_yt_comments(video_id: str, hours: int) -> int:
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

    all_queries = list(dict.fromkeys(queries + ["AI news", "tech news innovation"]))[:6]
    for q in all_queries:
        params = {
            "part": "snippet",
            "type": "video",
            "order": "viewCount",
            "maxResults": 8,
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

    videos: list[dict[str, Any]] = []
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
            dur_sec = parse_iso8601_duration(item.get("contentDetails", {}).get("duration", ""))
            title = snippet.get("title", "")
            is_short = dur_sec <= 60 or "#shorts" in title.lower()
            videos.append(
                {
                    "id": item["id"],
                    "title": title,
                    "channel": snippet.get("channelTitle", ""),
                    "views": int(stats.get("viewCount", 0)),
                    "total_comments": int(stats.get("commentCount", 0)),
                    "duration_sec": dur_sec,
                    "is_short": is_short,
                    "url": f"https://www.youtube.com/shorts/{item['id']}" if is_short else f"https://www.youtube.com/watch?v={item['id']}",
                }
            )
    videos.sort(key=lambda x: x["views"], reverse=True)
    return videos


def pick_3_yt_videos(matches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Prefer 3 videos on the same viral topic if available; else top 3 distinct topics."""
    if len(matches) <= 3:
        return matches
    prompt = f"""
Given these qualifying viral Tech/AI YouTube videos, pick exactly 3 video IDs:
- If at least 3 videos cover the SAME major viral topic, return those 3 IDs.
- Otherwise, return the top 3 videos covering DIFFERENT topics (prioritizing highest views).
Return JSON: {{"selected_ids": ["id1", "id2", "id3"]}}

VIDEOS:
{json.dumps([{"id": m["id"], "title": m["title"], "views": m["views"]} for m in matches])}
"""
    try:
        chosen = call_gemini_json(prompt).get("selected_ids", [])
        by_id = {m["id"]: m for m in matches}
        result = [by_id[i] for i in chosen if i in by_id]
        if len(result) == 3:
            return result
    except Exception:
        pass
    return matches[:3]


def run_youtube_waterfall(queries: list[str]) -> tuple[list[dict[str, Any]], str]:
    """
    Exact waterfall:
      Pass 1: 24h Long Videos (>60s), >=100K views, >=10 comments in last 3h
      Pass 2: 24h Shorts (<=60s), >=250K views, >=20 comments in last 24h
      Pass 3: 48h Long Videos (same rules), then 48h Shorts (same rules)
    Returns (up to 3 videos, pass_label).
    """
    for window_hours in (24, 48):
        candidates = search_youtube_candidates(queries, hours_back=window_hours)

        long_matches = []
        for v in candidates:
            if not v["is_short"] and v["views"] >= 100_000 and v["total_comments"] >= 10:
                recent_3h = count_recent_yt_comments(v["id"], hours=3)
                if recent_3h >= 10:
                    v["recent_comments"] = f"{recent_3h} in last 3h"
                    v["format"] = "Long Video"
                    long_matches.append(v)
                    if len(long_matches) >= 6:
                        break
        if long_matches:
            return pick_3_yt_videos(long_matches), f"{window_hours}h Long (1L+ & 10+ comments/3h)"

        short_matches = []
        for v in candidates:
            if v["is_short"] and v["views"] >= 250_000 and v["total_comments"] >= 20:
                recent_24h = count_recent_yt_comments(v["id"], hours=24)
                if recent_24h >= 20:
                    v["recent_comments"] = f"{recent_24h} in last 24h"
                    v["format"] = "Short"
                    short_matches.append(v)
                    if len(short_matches) >= 6:
                        break
        if short_matches:
            return pick_3_yt_videos(short_matches), f"{window_hours}h Shorts (250K+ & 20+ comments/24h)"

    fallback = search_youtube_candidates(queries, hours_back=48)[:3]
    for v in fallback:
        v["recent_comments"] = f"{v['total_comments']} total"
        v["format"] = "Short" if v["is_short"] else "Long Video"
    return fallback, "48h Top Fallback (Thresholds not fully met)"


# =============================================================================
# 3. INSTAGRAM REELS SEARCH VIA APIFY (EXACT USER RULES)
# =============================================================================
def scrape_insta_reels_apify(usernames: list[str], results_per_user: int = 3) -> list[dict[str, Any]]:
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


def filter_insta_last_24h(items: list[dict[str, Any]], min_likes: int) -> list[dict[str, Any]]:
    """Filter Apify Reel items to last 24 hours and minimum likes."""
    cutoff = NOW_UTC - timedelta(hours=24)
    filtered = []
    for item in items:
        ts = item.get("timestamp")
        if ts:
            try:
                pub_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if pub_dt < cutoff:
                    continue
            except ValueError:
                pass
        likes = int(item.get("likesCount") or 0)
        plays = int(item.get("videoPlayCount") or item.get("videoViewCount") or 0)
        if likes >= min_likes:
            reel_url = item.get("url") or f"https://www.instagram.com/reel/{item.get('shortCode', '')}/"
            filtered.append(
                {
                    "owner": item.get("ownerUsername", ""),
                    "caption": (item.get("caption") or "").replace("\n", " ")[:180],
                    "likes": likes,
                    "plays": plays,
                    "url": reel_url,
                }
            )
    filtered.sort(key=lambda x: x["likes"], reverse=True)
    return filtered


def run_instagram_pipeline() -> tuple[list[dict[str, Any]], str]:
    """
    Exact Instagram rule:
      Pass 1: Last 24h Tech/AI/Innovation/IT Reels with >= 50,000 Likes (50K-100K+)
      Pass 2 (Fallback): India Viral News ONLY with >= 100,000 Likes/Plays
    """
    tech_raw = scrape_insta_reels_apify(INSTA_TECH_ACCOUNTS, results_per_user=3)
    tech_matches = filter_insta_last_24h(tech_raw, min_likes=50_000)
    if tech_matches:
        return tech_matches[:3], "24h Tech/AI Reels (50K+ Likes)"

    news_raw = scrape_insta_reels_apify(INSTA_INDIA_NEWS_ACCOUNTS, results_per_user=3)
    news_candidates = []
    cutoff = NOW_UTC - timedelta(hours=24)
    for item in news_raw:
        ts = item.get("timestamp")
        if ts:
            try:
                if datetime.fromisoformat(str(ts).replace("Z", "+00:00")) < cutoff:
                    continue
            except ValueError:
                pass
        likes = int(item.get("likesCount") or 0)
        plays = int(item.get("videoPlayCount") or item.get("videoViewCount") or 0)
        if likes >= 100_000 or plays >= 100_000:
            news_candidates.append(
                {
                    "owner": item.get("ownerUsername", ""),
                    "caption": (item.get("caption") or "").replace("\n", " ")[:180],
                    "likes": likes,
                    "plays": plays,
                    "url": item.get("url") or f"https://www.instagram.com/reel/{item.get('shortCode', '')}/",
                }
            )
    news_candidates.sort(key=lambda x: max(x["likes"], x["plays"]), reverse=True)
    return news_candidates[:3], "24h India Viral News Reels Fallback (100K+)"


# =============================================================================
# 4. GENERATE 3 TENGLISH SCRIPTS (~1,500 CHARS EACH) + EMAIL TITLES
# =============================================================================
def generate_scripts_and_titles(
    yt_videos: list[dict[str, Any]],
    insta_reels: list[dict[str, Any]],
    major_news: list[dict[str, Any]],
) -> dict[str, Any]:
    """Generate 3 ~1,500-char Telugu-in-English-transliteration scripts & 3 short titles."""
    prompt = f"""
You are a Telugu tech & viral news scriptwriter for Dhairyam Tech (D.tech).
Using ONLY the verified inputs below, return JSON with these exact keys:
- "yt_short_title": 1 crisp YouTube-title-style headline (under 85 chars) summarizing the YouTube Major News.
- "insta_short_title": 1 crisp YouTube-title-style headline (under 85 chars) summarizing the Instagram Reel trend.
- "news_short_title": 1 crisp YouTube-title-style headline (under 85 chars) summarizing the Major India/World News.
- "script_insta": Keyword-driven script in TELUGU WRITTEN IN ENGLISH TRANSLITERATION (Tenglish), strictly between 1,450 and 1,550 characters INCLUDING SPACES, based on the Instagram Reel item(s).
- "script_yt": Keyword-driven script in TELUGU WRITTEN IN ENGLISH TRANSLITERATION (Tenglish), strictly between 1,450 and 1,550 characters INCLUDING SPACES, based on the YouTube video(s).
- "script_news": Keyword-driven script in TELUGU WRITTEN IN ENGLISH TRANSLITERATION (Tenglish), strictly between 1,450 and 1,550 characters INCLUDING SPACES, based on the Major News item(s).

SCRIPT STYLE RULES:
- Language: Natural conversational Telugu written strictly in English alphabet (Tenglish).
- Focus on sharp keywords, high-retention hook, verified facts, and punchy closing CTA.
- Target length: ~1,500 characters including spaces per script.

YOUTUBE DATA:
{json.dumps(yt_videos, ensure_ascii=False)}

INSTAGRAM DATA:
{json.dumps(insta_reels, ensure_ascii=False)}

MAJOR INDIA/WORLD NEWS DATA:
{json.dumps(major_news, ensure_ascii=False)}
"""
    return call_gemini_json(prompt)


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
        ["Youtube", f"Rule Matched: {yt_pass}", "Format", "Views", "Recent Comments", "Link"],
    ]
    for idx in range(3):
        if idx < len(yt_videos):
            v = yt_videos[idx]
            rows.append([idx + 1, v["title"], v.get("format", ""), v["views"], v.get("recent_comments", ""), v["url"]])
        else:
            rows.append([idx + 1, "No qualifying video found", "", "", "", ""])

    rows.append([])
    rows.append(["Insta", f"Rule Matched: {insta_pass}", "Account", "Likes", "Plays", "Link"])
    for idx in range(3):
        if idx < len(insta_reels):
            r = insta_reels[idx]
            rows.append([idx + 1, r["caption"], f"@{r['owner']}", r["likes"], r["plays"], r["url"]])
        else:
            rows.append([idx + 1, "No qualifying reel found", "", "", "", ""])

    rows.append([])
    rows.append(["News", "Major World & India Viral News (Blogs/News Only)", "Source", "Why Viral", "", "Link"])
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
    """Insert today's 3 Tenglish scripts at the top of the Tech Gemini Google Doc."""
    docs = build("docs", "v1", credentials=creds)
    today_header = NOW_IST.strftime("%d %b %Y — 10:00 AM IST")

    s_insta = generated.get("script_insta", "").strip()
    s_yt = generated.get("script_yt", "").strip()
    s_news = generated.get("script_news", "").strip()

    block = (
        f"==================================================\n"
        f"DATE: {today_header}\n"
        f"==================================================\n\n"
        f"1. INSTAGRAM REEL SCRIPT (Tenglish | {len(s_insta)} chars)\n"
        f"{s_insta}\n\n"
        f"2. YOUTUBE SCRIPT (Tenglish | {len(s_yt)} chars)\n"
        f"{s_yt}\n\n"
        f"3. MAJOR NEWS SCRIPT (Tenglish | {len(s_news)} chars)\n"
        f"{s_news}\n\n\n"
    )

    docs.documents().batchUpdate(
        documentId=GOOGLE_DOC_ID,
        body={"requests": [{"insertText": {"location": {"index": 1}, "text": block}}]},
    ).execute()
    print("[DOC] Prepended 3 Tenglish scripts to Tech Gemini Doc.")


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
        <strong>Google Doc (3 Tenglish Scripts ~1,500 chars each):</strong>
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

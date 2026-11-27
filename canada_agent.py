#!/usr/bin/env python3
"""
Canada-India Daily Agent — runs once a day on GitHub Actions (scripts by ~6 PM IST).

Every day it collects the last 24 h of:
  1. Canada-India relations + Canada immigration news (IRCC, Express Entry, PGWP, study
     permits, PR, work permits, visas) and any MAJOR news that affects Indians living
     overseas (US / UK / Gulf / Australia visas, deportations, safety, remittances...).
  2. YouTube videos on these topics (YouTube Data API), ranked by views per hour.
  3. Canada JOBS from Job Bank (official RSS): LMIA / open-to-abroad postings for Indians in India,
     and postings open to people already in Canada on a PGWP, PR or work permit. IT + non-IT,
     freshers first. Each posting page is checked for "who can apply" and LMIA status.

Then it writes 2 scripts (Telugu in English letters):
  Script 1 = the biggest immigration / Canada-India / overseas-Indians story of the day.
  Script 2 = the best jobs update (or the 2nd biggest story if no real jobs were found).

OUTPUT: Google Doc (newest day on top: 2 scripts, then every story + job with links) and
a Gmail summary. Uses the same secrets as the tech-news agent, plus CANADA_DOC_ID.
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
# SECRETS / SETTINGS
# =============================================================================
YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS") or ""
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD") or ""
RECIPIENT_EMAIL = os.getenv("CANADA_RECIPIENT_EMAIL") or os.getenv("RECIPIENT_EMAIL") or GMAIL_ADDRESS
CANADA_DOC_ID = os.getenv("CANADA_DOC_ID") or "1aTDnISq8XeSXbh8_8TLujVDsIFCH8sBbx8yFad-lMfo"
DOC_URL = f"https://docs.google.com/document/d/{CANADA_DOC_ID}/edit"
HOURS = int(os.getenv("CANADA_HOURS") or 24)

IST = timezone(timedelta(hours=5, minutes=30))
NOW_UTC = datetime.now(timezone.utc)
NOW_IST = NOW_UTC.astimezone(IST)
TODAY = NOW_IST.strftime("%d %b %Y")

NEWS_QUERIES = [
    "Canada India relations",
    "Canada immigration",
    "IRCC new rules",
    "Express Entry draw",
    "PGWP OR \"post-graduation work permit\"",
    "Canada study permit Indian students",
    "Canada PR OR \"permanent residence\" Canada",
    "Canada work permit OR LMIA",
    "Canada jobs Indians OR \"hiring in Canada\"",
    "Indian students abroad OR NRI news",
    "H-1B OR US visa Indians",
    "UK visa OR Australia visa Indians",
    "Indians in Gulf OR UAE visa rules",
    "deported Indians OR Indian diaspora",
]
# region = (hl, gl, ceid)  India + Canada editions so both sides of the story are covered
NEWS_REGIONS = [("en-IN", "IN", "IN:en"), ("en-CA", "CA", "CA:en")]
OFFICIAL_FEEDS = [
    ("IRCC news (canada.ca)",
     "https://api.io.canada.ca/io-server/gc/news/en/v2?dept=departmentofcitizenshipandimmigration"
     "&sort=publishedDate&orderBy=desc&pick=30&format=atom"),
]
YT_QUERIES = [
    "Canada immigration news",
    "Canada jobs for Indians",
    "Express Entry draw",
    "PGWP Canada update",
    "Canada India news",
    "Canada telugu",
]


def log(msg: str) -> None:
    print(f"[{datetime.now(IST).strftime('%H:%M:%S')}] {msg}", flush=True)


def safe(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception as exc:
        log(f"[ERROR] {fn.__name__}: {exc}")
        return default


# =============================================================================
# GEMINI
# =============================================================================
gemini_client = genai.Client(api_key=GEMINI_API_KEY)


def discover_models() -> list[str]:
    preferred = [m.strip() for m in (os.getenv("GEMINI_MODELS") or "").split(",") if m.strip()]
    found: list[str] = []
    try:
        for m in gemini_client.models.list():
            name = (m.name or "").replace("models/", "")
            actions = getattr(m, "supported_actions", None) or []
            if actions and "generateContent" not in actions:
                continue
            low = name.lower()
            if any(b in low for b in ("image", "tts", "live", "embed", "audio", "vision", "robotics", "nano")):
                continue
            if "gemini" in low and ("flash" in low or "pro" in low):
                found.append(name)
    except Exception as exc:
        log(f"[GEMINI WARN] list models: {exc}")

    def rank(n: str) -> tuple:
        nums = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)", n)[:1]] or [0.0]
        return (nums[0], "flash" in n and "lite" not in n, "preview" not in n and "exp" not in n)

    found.sort(key=rank, reverse=True)
    models = list(dict.fromkeys(preferred + found[:6] + ["gemini-flash-latest", "gemini-2.5-flash", "gemini-2.5-pro"]))
    log(f"[GEMINI] model order: {models}")
    return models


MODELS = discover_models()


def gemini(prompt: str, want_json: bool = True, search: bool = False) -> Any:
    last: Exception | None = None
    for model in (MODELS[:3] if search else MODELS):
        for attempt in range(2):
            try:
                cfg: dict[str, Any] = {}
                if search:
                    cfg["tools"] = [{"google_search": {}}]
                elif want_json:
                    cfg["response_mime_type"] = "application/json"
                text = (gemini_client.models.generate_content(model=model, contents=prompt, config=cfg).text or "").strip()
                if not want_json:
                    return text
                text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
                if search:  # search mode cannot force JSON: pull the first {...} or [...] block
                    m = re.search(r"(\{.*\}|\[.*\])", text, flags=re.S)
                    text = m.group(1) if m else text
                return json.loads(text)
            except Exception as exc:
                last = exc
                log(f"[GEMINI WARN] {model} try {attempt + 1}: {str(exc)[:160]}")
                if any(c in str(exc) for c in ("429", "404", "RESOURCE_EXHAUSTED", "NOT_FOUND")):
                    break  # quota / missing model: go to the next model at once
                time.sleep(4)
    raise RuntimeError(f"All Gemini models failed: {last}")


# =============================================================================
# 1. NEWS (Google News RSS, India + Canada editions, + IRCC official feed)
# =============================================================================
def hours_ago(dt: datetime) -> float:
    return round((NOW_UTC - dt).total_seconds() / 3600, 1)


def collect_news() -> list[dict[str, Any]]:
    cutoff = NOW_UTC - timedelta(hours=HOURS)
    feeds = list(OFFICIAL_FEEDS)
    for q in NEWS_QUERIES:
        enc = requests.utils.quote(f"{q} when:{HOURS}h")
        for hl, gl, ceid in NEWS_REGIONS:
            feeds.append((f"Google News {gl}: {q}", f"https://news.google.com/rss/search?q={enc}&hl={hl}&gl={gl}&ceid={ceid}"))

    items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, url in feeds:
        try:
            for e in feedparser.parse(url).entries[:20]:
                pp = e.get("published_parsed") or e.get("updated_parsed")
                if not pp:
                    continue
                pub = datetime(*pp[:6], tzinfo=timezone.utc)
                if pub < cutoff:
                    continue
                title = re.sub(r"\s+-\s+[^-]+$", "", (e.get("title") or "").strip())  # drop " - Outlet"
                key = title.lower()[:90]
                if not title or key in seen:
                    continue
                seen.add(key)
                outlet = (e.get("source") or {}).get("title") or name
                items.append({"id": f"n{len(items)}", "outlet": outlet, "title": title,
                              "url": (e.get("link") or "").strip(), "hours_ago": hours_ago(pub)})
        except Exception as exc:
            log(f"[RSS WARN] {name}: {exc}")
    log(f"[NEWS] {len(items)} headlines in the last {HOURS} h")
    return items


# =============================================================================
# 2. YOUTUBE (last 24 h, ranked by views per hour)
# =============================================================================
def yt_get(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    r = requests.get(f"https://www.googleapis.com/youtube/v3/{endpoint}", params={**params, "key": YOUTUBE_API_KEY}, timeout=20)
    data = r.json()
    if "error" in data:
        raise RuntimeError(data["error"].get("message", "YouTube API error"))
    return data


def collect_youtube() -> list[dict[str, Any]]:
    after = (NOW_UTC - timedelta(hours=HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids: list[str] = []
    for q in YT_QUERIES:
        try:
            data = yt_get("search", {"part": "id", "q": q, "type": "video", "order": "viewCount",
                                     "publishedAfter": after, "maxResults": 15, "regionCode": "IN"})
            ids += [i["id"]["videoId"] for i in data.get("items", []) if i.get("id", {}).get("videoId")]
        except Exception as exc:
            log(f"[YT WARN] {q}: {exc}")
    ids = list(dict.fromkeys(ids))
    videos: list[dict[str, Any]] = []
    for i in range(0, len(ids), 50):
        try:
            data = yt_get("videos", {"part": "snippet,statistics", "id": ",".join(ids[i:i + 50])})
        except Exception as exc:
            log(f"[YT WARN] videos: {exc}")
            continue
        for v in data.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            pub = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
            h = max(hours_ago(pub), 0.5)
            views = int(st.get("viewCount", 0))
            videos.append({"id": f"y{len(videos)}", "title": sn["title"], "channel": sn["channelTitle"],
                           "views": views, "comments": int(st.get("commentCount", 0)), "hours_ago": h,
                           "views_per_hour": int(views / h), "description": (sn.get("description") or "")[:300],
                           "url": f"https://www.youtube.com/watch?v={v['id']}"})
    videos.sort(key=lambda v: v["views_per_hour"], reverse=True)
    log(f"[YT] {len(videos)} videos")
    return videos[:40]


# =============================================================================
# 3. PICK THE STORIES (Gemini clusters headlines + videos, scores /10)
# =============================================================================
def rank_stories(news: list[dict[str, Any]], videos: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not news and not videos:
        return []
    slim_news = [{k: n[k] for k in ("id", "outlet", "title", "hours_ago")} for n in news[:250]]
    slim_yt = [{k: v[k] for k in ("id", "title", "channel", "views", "views_per_hour", "hours_ago")} for v in videos]
    prompt = f"""Today is {TODAY}. You pick stories for an Instagram/YouTube page for INDIANS who live in
Canada or want to move abroad (students, workers, PGWP/PR holders, their families in India).

Group these headlines and YouTube videos (last {HOURS} h) into STORIES (same event = one story).
Keep only stories about: Canada-India relations, Canada immigration/visa/PR/PGWP/study permit/work
permit rules, Express Entry or PNP draws, Canada jobs/hiring, OR major news that affects Indians living
overseas (any country: visa rules, deportations, safety, taxes, remittances, students). Drop everything else.

Score each story:
- impact (0-4): how many Indians abroad / wanting to go abroad it changes things for, and how much
- buzz (0-3): number of outlets + YouTube views per hour
- fresh (0-2): newer = higher
- clarity (0-1): can be explained in a 60 s reel
score = sum (out of 10).

HEADLINES: {json.dumps(slim_news, ensure_ascii=False)}
YOUTUBE: {json.dumps(slim_yt, ensure_ascii=False)}

Return JSON list, best first, max 8:
[{{"headline": "short story headline", "category": "immigration|relations|overseas|jobs",
"what_happened": "2 lines", "why_it_matters": "1 line for Indians abroad", "score": 8.5,
"news_ids": ["n1", ...], "yt_ids": ["y1", ...]}}]"""
    stories = gemini(prompt)
    by_id = {x["id"]: x for x in news + videos}
    for s in stories:
        s["links"] = [by_id[i]["url"] for i in s.get("news_ids", []) + s.get("yt_ids", []) if i in by_id][:6]
        s["refs"] = ([f"{by_id[i]['outlet']}: {by_id[i]['title']}" for i in s.get("news_ids", []) if i in by_id][:5]
                     + [f"YouTube ({by_id[i]['channel']}, {by_id[i]['views']:,} views): {by_id[i]['url']}"
                        for i in s.get("yt_ids", []) if i in by_id][:3])
        s["outlets"] = sorted({by_id[i]["outlet"] for i in s.get("news_ids", []) if i in by_id})
        s["yt"] = [by_id[i] for i in s.get("yt_ids", []) if i in by_id]
    stories.sort(key=lambda s: float(s.get("score", 0)), reverse=True)
    log(f"[RANK] {len(stories)} stories")
    return stories


# =============================================================================
# 4. JOBS (Job Bank Canada official RSS + each posting page; real postings only)
# =============================================================================
JOB_SEARCHES = [  # (search words, IT or Non-IT)
    ("software developer", "IT"), ("programmer", "IT"), ("data analyst", "IT"), ("web developer", "IT"),
    ("IT support", "IT"), ("QA tester", "IT"), ("cloud engineer", "IT"), ("database", "IT"),
    ("truck driver", "Non-IT"), ("cook", "Non-IT"), ("food service supervisor", "Non-IT"),
    ("home child care", "Non-IT"), ("personal support worker", "Non-IT"), ("registered nurse", "Non-IT"),
    ("welder", "Non-IT"), ("electrician", "Non-IT"), ("carpenter", "Non-IT"), ("warehouse", "Non-IT"),
    ("retail supervisor", "Non-IT"), ("accounting", "Non-IT"), ("administrative assistant", "Non-IT"),
    ("farm worker", "Non-IT"), ("construction", "Non-IT"), ("customer service", "Non-IT"),
]
UA = {"User-Agent": "Mozilla/5.0 (compatible; daily-jobs-digest)"}


def jobbank_feed(words: str, kind: str) -> list[dict[str, Any]]:
    q = requests.utils.quote(words)
    url = f"https://www.jobbank.gc.ca/jobsearch/feed/jobSearchRSSfeed?searchstring={q}&fage=2&sort=D"
    out = []
    for e in feedparser.parse(requests.get(url, headers=UA, timeout=20).content).entries[:12]:
        summ = re.sub(r"<[^>]+>", " | ", e.get("summary", ""))

        def field(k: str) -> str:
            m = re.search(k + r":\s*\|?\s*([^|]+)", summ)
            return m.group(1).strip() if m else ""

        out.append({"role": e.get("title", "").strip(), "link": e.get("link", ""), "it_or_non_it": kind,
                    "location": field("Location"), "company": field("Employer"), "salary": field("Salary")})
    return out


def jobbank_details(job: dict[str, Any]) -> None:
    raw = requests.get(job["link"], headers=UA, timeout=20).text
    raw = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", " ", raw)  # the JS holds both LMIA texts
    t = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))
    low = t.lower()
    job["lmia"] = ("approved" if "has an approved labour market impact assessment" in low
                   else "requested" if "applied for a labour market impact assessment" in low else "")
    job["open_to_abroad"] = "with or without a valid canadian work permit" in low
    external = 'id="externaljoblink"' in raw.lower() and "who can apply for this job" not in low
    job["work_permit_ok"] = ("temporary resident of canada with a valid work permit" in low or job["open_to_abroad"]
                             or external)  # external postings: anyone allowed to work in Canada
    m = re.search(r"Experience:?\s+(.{3,45}?)(?=\s+(?:Employment|On site|On the road|Work|Hybrid|Remote|Education|Tasks|Screening|Location)|\.)", t)
    exp = m.group(1).strip() if m else ""
    job["experience"] = exp
    job["level"] = ("Fresher" if re.search(r"(?i)will train|no experience|less than 7 months|experience an asset", exp)
                    else "Experienced" if exp else "")
    if job["open_to_abroad"] or job["lmia"]:
        parts = []
        if job["open_to_abroad"]:
            parts.append("Anyone (no Canadian work permit needed)")
        if job["lmia"]:
            parts.append(f"LMIA {job['lmia']} (employer can hire foreign workers)")
        job["who_can_apply"] = "; ".join(parts)
    elif external:
        job["who_can_apply"] = "Anyone allowed to work in Canada (PGWP / work permit / PR); check the posting"
    elif job["work_permit_ok"]:
        job["who_can_apply"] = "Citizens, PR and work-permit holders (PGWP / open work permit)"
    else:
        job["who_can_apply"] = "Citizens / PR only"


def collect_jobs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (jobs open to Indians in India, jobs for PGWP / PR / work-permit holders)."""
    seen: set[str] = set()
    pool: list[dict[str, Any]] = []
    for words, kind in JOB_SEARCHES:
        try:
            for j in jobbank_feed(words, kind):
                key = f"{j['company']}|{j['role']}|{j['location']}".lower()
                if j["link"] and key not in seen:
                    seen.add(key)
                    pool.append(j)
        except Exception as exc:
            log(f"[JOBS WARN] feed {words}: {exc}")
    log(f"[JOBS] {len(pool)} fresh Job Bank postings (last 2 days)")
    per_role: dict[str, int] = {}
    checked = []
    for j in pool:  # check each posting page (capped: polite and fast)
        key = j["role"].lower()
        if per_role.get(key, 0) >= 3 or len(checked) >= 120:
            continue
        try:
            jobbank_details(j)
            checked.append(j)
            per_role[key] = per_role.get(key, 0) + 1
            time.sleep(0.3)
        except Exception as exc:
            log(f"[JOBS WARN] {j['link']}: {exc}")
    abroad = [j for j in checked if j["open_to_abroad"] or j["lmia"]]
    in_canada = [j for j in checked if j["work_permit_ok"] and j not in abroad]

    def pick(jobs: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
        def fresh_first(lst):
            return sorted(lst, key=lambda j: j.get("level") != "Fresher")
        a = fresh_first([j for j in jobs if j["it_or_non_it"] == "IT"])
        b = fresh_first([j for j in jobs if j["it_or_non_it"] != "IT"])
        out = []
        while len(out) < n and (a or b):  # mix IT and non-IT
            if a:
                out.append(a.pop(0))
            if b and len(out) < n:
                out.append(b.pop(0))
        return out

    abroad, in_canada = pick(abroad, 10), pick(in_canada, 10)
    log(f"[JOBS] open to Indians abroad (LMIA / no permit needed): {len(abroad)} | PGWP/PR/work permit: {len(in_canada)}")
    return abroad, in_canada


# =============================================================================
# 5. SCRIPTS
# =============================================================================
STYLE = """STYLE (Telugu written in English letters, like a top Canada-immigration Instagram reel):
- Line 1 = a hook that stops the scroll (the big change / number / opportunity), under 12 words.
- Then: "Ikkada asalu important point enti ante..." -> what exactly happened (dates, numbers, who it is for).
- "Simple ga cheppali ante..." -> what it means for an Indian student / worker / PGWP / PR holder.
- "Kani jagrtha ga vinandi" -> the catch, deadline or eligibility rule.
- End with one comment question + "follow avvandi, daily Canada updates kosam".
- 1,000-1,300 characters. Short lines. English words for official terms (PGWP, PR, Express Entry, LMIA).
HARD RULES: only facts you verified; give exact dates/numbers; no made-up jobs or rules; never say
"ee video lo" or name the YouTuber we got the idea from. Official sources (canada.ca / IRCC) beat media."""


def research(topic: str) -> str:
    prompt = f"""Today is {TODAY}. Research with Google Search and give verified facts on:
{topic}
Give: exactly what happened / changed (dates, numbers, who it applies to), the official source
(canada.ca / IRCC / government / company page) if any, what it means for Indians (students, workers,
PGWP/PR holders, families), deadlines/eligibility, and 2-4 source URLs. Bullet points."""
    return safe(gemini, prompt, False, True, default="") or ""


def write_script(label: str, topic: str, context: str = "") -> dict[str, Any]:
    facts = (research(topic) if label != "jobs" else "") or context
    prompt = f"""You write a 60-second reel script for Indians in / going to Canada and abroad.
{STYLE}

TOPIC ({label}):
{topic}

VERIFIED RESEARCH:
{facts or "(no extra research: use ONLY the facts in the topic, add no numbers or dates of your own)"}

Return JSON: {{"title": "punchy title under 80 chars", "script": "the script",
"key_facts": ["fact 1", "fact 2"], "fact_sources": ["URLs"]}}"""
    try:
        out = gemini(prompt)
        out["script"] = re.sub(r"(?i)\b(according to|as per)\s+[A-Z][\w .&-]{1,40}[,:]?\s*", "", out.get("script", ""))
        return out
    except Exception as exc:
        return {"title": topic[:80], "script": f"[Gemini failed: {exc}]", "key_facts": [], "fact_sources": []}


def jobs_topic(jobs: list[dict[str, Any]], scope: str) -> str:
    who = "Indians applying from India" if scope == "india" else "people already in Canada on PGWP / work permit / PR"
    lines = [f"- {j.get('company')}: {j.get('role')} ({j.get('location')}, {j.get('it_or_non_it')}, {j.get('level')}; "
             f"{j.get('who_can_apply')}; salary {j.get('salary') or 'n/a'}; experience {j.get('experience') or 'n/a'}) {j.get('link')}" for j in jobs[:6]]
    return (f"Real Canada Job Bank postings from the last 2 days, open to {who}, IT and non-IT, freshers and "
            f"experienced. Use ONLY these jobs and numbers; tell viewers to check the Job Bank link and apply only "
            f"on official sites, and warn that paying anyone for an LMIA is illegal:\n" + "\n".join(lines))


# =============================================================================
# 6. DOC + EMAIL
# =============================================================================
def google_creds():
    return service_account.Credentials.from_service_account_info(
        json.loads(GOOGLE_SERVICE_ACCOUNT_JSON), scopes=["https://www.googleapis.com/auth/documents"])


def script_block(n: int, label: str, s: dict[str, Any] | None, links: list[str]) -> str:
    if not s:
        return f"SCRIPT {n} — {label}\nNothing strong enough today.\n\n"
    facts = "\n".join(f"  - {f}" for f in s.get("key_facts", []))
    fs = [u for u in s.get("fact_sources", []) if "vertexaisearch" not in u and "news.google.com" not in u]
    srcs = "\n".join(f"  - {u}" for u in (fs + links)[:8])
    return (f"SCRIPT {n} — {label}: {s.get('title', '')}  ({len(s.get('script', ''))} chars)\n"
            f"{s.get('script', '').strip()}\n\nKey facts:\n{facts}\nSources / references:\n{srcs}\n\n")


def jobs_block(title: str, jobs: list[dict[str, Any]]) -> str:
    if not jobs:
        return f"{title}\n  (none found today)\n\n"
    rows = [f"  • {j.get('company')} — {j.get('role')} | {j.get('location')} | {j.get('it_or_non_it')} | "
            f"{j.get('level')} | {j.get('who_can_apply')}"
            + (f" | {j['salary']}" if j.get("salary") else "") + (f" | exp: {j['experience']}" if j.get("experience") else "")
            + f"\n    {j.get('link')}" for j in jobs]
    return f"{title}\n" + "\n".join(rows) + "\n\n"


def stories_block(stories: list[dict[str, Any]]) -> str:
    if not stories:
        return "ALL STORIES TODAY\n  (none)\n\n"
    out = ["ALL STORIES TODAY (score /10)"]
    for i, s in enumerate(stories, 1):
        yt = "; ".join(f"{v['channel']} {v['views']:,} views" for v in s.get("yt", [])[:2])
        out.append(f"  {i}. [{s.get('score')}/10] [{s.get('category')}] {s.get('headline')}\n"
                   f"     {s.get('what_happened', '')}\n     Why it matters: {s.get('why_it_matters', '')}\n"
                   f"     Outlets: {', '.join(s.get('outlets', [])[:6]) or '-'}" + (f" | YouTube: {yt}" if yt else "")
                   + "".join(f"\n     {u}" for u in s.get("links", [])[:2]))
    return "\n".join(out) + "\n\n"


def prepend_doc(creds, text: str) -> None:
    build("docs", "v1", credentials=creds).documents().batchUpdate(
        documentId=CANADA_DOC_ID, body={"requests": [{"insertText": {"location": {"index": 1}, "text": text}}]}).execute()
    log("[DOC] added on top")


def send_email(lines: list[str]) -> None:
    if not (GMAIL_ADDRESS and GMAIL_APP_PASSWORD and RECIPIENT_EMAIL):
        log("[EMAIL] skipped (no Gmail secrets)")
        return
    html = (f"<div style='font-family:Arial;line-height:1.6;color:#111'><h2>Canada-India Brief — {TODAY}</h2>"
            + "".join(f"<p>{l}</p>" for l in lines) + f"<hr/><p><a href='{DOC_URL}'>Open the 2 scripts (Doc)</a></p></div>")
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = f"Canada-India Brief — {TODAY}", GMAIL_ADDRESS, RECIPIENT_EMAIL
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, [RECIPIENT_EMAIL], msg.as_string())
    log(f"[EMAIL] sent to {RECIPIENT_EMAIL}")


# =============================================================================
# MAIN
# =============================================================================
def main() -> None:
    log(f"[START] {NOW_IST.isoformat()} | window {HOURS} h")
    news = safe(collect_news, default=[]) or []
    videos = safe(collect_youtube, default=[]) or []
    stories = safe(rank_stories, news, videos, default=[]) or []
    jobs_india, jobs_canada = safe(collect_jobs, default=([], [])) or ([], [])

    non_job = [s for s in stories if s.get("category") != "jobs"]
    top = non_job[0] if non_job else (stories[0] if stories else None)
    topic1 = f"{top['headline']}. {top.get('what_happened', '')}" if top else None
    def ctx(st: dict[str, Any]) -> str:
        return ("Headlines covering this story:\n" + "\n".join(st.get("refs", [])) + "\n"
                + "\n".join(f"YouTube: {v['title']} - {v['description']}" for v in st.get("yt", [])[:2]))

    s1 = write_script("Top immigration / overseas news", topic1, ctx(top)) if topic1 else None
    links1 = top.get("refs", []) if top else []

    # Script 2: jobs open to Indians from India -> else jobs for PGWP/PR/work-permit holders -> else 2nd story
    if jobs_india:
        label2, s2, links2 = "Canada jobs for Indians (apply from India)", write_script("jobs", jobs_topic(jobs_india, "india")), []
    elif jobs_canada:
        label2, s2, links2 = "Canada jobs for PGWP / PR / work-permit holders", write_script("jobs", jobs_topic(jobs_canada, "canada")), []
    else:
        nxt = next((s for s in stories if s is not top), None)
        label2 = "2nd biggest story (no open jobs found today)"
        s2 = write_script("news", f"{nxt['headline']}. {nxt.get('what_happened', '')}", ctx(nxt)) if nxt else None
        links2 = nxt.get("refs", []) if nxt else []

    text = ("==================================================\n"
            f"CANADA-INDIA DAILY — {NOW_IST.strftime('%d %b %Y, %I:%M %p IST')}\n"
            "==================================================\n\n"
            + script_block(1, "Top immigration / overseas news", s1, links1)
            + script_block(2, label2, s2, links2)
            + jobs_block("JOBS OPEN TO INDIANS FROM INDIA", jobs_india)
            + jobs_block("JOBS FOR PEOPLE IN CANADA (PGWP / PR / WORK PERMIT)", jobs_canada)
            + stories_block(stories) + "\n")
    safe(prepend_doc, google_creds(), text)
    safe(send_email, [f"<b>Script 1:</b> {(s1 or {}).get('title', 'nothing strong today')}",
                      f"<b>Script 2 ({label2}):</b> {(s2 or {}).get('title', 'nothing strong today')}",
                      f"Jobs found: {len(jobs_india)} for Indians in India, {len(jobs_canada)} for PGWP/PR/work-permit holders. "
                      f"Stories tracked: {len(stories)}."])
    log("[DONE]")


if __name__ == "__main__":
    main()

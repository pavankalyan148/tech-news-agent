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

import html
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
    "hiring in Canada 2026 OR \"is hiring\" Canada",
    "mass hiring freshers OR \"off campus drive\" 2026",
    "Canada job fair OR \"international recruitment\" Canada",
    "Canada nurses LMIA OR healthcare workers Canada recruitment",
    "Canada new PR pathway OR provincial nominee draw",
    "Cognizant OR Infosys OR TCS OR Wipro OR Accenture OR Deloitte OR Capgemini hiring Canada",
    "Concentrix OR Teleperformance OR Amazon OR Walmart hiring freshers any graduate",
    "Google OR Microsoft OR Shopify OR RBC OR TD Bank hiring Canada",
    "Canada startups hiring OR Global Hypergrowth Project",
    "IRCC immigration levels plan OR PR targets",
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
    "Canada jobs telugu",
    "companies hiring in Canada",
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
# Her most-viewed reels (checked 2026-11-27). Every daily topic must fit one of these.
PATTERNS = """P1 TOP-N COMPANIES LIST (125K views): "Top 30 companies hiring in Canada" + comment for the sheet.
P2 BIG COMPANY HIRING CANADA + INDIA (123K): Cognizant / Infosys / Deloitte / Amazon - Canada roles for WP/PR
   + India roles for freshers, cities, pay, process.
P3 FRESHERS MASS HIRING, ANY GRADUATE (88K): Concentrix-type BPO / CX / support, Canada (WP/PR) + India, 2022-2026 batches.
P4 BULK LIST FOR ONE ROLE (61K): "200+ companies hiring Data / Business Analysts" (careers pages, not LinkedIn).
P5 PR BIG UPDATE (30K): IRCC levels plan, TR-to-PR slots, new PR pathway, big draws, budget.
P6 STARTUPS / LMIA-EXEMPT (25K): "60+ Canada startups, 500+ roles, 8 need no LMIA (Global Hypergrowth)".
P7 JOB-SEARCH HACK (23K): Job Bank LMIA filter, hidden job sites, Google ATS search.
P8 HEALTHCARE / TRADES INTERNATIONAL RECRUITMENT WITH LMIA (19K): nurses, PSW, Alberta Health Services.
P9 JOB FAIRS / BIG WAREHOUSE HIRING (15K): "39 job fairs in Canada", "Amazon Canada 9,000 jobs $24/h"."""


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

Map each story to ONE of her proven reel patterns (or "none"):
{PATTERNS}
Stories that fit no pattern (e.g. US-only visa news) get pattern "none" and score at most 5,
unless it directly changes things for Indians in Canada.

Score each story:
- impact (0-4): how many Indians abroad / wanting to go abroad it changes things for, and how much.
  Give the top marks to what wins on the kranthi.in.canada page: a big company hiring in Canada (+ India
  freshers), mass / any-graduate hiring, top-N companies hiring, healthcare recruitment with LMIA, new PR
  pathways / big PR news, job fairs.
- buzz (0-3): number of outlets + YouTube views per hour
- fresh (0-2): newer = higher
- clarity (0-1): can be explained in a 60 s reel
score = sum (out of 10); then add +1 for P1-P3 and +0.5 for P4-P6 (max 10).

HEADLINES: {json.dumps(slim_news, ensure_ascii=False)}
YOUTUBE: {json.dumps(slim_yt, ensure_ascii=False)}

Return JSON list, best first, max 8:
[{{"headline": "short story headline", "category": "immigration|relations|overseas|jobs", "pattern": "P2",
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
    stories.sort(key=lambda s: (s.get("pattern", "none") != "none", float(s.get("score", 0))), reverse=True)
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


def jobbank_feed(words: str, kind: str, days: int = 2) -> list[dict[str, Any]]:
    q = requests.utils.quote(words)
    url = f"https://www.jobbank.gc.ca/jobsearch/feed/jobSearchRSSfeed?searchstring={q}&fage={days}&sort=D"
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
    m = re.search(r"(\d+)\s+vacanc", t)
    job["vacancies"] = int(m.group(1)) if m else 1
    m = re.search(r"Education:\s*(.{3,80}?)(?:\.|\s+(?:Experience|Work setting|Tasks|Certificates|Screening))", t)
    job["education"] = m.group(1).strip() if m else ""
    m = re.search(r"Certificates, licences, memberships, and courses\s*:\s*(.{3,250}?)\.?\s+(?:Experience:|Employment|Work setting|Tasks|Screening|Health benefits|Location)", t)
    if not m:
        m = re.search(r"Credentials Certificates, licences, memberships, and courses\s+(.{3,200}?)\s+(?:Additional information|"
                      r"Experience and specialization|Personal suitability|Who can apply|Work conditions|Security and safety|"
                      r"Transportation|Health benefits|Financial benefits|Other benefits)", t)
    job["credentials"] = m.group(1).strip() if m else ""
    job["housing"] = "on-site housing" in low
    m = re.search(r"([\d.]+)\s+hours per week", t)
    job["hours"] = float(m.group(1)) if m else 40.0
    job["employer_is_person"] = not re.search(
        r"(?i)\b(inc|ltd|lt[ée]e|corp|corporation|limited|llc|llp|co|company|group|services|health|lodge|restaurant|"
        r"hotel|authority|centre|center|school|farms?|foods?|transport|logistics|express|motors|clinic|care|homes?|"
        r"society|association|church|university|college|bakery|cafe|systems|solutions|technologies|enterprises|"
        r"industries|construction|manor|villa|residence|hospital|pharmacy|market|store)\b", job.get("company", ""))
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


def collect_jobs_window(days: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Returns (jobs open to Indians in India, jobs for PGWP / PR / work-permit holders)."""
    seen: set[str] = set()
    pool: list[dict[str, Any]] = []
    for words, kind in JOB_SEARCHES:
        try:
            for j in jobbank_feed(words, kind, days):
                key = f"{j['company']}|{j['role']}|{j['location']}".lower()
                if j["link"] and key not in seen:
                    seen.add(key)
                    pool.append(j)
        except Exception as exc:
            log(f"[JOBS WARN] feed {words}: {exc}")
    log(f"[JOBS] {len(pool)} Job Bank postings (posted in the last {days} days, still open)")
    for j in pool:
        j["posted_window"] = f"posted in last {days} days"
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
    log(f"[JOBS] {days}d: open to Indians abroad (LMIA / no permit needed): {len(abroad)} | PGWP/PR/work permit: {len(in_canada)}")
    return abroad, in_canada


def collect_jobs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Newest first (2 days). Too few? Widen to postings from the last 14 days: Job Bank only lists
    postings that are still open, so these can still be applied to today and after."""
    abroad, in_canada = collect_jobs_window(2)
    if len(abroad) < 4 or len(in_canada) < 4:
        more_abroad, more_canada = collect_jobs_window(14)
        have = {j["link"] for j in abroad + in_canada}
        abroad += [j for j in more_abroad if j["link"] not in have][:10 - len(abroad)]
        in_canada += [j for j in more_canada if j["link"] not in have][:10 - len(in_canada)]
    return abroad, in_canada


# =============================================================================
# 4b. ONE COMPANY HIRING FOR IT ROLES (official career-site APIs)
# =============================================================================
IT_WORDS = re.compile(r"(?i)engineer|developer|software|data|cloud|devops|\bqa\b|test|analyst|security|\bsre\b|"
                      r"machine learning|\bml\b|\bai\b|\bit\b|architect|programmer|full.?stack|backend|frontend|mobile")
CA_CITIES = ("canada", "toronto", "vancouver", "montreal", "ottawa", "calgary", "waterloo", "edmonton", "burnaby",
             "oakville", "mississauga", "halifax", "winnipeg", ", on", ", bc", ", ab", ", qc")
# Official job boards with Canada IT roles (checked 2026-11-27). Add more any time: "Name": "board-slug".
GREENHOUSE = {"Okta": "okta", "Affirm": "affirm", "Lyft": "lyft", "Stripe": "stripe", "GitLab": "gitlab",
              "Databricks": "databricks", "Samsara": "samsara", "Instacart": "instacart", "Robinhood": "robinhood",
              "AlayaCare": "alayacare", "Brex": "brex", "Pinterest": "pinterest", "Geotab": "geotab", "Faire": "faire",
              "D2L": "d2l", "Dropbox": "dropbox", "Reddit": "reddit", "Coinbase": "coinbase", "Hootsuite": "hootsuite"}
ASHBY = {"1Password": "1password", "Cohere": "cohere", "Wealthsimple": "wealthsimple", "Hopper": "hopper",
         "Jobber": "jobber", "Benevity": "benevity", "Trulioo": "trulioo", "Snowflake": "snowflake",
         "OpenAI": "openai", "Float": "float"}
LEVER = {"Fullscript": "fullscript"}
# Companies already done (by us or by her reels). The agent adds each day's pick and never repeats one.
USED_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "canada_used_companies.json")
COMPANY_DAYS = int(os.getenv("COMPANY_DAYS") or 14)


def _recent(dt: datetime | None) -> bool:
    return dt is not None and (NOW_UTC - dt) <= timedelta(days=COMPANY_DAYS)


def _is_canada(loc: str) -> bool:
    low = f" {loc.lower()}"
    return any(c in low for c in CA_CITIES)


def amazon_jobs(country: str, extra: dict[str, str] | None = None) -> list[dict[str, Any]]:
    params = {"normalized_country_code[]": country, "category[]": "software-development",
              "result_limit": "50", "sort": "recent", **(extra or {})}
    data = requests.get("https://www.amazon.jobs/en/search.json", params=params, headers=UA, timeout=25).json()
    out = []
    for j in data.get("jobs", []):
        try:
            dt = datetime.strptime(re.sub(r"\s+", " ", j["posted_date"]), "%B %d, %Y").replace(tzinfo=timezone.utc)
        except Exception:
            dt = None
        if _recent(dt):
            out.append({"role": j["title"], "location": j["location"], "posted": dt.strftime("%d %b"),
                        "link": "https://www.amazon.jobs" + j["job_path"],
                        "text": _plain(" ".join(j.get(k, "") or "" for k in (
                            "description", "basic_qualifications", "preferred_qualifications")))})
    return out


def greenhouse_jobs(slug: str) -> list[dict[str, Any]]:
    data = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true", headers=UA, timeout=40).json()
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        dt = datetime.fromisoformat(j["updated_at"]) if j.get("updated_at") else None
        if _is_canada(loc) and IT_WORDS.search(j.get("title", "")) and _recent(dt):
            out.append({"role": j["title"], "location": loc, "posted": dt.strftime("%d %b"), "link": j["absolute_url"],
                        "text": _plain(html.unescape(j.get("content", "")))})
    return out


def lever_jobs(slug: str) -> list[dict[str, Any]]:
    data = requests.get(f"https://api.lever.co/v0/postings/{slug}?mode=json", headers=UA, timeout=25).json()
    out = []
    for j in data if isinstance(data, list) else []:
        loc = (j.get("categories") or {}).get("location", "") or ""
        dt = datetime.fromtimestamp(j["createdAt"] / 1000, tz=timezone.utc) if j.get("createdAt") else None
        if _is_canada(loc) and IT_WORDS.search(j.get("text", "")) and _recent(dt):
            sal = j.get("salaryRange") or {}
            pay = f" ${sal.get('min'):,} - ${sal.get('max'):,} {sal.get('currency', '')}" if sal.get("min") else ""
            out.append({"role": j["text"], "location": loc, "posted": dt.strftime("%d %b"), "link": j["hostedUrl"],
                        "text": _plain(j.get("descriptionPlain", "") + " " + " ".join(
                            x.get("content", "") for x in j.get("lists", [])) + " " + j.get("additionalPlain", "")) + pay})
    return out


def load_used() -> list[dict[str, str]]:
    try:
        return json.load(open(USED_FILE, encoding="utf-8")).get("used", [])
    except Exception:
        return []


def save_used(used: list[dict[str, str]]) -> None:
    with open(USED_FILE, "w", encoding="utf-8") as f:
        json.dump({"used": used}, f, indent=1, ensure_ascii=False)


def ashby_jobs(slug: str) -> list[dict[str, Any]]:
    data = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true",
                        headers=UA, timeout=40).json()
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location", "") + " " + str((j.get("address") or {}).get("postalAddress", {}).get("addressCountry", ""))
        dt = datetime.fromisoformat(j["publishedAt"]) if j.get("publishedAt") else None
        if _is_canada(loc) and IT_WORDS.search(j.get("title", "")) and _recent(dt):
            comp = (j.get("compensation") or {}).get("compensationTierSummary", "") or ""
            out.append({"role": j["title"], "location": j.get("location", ""), "posted": dt.strftime("%d %b"),
                        "link": j["jobUrl"], "remote": j.get("isRemote"), "workplace": j.get("workplaceType", ""),
                        "text": _plain(j.get("descriptionPlain", "")) + " " + comp})
    return out


def _plain(x: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", x or ""))).strip()


_RATE: list[float] = []


def cad_inr() -> float:
    """Live CAD -> INR rate (free API), fallback 61."""
    if not _RATE:
        try:
            _RATE.append(float(requests.get("https://open.er-api.com/v6/latest/CAD", timeout=15).json()["rates"]["INR"]))
        except Exception:
            _RATE.append(float(os.getenv("CAD_INR") or 61))
        log(f"[RATE] 1 CAD = {_RATE[0]:.2f} INR")
    return _RATE[0]


def lpa(cad_per_year: float) -> str:
    lakhs = cad_per_year * cad_inr() / 100000
    return f"{lakhs / 100:.1f} Crore" if lakhs >= 100 else f"{round(lakhs)} LPA"


PAY_RE = re.compile(r"(?:CA)?\$\s?([\d,]{5,9})(?:\.\d+)?\s*(?:-|–|—|to)\s*(?:CA)?\$?\s?([\d,]{5,9})(?:\.\d+)?\s*(CAD|USD)?", re.I)
SPONSOR_NO = re.compile(r"(?i)(not|unable to|cannot|can't|won't|will not|do not|does not)\s+(be\s+able\s+to\s+)?"
                        r"(provide|offer|support|sponsor)\w*\s+(visa|immigration|work permit|sponsorship)"
                        r"|sponsorship\s+(is\s+)?not\s+(available|offered|provided)|no\s+(visa\s+)?sponsorship"
                        r"|not\s+eligible\s+for\s+(visa\s+)?sponsorship|must\s+(already\s+)?(be\s+)?(legally\s+)?"
                        r"(authorized|eligible)\s+to\s+work\s+in\s+canada")
SPONSOR_YES = re.compile(r"(?i)(visa|immigration|relocation)\s+(sponsorship|support)\s+(is\s+)?(available|provided|offered)"
                         r"|(we|will)\s+(can\s+)?sponsor|sponsorship\s+available")
JUNIOR_RE = re.compile(r"(?i:new grad|graduate|intern\b|junior|entry.level|early career|engineer 1\b)|\bI\b|\bL4\b")
SENIOR_RE = re.compile(r"(?i)senior|\bsr\.?\b|staff|principal|lead|director|manager|head|architect|\bIII\b")


def company_facts(c: dict[str, Any]) -> dict[str, Any]:
    """Verified numbers straight from the official postings (no guessing)."""
    jobs = c["canada"]
    lows, highs, named = [], [], []
    hybrid = remote = onsite = 0
    no_sp = yes_sp = junior = senior = 0
    cities: dict[str, int] = {}
    for j in jobs:
        t = j.get("text", "")
        pays = [(int(a.replace(",", "")), int(b.replace(",", "")), (cur or "").upper()) for a, b, cur in PAY_RE.findall(t)]
        pays = [x for x in pays if x[2] in ("CAD", "") and 30000 <= x[0] <= x[1] <= 600000]
        if pays:
            lows.append(pays[0][0]); highs.append(pays[0][1])
            named.append(f"{j['role']}: ${pays[0][0]:,} - ${pays[0][1]:,} CAD")
        low = t.lower() + " " + str(j.get("workplace", "")).lower()
        if j.get("remote") or re.search(r"\bremote\b", low):
            remote += 1
        if "hybrid" in low:
            hybrid += 1
        elif not re.search(r"\bremote\b", low):
            onsite += 1
        no_sp += bool(SPONSOR_NO.search(t)); yes_sp += bool(SPONSOR_YES.search(t))
        junior += bool(JUNIOR_RE.search(j["role"])) and not SENIOR_RE.search(j["role"])
        senior += bool(SENIOR_RE.search(j["role"]))
        for city in re.findall(r"(Toronto|Vancouver|Montreal|Ottawa|Calgary|Waterloo|Edmonton|Burnaby|Mississauga|"
                               r"Halifax|Winnipeg|Kitchener|Oakville|Markham|Victoria|Quebec)", j["location"], re.I):
            cities[city.title()] = cities.get(city.title(), 0) + 1
    if yes_sp and not no_sp:
        sponsor = f"{yes_sp} postings SAY visa sponsorship/support is available"
    elif no_sp:
        sponsor = (f"{no_sp} of {len(jobs)} postings say NO sponsorship / must already be authorized to work in Canada"
                   + (f"; {yes_sp} mention sponsorship support" if yes_sp else ""))
    else:
        sponsor = "NOT MENTIONED in any posting (do not claim yes or no)"
    lo, hi = (min(lows), max(highs)) if lows else (None, None)
    return {
        "count": len(jobs), "cities": sorted(cities.items(), key=lambda x: -x[1]),
        "pay_cad": f"${lo:,} - ${hi:,} CAD per year" if lo else "NOT LISTED (do not mention pay)",
        "pay_lpa": f"almost {lpa(lo)} nunchi {lpa(hi)}" if lo else "",
        "pay_examples": named[:8],
        "work_mode": f"{hybrid} hybrid, {remote} mention remote, {onsite} on-site/not stated (of {len(jobs)})",
        "sponsorship": sponsor,
        "seniority": (f"{senior} senior/staff/lead, {junior} junior/entry/new-grad, "
                      f"{len(jobs) - senior - junior} mid-level"),
        "freshers_ok": junior > 0,
    }


def company_spotlight() -> dict[str, Any] | None:
    """Pick ONE NEW company hiring for IT roles in Canada today (official career sites). A company that was
    already done (canada_used_companies.json) is never picked again; the busiest new company wins."""
    used = load_used()
    done = {u["company"].lower() for u in used}
    log(f"[COMPANY] already done: {', '.join(sorted(done)) or '-'}")
    sources = ([("Amazon", "amazon", "")] + [(n, "gh", sl) for n, sl in GREENHOUSE.items()]
               + [(n, "ab", sl) for n, sl in ASHBY.items()] + [(n, "lv", sl) for n, sl in LEVER.items()])
    careers = {"gh": "https://job-boards.greenhouse.io/{}", "ab": "https://jobs.ashbyhq.com/{}",
               "lv": "https://jobs.lever.co/{}", "amazon": "https://www.amazon.jobs/en/search?country=CAN"}
    found: list[dict[str, Any]] = []
    for name, kind, slug in sources:
        if name.lower() in done:
            continue
        try:
            india: list[dict[str, Any]] = []
            if kind == "amazon":
                jobs = amazon_jobs("CAN")
                india = amazon_jobs("IND", {"base_query": "SDE I"})
            else:
                jobs = {"gh": greenhouse_jobs, "ab": ashby_jobs, "lv": lever_jobs}[kind](slug)
            if jobs:
                found.append({"company": name, "canada": jobs, "india": india[:6], "careers": careers[kind].format(slug)})
                log(f"[COMPANY] {name}: {len(jobs)} Canada IT roles (last {COMPANY_DAYS} days)")
        except Exception as exc:
            log(f"[COMPANY WARN] {name}: {exc}")
    if not found:
        log("[COMPANY] no new company with fresh Canada IT roles today")
        return None
    pick = max(found, key=lambda c: len(c["canada"]))
    used.append({"company": pick["company"], "date": NOW_IST.strftime("%Y-%m-%d")})
    save_used(used)
    log(f"[COMPANY] spotlight today: {pick['company']} (saved so it is not repeated)")
    return pick


def company_topic(c: dict[str, Any]) -> str:
    f = company_facts(c)
    c["facts"] = f
    roles = "\n".join(f"- {j['role']} | {j['location']} | posted {j['posted']}" for j in c["canada"][:15])
    ind = "\n".join(f"- {j['role']} | {j['location']} | posted {j['posted']} | {j['link']}" for j in c["india"][:6])
    return (f"COMPANY: {c['company']} (official careers site: {c['careers']})\n"
            f"VERIFIED FACTS FROM THE OFFICIAL POSTINGS (use exactly these, nothing else):\n"
            f"- Canada IT / tech roles posted in the last {COMPANY_DAYS} days: {f['count']}\n"
            f"- Cities: {', '.join(f'{k} ({v})' for k, v in f['cities']) or 'see postings'}\n"
            f"- Pay range: {f['pay_cad']}" + (f" = {f['pay_lpa']} (1 CAD = Rs {cad_inr():.0f})" if f["pay_lpa"] else "") + "\n"
            f"- Pay examples: {'; '.join(f['pay_examples']) or 'none listed'}\n"
            f"- Work mode: {f['work_mode']}\n"
            f"- Seniority: {f['seniority']}\n"
            f"- Visa sponsorship: {f['sponsorship']}\n"
            f"ROLES:\n{roles}" + (f"\nINDIA ROLES (official):\n{ind}" if ind else ""))


def india_jobs_pick(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """3 Job Bank jobs open to applicants without a Canadian work permit: different roles, fresher-friendly first,
    then higher pay."""
    def hourly(j):
        m = re.search(r"\$([\d,.]+)", j.get("salary", ""))
        v = float(m.group(1).replace(",", "")) if m else 0
        return v if "hour" in j.get("salary", "") else v / 2080 if "annual" in j.get("salary", "") else v
    pool = sorted([j for j in jobs if j.get("open_to_abroad")] or jobs,
                  key=lambda j: (j.get("level") != "Fresher", -hourly(j)))
    # mix: best IT job first if any, then the rest (any job type)
    it = [j for j in pool if j.get("it_or_non_it") == "IT"][:1]
    pool = it + [j for j in pool if j not in it]
    out, roles = [], set()
    for j in pool:
        key = j["role"].lower().split()[0]
        if key in roles:
            continue
        roles.add(key)
        out.append(j)
        if len(out) == 3:
            break
    return out


def india_jobs_topic(jobs: list[dict[str, Any]]) -> str:
    lines = []
    for n, j in enumerate(jobs, 1):
        m = re.search(r"\$([\d,.]+)", j.get("salary", ""))
        rate = float(m.group(1).replace(",", "")) if m else 0
        yearly = rate * j.get("hours", 40) * 52 if "hour" in j.get("salary", "") else rate
        who = (f"a private family (DO NOT say the person's name; say 'oka family')" if j.get("employer_is_person")
               else j.get("company"))
        lines.append(
            f"{n}) {j['role']} | employer: {who} | city: {j.get('location')} | pay: {j.get('salary')}"
            + (f" (full time ~${yearly:,.0f} CAD/year = {lpa(yearly)})" if yearly else "")
            + f" | vacancies: {j.get('vacancies', 1)} | education: {j.get('education') or 'not listed'}"
            + f" | certificates/licence: {j.get('credentials') or 'none listed'} | experience: {j.get('experience') or 'not listed'}"
            + f" | freshers OK: {'YES' if j.get('level') == 'Fresher' else 'NO'}"
            + (" | on-site housing: YES" if j.get("housing") else "")
            + f" | LMIA: {j.get('lmia') or 'not stated'} | {j['link']}")
    return ("VERIFIED Job Bank postings that accept applicants 'with or without a valid Canadian work permit' "
            "(use exactly these facts):\n" + "\n".join(lines))


# =============================================================================
# 5. SCRIPTS
# =============================================================================
STYLE = """STYLE = the kranthi.in.canada reel format (learned from her last 20 reels, 2026-11-27).
Language: Telugu in English letters mixed with simple English (Tenglish), talking to the viewer
("meeru", "chustunara?"), friendly and fast. Job/official words stay in English.
STRUCTURE (spoken script, 35-60 s, 600-1,000 characters, short punchy lines):
1. HOOK (first line): "Canada lo <jobs / PR / X> kosam chustunara? Ayithe Good News!" OR
   "<Company> is hiring in Canada + India!" OR a big number ("Amazon Canada lo 9,000 Jobs! $24/hour!").
2. WHAT: company / program + the headline fact (how many jobs, which roles, which cities, the date).
3. CANADA PART: roles, locations, pay in CAD AND in rupees (e.g. "$103k CAD ante almost 63 LPA"),
   eligibility in plain words ("Canada lo Work Permit / PR / PGWP unte YES").
4. INDIA PART (if any): what people in India can do (freshers / any graduate / 2024-2026 batches,
   LMIA / sponsorship, process: screening + assessment + interview). If it is NOT open from India,
   say it straight ("India nunchi direct apply kudaradu").
5. IMPORTANT / catch: who is NOT eligible, deadline, "competition high", and the scam line
   ("Evaru dabbulu adigina ivvakandi - official site lo matrame apply cheyandi").
6. CTA: 'Link kavala? Comment "<ONE KEYWORD IN CAPS>" - nenu official link DM chestha!'
   then "Share with friends" + "Follow @kranthi.in.canada for genuine Canada jobs!"
TOPIC PATTERNS SHE WINS WITH (copy the angle of the pattern exactly):
""" + PATTERNS + """
HARD RULES: only facts you verified; exact dates/numbers; no made-up jobs, pay or rules; never say
"ee video lo" or name another creator. Official sources (canada.ca / IRCC / company careers page) beat media.
She is not an agent: never promise a job or visa."""


EXAMPLE_COMPANY = """Canada lo IT jobs kosam chustunnara? Okta massive ga hire chestundi!
Official careers site lo Toronto lo 50+ tech & product roles post chesaru. Packages $114K nunchi $231K CAD varaku, ante almost 70 LPA nunchi 1.4 Crore!
Mundu nannu follow ayyi 'OKTA' ani comment cheyandi, official apply link DM chestha!

Roles enti ante: Senior Full-Stack Engineer, Senior Platform Engineer (Auth0), Senior Product Designer, Senior Product Manager (Auth0 AI), mariyu chala Senior Software Engineer openings!

Anni Toronto, Ontario lo, mostly hybrid. Ivi experienced vallake, freshers ki kaadu.
Important: Postings lo visa sponsorship gurinchi emi cheppaledu. So Canada lo already Work Permit, PGWP leda PR unna vallaki best chance. India nunchi ayithe, apply chese mundu posting lo work authorization rules check cheyandi.

Gurthunchukondi, evaru dabbulu adigina ivvakandi, official Okta careers site lo matrame apply cheyandi.
Canada lo IT jobs kosam chustunna mee friends ki share cheyandi, and follow @kranthi.in.canada for genuine Canada jobs!"""

EXAMPLE_JOBS = """Canada lo Job kosam chustunara? Ayithe Good News!
Canadian work permit lekunda, India nunchi direct apply cheyagalige jobs list ready chesanu!
Mundu nannu follow ayyi "CANADA" ani comment cheyandi, official apply links DM chestha!

1st) Ontario, Ancaster lo oka family Home Child Care Provider ni hire chesukuntunnaru, $21 per hour, stay kuda istharu! High school chalu, freshers kuda apply cheyochu.
2nd) Groves Park Lodge lo 9 Personal Support Worker jobs, $29.35 per hour. PSW certificate, First Aid, CPR undali.
3rd) Nova Scotia Health lo 20 Emergency Nurse jobs, $40.55 per hour, ante almost 50 LPA! Kani Canada RN licence, 1-2 years experience undali.

Apply cheyadaniki mundu work permit avasaram ledu. Job offer vaste, employer LMIA tho work permit process chestaru.
Gurthunchukondi, ee jobs kosam evaru dabbulu adigina ivvakandi. Official Job Bank site lo matrame apply cheyandi.
Canada lo job kosam chustunna mee friends ki ee video share cheyandi, and follow @kranthi.in.canada for genuine Canada jobs!"""

FACT_RULES = """FACT RULES (the user rejected scripts that broke these):
- Pay: only the range in the facts. Rupees: use the LPA given in the facts (never compute your own).
- Sponsorship: say exactly what the facts say. If "NOT MENTIONED", say "Postings lo visa sponsorship gurinchi
  emi cheppaledu" - NEVER "sponsorship ivvatledu" or "India nunchi apply cheyalemu".
- Work permit (Job Bank): NEVER just "work permit avasaram ledu". Say "Apply cheyadaniki mundu work permit
  avasaram ledu. Job offer vaste, employer LMIA tho work permit process chestaru."
- Freshers: say "freshers kuda apply cheyochu" ONLY where the facts say freshers OK: YES. Otherwise say the
  requirement (licence / certificate / years of experience).
- Work mode: say "mostly hybrid" / "remote" only as the counts show.
- Private family employer: never say the person's name - say "oka family".
- Add the strong extras when present: number of vacancies, on-site housing, licence needed."""


def research(topic: str) -> str:
    prompt = f"""Today is {TODAY}. Research with Google Search and give verified facts on:
{topic}
Give: exactly what happened / changed (dates, numbers, who it applies to), the official source
(canada.ca / IRCC / government / company page) if any, what it means for Indians (students, workers,
PGWP/PR holders, families), deadlines/eligibility, and 2-4 source URLs. Bullet points."""
    return safe(gemini, prompt, False, True, default="") or ""


def write_script(label: str, topic: str, context: str = "", example: str = "") -> dict[str, Any]:
    facts = (research(topic) if label != "jobs" else "") or context
    ex = (f"""
APPROVED EXAMPLE = FORMAT ONLY (the user wrote it): copy its structure, line order, tone and length.
NEVER reuse its jobs, companies, cities or numbers - every job/company/number must come from TODAY'S facts above
(any job type: IT, non-IT, healthcare, trades, warehouse, freshers or experienced).
---
{example}
---
{FACT_RULES}
""" if example else "")
    prompt = f"""You write a 60-second reel script for Indians in / going to Canada and abroad.
{STYLE}
{ex}
TOPIC ({label}):
{topic}

VERIFIED RESEARCH:
{facts or "(no extra research: use ONLY the facts in the topic, add no numbers or dates of your own)"}

Return JSON: {{"title": "topic title like her captions' first line, under 80 chars",
"script": "the spoken script", "comment_keyword": "ONE WORD",
"caption": "Instagram caption in her style: hook line, emoji ticks for the facts, eligibility, the
Comment-keyword line, Share + Follow @kranthi.in.canada, a one-line Disclaimer (Nenu agent ni kaadu, info
from official sites, no fee), then 5-7 hashtags incl. #CanadaJobs #KranthiInCanada",
"official_links_to_dm": ["official URLs people get in DM"],
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
    return (f"PATTERN P1 + P7: make it a 'Top {min(len(jobs), 10)} jobs list' reel (her #1 format): hook with the "
            f"number, read out 3-4 best roles with pay (CAD + LPA) and city, say the rest are in the sheet, "
            f"comment keyword for the full list. "
            f"Real Canada Job Bank postings that are OPEN NOW (still accepting applications), open to {who}, IT and non-IT, freshers and "
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
    dm = "\n".join(f"  - {u}" for u in s.get("official_links_to_dm", []))
    return (f"SCRIPT {n} — {label}\nTOPIC: {s.get('title', '')}  ({len(s.get('script', ''))} chars)\n"
            f"Comment keyword: {s.get('comment_keyword', '')}\n\n"
            f"{s.get('script', '').strip()}\n\nCAPTION:\n{s.get('caption', '').strip()}\n\n"
            f"Links to DM:\n{dm or '  (use the sources below)'}\nKey facts:\n{facts}\nSources / references:\n{srcs}\n\n")


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
        out.append(f"  {i}. [{s.get('score')}/10] [{s.get('pattern', 'none')}] [{s.get('category')}] {s.get('headline')}\n"
                   f"     {s.get('what_happened', '')}\n     Why it matters: {s.get('why_it_matters', '')}\n"
                   f"     Outlets: {', '.join(s.get('outlets', [])[:6]) or '-'}" + (f" | YouTube: {yt}" if yt else "")
                   + "".join(f"\n     {u}" for u in s.get("links", [])[:2]))
    return "\n".join(out) + "\n\n"


def write_doc(creds, text: str) -> None:
    """Step 1: wipe the WHOLE Doc and check it is empty. Step 2: write today's scripts. Nothing old is kept."""
    docs = build("docs", "v1", credentials=creds).documents()
    end = docs.get(documentId=CANADA_DOC_ID).execute()["body"]["content"][-1]["endIndex"]
    if end > 2:
        docs.batchUpdate(documentId=CANADA_DOC_ID, body={"requests": [
            {"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end - 1}}}]}).execute()
    left = docs.get(documentId=CANADA_DOC_ID).execute()["body"]["content"][-1]["endIndex"]
    if left > 2:
        raise RuntimeError(f"Doc not empty after clearing (endIndex {left}); not writing so nothing gets mixed")
    log("[DOC] whole Doc cleared")
    docs.batchUpdate(documentId=CANADA_DOC_ID, body={"requests": [
        {"insertText": {"location": {"index": 1}, "text": text}}]}).execute()
    log("[DOC] today's scripts written")


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

    top = stories[0] if stories else None  # best story of any kind

    def ctx(st: dict[str, Any]) -> str:
        return ("Headlines covering this story:\n" + "\n".join(st.get("refs", [])) + "\n"
                + "\n".join(f"YouTube: {v['title']} - {v['description']}" for v in st.get("yt", [])[:2]))

    # Script 1: ONE new company hiring for IT roles (user's Okta template)
    spot = safe(company_spotlight, default=None)
    if spot:
        label1 = f"{spot['company']} is hiring - IT roles in Canada" + (" + India" if spot["india"] else "")
        s1 = write_script("jobs", company_topic(spot), example=EXAMPLE_COMPANY)
        links1 = [spot["careers"]] + [x["link"] for x in spot["canada"][:5]]
    else:
        label1, s1, links1 = "IT company spotlight", None, []

    # Script 2: 3 Job Bank jobs Indians can apply for from India (user's 3-jobs template)
    picks = india_jobs_pick(jobs_india)
    if picks:
        label2 = "Canada jobs - apply from India (no work permit needed to apply)"
        s2 = write_script("jobs", india_jobs_topic(picks), example=EXAMPLE_JOBS)
        links2 = [x["link"] for x in picks]
    elif jobs_canada:
        label2, s2, links2 = "Canada jobs for PGWP / PR / work-permit holders", write_script("jobs", jobs_topic(jobs_canada, "canada")), []
    else:
        label2, s2, links2 = "Jobs from India", None, []

    # Script 3: the day's top immigration / overseas-Indians story
    topic3 = (f"PATTERN {top.get('pattern', 'none')}: {top['headline']}. {top.get('what_happened', '')}") if top else None
    s3 = write_script("Top immigration / overseas news", topic3, ctx(top)) if topic3 else None
    links3 = top.get("refs", []) if top else []

    text = ("==================================================\n"
            f"CANADA-INDIA DAILY — {NOW_IST.strftime('%d %b %Y, %I:%M %p IST')}\n"
            "==================================================\n\n"
            + script_block(1, label1, s1, links1)
            + script_block(2, label2, s2, links2)
            + script_block(3, "Top immigration / overseas-Indians story", s3, links3)
            + (f"{spot['company'].upper()} - ALL CANADA IT ROLES (official)\n"
               + "".join(f"  • {x['role']} | {x['location']} | posted {x['posted']}\n    {x['link']}\n" for x in spot["canada"])
               + "".join(f"  • [INDIA] {x['role']} | {x['location']} | posted {x['posted']}\n    {x['link']}\n" for x in spot["india"])
               + "\n" if spot else "")
            + jobs_block("JOBS OPEN TO INDIANS FROM INDIA", jobs_india)
            + jobs_block("JOBS FOR PEOPLE IN CANADA (PGWP / PR / WORK PERMIT)", jobs_canada)
            + stories_block(stories) + "\n")
    safe(write_doc, google_creds(), text)
    safe(send_email, [f"<b>Script 1 ({label1}):</b> {(s1 or {}).get('title', 'nothing strong today')}",
                      f"<b>Script 2 ({label2}):</b> {(s2 or {}).get('title', 'nothing strong today')}",
                      f"<b>Script 3 (news):</b> {(s3 or {}).get('title', 'nothing strong today')}",
                      f"IT company today: {spot['company'] if spot else '-'}. Jobs found: {len(jobs_india)} for Indians in India, {len(jobs_canada)} for PGWP/PR/work-permit holders. "
                      f"Stories tracked: {len(stories)}."])
    log("[DONE]")


if __name__ == "__main__":
    main()

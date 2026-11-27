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
GREENHOUSE = {"Geotab": "geotab", "Faire": "faire", "Hootsuite": "hootsuite"}
ASHBY = {"Wealthsimple": "wealthsimple", "1Password": "1password"}
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
                        "quals": re.sub(r"<[^>]+>", " ", j.get("basic_qualifications", ""))[:300]})
    return out


def greenhouse_jobs(slug: str) -> list[dict[str, Any]]:
    data = requests.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs", headers=UA, timeout=25).json()
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        dt = datetime.fromisoformat(j["updated_at"]) if j.get("updated_at") else None
        if _is_canada(loc) and IT_WORDS.search(j.get("title", "")) and _recent(dt):
            out.append({"role": j["title"], "location": loc, "posted": dt.strftime("%d %b"), "link": j["absolute_url"]})
    return out


def ashby_jobs(slug: str) -> list[dict[str, Any]]:
    data = requests.get(f"https://api.ashbyhq.com/posting-api/job-board/{slug}", headers=UA, timeout=25).json()
    out = []
    for j in data.get("jobs", []):
        loc = j.get("location", "") + " " + str((j.get("address") or {}).get("postalAddress", {}).get("addressCountry", ""))
        dt = datetime.fromisoformat(j["publishedAt"]) if j.get("publishedAt") else None
        if _is_canada(loc) and IT_WORDS.search(j.get("title", "")) and _recent(dt):
            out.append({"role": j["title"], "location": j.get("location", ""), "posted": dt.strftime("%d %b"),
                        "link": j["jobUrl"], "remote": j.get("isRemote")})
    return out


def company_spotlight() -> dict[str, Any] | None:
    """Pick ONE company hiring for IT roles in Canada today (official career sites). Companies with 3+ fresh
    Canada IT roles rotate day by day, so the same company is not repeated every day."""
    found: list[dict[str, Any]] = []
    try:
        ca = amazon_jobs("CAN")
        india = amazon_jobs("IND", {"base_query": "SDE I"}) + amazon_jobs("IND", {"base_query": "university graduate"})
        if ca:
            found.append({"company": "Amazon", "canada": ca, "india": india[:6],
                          "careers": "https://www.amazon.jobs/en/search?country=CAN"})
    except Exception as exc:
        log(f"[COMPANY WARN] Amazon: {exc}")
    for name, slug in GREENHOUSE.items():
        try:
            jobs = greenhouse_jobs(slug)
            if jobs:
                found.append({"company": name, "canada": jobs, "india": [],
                              "careers": f"https://job-boards.greenhouse.io/{slug}"})
        except Exception as exc:
            log(f"[COMPANY WARN] {name}: {exc}")
    for name, slug in ASHBY.items():
        try:
            jobs = ashby_jobs(slug)
            if jobs:
                found.append({"company": name, "canada": jobs, "india": [], "careers": f"https://jobs.ashbyhq.com/{slug}"})
        except Exception as exc:
            log(f"[COMPANY WARN] {name}: {exc}")
    for c in found:
        log(f"[COMPANY] {c['company']}: {len(c['canada'])} Canada IT roles, {len(c['india'])} India roles "
            f"(last {COMPANY_DAYS} days)")
    strong = sorted([c for c in found if len(c["canada"]) >= 3], key=lambda c: c["company"])
    pool = strong or sorted(found, key=lambda c: -len(c["canada"]))[:1]
    if not pool:
        return None
    pick = pool[NOW_IST.timetuple().tm_yday % len(pool)]
    log(f"[COMPANY] spotlight today: {pick['company']}")
    return pick


def company_topic(c: dict[str, Any]) -> str:
    ca = "\n".join(f"- {j['role']} | {j['location']} | posted {j['posted']} | {j['link']}" for j in c["canada"][:12])
    ind = "\n".join(f"- {j['role']} | {j['location']} | posted {j['posted']} | {j['link']}" for j in c["india"][:6])
    return (f"PATTERN P2 (her 123K-view format): {c['company']} is hiring for IT roles in Canada"
            + (" + India" if ind else "") + f". {len(c['canada'])} Canada IT roles posted in the last "
            f"{COMPANY_DAYS} days on the official careers site ({c['careers']}).\n"
            f"Say the company name in the hook. Read out the 4-5 main role names and the cities. Canada roles need a "
            f"valid work permit / PGWP / PR (no sponsorship promised). Do not invent pay: give a pay range only if it "
            f"is listed below." + (" India part: freshers / SDE I roles listed below." if ind else
                                   " No India roles today: say the Canada roles need Canada work authorization.")
            + f"\nCANADA ROLES (official):\n{ca}" + (f"\nINDIA ROLES (official):\n{ind}" if ind else ""))


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
    """Clears the old scripts, then writes today's (the Doc only ever shows today)."""
    docs = build("docs", "v1", credentials=creds).documents()
    end = docs.get(documentId=CANADA_DOC_ID).execute()["body"]["content"][-1]["endIndex"]
    reqs = [{"deleteContentRange": {"range": {"startIndex": 1, "endIndex": end - 1}}}] if end > 2 else []
    reqs.append({"insertText": {"location": {"index": 1}, "text": text}})
    docs.batchUpdate(documentId=CANADA_DOC_ID, body={"requests": reqs}).execute()
    log("[DOC] old scripts cleared, today's written")


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

    top = stories[0] if stories else None  # best story of any kind (company hiring counts too)
    topic1 = (f"PATTERN {top.get('pattern', 'none')}: {top['headline']}. {top.get('what_happened', '')}") if top else None
    def ctx(st: dict[str, Any]) -> str:
        return ("Headlines covering this story:\n" + "\n".join(st.get("refs", [])) + "\n"
                + "\n".join(f"YouTube: {v['title']} - {v['description']}" for v in st.get("yt", [])[:2]))

    s1 = write_script("Top immigration / overseas news", topic1, ctx(top)) if topic1 else None
    links1 = top.get("refs", []) if top else []

    # Script 2: ONE company hiring for IT roles (official careers site) -> else Job Bank jobs list -> else 2nd story
    spot = safe(company_spotlight, default=None)
    if spot:
        label2 = f"{spot['company']} is hiring - IT roles in Canada" + (" + India" if spot["india"] else "")
        s2 = write_script("jobs", company_topic(spot))
        links2 = [spot["careers"]] + [j["link"] for j in spot["canada"][:5]]
    elif jobs_india:
        label2, s2, links2 = "Canada jobs open to Indians (apply from India, LMIA / no permit needed)", write_script("jobs", jobs_topic(jobs_india, "india")), []
    elif jobs_canada:
        label2, s2, links2 = "Canada jobs for PGWP / PR / work-permit holders", write_script("jobs", jobs_topic(jobs_canada, "canada")), []
    else:
        nxt = next((x for x in stories if x is not top), None)
        label2 = "2nd biggest story"
        s2 = write_script("news", f"{nxt['headline']}. {nxt.get('what_happened', '')}", ctx(nxt)) if nxt else None
        links2 = nxt.get("refs", []) if nxt else []

    text = ("==================================================\n"
            f"CANADA-INDIA DAILY — {NOW_IST.strftime('%d %b %Y, %I:%M %p IST')}\n"
            "==================================================\n\n"
            + script_block(1, "Top story (hiring / immigration / overseas Indians)", s1, links1)
            + script_block(2, label2, s2, links2)
            + (f"{spot['company'].upper()} - ALL CANADA IT ROLES (official)\n"
               + "".join(f"  • {x['role']} | {x['location']} | posted {x['posted']}\n    {x['link']}\n" for x in spot["canada"])
               + "".join(f"  • [INDIA] {x['role']} | {x['location']} | posted {x['posted']}\n    {x['link']}\n" for x in spot["india"])
               + "\n" if spot else "")
            + jobs_block("JOBS OPEN TO INDIANS FROM INDIA", jobs_india)
            + jobs_block("JOBS FOR PEOPLE IN CANADA (PGWP / PR / WORK PERMIT)", jobs_canada)
            + stories_block(stories) + "\n")
    safe(write_doc, google_creds(), text)
    safe(send_email, [f"<b>Script 1:</b> {(s1 or {}).get('title', 'nothing strong today')}",
                      f"<b>Script 2 ({label2}):</b> {(s2 or {}).get('title', 'nothing strong today')}",
                      f"IT company today: {spot['company'] if spot else '-'}. Jobs found: {len(jobs_india)} for Indians in India, {len(jobs_canada)} for PGWP/PR/work-permit holders. "
                      f"Stories tracked: {len(stories)}."])
    log("[DONE]")


if __name__ == "__main__":
    main()

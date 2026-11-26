# Daily Viral Agent — how it works & setup

Runs free on GitHub Actions. Scripts land in your Google Doc by ~10 AM IST.

## What it does every day
1. **Instagram** — finds reels from the last 24 h with **100K+ likes** (or very fast likes),
   keeps only **educational, niche** ones (tech, AI, science facts, defence, geopolitics, India stories),
   notes the likes, **waits 75 min**, checks again, and keeps reels that gained **5,000+ likes**.
2. **YouTube (long videos)** — 12 h → 24 h → 48 h. Only **small channels** (≤300K subs), long videos (4 min+),
   **100K+ views**, views ≥ subscribers (outlier), **3+ viewer comments in the last 3 h**.
   News networks / big channels (CNN, BBC, NDTV…) are never picked.
3. **Major tech news** — 12 h → 24 h → 48 h. The story covered by the most different outlets (3+).
4. **Scripts** — the viral post is used for the **topic only**. Gemini researches it on Google Search,
   adds new points + an India angle, and writes a fresh Tenglish script. No "according to CNN", no copying.
5. **Google Sheet** — `Creatives` tab = today's picks with all numbers and links;
   `History` tab = every day appended, so you can check the AI's picks.
6. **Gmail** summary with links.

## Secrets (Settings → Secrets and variables → Actions → Secrets)
`YOUTUBE_API_KEY`, `GEMINI_API_KEY`, `APIFY_API_TOKEN`, `GOOGLE_SERVICE_ACCOUNT_JSON`,
`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD`, `RECIPIENT_EMAIL`.
The Sheet and Doc must be shared (Editor) with the service-account email.

## Change the rules without code (Settings → Secrets and variables → Actions → **Variables**)
| Variable | Default | Meaning |
|---|---|---|
| `WAIT_MINUTES` | 75 | Gap between 1st and 2nd check |
| `IG_MIN_LIKES` | 100000 | Instagram viral likes (24 h) |
| `IG_FAST_LIKES_PER_HOUR` | 4000 | Or this many likes per hour |
| `IG_MIN_GAIN` | 5000 | Likes gained during the wait |
| `YT_MIN_VIEWS` | 100000 | YouTube views |
| `YT_MAX_SUBS` | 300000 | Max subscribers = "small channel" |
| `YT_MIN_OUTLIER` | 1.0 | Views ÷ subscribers |
| `YT_MIN_RECENT_COMMENTS` | 3 | Viewer comments in last 3 h |
| `NEWS_MIN_SOURCES` | 3 | Outlets covering a story |
| `GEMINI_MODELS` | auto | Comma list to force a Gemini model |

## Why runs were late before
GitHub starts scheduled jobs late (yesterday's started ~9.5 h late) or skips them at busy times.
The workflow now has 5 start times (08:17, 08:47, 09:17, 10:47, 13:47 IST); the first one that starts runs,
the rest skip. GitHub also **pauses schedules after 60 days with no commits** — if emails stop, open
Actions and click "Enable workflow".

## Test now
Actions → **Daily Viral Agent** → **Run workflow** → set wait to `0` for a quick test (skips the 2nd check),
or leave `75` for the real method.

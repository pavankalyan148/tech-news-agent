# Daily Viral Tech & India News Agent — 5-Minute Cloud Setup Guide

This package runs **100% free online** on GitHub Actions every morning at **10:00 AM IST** (`04:30 UTC`).

## Step 1: Create a Gmail App Password (30 seconds)
1. Open `https://myaccount.google.com/apppasswords` (make sure 2-Step Verification is enabled on your Google account).
2. Name the app `Tech-News-Agent` and click **Create**.
3. Copy the 16-character password (no spaces needed)—this is your `GMAIL_APP_PASSWORD`.

## Step 2: Enable Sheets & Docs APIs + Service Account (2 minutes)
1. Open `console.cloud.google.com` and confirm your **`Tech-News-Agent`** project is selected.
2. Search and **Enable** both:
   - **Google Sheets API**
   - **Google Docs API**
3. In the left menu, go to **IAM & Admin → Service Accounts** → click **+ Create Service Account**.
   - Name it `sheet-doc-writer` → click **Create and Continue** → **Done**.
4. Click the new service account → open the **Keys** tab → **Add Key → Create new key → JSON** → download the `.json` file.
5. Copy the service account's email address (ends in `@tech-news-agent-...iam.gserviceaccount.com`), then click **Share** on BOTH your Google Sheet (**Gemini Tech**) and Google Doc (**Tech Gemini**) and give that email **Editor** access.

## Step 3: Upload to a Free Private GitHub Repository (2 minutes)
1. Go to `github.com/new` and create a **Private** repository named `tech-news-agent`.
2. Unzip `tech_news_agent_pipeline.zip` and upload all files (`agent.py`, `requirements.txt`, `SETUP_GUIDE.md`, and `.github/workflows/daily_tech_news.yml`).
3. In your repo, go to **Settings → Secrets and variables → Actions → New repository secret** and add these 7 secrets:
   - `YOUTUBE_API_KEY` — Your YouTube Data API v3 key
   - `GEMINI_API_KEY` — Your Google AI Studio Gemini API key
   - `APIFY_API_TOKEN` — Your regenerated Apify personal API token
   - `GOOGLE_SERVICE_ACCOUNT_JSON` — Full contents of the downloaded `.json` key file
   - `GMAIL_ADDRESS` — Your Gmail address (`vulluri.pavan@gmail.com`)
   - `GMAIL_APP_PASSWORD` — Your 16-character Gmail App Password from Step 1
   - `RECIPIENT_EMAIL` — Where you want the daily email sent (`vulluri.pavan@gmail.com`)

## Step 4: Test Immediately
1. Open the **Actions** tab in your GitHub repository.
2. Click **Daily Viral Tech & India News Agent (10 AM IST)** → click **Run workflow**.
3. Within ~1–2 minutes, check your **Gemini Tech** Sheet, **Tech Gemini** Doc, and Gmail inbox!

# 626 Eats — Setup Guide (Windows)

This guide assumes you have never used the command line before. Every step
is explained. Commands you type are shown in code blocks — type them exactly
as written, then press Enter.

---

## What you'll end up with

```
626eats/                          ← GitHub repo (also your live website)
├── index.html                    ← The PWA app
├── data/
│   └── restaurants.json          ← Auto-generated, never edit manually
├── scripts/
│   ├── research_sweep.py         ← Discovers restaurants via Google + Yelp
│   ├── refresh_photos.py         ← Refreshes expired photo URLs weekly
│   └── export_json.py            ← Exports Sheet → restaurants.json
└── .github/
    └── workflows/
        ├── research-sweep.yml    ← Manual trigger for new sweeps
        └── refresh-photos.yml    ← Runs every Sunday at 3am PT automatically
```

---

## Before you start — files you need

Make sure you have these two files saved somewhere easy to find, like your
Desktop or Downloads folder:

- `626eats_pwa.html` — the app
- `626eats_github_repo.zip` — scripts and automation files

---

## Step 1 — Install the required software

You need to install four things. Do them in order.

### 1a — Git (version control)

1. Go to **git-scm.com/download/win**
2. Click the download link — it auto-detects your Windows version
3. Run the installer
4. On every screen, leave all options at their defaults and click Next
5. Click Install, then Finish

### 1b — Python 3

1. Go to **python.org/downloads**
2. Click the big yellow **Download Python 3.x.x** button
3. Run the installer
4. **IMPORTANT:** On the first screen, check the box that says
   **"Add Python to PATH"** before clicking anything else
5. Click **Install Now**
6. When it finishes, click **Close**

### 1c — Visual Studio C++ Build Tools (needed for Playwright)

1. Go to **visualstudio.microsoft.com/visual-cpp-build-tools/**
2. Click **Download Build Tools**
3. Run the installer — when it opens, check **"Desktop development with C++"**
4. Click Install (downloads ~2GB, takes a few minutes)

### 1d — Verify everything installed

Press the **Windows key**, type `cmd`, and open **Command Prompt**.
Type each of these and press Enter — you should see a version number, not an error:

```
git --version
python --version
pip --version
```

If any of them say "not recognized", restart your computer and try again.
If still not working, the most common fix is re-running the installer and
making sure "Add to PATH" is checked.

---

## Step 2 — Create a GitHub account and repo

If you don't have a GitHub account:
1. Go to **github.com** → Sign up → follow the steps

Create the repo:
1. Once logged in, click **+** in the top right → **New repository**
2. Name it `626eats`
3. Set to **Public** (required for free hosting)
4. Leave all checkboxes unchecked — don't add README or .gitignore
5. Click **Create repository**
6. You'll see a page with setup instructions — copy the HTTPS URL at the top.
   It looks like: `https://github.com/YOUR_USERNAME/626eats.git`
   Keep this tab open, you'll need it in Step 4.

---

## Step 3 — Generate a GitHub Personal Access Token

GitHub no longer accepts your account password from the command line.
You need a token instead — it's like a special password just for this.

1. On github.com → click your profile photo (top right) → **Settings**
2. Scroll all the way down the left sidebar → **Developer settings**
3. **Personal access tokens → Tokens (classic)**
4. Click **Generate new token (classic)**
5. Give it a name like `626eats`
6. Under **Expiration**, choose **No expiration** (or 1 year)
7. Check the box next to **repo** (the first item in the list)
8. Scroll down → **Generate token**
9. **Copy the token NOW** — it starts with `ghp_` and you won't see it again.
   Paste it in Notepad to save it temporarily.

---

## Step 4 — Set up your project folder

Open **Command Prompt** (Windows key → type `cmd` → Enter).

```
cd %USERPROFILE%\Downloads
```

Unzip the repo files (this creates a `626eats_repo` folder):

```
tar -xf 626eats_github_repo.zip
```

Clone your new empty GitHub repo (paste your URL from Step 2):

```
git clone https://github.com/YOUR_USERNAME/626eats.git
```

When it asks for your username, type your GitHub username and press Enter.
When it asks for your password, paste your token from Step 3 (right-click
to paste in Command Prompt) and press Enter. You won't see it appear — that's normal.

Now go into the folder and copy the files in:

```
cd 626eats
xcopy ..\626eats_repo\scripts scripts\ /E /I
xcopy "..\626eats_repo\.github" .github\ /E /I
copy ..\626eats_repo\SETUP.md .
copy ..\626eats_pwa.html index.html
```

Create the data folder with a placeholder file:

```
mkdir data
echo {"restaurants":[],"dishes":[]} > data\restaurants.json
```

Check everything looks right:

```
dir
```

You should see: `index.html`, `data`, `scripts`, `.github`

---

## Step 5 — Push files to GitHub

Set your identity (only need to do this once):

```
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
```

Commit and push:

```
git add .
git commit -m "Initial 626 Eats setup"
git push origin main
```

When prompted for username and password again, use your GitHub username and
your token (from Step 3). Go to github.com/YOUR_USERNAME/626eats and
you should see your files there.

---

## Step 6 — Enable GitHub Pages (free hosting)

1. Go to your repo on github.com → **Settings** tab
2. Click **Pages** in the left sidebar
3. Under Source: **Deploy from a branch**
4. Branch: `main`, Folder: `/ (root)` → **Save**
5. Wait about 60 seconds, then your app is live at:
   `https://YOUR_USERNAME.github.io/626eats`

---

## Step 7 — Google Cloud setup

You need a Google Maps API key so the research script can find restaurants.

### 7a — Create a project (must be personal, not org)

1. Go to **console.cloud.google.com** and sign in with your Google account
2. Click the project dropdown at the very top → **New Project**
3. Name: `626eats`
4. **Organization: No organization** ← very important, avoids permission errors
5. Click **Create**, then wait a few seconds for it to appear

### 7b — Enable the required APIs

In your new project, go to **APIs & Services → Library**.
Search for each of the following and click **Enable**:

- **Places API**
- **Maps JavaScript API**
- **Google Sheets API**
- **Google Drive API**

### 7c — Get your Maps API key

1. **APIs & Services → Credentials → Create Credentials → API Key**
2. A key is created — click **Copy** to copy it (starts with `AIzaSy`)
3. Paste it in Notepad — you'll need it in Step 9

### 7d — Create a service account

This lets the automation write to your Google Sheet without your personal login.

1. **IAM & Admin → Service Accounts → Create Service Account**
   - Name: `626eats-bot`
   - Description: `626 Eats automation account`
   - Click **Create and Continue**
   - Skip the optional role/access steps → click **Done**
2. You'll see `626eats-bot` listed — click on it
3. Go to the **Keys** tab → **Add Key → Create new key → JSON → Create**
4. A `.json` file downloads automatically — this is your credential file.
   Move it somewhere easy to find, like `C:\Users\YOU\Downloads\626eats-key.json`

---

## Step 8 — Set up your Google Sheet

1. Go to **drive.google.com**
2. Click **New → File upload** → upload `626eats_database_template.xlsx`
3. Once uploaded, right-click it → **Open with → Google Sheets**
4. It opens as a Sheet — go to **File → Save as Google Sheets**
5. Copy the Sheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/THIS_IS_THE_ID/edit`
   The ID is everything between `/d/` and `/edit` — paste it in Notepad
6. Share the sheet with your service account:
   - Click **Share** (top right of the Sheet)
   - Open your `626eats-key.json` file in Notepad
   - Find the line that says `"client_email"` — copy that email address
     (looks like `626eats-bot@626eats-12345.iam.gserviceaccount.com`)
   - Paste it in the Share box → set to **Editor** → **Send**

---

## Step 9 — Add GitHub Secrets

Your API keys are stored as secrets so GitHub Actions can use them
without exposing them publicly in your code.

Go to your repo → **Settings → Secrets and variables → Actions →
New repository secret**

Add these three secrets one at a time:

| Secret Name | Value |
|---|---|
| `GOOGLE_API_KEY` | The `AIzaSy...` key from Step 7c |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | Open `626eats-key.json` in Notepad → Select All (Ctrl+A) → Copy (Ctrl+C) → paste the entire contents |
| `SPREADSHEET_ID` | The Sheet ID from the URL in Step 8 |

---

## Step 10 — Install Python packages

Back in Command Prompt:

```
pip install requests gspread google-auth playwright beautifulsoup4
```

Then install the Chrome browser for Playwright:

```
playwright install chromium
```

This downloads a ~150MB headless Chrome. It's used to scrape Yelp.

---

## Step 11 — Test your setup

Still in Command Prompt, go to your project folder if you're not already there:

```
cd %USERPROFILE%\Downloads\626eats
```

Set your API key for the test:

```
set GOOGLE_API_KEY=YOUR_AIzaSy_KEY_HERE
```

Run the test:

```
python scripts\research_sweep.py --test
```

You should see:
```
Google Places API... OK (20 results)
Playwright + Yelp... OK (Yelp page loaded)
All systems go.
```

If Google fails, double-check your API key and that the Places API is enabled.
If Yelp fails, re-run `playwright install chromium`.

---

## Step 12 — Run the first research sweep

This discovers all the Chinese restaurants in the SGV and writes them to
your Google Sheet. You run this from your PC the first time.

In Command Prompt, set all three environment variables:

```
set GOOGLE_API_KEY=YOUR_AIzaSy_KEY_HERE
set SPREADSHEET_ID=YOUR_SHEET_ID_HERE
```

For the service account JSON, the easiest approach on Windows is to set it
from the file directly using PowerShell. Open **PowerShell** (Windows key →
type `powershell` → Enter) and run:

```
$env:GOOGLE_SERVICE_ACCOUNT_JSON = Get-Content "$env:USERPROFILE\Downloads\626eats-key.json" -Raw
$env:GOOGLE_API_KEY = "YOUR_AIzaSy_KEY_HERE"
$env:SPREADSHEET_ID = "YOUR_SHEET_ID_HERE"
cd "$env:USERPROFILE\Downloads\626eats"
```

Then preview what the script would find, without writing anything yet:

```
python scripts\research_sweep.py --no-sheet
```

When you're happy, run the full sweep:

```
# Full run with Yelp (~45-60 min for all 16 cities)
python scripts\research_sweep.py

# Faster option — Google data only, no Yelp (~10 min)
python scripts\research_sweep.py --no-yelp

# Just a few cities to test
python scripts\research_sweep.py --cities "Alhambra,San Gabriel"
```

Leave the PowerShell window open — the script will print progress as it runs.

---

## Step 13 — Review, then push live

1. Open your Google Sheet
2. Click the filter icon on column J (Region) and filter for `NEEDS CLASSIFICATION`
3. For each flagged row, pick the correct Region from the dropdown
4. Review columns AA–AC (Yelp-detected dishes) — correct anything that looks wrong
5. When happy, export and push to GitHub:

Back in **PowerShell** (with env vars still set from above):

```
python scripts\export_json.py
git add data\restaurants.json
git commit -m "Add restaurants from first sweep"
git push origin main
```

Your live app at `https://YOUR_USERNAME.github.io/626eats` now has real data.

---

## Ongoing updates

**Weekly photo refresh** runs automatically every Sunday at 3am PT via
GitHub Actions. You don't need to do anything.

**To run a new sweep** (picks up newly opened restaurants):
Open PowerShell, set your env vars (same as Step 12), then:
```
python scripts\research_sweep.py
```

**To update the app** when you get a new version from Claude:
```
cd "$env:USERPROFILE\Downloads\626eats"
copy "$env:USERPROFILE\Downloads\626eats_pwa.html" index.html
git add index.html
git commit -m "Update app"
git push origin main
```
Live in about 60 seconds.

---

## Troubleshooting

**`git` / `python` / `pip` says "not recognized as an internal or external command"**
→ Restart your computer. If still broken, re-run the installer and make sure
  "Add to PATH" was checked. You can also manually search for the install
  location and add it to your PATH via System Properties → Environment Variables.

**`git push` fails with "Authentication failed"**
→ Make sure you're pasting your Personal Access Token (ghp_...) as the
  password, not your GitHub account password. They are different.
  If your token expired, generate a new one in Step 3.

**`GOOGLE_API_KEY not set` error**
→ Environment variables reset when you close the window. Re-run the
  `set` or `$env:` commands at the top of Step 12 each time you open a new session.
  To make them permanent: Windows key → search "Edit environment variables for
  your account" → New → add each variable there.

**`playwright install` fails or Chrome won't open**
→ Make sure you installed the Visual Studio C++ Build Tools in Step 1c.
  Try running `python -m playwright install chromium` instead.

**`iam.disableServiceAccountKeyCreation` error in Google Cloud**
→ You created the project inside a Google Workspace org. Go back to Step 7a
  and make sure you selected **"No organization"** when creating the project.

**GitHub Pages shows 404**
→ Wait 2-3 minutes after enabling Pages. Make sure `index.html` is in the
  root of the `main` branch, not inside a subfolder.
  Check Settings → Pages to confirm the source branch and folder.

**The sweep finishes but nothing appears in the Sheet**
→ Confirm the sheet was shared with the `client_email` from the JSON key
  (Step 8). Without Editor access, the script can't write rows.

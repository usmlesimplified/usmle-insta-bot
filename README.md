# USMLE Step 1 Instagram Autopilot (@usmle.simplified)

Every day, with no one touching it, this bot:

1. **Picks a topic** – a weekly series (Micro Monday, Pharm Tuesday, Path Wednesday, Physio/Biochem Thursday,
   Immuno Friday, Mnemonic Saturday, Sunday Vignette) drawn from ~200 high-yield Step 1 topics, never repeating a
   topic within 150 days.
2. **Refines it with live web search** – Claude checks what is timely (outbreaks, FDA news, what students are
   discussing), chooses the best candidate, and researches the exact high-yield facts, NBME traps and the
   keywords students search for.
3. **Writes a carousel** – cover hook, tables, mnemonics, NBME-style vignette + answer, trap/pearl, CTA.
4. **Fact-checks it** – a second, stricter Claude pass verifies every claim (with web search), fixes errors,
   and blocks the post entirely if it can't be made accurate.
5. **Designs the slides** – 1080×1350 branded JPEGs + a 1080×1920 story, auto-sized for phone readability.
6. **SEO** – keyword-first caption hook, keywords in the body, per-slide alt text, exactly 5 hashtags
   (Instagram's current cap: 2 fixed brand anchors + 3 topic tags), comment prompt, save/share/DM CTAs.
7. **Publishes** the carousel and a story to Instagram, then logs it in `data/history.json`.

It runs on **GitHub Actions** (free for public repos), so your laptop can be off.

---

## One-time setup (~30 minutes)

### 1. Instagram account
Your account must be **Professional** (Creator or Business):
Instagram → Settings → *Account type and tools* → *Switch to professional account*.

### 2. Claude token (uses your Claude subscription, no extra cost)
The bot runs Claude Code in the cloud with your existing Claude plan (Pro, Max, Team or Enterprise), so there
is no API bill. Each daily post uses a small part of your plan's usage limits.
1. On your computer, open a terminal where Claude Code is installed and run:
   ```bash
   claude setup-token
   ```
2. Approve in the browser. The terminal prints a long token (valid for 1 year). Copy it; you'll paste it into
   GitHub Secrets in step 5. Don't share it anywhere else.

*(Optional alternative: an API key from the Claude Console in `ANTHROPIC_API_KEY`, billed per use. If both are
set, the subscription token is used.)*

### 3. Instagram API token (Meta developer app)
1. Go to <https://developers.facebook.com> → **My Apps → Create app** → choose the use case for managing
   content on **Instagram** (Business app type).
2. In the app dashboard open **Instagram → API setup with Instagram business login**.
3. Under *Generate access tokens* click **Add account** and log in as **@usmle.simplified**. Approve the
   permissions `instagram_business_basic` and `instagram_business_content_publish`.
   - If it asks you to add the account as a tester: *App roles → Roles → Instagram Testers*, then accept the
     invite in the Instagram app (Settings → Website permissions → Apps and websites → Tester invites).
4. Click **Generate token**. Copy the **token** and the **Instagram account ID** shown next to it.
   The app can stay in Development mode because it only posts to your own account.

### 4. GitHub repository
1. Create a **public** repository (e.g. `usmle-insta-bot`). Public is required: Instagram downloads the
   slide images from the repo's public `media` branch. Your keys stay private in GitHub Secrets.
2. Upload this whole folder to it, **including the hidden `.github` folder** (easiest with
   `git`, GitHub Desktop, or by asking Claude to push it for you):
   ```bash
   cd usmle-insta-bot
   git init && git add . && git commit -m "Instagram autopilot"
   git branch -M main
   git remote add origin https://github.com/<you>/usmle-insta-bot.git
   git push -u origin main
   ```

### 5. Secrets (repo → Settings → Secrets and variables → Actions → New repository secret)

| Secret | Value |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | the token from `claude setup-token` (step 2) |
| `IG_ACCESS_TOKEN` | the Instagram token from step 3 |
| `IG_USER_ID` | the Instagram account ID from step 3 (optional, auto-detected if missing) |
| `GH_PAT` | a GitHub fine-grained token: *Settings → Developer settings → Fine-grained tokens*, access to **this repo only**, permission **Secrets: Read and write**. Used only to save the refreshed Instagram token every week. |

Also check *Settings → Actions → General → Workflow permissions* is **Read and write**.

### 6. Test, then go live
1. **Actions → Daily Instagram post → Run workflow**, tick **Dry run** → Run.
   When it finishes, open the run and download the **post-…** artifact: slides, caption, research brief and
   the fact-check log. Nothing is published.
2. Run it again **without** Dry run to publish your first post.
3. Run **Refresh Instagram token** once by hand to confirm the token refresh works.

From then on it posts every day at **6:47 PM Pakistan time** on its own.

---

## Day-to-day

| You want to… | Do this |
|---|---|
| See what was posted and why | `data/history.json`, or the run's artifact (kept 30 days) |
| Post a specific topic today | Actions → Daily Instagram post → Run workflow → fill **topic** (tick **force** if today's post already went out) |
| Change posting time | `.github/workflows/daily-post.yml` → `cron` (UTC; PKT = UTC+5) |
| Change hashtags, CTA, colours, model | `config.json` |
| Add/remove topics or change their weight | `bot/topic_bank.py` |
| Put your logo on the cover and CTA slides | add `assets/logo.png` (square, transparent background) |
| Pause the bot | Actions → Daily Instagram post → ⋯ → Disable workflow |

If a run fails (bad token, fact-check rejected two topics, API outage) **nothing is posted** and GitHub
emails you. The next day's run starts fresh.

## Safety rails
- A post is only published if the fact-checker returns a clean **pass** (up to two rounds of automatic fixes,
  plus a re-check if text had to be shortened to fit a slide). Otherwise the day is skipped.
- Never posts twice on the same day, and never blindly retries a publish call.
- Slides that would need tiny text are automatically shortened instead of being posted unreadable.
- Every post carries "Educational content for USMLE prep. Not medical advice."
- `config.json → mark_ai_generated: true` applies Instagram's "AI info" label if you want to disclose it.

## Running locally (optional)
```bash
pip install -r requirements.txt
python -m bot.main --sample --dry-run           # no API calls: renders samples/sample_post.json into out/
CLAUDE_CODE_OAUTH_TOKEN=... python -m bot.main --dry-run   # real research + writing, no publishing
python -m pytest -q                             # offline tests
```

## Cost
- GitHub Actions: free for public repos.
- Claude: $0 extra. Runs on your Claude subscription. If your plan's usage limit is used up at posting time,
  that day's run fails (nothing is posted) and the next day continues normally.
- The Claude token lasts 1 year: run `claude setup-token` again next October and update the secret.

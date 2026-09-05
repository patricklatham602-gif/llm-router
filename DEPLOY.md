# Deploying to Render (always-on, no computer required)

This gets you a permanent `https://something.onrender.com` URL that works
from your phone or computer, anywhere, without your own computer running.
Takes about 15 minutes. Costs **$7/month** (Render's Starter plan — the free
tier sleeps after 15 minutes idle and can't keep persistent files, neither
of which work for a chat app), plus a few cents/month for the disk.

## 1. Put the code on GitHub

Render deploys from a Git repo. Easiest path, no command line needed:

1. Go to https://github.com and sign up if you don't have an account (free).
2. Click **New repository**. Name it something like `llm-router`. Leave it
   **Public** (there's nothing secret in the code — your API keys and
   passwords never get committed, see `.gitignore`). Don't add a README.
3. On the new repo's page, click **uploading an existing file**.
4. Drag in every file from this folder *except* `config.json` and
   `history.json` if you have them locally (you won't yet, on a fresh copy).
5. Commit.

(If you're comfortable with git instead: `git init`, `git add .`,
`git commit -m "initial"`, create the repo on GitHub, then
`git remote add origin <url>` and `git push -u origin main`.)

## 2. Create the Render service

1. Go to https://render.com and sign up (you can use your GitHub account to
   sign in, which also makes step 3 easier).
2. Click **New +** → **Web Service**.
3. Connect your GitHub account if asked, then pick the `llm-router` repo.
4. Render should detect the `Dockerfile` and offer **Docker** as the
   runtime — pick that (not "Python 3") so it uses gunicorn correctly for
   streaming instead of Render's default dev-server guess.
5. Name it whatever you want — this becomes part of your URL
   (`https://<name>.onrender.com`).
6. Instance type: pick **Starter** ($7/mo). The free tier can't attach a
   persistent disk, which means every restart wipes your settings and
   history — not worth it for a chat app you'll actually use.

## 3. Add a persistent disk

Still on the service setup page (or under the service's **Disks** tab after
creation):

1. Add a disk. Mount path: `/data`. 1 GB is overkill for this app but it's
   the minimum — costs about $0.25/month.

## 4. Set environment variables

Under the service's **Environment** tab, add:

| Key | Value |
|---|---|
| `DATA_DIR` | `/data` |
| `FLASK_SECRET_KEY` | `42dda52362cc33697bc282b4c4e3bf3a2c972c5c8cc11c1a4c14d4837622a5a8` |

That secret key was randomly generated for you — fine to use as-is, or
generate your own with `python3 -c "import secrets; print(secrets.token_hex(32))"`
if you'd rather not use one that appeared in a chat transcript.

## 5. Deploy

Click **Create Web Service**. Render builds the Docker image and deploys —
takes a few minutes the first time. When it's done, you'll get a URL like
`https://llm-router-xyz.onrender.com`.

## 6. First visit — do this immediately

Open the URL. Before anything else:

1. Go to **Settings**, set an **app password**. This URL is now public on
   the internet — without a password, anyone who finds or guesses it can
   use your app and your API keys.
2. Add your provider API keys (same as local setup).

From then on, that URL is your app — open it from your phone (any network,
not just home Wi-Fi) or your computer, and it's the same conversation,
same settings, same everything, persisted on the disk you attached.

## Updating the app later

Push new code to the GitHub repo (upload changed files the same way, or
`git push` if using the command line) — Render auto-redeploys on push. Your
`/data` disk isn't touched by deploys, so settings and history survive.

## Running locally and in the cloud at the same time?

You can, but they're two separate instances with separate `config.json` /
`history.json` — a message sent on your local copy won't show up on the
Render one. Once this is deployed, simplest is to just use the Render URL
day to day and treat your local copy as a place to test changes before
pushing them.

## Cost summary

- Render Starter web service: $7/month
- 1 GB persistent disk: ~$0.25/month
- Plus whatever you spend on the actual AI provider API calls — same as
  running locally, just now billed to whichever provider gets picked per
  message.

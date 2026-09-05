# Deploying to Google Cloud Run

Uses your existing GCP billing instead of a new Render account. Gives you a
permanent `https://something-xyz.a.run.app` URL, no reverse proxy or cert
setup needed (Cloud Run provides HTTPS automatically). The app's storage
already reads its path from an environment variable, so this needs zero
code changes — Cloud Run mounts a Cloud Storage bucket as `/data` and the
app just writes `config.json`/`history.json` there like it would anywhere
else.

Everything below runs in **Cloud Shell** (the terminal icon `>_` in the
Google Cloud Console, top right) — nothing to install locally.

## 1. One-time project setup

```bash
# Set your project (find your project ID at console.cloud.google.com)
gcloud config set project YOUR_PROJECT_ID

# Turn on the services this needs
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
    cloudbuild.googleapis.com storage.googleapis.com

# Pick a region close to you, e.g. us-central1, us-east1, europe-west1
REGION=us-central1
PROJECT_ID=$(gcloud config get-value project)
BUCKET="${PROJECT_ID}-llm-router-data"

# Create the bucket that becomes /data inside the container
gcloud storage buckets create gs://$BUCKET --location=$REGION
```

## 2. Get the code into Cloud Shell

Either upload the zip (Cloud Shell's `⋮` menu → **Upload**, then
`unzip llm-router-app.zip && cd llm-router-app`), or clone it if you've
already pushed it to GitHub (see DEPLOY.md step 1 if you want a repo either
way — useful for tracking changes, not required for this path).

## 3. Deploy

```bash
SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")

gcloud run deploy llm-router \
  --source . \
  --region $REGION \
  --allow-unauthenticated \
  --execution-environment gen2 \
  --min-instances 1 \
  --memory 512Mi \
  --set-env-vars DATA_DIR=/data,FLASK_SECRET_KEY=$SECRET_KEY \
  --add-volume name=data,type=cloud-storage,bucket=$BUCKET \
  --add-volume-mount volume=data,mount-path=/data
```

- `--allow-unauthenticated` is required — without it, Cloud Run demands a
  Google-account auth token on every request, which breaks a normal
  browser visit. Your app password (set in step 5) is the actual gate here.
- `--min-instances 1` keeps it always warm (no cold-start delay) — this is
  also most of what you're paying for. Drop this flag if occasional
  5-15 second cold starts on the first request after idle time are fine;
  it noticeably lowers cost.
- `--execution-environment gen2` is required for the bucket volume mount to
  work at all.
- This builds straight from the `Dockerfile` in this folder via Cloud
  Build — no separate build/push step needed.

## 4. Let the service read/write the bucket

The deploy in step 3 will likely fail its health check the first time,
because the service's identity doesn't have permission to touch the bucket
yet:

```bash
PROJECT_NUMBER=$(gcloud projects describe $PROJECT_ID --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud storage buckets add-iam-policy-binding gs://$BUCKET \
  --member="serviceAccount:$SA" \
  --role="roles/storage.objectAdmin"
```

Then redeploy (same `gcloud run deploy` command as step 3 — it's safe to
run again).

## 5. First visit — do this immediately

```bash
gcloud run services describe llm-router --region $REGION --format='value(status.url)'
```

Open that URL. Before anything else:

1. **Settings** → set an app password. This is a public URL — without a
   password, anyone who finds it can use your app and spend your API
   budget.
2. Add your provider API keys.

## Cost

Estimates vary by source (roughly $3–10/month for the always-on
`--min-instances 1` instance, depending on region and exact config), plus
pennies for the bucket, plus Cloud Run's free monthly allowance covers some
of it automatically. Check the actual number for your setup with Google's
calculator: https://cloud.google.com/products/calculator — search "Cloud
Run" and plug in `1 vCPU / 512 MiB / min-instances=1`. Drop `--min-instances`
to `0` (scale-to-zero) if you'd rather trade occasional cold starts for a
close-to-free bill.

## Updating the app later

```bash
gcloud run deploy llm-router --source . --region $REGION
```
Re-run this from the folder with your changes — it rebuilds and redeploys
in place. The other flags (volume mount, env vars, min-instances) stick
from the first deploy unless you explicitly change them.

## Troubleshooting

- **Revision fails to become healthy** — almost always the IAM step (step
  4). Check `gcloud run services logs read llm-router --region $REGION`.
- **Settings don't survive a redeploy** — double check `DATA_DIR=/data` is
  still set (`gcloud run services describe llm-router --region $REGION`)
  and the volume mount is still attached.

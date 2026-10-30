#!/usr/bin/env bash
# One-time: move the existing Sahay project to the small, cheap setup. Safe to re-run.
#   - Cloud SQL db-g1-small → db-f1-micro (restarts the database: ~2-5 minutes of downtime)
#   - three scheduler jobs → one hourly "tick" job, inside the free tier
# Cloud Run itself is resized by the next infra/deploy.sh (1 CPU, 1 GiB, exactly one always-warm instance).
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${GCP_PROJECT:-project-422405a7-b76c-49df-b1c}"
REGION="${REGION:-asia-south1}"
URL="$(gcloud run services describe sahay --region "$REGION" --project "$PROJECT" --format "value(status.url)")"
CRON_SECRET="$(gcloud secrets versions access latest --secret sahay-cron-secret --project "$PROJECT" | tr -d '\r\n')"

echo "▶ Cloud SQL → db-f1-micro"
gcloud sql instances patch sahay-db --project "$PROJECT" --tier db-f1-micro --quiet

echo "▶ scheduler → one tick job"
for job in sahay-weekly-reports sahay-lifecycle sahay-daily-summary; do
  gcloud scheduler jobs delete "$job" --location "$REGION" --project "$PROJECT" --quiet 2>/dev/null || true
done
gcloud scheduler jobs delete sahay-tick --location "$REGION" --project "$PROJECT" --quiet 2>/dev/null || true
gcloud scheduler jobs create http sahay-tick --project "$PROJECT" --location "$REGION" --schedule "0 * * * *" \
  --time-zone "Asia/Kolkata" --uri "$URL/internal/cron/tick" --http-method POST \
  --headers "x-cron-secret=$CRON_SECRET,Content-Type=application/json" --message-body '{}'
echo "done"

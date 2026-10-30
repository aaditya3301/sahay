#!/usr/bin/env bash
# One-time GCP setup for Sahay. Safe to re-run (existing resources are skipped with an error message).
# Requires: gcloud logged in as a project owner. Passwords are read from .secrets/ (create them first:
#   mkdir -p .secrets && for f in db_password app_password jwt_secret cron_secret; do python -c "import secrets;print(secrets.token_urlsafe(24))" > .secrets/$f; done
set -uo pipefail
cd "$(dirname "$0")/.."

PROJECT="${GCP_PROJECT:-project-422405a7-b76c-49df-b1c}"
REGION="${REGION:-asia-south1}"
NUM=$(gcloud projects describe "$PROJECT" --format="value(projectNumber)")
BUCKET="sahay-media-$NUM"
SA="sahay-run@$PROJECT.iam.gserviceaccount.com"

gcloud services enable aiplatform.googleapis.com sqladmin.googleapis.com run.googleapis.com cloudbuild.googleapis.com \
  artifactregistry.googleapis.com secretmanager.googleapis.com storage.googleapis.com --project "$PROJECT"

gcloud sql instances create sahay-db --project "$PROJECT" --database-version=POSTGRES_17 --edition=ENTERPRISE \
  --tier=db-f1-micro --region="$REGION" --storage-size=10 --root-password="$(cat .secrets/db_password)"
gcloud sql databases create sahay --instance sahay-db --project "$PROJECT"

gcloud storage buckets create "gs://$BUCKET" --project "$PROJECT" --location "$REGION" --uniform-bucket-level-access
gcloud artifacts repositories create sahay --repository-format=docker --location="$REGION" --project "$PROJECT"

gcloud iam service-accounts create sahay-run --display-name "Sahay Cloud Run" --project "$PROJECT"
for r in roles/aiplatform.user roles/cloudsql.client roles/secretmanager.secretAccessor roles/logging.logWriter; do
  gcloud projects add-iam-policy-binding "$PROJECT" --member "serviceAccount:$SA" --role "$r" --condition=None -q >/dev/null
done
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" --member "serviceAccount:$SA" --role roles/storage.objectAdmin -q >/dev/null

for s in db_password:sahay-db-owner-password app_password:sahay-db-app-password jwt_secret:sahay-jwt-secret cron_secret:sahay-cron-secret; do
  tr -d '\n' < ".secrets/${s%%:*}" | gcloud secrets create "${s##*:}" --data-file=- --replication-policy=automatic --project "$PROJECT"
done
# One scheduler job (inside Cloud Scheduler's free tier). The app decides what's due on each tick:
# lifecycle every run, owner summary at 21:00, weekly report on Monday morning, newsletter draft on the 1st.
URL="${SERVICE_URL:-https://sahay-$NUM.$REGION.run.app}"
gcloud scheduler jobs create http sahay-tick --project "$PROJECT" --location "$REGION" --schedule "0 * * * *" \
  --time-zone "Asia/Kolkata" --uri "$URL/internal/cron/tick" --http-method POST \
  --headers "x-cron-secret=$(cat .secrets/cron_secret),Content-Type=application/json" --message-body '{}'
echo "done — now run: infra/deploy.sh --setup"

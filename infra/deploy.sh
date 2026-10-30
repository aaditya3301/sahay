#!/usr/bin/env bash
# Build and deploy Sahay to Google Cloud (Cloud Run + Cloud SQL + GCS + Vertex AI).
#
#   infra/deploy.sh                # build image, deploy service
#   infra/deploy.sh --setup        # also run migrations + reseed demo tenants (Cloud Run Job)
#   infra/deploy.sh --migrate      # apply schema changes + Agent Profiles, keep data
#
# One-time resources (already created for project-422405a7-b76c-49df-b1c; see infra/bootstrap.sh).
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${GCP_PROJECT:-project-422405a7-b76c-49df-b1c}"
REGION="${REGION:-asia-south1}"
TAG="${TAG:-$(date +%Y%m%d-%H%M%S)}"
IMG="$REGION-docker.pkg.dev/$PROJECT/sahay/app:$TAG"
SA="sahay-run@$PROJECT.iam.gserviceaccount.com"
SQL="$PROJECT:$REGION:sahay-db"
SECRETS="DB_PASSWORD=sahay-db-app-password:latest,DB_OWNER_PASSWORD=sahay-db-owner-password:latest,JWT_SECRET=sahay-jwt-secret:latest,CRON_SECRET=sahay-cron-secret:latest"
ENVFILE="infra/cloud.env.yaml"

echo "▶ building $IMG"
gcloud builds submit --tag "$IMG" --project "$PROJECT" --region "$REGION" .

SETUP_CMD=""
[[ "${1:-}" == "--setup" ]] && SETUP_CMD="python -m app.db.migrate && python -m app.seed --reset"
# --migrate: apply schema changes and (re)seed Agent Profiles on the existing tenants, keeping their data
[[ "${1:-}" == "--migrate" ]] && SETUP_CMD="python -m app.db.migrate && python -m app.seed --agent-only"
if [[ -n "$SETUP_CMD" ]]; then
  echo "▶ $SETUP_CMD"
  gcloud run jobs deploy sahay-setup --image "$IMG" --region "$REGION" --project "$PROJECT" --service-account "$SA" \
    --set-cloudsql-instances "$SQL" --env-vars-file "$ENVFILE" --set-secrets "$SECRETS" \
    --memory 2Gi --cpu 2 --task-timeout 1800 --max-retries 0 \
    --command sh --args="-c,$SETUP_CMD"
  gcloud run jobs execute sahay-setup --region "$REGION" --project "$PROJECT" --wait
fi

echo "▶ deploying service"
# Exactly one small, always-warm instance (no cold starts). Background jobs and per-conversation locks live in-process,
# and CPU stays allocated so debounced replies and simulations finish after the response.
gcloud run deploy sahay --image "$IMG" --region "$REGION" --project "$PROJECT" --service-account "$SA" \
  --set-cloudsql-instances "$SQL" --env-vars-file "$ENVFILE" --set-secrets "$SECRETS" \
  --memory 1Gi --cpu 1 --min-instances 1 --max-instances 1 --no-cpu-throttling --cpu-boost --concurrency 80 --timeout 900 \
  --allow-unauthenticated
gcloud run services describe sahay --region "$REGION" --project "$PROJECT" --format "value(status.url)"

#!/usr/bin/env bash
# Build and deploy Sahay to Google Cloud (Cloud Run + Cloud SQL + GCS + Vertex AI).
#
#   infra/deploy.sh                # build image, deploy service
#   infra/deploy.sh --setup        # also run migrations + reseed demo tenants (Cloud Run Job)
#   infra/deploy.sh --migrate      # apply schema changes + Agent Profiles, keep data
#   infra/deploy.sh --schema       # apply schema changes only
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
# Channel credentials live in Secret Manager; --env-vars-file replaces every env var, so they must be re-attached here.
# LIVEKIT_URL turns the voice worker on (see Dockerfile).
for pair in WHATSAPP_VERIFY_TOKEN=sahay-wa-verify-token WHATSAPP_APP_SECRET=sahay-wa-app-secret \
            LIVEKIT_URL=sahay-livekit-url LIVEKIT_API_KEY=sahay-livekit-api-key LIVEKIT_API_SECRET=sahay-livekit-api-secret \
            LIVEKIT_SIP_TRUNK_ID=sahay-livekit-sip-trunk SARVAM_API_KEY=sahay-sarvam-api-key VOICE_PLATFORM_KEY=sahay-voice-platform-key \
            BREVO_API_KEY=sahay-brevo-api-key EMAIL_WEBHOOK_TOKEN=sahay-email-webhook-token EMAIL_FROM=sahay-email-from \
            INBOUND_EMAIL_DOMAIN=sahay-inbound-email-domain TELEGRAM_BOT_TOKEN=sahay-telegram-bot-token \
            EMAIL_IMAP_USER=sahay-email-imap-user EMAIL_IMAP_PASSWORD=sahay-email-imap-password VOICE_DEFAULT_TENANT=sahay-voice-default-tenant \
            CASHFREE_APP_ID=sahay-cashfree-app-id CASHFREE_SECRET_KEY=sahay-cashfree-secret-key; do
  gcloud secrets describe "${pair#*=}" --project "$PROJECT" >/dev/null 2>&1 && SECRETS="$SECRETS,$pair:latest"
done

echo "▶ building $IMG"
gcloud builds submit --tag "$IMG" --project "$PROJECT" --region "$REGION" .

SETUP_CMD=""
[[ "${1:-}" == "--setup" ]] && SETUP_CMD="python -m app.db.migrate && python -m app.seed --reset"
# --migrate: apply schema changes and (re)seed Agent Profiles on the existing tenants, keeping their data
[[ "${1:-}" == "--migrate" ]] && SETUP_CMD="python -m app.db.migrate && python -m app.seed --agent-only"
# --schema: only apply schema changes (never touches seeded data or settings edited on the live site)
[[ "${1:-}" == "--schema" ]] && SETUP_CMD="python -m app.db.migrate"
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
  --memory 2Gi --cpu 1 --min-instances 1 --max-instances 1 --no-cpu-throttling --cpu-boost --concurrency 80 --timeout 900 \
  --allow-unauthenticated
gcloud run services describe sahay --region "$REGION" --project "$PROJECT" --format "value(status.url)"

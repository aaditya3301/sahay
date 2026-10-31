#!/usr/bin/env bash
# Put the Sahay Cloud Run service behind a global HTTPS load balancer on its own domain (default: sahay.naaradh.com).
# Cloud Run domain mappings are not offered in asia-south1, so the load balancer is the supported way to get a custom domain.
# Safe to re-run: every step is skipped when the resource already exists.
#
#   infra/domain.sh                      # create the load balancer, then print the DNS record to add
#   DOMAIN=sahay.example.com infra/domain.sh
set -euo pipefail

PROJECT="${GCP_PROJECT:-project-422405a7-b76c-49df-b1c}"
REGION="${REGION:-asia-south1}"
DOMAIN="${DOMAIN:-sahay.naaradh.com}"
SERVICE="${SERVICE:-sahay}"
G=(--global --project "$PROJECT")
q() { gcloud "$@" >/dev/null 2>&1; }

q compute addresses describe sahay-lb-ip "${G[@]}" || gcloud compute addresses create sahay-lb-ip --ip-version IPV4 "${G[@]}"
q compute network-endpoint-groups describe sahay-neg --region "$REGION" --project "$PROJECT" || \
  gcloud compute network-endpoint-groups create sahay-neg --region "$REGION" --project "$PROJECT" \
    --network-endpoint-type serverless --cloud-run-service "$SERVICE"
if ! q compute backend-services describe sahay-backend "${G[@]}"; then
  gcloud compute backend-services create sahay-backend --load-balancing-scheme EXTERNAL_MANAGED "${G[@]}"
  gcloud compute backend-services add-backend sahay-backend --network-endpoint-group sahay-neg \
    --network-endpoint-group-region "$REGION" "${G[@]}"
fi
q compute url-maps describe sahay-urlmap "${G[@]}" || gcloud compute url-maps create sahay-urlmap --default-service sahay-backend "${G[@]}"
q compute ssl-certificates describe sahay-cert "${G[@]}" || gcloud compute ssl-certificates create sahay-cert --domains "$DOMAIN" "${G[@]}"
q compute target-https-proxies describe sahay-https-proxy "${G[@]}" || \
  gcloud compute target-https-proxies create sahay-https-proxy --url-map sahay-urlmap --ssl-certificates sahay-cert "${G[@]}"
q compute forwarding-rules describe sahay-https "${G[@]}" || \
  gcloud compute forwarding-rules create sahay-https --load-balancing-scheme EXTERNAL_MANAGED --network-tier PREMIUM \
    --address sahay-lb-ip --target-https-proxy sahay-https-proxy --ports 443 "${G[@]}"

# plain http:// is redirected to https://
if ! q compute url-maps describe sahay-redirect "${G[@]}"; then
  tmp="$(mktemp)"
  printf 'name: sahay-redirect\ndefaultUrlRedirect:\n  httpsRedirect: true\n  redirectResponseCode: MOVED_PERMANENTLY_DEFAULT\n' > "$tmp"
  gcloud compute url-maps import sahay-redirect --source "$tmp" --quiet "${G[@]}"
  rm -f "$tmp"
fi
q compute target-http-proxies describe sahay-http-proxy "${G[@]}" || \
  gcloud compute target-http-proxies create sahay-http-proxy --url-map sahay-redirect "${G[@]}"
q compute forwarding-rules describe sahay-http "${G[@]}" || \
  gcloud compute forwarding-rules create sahay-http --load-balancing-scheme EXTERNAL_MANAGED --network-tier PREMIUM \
    --address sahay-lb-ip --target-http-proxy sahay-http-proxy --ports 80 "${G[@]}"

IP="$(gcloud compute addresses describe sahay-lb-ip --format 'value(address)' "${G[@]}")"
echo
echo "Add this DNS record where $DOMAIN's zone is managed:"
echo "  ${DOMAIN%%.*}   A   $IP   (TTL 300)"
echo "Google issues the HTTPS certificate once that record resolves (usually 10-60 minutes). Check with:"
echo "  gcloud compute ssl-certificates describe sahay-cert --global --project $PROJECT --format 'value(managed.status,managed.domainStatus)'"

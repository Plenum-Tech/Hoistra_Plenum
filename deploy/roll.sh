#!/usr/bin/env bash
#
# Build the all-in-one image, push it to ACR, and roll the container app onto it.
#
#   ./deploy/roll.sh <tag>
#
# Every step checks its own exit code, and the tag is verified to be in ACR before the app is
# told to use it. An earlier version of this piped each step to `tail -1`, which made `set -e`
# see tail's exit code rather than the command's: a build that FAILED still reached
# `az containerapp update`, and the app was pointed at an image that did not exist. That is
# revision 0000257, Failed, holding 100% of the traffic weight. The site was unharmed only
# because single-revision mode never cuts over to a revision that cannot start.
#
# Prerequisites: docker (linux/amd64), az CLI with the containerapp extension, and
#   az login && az acr login -n "$ACR_NAME"
# The ACR token expires; re-run `az acr login` if a push is refused.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Overridable so this is usable against another environment without editing it.
ACR_NAME="${ACR_NAME:-agentapi}"
ACR_REPO="${ACR_REPO:-finalplenumcafm}"
APP="${APP:-hoistra-frontend}"
RG="${RG:-azure-cotainer}"
# The workspace the container app logs to, for the Trace Lab schema check at the end.
LAW_WORKSPACE="${LAW_WORKSPACE:-0f4636cb-e800-421a-9d00-92eb95161659}"

TAG="${1:?usage: deploy/roll.sh <tag>}"
IMAGE="${ACR_NAME}.azurecr.io/${ACR_REPO}:${TAG}"
LOG="${ROLL_LOG:-${TMPDIR:-/tmp}/build_${TAG}.log}"

step() { printf '\n=== %s ===\n' "$1"; }

step "build ${IMAGE}"
cd "$ROOT" || exit 1
docker build --platform linux/amd64 \
  -f deploy/allinone/Dockerfile \
  --build-arg VITE_TRACE_LAB=1 \
  --build-arg "VITE_BUILD_ID=${TAG}" \
  -t "$IMAGE" . > "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  echo "BUILD FAILED (exit $rc). Last 40 lines of $LOG:"
  tail -40 "$LOG"
  exit 1
fi
echo "build ok -> $LOG"

step "push"
docker push "$IMAGE" >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
  echo "PUSH FAILED (exit $rc). Last 30 lines:"
  tail -30 "$LOG"
  echo
  echo "If this says authentication required: az acr login -n ${ACR_NAME}"
  exit 1
fi
echo "push ok"

step "verify the tag is really in ACR"
found=$(az acr repository show-tags -n "$ACR_NAME" --repository "$ACR_REPO" --output tsv 2>/dev/null \
        | grep -Fx "$TAG")
if [ -z "$found" ]; then
  echo "TAG ${TAG} IS NOT IN ACR -- refusing to roll."
  echo "This check is what stops the app being pointed at an image that does not exist."
  exit 1
fi
echo "tag present in ACR"

step "roll"
az containerapp update -n "$APP" -g "$RG" --image "$IMAGE" --output none
rc=$?
if [ $rc -ne 0 ]; then echo "UPDATE FAILED (exit $rc)"; exit 1; fi
echo "update accepted"

step "revision"
az containerapp revision list -n "$APP" -g "$RG" \
  --query "[?properties.trafficWeight>\`0\`].{rev:name,state:properties.runningState,traffic:properties.trafficWeight}" \
  --output table

step "wait for the traffic switch, then check the build id"
FQDN=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
# Traffic switches ~20-25 s AFTER `update` returns, so a check run straight away is answered by
# the OLD revision and reports a roll that has not happened yet.
for _ in $(seq 1 60); do
  curl -s -I "https://$FQDN/" | grep -qi "x-build-id: ${TAG}" && break
  sleep 5
done
served=$(curl -s -I "https://$FQDN/" | grep -i x-build-id | tr -d '
')
echo "${served:-no x-build-id served}"
case "$served" in
  *"$TAG"*) ;;
  *) echo "THE SITE IS NOT SERVING ${TAG} YET. Check the revision before trusting anything below."; ;;
esac

step "is Trace Lab's schema actually ready?"
cat <<'NOTE'
This cannot be answered over HTTP. /backend/trace-lab/health is a bare liveness check that never
looks at the schema, and authentication runs BEFORE the readiness gate, so every /api/trace-lab/*
route answers 401 without a token whether the schema is ready or not. Reading a 401 as "not 503,
therefore ready" is how the lab stayed down for an hour on 3 Nov 2026 while being reported healthy.

A schema version is TWO files -- the migration AND migrations/catalog.json's "version". Shipping
one without the other gives status=unreadable, which 503s every route exactly like a missing
migration does.

The service logs its own verdict on every check. That is the thing to read:
NOTE
# Printing the command and trusting the reader to run it is how it gets skipped. Run it.
sleep 30
verdicts=$(az monitor log-analytics query -w "$LAW_WORKSPACE" --analytics-query   "ContainerAppConsoleLogs_CL | where TimeGenerated > ago(10m) | where Log_s has 'trace_lab.schema' | project TimeGenerated, Log_s | order by TimeGenerated desc | take 10"   -o tsv 2>/dev/null)
if [ -z "$verdicts" ]; then
  echo "NO trace_lab.schema LINES YET. The check runs on the first request; open the lab and run"
  echo "this again before believing it is up. Ingestion also lags a minute or two."
else
  echo "$verdicts" | sed 's/^/  /' | head -6
  newest=$(echo "$verdicts" | head -1)
  case "$newest" in
    *unavailable*) echo; echo "TRACE LAB IS 503 FOR EVERYONE. The newest verdict is 'unavailable' (see the problems= field)." ;;
    *verified*)    echo; echo "Trace Lab schema verified." ;;
    *)             echo; echo "Could not read a verdict from the newest line; read it yourself above." ;;
  esac
fi


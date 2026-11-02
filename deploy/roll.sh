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

cat <<'NOTE'

Traffic switches a little AFTER `update` returns, so a health check run now reads the OLD
revision and will tell you the roll worked when it has not yet happened. Wait for the new
revision, then check the build id it serves:

  FQDN=$(az containerapp show -n "$APP" -g "$RG" --query properties.configuration.ingress.fqdn -o tsv)
  until curl -s -I "https://$FQDN/" | grep -qi "x-build-id: <tag>"; do sleep 10; done
  curl -s -I "https://$FQDN/" | grep -i x-build-id
NOTE

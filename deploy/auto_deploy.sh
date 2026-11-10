#!/bin/bash
# Deploy the image a push to main just built (last step of deploy/cloudbuild.trigger.yaml).
# Only the image changes: settings, secrets, volumes and who may call each service stay as `cloudrun.py deploy` set them,
# so the API stays public the whole time. Changing settings still goes through `cloudrun.py deploy` + `publish`.
# Order: migrate -> OCR service -> API service -> /ready check. A failed check puts both services back on the previous revision
# (a migration is not undone: migrations must keep working with the previous code).
set -euo pipefail
: "${IMAGE:?}" "${REGION:?}" "${SERVICE:?}"

if [ "${AUTO_DEPLOY:-true}" != "true" ]; then
    echo "Automatic deploy is off (_AUTO_DEPLOY=${AUTO_DEPLOY}). The image was built only."
    exit 0
fi

OCR="$SERVICE-ocr"
run() { gcloud "$@" --region="$REGION" --quiet; }
revision() { run run services describe "$1" --format='value(status.latestReadyRevisionName)'; }

if ! run run services describe "$OCR" --format='value(metadata.name)' >/dev/null 2>&1; then
    echo "The $OCR service does not exist yet. Create both services once from Cloud Shell" >&2
    echo "(cloudrun.py release --image <commit>); pushes deploy automatically after that." >&2
    exit 1
fi
previous_ocr=$(revision "$OCR")
previous_api=$(revision "$SERVICE")
echo "Serving now: $SERVICE=$previous_api, $OCR=$previous_ocr"

run run jobs update "$SERVICE-migrate" --image="$IMAGE"
run run jobs execute "$SERVICE-migrate" --wait

for name in "$OCR" "$SERVICE"; do
    run run services update "$name" --image="$IMAGE"
    run run services update-traffic "$name" --to-latest  # undo a pin left by an earlier rollback
done
if run run jobs describe "$SERVICE-push" --format='value(metadata.name)' >/dev/null 2>&1; then
    run run jobs update "$SERVICE-push" --image="$IMAGE"
fi

url=$(run run services describe "$SERVICE" --format='value(status.url)')
for attempt in 1 2 3; do
    # /ready checks the database and wakes the OCR service (cold start ~40 s)
    if curl -fsS --max-time 180 "$url/ready" >/dev/null; then
        echo "Deployed $IMAGE: $url"
        exit 0
    fi
    echo "/ready failed (attempt $attempt)" >&2
    sleep 10
done
echo "Going back to $SERVICE=$previous_api and $OCR=$previous_ocr" >&2
run run services update-traffic "$SERVICE" --to-revisions="$previous_api=100"
run run services update-traffic "$OCR" --to-revisions="$previous_ocr=100"
exit 1

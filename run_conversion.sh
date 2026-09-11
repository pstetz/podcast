#!/bin/bash
# Runs the newsletter->podcast conversion one email per process invocation.
# Each invocation loads the model, converts one email, then exits — fully
# releasing memory before the next one starts. Safer on low-RAM machines
# than one long-lived process holding the model for the whole batch.
set -u
cd "$(dirname "$0")"
source .venv/bin/activate
export HF_TOKEN=$(grep '^HF_TOKEN=' ~/.passwords/.env | cut -d= -f2-)
export HUGGING_FACE_HUB_TOKEN="$HF_TOKEN"
export OMP_NUM_THREADS=2
export MKL_NUM_THREADS=2

for i in $(seq 1 100); do
  out=$(python newsletter_to_podcast.py --input ./emails --output ./episodes \
        --engine kokoro --voice af_bella --concurrency 1 --limit 1 2>&1)
  echo "$out" | tee -a episodes/conversion.log
  if echo "$out" | grep -q "No emails could be read."; then
    echo "All done."
    break
  fi
  sleep 3
done

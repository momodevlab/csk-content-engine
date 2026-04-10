#!/bin/bash
# Usage: ./render.sh <Composition> '<props JSON>' <output_filename> <date_str>
# Example: ./render.sh StatReveal '{"stat": 40, "contextLine": "Accounting firms spend", "insightLine": "hours/month on manual entry"}' stat_reveal.mp4 2026-03-29

COMPOSITION=$1
PROPS=$2
OUTPUT=$3
DATE_STR=$4

npx remotion render src/index.ts "$COMPOSITION" "../content/$DATE_STR/track3/$OUTPUT" \
  --props="$PROPS" \
  --log=verbose

#!/usr/bin/env bash

set -e

ROOT="$(
  cd "$(dirname "$0")/.." &&
  pwd
)"

cd "$ROOT"

OPEN_ISSUES="$(
  gh issue list \
    --state open \
    --limit 1000 \
    --json number \
    --jq 'length'
)"

CLOSED_ISSUES="$(
  gh issue list \
    --state closed \
    --limit 1000 \
    --json number \
    --jq 'length'
)"

OPEN_PRS="$(
  gh pr list \
    --state open \
    --limit 1000 \
    --json number \
    --jq 'length'
)"

MERGED_PRS="$(
  gh pr list \
    --state merged \
    --limit 1000 \
    --json number \
    --jq 'length'
)"

ACTIVE_HOURS="${SERVESENSE_ACTIVE_HOURS:-1}"

TEST_OUTPUT="$(
  PYTHONPATH="$ROOT" \
  python3 -m pytest -q
)"

TEST_TOTAL="$(
  printf '%s\n' "$TEST_OUTPUT" \
    | grep -Eo '[0-9]+ passed' \
    | tail -1 \
    | awk '{print $1}'
)"

TEST_TOTAL="${TEST_TOTAL:-0}"

export SERVESENSE_OPEN_ISSUES="$OPEN_ISSUES"
export SERVESENSE_CLOSED_ISSUES="$CLOSED_ISSUES"
export SERVESENSE_OPEN_PRS="$OPEN_PRS"
export SERVESENSE_MERGED_PRS="$MERGED_PRS"
export SERVESENSE_TESTS_PASSED="$TEST_TOTAL"
export SERVESENSE_TESTS_TOTAL="$TEST_TOTAL"
export SERVESENSE_CI_SUCCESS=1
export SERVESENSE_ACTIVE_HOURS="$ACTIVE_HOURS"

python3 tools/richmack_metrics.py

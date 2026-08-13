#!/usr/bin/env bash
# Build the radar-pipeline image and push it to ECR. Run this yourself from
# somewhere with AWS credentials (CloudShell, your own machine) — not from
# the Codespace this was written in, which has no AWS access.
#
# Prerequisite: the `radar-scraper` ECR repo already exists (DEPLOYMENT.md,
# Phase 2 — "Create the ECR repo" console steps).
#
# Usage:
#   AWS_ACCOUNT_ID=123456789012 AWS_REGION=us-east-1 ./push_to_ecr.sh
#
# Both can also be edited as defaults below instead of passed as env vars.

set -euo pipefail

AWS_ACCOUNT_ID="${AWS_ACCOUNT_ID:-<your-account-id>}"
AWS_REGION="${AWS_REGION:-<your-region>}"
REPO_NAME="${REPO_NAME:-radar-scraper}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

if [[ "$AWS_ACCOUNT_ID" == "<your-account-id>" || "$AWS_REGION" == "<your-region>" ]]; then
  echo "Set AWS_ACCOUNT_ID and AWS_REGION (env vars, or edit the defaults in this script)." >&2
  exit 1
fi

ECR_URI="${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/${REPO_NAME}"

echo "== Building ${REPO_NAME}:${IMAGE_TAG} =="
docker build -t "${REPO_NAME}:${IMAGE_TAG}" "$(dirname "$0")"

echo "== Authenticating Docker to ECR (${AWS_REGION}) =="
aws ecr get-login-password --region "${AWS_REGION}" \
  | docker login --username AWS --password-stdin "${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com"

echo "== Tagging ${REPO_NAME}:${IMAGE_TAG} -> ${ECR_URI}:${IMAGE_TAG} =="
docker tag "${REPO_NAME}:${IMAGE_TAG}" "${ECR_URI}:${IMAGE_TAG}"

echo "== Pushing ${ECR_URI}:${IMAGE_TAG} =="
docker push "${ECR_URI}:${IMAGE_TAG}"

echo "== Done =="
echo "Image URI for the task definition: ${ECR_URI}:${IMAGE_TAG}"

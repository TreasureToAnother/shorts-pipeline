#!/usr/bin/env bash
# Installs ffmpeg, ImageMagick and Tesseract (OCR, to catch stray writing in
# AI illustrations) on the GitHub runner.
#
# Ubuntu's package mirror is sometimes so slow that this step alone has
# outlasted the whole job's time limit (2 Shorts lost on 2026-10-23), so
# the downloaded .deb files are kept in ~/apt-cache, which the workflow
# saves with actions/cache — after the first run, installs come from
# GitHub's cache instead of the mirror.
set -euo pipefail

CACHE="$HOME/apt-cache"
mkdir -p "$CACHE/partial"
APT_OPTS=(-o Acquire::Retries=5 -o Acquire::http::Timeout=60 -o DPkg::Lock::Timeout=120
          -o Dir::Cache::Archives="$CACHE")

for attempt in 1 2 3; do
  if sudo apt-get "${APT_OPTS[@]}" update -qq && \
     sudo DEBIAN_FRONTEND=noninteractive apt-get "${APT_OPTS[@]}" install -y -qq ffmpeg imagemagick tesseract-ocr; then
    break
  fi
  echo "apt attempt $attempt failed; retrying in 15s"
  sleep 15
done

# actions/cache runs as the runner user; apt left these owned by root.
sudo chown -R "$(id -u):$(id -g)" "$CACHE"
rm -f "$CACHE/lock"

ffmpeg -version | head -1
convert -version | head -1
tesseract --version | head -1

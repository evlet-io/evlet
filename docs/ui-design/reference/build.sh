#!/bin/sh
set -e
cd "$(dirname "$0")"
{
  printf '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
  cat src/1-head.html
  printf '\n</head>\n<body>\n'
  cat src/2-body.html
  printf '<script>\n'
  for f in icons.js 3-data.js 4-core.js 5-views.js 6-actions.js 7-doc.js 9-invoice.js 10-library.js 8-boot.js; do cat "src/$f"; done
  printf '</script>\n</body>\n</html>\n'
} > mockup.html
echo "Built mockup.html"

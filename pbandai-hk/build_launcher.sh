#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p dist

echo "Building Windows amd64 exe..."
(cd launcher && GOOS=windows GOARCH=amd64 go build -ldflags="-s -w" -o ../dist/PBandaiHK.exe .)

echo "Building Linux amd64 binary..."
(cd launcher && GOOS=linux GOARCH=amd64 go build -ldflags="-s -w" -o ../dist/PBandaiHK .)

echo "Building macOS amd64 binary..."
(cd launcher && GOOS=darwin GOARCH=amd64 go build -ldflags="-s -w" -o ../dist/PBandaiHK-macos-amd64 .)

echo "Building macOS arm64 binary..."
(cd launcher && GOOS=darwin GOARCH=arm64 go build -ldflags="-s -w" -o ../dist/PBandaiHK-macos-arm64 .)

chmod +x dist/PBandaiHK dist/PBandaiHK-macos-amd64 dist/PBandaiHK-macos-arm64 setup_and_launch.sh
ls -lh dist/PBandaiHK.exe dist/PBandaiHK dist/PBandaiHK-macos-*
echo "Done."

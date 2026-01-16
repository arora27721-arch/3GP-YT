#!/bin/bash
set -e

echo "================================"
echo "Building YouTube 3GP Converter"
echo "================================"

# Install system dependencies (FFmpeg only)
echo "Installing system dependencies..."
apt-get update
apt-get install -y --no-install-recommends \
    ffmpeg \
    imagemagick \
    fonts-dejavu-core \
    curl

# Clean up to reduce image size
rm -rf /var/lib/apt/lists/*

# Upgrade pip
echo "Upgrading pip..."
pip install --no-cache-dir --upgrade pip

# Install Python dependencies
echo "Installing Python dependencies..."
pip install --no-cache-dir -r requirements.txt

# Verify installations
echo "Verifying installations..."
python --version
ffmpeg -version | head -1
pip list | grep -E "(Flask|yt-dlp|gunicorn)"

echo "================================"
echo "Build completed successfully!"
echo "================================"
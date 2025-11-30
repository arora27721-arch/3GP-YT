# YouTube to 3GP/MP3 Converter for Feature Phones

## Overview

This is a Flask-based web application that converts YouTube videos to feature phone-compatible formats (3GP video and MP3 audio). The application is specifically designed for users with older Nokia phones and low-bandwidth 2G/3G networks. It provides a simple, lightweight interface optimized for basic browsers and includes features like video search, playlist conversion, file splitting for large downloads, and optional cookie management for bypassing YouTube restrictions.

## Recent Changes

**November 29, 2025 - Google Cloud Shell Optimizations & Standalone Subtitle Burner**
- ✓ **Extreme Resource Limits for Google Cloud Shell:**
  - Video duration: 120 hours max (was unlimited, now explicit)
  - File size: 10GB max
  - FFmpeg threads: 4 (from 1)
  - Concurrent downloads: 3 (from 1)
  - Disk space thresholds: 200MB critical, 500MB warning
- ✓ **New Standalone Subtitle Burner Tool (Port 8000):**
  - Independent Flask app for burning subtitles into videos
  - Supports MP4, 3GP, MKV, AVI, MOV, WEBM input formats
  - Supports SRT, ASS, VTT subtitle formats
  - Outputs 3GP format optimized for feature phones
  - Quality presets: low, medium (default), high
  - **Smart splitting**: Split output into 2-20 parts for easier transfer
  - Time estimation based on video duration and quality settings
  - Separate history page at /history
  - Files automatically cleaned up after 6 hours

**November 12, 2025 - Quality Preservation & Subtitle Burning Reliability**
- ✓ **Quality Preservation Across All Retry Scenarios:**
  - Fixed simpler retry logic to preserve user's selected quality (high, low, medium, etc.)
  - Previously hardcoded low-quality values for retries, now uses selected quality preset
  - Applies to both MP3 and 3GP conversions
  - Retry removes advanced compression flags but keeps same bitrate, fps, sample rate
- ✓ **Enhanced Subtitle Burning Reliability:**
  - Created convert_srt_to_dual_line_ass() helper for modular SRT→ASS conversion
  - Added 3-attempt SRT→ASS conversion retry:
    1. Initial conversion attempt
    2. Immediate retry if failed
    3. Re-download subtitles from YouTube and convert if still failing
  - Added 2-attempt FFmpeg burning retry:
    1. Full compression + selected quality
    2. Simpler settings (same quality, removes trellis/mbd/cmp/subcmp) if failed
  - Subtitle text appearance unchanged across retries (controlled by ASS styling, not compression)
  - Graceful degradation with detailed status updates at each retry stage
  - Added url parameter to burn_subtitles_ffmpeg_3gp() to enable subtitle re-downloading

**November 12, 2025 - Cookie System Hardening & Subtitle Enhancements**
- ✓ **Cookie System Major Improvements:**
  - Created centralized get_valid_cookiefile() helper with cookie health validation
  - Enhanced validate_cookies() with expiry detection (7-day warning threshold)
  - Added expired cookie counting and malformed line tracking
  - Atomic file writes with 2MB size limit and encoding auto-detection (UTF-8 + Latin-1 fallback)
  - Detailed logging and health metrics for debugging
  - Integrated cookie validation in all yt-dlp paths (download, search, playlist, subtitles)
- ✓ **Subtitle Download Improvements:**
  - Added 3-retry mechanism with exponential backoff (1s, 2s, 4s delays)
  - Improved VTT to SRT conversion with encoding fallback and validation
  - Better error handling and detailed logging for subtitle failures
  - **REMOVED subtitle limits** - now unlimited (was 45min/500MB, now infinite)
- ✓ **Playlist Subtitle Support:**
  - Verified burn_subtitles parameter passes through entire playlist chain
  - Works identically to single-video subtitle burning
  - Error messages properly surfaced for geo-restricted videos

**November 10, 2025 - Production Deployment Configuration**
- ✓ Fixed None comparison bug in MAX_VIDEO_DURATION check (caused conversion failures)
- ✓ Created complete Render/Docker deployment setup optimized for FREE TIER (512MB RAM, 0.1 vCPU)
- ✓ Added ImageMagick and fonts to Dockerfile and build.sh for subtitle burning support
- ✓ Configured Gunicorn with 1 worker, 1 thread (minimizes CPU usage for 0.1 vCPU constraint)
- ✓ MoviePy subtitle burning already optimized with threads=1, ultrafast preset, 2MB buffers
- ✓ Created render.yaml, Dockerfile, docker-compose.yml, build.sh, .dockerignore
- ✓ Fixed LSP errors in playlist code (defensive None checking)
- ✓ Verified subtitle limits (45min, 500MB) are separate from main conversion (unlimited)

**November 10, 2025 - Subtitle Burning Feature (3GP only, EXPERIMENTAL)**
- ✓ Implemented English subtitle burning capability for 3GP videos only
- ✓ **3GP subtitle burning**: FFmpeg-based dual-line subtitles using ASS format
  - Uses IDENTICAL encoding parameters as original 3GP conversion (bitrate, fps, GOP, trellis, etc.)
  - Combines video filters: scale/pad THEN ass subtitle filter
  - Font size: 3px for minimal interference with video content
  - Line 1 positioned at BOTTOM center (Alignment=2, MarginV=5)
  - Line 2 (if exists) positioned at TOP center (Alignment=8, MarginV=5)
  - Preserves YouTube's line breaks - different speakers get different lines
  - Output: {file_id}_with_subs.3gp (replaces regular 3GP)
  - FIXED: Videos with subtitles now display at exact same size as non-subtitle versions
- ✓ Removed MoviePy dependency completely - FFmpeg only
- ✓ Added download_subtitles() function to fetch English subtitles via yt-dlp (manual + auto-generated)
- ✓ Created burn_subtitles_ffmpeg_3gp() with quality_preset parameter for exact encoding match
- ✓ Updated file detection in /status, /download, /history routes to handle _with_subs.3gp files
- ✓ Updated cleanup functions to delete both regular and subtitled 3GP files
- ✓ Integrated subtitle burning into conversion pipeline with resource limits (45 min, 500MB max when enabled)
- ✓ Added UI checkboxes in index.html and 3gp.html with clear experimental warnings
- ✓ Implemented graceful degradation: subtitle burning failure continues normal conversion with status messages

**Earlier Changes**
- ✓ Implemented full playlist support with detection, confirmation page, and batch processing
- ✓ Fixed playlist URL detection to handle both pure playlist URLs and watch?v=...&list=... formats
- ✓ Configured unlimited processing time (DOWNLOAD_TIMEOUT = None, CONVERSION_TIMEOUT = None, MAX_VIDEO_DURATION = None)
- ✓ Implemented smart cleanup that only deletes completed files after 6 hours (processing files never expire)
- ✓ Installed FFmpeg system dependency for video/audio conversion
- ✓ Configured Flask workflow running on port 5000 with proper host binding (0.0.0.0)
- ✓ Added thread-safe JSON persistence for playlist status tracking

## User Preferences

Preferred communication style: Simple, everyday language.

## System Architecture

### Frontend Architecture
- **Template Engine**: Jinja2 templates with Flask
- **Design Philosophy**: Minimal, feature-phone-optimized HTML/CSS
  - Inline CSS for simplicity and reduced HTTP requests
  - No JavaScript dependencies (works on basic browsers)
  - Low-resolution optimized layouts (max-width: 400px)
  - Cache-control headers to prevent stale content
  - Optional thumbnail loading to save bandwidth
- **User Interface Pattern**: Form-based workflows with server-side rendering
  - Status pages with auto-refresh meta tags (30-second intervals)
  - Simple navigation with button-style links
  - Progressive disclosure of conversion options

### Backend Architecture
- **Web Framework**: Flask 3.0.0
  - Session management with secret key (environment variable or generated)
  - After-request cache control for HTML responses
  - Template rendering for all user-facing pages
- **Download Engine**: yt-dlp library
  - Multiple download methods (7 fallback strategies mentioned in templates)
  - Cookie support for bypassing YouTube restrictions (optional)
  - Format selection and quality presets for 3GP and MP3
- **File Processing**: Server-side video/audio conversion
  - Subprocess-based FFmpeg operations (implied by conversion functionality)
  - Background processing with threading for long-running conversions
  - File splitting capability for large files (re-encoding each part)
  - **Subtitle Burning (EXPERIMENTAL)**: MoviePy-based subtitle overlay
    - Downloads English subtitles (manual or auto-generated) via yt-dlp
    - **3GP format**: Split 2-line subtitles for Nokia 5310 (240x320 screen, 176x144 video)
      - Canvas expanded to 240x320 (full screen, video centered vertically at Y=88)
      - Font size: 12px, size constraint: (230, None) for natural wrapping
      - First line positioned BELOW video at Y=250 (primary position)
      - Second line (if exists) positioned ABOVE video at Y=50
      - Line breaks PRESERVED (YouTube-style dynamic lines, different speakers get different lines)
      - No overlap on video content - subtitles in 88px gaps above and below
      - Burned after 3GP conversion
      - Output file: {file_id}_with_subs.3gp
    - **MP4 format**: Multi-line YouTube-style text (fontsize=18, preserves line breaks)
      - Burned before final output
      - Output file: {file_id}_with_subs.mp4
    - Memory-optimized settings for Render constraints (threads=1, bufsize=2M)
    - Resource limits: max 45 minutes, 500MB file size when enabled
    - Requires ImageMagick and system fonts (DejaVu-Sans-Bold preferred)
    - Graceful degradation: continues normal conversion if subtitle burning fails
- **Status Tracking**: JSON-based status file system
  - File: `/tmp/conversion_status.json`
  - Tracks download/conversion progress
  - Manages file lifecycle and cleanup (6-hour retention)
  - History tracking (48-hour window)

### Data Storage
- **File Storage**: Temporary filesystem storage
  - Downloads folder: `/tmp/downloads`
  - Cookies folder: `/tmp/cookies`
  - Ephemeral storage with automatic cleanup
  - File retention: 6 hours after conversion
  - History retention: 48 hours
- **State Management**: JSON file for conversion status
  - No persistent database
  - In-memory session state via Flask sessions
  - Stateless design suitable for cloud/container deployment
- **File Identification**: Hash-based file IDs
  - Uses hashlib for generating unique file identifiers
  - Enables file deduplication and retrieval

### Processing Pipeline
- **Conversion Workflow**:
  1. URL submission (single video or playlist)
  2. Format selection (3GP video or MP3 audio)
  3. Quality preset selection
  4. Background download and conversion
  5. Status polling with auto-refresh
  6. File delivery via send_file
- **Playlist Handling**:
  - Playlist detection and confirmation step
  - Sequential video processing
  - Progress tracking per video and overall
  - Partial success handling (some videos may fail)
- **File Splitting**:
  - Post-download splitting for large files
  - Configurable number of parts (2-50)
  - Re-encoding each part for compatibility
  - Command-line instructions for rejoining parts

### Quality Presets
- **3GP Video Presets**: Multiple quality levels for different network conditions
  - Auto mode (recommended low quality)
  - Resolution: 176x144 mentioned as default
  - Configurable via video_quality parameter
- **MP3 Audio Presets**: Bitrate-based quality selection
  - Auto mode defaults to 128kbps
  - Multiple preset options for different file sizes
  - Configurable via mp3_quality parameter

## External Dependencies

### Third-Party Services
- **YouTube**: Primary video source
  - Video and playlist metadata extraction
  - Content download via yt-dlp
  - Search functionality
  - Optional cookie-based authentication
- **yt-dlp Library**: YouTube download engine
  - Version: 2024.11.4
  - Handles video extraction, format selection, and download
  - Manages YouTube API interactions and format negotiation

### Required System Tools
- **FFmpeg**: Video/audio conversion
  - Used for format conversion (3GP, MP3)
  - File splitting and re-encoding
  - Should be available in system PATH
- **ImageMagick** (Optional - for subtitle burning): Text rendering for MoviePy
  - Required for subtitle burning feature
  - Installed in dev environment via Nix (imagemagick, dejavu_fonts)
  - Must be provisioned separately for Render/production deployment
  - Subtitle feature fails gracefully if not available

### Python Dependencies
- **Flask 3.0.0**: Web framework
- **yt-dlp 2024.11.4**: YouTube downloader
- **gunicorn 21.2.0**: WSGI HTTP server for production deployment
- **moviepy 1.0.3**: Video editing and subtitle burning (experimental feature)

### Environment Variables
- **SESSION_SECRET**: Flask session encryption key (optional, auto-generated if missing)

### Cookie Management (Optional)
- **Purpose**: Bypass YouTube restrictions and rate limits
- **Format**: Netscape cookies.txt format
- **Storage**: `/tmp/cookies/youtube_cookies.txt`
- **Validation**: Cookie format and YouTube domain checking
- **Use Case**: Cloud hosting with IP-based rate limiting, sign-in required errors

### Deployment Considerations
- Designed for cloud/container deployment (ephemeral storage in /tmp)
- No persistent database required
- Suitable for Replit, Heroku, or similar platforms
- Requires FFmpeg installation on host system
- File cleanup mechanism needed for long-running instances
- **For Subtitle Burning on Render**: Must install ImageMagick and fonts in build command:
  ```
  apt-get update && apt-get install -y imagemagick fonts-dejavu-core
  ```
  - Feature will fail gracefully without these dependencies
  - Users will receive clear error messages in status updates
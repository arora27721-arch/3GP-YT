# YouTube to 3GP/MP3 Converter for Feature Phones

## Overview
This project is a Flask-based web application designed to convert YouTube videos into 3GP (video) and MP3 (audio) formats, specifically optimized for feature phones and low-bandwidth networks. Its primary purpose is to provide users with older mobile devices access to YouTube content. Key capabilities include video search, playlist conversion, splitting large files for easier download, and optional cookie management to bypass YouTube restrictions. The application aims to be lightweight, simple, and accessible, catering to a market segment with basic browsing capabilities.

## User Preferences
Preferred communication style: Simple, everyday language.

## System Architecture

### UI/UX Decisions
The application uses Jinja2 templates with Flask for a minimal, feature-phone-optimized interface. It relies on inline CSS, avoids JavaScript for broad compatibility, and features low-resolution layouts (max-width: 400px). User interaction is primarily form-based with server-side rendering, status pages with auto-refresh, and simple navigation. Optional thumbnail loading helps conserve bandwidth.

### Technical Implementations
- **Web Framework**: Flask 3.0.0 handles routing, session management, and template rendering.
- **Download Engine**: `yt-dlp` (version 2024.11.4) is used for robust YouTube video extraction, format selection, and downloading, including cookie support for bypassing restrictions.
- **Conversion Engine**: FFmpeg performs all video/audio conversions (3GP, MP3), file splitting, and advanced encoding tasks. Operations are run in subprocesses.
- **Subtitle Burning**: An experimental feature allows burning English subtitles into 3GP videos. This uses FFmpeg to overlay dual-line subtitles, positioned to avoid overlapping video content, optimized for small screens (e.g., Nokia 5310). Resource limits (45 min, 500MB) apply.
- **Background Processing**: Long-running conversions are handled with threading, and a JSON-based status file system tracks progress and manages file lifecycles.
- **File Management**: Temporary filesystem storage (`/tmp/downloads`, `/tmp/cookies`) is used, with files retained for 6 hours post-conversion and history for 48 hours. File IDs are hash-based for uniqueness.
- **Playlist Support**: The application detects and processes entire YouTube playlists sequentially, with progress tracking and partial success handling.
- **File Splitting**: Large converted files can be split into multiple smaller parts, which are then re-encoded for compatibility.

### Feature Specifications
- **Quality Presets**: Multiple quality levels are available for both 3GP video (e.g., 176x144 resolution) and MP3 audio (bitrate-based), including 'Auto' modes.
- **Error Handling**: Robust retry mechanisms are implemented for subtitle downloads and FFmpeg conversions, ensuring quality preservation and graceful degradation.
- **Cookie Management**: A robust system validates, manages, and uses `Netscape cookies.txt` format cookies to improve download reliability and bypass YouTube limitations.
- **Resource Limits**: Configurable limits for video duration (120 hours), file size (10GB), FFmpeg threads (4), and concurrent downloads (3) are in place, particularly for cloud environments like Google Cloud Shell.

### System Design Choices
The architecture is designed to be largely stateless, making it suitable for containerized and cloud deployments (e.g., Replit, Render). It relies on ephemeral storage and does not require a persistent database.

## External Dependencies

### Third-Party Services
- **YouTube**: The primary source for video and audio content.

### Required System Tools
- **FFmpeg**: Essential for all video and audio conversions.
- **ImageMagick**: Required for the experimental subtitle burning feature (must be installed on the host system, e.g., via `apt-get` on Render).

### Python Dependencies
- **Flask**: Web framework.
- **yt-dlp**: YouTube downloading library.
- **gunicorn**: WSGI HTTP server for production.

### Environment Variables
- **SESSION_SECRET**: Used for Flask session encryption.

### Cookie Management
- **File Location**: `/tmp/cookies/youtube_cookies.txt` (Netscape format).
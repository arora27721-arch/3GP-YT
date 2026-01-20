import os
import subprocess
import time
import threading
import json
import signal
import sys
import logging
import secrets
import re
import gc
import requests
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, send_file, flash, jsonify
import hashlib
import yt_dlp

# Set environment variables for reduced CPU usage on free tier hosting
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('UV_THREADPOOL_SIZE', '4')

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.environ.get('SESSION_SECRET', secrets.token_hex(32))

@app.after_request
def add_cache_control_headers(response):
    if response.content_type and 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

DOWNLOAD_FOLDER = '/tmp/downloads'
COOKIES_FOLDER = '/tmp/cookies'
STATUS_FILE = '/tmp/conversion_status.json'
COOKIES_FILE = os.path.join(COOKIES_FOLDER, 'youtube_cookies.txt')
os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(COOKIES_FOLDER, exist_ok=True)

def parse_filesize(size_str):
    """Parse filesize string like '500M', '2G' to bytes"""
    if isinstance(size_str, int):
        return size_str
    size_str = str(size_str).strip().upper()
    multipliers = {'K': 1024, 'M': 1024**2, 'G': 1024**3}
    for suffix, multiplier in multipliers.items():
        if size_str.endswith(suffix):
            return int(float(size_str[:-1]) * multiplier)
    return int(size_str)

# Resource limits optimized for Render free tier (512MB RAM, 0.1 CPU, 2GB storage, sleep after 15min inactivity)
# Can be overridden via environment variables for different deployment environments
MAX_VIDEO_DURATION = int(os.environ.get('MAX_VIDEO_DURATION', 86400))  # 0 = no limit (infinite)
DOWNLOAD_TIMEOUT = int(os.environ.get('DOWNLOAD_TIMEOUT', 3600))  # 0 = no timeout (infinite)
FILE_RETENTION_HOURS = int(os.environ.get('FILE_RETENTION_HOURS', 24))  # 24 hours retention
MAX_FILESIZE = parse_filesize(os.environ.get('MAX_FILESIZE', '2G'))  # 2GB for Render free tier

# Playlist storage
PLAYLIST_STATUS_FILE = '/tmp/playlist_status.json'
playlist_status_lock = threading.Lock()

# Split job storage for background processing
SPLIT_STATUS_FILE = '/tmp/split_status.json'
split_status_lock = threading.Lock()

# Search settings storage
SEARCH_SETTINGS_FILE = '/tmp/search_settings.json'
search_settings_lock = threading.Lock()

# Default search settings
DEFAULT_SEARCH_SETTINGS = {
    'duration': 'any',
    'upload_date': 'any',
    'sort_by': 'relevance',
    'content_type': 'video',
    'quality': 'any',
    'subtitles': False,
    'creative_commons': False,
    'live': False,
    '3d': False,
    'vr180': False,
    'purchased': False,
    'results_count': 10,
    'safe_search': 'moderate',
    'region': 'auto',
    'min_views': 0
}

def get_search_settings():
    """Get current search filter settings"""
    with search_settings_lock:
        if os.path.exists(SEARCH_SETTINGS_FILE):
            try:
                with open(SEARCH_SETTINGS_FILE, 'r') as f:
                    settings = json.load(f)
                    merged = DEFAULT_SEARCH_SETTINGS.copy()
                    merged.update(settings)
                    return merged
            except:
                return DEFAULT_SEARCH_SETTINGS.copy()
        return DEFAULT_SEARCH_SETTINGS.copy()

def save_search_settings(settings):
    """Save search filter settings"""
    with search_settings_lock:
        with open(SEARCH_SETTINGS_FILE, 'w') as f:
            json.dump(settings, f)

def build_yt_search_query(query, settings):
    """Build yt-dlp search query with filters applied"""
    results_count = settings.get('results_count', 10)
    
    search_prefix = f"ytsearch{results_count}:"
    
    duration_map = {
        'short': 'short',
        'medium': 'medium', 
        'long': 'long',
        'verylong': 'long'
    }
    
    upload_map = {
        'hour': 'hour',
        'today': 'today',
        'week': 'week',
        'month': 'month',
        'year': 'year'
    }
    
    sort_map = {
        'upload_date': 'date',
        'view_count': 'viewCount',
        'rating': 'rating'
    }
    
    content_type_map = {
        'playlist': 'playlist',
        'channel': 'channel'
    }
    
    filters = []
    
    duration = settings.get('duration', 'any')
    if duration in duration_map:
        filters.append(f"duration={duration_map[duration]}")
    
    upload_date = settings.get('upload_date', 'any')
    if upload_date in upload_map:
        filters.append(f"upload_date={upload_map[upload_date]}")
    
    sort_by = settings.get('sort_by', 'relevance')
    if sort_by in sort_map:
        filters.append(f"sort={sort_map[sort_by]}")
    
    content_type = settings.get('content_type', 'video')
    if content_type in content_type_map:
        filters.append(f"type={content_type_map[content_type]}")
    
    quality = settings.get('quality', 'any')
    if quality == 'hd':
        filters.append("hd=true")
    elif quality == 'fullhd':
        filters.append("hd=true")
    elif quality == '4k':
        filters.append("4k=true")
    
    if settings.get('subtitles'):
        filters.append("subtitles=true")
    if settings.get('creative_commons'):
        filters.append("creative_commons=true")
    if settings.get('live'):
        filters.append("live=true")
    if settings.get('3d'):
        filters.append("3d=true")
    if settings.get('vr180'):
        filters.append("vr180=true")
    if settings.get('purchased'):
        filters.append("purchased=true")
    
    if filters:
        # For YouTube search, we can append filters as keywords
        # Note: yt-dlp doesn't support direct filter strings in ytsearch, 
        # so we add them as search terms which YouTube's engine understands
        filter_query = " ".join(filters)
        search_query = f"{search_prefix}{query} {filter_query}"
    else:
        search_query = f"{search_prefix}{query}"
    
    return search_query, settings

# Active job tracking for keep-alive during processing
active_jobs = {'count': 0, 'last_activity': time.time()}
active_jobs_lock = threading.Lock()

def register_active_job():
    """Register an active job to prevent Render sleep"""
    with active_jobs_lock:
        active_jobs['count'] += 1
        active_jobs['last_activity'] = time.time()
        logger.debug(f"Active jobs: {active_jobs['count']}")

def unregister_active_job():
    """Unregister a completed job"""
    with active_jobs_lock:
        active_jobs['count'] = max(0, active_jobs['count'] - 1)
        active_jobs['last_activity'] = time.time()
        logger.debug(f"Active jobs: {active_jobs['count']}")

def has_active_jobs():
    """Check if there are active jobs"""
    with active_jobs_lock:
        return active_jobs['count'] > 0

def get_split_status(split_id):
    """Get status for a split job"""
    with split_status_lock:
        if os.path.exists(SPLIT_STATUS_FILE):
            try:
                with open(SPLIT_STATUS_FILE, 'r') as f:
                    all_status = json.load(f)
                    return all_status.get(split_id, {})
            except:
                return {}
        return {}

def update_split_status(split_id, updates):
    """Update status for a split job"""
    with split_status_lock:
        all_status = {}
        if os.path.exists(SPLIT_STATUS_FILE):
            try:
                with open(SPLIT_STATUS_FILE, 'r') as f:
                    all_status = json.load(f)
            except:
                pass
        
        if split_id not in all_status:
            all_status[split_id] = {}
        all_status[split_id].update(updates)
        all_status[split_id]['updated_at'] = datetime.now().isoformat()
        
        with open(SPLIT_STATUS_FILE, 'w') as f:
            json.dump(all_status, f)

# YouTube IP block bypass settings
USE_IPV6 = os.environ.get('USE_IPV6', 'false').lower() == 'true'
PROXY_URL = os.environ.get('PROXY_URL', '')  # Optional: http://user:pass@proxy:port
USE_OAUTH = os.environ.get('USE_OAUTH', 'false').lower() == 'true'

# Advanced performance settings - optimized for Render free tier (512MB RAM, 0.1 CPU, 2GB storage)
RATE_LIMIT_BYTES = int(os.environ.get('RATE_LIMIT_BYTES', 0))  # 0 = unlimited
MAX_CONCURRENT_DOWNLOADS = int(os.environ.get('MAX_CONCURRENT_DOWNLOADS', 1))  # 1 concurrent for Render free tier (0.1 CPU)
ENABLE_DISK_SPACE_MONITORING = os.environ.get('ENABLE_DISK_SPACE_MONITORING', 'true').lower() == 'true'
DISK_SPACE_THRESHOLD_MB = int(os.environ.get('DISK_SPACE_THRESHOLD_MB', 50))  # 50MB threshold (2GB storage on Render)

# Subtitle burning settings - optimized for Render free tier (512MB RAM, 0.1 CPU)
SUBTITLE_MAX_DURATION_MINS = int(os.environ.get('SUBTITLE_MAX_DURATION_MINS', 246000))  # 0 = no limit (infinite)
SUBTITLE_MAX_FILESIZE_MB = int(os.environ.get('SUBTITLE_MAX_FILESIZE_MB', 2000))  # 2GB max for subtitle burning
ENABLE_SUBTITLE_BURNING = os.environ.get('ENABLE_SUBTITLE_BURNING', 'true').lower() == 'true'

# FFmpeg performance settings - optimized for Render free tier (0.1 CPU)
FFMPEG_THREADS = 1  # Hardcoded to 1 thread for 0.1 CPU environment

# Playlist processing settings for Render free tier
PLAYLIST_MAX_VIDEOS = int(os.environ.get('PLAYLIST_MAX_VIDEOS', 50))  # Max videos per playlist
PLAYLIST_VIDEO_TIMEOUT = int(os.environ.get('PLAYLIST_VIDEO_TIMEOUT', 862400))  # 0 = no timeout (infinite)

# Quality presets for MP3 audio conversion
# Note: Minimum 128kbps to avoid YouTube download errors with low bitrate
MP3_QUALITY_PRESETS = {
    'medium': {
        'name': '128 kbps (Good Quality - Recommended)',
        'bitrate': '128k',
        'sample_rate': '44100',
        'vbr_quality': '4',
        'description': '~5 MB per 5 min'
    },
    'high': {
        'name': '192 kbps (High Quality)',
        'bitrate': '192k',
        'sample_rate': '44100',
        'vbr_quality': '2',
        'description': '~7 MB per 5 min'
    },
    'veryhigh': {
        'name': '256 kbps (Very High Quality)',
        'bitrate': '256k',
        'sample_rate': '48000',
        'vbr_quality': '0',
        'description': '~9 MB per 5 min'
    },
    'extreme': {
        'name': '320 kbps (Maximum Quality)',
        'bitrate': '320k',
        'sample_rate': '48000',
        'vbr_quality': '0',
        'description': '~12 MB per 5 min'
    }
}

# Quality presets for 3GP video conversion
# Note: Updated with higher audio bitrates for better quality
VIDEO_QUALITY_PRESETS = {
    'ultralow': {
        'name': 'Ultra Low (2G Networks)',
        'video_codec': 'h263',
        'video_bitrate': '40k',
        'video_filter': 'scale=176:144,fps=8',
        'audio_codec': 'amr_nb',
        'audio_bitrate': '12.2k',
        'audio_sample_rate': '8000',
        'gop': '90',
        'description': 'Under 14MB for 30min'
    },
    'low': {
        'name': 'Low (Recommended for Feature Phones)',
        'video_bitrate': '200k',
        'audio_bitrate': '96k',
        'audio_sample_rate': '44100',
        'fps': '12',
        'description': '~3 MB per 5 min'
    },
    'medium': {
        'name': 'Medium (Better Quality)',
        'video_bitrate': '300k',
        'audio_bitrate': '256k',
        'audio_sample_rate': '44100',
        'fps': '15',
        'description': '~4 MB per 5 min'
    },
    'high': {
        'name': 'High (Best Quality)',
        'video_bitrate': '400k',
        'audio_bitrate': '320k',
        'audio_sample_rate': '48000',
        'fps': '18',
        'description': '~5 MB per 5 min'
    }
}

# Detect FFmpeg path (for Render free tier compatibility)
def download_ffmpeg_binary():
    """Auto-download FFmpeg using Python requests (no wget dependency)"""
    try:
        logger.info("FFmpeg not found in expected locations. Attempting auto-download...")

        download_dir = '/tmp/bin'
        os.makedirs(download_dir, exist_ok=True)

        ffmpeg_url = 'https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz'
        download_path = os.path.join(download_dir, 'ffmpeg-static.tar.xz')

        logger.info(f"Downloading FFmpeg from {ffmpeg_url}...")
        
        try:
            response = requests.get(ffmpeg_url, stream=True, timeout=120)
            response.raise_for_status()
            
            with open(download_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            
            logger.info(f"Download successful! Extracting to {download_dir}...")
            subprocess.run(['tar', '-xJf', download_path, '-C', download_dir, '--strip-components=1'],
                         timeout=120, check=True)
            os.remove(download_path)

            ffmpeg_binary = os.path.join(download_dir, 'ffmpeg')
            if os.path.exists(ffmpeg_binary):
                os.chmod(ffmpeg_binary, 0o755)
                logger.info(f"✓ FFmpeg auto-downloaded successfully to: {ffmpeg_binary}")
                return ffmpeg_binary
                
        except requests.RequestException as e:
            logger.warning(f"Download via requests failed: {e}")

        return 'ffmpeg'

    except Exception as e:
        logger.error(f"Auto-download failed: {e}")
        return 'ffmpeg'

def get_ffmpeg_path():
    """Find FFmpeg binary - checks multiple locations, auto-downloads if needed"""
    possible_paths = [
        'bin/ffmpeg',  # Pre-placed binary in repository
        '/opt/bin/ffmpeg',  # Static binary location (from build.sh)
        '/tmp/bin/ffmpeg',  # Auto-downloaded location
        'ffmpeg',  # System PATH
        '/usr/bin/ffmpeg',  # Standard location
        '/usr/local/bin/ffmpeg',  # Alternative location
    ]

    # First pass: try all known locations
    for path in possible_paths:
        try:
            result = subprocess.run([path, '-version'], capture_output=True, timeout=5)
            if result.returncode == 0:
                logger.info(f"✓ FFmpeg found at: {path}")
                return path
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
            continue

    # Not found - try auto-download
    logger.warning("FFmpeg not found in any expected location - attempting auto-download...")
    downloaded_path = download_ffmpeg_binary()

    # Verify the downloaded binary works
    try:
        result = subprocess.run([downloaded_path, '-version'], capture_output=True, timeout=5)
        if result.returncode == 0:
            logger.info(f"✓ Auto-downloaded FFmpeg working at: {downloaded_path}")
            return downloaded_path
    except:
        pass

    logger.error("⚠️ FFmpeg not available - conversions may fail!")
    return 'ffmpeg'  # Last resort fallback

def get_ffprobe_path():
    """Find FFprobe binary - checks multiple locations, uses ffmpeg if needed"""
    possible_paths = [
        'bin/ffprobe',  # Pre-placed binary in repository
        '/opt/bin/ffprobe',  # Static binary location (from build.sh)
        '/tmp/bin/ffprobe',  # Auto-downloaded location
        'ffprobe',  # System PATH
        '/usr/bin/ffprobe',  # Standard location
        '/usr/local/bin/ffprobe',  # Alternative location
    ]

    for path in possible_paths:
        try:
            result = subprocess.run([path, '-version'], capture_output=True, timeout=5)
            if result.returncode == 0:
                logger.info(f"✓ FFprobe found at: {path}")
                return path
        except (subprocess.TimeoutExpired, FileNotFoundError, PermissionError):
            continue

    logger.info("FFprobe not found (not critical - FFmpeg can handle duration detection)")
    return 'ffprobe'  # Fallback to system PATH

FFMPEG_PATH = get_ffmpeg_path()
FFPROBE_PATH = get_ffprobe_path()
logger.info(f"Using FFmpeg: {FFMPEG_PATH}")
logger.info(f"Using FFprobe: {FFPROBE_PATH}")

status_lock = threading.Lock()

def get_status():
    with status_lock:
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

def save_status(status_data):
    with status_lock:
        temp_file = STATUS_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(status_data, f)
        os.replace(temp_file, STATUS_FILE)

def update_status(file_id, updates):
    with status_lock:
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, 'r') as f:
                    status = json.load(f)
            except json.JSONDecodeError:
                status = {}
        else:
            status = {}

        if file_id not in status:
            status[file_id] = {}
        status[file_id].update(updates)

        temp_file = STATUS_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(status, f)
        os.replace(temp_file, STATUS_FILE)

def generate_file_id(url):
    timestamp = str(int(time.time() * 1000))
    combined = f"{url}_{timestamp}"
    return hashlib.md5(combined.encode()).hexdigest()[:16]

def get_playlist_status():
    with playlist_status_lock:
        if os.path.exists(PLAYLIST_STATUS_FILE):
            try:
                with open(PLAYLIST_STATUS_FILE, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

def save_playlist_status(status_data):
    with playlist_status_lock:
        temp_file = PLAYLIST_STATUS_FILE + '.tmp'
        with open(temp_file, 'w') as f:
            json.dump(status_data, f)
        os.replace(temp_file, PLAYLIST_STATUS_FILE)

def update_playlist_status(playlist_id, updates):
    with playlist_status_lock:
        status = get_playlist_status()
        if playlist_id not in status:
            status[playlist_id] = {}
        status[playlist_id].update(updates)
        save_playlist_status(status)

def extract_playlist_info(url):
    """Extract playlist information using yt-dlp"""
    try:
        ydl_opts = {
            'quiet': True,
            'extract_flat': True,
            'force_generic_extractor': False,
            'socket_timeout': 60,  # 0 = no timeout (infinite)
        }
        cookiefile = get_valid_cookiefile()
        if cookiefile:
            ydl_opts['cookiefile'] = cookiefile

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and info.get('_type') == 'playlist':
                videos = []
                for entry in info.get('entries', []):
                    if entry:
                        video_id = entry.get('id', '')
                        videos.append({
                            'id': video_id,
                            'title': entry.get('title', 'Unknown'),
                            'url': f"https://www.youtube.com/watch?v={video_id}",
                            'duration': entry.get('duration', 0)
                        })
                return {
                    'is_playlist': True,
                    'title': info.get('title', 'Playlist'),
                    'video_count': len(videos),
                    'videos': videos
                }
    except Exception as e:
        logger.error(f"Error extracting playlist: {e}")
    return {'is_playlist': False}

def process_playlist(playlist_id, url, output_format, quality, burn_subtitles=False):
    """Background thread function to process all videos in a playlist
    Optimized for Render free tier with proper timeouts and limits.
    """
    register_active_job()
    
    try:
        status = get_playlist_status()
        playlist_data = status.get(playlist_id, {})
        videos = playlist_data.get('videos', {})
        
        # Limit videos for Render free tier
        video_items = list(videos.items())
        if len(video_items) > PLAYLIST_MAX_VIDEOS:
            logger.warning(f"Playlist has {len(video_items)} videos, limiting to {PLAYLIST_MAX_VIDEOS}")
            update_playlist_status(playlist_id, {
                'warning': f'Processing first {PLAYLIST_MAX_VIDEOS} videos only (Render free tier limit)'
            })
            video_items = video_items[:PLAYLIST_MAX_VIDEOS]
        
        processed_count = 0
        
        for video_id, video_info in video_items:
            if video_info.get('status') == 'pending':
                # Check disk space before each video
                if ENABLE_DISK_SPACE_MONITORING:
                    has_space, free_mb = check_disk_space()
                    if not has_space:
                        logger.warning(f"Low disk space ({free_mb:.0f}MB), attempting cleanup...")
                        clean_tmp_immediately()
                        has_space, free_mb = check_disk_space()
                        if not has_space:
                            update_playlist_status(playlist_id, {
                                'status': 'failed',
                                'error': f'Server storage full ({free_mb:.0f}MB free). Processed {processed_count} videos.'
                            })
                            return
                
                # Update status to processing
                video_info['status'] = 'processing'
                videos[video_id] = video_info
                update_playlist_status(playlist_id, {
                    'videos': videos,
                    'current_video': video_info.get('title', 'Unknown'),
                    'progress': f'Processing video {processed_count + 1}/{len(video_items)}'
                })

                file_id = generate_file_id(video_info['url'])
                video_info['file_id'] = file_id

                try:
                    # Process with timeout handling
                    start_time = time.time()
                    download_and_convert(video_info['url'], file_id, output_format, quality, burn_subtitles)
                    elapsed = time.time() - start_time
                    
                    # Check if it took too long (potential stuck)
                    if elapsed > PLAYLIST_VIDEO_TIMEOUT:
                        logger.warning(f"Video {video_id} took {elapsed:.0f}s (limit: {PLAYLIST_VIDEO_TIMEOUT}s)")
                    
                    file_status = get_status().get(file_id, {})
                    if file_status.get('status') == 'completed':
                        video_info['status'] = 'completed'
                        playlist_data['completed_count'] = playlist_data.get('completed_count', 0) + 1
                        processed_count += 1
                    else:
                        video_info['status'] = 'failed'
                        video_info['error'] = file_status.get('progress', 'Unknown error')[:100]
                        playlist_data['failed_count'] = playlist_data.get('failed_count', 0) + 1
                        
                except Exception as video_error:
                    logger.error(f"Error processing video {video_id}: {str(video_error)[:100]}")
                    video_info['status'] = 'failed'
                    video_info['error'] = str(video_error)[:100]
                    playlist_data['failed_count'] = playlist_data.get('failed_count', 0) + 1

                videos[video_id] = video_info
                update_playlist_status(playlist_id, {
                    'videos': videos, 
                    'completed_count': playlist_data.get('completed_count', 0), 
                    'failed_count': playlist_data.get('failed_count', 0)
                })
                
                # Small delay between videos to prevent resource exhaustion
                time.sleep(1)

        update_playlist_status(playlist_id, {
            'status': 'completed',
            'progress': f'Completed! {playlist_data.get("completed_count", 0)} successful, {playlist_data.get("failed_count", 0)} failed'
        })
        logger.info(f"Playlist {playlist_id} completed: {playlist_data.get('completed_count', 0)} successful, {playlist_data.get('failed_count', 0)} failed")
        
    except Exception as e:
        logger.error(f"Playlist processing error: {e}")
        update_playlist_status(playlist_id, {'status': 'failed', 'error': str(e)[:200]})
    finally:
        unregister_active_job()

def check_disk_space():
    """Check available disk space on /tmp (Render has 2GB ephemeral storage limit)"""
    try:
        import shutil
        total, used, free = shutil.disk_usage('/tmp')
        free_mb = free / (1024 * 1024)
        used_mb = used / (1024 * 1024)
        total_mb = total / (1024 * 1024)

        logger.info(f"Disk space: {free_mb:.0f}MB free / {total_mb:.0f}MB total ({used_mb:.0f}MB used)")

        if free_mb < DISK_SPACE_THRESHOLD_MB:
            logger.warning(f"⚠️ Low disk space: {free_mb:.0f}MB free (threshold: {DISK_SPACE_THRESHOLD_MB}MB)")
            return False, free_mb
        return True, free_mb
    except Exception as e:
        logger.error(f"Error checking disk space: {e}")
        return True, 0  # Continue anyway

def clean_tmp_immediately():
    """Emergency cleanup of /tmp when space is low"""
    try:
        import glob

        # Clean downloads folder
        files = glob.glob(os.path.join(DOWNLOAD_FOLDER, '*'))
        deleted = 0
        freed_mb = 0

        for filepath in files:
            try:
                size_mb = os.path.getsize(filepath) / (1024 * 1024)
                os.remove(filepath)
                deleted += 1
                freed_mb += size_mb
            except:
                pass

        logger.info(f"Emergency cleanup: deleted {deleted} files, freed {freed_mb:.1f}MB")
        return freed_mb
    except Exception as e:
        logger.error(f"Emergency cleanup failed: {e}")
        return 0

# Default FFmpeg timeout for Render free tier (0 = no timeout / infinite)
FFMPEG_DEFAULT_TIMEOUT = int(os.environ.get('FFMPEG_DEFAULT_TIMEOUT', 0))  # 0 = no timeout
# Split timeout per part (0 = no timeout / infinite for Render free tier 0.1 CPU)
SPLIT_PART_TIMEOUT = int(os.environ.get('SPLIT_PART_TIMEOUT', 0))  # 0 = no timeout

def run_ffmpeg(args, timeout=None, **kwargs):
    """
    Centralized FFmpeg wrapper that injects CPU/thread limits for Render free tier (0.1 CPU).
    
    Args:
        args: List of FFmpeg arguments (WITHOUT the ffmpeg binary path)
        timeout: Optional timeout in seconds (0 or None = no timeout)
        **kwargs: Additional subprocess.run arguments
    
    Returns:
        subprocess.CompletedProcess result
    """
    # Use default timeout if not specified; 0 means no timeout (infinite)
    if timeout is None:
        timeout = FFMPEG_DEFAULT_TIMEOUT if FFMPEG_DEFAULT_TIMEOUT > 0 else None
    elif timeout == 0:
        timeout = None  # 0 means no timeout
    
    # Build command with thread limiting for CPU-constrained environments (0.1 CPU)
    cmd = [
        FFMPEG_PATH,
        '-threads', str(FFMPEG_THREADS),  # Limit FFmpeg threads for 0.1 CPU
    ] + args
    
    # Also limit OpenMP threads (used by some codecs)
    env = dict(os.environ, OMP_NUM_THREADS=str(FFMPEG_THREADS))
    
    return subprocess.run(cmd, env=env, timeout=timeout, **kwargs)

def get_video_duration(file_path):
    try:
        cmd = [
            FFPROBE_PATH,
            '-v', 'error',
            '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            file_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return float(result.stdout.strip())
        return 0
    except Exception as e:
        logger.warning(f"Could not get video duration: {e}")
        return 0

def has_cookies():
    """Check if cookie file exists and is not empty"""
    return os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 0

def get_valid_cookiefile():
    """
    Centralized cookie health check for background operations.
    Returns the cookie file path if valid and healthy, None otherwise.
    Logs warnings for expired or problematic cookies.
    """
    if not has_cookies():
        return None
    
    try:
        # Check if cookie file exists
        if not os.path.exists(COOKIES_FILE):
            return None
            
        # Basic size check
        if os.path.getsize(COOKIES_FILE) < 10:
            logger.warning("Cookie file is suspiciously small")
            return None

        is_valid, message, health = validate_cookies()
        if not is_valid:
            logger.warning(f"Cookie validation failed: {message}")
            return None
        
        # Warn about cookie health issues
        if health.get('expired_count', 0) > 0:
            logger.warning(f"⚠ {health['expired_count']} expired cookies detected - may cause download failures")
        
        if health.get('expiring_soon', False):
            days_left = (health.get('earliest_expiry', 0) - int(time.time())) // 86400 if health.get('earliest_expiry') else 0
            logger.warning(f"⚠ Some cookies expire in {days_left} days - consider refreshing soon")
        
        if health.get('malformed_lines', 0) > 0:
            logger.info(f"Skipped {health['malformed_lines']} malformed cookie lines")
        
        logger.info(f"Using cookies: {health.get('cookie_count', 0)} YouTube cookies, {len(health.get('session_cookies', []))} session cookies")
        return COOKIES_FILE
        
    except Exception as e:
        logger.error(f"Cookie validation error: {str(e)[:100]}")
        return None

def validate_cookies():
    """
    Enhanced cookie validation with expiry detection, robust parsing, and detailed health reporting.
    Returns: (is_valid, message, health_dict)
    """
    if not has_cookies():
        return False, "No cookies file found", {}

    health = {
        'cookie_count': 0,
        'youtube_cookies': 0,
        'expired_count': 0,
        'earliest_expiry': None,
        'expiring_soon': False,
        'malformed_lines': 0,
        'session_cookies': []
    }
    
    current_time = int(time.time())
    has_youtube_cookies = False

    try:
        with open(COOKIES_FILE, 'r') as f:
            lines = f.readlines()

        for line_num, line in enumerate(lines, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            parts = line.split('\t')
            if len(parts) < 7:
                health['malformed_lines'] += 1
                continue
                
            health['cookie_count'] += 1
            domain = parts[0].lower()
            name = parts[5]
            expiry = parts[4]

            if 'youtube.com' in domain or 'google.com' in domain:
                has_youtube_cookies = True
                health['youtube_cookies'] += 1
            
            # Auth cookie check
            if any(key in name for key in ['PSID', 'LOGIN', 'SAPISID', 'SSID', 'HSID', 'SID', 'APISID']):
                health['session_cookies'].append(name)

            try:
                exp_time = int(expiry)
                if exp_time > 0:
                    if exp_time < current_time:
                        health['expired_count'] += 1
                    else:
                        if health['earliest_expiry'] is None or exp_time < health['earliest_expiry']:
                            health['earliest_expiry'] = exp_time
                        if (exp_time - current_time) < (7 * 86400):
                            health['expiring_soon'] = True
            except:
                pass

        if health['cookie_count'] == 0:
            return False, "No valid cookie lines found", health

        if not has_youtube_cookies:
            return True, f"Found {health['cookie_count']} cookies, but none for YouTube. Downloads might still fail.", health

        if health['expired_count'] > 0:
            return True, f"Detected {health['youtube_cookies']} YouTube cookies ({health['expired_count']} expired).", health

        return True, f"Successfully validated {health['youtube_cookies']} YouTube cookies.", health

    except Exception as e:
        return False, f"Error validating cookies: {str(e)}", health

def download_subtitles(url, file_id, max_retries=3):
    """
    Download English subtitles (manual or auto-generated) from YouTube using yt-dlp.
    Returns the path to the subtitle file if successful, None otherwise.
    Supports both SRT and VTT formats (YouTube primarily uses VTT for auto-captions).
    
    Args:
        url: YouTube video URL
        file_id: Unique identifier for the file
        max_retries: Maximum number of retry attempts (default: 3)
    """
    subtitle_path_srt = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.en.srt')
    subtitle_path_vtt = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.en.vtt')

    for attempt in range(max_retries):
        try:
            # Only clean up on retry (not first attempt)
            if attempt > 0:
                logger.info(f"Retry attempt {attempt + 1}/{max_retries} for subtitle download: {file_id}")
                # Clean up any previous failed attempts
                for path in [subtitle_path_srt, subtitle_path_vtt]:
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except:
                            pass
            else:
                logger.info(f"Attempting to download English subtitles for {file_id}")

            ydl_opts = {
                'writesubtitles': True,
                'writeautomaticsub': True,
                'subtitleslangs': ['en'],
                'skip_download': True,
                'outtmpl': os.path.join(DOWNLOAD_FOLDER, f'{file_id}'),
                'quiet': True,
                'no_warnings': True,
                'socket_timeout': 60,  # 0 = no timeout (infinite)
                'retries': 10,  # More internal retries
                # 2025 anti-throttling/anti-bot measures
                'sleep_interval': 2,
                'max_sleep_interval': 5,
                'http_chunk_size': 2097152,  # 2MB chunks (mimics YouTube app)
            }

            # Add cookies if available for better subtitle access
            cookiefile = get_valid_cookiefile()
            if cookiefile:
                ydl_opts['cookiefile'] = cookiefile

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Check for SRT file first, then VTT (YouTube uses VTT for auto-captions)
            if os.path.exists(subtitle_path_srt) and os.path.getsize(subtitle_path_srt) > 0:
                logger.info(f"✓ English subtitles (SRT) downloaded successfully: {subtitle_path_srt}")
                return subtitle_path_srt
            elif os.path.exists(subtitle_path_vtt) and os.path.getsize(subtitle_path_vtt) > 0:
                logger.info(f"✓ English subtitles (VTT) downloaded, converting to SRT...")
                # Convert VTT to SRT for FFmpeg compatibility
                srt_path = convert_vtt_to_srt(subtitle_path_vtt)
                if srt_path and os.path.exists(srt_path) and os.path.getsize(srt_path) > 0:
                    # Clean up VTT file after successful conversion
                    try:
                        os.remove(subtitle_path_vtt)
                    except:
                        pass
                    logger.info(f"✓ VTT to SRT conversion successful: {srt_path}")
                    return srt_path
                else:
                    # Conversion failed - clean up and retry
                    logger.warning(f"VTT to SRT conversion failed for {file_id} (attempt {attempt + 1}/{max_retries})")
                    try:
                        if os.path.exists(subtitle_path_vtt):
                            os.remove(subtitle_path_vtt)
                    except:
                        pass
                    
                    if attempt < max_retries - 1:
                        wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                        logger.info(f"Waiting {wait_time}s before retry...")
                        time.sleep(wait_time)
                        continue
                    else:
                        return None
            else:
                logger.info(f"No English subtitles available for {file_id} (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.info(f"Retrying subtitle download in {wait_time}s...")
                    time.sleep(wait_time)
                    continue
                else:
                    return None

        except Exception as e:
            error_msg = str(e)
            if '429' in error_msg or 'Too Many Requests' in error_msg:
                logger.warning(f"YouTube rate limit hit when downloading subtitles for {file_id} (attempt {attempt + 1}/{max_retries}). Upload cookies to bypass rate limits.")
            else:
                logger.warning(f"Could not download subtitles for {file_id} (attempt {attempt + 1}/{max_retries}): {error_msg[:150]}")
            
            # Retry with exponential backoff
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.info(f"Waiting {wait_time}s before retry...")
                time.sleep(wait_time)
            else:
                logger.error(f"Failed to download subtitles after {max_retries} attempts")
                return None
    
    return None

def convert_vtt_to_srt(vtt_path):
    """
    Convert VTT subtitle file to SRT format.
    FFmpeg's subtitle filter requires properly formatted SRT.
    Handles timestamp conversion, cue numbering, and VTT metadata removal.
    Maintains subtitle quality and formatting from original VTT.
    """
    try:
        import re

        srt_path = vtt_path.replace('.vtt', '.srt')

        # Read VTT file with proper encoding handling
        try:
            with open(vtt_path, 'r', encoding='utf-8') as vtt_file:
                lines = vtt_file.readlines()
        except UnicodeDecodeError:
            # Fallback to other encodings if UTF-8 fails
            with open(vtt_path, 'r', encoding='latin-1') as vtt_file:
                lines = vtt_file.readlines()

        srt_lines = []
        cue_number = 1
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # Skip WEBVTT header, Kind:, Language:, NOTE, Style:, and empty lines at the start
            if (line.startswith('WEBVTT') or line.startswith('Kind:') or 
                line.startswith('Language:') or line.startswith('NOTE') or 
                line.startswith('Style:') or line.startswith('STYLE') or not line):
                i += 1
                continue

            # Check if this line contains a timestamp (VTT cue timing)
            # VTT format: 00:00:00.000 --> 00:00:05.000 or with positioning
            timestamp_pattern = r'(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})'
            match = re.search(timestamp_pattern, line)

            if match:
                # Add cue number
                srt_lines.append(str(cue_number))
                cue_number += 1

                # Convert timestamps from VTT (.) to SRT (,) format
                start_time = match.group(1).replace('.', ',')
                end_time = match.group(2).replace('.', ',')
                srt_lines.append(f"{start_time} --> {end_time}")

                # Get subtitle text (next lines until blank line)
                i += 1
                subtitle_text = []
                while i < len(lines) and lines[i].strip():
                    text_line = lines[i].strip()
                    # Remove VTT-specific tags while preserving subtitle content quality
                    text_line = re.sub(r'<[^>]+>', '', text_line)  # HTML tags
                    text_line = re.sub(r'</?c[^>]*>', '', text_line)  # <c> color tags
                    text_line = re.sub(r'\{[^}]+\}', '', text_line)  # CSS-like tags
                    # Remove positioning/alignment tags
                    text_line = re.sub(r'align:start|align:middle|align:end', '', text_line)
                    text_line = text_line.strip()
                    if text_line:
                        subtitle_text.append(text_line)
                    i += 1

                # Add subtitle text
                if subtitle_text:
                    srt_lines.extend(subtitle_text)

                # Add blank line between cues
                srt_lines.append('')
            else:
                # Skip cue identifiers or other VTT metadata
                i += 1

        # Validate conversion - ensure we got subtitle content
        if cue_number <= 1:
            logger.error(f"VTT conversion failed - no subtitle cues found in {vtt_path}")
            return None

        # Write SRT file with UTF-8 encoding
        with open(srt_path, 'w', encoding='utf-8') as srt_file:
            srt_file.write('\n'.join(srt_lines))

        # Verify output file
        if not os.path.exists(srt_path) or os.path.getsize(srt_path) == 0:
            logger.error(f"VTT to SRT conversion produced empty file: {srt_path}")
            return None

        logger.info(f"✓ Converted VTT to SRT: {srt_path} ({cue_number-1} cues)")
        return srt_path

    except Exception as e:
        logger.error(f"Failed to convert VTT to SRT: {str(e)[:200]}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()[:500]}")
        return None

def convert_srt_to_ass(srt_path, ass_path, video_width=320, video_height=240):
    """
    Convert SRT subtitles to ASS format with custom styling for feature phones.
    Single-line horizontal scrolling text optimized for feature phone screens (320x240).

    Args:
        srt_path: Path to SRT subtitle file
        ass_path: Path for output ASS file
        video_width: Video width (default 320)
        video_height: Video height (default 240)

    Returns:
        True if successful, False otherwise
    """
    try:
        # Large, bold font for exceptional clarity on 240x320 screen
        fontsize = 16

        # ASS header with clear, readable style for feature phones
        # Font: 16px bold white text with thick black outline and shadow for maximum visibility
        ass_header = f"""[Script Info]
Title: Feature Phone Subtitles
ScriptType: v4.00+
WrapStyle: 0
PlayResX: {video_width}
PlayResY: {video_height}
Collisions: Normal
PlayDepth: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{fontsize},&H00FFFFFF,&H000000FF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,1,3,2,2,5,5,8,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        # Read SRT file
        with open(srt_path, 'r', encoding='utf-8') as f:
            srt_content = f.read()

        # Parse SRT and convert to ASS
        ass_events = []
        blocks = srt_content.strip().split('\n\n')

        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) < 3:
                continue

            # Parse timing (line 1 is number, line 2 is timing)
            timing_line = lines[1]
            if '-->' not in timing_line:
                continue

            start_time, end_time = timing_line.split('-->')
            start_time = start_time.strip().replace(',', '.')
            end_time = end_time.strip().replace(',', '.')

            # Convert SRT time format (HH:MM:SS,mmm) to ASS format (H:MM:SS.cc)
            def srt_to_ass_time(srt_time):
                parts = srt_time.split(':')
                if len(parts) == 3:
                    h, m, s = parts
                    h = str(int(h))  # Remove leading zero
                    return f"{h}:{m}:{s[:5]}"  # Only keep 2 decimal places
                return srt_time

            ass_start = srt_to_ass_time(start_time)
            ass_end = srt_to_ass_time(end_time)

            # Get subtitle text (all lines after timing) - join as single line
            subtitle_text = ' '.join(lines[2:]).replace('\n', ' ')
            # Remove ASS-style line breaks (\\N) and make it single line
            subtitle_text = subtitle_text.replace('\\N', ' ')

            # Create ASS event
            ass_event = f"Dialogue: 0,{ass_start},{ass_end},Default,,0,0,0,,{subtitle_text}"
            ass_events.append(ass_event)

        # Write ASS file
        with open(ass_path, 'w', encoding='utf-8') as f:
            f.write(ass_header)
            f.write('\n'.join(ass_events))

        logger.info(f"✓ Converted SRT to ASS with single-line style: {ass_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to convert SRT to ASS: {str(e)[:200]}")
        return False


def convert_srt_to_dual_line_ass(subtitle_path, file_id):
    """
    Convert SRT subtitle file to dual-line ASS format for burning into video.
    Creates two subtitle lines: one at bottom and one at top.
    
    Args:
        subtitle_path: Path to SRT subtitle file
        file_id: Unique file identifier
    
    Returns:
        Path to ASS file if successful, None otherwise
    """
    try:
        ass_path = os.path.join(DOWNLOAD_FOLDER, f'{file_id}_subs.ass')
        
        # Read SRT file
        with open(subtitle_path, 'r', encoding='utf-8') as f:
            srt_content = f.read()

        # ASS header with two styles: one for bottom (line1), one for top (line2)
        # Font size 14px bold with thick outline and shadow for exceptional clarity on 240x320
        ass_header = """[Script Info]
Title: 3GP Dual-Line Subtitles
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 320
PlayResY: 240
Collisions: Normal

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Line1,Arial,14,&H00FFFFFF,&H000000FF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,1,3,2,2,0,0,0,1
Style: Line2,Arial,14,&H00FFFFFF,&H000000FF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,1,3,2,8,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

        # Parse SRT and create dual-line ASS events
        ass_events = []
        blocks = srt_content.strip().split('\n\n')

        for block in blocks:
            lines = block.strip().split('\n')
            if len(lines) < 3:
                continue

            # Parse timing
            timing_line = lines[1]
            if '-->' not in timing_line:
                continue

            start_time, end_time = timing_line.split('-->')
            start_time = start_time.strip().replace(',', '.')
            end_time = end_time.strip().replace(',', '.')

            # Convert time format
            def srt_to_ass_time(srt_time):
                parts = srt_time.split(':')
                if len(parts) == 3:
                    h, m, s = parts
                    h = str(int(h))
                    return f"{h}:{m}:{s[:5]}"
                return srt_time

            ass_start = srt_to_ass_time(start_time)
            ass_end = srt_to_ass_time(end_time)

            # Get subtitle text and preserve line breaks
            subtitle_text = '\n'.join(lines[2:])
            subtitle_lines = subtitle_text.split('\n')

            # Line 1 (bottom) - always use first line or full text
            line1_text = subtitle_lines[0] if subtitle_lines else subtitle_text
            ass_events.append(f"Dialogue: 0,{ass_start},{ass_end},Line1,,0,0,0,,{line1_text}")

            # Line 2 (top) - only if there's a second line
            if len(subtitle_lines) > 1 and subtitle_lines[1].strip():
                line2_text = subtitle_lines[1]
                ass_events.append(f"Dialogue: 0,{ass_start},{ass_end},Line2,,0,0,0,,{line2_text}")

        # Write ASS file
        with open(ass_path, 'w', encoding='utf-8') as f:
            f.write(ass_header)
            f.write('\n'.join(ass_events))

        logger.info(f"✓ Created ASS subtitle file: {ass_path}")
        return ass_path

    except Exception as e:
        logger.error(f"Failed to convert SRT to ASS: {str(e)[:200]}")
        return None


def burn_subtitles_ffmpeg_3gp(video_path, subtitle_path, output_path, file_id, quality_preset, url):
    """
    Burn subtitles into 3GP video using FFmpeg with ASS format.
    Includes robust retry logic for both SRT→ASS conversion and FFmpeg encoding.
    
    Retry Strategy:
    1. SRT→ASS conversion: Try conversion → Retry conversion → Re-download subs & convert
    2. FFmpeg burning: Try with full compression → Retry with simpler settings (same quality)

    Args:
        video_path: Path to input 3GP video file
        subtitle_path: Path to SRT subtitle file
        output_path: Path for output video with burned subtitles
        file_id: Unique file identifier for status updates
        quality_preset: The same quality preset used in original conversion
        url: YouTube URL for re-downloading subtitles if needed

    Returns:
        True if successful, False otherwise
    """
    try:
        update_status(file_id, {
            'status': 'burning_subtitles',
            'progress': 'Burning subtitles into 3GP... This may take a few minutes.'
        })

        logger.info(f"Starting FFmpeg subtitle burning for {file_id}")

        # SRT→ASS conversion with retry logic
        ass_path = None
        
        # Try 1: Initial conversion
        logger.info(f"Converting SRT to ASS (attempt 1/3)...")
        ass_path = convert_srt_to_dual_line_ass(subtitle_path, file_id)
        
        # Try 2: Retry conversion if failed
        if not ass_path:
            logger.warning(f"SRT→ASS conversion failed, retrying (attempt 2/3)...")
            ass_path = convert_srt_to_dual_line_ass(subtitle_path, file_id)
        
        # Try 3: Re-download subtitles and convert if still failed
        if not ass_path:
            logger.warning(f"SRT→ASS conversion failed again, re-downloading subtitles (attempt 3/3)...")
            update_status(file_id, {
                'progress': 'Subtitle conversion issue, re-downloading subtitles...'
            })
            
            # Re-download subtitles
            new_subtitle_path = download_subtitles(url, file_id)
            if new_subtitle_path:
                ass_path = convert_srt_to_dual_line_ass(new_subtitle_path, file_id)
                # Update subtitle_path to new one for cleanup
                subtitle_path = new_subtitle_path
        
        # Final check: If conversion still failed after all attempts, give up
        if not ass_path:
            logger.error(f"Failed to convert SRT to ASS after 3 attempts")
            return False

        # Calculate derived parameters (same as original conversion)
        video_bitrate_num = int(quality_preset['video_bitrate'].replace('k', ''))
        maxrate = f"{int(video_bitrate_num * 1.25)}k"
        bufsize = f"{int(video_bitrate_num * 2)}k"
        fps_num = int(quality_preset['fps'])
        gop_size = fps_num * 10

        # FFmpeg path escaping for filter syntax
        escaped_ass_path = ass_path.replace('\\', '/').replace(':', '\\:')
        video_filter = f"scale=320:236:force_original_aspect_ratio=increase,crop=320:232,pad=320:240:0:0,setsar=1,subtitles={escaped_ass_path}"

        # Attempt 1: Full compression + user's selected quality
        logger.info(f"Burning subtitles with full compression ({quality_preset['name']})...")
        ffmpeg_cmd = [
            '-i', video_path,
            '-vf', video_filter,
            '-vcodec', 'mpeg4',
            '-r', quality_preset['fps'],
            '-b:v', quality_preset['video_bitrate'],
            '-maxrate', maxrate,
            '-bufsize', bufsize,
            '-qmin', '2',
            '-qmax', '31',
            '-mbd', 'rd',
            '-flags', '+cgop',
            '-sc_threshold', '1000000000',
            '-g', str(gop_size),
            '-trellis', '2',
            '-cmp', '2',
            '-subcmp', '2',
            '-me_method', 'hex',
            '-acodec', 'aac',
            '-ar', quality_preset['audio_sample_rate'],
            '-b:a', quality_preset['audio_bitrate'],
            '-ac', '1',
            '-y',
            output_path
        ]

        result = run_ffmpeg(ffmpeg_cmd, capture_output=True, text=True, timeout=None)

        if result.returncode != 0:
            # Attempt 2: Simpler encoding (same quality, no advanced compression)
            logger.warning(f"Subtitle burning failed with full compression, retrying with simpler settings ({quality_preset['name']})...")
            update_status(file_id, {
                'progress': f'Retrying subtitle burning with simpler settings ({quality_preset["name"]})...'
            })
            
            # Simpler FFmpeg command - removes advanced compression but keeps user's quality
            simple_ffmpeg_cmd = [
                '-i', video_path,
                '-vf', video_filter,
                '-vcodec', 'mpeg4',
                '-r', quality_preset['fps'],
                '-b:v', quality_preset['video_bitrate'],
                '-acodec', 'aac',
                '-ar', quality_preset['audio_sample_rate'],
                '-b:a', quality_preset['audio_bitrate'],
                '-ac', '1',
                '-y',
                output_path
            ]
            
            result = run_ffmpeg(simple_ffmpeg_cmd, capture_output=True, text=True, timeout=None)
            
            if result.returncode != 0:
                error_msg = result.stderr if result.stderr else "Unknown FFmpeg error"
                logger.error(f"FFmpeg subtitle burning failed after retry: {error_msg[:300]}")
                logger.error(f"Full FFmpeg command: {' '.join(simple_ffmpeg_cmd)}")
                
                # Clean up ASS file
                try:
                    os.remove(ass_path)
                except:
                    pass
                
                return False

        # Clean up ASS file after successful burning
        try:
            os.remove(ass_path)
        except:
            pass

        logger.info(f"✓ Subtitles burned successfully with FFmpeg for {file_id}")
        return True

    except Exception as e:
        logger.error(f"FFmpeg subtitle burning failed for {file_id}: {str(e)[:300]}")
        return False

def download_and_convert(url, file_id, output_format='3gp', quality='auto', burn_subtitles=False):
    # Check disk space BEFORE starting download
    if ENABLE_DISK_SPACE_MONITORING:
        has_space, free_mb = check_disk_space()
        if not has_space:
            logger.warning(f"Low disk space ({free_mb:.0f}MB), attempting cleanup...")
            freed_mb = clean_tmp_immediately()
            has_space, free_mb = check_disk_space()
            if not has_space:
                update_status(file_id, {
                    'status': 'failed',
                    'progress': f'Server storage full ({free_mb:.0f}MB free). Please try again in a few minutes after cleanup.'
                })
                return

    file_extension = 'mp3' if output_format == 'mp3' else '3gp'
    format_name = 'MP3 audio' if output_format == 'mp3' else '3GP video'

    # Auto-select quality if not specified
    if quality == 'auto':
        if output_format == 'mp3':
            quality = 'medium'  # 128kbps default for MP3
        else:
            quality = 'low'  # Low quality default for 3GP (feature phone optimized)

    # Validate quality preset
    if output_format == 'mp3':
        if quality not in MP3_QUALITY_PRESETS:
            quality = 'medium'
        quality_preset = MP3_QUALITY_PRESETS[quality]
    else:
        if quality not in VIDEO_QUALITY_PRESETS:
            quality = 'low'
        quality_preset = VIDEO_QUALITY_PRESETS[quality]

    update_status(file_id, {
        'status': 'downloading',
        'progress': f'Downloading from YouTube for {format_name} conversion ({quality_preset["name"]})... (this may take several minutes for long videos)',
        'url': url,
        'timestamp': datetime.now().isoformat()
    })

    output_path = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.{file_extension}')
    temp_video = os.path.join(DOWNLOAD_FOLDER, f'{file_id}_temp.mp4')

    try:
        # Base yt-dlp options (using Python API instead of subprocess)
        # Use flexible format selection to avoid "Requested format not available" errors
        # Priority: smaller files for feature phones, but fallback to any available format
        if output_format == 'mp3':
            # For audio: get best audio, any format
            format_str = 'bestaudio/best'
        else:
            # For video: prefer smaller files but accept anything available
            # Try: low quality video+audio, then medium, then any available
            format_str = 'worst[height<=480]+worstaudio/bestvideo[height<=480]+bestaudio/best[height<=480]/worst+worstaudio/best'

        base_opts = {
            'format': format_str,
            'merge_output_format': 'mp4',
            'outtmpl': temp_video,
            'max_filesize': MAX_FILESIZE,
            'nocheckcertificate': True,
            'retries': 10,
            'fragment_retries': 10,
            'sleep_requests': 2,
            'sleep_interval': 3,
            'max_sleep_interval': 10,
            'concurrent_fragment_downloads': 10,
            'ignoreerrors': False,
            'extractor_retries': 8,
            'socket_timeout': 60,
            'quiet': False,
            'no_warnings': False,
            'logger': logger,
            'dynamic_mpd': True,
        }

        # YouTube IP block bypass: Use IPv6 if enabled (less blocked by YouTube)
        if USE_IPV6:
            base_opts['force_ipv6'] = True
            logger.info(f"Using IPv6 for download (IP block bypass)")
        else:
            base_opts['force_ipv4'] = True

        # Add cookies if available (with health validation)
        cookiefile = get_valid_cookiefile()

        # Download strategies - OPTIMIZED FOR COOKIE-LESS CLOUD HOSTING (Nov 2025)
        # Always use mobile and TV clients to avoid SABR/PO token blocks
        # Web clients are deprioritized as they often fail without PO tokens
        strategies = [
            {
                'name': 'Android (Primary)',
                'opts': {
                    'extractor_args': {'youtube': {
                        'player_client': ['android'],
                        'player_skip': ['configs', 'webpage']
                    }},
                    'http_headers': {
                        'User-Agent': 'com.google.android.youtube/19.45.38 (Linux; U; Android 14; en_US)',
                        'X-YouTube-Client-Name': '3',
                        'X-YouTube-Client-Version': '19.45.38',
                        'Accept-Language': 'en-US,en;q=0.9',
                    }
                }
            },
            {
                'name': 'iOS (Fallback)',
                'opts': {
                    'extractor_args': {'youtube': {
                        'player_client': ['ios'],
                        'player_skip': ['configs', 'webpage']
                    }},
                    'http_headers': {
                        'User-Agent': 'com.google.ios.youtube/19.45.4 (iPhone16,2; U; CPU iOS 18_1_1 like Mac OS X;)',
                        'X-YouTube-Client-Name': '5',
                        'X-YouTube-Client-Version': '19.45.4',
                    }
                }
            },
            {
                'name': 'TV Client (Unrestricted)',
                'opts': {
                    'extractor_args': {'youtube': {
                        'player_client': ['tv'],
                        'player_skip': ['configs', 'webpage']
                    }}
                }
            }
        ]

        # Add Web strategies as last resort if cookies are present
        if cookiefile:
            strategies.extend([
                {
                    'name': 'Web (Cookie-Fallback)',
                    'opts': {
                        'extractor_args': {'youtube': {
                            'player_client': ['web'],
                        }}
                    }
                }
            ])

        if cookiefile:
            # Check if cookiefile actually exists and is not empty
            if os.path.exists(cookiefile) and os.path.getsize(cookiefile) > 0:
                base_opts['cookiefile'] = cookiefile
                logger.info(f"Using validated cookies for download: {file_id}")
            else:
                logger.warning(f"Cookie validation returned non-existent or empty file: {cookiefile}")
        else:
            logger.info(f"No valid cookies available - proceeding with multi-strategy download: {file_id}")

        last_error = None
        download_success = False

        # Custom user agent support
        custom_ua = os.environ.get('CUSTOM_USER_AGENT', '')

        # Multi-level retry strategy for cookie-less cloud hosting:
        # 1. yt-dlp retries each strategy 10 times with internal backoff (sleep_interval 3-10s)
        # 2. Our code tries 7 different strategies with exponential delays between them
        # 3. Total: up to 70 attempts (10 retries × 7 strategies) with smart backoff
        for i, strategy in enumerate(strategies):
            try:
                if i > 0:
                    # Faster retry delays: 1s, 2s, 3s, 5s, 8s, 10s
                    # Quick retries for first strategies, longer delays if still failing
                    if i == 1:
                        delay = 1
                    elif i == 2:
                        delay = 2
                    elif i == 3:
                        delay = 3
                    elif i == 4:
                        delay = 5
                    elif i == 5:
                        delay = 8
                    else:
                        delay = 10

                    update_status(file_id, {
                        'status': 'downloading',
                        'progress': f'Retrying with {strategy["name"]} client... (attempt {i+1}/{len(strategies)}, waiting {delay}s to avoid rate limits)'
                    })
                    time.sleep(delay)

                # Merge strategy options with base options
                ydl_opts = {**base_opts, **strategy['opts']}

                # Override user agent if custom one is provided
                if custom_ua:
                    if 'http_headers' not in ydl_opts:
                        ydl_opts['http_headers'] = {}
                    ydl_opts['http_headers']['User-Agent'] = custom_ua
                    logger.info(f"Using custom user agent for {file_id}")

                # Enhanced browser headers for better mimicking
                if 'http_headers' not in ydl_opts:
                    ydl_opts['http_headers'] = {}

                # Add realistic browser headers if not already present
                headers = ydl_opts['http_headers']
                if 'DNT' not in headers:
                    headers['DNT'] = '1'
                if 'Sec-Fetch-Dest' not in headers:
                    headers['Sec-Fetch-Dest'] = 'document'
                if 'Sec-Fetch-Mode' not in headers:
                    headers['Sec-Fetch-Mode'] = 'navigate'
                if 'Sec-Fetch-Site' not in headers:
                    headers['Sec-Fetch-Site'] = 'none'
                if 'Upgrade-Insecure-Requests' not in headers:
                    headers['Upgrade-Insecure-Requests'] = '1'

                logger.info(f"Attempting download with {strategy['name']} strategy for {file_id}")

                # Use yt-dlp Python API instead of subprocess
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info_dict = ydl.extract_info(url, download=True)

                    # Save video title for better download filenames
                    if info_dict and 'title' in info_dict:
                        video_title = info_dict.get('title', 'video')
                        # Sanitize the title for use as a filename
                        video_title = re.sub(r'[<>:"/\\|?*]', '_', video_title)[:50]  # Limit length
                        update_status(file_id, {'video_title': video_title})

                if os.path.exists(temp_video) and os.path.getsize(temp_video) > 0:
                    logger.info(f"Download successful with {strategy['name']} for {file_id}")
                    download_success = True
                    break
                else:
                    logger.warning(f"{strategy['name']} strategy failed - file not created or empty")

            except yt_dlp.utils.DownloadError as e:
                last_error = str(e)
                error_lower = last_error.lower()

                # Detect temporary vs permanent errors for better retry logic
                is_temporary = False

                # Temporary errors (should retry with different strategy)
                if any(code in last_error for code in ['429', '503', '504']):
                    is_temporary = True
                    logger.warning(f"Temporary error {strategy['name']}: {last_error[:150]}")
                elif 'timeout' in error_lower or 'timed out' in error_lower:
                    is_temporary = True
                    logger.warning(f"Timeout with {strategy['name']}: {last_error[:150]}")

                # Permanent errors (less likely to succeed with retry)
                elif any(code in last_error for code in ['404', '410']):
                    logger.error(f"Permanent error {strategy['name']}: Video not found or deleted")
                    # Don't retry for permanent errors
                    break

                # IP blocking / bot detection
                elif '403' in last_error or 'forbidden' in error_lower or 'bot' in error_lower:
                    logger.warning(f"⚠️ Possible IP block detected with {strategy['name']}: {last_error[:200]}")
                    # Add extra delay when IP blocked
                    time.sleep(5)
                else:
                    logger.error(f"{strategy['name']} download error for {file_id}: {last_error[:200]}")

                continue
            except Exception as e:
                last_error = str(e)
                logger.error(f"{strategy['name']} unexpected error for {file_id}: {last_error[:200]}")
                continue

        if not download_success:
            error_msg = last_error if last_error else "All download strategies failed"
            error_lower = error_msg.lower()

            # Optional cookie suggestion (only for specific errors where cookies definitely help)
            cookies_help = " (Optional: Upload cookies from /cookies page if this persists)" if not has_cookies() else ""

            # Enhanced error detection with better messages
            if '403' in error_msg or 'forbidden' in error_lower:
                raise Exception(f"⚠️ YouTube blocked this request. Tried 7 different download methods. Wait 10-15 minutes before retrying.{cookies_help}")

            if 'po token' in error_lower or 'po_token' in error_lower:
                raise Exception(f"⚠️ YouTube requires PO tokens for this video. Upload cookies from /cookies page to access it.")

            if 'failed to extract' in error_lower or 'failed to parse' in error_lower:
                raise Exception(f"⚠️ Could not extract video information. Error details: {error_msg[:300]}{cookies_help}")

            if 'video player configuration error' in error_lower or 'error 153' in error_lower:
                raise Exception(f"⚠️ Video player error (Error 153). This video has playback restrictions.{cookies_help}")

            if 'bot' in error_lower and ('sign in' in error_lower or 'confirm' in error_lower):
                raise Exception(f"⚠️ YouTube bot detection activated. Wait 10-15 minutes before trying again.{cookies_help}")

            if 'duration' in error_lower:
                if MAX_VIDEO_DURATION is not None:
                    raise Exception(f"Video exceeds {MAX_VIDEO_DURATION/3600:.0f}-hour limit")
                else:
                    raise Exception("Video duration error during download")
            if 'filesize' in error_msg.lower() or 'too large' in error_msg.lower():
                raise Exception(f"Video file too large (server limit: 500MB)")
            if '429' in error_msg or 'too many requests' in error_msg.lower():
                raise Exception(f"YouTube rate limit reached. Wait 10-15 minutes and try again. Server tried multiple download methods.")
            if 'age' in error_msg.lower() and 'restricted' in error_msg.lower():
                raise Exception(f"Video is age-restricted. Upload cookies from /cookies page to access it.")
            if 'private' in error_msg.lower() or 'members-only' in error_msg.lower():
                raise Exception("Video is private or members-only. Cannot download.")
            if 'geo' in error_msg.lower() or 'not available in your country' in error_msg.lower():
                raise Exception("Video is geo-restricted and not available in this region.")
            if 'copyright' in error_msg.lower() or 'removed' in error_msg.lower():
                raise Exception("Video removed due to copyright or deletion.")
            if 'live' in error_msg.lower() and 'stream' in error_msg.lower():
                raise Exception("Cannot download live streams. Try again after the stream ends.")
            if 'sign in' in error_msg.lower() or 'login' in error_msg.lower():
                if has_cookies():
                    raise Exception("YouTube authentication failed. Upload fresh cookies from /cookies page.")
                else:
                    raise Exception(f"YouTube requires sign-in for this video. Upload cookies from /cookies page to access it.")

            # Log full error for debugging
            logger.error(f"All download strategies failed. Full error: {error_msg}")
            raise Exception(f"Download failed after trying 7 different methods. Error: {error_msg[:250]}. Wait 10-15 minutes before retrying.")

        if not os.path.exists(temp_video):
            raise Exception("Download failed: Video file not created")

        duration = get_video_duration(temp_video)
        if MAX_VIDEO_DURATION is not None and duration > MAX_VIDEO_DURATION:
            os.remove(temp_video)
            raise Exception(f"Video is {duration/3600:.1f} hours long. Maximum allowed is {MAX_VIDEO_DURATION/3600:.0f} hours.")

        file_size = os.path.getsize(temp_video)
        file_size_mb = file_size / (1024 * 1024)

        # Check disk space AGAIN before conversion (video might be large)
        if ENABLE_DISK_SPACE_MONITORING:
            has_space, free_mb = check_disk_space()
            if free_mb < (file_size_mb * 1.5):  # Need ~1.5x video size for conversion
                logger.warning(f"Insufficient space for conversion: {free_mb:.0f}MB free, need ~{file_size_mb*1.5:.0f}MB")
                os.remove(temp_video)
                raise Exception(f"Insufficient disk space for conversion. Downloaded video is {file_size_mb:.1f}MB but only {free_mb:.0f}MB free. Try a shorter video.")

        # Download subtitles if requested (will be burned AFTER conversion for 3GP)
        subtitle_file = None
        if burn_subtitles and ENABLE_SUBTITLE_BURNING and output_format != 'mp3':
            # Check resource limits for subtitle burning (Render constraints)
            duration_mins = duration / 60
            # Check subtitle limits if they are set (None = unlimited)
            if SUBTITLE_MAX_DURATION_MINS is not None and duration_mins > SUBTITLE_MAX_DURATION_MINS:
                logger.warning(f"Video too long for subtitle burning: {duration_mins:.1f} mins > {SUBTITLE_MAX_DURATION_MINS} mins limit")
                update_status(file_id, {
                    'progress': f'⚠️ Subtitle burning skipped: Video is {duration_mins:.1f} minutes (limit: {SUBTITLE_MAX_DURATION_MINS} mins for resource constraints)'
                })
            elif SUBTITLE_MAX_FILESIZE_MB is not None and file_size_mb > SUBTITLE_MAX_FILESIZE_MB:
                logger.warning(f"Video too large for subtitle burning: {file_size_mb:.1f}MB > {SUBTITLE_MAX_FILESIZE_MB}MB limit")
                update_status(file_id, {
                    'progress': f'⚠️ Subtitle burning skipped: Video is {file_size_mb:.1f}MB (limit: {SUBTITLE_MAX_FILESIZE_MB}MB for resource constraints)'
                })
            else:
                # Download English subtitles
                subtitle_file = download_subtitles(url, file_id)

                if not subtitle_file:
                    logger.info(f"No English subtitles available, proceeding without subtitle burning")
                    update_status(file_id, {
                        'progress': 'ℹ️ No English subtitles found for this video. Continuing with normal conversion...'
                    })

        est_time = max(1, int(duration / 60))

        if output_format == 'mp3':
            update_status(file_id, {
                'status': 'converting',
                'progress': f'Converting to MP3 audio ({quality_preset["name"]})... Duration: {duration/60:.1f} minutes, Size: {file_size_mb:.1f} MB. Estimated time: {est_time} minute(s).'
            })

            # MP3 conversion with quality preset and ENHANCED compression
            # All presets use stereo (2 channels) as described in the preset descriptions
            convert_cmd = [
                '-threads', '1',  # Limit to 1 thread for 0.1 CPU
                '-i', temp_video,
                '-vn',  # No video
                '-acodec', 'libmp3lame',
                '-ar', quality_preset['sample_rate'],  # Sample rate from preset
                '-b:a', quality_preset['bitrate'],  # Bitrate from preset
                '-ac', '2',  # Stereo for all presets (matches preset descriptions)
                '-q:a', quality_preset['vbr_quality'],  # VBR quality from preset
                '-compression_level', '9',  # Maximum compression (smaller files, slightly slower)
                '-joint_stereo', '1',  # Better stereo compression (5-10% smaller)
                '-y',
                output_path
            ]
        elif quality == 'ultralow':
            update_status(file_id, {
                'status': 'converting',
                'progress': f'Converting to Ultra Low 3GP video... Duration: {duration/60:.1f} minutes, Size: {file_size_mb:.1f} MB. Highly optimized for maximum compression.'
            })

            # ULTRA LOW optimized 3GP conversion
            convert_cmd = [
                '-threads', '1',  # Strictly limit to 1 thread for 0.1 CPU
                '-i', temp_video,
                '-vf', 'scale=176:144,fps=8',
                '-c:v', 'h263',
                '-b:v', '40k',
                '-g', '90',
                '-c:a', 'amr_nb',
                '-b:a', '12.2k',
                '-ac', '1',
                '-ar', '8000',
                '-f', '3gp',
                '-y',
                output_path
            ]
        else:
            update_status(file_id, {
                'status': 'converting',
                'progress': f'Converting to 3GP video ({quality_preset["name"]})... Duration: {duration/60:.1f} minutes, Size: {file_size_mb:.1f} MB. Estimated time: {est_time}-{est_time*2} minutes.'
            })

            # 3GP video conversion with quality preset and ENHANCED compression
            video_bitrate_num = int(quality_preset['video_bitrate'].replace('k', ''))
            maxrate = f"{int(video_bitrate_num * 1.25)}k"  # 25% higher maxrate for better quality
            bufsize = f"{int(video_bitrate_num * 2)}k"  # Buffer size for smooth streaming
            fps_num = int(quality_preset['fps'])
            gop_size = fps_num * 10  # GOP every 10 seconds for better compression

            convert_cmd = [
                '-threads', '1',  # Strictly limit to 1 thread for 0.1 CPU
                '-i', temp_video,
                '-vf','scale=176:144:force_original_aspect_ratio=increase,setsar=1',
                '-vcodec', 'mpeg4',
                '-r', quality_preset['fps'],  # FPS from preset
                '-b:v', quality_preset['video_bitrate'],  # Video bitrate from preset
                '-maxrate', maxrate,  # Dynamic maxrate based on bitrate
                '-bufsize', bufsize,  # Buffer size for smooth streaming
                '-qmin', '2',  # Minimum quantizer for better quality
                '-qmax', '31',  # Maximum quantizer
                '-mbd', 'rd',  # Rate distortion optimization for better compression
                '-flags', '+cgop',  # Closed GOP for better compression
                '-g', str(gop_size),  # GOP size for efficient keyframe placement
                '-trellis', '2',  # Trellis quantization for 10-15% smaller files
                '-cmp', '2',  # Use hadamard comparison (better compression)
                '-subcmp', '2',  # Subpixel comparison for better quality
                '-me_method', 'hex',  # Fast motion estimation with good quality
                '-acodec', 'aac',
                '-ar', quality_preset['audio_sample_rate'],  # Audio sample rate from preset
                '-b:a', quality_preset['audio_bitrate'],  # Audio bitrate from preset
                '-ac', '1',
                '-y',
                output_path
            ]

        dynamic_timeout = None  # No timeout for conversions
        result = run_ffmpeg(convert_cmd, capture_output=True, text=True, timeout=dynamic_timeout)

        if result.returncode != 0:
            error_msg = result.stderr[:300] if result.stderr else "Unknown FFmpeg error"
            logger.error(f"FFmpeg conversion failed for {file_id}: {error_msg}")

            # Retry once with simpler encoding if first attempt fails
            # Uses SAME quality settings but removes advanced compression options
            logger.info(f"Retrying conversion with simpler settings (same quality: {quality_preset['name']}) for {file_id}")

            if output_format == 'mp3':
                # Simpler MP3 conversion - uses same quality preset but removes advanced options
                simple_cmd = [
                    '-threads', str(FFMPEG_THREADS),
                    '-i', temp_video,
                    '-vn',
                    '-acodec', 'libmp3lame',
                    '-ar', quality_preset['sample_rate'],  # Use selected quality
                    '-b:a', quality_preset['bitrate'],  # Use selected quality
                    '-ac', '2',  # Stereo as per preset
                    '-y',
                    output_path
                ]
            else:
                # Simpler 3GP conversion - uses same quality preset but removes advanced options
                simple_cmd = [
                   '-threads', str(FFMPEG_THREADS),
                    '-i', temp_video,
                    '-vf', 'scale=176:144:force_original_aspect_ratio=increase,setsar=1',
                    '-vcodec', 'mpeg4',
                    '-r', quality_preset['fps'],  # Use selected quality
                    '-b:v', quality_preset['video_bitrate'],  # Use selected quality
                    '-acodec', 'aac',
                    '-ar', quality_preset['audio_sample_rate'],  # Use selected quality
                    '-b:a', quality_preset['audio_bitrate'],  # Use selected quality
                    '-ac', '1',
                    '-y',
                    output_path
                ]

            retry_result = run_ffmpeg(simple_cmd, capture_output=True, text=True, timeout=dynamic_timeout)

            if retry_result.returncode != 0:
                # Clean up temp file before raising exception
                if os.path.exists(temp_video):
                    try:
                        os.remove(temp_video)
                    except Exception as e:
                        logger.warning(f"Could not remove temp file {temp_video}: {e}")
                raise Exception(f"Conversion failed after retry: {error_msg}")

        # Clean up temp video after successful conversion
        if os.path.exists(temp_video):
            try:
                os.remove(temp_video)
            except Exception as e:
                logger.warning(f"Could not remove temp file {temp_video}: {e}")

        if not os.path.exists(output_path):
            raise Exception("Conversion failed: Output file not created")

        # Track if subtitles were requested but failed
        subtitle_burn_failed = False
        
        # Burn subtitles into 3GP AFTER conversion if requested
        if subtitle_file and output_format == '3gp':
            output_with_subs = os.path.join(DOWNLOAD_FOLDER, f'{file_id}_with_subs.3gp')

            # Use FFmpeg for 3GP subtitle burning (keeps exact same format/display size)
            if burn_subtitles_ffmpeg_3gp(output_path, subtitle_file, output_with_subs, file_id, quality_preset, url):
                # Replace output_path with subtitle-burned version
                try:
                    os.remove(output_path)
                except Exception as e:
                    logger.warning(f"Could not remove original 3GP: {e}")

                output_path = output_with_subs
                logger.info(f"✓ Subtitles burned into 3GP: {output_path}")

                update_status(file_id, {
                    'status': 'completed',
                    'progress': '✓ Subtitles burned successfully into 3GP!'
                })
            else:
                logger.warning(f"Subtitle burning failed, using 3GP without subtitles")
                subtitle_burn_failed = True
                update_status(file_id, {
                    'progress': '⚠️ SUBTITLE BURNING FAILED - Your video is ready but WITHOUT SUBTITLES. All retry attempts exhausted.'
                })
                # Clean up failed attempt
                if os.path.exists(output_with_subs):
                    try:
                        os.remove(output_with_subs)
                    except:
                        pass

            # Clean up subtitle file
            try:
                os.remove(subtitle_file)
            except:
                pass
        
        # Check if user requested subs but didn't get them
        elif burn_subtitles and ENABLE_SUBTITLE_BURNING and output_format == '3gp' and not subtitle_file:
            subtitle_burn_failed = True

        final_size = os.path.getsize(output_path)
        final_size_mb = final_size / (1024 * 1024)

        # Use correct filename extension based on format
        filename_with_ext = f'{file_id}.{file_extension}'
        
        # Build completion message with subtitle status if relevant
        completion_message = f'Conversion complete! Duration: {duration/60:.1f} min, File size: {final_size_mb:.2f} MB'
        if subtitle_burn_failed:
            completion_message += ' ⚠️ (WITHOUT SUBTITLES - burning failed)'

        update_status(file_id, {
            'status': 'completed',
            'progress': completion_message,
            'filename': filename_with_ext,
            'file_size': final_size,
            'duration': duration,
            'completed_at': datetime.now().isoformat(),
            'quality': quality,
            'output_format': output_format
        })

    except subprocess.TimeoutExpired:
        # This should never happen since we removed all processing timeouts
        logger.error(f"Unexpected timeout processing {file_id}")
        update_status(file_id, {
            'status': 'failed',
            'progress': 'Error: Unexpected processing timeout occurred.'
        })
        if os.path.exists(temp_video):
            try:
                os.remove(temp_video)
            except Exception as e:
                logger.warning(f"Could not remove temp file {temp_video}: {e}")
    except Exception as e:
        logger.error(f"Error processing {file_id}: {str(e)}")
        update_status(file_id, {
            'status': 'failed',
            'progress': f'Error: {str(e)}'
        })
        if os.path.exists(temp_video):
            try:
                os.remove(temp_video)
            except Exception as e:
                logger.warning(f"Could not remove temp file {temp_video}: {e}")

        # Cleanup output if partially created
        if os.path.exists(output_path):
            try:
                os.remove(output_path)
            except Exception as e:
                logger.warning(f"Could not remove output file {output_path}: {e}")

def cleanup_old_files():
    while True:
        try:
            time.sleep(1800)

            cutoff_time = datetime.now() - timedelta(hours=FILE_RETENTION_HOURS)
            deleted_count = 0

            with status_lock:
                if os.path.exists(STATUS_FILE):
                    try:
                        with open(STATUS_FILE, 'r') as f:
                            status = json.load(f)
                    except json.JSONDecodeError:
                        status = {}
                else:
                    status = {}

                for file_id, data in list(status.items()):
                    try:
                        should_delete = False

                        if 'completed_at' in data:
                            completed_time = datetime.fromisoformat(data['completed_at'])
                            if completed_time < cutoff_time:
                                should_delete = True
                        elif 'timestamp' in data:
                            start_time = datetime.fromisoformat(data['timestamp'])
                            if start_time < cutoff_time:
                                if data.get('status') in ['failed', 'unknown', 'downloading', 'converting']:
                                    should_delete = True

                        if should_delete:
                            # Delete all possible file formats if they exist
                            file_path_3gp_subs = os.path.join(DOWNLOAD_FOLDER, f'{file_id}_with_subs.3gp')
                            file_path_3gp = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.3gp')
                            file_path_mp3 = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.mp3')
                            file_path_mp4_subs = os.path.join(DOWNLOAD_FOLDER, f'{file_id}_with_subs.mp4')
                            file_path_mp4 = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.mp4')

                            for file_path in [file_path_3gp_subs, file_path_3gp, file_path_mp3, file_path_mp4_subs, file_path_mp4]:
                                if os.path.exists(file_path):
                                    os.remove(file_path)
                                    deleted_count += 1

                            # Also delete any split parts for this file_id
                            for filename in os.listdir(DOWNLOAD_FOLDER):
                                if filename.startswith(f'{file_id}_part'):
                                    part_path = os.path.join(DOWNLOAD_FOLDER, filename)
                                    try:
                                        os.remove(part_path)
                                        deleted_count += 1
                                    except Exception as e:
                                        logger.warning(f"Could not remove split part {filename}: {e}")

                            del status[file_id]
                    except Exception as e:
                        logger.error(f"Error cleaning file {file_id}: {e}")
                        continue

                temp_file = STATUS_FILE + '.tmp'
                with open(temp_file, 'w') as f:
                    json.dump(status, f)
                os.replace(temp_file, STATUS_FILE)

            for filename in os.listdir(DOWNLOAD_FOLDER):
                try:
                    file_path = os.path.join(DOWNLOAD_FOLDER, filename)
                    if os.path.isfile(file_path):
                        file_time = datetime.fromtimestamp(os.path.getmtime(file_path))
                        if file_time < cutoff_time:
                            os.remove(file_path)
                            deleted_count += 1
                except Exception as e:
                    logger.error(f"Error removing orphan file {filename}: {e}")
                    continue

            if deleted_count > 0:
                logger.info(f"Cleanup completed: Deleted {deleted_count} old files")

        except Exception as e:
            logger.error(f"Cleanup error: {e}")

cleanup_thread = threading.Thread(target=cleanup_old_files, daemon=True)
cleanup_thread.start()

def signal_handler(sig, frame):
    logger.info(f'\nReceived signal {sig}. Gracefully shutting down...')
    logger.info('Cleaning up temporary files...')
    try:
        for filename in os.listdir(DOWNLOAD_FOLDER):
            file_path = os.path.join(DOWNLOAD_FOLDER, filename)
            if os.path.isfile(file_path) and filename.endswith('_temp.mp4'):
                try:
                    os.remove(file_path)
                    logger.info(f'Cleaned up temp file: {filename}')
                except Exception as e:
                    logger.warning(f'Could not remove temp file {filename}: {e}')
    except Exception as e:
        logger.error(f'Error during cleanup: {e}')
    logger.info('Shutdown complete.')
    sys.exit(0)

signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)

@app.route('/')
def index():
    max_hours = "unlimited" if MAX_VIDEO_DURATION is None else MAX_VIDEO_DURATION / 3600
    cookies_status = has_cookies()
    return render_template('index.html', 
                         max_hours=max_hours, 
                         has_cookies=cookies_status,
                         mp3_presets=MP3_QUALITY_PRESETS,
                         video_presets=VIDEO_QUALITY_PRESETS)

@app.route('/mp3')
def mp3_converter():
    max_hours = "unlimited" if MAX_VIDEO_DURATION is None else MAX_VIDEO_DURATION / 3600
    cookies_status = has_cookies()
    return render_template('mp3.html', 
                         max_hours=max_hours, 
                         has_cookies=cookies_status,
                         mp3_presets=MP3_QUALITY_PRESETS)

@app.route('/3gp')
def gp3_converter():
    max_hours = "unlimited" if MAX_VIDEO_DURATION is None else MAX_VIDEO_DURATION / 3600
    cookies_status = has_cookies()
    return render_template('3gp.html', 
                         max_hours=max_hours, 
                         has_cookies=cookies_status,
                         video_presets=VIDEO_QUALITY_PRESETS)

@app.route('/favicon.ico')
def favicon():
    return '', 204

@app.route('/health')
def health():
    return {'status': 'ok', 'service': 'youtube-3gp-converter'}, 200

@app.route('/history')
def history():
    """Show download history of recent conversions (last 48 hours)"""
    status_data = get_status()

    # Filter for files from last 48 hours
    cutoff_time = datetime.now() - timedelta(hours=48)
    history_items = []

    for file_id, data in status_data.items():
        try:
            # Get timestamp
            timestamp_str = data.get('timestamp') or data.get('completed_at')
            if not timestamp_str:
                continue

            file_time = datetime.fromisoformat(timestamp_str)
            if file_time < cutoff_time:
                continue

            # Determine file format - check all possible file types
            file_path_3gp_subs = os.path.join(DOWNLOAD_FOLDER, f'{file_id}_with_subs.3gp')
            file_path_3gp = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.3gp')
            file_path_mp3 = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.mp3')
            file_path_mp4_subs = os.path.join(DOWNLOAD_FOLDER, f'{file_id}_with_subs.mp4')
            file_path_mp4 = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.mp4')

            format_type = None
            file_exists = False
            file_size = 0
            actual_file_path = None

            if os.path.exists(file_path_mp4_subs):
                format_type = 'MP4 (with subtitles)'
                file_exists = True
                file_size = os.path.getsize(file_path_mp4_subs)
                actual_file_path = file_path_mp4_subs
            elif os.path.exists(file_path_mp4):
                format_type = 'MP4'
                file_exists = True
                file_size = os.path.getsize(file_path_mp4)
                actual_file_path = file_path_mp4
            elif os.path.exists(file_path_3gp_subs):
                format_type = '3GP (with subtitles)'
                file_exists = True
                file_size = os.path.getsize(file_path_3gp_subs)
                actual_file_path = file_path_3gp_subs
            elif os.path.exists(file_path_3gp):
                format_type = '3GP'
                file_exists = True
                file_size = os.path.getsize(file_path_3gp)
                actual_file_path = file_path_3gp
            elif os.path.exists(file_path_mp3):
                format_type = 'MP3'
                file_exists = True
                file_size = os.path.getsize(file_path_mp3)
                actual_file_path = file_path_mp3

            # Calculate expiry time
            expiry_time = None
            time_remaining = None
            if data.get('completed_at'):
                completed_at = datetime.fromisoformat(data['completed_at'])
                expiry_time = completed_at + timedelta(hours=FILE_RETENTION_HOURS)
                time_remaining = expiry_time - datetime.now()

            history_items.append({
                'file_id': file_id,
                'title': data.get('video_title', 'Unknown'),
                'url': data.get('url', ''),
                'format': format_type,
                'status': data.get('status', 'unknown'),
                'file_exists': file_exists,
                'file_size': file_size,
                'file_size_mb': f"{file_size / (1024 * 1024):.2f}" if file_size > 0 else "0",
                'timestamp': file_time,
                'expiry_time': expiry_time,
                'time_remaining': time_remaining
            })
        except Exception as e:
            logger.warning(f"Error processing history item {file_id}: {e}")
            continue

    # Sort by timestamp (newest first)
    history_items.sort(key=lambda x: x['timestamp'], reverse=True)

    return render_template('history.html', history_items=history_items)

@app.route('/convert', methods=['POST'])
def convert():
    url = request.form.get('url', '').strip()
    output_format = request.form.get('format', '3gp').strip()

    # Get quality based on selected format
    if output_format == 'mp3':
        quality = request.form.get('mp3_quality', 'auto').strip()
    else:
        quality = request.form.get('video_quality', 'auto').strip()

    # Get subtitle burning option (only applicable for video formats)
    burn_subtitles = request.form.get('burn_subtitles', 'off') == 'on'

    if not url:
        flash('Please enter a YouTube URL')
        return redirect(url_for('index'))

    if 'youtube.com' not in url and 'youtu.be' not in url:
        flash('Please enter a valid YouTube URL')
        return redirect(url_for('index'))

    if output_format not in ['3gp', 'mp3']:
        output_format = '3gp'

    # Check if URL is a playlist
    if 'list=' in url or '/playlist?' in url:
        # For watch?v=...&list=... URLs, convert to playlist-only URL
        if '&list=' in url or '?list=' in url:
            import re
            list_match = re.search(r'[?&]list=([^&]+)', url)
            if list_match:
                playlist_id = list_match.group(1)
                # Convert to playlist URL to force yt-dlp to treat it as a playlist
                playlist_url = f'https://www.youtube.com/playlist?list={playlist_id}'
                playlist_info = extract_playlist_info(playlist_url)
                if playlist_info.get('is_playlist'):
                    return redirect(url_for('playlist_confirm', url=playlist_url, format=output_format, quality=quality, burn_subtitles=burn_subtitles))
        else:
            playlist_info = extract_playlist_info(url)
            if playlist_info.get('is_playlist'):
                return redirect(url_for('playlist_confirm', url=url, format=output_format, quality=quality, burn_subtitles=burn_subtitles))

    file_id = generate_file_id(url)

    thread = threading.Thread(target=download_and_convert, args=(url, file_id, output_format, quality, burn_subtitles))
    thread.daemon = True
    thread.start()

    return redirect(url_for('status', file_id=file_id))

@app.route('/playlist/confirm')
def playlist_confirm():
    url = request.args.get('url', '')
    output_format = request.args.get('format', '3gp')
    quality = request.args.get('quality', 'auto')
    burn_subtitles = request.args.get('burn_subtitles', 'False') == 'True'

    if not url:
        flash('No playlist URL provided')
        return redirect(url_for('index'))

    playlist_info = extract_playlist_info(url)
    if not playlist_info.get('is_playlist'):
        flash('Invalid playlist URL')
        return redirect(url_for('index'))

    return render_template('playlist_confirm.html', 
                          playlist=playlist_info, 
                          url=url, 
                          output_format=output_format,
                          quality=quality,
                          burn_subtitles=burn_subtitles,
                          mp3_presets=MP3_QUALITY_PRESETS,
                          video_presets=VIDEO_QUALITY_PRESETS)

@app.route('/playlist/convert', methods=['POST'])
def playlist_convert():
    url = request.form.get('url', '').strip()
    output_format = request.form.get('format', '3gp').strip()
    quality = request.form.get('quality', 'auto').strip()
    burn_subtitles = request.form.get('burn_subtitles', 'off') == 'on'

    if not url:
        flash('No playlist URL provided')
        return redirect(url_for('index'))

    playlist_info = extract_playlist_info(url)
    if not playlist_info.get('is_playlist'):
        flash('Invalid playlist URL')
        return redirect(url_for('index'))

    playlist_id = generate_file_id(url)

    videos_dict = {}
    videos = playlist_info.get('videos') or []
    if not isinstance(videos, list):
        videos = []
    for idx, video in enumerate(videos, 1):
        videos_dict[video['id']] = {
            'index': idx,
            'title': video['title'],
            'url': video['url'],
            'status': 'pending',
            'file_id': None,
            'error': None
        }

    update_playlist_status(playlist_id, {
        'created_at': datetime.now().isoformat(),
        'playlist_title': playlist_info['title'],
        'url': url,
        'format': output_format,
        'quality': quality,
        'burn_subtitles': burn_subtitles,
        'status': 'processing',
        'total_videos': playlist_info['video_count'],
        'completed_count': 0,
        'failed_count': 0,
        'videos': videos_dict
    })

    thread = threading.Thread(target=process_playlist, args=(playlist_id, url, output_format, quality, burn_subtitles))
    thread.daemon = True
    thread.start()

    return redirect(url_for('playlist_status_page', playlist_id=playlist_id))

@app.route('/playlist/status/<playlist_id>')
def playlist_status_page(playlist_id):
    status_data = get_playlist_status()
    playlist = status_data.get(playlist_id, {})

    if not playlist:
        flash('Playlist not found or expired')
        return redirect(url_for('index'))

    return render_template('playlist_status.html', playlist_id=playlist_id, playlist=playlist)

@app.route('/status/<file_id>')
def status(file_id):
    status_data = get_status()
    file_status = status_data.get(file_id, {'status': 'unknown', 'progress': 'File not found'})

    # Get file info if file exists
    file_info = None
    if file_status.get('status') == 'completed':
        file_path_3gp_subs = os.path.join(DOWNLOAD_FOLDER, f'{file_id}_with_subs.3gp')
        file_path_3gp = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.3gp')
        file_path_mp3 = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.mp3')
        file_path_mp4_subs = os.path.join(DOWNLOAD_FOLDER, f'{file_id}_with_subs.mp4')
        file_path_mp4 = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.mp4')

        if os.path.exists(file_path_mp4_subs):
            file_info = get_file_info(file_path_mp4_subs)
        elif os.path.exists(file_path_mp4):
            file_info = get_file_info(file_path_mp4)
        elif os.path.exists(file_path_3gp_subs):
            file_info = get_file_info(file_path_3gp_subs)
        elif os.path.exists(file_path_3gp):
            file_info = get_file_info(file_path_3gp)
        elif os.path.exists(file_path_mp3):
            file_info = get_file_info(file_path_mp3)

    return render_template('status.html', file_id=file_id, file_status=file_status, file_info=file_info)

@app.route('/download/<file_id>')
def download(file_id):
    # Check for all possible file types
    file_path_mp4_subs = os.path.join(DOWNLOAD_FOLDER, f'{file_id}_with_subs.mp4')
    file_path_mp4 = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.mp4')
    file_path_3gp_subs = os.path.join(DOWNLOAD_FOLDER, f'{file_id}_with_subs.3gp')
    file_path_3gp = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.3gp')
    file_path_mp3 = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.mp3')

    # Get video title from status for better filename
    status_data = get_status()
    file_status = status_data.get(file_id, {})
    video_title = file_status.get('video_title', 'video')

    download_as_job_id = request.args.get('as_job_id') == '1'
    
    if os.path.exists(file_path_mp4_subs):
        dn = f'{file_id}.mp4' if download_as_job_id else f'{video_title}_with_subs.mp4'
        return send_file(file_path_mp4_subs, as_attachment=True, download_name=dn)
    elif os.path.exists(file_path_mp4):
        dn = f'{file_id}.mp4' if download_as_job_id else f'{video_title}.mp4'
        return send_file(file_path_mp4, as_attachment=True, download_name=dn)
    elif os.path.exists(file_path_3gp_subs):
        dn = f'{file_id}.3gp' if download_as_job_id else f'{video_title}_with_subs.3gp'
        return send_file(file_path_3gp_subs, as_attachment=True, download_name=dn)
    elif os.path.exists(file_path_3gp):
        dn = f'{file_id}.3gp' if download_as_job_id else f'{video_title}.3gp'
        return send_file(file_path_3gp, as_attachment=True, download_name=dn)
    elif os.path.exists(file_path_mp3):
        dn = f'{file_id}.mp3' if download_as_job_id else f'{video_title}.mp3'
        return send_file(file_path_mp3, as_attachment=True, download_name=dn)
    else:
        flash('File not found or has been deleted')
        return redirect(url_for('index'))

def get_file_info(file_path):
    """Get file information: size, duration (for video/audio), format"""
    info = {
        'size_bytes': 0,
        'size_mb': 0,
        'size_human': '0 MB',
        'duration_seconds': 0,
        'duration_human': 'Unknown',
        'format': os.path.splitext(file_path)[1].replace('.', '').upper()
    }

    if not os.path.exists(file_path):
        return info

    # Get file size
    size_bytes = os.path.getsize(file_path)
    info['size_bytes'] = size_bytes
    info['size_mb'] = size_bytes / (1024 * 1024)

    # Human readable size
    if size_bytes >= 1024 * 1024:
        info['size_human'] = f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        info['size_human'] = f"{size_bytes / 1024:.2f} KB"

    # Get duration using ffprobe (for video/audio files)
    ext = os.path.splitext(file_path)[1].lower()
    if ext in ['.3gp', '.mp3', '.mp4', '.avi', '.mkv', '.flv']:
        try:
            ffprobe_cmd = [
                get_ffprobe_path(),
                '-v', 'quiet',
                '-show_entries', 'format=duration',
                '-of', 'default=noprint_wrappers=1:nokey=1',
                file_path
            ]
            result = subprocess.run(ffprobe_cmd, capture_output=True, text=True, timeout=None)
            if result.returncode == 0 and result.stdout.strip():
                duration_seconds = float(result.stdout.strip())
                info['duration_seconds'] = int(duration_seconds)

                # Human readable duration
                hours = int(duration_seconds // 3600)
                minutes = int((duration_seconds % 3600) // 60)
                seconds = int(duration_seconds % 60)

                if hours > 0:
                    info['duration_human'] = f"{hours}h {minutes}m {seconds}s"
                elif minutes > 0:
                    info['duration_human'] = f"{minutes}m {seconds}s"
                else:
                    info['duration_human'] = f"{seconds}s"
        except Exception as e:
            logger.warning(f"Could not get duration for {file_path}: {str(e)}")

    return info

def split_media_file_background(file_path, num_parts, file_id, split_id, quality=None, output_format=None):
    """
    Background worker for splitting media files.
    Updates status in real-time for progress tracking.
    Uses the same quality preset as the original conversion.
    """
    register_active_job()
    
    try:
        if not os.path.exists(file_path):
            update_split_status(split_id, {
                'status': 'error',
                'error': 'File not found',
                'completed_parts': 0,
                'total_parts': num_parts
            })
            return

        ext = os.path.splitext(file_path)[1].lower()
        info = get_file_info(file_path)
        total_duration = info['duration_seconds']

        if total_duration == 0:
            update_split_status(split_id, {
                'status': 'error',
                'error': 'Could not determine file duration',
                'completed_parts': 0,
                'total_parts': num_parts
            })
            return

        duration_per_part = total_duration / num_parts

        if duration_per_part < 10:
            num_parts = max(2, int(total_duration / 10))
            duration_per_part = total_duration / num_parts

        # Get the quality preset from the original conversion
        if ext == '.mp3':
            if quality and quality in MP3_QUALITY_PRESETS:
                quality_preset = MP3_QUALITY_PRESETS[quality]
                logger.info(f"[SPLIT {split_id}] Using original quality preset: {quality}")
            else:
                quality_preset = MP3_QUALITY_PRESETS['medium']
                logger.warning(f"[SPLIT {split_id}] Quality '{quality}' not found in status, using default 'medium' for MP3")
        elif ext == '.3gp':
            if quality and quality in VIDEO_QUALITY_PRESETS:
                quality_preset = VIDEO_QUALITY_PRESETS[quality]
                logger.info(f"[SPLIT {split_id}] Using original quality preset: {quality}")
            else:
                quality_preset = VIDEO_QUALITY_PRESETS['low']
                logger.warning(f"[SPLIT {split_id}] Quality '{quality}' not found in status, using default 'low' for 3GP")
        else:
            quality_preset = None
            logger.warning(f"[SPLIT {split_id}] Unknown file extension: {ext}")
        
        if not quality_preset:
            update_split_status(split_id, {
                'status': 'error',
                'error': f'Quality settings not found for: {ext}'
            })
            return

        quality_name = quality_preset['name']

        update_split_status(split_id, {
            'status': 'processing',
            'total_parts': num_parts,
            'completed_parts': 0,
            'current_part': 1,
            'file_id': file_id,
            'format': ext.replace('.', '').upper(),
            'quality': quality_name
        })

        parts = []
        part_num = 1
        start_time = 0

        logger.info(f"[SPLIT {split_id}] Starting split of {file_path} into {num_parts} parts")

        while start_time < total_duration and part_num <= num_parts:
            part_filename = f"{file_id}_part{part_num}{ext}"
            part_path = os.path.join(DOWNLOAD_FOLDER, part_filename)

            if part_num == num_parts:
                part_duration = total_duration - start_time
            else:
                part_duration = duration_per_part

            update_split_status(split_id, {
                'current_part': part_num,
                'current_part_progress': 'encoding'
            })

            if ext == '.mp3':
                # MP3: Re-encode with the SAME quality settings as original conversion
                ffmpeg_cmd = [
                    '-threads', '1',
                    '-ss', str(start_time),
                    '-i', file_path,
                    '-t', str(part_duration),
                    '-c:a', 'libmp3lame',
                    '-ar', quality_preset['sample_rate'],
                    '-b:a', quality_preset['bitrate'],
                    '-q:a', quality_preset['vbr_quality'],
                    '-ac', '2',
                    '-write_xing', '0',
                    '-y',
                    part_path
                ]
            elif ext == '.3gp':
                # 3GP: Re-encode with the SAME quality settings as original conversion
                # Calculate appropriate buffer sizes based on bitrate
                video_bitrate_num = int(quality_preset['video_bitrate'].replace('k', ''))
                maxrate = f"{int(video_bitrate_num * 1.5)}k"
                bufsize = f"{int(video_bitrate_num * 0.75)}k"
                
                ffmpeg_cmd = [
                    '-threads', '1',
                    '-ss', str(start_time),
                    '-i', file_path,
                    '-t', str(part_duration),
                    '-c:v', 'libx264',
                    '-preset', 'ultrafast',
                    '-vf', 'scale=176:144:force_original_aspect_ratio=decrease,pad=176:144:(ow-iw)/2:(oh-ih)/2',
                    '-r', quality_preset['fps'],
                    '-b:v', quality_preset['video_bitrate'],
                    '-maxrate', maxrate,
                    '-bufsize', bufsize,
                    '-c:a', 'aac',
                    '-ar', quality_preset['audio_sample_rate'],
                    '-b:a', quality_preset['audio_bitrate'],
                    '-ac', '1',
                    '-movflags', '+faststart',
                    '-max_muxing_queue_size', '1024',
                    '-f', '3gp',
                    '-y',
                    part_path
                ]
            else:
                update_split_status(split_id, {
                    'status': 'error',
                    'error': f'Unsupported format: {ext}'
                })
                return

            try:
                logger.info(f"[SPLIT {split_id}] Creating part {part_num}/{num_parts} (quality: {quality_name})")
                result = run_ffmpeg(ffmpeg_cmd, capture_output=True, text=True, timeout=SPLIT_PART_TIMEOUT)

                if result.returncode == 0 and os.path.exists(part_path) and os.path.getsize(part_path) > 0:
                    part_info = {
                        'filename': part_filename,
                        'path': part_path,
                        'size': os.path.getsize(part_path),
                        'size_human': f"{os.path.getsize(part_path) / (1024 * 1024):.2f} MB",
                        'part_num': part_num
                    }
                    parts.append(part_info)
                    
                    update_split_status(split_id, {
                        'completed_parts': part_num,
                        'parts': parts
                    })
                    logger.info(f"[SPLIT {split_id}] Completed part {part_num}/{num_parts}")
                else:
                    error_msg = result.stderr[:200] if result.stderr else 'Unknown FFmpeg error'
                    update_split_status(split_id, {
                        'status': 'error',
                        'error': f'Failed to create part {part_num}: {error_msg}',
                        'parts': parts
                    })
                    logger.error(f"[SPLIT {split_id}] Failed part {part_num}: {error_msg}")
                    return

            except subprocess.TimeoutExpired:
                update_split_status(split_id, {
                    'status': 'error',
                    'error': f'Timeout creating part {part_num} (took too long)',
                    'parts': parts
                })
                return
            except Exception as e:
                update_split_status(split_id, {
                    'status': 'error',
                    'error': f'Error on part {part_num}: {str(e)[:100]}',
                    'parts': parts
                })
                return

            start_time += part_duration
            part_num += 1

        # All parts completed - include file_id for download link
        update_split_status(split_id, {
            'status': 'completed',
            'completed_parts': len(parts),
            'parts': parts,
            'file_id': file_id,
            'download_url': f'/split_downloads/{file_id}'
        })
        logger.info(f"[SPLIT {split_id}] All {len(parts)} parts completed successfully")

    except Exception as e:
        update_split_status(split_id, {
            'status': 'error',
            'error': str(e)[:200]
        })
        logger.error(f"[SPLIT {split_id}] Fatal error: {e}")
    finally:
        unregister_active_job()

def start_split_job(file_path, num_parts, file_id, quality=None, output_format=None):
    """Start a background split job and return the split_id for tracking.
    
    Args:
        file_path: Path to the media file to split
        num_parts: Number of parts to split into
        file_id: The file identifier
        quality: Quality preset key (e.g., 'low', 'medium', 'high') - uses same settings as original conversion
        output_format: Format type ('mp3' or '3gp')
    """
    split_id = f"split_{file_id}_{int(time.time())}"
    
    update_split_status(split_id, {
        'status': 'starting',
        'file_id': file_id,
        'total_parts': num_parts,
        'completed_parts': 0,
        'started_at': datetime.now().isoformat()
    })
    
    thread = threading.Thread(
        target=split_media_file_background,
        args=(file_path, num_parts, file_id, split_id, quality, output_format),
        daemon=True
    )
    thread.start()
    
    return split_id

@app.route('/split/<file_id>', methods=['POST'])
def split_file(file_id):
    """Handle file splitting requests - starts background job"""
    file_path_3gp = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.3gp')
    file_path_mp3 = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.mp3')

    file_path = None
    output_format = None
    if os.path.exists(file_path_3gp):
        file_path = file_path_3gp
        output_format = '3gp'
    elif os.path.exists(file_path_mp3):
        file_path = file_path_mp3
        output_format = 'mp3'
    else:
        flash('File not found or has been deleted')
        return redirect(url_for('status', file_id=file_id))

    # Get the original quality from the conversion status
    file_status = get_status().get(file_id, {})
    quality = file_status.get('quality')
    
    try:
        num_parts = int(request.form.get('num_parts', 2))
        if num_parts < 2 or num_parts > 50:
            flash('Number of parts must be between 2 and 50')
            return redirect(url_for('status', file_id=file_id))

        # Start background job with same quality as original conversion
        split_id = start_split_job(file_path, num_parts, file_id, quality=quality, output_format=output_format)
        return redirect(url_for('split_progress', split_id=split_id))

    except ValueError:
        flash('Invalid number of parts. Please enter a valid number.')
        return redirect(url_for('status', file_id=file_id))
    except Exception as e:
        logger.error(f"Error starting split: {str(e)}")
        flash('An error occurred while starting the split.')
        return redirect(url_for('status', file_id=file_id))

@app.route('/split_progress/<split_id>')
def split_progress(split_id):
    """Show split progress page with real-time updates"""
    status = get_split_status(split_id)
    if not status:
        flash('Split job not found')
        return redirect(url_for('split_tool'))
    return render_template('split_progress.html', split_id=split_id, status=status)

@app.route('/split_status_api/<split_id>')
def split_status_api(split_id):
    """JSON API endpoint for split status polling"""
    status = get_split_status(split_id)
    return jsonify(status)

@app.route('/split_downloads/<file_id>')
def split_downloads(file_id):
    """Show download links for all split parts"""
    # Find all parts for this file_id
    parts = []
    for filename in os.listdir(DOWNLOAD_FOLDER):
        if filename.startswith(f'{file_id}_part'):
            part_path = os.path.join(DOWNLOAD_FOLDER, filename)
            # Extract part number
            match = re.search(r'part(\d+)', filename)
            part_num = int(match.group(1)) if match else 0

            parts.append({
                'filename': filename,
                'path': part_path,
                'size': os.path.getsize(part_path),
                'size_human': f"{os.path.getsize(part_path) / (1024 * 1024):.2f} MB",
                'part_num': part_num
            })

    # Sort by part number
    parts.sort(key=lambda x: x['part_num'])

    if not parts:
        flash('No split parts found. File may have expired.')
        return redirect(url_for('index'))

    return render_template('split_downloads.html', file_id=file_id, parts=parts)

@app.route('/download_part/<filename>')
def download_part(filename):
    """Download a specific split part"""
    # Prevent path traversal attacks - only allow safe filenames
    if '..' in filename or '/' in filename or '\\' in filename:
        flash('Invalid filename')
        return redirect(url_for('index'))

    file_path = os.path.join(DOWNLOAD_FOLDER, filename)

    # Double-check the resolved path is still within DOWNLOAD_FOLDER
    if not os.path.abspath(file_path).startswith(os.path.abspath(DOWNLOAD_FOLDER)):
        flash('Invalid file path')
        return redirect(url_for('index'))

    if os.path.exists(file_path):
        return send_file(file_path, as_attachment=True, download_name=filename)
    else:
        flash('File part not found or has been deleted')
        return redirect(url_for('index'))

@app.route('/split_tool', methods=['GET', 'POST'])
def split_tool():
    """Dedicated page for splitting downloaded files"""
    if request.method == 'POST':
        # Handle split request
        file_id = request.form.get('file_id', '').strip()
        num_parts_str = request.form.get('num_parts', '').strip()

        # Validate inputs
        if not file_id:
            flash('Invalid file selected')
            return redirect(url_for('split_tool'))

        # Safely parse num_parts
        try:
            num_parts = int(num_parts_str)
        except (ValueError, TypeError):
            flash('Please enter a valid number of parts (2-50)')
            return redirect(url_for('split_tool'))

        # Validate range
        if num_parts < 2 or num_parts > 50:
            flash('Number of parts must be between 2 and 50')
            return redirect(url_for('split_tool'))

        # Find the file - sanitize file_id to prevent path traversal
        if '..' in file_id or '/' in file_id or '\\' in file_id:
            flash('Invalid file ID')
            return redirect(url_for('split_tool'))

        file_path_3gp = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.3gp')
        file_path_mp3 = os.path.join(DOWNLOAD_FOLDER, f'{file_id}.mp3')

        file_path = None
        output_format = None
        if os.path.exists(file_path_3gp):
            file_path = file_path_3gp
            output_format = '3gp'
        elif os.path.exists(file_path_mp3):
            file_path = file_path_mp3
            output_format = 'mp3'
        else:
            flash('File not found or has been deleted')
            return redirect(url_for('split_tool'))

        # Verify resolved path is within DOWNLOAD_FOLDER
        if not os.path.abspath(file_path).startswith(os.path.abspath(DOWNLOAD_FOLDER)):
            flash('Invalid file path')
            return redirect(url_for('split_tool'))

        # Get the original quality from the conversion status
        file_status = get_status().get(file_id, {})
        quality = file_status.get('quality')

        try:
            # Start background job with same quality as original conversion
            split_id = start_split_job(file_path, num_parts, file_id, quality=quality, output_format=output_format)
            return redirect(url_for('split_progress', split_id=split_id))
        except Exception as e:
            logger.error(f"Error starting split: {str(e)}")
            flash('An error occurred while starting the split.')
            return redirect(url_for('split_tool'))

    # GET request - show available files
    files = []
    for filename in os.listdir(DOWNLOAD_FOLDER):
        # Only show main files, not split parts
        if filename.endswith('.3gp') or filename.endswith('.mp3'):
            if '_part' not in filename:  # Skip already split parts
                file_path = os.path.join(DOWNLOAD_FOLDER, filename)
                file_id = os.path.splitext(filename)[0]

                # Get file info
                info = get_file_info(file_path)

                files.append({
                    'filename': filename,
                    'file_id': file_id,
                    'size': os.path.getsize(file_path),
                    'size_human': info['size_human'],
                    'size_mb': info['size_mb'],
                    'format': info['format'],
                    'duration_human': info['duration_human'],
                    'duration_seconds': info['duration_seconds']
                })

    # Sort by newest first (based on filename which contains timestamp hash)
    files.sort(key=lambda x: x['filename'], reverse=True)

    return render_template('split_tool.html', files=files)

@app.route('/search/settings', methods=['GET', 'POST'])
def search_settings():
    """Search filter settings page"""
    if request.method == 'POST':
        if request.form.get('reset'):
            save_search_settings(DEFAULT_SEARCH_SETTINGS.copy())
            flash('Search settings reset to defaults')
            return redirect(url_for('search_settings'))
        
        settings = {
            'duration': request.form.get('duration', 'any'),
            'upload_date': request.form.get('upload_date', 'any'),
            'sort_by': request.form.get('sort_by', 'relevance'),
            'content_type': request.form.get('content_type', 'video'),
            'quality': request.form.get('quality', 'any'),
            'subtitles': request.form.get('subtitles') == '1',
            'creative_commons': request.form.get('creative_commons') == '1',
            'live': request.form.get('live') == '1',
            '3d': request.form.get('3d') == '1',
            'vr180': request.form.get('vr180') == '1',
            'purchased': request.form.get('purchased') == '1',
            'results_count': int(request.form.get('results_count', 10)),
            'safe_search': request.form.get('safe_search', 'moderate'),
            'region': request.form.get('region', 'auto'),
            'min_views': int(request.form.get('min_views', 0))
        }
        save_search_settings(settings)
        flash('Search settings saved!')
        return redirect(url_for('search_settings'))
    
    settings = get_search_settings()
    return render_template('search_settings.html', settings=settings)

@app.route('/search', methods=['GET', 'POST'])
def search():
    settings = get_search_settings()
    
    show_thumbnails = request.args.get('show_thumbnails', '0') == '1'
    page = int(request.args.get('page', 1))
    results_per_page = 10
    force_format = request.args.get('force_format') or request.form.get('force_format')

    if request.method == 'POST':
        query = request.form.get('query', '').strip()
        # Redirect to GET to put query in URL
        return redirect(url_for('search', query=query, force_format=force_format, show_thumbnails=1 if show_thumbnails else 0))
    else:
        query = request.args.get('query', '').strip()

    if not query:
        return render_template('search.html', results=None, query='', force_format=force_format, show_thumbnails=show_thumbnails, settings=settings, page=page)

    try:
        search_query, settings = build_yt_search_query(query, settings)
        
        # Enhanced yt-dlp options for better search reliability in 2025/2026
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'force_generic_extractor': False,
            'socket_timeout': 30,
            'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'http_headers': {
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Sec-Fetch-Mode': 'navigate',
            },
            # Use specific clients to bypass bot detection
            'extractor_args': {
                'youtube': {
                    'player_client': ['web', 'mweb', 'ios'],
                    'player_skip': ['webpage', 'configs'],
                }
            }
        }
        
        cookiefile = get_valid_cookiefile()
        if cookiefile:
            ydl_opts['cookiefile'] = cookiefile
            logger.info("Using cookiefile for search")

        results = []
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            try:
                search_results = ydl.extract_info(search_query, download=False)
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Search extraction error: {error_msg}")
                if '429' in error_msg or 'too many requests' in error_msg.lower():
                    flash('YouTube search is temporarily limited. Try again in a few minutes.')
                elif '403' in error_msg or 'forbidden' in error_msg.lower():
                    flash('Search blocked by YouTube. Please refresh your cookies.')
                else:
                    flash(f'Search error: {error_msg[:100]}')
                return render_template('search.html', results=None, query=query, force_format=force_format, show_thumbnails=show_thumbnails, settings=settings, page=page)

        if search_results and 'entries' in search_results:
            min_views = settings.get('min_views', 0)
            duration_filter = settings.get('duration', 'any')
            
            # Skip entries from previous pages
            start_index = (page - 1) * results_per_page
            entries = search_results['entries']
            
            for i in range(start_index, len(entries)):
                entry = entries[i]
                if entry and entry.get('id'):
                    duration = entry.get('duration', 0) or 0
                    view_count = entry.get('view_count', 0) or 0
                    
                    if min_views > 0 and view_count < min_views:
                        continue
                    
                    if duration_filter != 'any' and duration:
                        if duration_filter == 'short' and duration >= 240:
                            continue
                        elif duration_filter == 'medium' and (duration < 240 or duration >= 1200):
                            continue
                        elif duration_filter == 'long' and (duration < 1200 or duration >= 3600):
                            continue
                        elif duration_filter == 'verylong' and duration < 3600:
                            continue
                    
                    duration_str = f"{int(duration // 60)}:{int(duration % 60):02d}" if duration else "Unknown"

                    upload_date = entry.get('upload_date', '')
                    upload_date_str = "Unknown"
                    if upload_date and len(upload_date) == 8:
                        try:
                            upload_date_str = f"{upload_date[6:8]}/{upload_date[4:6]}/{upload_date[0:4]}"
                        except:
                            upload_date_str = "Unknown"

                    if view_count:
                        if view_count >= 1000000:
                            view_str = f"{view_count/1000000:.1f}M views"
                        elif view_count >= 1000:
                            view_str = f"{view_count/1000:.1f}K views"
                        else:
                            view_str = f"{view_count} views"
                    else:
                        view_str = "Unknown views"

                    video_id = entry.get('id', '')
                    video_url = entry.get('url', '')

                    if video_url and video_url.startswith('http'):
                        final_url = video_url
                    elif video_id:
                        final_url = f"https://www.youtube.com/watch?v={video_id}"
                    else:
                        logger.warning(f"Could not determine URL for search result: {entry.get('title', 'Unknown')}")
                        continue

                    thumbnail_url = f"https://i.ytimg.com/vi/{video_id}/default.jpg"

                    results.append({
                        'title': entry.get('title', 'Unknown'),
                        'url': final_url,
                        'duration': duration_str,
                        'duration_seconds': duration,
                        'upload_date': upload_date_str,
                        'channel': entry.get('channel', entry.get('uploader', 'Unknown')),
                        'views': view_str,
                        'view_count': view_count,
                        'thumbnail': thumbnail_url,
                    })
                    
                    if len(results) >= results_per_page:
                        break

        if not results:
            if page > 1:
                flash('No more results found.')
                return redirect(url_for('search', query=query, force_format=force_format, page=1, show_thumbnails=1 if show_thumbnails else 0))
            return render_template('search.html', results=[], query=query, force_format=force_format, show_thumbnails=show_thumbnails, settings=settings, page=page)

        return render_template('search.html', results=results, query=query, force_format=force_format, show_thumbnails=show_thumbnails, settings=settings, page=page)
    except Exception as e:
        logger.error(f"General search error: {str(e)}")
        flash(f"An unexpected error occurred: {str(e)}")
        return render_template('search.html', results=None, query=query, force_format=force_format, show_thumbnails=show_thumbnails, settings=settings, page=page)

@app.route('/cookies', methods=['GET', 'POST'])
def cookies_page():
    """
    Enhanced cookie upload route with file size limits, atomic writes, and detailed validation.
    """
    MAX_COOKIE_FILE_SIZE = 2 * 1024 * 1024  # 2MB limit
    
    if request.method == 'POST':
        # Handle cookie deletion
        if request.form.get('delete_cookies') == '1':
            if has_cookies():
                try:
                    os.remove(COOKIES_FILE)
                    flash('Cookies deleted successfully')
                except Exception as e:
                    flash(f'Error deleting cookies: {str(e)}')
            return redirect(url_for('cookies_page'))

        # Handle file upload
        if 'cookies_file' not in request.files:
            flash('No file part')
            return redirect(request.url)
        
        file = request.files['cookies_file']
        if file.filename == '':
            flash('No selected file')
            return redirect(request.url)

        if file:
            try:
                # Read file content to validate size and basic format
                content = file.read(MAX_COOKIE_FILE_SIZE + 1)
                if len(content) > MAX_COOKIE_FILE_SIZE:
                    flash(f'File too large (max {MAX_COOKIE_FILE_SIZE // (1024*1024)}MB)')
                    return redirect(request.url)
                
                # Basic validation
                if not content:
                    flash('The uploaded file is empty.')
                    return redirect(request.url)
                
                # We'll be more lenient here and let validate_cookies handle the format check
                # but we'll check if it looks like a cookies file (at least some tabs or specific structure)
                if b'\t' not in content and b'youtube.com' not in content.lower():
                    flash('Invalid cookie file format. Please upload a standard Netscape cookies.txt file.')
                    return redirect(request.url)

                # Ensure folder exists
                os.makedirs(COOKIES_FOLDER, exist_ok=True)

                # Save file atomically
                temp_path = COOKIES_FILE + '.tmp'
                with open(temp_path, 'wb') as f:
                    f.write(content)
                os.replace(temp_path, COOKIES_FILE)
                
                # Full validation
                is_valid, message, health = validate_cookies()
                if is_valid:
                    flash(f'Cookies uploaded successfully! {message}')
                else:
                    # Check if it's a Netscape format at least, even if it doesn't have youtube.com
                    # (user might be uploading a global cookie file or from a different domain)
                    if health.get('cookie_count', 0) > 0:
                        flash(f'Cookies uploaded. {message}')
                    else:
                        flash(f'Cookies uploaded but validation failed: {message}')
                    
                return redirect(url_for('cookies_page'))
            except Exception as e:
                logger.error(f"Error uploading cookies: {e}")
                flash(f'Error uploading cookies: {str(e)}')
                return redirect(request.url)

    # GET request - show cookie status
    cookies_exist = has_cookies()
    
    if cookies_exist:
        is_valid, message, health = validate_cookies()
    else:
        is_valid, message, health = False, "No cookies uploaded", {}

    return render_template('cookies.html', 
                         cookies_exist=cookies_exist, 
                         is_valid=is_valid, 
                         validation_message=message)

@app.route('/privacy')
def privacy_page():
    return render_template('privacy.html')

@app.route('/contact')
def contact_page():
    return render_template('contact.html')

@app.route('/about-formats')
def about_formats_page():
    return render_template('about_formats.html')


# Health check endpoint for Render and monitoring services
@app.route('/health')
def health_check():
    """Simple health check endpoint - keeps app alive on Render free tier"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'youtube-3gp-converter'
    }), 200

# Keep-alive system for Render free tier (prevents 15-min spin-down)
KEEP_ALIVE_ENABLED = os.environ.get('KEEP_ALIVE_ENABLED', 'true').lower() == 'true'
KEEP_ALIVE_INTERVAL = int(os.environ.get('KEEP_ALIVE_INTERVAL', 300))  # 5 minutes default
PUBLIC_URL = os.environ.get('PUBLIC_URL', os.environ.get('RENDER_EXTERNAL_URL', ''))

def keep_alive_ping():
    """Background thread that pings the app to keep it alive on Render free tier.
    Pings more frequently during active jobs to prevent timeout."""
    while KEEP_ALIVE_ENABLED:
        try:
            # Use shorter interval during active jobs
            if has_active_jobs():
                sleep_time = 60  # Ping every 60 seconds during active work
            else:
                sleep_time = KEEP_ALIVE_INTERVAL
            
            time.sleep(sleep_time)
            
            if PUBLIC_URL:
                health_url = f"{PUBLIC_URL.rstrip('/')}/health"
                try:
                    response = requests.get(health_url, timeout=30)
                    if response.status_code == 200:
                        if has_active_jobs():
                            logger.info(f"Keep-alive (active job): ping successful")
                        else:
                            logger.debug(f"Keep-alive ping successful")
                    else:
                        logger.warning(f"Keep-alive ping returned {response.status_code}")
                except requests.RequestException as e:
                    logger.warning(f"Keep-alive ping failed: {e}")
            else:
                logger.debug("Keep-alive: No PUBLIC_URL configured, skipping external ping")
        except Exception as e:
            logger.error(f"Keep-alive error: {e}")

def start_keep_alive():
    """Start the keep-alive background thread"""
    if KEEP_ALIVE_ENABLED and PUBLIC_URL:
        keep_alive_thread = threading.Thread(target=keep_alive_ping, daemon=True)
        keep_alive_thread.start()
        logger.info(f"Keep-alive started: pinging {PUBLIC_URL} every {KEEP_ALIVE_INTERVAL}s")
    elif KEEP_ALIVE_ENABLED:
        logger.info("Keep-alive enabled but no PUBLIC_URL configured - set PUBLIC_URL or RENDER_EXTERNAL_URL")

if __name__ == '__main__':
    start_keep_alive()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)

# YouTube 3GP/MP3 Converter - Complete Configuration Guide

**Last Updated:** November 10, 2025  
**For:** Feature Phone Video Converter with Subtitle Support

---

## Table of Contents

1. [Subtitle System Configuration](#1-subtitle-system-configuration)
2. [Video Quality Presets](#2-video-quality-presets)
3. [Audio/MP3 Quality Settings](#3-audiomp3-quality-settings)
4. [FFmpeg Encoding Parameters](#4-ffmpeg-encoding-parameters)
5. [YouTube Download System](#5-youtube-download-system)
6. [System Limits & Resource Management](#6-system-limits--resource-management)
7. [Quick Reference & Troubleshooting](#7-quick-reference--troubleshooting)
8. [Code Reference Map](#8-code-reference-map)

---

## 1. Subtitle System Configuration

### 1.1 Overview

Subtitles are burned directly into the video using FFmpeg with ASS (Advanced SubStation Alpha) format. The system creates a black bar at the bottom of the video where subtitles appear.

**Location in code:** Lines 735-893 in `app.py`

### 1.2 Video Scaling & Black Bar

**Current Configuration (Line 846):**
```python
video_filter = f"scale=176:132,pad=176:144:0:0,setsar=1,subtitles={escaped_ass_path}"
```

**Visual Diagram:**
```
┌────────────────────────────┐
│                            │ ← Video content
│      176 x 132 pixels      │   scaled to exact size
│                            │
├────────────────────────────┤ ← Dividing line
│   Subtitles (12px tall)    │ ← Black bar for subs
└────────────────────────────┘
    Total: 176 x 144 pixels
```

**How to Adjust:**

| Change | Modify to | Result |
|--------|-----------|--------|
| Bigger video, smaller sub bar | `scale=176:136,pad=176:144:0:0` | 136px video + 8px subs |
| Smaller video, bigger sub bar | `scale=176:128,pad=176:144:0:0` | 128px video + 16px subs |
| Much bigger video, tiny subs | `scale=176:140,pad=176:144:0:0` | 140px video + 4px subs |
| Maximum video, minimal subs | `scale=176:142,pad=176:144:0:0` | 142px video + 2px subs |

**Formula:**
- Video height + subtitle bar height = 144 pixels
- Current: 132 + 12 = 144
- Subtitle bar height = 144 - video_height

### 1.3 Subtitle Font Styling

**Location:** Lines 781-782 in `app.py`

**Current ASS Style Configuration:**
```
Style: Line1,Arial,6,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,1,1,2,0,0,0,1
```

**Parameter Breakdown:**

| Position | Parameter | Current Value | What it Does | How to Change |
|----------|-----------|---------------|--------------|---------------|
| 3 | Fontsize | `6` | Font size in pixels | 4=tiny, 6=small, 8=medium, 10=large |
| 4 | PrimaryColour | `&H00FFFFFF` | White text | Keep as is |
| 5 | SecondaryColour | `&H000000FF` | Red karaoke color | Not used here |
| 6 | OutlineColour | `&H00000000` | Black outline | Keep as is |
| 7 | BackColour | `&H80000000` | Semi-transparent black background | Keep as is |
| 8 | Bold | `-1` | Bold enabled | -1=yes, 0=no |
| 17 | BorderStyle | `1` | Outline + shadow | 1=outline, 3=box background |
| 18 | Outline | `1` | Outline thickness | 0=none, 1=thin, 2=thick |
| 19 | Shadow | `1` | Shadow offset | 0=none, 1=small, 2=large |
| 20 | Alignment | `2` | Bottom center | See alignment table below |
| 21-23 | MarginL, MarginR, MarginV | `0,0,0` | Left, Right, Bottom margins (pixels) | Increase to add spacing from edges |

**Alignment Codes:**
```
7  8  9    ← Top row
4  5  6    ← Middle row
1  2  3    ← Bottom row (2 = bottom center, used for Line1)
```

**Common Adjustments:**

1. **Make subtitles bigger:**
   ```
   Line 781: Change Arial,6 to Arial,8
   ```

2. **Make subtitles smaller:**
   ```
   Line 781: Change Arial,6 to Arial,4
   ```

3. **Thicker outline for better readability:**
   ```
   Line 781: Change ,1,1,1, to ,2,2,2,
   ```

4. **Move subtitles away from bottom edge (add margin):**
   ```
   Line 781: Change ,0,0,0,1 to ,0,0,3,1
   ```

5. **Remove bold:**
   ```
   Line 781: Change ,-1,0,0,0, to ,0,0,0,0,
   ```

### 1.4 Dual-Line Subtitle Support

The system supports two subtitle lines:
- **Line1:** Bottom line (most important text)
- **Line2:** Top line (appears above Line1 if SRT has 2 lines)

**Line2 Configuration (Line 782):**
```
Style: Line2,Arial,6,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,1,1,8,0,0,0,1
```
- Same as Line1, but alignment is `8` (top center within subtitle area)

### 1.5 PlayRes (Subtitle Resolution)

**Lines 772-773:**
```
PlayResX: 176
PlayResY: 144
```

These **must match** the final video resolution (176×144). Don't change these unless you change the output video size.

---

## 2. Video Quality Presets

**Location:** Lines 114-147 in `app.py`

### 2.1 All Presets Overview

| Preset | Video Bitrate | Audio Bitrate | FPS | File Size (5 min) | Use Case |
|--------|---------------|---------------|-----|-------------------|----------|
| ultralow | 150k | 64k | 10 | ~2 MB | 2G networks, very slow connections |
| low | 200k | 128k | 12 | ~3 MB | **Recommended for feature phones** |
| medium | 300k | 256k | 15 | ~4 MB | Better quality, decent phones |
| high | 400k | 320k | 20 | ~5 MB | Best quality, modern feature phones |

### 2.2 Detailed Preset Specifications

#### ultralow Preset
```python
'ultralow': {
    'name': 'Ultra Low (2G Networks)',
    'video_bitrate': '150k',
    'audio_bitrate': '64k',
    'audio_sample_rate': '44100',
    'fps': '10',
    'description': '~2 MB per 5 min'
}
```
- **When to use:** Very old phones, 2G networks, minimal storage
- **Quality:** Very compressed, noticeable artifacts
- **Performance:** Plays on any device

#### low Preset (DEFAULT)
```python
'low': {
    'name': 'Low (Recommended for Feature Phones)',
    'video_bitrate': '200k',
    'audio_bitrate': '128k',
    'audio_sample_rate': '44100',
    'fps': '12',
    'description': '~3 MB per 5 min'
}
```
- **When to use:** Most feature phones (Nokia, Samsung basic)
- **Quality:** Good balance of size and quality
- **Performance:** Smooth playback on most devices

#### medium Preset
```python
'medium': {
    'name': 'Medium (Better Quality)',
    'video_bitrate': '300k',
    'audio_bitrate': '256k',
    'audio_sample_rate': '44100',
    'fps': '15',
    'description': '~4 MB per 5 min'
}
```
- **When to use:** Better feature phones with more storage
- **Quality:** Clear video, good audio
- **Performance:** May stutter on very old devices

#### high Preset
```python
'high': {
    'name': 'High (Best Quality)',
    'video_bitrate': '400k',
    'audio_bitrate': '320k',
    'audio_sample_rate': '48000',
    'fps': '20',
    'description': '~5 MB per 5 min'
}
```
- **When to use:** Modern feature phones, testing
- **Quality:** Excellent for 176×144 resolution
- **Performance:** Requires capable phone

### 2.3 Creating Custom Presets

To add a new preset, edit lines 114-147:

```python
VIDEO_QUALITY_PRESETS = {
    # ... existing presets ...
    'custom': {
        'name': 'My Custom Preset',
        'video_bitrate': '250k',      # Video quality
        'audio_bitrate': '192k',      # Audio quality
        'audio_sample_rate': '44100', # 44100 or 48000
        'fps': '15',                   # Frames per second
        'description': '~3.5 MB per 5 min'
    }
}
```

**Guidelines:**
- Keep video_bitrate between 100k-500k
- Keep fps between 10-20 (higher = smoother but bigger file)
- audio_sample_rate: use 44100 for most cases, 48000 for high quality
- Total file size ≈ (video_bitrate + audio_bitrate) × duration_seconds / 8

### 2.4 Bitrate Calculations

The system automatically calculates additional parameters:

**Lines 836-840:**
```python
video_bitrate_num = int(quality_preset['video_bitrate'].replace('k', ''))
maxrate = f"{int(video_bitrate_num * 1.25)}k"   # Max bitrate = 1.25x average
bufsize = f"{int(video_bitrate_num * 2)}k"       # Buffer size = 2x average
fps_num = int(quality_preset['fps'])
gop_size = fps_num * 10                           # GOP = 10 seconds of video
```

**Example for "low" preset (200k video bitrate):**
- maxrate = 200 × 1.25 = 250k
- bufsize = 200 × 2 = 400k
- gop_size = 12 × 10 = 120 frames

---

## 3. Audio/MP3 Quality Settings

**Location:** Lines 81-110 in `app.py`

### 3.1 All MP3 Presets

| Preset | Bitrate | Sample Rate | VBR Quality | File Size (5 min) | Use Case |
|--------|---------|-------------|-------------|-------------------|----------|
| medium | 128k | 44100 | 4 | ~5 MB | **Recommended**, good quality |
| high | 192k | 44100 | 2 | ~7 MB | High quality music |
| veryhigh | 256k | 48000 | 0 | ~9 MB | Very high quality |
| extreme | 320k | 48000 | 0 | ~12 MB | Maximum quality |

### 3.2 Understanding Audio Parameters

#### Bitrate
- **What it is:** Amount of data used per second of audio
- **128k:** Good for speech, acceptable music
- **192k:** High quality music, transparent for most listeners
- **256k:** Very high quality, indistinguishable from source
- **320k:** Maximum MP3 quality, overkill for most uses

#### Sample Rate
- **44100 Hz:** CD quality, standard for music
- **48000 Hz:** Professional audio, slightly better for high frequencies
- **When to use 48000:** Only for "veryhigh" and "extreme" presets

#### VBR Quality
- **What it is:** Variable Bit Rate quality level (0-9, lower = better)
- **0:** Maximum quality (~320k effective)
- **2:** High quality (~190k effective)
- **4:** Medium quality (~165k effective)
- **Effect:** Varies bitrate based on audio complexity

### 3.3 File Size Calculations

**Formula:**
```
File size (MB) = (bitrate in kbps × duration in seconds) / 8000
```

**Examples:**
- 5 min at 128k = (128 × 300) / 8000 = 4.8 MB
- 10 min at 192k = (192 × 600) / 8000 = 14.4 MB
- 20 min at 320k = (320 × 1200) / 8000 = 48 MB

### 3.4 Stereo vs Mono

**In code (Line 1358):**
```python
'-ac', '2',  # Stereo for all MP3 presets
```

**For 3GP videos (Line 870):**
```python
'-ac', '1',  # Mono for 3GP (smaller files)
```

**To change MP3 to mono** (smaller files):
- Change `'-ac', '2'` to `'-ac', '1'` on line 1358
- File size reduction: ~40%

---

## 4. FFmpeg Encoding Parameters

**Location:** Lines 848-873 in `app.py`

### 4.1 Complete FFmpeg Command Breakdown

```bash
ffmpeg -i input.3gp \
  -vf "scale=176:132,pad=176:144:0:0,setsar=1,subtitles=file.ass" \
  -vcodec mpeg4 \
  -r 12 \
  -b:v 200k \
  -maxrate 250k \
  -bufsize 400k \
  -qmin 2 \
  -qmax 31 \
  -mbd rd \
  -flags +cgop \
  -sc_threshold 1000000000 \
  -g 120 \
  -trellis 2 \
  -cmp 2 \
  -subcmp 2 \
  -me_method hex \
  -acodec aac \
  -ar 44100 \
  -b:a 128k \
  -ac 1 \
  -y output.3gp
```

### 4.2 Video Filter Explanation

**Line 846:**
```python
video_filter = f"scale=176:132,pad=176:144:0:0,setsar=1,subtitles={escaped_ass_path}"
```

| Filter | What It Does | Parameters |
|--------|--------------|------------|
| `scale=176:132` | Scales video to 176×132 pixels | Width:Height |
| `pad=176:144:0:0` | Adds padding to reach 176×144 | Width:Height:X:Y offset |
| `setsar=1` | Sets Sample Aspect Ratio to 1:1 | Ensures square pixels |
| `subtitles=file.ass` | Burns ASS subtitles into video | Path to subtitle file |

**Alternative filters you might use:**
```python
# No scaling, just add subtitles to 176×144 video
"subtitles={escaped_ass_path}"

# Scale without maintaining aspect ratio (stretches image)
"scale=176:144,setsar=1,subtitles={escaped_ass_path}"

# Maintain aspect ratio with black bars on sides
"scale=176:144:force_original_aspect_ratio=decrease,pad=176:144:(ow-iw)/2:(oh-ih)/2,setsar=1"
```

### 4.3 Video Codec Parameters

| Parameter | Value | What It Does | Effect |
|-----------|-------|--------------|--------|
| `-vcodec mpeg4` | mpeg4 | Video codec for 3GP | Required for feature phone compatibility |
| `-r 12` | 12 fps | Frame rate | Higher = smoother, larger file |
| `-b:v 200k` | 200 kbps | Average video bitrate | Higher = better quality |
| `-maxrate 250k` | 250 kbps | Maximum bitrate (1.25x avg) | Prevents quality spikes |
| `-bufsize 400k` | 400 kbps | Buffer size (2x avg) | Smooths bitrate variations |

### 4.4 Quality Control Parameters

| Parameter | Value | What It Does | Range | Effect |
|-----------|-------|--------------|-------|--------|
| `-qmin 2` | 2 | Minimum quantizer | 1-31 | Lower = better quality (2 is high) |
| `-qmax 31` | 31 | Maximum quantizer | 1-31 | Higher = more compression (31 is max) |

**Quantizer explained:**
- Lower number = higher quality, larger file
- Higher number = lower quality, smaller file
- qmin=2 ensures video never gets TOO high quality (wastes space)
- qmax=31 allows very low quality when needed (saves space)

### 4.5 Advanced Encoding Flags

| Parameter | Value | What It Does | Performance Impact |
|-----------|-------|--------------|-------------------|
| `-mbd rd` | rd | Macroblock decision = rate-distortion | Slower encoding, better quality |
| `-flags +cgop` | enabled | Closed GOP | Better seeking, required for 3GP |
| `-sc_threshold 1000000000` | billion | Disable scene change detection | **REQUIRED with +cgop** |
| `-g 120` | 120 | GOP size (10 seconds at 12 fps) | Larger = better compression, worse seeking |
| `-trellis 2` | 2 | Trellis quantization | Better quality, slower encoding |
| `-cmp 2` | 2 | Comparison function for motion estimation | SATD (good quality) |
| `-subcmp 2` | 2 | Sub-pixel motion estimation comparison | SATD (good quality) |
| `-me_method hex` | hex | Motion estimation method | Fast and efficient |

### 4.6 Critical Fix: sc_threshold

**Line 861:**
```python
'-sc_threshold', '1000000000',
```

**Why this is required:**
- FFmpeg's mpeg4 encoder doesn't support scene change detection with closed GOP
- Without this, subtitle burning FAILS with error: "closed gop with scene change detection are not supported yet"
- Setting to 1 billion effectively disables scene detection
- **DO NOT REMOVE THIS LINE** if using `-flags +cgop`

### 4.7 Audio Encoding Parameters

| Parameter | Value | What It Does |
|-----------|-------|--------------|
| `-acodec aac` | aac | Audio codec (AAC for 3GP) |
| `-ar 44100` | 44100 Hz | Audio sample rate |
| `-b:a 128k` | 128 kbps | Audio bitrate |
| `-ac 1` | mono | Audio channels (1=mono, 2=stereo) |

**For MP3 conversion:**
```python
'-acodec', 'libmp3lame',  # MP3 encoder
'-ar', quality_preset['sample_rate'],
'-b:a', quality_preset['bitrate'],
'-ac', '2',  # Stereo
'-q:a', quality_preset['vbr_quality'],  # VBR quality
```

### 4.8 Motion Estimation Methods

Current: `hex` (hexagonal search)

**Available options:**
| Method | Speed | Quality | When to Use |
|--------|-------|---------|-------------|
| `dia` | Fastest | Lowest | Not recommended |
| `hex` | Fast | Good | **Current (recommended)** |
| `umh` | Slow | Better | If encoding time isn't critical |
| `esa` | Very slow | Best | Only for archival quality |

**To change** (Line 866):
```python
'-me_method', 'umh',  # Better quality, slower encoding
```

---

## 5. YouTube Download System

### 5.1 Download Strategies

The app tries multiple strategies in order until one succeeds.

**Location:** Lines 964-1064 in `app.py`

**Strategy Order:**
1. **Android Test Suite** (Most Reliable)
2. **web_safari client**
3. **ios client**
4. **android client**
5. **Default strategy**
6. **With cookies** (if cookies file exists)
7. **OAuth** (if enabled)

### 5.2 Format Selection

**For MP3 (Line 948):**
```python
format_str = 'bestaudio/best'
```
- Gets best available audio quality
- Fallback to any format if audio-only not available

**For 3GP Video (Line 952):**
```python
format_str = 'worst[height<=480]+worstaudio/bestvideo[height<=480]+bestaudio/best[height<=480]/worst+worstaudio/best'
```

**What this means:**
1. Try: Low quality video (≤480p) + worst audio
2. Fallback: Best video (≤480p) + best audio
3. Fallback: Any video ≤480p
4. Fallback: Any worst quality + worst audio
5. Last resort: Just get anything available

**Why use "worst"?**
- Feature phones don't need high resolution
- Smaller download = faster conversion
- Final output is only 176×144 anyway

### 5.3 Cookie System

**Location:** Lines 38, 1050-1056

**Cookie file path:**
```python
COOKIES_FILE = '/tmp/cookies/youtube_cookies.txt'
```

**How to use cookies:**
1. Export YouTube cookies from browser (Netscape format)
2. Upload via `/cookies` route
3. Cookies enable:
   - Access to age-restricted videos
   - Access to private/unlisted videos (if logged in)
   - Bypass some bot detection

**Check cookie status:**
```python
def has_cookies():
    return os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 0
```

### 5.4 Download Options

**Base options (Lines 954-968):**
```python
base_opts = {
    'format': format_str,
    'merge_output_format': 'mp4',
    'outtmpl': temp_video,
    'max_filesize': MAX_FILESIZE,  # Default: 1000MB
    'nocheckcertificate': True,
    'retries': 10,
    'fragment_retries': 10,
    'ignoreerrors': False,
    'noplaylist': True,
    'quiet': False,
    'no_warnings': False,
    'geo_bypass': True,
}
```

**Optional features:**
- **IPv6:** Set `USE_IPV6=true` env var
- **Proxy:** Set `PROXY_URL=http://proxy:port` env var
- **Rate limiting:** Set `RATE_LIMIT_BYTES=500000` (500 KB/s)

### 5.5 Troubleshooting Downloads

**Common errors and solutions:**

| Error | Cause | Solution |
|-------|-------|----------|
| "Requested format not available" | Video doesn't have requested format | Uses fallback formats automatically |
| "This video is not available" | Geo-restricted or private | Upload cookies file |
| "HTTP Error 403" | IP blocked by YouTube | Enable IPv6 or use proxy |
| "Sign in to confirm your age" | Age-restricted | Upload cookies from logged-in session |

---

## 6. System Limits & Resource Management

### 6.1 File Size Limits

**Line 57:**
```python
MAX_FILESIZE = parse_filesize(os.environ.get('MAX_FILESIZE', '1000M'))
```

**Default:** 1000 MB (1 GB)

**How to change:**
- Set environment variable: `MAX_FILESIZE=500M`
- Or edit line 57 directly

**Effect:**
- Videos larger than this are rejected during download
- Prevents disk space issues
- Adjust based on available storage

### 6.2 Duration Limits

**Line 53:**
```python
MAX_VIDEO_DURATION = None  # Unlimited
```

**Default:** Unlimited

**To add duration limit:**
```python
MAX_VIDEO_DURATION = 3600  # 1 hour in seconds
```

**For subtitle burning only (Line 75):**
```python
SUBTITLE_MAX_DURATION_MINS = 45  # minutes
```
- Videos longer than 45 minutes skip subtitle burning
- Conversion still happens, just without subs
- Prevents excessive processing time

### 6.3 Disk Space Monitoring

**Lines 71-72:**
```python
ENABLE_DISK_SPACE_MONITORING = True
DISK_SPACE_THRESHOLD_MB = 1500  # Alert when < 1.5GB free
```

**How it works:**
1. Checks disk space before every download
2. If below threshold, triggers cleanup
3. If still below threshold after cleanup, rejects download

**Check code (Lines 459-478):**
```python
def check_disk_space():
    stat = os.statvfs('/tmp')
    free_mb = (stat.f_bavail * stat.f_frsize) / (1024 * 1024)
    total_mb = (stat.f_blocks * stat.f_frsize) / (1024 * 1024)
    return free_mb >= DISK_SPACE_THRESHOLD_MB, free_mb
```

### 6.4 File Cleanup & Retention

**Line 56:**
```python
FILE_RETENTION_HOURS = 6  # Keep files for 6 hours
```

**Cleanup behavior:**
- Automatic cleanup runs periodically
- Removes files older than retention period
- Cleanup also triggered when disk space is low

**Cleanup code (Lines 480-520):**
```python
def clean_old_files():
    # Remove files older than FILE_RETENTION_HOURS
    cutoff_time = time.time() - (FILE_RETENTION_HOURS * 3600)
    for filename in os.listdir(DOWNLOAD_FOLDER):
        filepath = os.path.join(DOWNLOAD_FOLDER, filename)
        if os.path.getmtime(filepath) < cutoff_time:
            os.remove(filepath)
```

### 6.5 Subtitle Burning Limits

**Lines 75-77:**
```python
SUBTITLE_MAX_DURATION_MINS = 45   # Max 45 minutes
SUBTITLE_MAX_FILESIZE_MB = 500     # Max 500MB
ENABLE_SUBTITLE_BURNING = True
```

**Resource constraints:**
- Subtitle burning is CPU-intensive
- Long videos take too long to process
- Large files may cause memory issues

**Validation code (Lines 1320-1330):**
```python
if burn_subtitles and ENABLE_SUBTITLE_BURNING:
    duration_mins = duration / 60
    if duration_mins > SUBTITLE_MAX_DURATION_MINS:
        logger.warning(f"Video too long for subtitle burning")
        # Skip subtitle burning
    elif file_size_mb > SUBTITLE_MAX_FILESIZE_MB:
        logger.warning(f"Video too large for subtitle burning")
        # Skip subtitle burning
```

### 6.6 Concurrent Downloads

**Line 70:**
```python
MAX_CONCURRENT_DOWNLOADS = 1
```

**Default:** 1 (one download at a time)

**To allow multiple simultaneous downloads:**
```python
MAX_CONCURRENT_DOWNLOADS = 3  # Allow 3 at once
```

**Trade-offs:**
- More concurrent = faster overall
- More concurrent = higher resource usage
- Recommended: Keep at 1 for free hosting

---

## 7. Quick Reference & Troubleshooting

### 7.1 Common Tweaks Cheat Sheet

| Want to... | Change this | Line | Example |
|------------|-------------|------|---------|
| Make subtitles bigger | Font size | 781 | `Arial,6` → `Arial,8` |
| Make subtitles smaller | Font size | 781 | `Arial,6` → `Arial,4` |
| Add space between video and subs | Vertical margin | 781 | `,0,0,0,1` → `,0,0,3,1` |
| More space for subs | Video height | 846 | `scale=176:132` → `scale=176:128` |
| Less space for subs | Video height | 846 | `scale=176:132` → `scale=176:136` |
| Better video quality | video_bitrate | 125 | `'200k'` → `'300k'` |
| Smaller file size | video_bitrate | 125 | `'200k'` → `'150k'` |
| Smoother video | fps | 128 | `'12'` → `'15'` |
| Better audio | audio_bitrate | 126 | `'128k'` → `'192k'` |
| Stereo MP3 → Mono | Audio channels | 1358 | `'-ac', '2'` → `'-ac', '1'` |
| Longer file retention | Retention hours | 56 | `6` → `12` |
| Allow longer videos | Subtitle max duration | 75 | `45` → `90` |

### 7.2 Subtitle Positioning Examples

**Current (12px black bar):**
```python
video_filter = f"scale=176:132,pad=176:144:0:0,setsar=1,subtitles={escaped_ass_path}"
```

**Minimal black bar (4px):**
```python
video_filter = f"scale=176:140,pad=176:144:0:0,setsar=1,subtitles={escaped_ass_path}"
```

**Large black bar (20px):**
```python
video_filter = f"scale=176:124,pad=176:144:0:0,setsar=1,subtitles={escaped_ass_path}"
```

**Subs at top instead of bottom:**
```python
video_filter = f"scale=176:132,pad=176:144:0:12,setsar=1,subtitles={escaped_ass_path}"
# Last parameter: 0:12 means video starts at Y=12, creating top bar
```

### 7.3 Troubleshooting Guide

#### Subtitle Problems

**Problem: Subtitles don't show**
- Check line 861: Must have `-sc_threshold 1000000000`
- Check line 846: Filter syntax must be correct
- Check logs: Look for "FFmpeg subtitle burning failed"

**Problem: Subtitles too small to read**
- Line 781: Increase font size (6 → 8 or 10)
- Line 781: Increase outline (,1,1,1, → ,2,2,2,)
- Line 846: Increase black bar size (132 → 128)

**Problem: Subtitles too close to video edge**
- Line 781: Add vertical margin (,0,0,0,1 → ,0,0,2,1)
- This creates space between video and subtitle text

#### Video Quality Problems

**Problem: Video too blurry**
- Lines 114-147: Use higher quality preset
- Line 125: Increase video_bitrate
- Line 857-858: Tighten qmin/qmax range

**Problem: File too large**
- Lines 114-147: Use lower quality preset
- Line 128: Decrease fps
- Line 126: Decrease audio_bitrate

**Problem: Video stutters on phone**
- Line 128: Decrease fps (15 → 12)
- Line 125: Decrease video_bitrate
- Use "low" or "ultralow" preset

#### Download Problems

**Problem: Download fails**
- Check internet connection
- Check YouTube URL is valid
- Try uploading cookies (age-restricted videos)
- Check logs for specific error

**Problem: "Format not available"**
- System should auto-fallback
- If persists, video may be unavailable in your region

**Problem: Download too slow**
- Line 69: Add rate limit if needed
- Consider using proxy (Line 65)
- Some videos are just slow from YouTube

#### System Problems

**Problem: Disk space full**
- Line 72: Increase threshold or disable monitoring
- Line 56: Decrease retention time
- Manually clean /tmp/downloads folder

**Problem: Processing timeout**
- Line 55: Increase CONVERSION_TIMEOUT
- Line 75: Decrease SUBTITLE_MAX_DURATION_MINS
- Disable subtitle burning for long videos

---

## 8. Code Reference Map

### 8.1 Configuration Variables

| Setting | Line | Default | Description |
|---------|------|---------|-------------|
| DOWNLOAD_FOLDER | 35 | /tmp/downloads | Where files are stored |
| COOKIES_FOLDER | 36 | /tmp/cookies | Where cookies are stored |
| MAX_VIDEO_DURATION | 53 | None | Max video length (seconds) |
| DOWNLOAD_TIMEOUT | 54 | None | Download timeout (seconds) |
| FILE_RETENTION_HOURS | 56 | 6 | How long to keep files |
| MAX_FILESIZE | 57 | 1000M | Max file size to download |
| DISK_SPACE_THRESHOLD_MB | 72 | 1500 | Min free space required (MB) |
| SUBTITLE_MAX_DURATION_MINS | 75 | 45 | Max video length for subs (min) |
| SUBTITLE_MAX_FILESIZE_MB | 76 | 500 | Max file size for subs (MB) |
| ENABLE_SUBTITLE_BURNING | 77 | True | Enable/disable subtitle feature |

### 8.2 Quality Presets

| Preset Type | Lines | Presets Available |
|-------------|-------|-------------------|
| MP3_QUALITY_PRESETS | 81-110 | medium, high, veryhigh, extreme |
| VIDEO_QUALITY_PRESETS | 114-147 | ultralow, low, medium, high |

### 8.3 Key Functions

| Function | Lines | Purpose |
|----------|-------|---------|
| burn_subtitles_ffmpeg_3gp | 735-893 | Burn subtitles into 3GP video |
| download_and_convert | 895-1506 | Main conversion function |
| convert_srt_to_ass | 643-732 | Convert SRT → ASS format |
| download_subtitles | 548-597 | Download YouTube subtitles |
| check_disk_space | 459-478 | Monitor disk usage |
| clean_old_files | 480-520 | Remove old files |

### 8.4 Subtitle Configuration

| Setting | Line | What to Edit |
|---------|------|--------------|
| ASS header | 771-786 | Subtitle format definition |
| Line1 style (bottom) | 781 | Font, size, color, margins |
| Line2 style (top) | 782 | Font, size, color, margins |
| PlayResX/Y | 775-776 | Subtitle coordinate system |
| Video filter | 846 | Video scaling and black bar |
| FFmpeg subtitle command | 848-873 | Complete encoding command |

### 8.5 FFmpeg Parameters

| Parameter Type | Lines | Parameters |
|----------------|-------|------------|
| Video filters | 846 | scale, pad, setsar, subtitles |
| Video encoding | 852-865 | vcodec, r, b:v, maxrate, qmin, qmax, etc. |
| Audio encoding | 867-870 | acodec, ar, b:a, ac |
| Advanced flags | 859-866 | mbd, flags, sc_threshold, trellis, etc. |

### 8.6 File Locations

| Type | Path | Purpose |
|------|------|---------|
| Downloaded videos | /tmp/downloads/{file_id}_temp.mp4 | Original download |
| Converted 3GP | /tmp/downloads/{file_id}.3gp | Converted video |
| With subtitles | /tmp/downloads/{file_id}_with_subs.3gp | Final with subs |
| Subtitle file (SRT) | /tmp/downloads/{file_id}.en.srt | English subtitles |
| Subtitle file (ASS) | /tmp/downloads/{file_id}_subs.ass | Formatted for FFmpeg |
| Status tracking | /tmp/conversion_status.json | Conversion status |
| Cookies | /tmp/cookies/youtube_cookies.txt | YouTube cookies |

---

## Final Notes

### After Making Changes

1. **Save app.py**
2. **Restart the Flask app**
3. **Test with a short video first**
4. **Check logs if something fails**

### Best Practices

- **Always test** changes with a short (1-2 min) video first
- **Keep backups** of your working configuration
- **Document** your custom changes
- **Monitor disk space** if hosting on free tier
- **Check logs** when debugging issues

### Getting Help

If you encounter issues:
1. Check the troubleshooting guide (Section 7.3)
2. Review the logs in /tmp/logs/
3. Verify your changes match the examples
4. Test with minimal changes first

---

**End of Documentation**

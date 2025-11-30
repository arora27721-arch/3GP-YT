import os
import subprocess
import time
import threading
import json
import secrets
import re
import logging
import math
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, send_file, flash
import hashlib

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder='templates/subtitle_burner')
app.secret_key = os.environ.get('SESSION_SECRET', secrets.token_hex(32))

@app.after_request
def add_cache_control_headers(response):
    if response.content_type and 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

UPLOAD_FOLDER = '/tmp/subtitle_uploads'
OUTPUT_FOLDER = '/tmp/subtitle_outputs'
STATUS_FILE = '/tmp/subtitle_status.json'
MAX_FILE_SIZE = 10 * 1024 * 1024 * 1024  # 10GB for Cloud Shell
ALLOWED_VIDEO_EXTENSIONS = {'mp4', 'MP4', '3gp', '3GP', 'mkv', 'MKV', 'avi', 'AVI', 'mov', 'MOV', 'webm', 'WEBM'}
ALLOWED_SUBTITLE_EXTENSIONS = {'srt', 'SRT', 'ass', 'ASS', 'vtt', 'VTT'}

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)

FFMPEG_THREADS = int(os.environ.get('FFMPEG_THREADS', 1))

def get_ffmpeg_path():
    possible_paths = ['ffmpeg', '/usr/bin/ffmpeg', '/usr/local/bin/ffmpeg', '/tmp/bin/ffmpeg']
    for path in possible_paths:
        try:
            result = subprocess.run([path, '-version'], capture_output=True, timeout=5)
            if result.returncode == 0:
                return path
        except:
            continue
    return 'ffmpeg'

FFMPEG_PATH = get_ffmpeg_path()
logger.info(f"Using FFmpeg: {FFMPEG_PATH}")

status_lock = threading.RLock()

def get_status():
    with status_lock:
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, 'r') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}

def update_status(job_id, updates):
    with status_lock:
        status = get_status()
        if job_id not in status:
            status[job_id] = {}
        status[job_id].update(updates)
        with open(STATUS_FILE, 'w') as f:
            json.dump(status, f)

def generate_job_id():
    timestamp = str(int(time.time() * 1000))
    return hashlib.md5(f"{timestamp}_{secrets.token_hex(8)}".encode()).hexdigest()[:16]

def allowed_video_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1] in ALLOWED_VIDEO_EXTENSIONS

def allowed_subtitle_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1] in ALLOWED_SUBTITLE_EXTENSIONS

def get_video_info(video_path):
    try:
        cmd = [
            'ffprobe', '-v', 'error',
            '-show_entries', 'format=duration,size',
            '-show_entries', 'stream=width,height',
            '-of', 'json',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            info = json.loads(result.stdout)
            streams = info.get('streams', [{}])
            format_info = info.get('format', {})
            width = 1920
            height = 1080
            for stream in streams:
                if 'width' in stream:
                    width = int(stream.get('width', 1920))
                    height = int(stream.get('height', 1080))
                    break
            return {
                'width': width,
                'height': height,
                'duration': float(format_info.get('duration', 0)),
                'size': int(format_info.get('size', 0))
            }
    except Exception as e:
        logger.error(f"Error getting video info: {e}")
    return {'width': 1920, 'height': 1080, 'duration': 0, 'size': 0}

def estimate_processing_time(duration_seconds, file_size_bytes, output_format, quality, split_parts=1):
    duration_hours = duration_seconds / 3600
    size_gb = file_size_bytes / (1024 * 1024 * 1024)
    
    if output_format == '3gp':
        base_ratio = 0.8 if quality == 'low' else 1.2 if quality == 'medium' else 1.8
    else:
        base_ratio = 0.5 if quality == 'low' else 1.0 if quality == 'medium' else 2.0
    
    est_minutes = (duration_hours * 60 * base_ratio) / FFMPEG_THREADS
    if split_parts > 1:
        est_minutes *= 1.2
    
    return max(1, int(est_minutes))

def format_duration(seconds):
    if seconds <= 0:
        return "Unknown"
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    else:
        return f"{secs}s"

def convert_vtt_to_srt(vtt_path, srt_path):
    try:
        with open(vtt_path, 'r', encoding='utf-8') as vtt_file:
            lines = vtt_file.readlines()
    except UnicodeDecodeError:
        with open(vtt_path, 'r', encoding='latin-1') as vtt_file:
            lines = vtt_file.readlines()

    srt_lines = []
    cue_number = 1
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        if (line.startswith('WEBVTT') or line.startswith('Kind:') or 
            line.startswith('Language:') or line.startswith('NOTE') or 
            line.startswith('Style:') or line.startswith('STYLE') or not line):
            i += 1
            continue

        timestamp_pattern = r'(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})'
        match = re.search(timestamp_pattern, line)

        if match:
            srt_lines.append(str(cue_number))
            cue_number += 1
            start_time = match.group(1).replace('.', ',')
            end_time = match.group(2).replace('.', ',')
            srt_lines.append(f"{start_time} --> {end_time}")
            i += 1
            while i < len(lines) and lines[i].strip():
                text_line = lines[i].strip()
                text_line = re.sub(r'<[^>]+>', '', text_line)
                text_line = re.sub(r'</?c[^>]*>', '', text_line)
                text_line = re.sub(r'\{[^}]+\}', '', text_line)
                text_line = text_line.strip()
                if text_line:
                    srt_lines.append(text_line)
                i += 1
            srt_lines.append('')
        else:
            i += 1

    with open(srt_path, 'w', encoding='utf-8') as srt_file:
        srt_file.write('\n'.join(srt_lines))
    return srt_path

def convert_srt_to_ass_3gp(srt_path, ass_path):
    fontsize = 14
    
    ass_header = f"""[Script Info]
Title: 3GP Feature Phone Subtitles
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 320
PlayResY: 240
Collisions: Normal
PlayDepth: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,{fontsize},&H00FFFFFF,&H000000FF,&H00000000,&HC0000000,-1,0,0,0,100,100,0,0,1,3,2,2,5,5,8,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    try:
        with open(srt_path, 'r', encoding='utf-8') as f:
            srt_content = f.read()
    except:
        with open(srt_path, 'r', encoding='latin-1') as f:
            srt_content = f.read()

    ass_events = []
    blocks = srt_content.strip().split('\n\n')

    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 2:
            continue

        timing_line = None
        text_start_idx = 0
        for idx, line in enumerate(lines):
            if '-->' in line:
                timing_line = line
                text_start_idx = idx + 1
                break
        
        if not timing_line:
            continue

        try:
            start_time, end_time = timing_line.split('-->')
            start_time = start_time.strip().replace(',', '.')
            end_time = end_time.strip().replace(',', '.').split()[0]

            def srt_to_ass_time(srt_time):
                parts = srt_time.split(':')
                if len(parts) == 3:
                    h, m, s = parts
                    h = str(int(h))
                    return f"{h}:{m}:{s[:5]}"
                return srt_time

            ass_start = srt_to_ass_time(start_time)
            ass_end = srt_to_ass_time(end_time)
            subtitle_text = ' '.join(lines[text_start_idx:]).replace('\n', ' ').replace('\\N', ' ')
            ass_event = f"Dialogue: 0,{ass_start},{ass_end},Default,,0,0,0,,{subtitle_text}"
            ass_events.append(ass_event)
        except Exception as e:
            logger.warning(f"Error parsing subtitle block: {e}")
            continue

    with open(ass_path, 'w', encoding='utf-8') as f:
        f.write(ass_header)
        f.write('\n'.join(ass_events))

    return True

def smart_split_video(input_path, num_parts, job_id, output_folder):
    try:
        video_info = get_video_info(input_path)
        total_duration = video_info['duration']
        
        if total_duration <= 0:
            logger.error("Could not determine video duration")
            return []
        
        part_duration = total_duration / num_parts
        output_ext = os.path.splitext(input_path)[1].lower()
        parts = []
        
        for i in range(num_parts):
            start_time = i * part_duration
            part_num = i + 1
            output_path = os.path.join(output_folder, f'{job_id}_part{part_num:02d}{output_ext}')
            
            update_status(job_id, {
                'progress': f'Splitting part {part_num}/{num_parts}...'
            })
            
            if output_ext in ['.3gp', '.3GP']:
                cmd = [
                    FFMPEG_PATH, '-threads', str(FFMPEG_THREADS),
                    '-ss', str(start_time),
                    '-i', input_path,
                    '-t', str(part_duration),
                    '-vcodec', 'mpeg4',
                    '-r', '15',
                    '-b:v', '300k',
                    '-s', '320x240',
                    '-acodec', 'aac',
                    '-ar', '22050',
                    '-b:a', '64k',
                    '-ac', '1',
                    '-movflags', '+faststart',
                    '-y', output_path
                ]
            else:
                cmd = [
                    FFMPEG_PATH, '-threads', str(FFMPEG_THREADS),
                    '-ss', str(start_time),
                    '-i', input_path,
                    '-t', str(part_duration),
                    '-c:v', 'libx264',
                    '-preset', 'fast',
                    '-crf', '23',
                    '-c:a', 'aac',
                    '-b:a', '128k',
                    '-movflags', '+faststart',
                    '-y', output_path
                ]
            
            logger.info(f"Splitting part {part_num}: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=None)
            
            if result.returncode != 0:
                logger.error(f"Split error part {part_num}: {result.stderr[:300]}")
                continue
            
            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                file_size = os.path.getsize(output_path)
                parts.append({
                    'path': output_path,
                    'filename': os.path.basename(output_path),
                    'part_num': part_num,
                    'size': file_size,
                    'size_human': f'{file_size / (1024*1024):.2f} MB',
                    'start_time': format_duration(start_time),
                    'duration': format_duration(part_duration)
                })
            else:
                logger.error(f"Part {part_num} output file empty or missing")
        
        return parts
    
    except Exception as e:
        logger.error(f"Smart split error: {str(e)}")
        return []

def burn_subtitles_3gp(job_id, video_path, subtitle_path, quality, split_parts=0):
    try:
        update_status(job_id, {
            'status': 'processing',
            'progress': 'Analyzing video file...'
        })

        video_info = get_video_info(video_path)
        duration = video_info['duration']
        file_size = video_info['size'] if video_info['size'] > 0 else os.path.getsize(video_path)
        
        est_time = estimate_processing_time(duration, file_size, '3gp', quality, split_parts)
        update_status(job_id, {
            'video_duration': format_duration(duration),
            'video_size': f'{file_size / (1024*1024):.2f} MB',
            'estimated_time': f'{est_time} minutes'
        })

        sub_ext = os.path.splitext(subtitle_path)[1].lower()
        
        srt_path = subtitle_path
        if sub_ext == '.vtt':
            update_status(job_id, {'progress': 'Converting VTT to SRT...'})
            srt_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_converted.srt')
            convert_vtt_to_srt(subtitle_path, srt_path)

        ass_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_styled.ass')
        update_status(job_id, {'progress': 'Creating styled subtitles for 3GP...'})
        
        if sub_ext == '.ass':
            ass_path = subtitle_path
        else:
            convert_srt_to_ass_3gp(srt_path, ass_path)

        output_path = os.path.join(OUTPUT_FOLDER, f'{job_id}_subtitled.3gp')
        
        update_status(job_id, {'progress': f'Burning subtitles into 3GP video (est. {est_time} min)...'})

        escaped_ass_path = ass_path.replace('\\', '/').replace(':', '\\:').replace("'", "\\'")
        
        quality_settings = {
            'low': {'vb': '200k', 'ab': '64k', 'fps': '12', 'ar': '22050'},
            'medium': {'vb': '300k', 'ab': '128k', 'fps': '15', 'ar': '44100'},
            'high': {'vb': '500k', 'ab': '192k', 'fps': '24', 'ar': '44100'},
        }
        q = quality_settings.get(quality, quality_settings['medium'])
        
        video_filter = f"scale=320:240:force_original_aspect_ratio=decrease,pad=320:240:(ow-iw)/2:(oh-ih)/2,subtitles={escaped_ass_path}"
        
        ffmpeg_cmd = [
            FFMPEG_PATH,
            '-threads', str(FFMPEG_THREADS),
            '-i', video_path,
            '-vf', video_filter,
            '-vcodec', 'mpeg4',
            '-r', q['fps'],
            '-b:v', q['vb'],
            '-qmin', '2',
            '-qmax', '31',
            '-acodec', 'aac',
            '-ar', q['ar'],
            '-b:a', q['ab'],
            '-ac', '1',
            '-movflags', '+faststart',
            '-y',
            output_path
        ]

        logger.info(f"Running FFmpeg: {' '.join(ffmpeg_cmd)}")
        start_time = time.time()
        result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=None)
        elapsed = time.time() - start_time

        if result.returncode != 0:
            logger.error(f"FFmpeg error: {result.stderr[:500]}")
            update_status(job_id, {
                'status': 'failed',
                'progress': f'FFmpeg encoding failed: {result.stderr[:200]}'
            })
            return False

        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            file_size = os.path.getsize(output_path)
            size_mb = file_size / (1024 * 1024)
            
            try:
                if srt_path != subtitle_path and os.path.exists(srt_path):
                    os.remove(srt_path)
                if ass_path != subtitle_path and os.path.exists(ass_path):
                    os.remove(ass_path)
            except:
                pass
            
            if split_parts > 1:
                update_status(job_id, {'progress': f'Subtitle burning complete! Now splitting into {split_parts} parts...'})
                parts = smart_split_video(output_path, split_parts, job_id, OUTPUT_FOLDER)
                
                if parts:
                    update_status(job_id, {
                        'status': 'completed',
                        'progress': f'Done! Created {len(parts)} parts in {format_duration(elapsed)}',
                        'output_file': output_path,
                        'output_filename': os.path.basename(output_path),
                        'file_size': f'{size_mb:.2f} MB',
                        'split_parts': parts,
                        'actual_time': format_duration(elapsed),
                        'completed_at': datetime.now().isoformat()
                    })
                else:
                    update_status(job_id, {
                        'status': 'completed',
                        'progress': f'Subtitles burned, but splitting failed. Download full file.',
                        'output_file': output_path,
                        'output_filename': os.path.basename(output_path),
                        'file_size': f'{size_mb:.2f} MB',
                        'actual_time': format_duration(elapsed),
                        'completed_at': datetime.now().isoformat()
                    })
            else:
                update_status(job_id, {
                    'status': 'completed',
                    'progress': f'3GP with subtitles ready! Completed in {format_duration(elapsed)}',
                    'output_file': output_path,
                    'output_filename': os.path.basename(output_path),
                    'file_size': f'{size_mb:.2f} MB',
                    'actual_time': format_duration(elapsed),
                    'completed_at': datetime.now().isoformat()
                })
            
            return True
        else:
            update_status(job_id, {
                'status': 'failed',
                'progress': 'Output file not created or empty'
            })
            return False

    except Exception as e:
        logger.error(f"Error burning subtitles: {str(e)}")
        update_status(job_id, {
            'status': 'failed',
            'progress': f'Error: {str(e)[:200]}'
        })
        return False

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload():
    if 'video' not in request.files or 'subtitle' not in request.files:
        flash('Please upload both video and subtitle files')
        return redirect(url_for('index'))

    video_file = request.files['video']
    subtitle_file = request.files['subtitle']

    if video_file.filename == '' or subtitle_file.filename == '':
        flash('No files selected')
        return redirect(url_for('index'))

    if not allowed_video_file(video_file.filename):
        flash('Invalid video format. Supported: MP4, 3GP, MKV, AVI, MOV, WEBM')
        return redirect(url_for('index'))

    if not allowed_subtitle_file(subtitle_file.filename):
        flash('Invalid subtitle format. Supported: SRT, ASS, VTT')
        return redirect(url_for('index'))

    job_id = generate_job_id()
    
    video_ext = os.path.splitext(video_file.filename)[1].lower()
    subtitle_ext = os.path.splitext(subtitle_file.filename)[1].lower()
    
    video_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_video{video_ext}')
    subtitle_path = os.path.join(UPLOAD_FOLDER, f'{job_id}_subtitle{subtitle_ext}')

    try:
        video_file.save(video_path)
        subtitle_file.save(subtitle_path)
    except Exception as e:
        flash(f'Error saving files: {str(e)}')
        return redirect(url_for('index'))

    quality = request.form.get('quality', 'medium')
    
    split_parts = 0
    try:
        split_str = request.form.get('split_parts', '0').strip()
        if split_str:
            split_parts = int(split_str)
            if split_parts < 0 or split_parts > 50:
                split_parts = 0
    except:
        split_parts = 0

    video_info = get_video_info(video_path)
    est_time = estimate_processing_time(
        video_info['duration'], 
        video_info['size'] if video_info['size'] > 0 else os.path.getsize(video_path),
        '3gp', quality, split_parts
    )

    update_status(job_id, {
        'status': 'queued',
        'progress': 'Job queued for processing...',
        'video_filename': video_file.filename,
        'subtitle_filename': subtitle_file.filename,
        'output_format': '3gp',
        'quality': quality,
        'split_part_count': split_parts,
        'estimated_time': f'{est_time} minutes',
        'video_duration': format_duration(video_info['duration']),
        'created_at': datetime.now().isoformat()
    })

    thread = threading.Thread(target=burn_subtitles_3gp, args=(job_id, video_path, subtitle_path, quality, split_parts))
    thread.daemon = True
    thread.start()

    return redirect(url_for('status', job_id=job_id))

@app.route('/status/<job_id>')
def status(job_id):
    job_status = get_status().get(job_id, {})
    if not job_status:
        flash('Job not found')
        return redirect(url_for('index'))
    return render_template('status.html', job_id=job_id, status=job_status)

@app.route('/status_json/<job_id>')
def status_json(job_id):
    job_status = get_status().get(job_id, {})
    return json.dumps(job_status)

@app.route('/download/<job_id>')
def download(job_id):
    job_status = get_status().get(job_id, {})
    
    if job_status.get('status') != 'completed':
        flash('File not ready for download')
        return redirect(url_for('status', job_id=job_id))

    output_file = job_status.get('output_file')
    if not output_file or not os.path.exists(output_file):
        flash('Output file not found')
        return redirect(url_for('index'))

    original_video = job_status.get('video_filename', 'video')
    base_name = os.path.splitext(original_video)[0]
    download_name = f"{base_name}_subtitled.3gp"

    return send_file(output_file, as_attachment=True, download_name=download_name)

@app.route('/download_part/<job_id>/<int:part_num>')
def download_part(job_id, part_num):
    job_status = get_status().get(job_id, {})
    parts = job_status.get('split_parts', [])
    
    for part in parts:
        if part.get('part_num') == part_num:
            if os.path.exists(part['path']):
                return send_file(part['path'], as_attachment=True, download_name=part['filename'])
    
    flash('Part not found')
    return redirect(url_for('status', job_id=job_id))

@app.route('/history')
def history():
    all_status = get_status()
    jobs = []
    for job_id, data in all_status.items():
        jobs.append({
            'job_id': job_id,
            **data
        })
    jobs.sort(key=lambda x: x.get('created_at', ''), reverse=True)
    return render_template('history.html', jobs=jobs[:50])

def cleanup_old_files():
    """Clean up files older than 6 hours"""
    try:
        cutoff_time = time.time() - (6 * 60 * 60)
        
        for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER]:
            if os.path.exists(folder):
                for filename in os.listdir(folder):
                    filepath = os.path.join(folder, filename)
                    if os.path.isfile(filepath):
                        if os.path.getmtime(filepath) < cutoff_time:
                            try:
                                os.remove(filepath)
                                logger.info(f"Cleaned up old file: {filepath}")
                            except Exception as e:
                                logger.warning(f"Failed to delete {filepath}: {e}")
        
        with status_lock:
            status = get_status()
            jobs_to_remove = []
            for job_id, data in status.items():
                created_at = data.get('created_at', '')
                if created_at:
                    try:
                        created_time = datetime.fromisoformat(created_at)
                        if datetime.now() - created_time > timedelta(hours=48):
                            jobs_to_remove.append(job_id)
                    except:
                        pass
            
            for job_id in jobs_to_remove:
                del status[job_id]
                logger.info(f"Removed old job from status: {job_id}")
            
            if jobs_to_remove:
                with open(STATUS_FILE, 'w') as f:
                    json.dump(status, f)
                    
    except Exception as e:
        logger.error(f"Cleanup error: {e}")

def run_cleanup_thread():
    """Run cleanup every hour"""
    while True:
        time.sleep(3600)
        cleanup_old_files()

if __name__ == '__main__':
    cleanup_thread = threading.Thread(target=run_cleanup_thread, daemon=True)
    cleanup_thread.start()
    
    initial_cleanup = threading.Thread(target=cleanup_old_files, daemon=True)
    initial_cleanup.start()
    
    port = int(os.environ.get('SUBTITLE_BURNER_PORT', 8000))
    app.run(host='0.0.0.0', port=port, debug=False)

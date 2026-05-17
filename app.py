# app.py
from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import yt_dlp
import os
import re
import uuid
import hashlib
import json
import requests
from datetime import datetime, timedelta
from functools import wraps
import redis
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for all domains

# ============ CONFIGURATION ============
class Config:
    # Directories
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    DOWNLOAD_DIR = os.path.join(BASE_DIR, "downloads")
    CACHE_DIR = os.path.join(BASE_DIR, "cache")
    
    # Create directories
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(CACHE_DIR, exist_ok=True)
    
    # Cache settings (7 days TTL)
    CACHE_TTL = 604800  # 7 days in seconds
    MAX_FILE_SIZE = 200 * 1024 * 1024  # 200MB max
    
    # Rate limiting
    RATELIMIT_REQUESTS = 100
    RATELIMIT_PERIOD = 60  # seconds
    
    # Redis (optional, for production)
    REDIS_URL = os.getenv("REDIS_URL", None)
    
    # YouTube cookies (optional, for age-restricted content)
    COOKIES_FILE = os.getenv("COOKIES_FILE", None)
    
    # Allowed formats
    ALLOWED_AUDIO_FORMATS = ['mp3', 'm4a', 'webm', 'opus']
    ALLOWED_VIDEO_FORMATS = ['mp4', 'mkv', 'webm']

config = Config()

# ============ HELPER FUNCTIONS ============
def get_cache_key(url, media_type, quality=None):
    """Generate unique cache key"""
    key_data = f"{url}_{media_type}_{quality or ''}"
    return hashlib.md5(key_data.encode()).hexdigest()

def get_file_extension(format_spec):
    """Get file extension from yt-dlp format"""
    ext_map = {
        'mp3': 'mp3',
        'm4a': 'm4a', 
        'opus': 'opus',
        'webm': 'webm',
        'mp4': 'mp4',
        'mkv': 'mkv'
    }
    return ext_map.get(format_spec, 'mp4' if 'video' in format_spec else 'mp3')

def cleanup_old_files():
    """Clean files older than 7 days"""
    try:
        cutoff = datetime.now() - timedelta(days=7)
        for dir_path in [config.DOWNLOAD_DIR, config.CACHE_DIR]:
            for filename in os.listdir(dir_path):
                filepath = os.path.join(dir_path, filename)
                if os.path.isfile(filepath):
                    mtime = datetime.fromtimestamp(os.path.getmtime(filepath))
                    if mtime < cutoff:
                        os.remove(filepath)
                        print(f"Cleaned up: {filepath}")
    except Exception as e:
        print(f"Cleanup error: {e}")

# ============ YOUTUBE DOWNLOADER ============
class YouTubeDownloader:
    @staticmethod
    def extract_info(url):
        """Extract video information without downloading"""
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'extract_flat': False,
        }
        
        if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
            ydl_opts['cookiefile'] = config.COOKIES_FILE
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
                # Format duration
                duration = info.get('duration', 0)
                if duration:
                    minutes = duration // 60
                    seconds = duration % 60
                    duration_str = f"{minutes}:{seconds:02d}"
                else:
                    duration_str = "LIVE"
                
                # Get best thumbnail
                thumbnails = info.get('thumbnails', [])
                thumbnail = thumbnails[-1]['url'] if thumbnails else None
                
                return {
                    'success': True,
                    'id': info.get('id'),
                    'title': info.get('title'),
                    'duration': duration_str,
                    'duration_seconds': duration,
                    'thumbnail': thumbnail,
                    'channel': info.get('uploader'),
                    'channel_id': info.get('channel_id'),
                    'view_count': info.get('view_count'),
                    'like_count': info.get('like_count'),
                    'is_live': info.get('is_live', False)
                }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    @staticmethod
    def download_audio(url, quality='best'):
        """Download audio only"""
        cache_key = get_cache_key(url, 'audio', quality)
        
        # Check cache
        cached_files = [f for f in os.listdir(config.CACHE_DIR) if f.startswith(cache_key)]
        if cached_files:
            return os.path.join(config.CACHE_DIR, cached_files[0])
        
        # Audio format options
        format_map = {
            'best': 'bestaudio/best',
            'high': 'bestaudio[abr>128]/bestaudio',
            'medium': 'bestaudio[abr>=64]/bestaudio',
            'low': 'worstaudio/worst'
        }
        
        filename = f"{uuid.uuid4().hex}.%(ext)s"
        output_path = os.path.join(config.DOWNLOAD_DIR, filename)
        
        ydl_opts = {
            'format': format_map.get(quality, 'bestaudio/best'),
            'outtmpl': output_path,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'extract_audio': True,
            'audio_format': 'mp3',
            'audio_quality': '192',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        }
        
        if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
            ydl_opts['cookiefile'] = config.COOKIES_FILE
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)
                
                # Find downloaded file
                downloaded_file = None
                for file in os.listdir(config.DOWNLOAD_DIR):
                    if file.startswith(uuid.uuid4().hex[:8]):
                        downloaded_file = os.path.join(config.DOWNLOAD_DIR, file)
                        break
                
                if downloaded_file:
                    # Move to cache
                    cache_file = os.path.join(config.CACHE_DIR, f"{cache_key}.mp3")
                    os.rename(downloaded_file, cache_file)
                    
                    # Cleanup old files
                    cleanup_old_files()
                    
                    return cache_file
                
                return None
                
        except Exception as e:
            raise Exception(f"Audio download failed: {str(e)}")
    
    @staticmethod
    def download_video(url, quality='360'):
        """Download video with specific quality"""
        cache_key = get_cache_key(url, 'video', quality)
        
        # Check cache
        cached_files = [f for f in os.listdir(config.CACHE_DIR) if f.startswith(cache_key)]
        if cached_files:
            return os.path.join(config.CACHE_DIR, cached_files[0])
        
        # Quality presets
        quality_map = {
            '144': 'bestvideo[height<=144][ext=mp4]+bestaudio[ext=m4a]/best[height<=144][ext=mp4]',
            '240': 'bestvideo[height<=240][ext=mp4]+bestaudio[ext=m4a]/best[height<=240][ext=mp4]',
            '360': 'bestvideo[height<=360][ext=mp4]+bestaudio[ext=m4a]/best[height<=360][ext=mp4]',
            '480': 'bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]',
            '720': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]',
            '1080': 'bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]',
        }
        
        format_spec = quality_map.get(quality, quality_map['360'])
        filename = f"{uuid.uuid4().hex}.%(ext)s"
        output_path = os.path.join(config.DOWNLOAD_DIR, filename)
        
        ydl_opts = {
            'format': format_spec,
            'outtmpl': output_path,
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'merge_output_format': 'mp4',
        }
        
        if config.COOKIES_FILE and os.path.exists(config.COOKIES_FILE):
            ydl_opts['cookiefile'] = config.COOKIES_FILE
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.extract_info(url, download=True)
                
                # Find downloaded file
                downloaded_file = None
                for file in os.listdir(config.DOWNLOAD_DIR):
                    if file.startswith(uuid.uuid4().hex[:8]):
                        downloaded_file = os.path.join(config.DOWNLOAD_DIR, file)
                        break
                
                if downloaded_file:
                    # Move to cache
                    cache_file = os.path.join(config.CACHE_DIR, f"{cache_key}.mp4")
                    os.rename(downloaded_file, cache_file)
                    
                    cleanup_old_files()
                    return cache_file
                
                return None
                
        except Exception as e:
            raise Exception(f"Video download failed: {str(e)}")

downloader = YouTubeDownloader()

# ============ API ENDPOINTS ============
@app.route('/info', methods=['GET'])
def get_info():
    """Get video information"""
    url = request.args.get('url')
    
    if not url:
        return jsonify({'error': 'URL parameter required'}), 400
    
    result = downloader.extract_info(url)
    return jsonify(result)

@app.route('/search', methods=['GET'])
def search():
    """Search YouTube videos"""
    query = request.args.get('q')
    limit = int(request.args.get('limit', 5))
    
    if not query:
        return jsonify({'error': 'Search query required'}), 400
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,
        'default_search': f'ytsearch{limit}',
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            search_query = f"ytsearch{limit}:{query}"
            info = ydl.extract_info(search_query, download=False)
            
            results = []
            if info and 'entries' in info:
                for entry in info['entries']:
                    duration = entry.get('duration', 0)
                    if duration:
                        minutes = duration // 60
                        seconds = duration % 60
                        duration_str = f"{minutes}:{seconds:02d}"
                    else:
                        duration_str = "LIVE"
                    
                    results.append({
                        'id': entry.get('id'),
                        'title': entry.get('title'),
                        'duration': duration_str,
                        'url': f"https://youtube.com/watch?v={entry.get('id')}",
                        'thumbnail': f"https://img.youtube.com/vi/{entry.get('id')}/hqdefault.jpg",
                        'channel': entry.get('uploader')
                    })
            
            return jsonify({'results': results, 'count': len(results)})
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/audio', methods=['GET'])
def download_audio():
    """Download audio only"""
    url = request.args.get('url')
    quality = request.args.get('quality', 'best')
    
    if not url:
        return jsonify({'error': 'URL parameter required'}), 400
    
    try:
        # Get video info first
        info = downloader.extract_info(url)
        if not info.get('success'):
            return jsonify({'error': info.get('error')}), 400
        
        # Download audio
        audio_file = downloader.download_audio(url, quality)
        
        if audio_file and os.path.exists(audio_file):
            return send_file(
                audio_file,
                as_attachment=True,
                download_name=f"{info['title'][:50]}.mp3",
                mimetype='audio/mpeg'
            )
        else:
            return jsonify({'error': 'Download failed'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download/video', methods=['GET'])
def download_video():
    """Download video"""
    url = request.args.get('url')
    quality = request.args.get('quality', '360')
    
    if not url:
        return jsonify({'error': 'URL parameter required'}), 400
    
    try:
        # Get video info
        info = downloader.extract_info(url)
        if not info.get('success'):
            return jsonify({'error': info.get('error')}), 400
        
        # Download video
        video_file = downloader.download_video(url, quality)
        
        if video_file and os.path.exists(video_file):
            return send_file(
                video_file,
                as_attachment=True,
                download_name=f"{info['title'][:50]}.mp4",
                mimetype='video/mp4'
            )
        else:
            return jsonify({'error': 'Download failed'}), 500
            
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'cache_size': len(os.listdir(config.CACHE_DIR))
    })

@app.route('/', methods=['GET'])
def home():
    """API documentation"""
    return jsonify({
        'name': 'YouTube Downloader API',
        'version': '2.0.0',
        'endpoints': {
            '/info': 'GET - Get video information (params: url)',
            '/search': 'GET - Search videos (params: q, limit)',
            '/download/audio': 'GET - Download audio (params: url, quality)',
            '/download/video': 'GET - Download video (params: url, quality)',
            '/health': 'GET - Health check'
        },
        'qualities': {
            'audio': ['best', 'high', 'medium', 'low'],
            'video': ['144', '240', '360', '480', '720', '1080']
        }
    })

if __name__ == '__main__':
    port = int(os.getenv('PORT', 5000))
    host = os.getenv('HOST', '0.0.0.0')
    
    print("""
    ╔═══════════════════════════════════════╗
    ║   YouTube Downloader API Started      ║
    ╠═══════════════════════════════════════╣
    ║   Server: http://{}:{}     ║
    ║   Status: Running                       ║
    ╚═══════════════════════════════════════╝
    """.format(host if host != '0.0.0.0' else 'localhost', port))
    
    app.run(host=host, port=port, debug=False, threaded=True)

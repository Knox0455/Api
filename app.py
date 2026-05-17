from flask import Flask, request, jsonify, send_file, Response, stream_with_context
import yt_dlp
import os
import uuid
import requests
import hashlib
import glob
import shutil
import json
from functools import wraps

app = Flask(__name__)

# Base directory using /tmp (Render free plan uses ephemeral storage)
BASE_TEMP_DIR = "/tmp"

# Directory for storing temporary download files (will be cleared after each request)
TEMP_DOWNLOAD_DIR = os.path.join(BASE_TEMP_DIR, "download")
os.makedirs(TEMP_DOWNLOAD_DIR, exist_ok=True)

# Directory for storing cached audio files (persists until container restart)
CACHE_DIR = os.path.join(BASE_TEMP_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Directory for storing cached video files separately
CACHE_VIDEO_DIR = os.path.join(BASE_TEMP_DIR, "cache_video")
os.makedirs(CACHE_VIDEO_DIR, exist_ok=True)

# Maximum cache size in bytes (adjusted to 500MB for Render free plan)
MAX_CACHE_SIZE = 500 * 1024 * 1024  # 500MB

# Path to your cookies file (if needed)
COOKIES_FILE = "cookies.txt"

# Search API URL (used both for regular searches and Spotify link resolution)
SEARCH_API_URL = "https://odd-block-a945.tenopno.workers.dev/search?title="

def get_cache_key(video_url):
    """Generate a cache key from the video URL."""
    return hashlib.md5(video_url.encode('utf-8')).hexdigest()

def get_directory_size(directory):
    total_size = 0
    for dirpath, dirnames, filenames in os.walk(directory):
        for f in filenames:
            fp = os.path.join(dirpath, f)
            if os.path.isfile(fp):
                total_size += os.path.getsize(fp)
    return total_size

def check_cache_size_and_cleanup():
    """Check combined cache size and remove all cache files if it exceeds the threshold."""
    total_size = get_directory_size(CACHE_DIR) + get_directory_size(CACHE_VIDEO_DIR)
    if total_size > MAX_CACHE_SIZE:
        for cache_dir in [CACHE_DIR, CACHE_VIDEO_DIR]:
            for file in os.listdir(cache_dir):
                file_path = os.path.join(cache_dir, file)
                try:
                    os.remove(file_path)
                except Exception as e:
                    print(f"Error deleting file {file_path}: {e}")

def get_stream_info(video_url, quality='audio'):
    """
    Get direct streaming URL and metadata from YouTube.
    Returns a dict with streaming URLs, headers, and metadata.
    """
    ydl_opts = {
        'quiet': True,
        'cookiefile': COOKIES_FILE,
        'socket_timeout': 60,
    }
    
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(video_url, download=False)
            
            if quality == 'audio':
                # Get best audio format
                formats = info.get('formats', [])
                audio_formats = []
                
                for f in formats:
                    if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                        # Prioritize m4a over webm for better compatibility
                        ext = f.get('ext', '')
                        abr = f.get('abr', 0)
                        audio_formats.append({
                            'url': f.get('url'),
                            'ext': ext,
                            'abr': abr,
                            'filesize': f.get('filesize'),
                            'format_id': f.get('format_id')
                        })
                
                # Sort by bitrate (higher is better) and extension preference
                audio_formats.sort(key=lambda x: (
                    x['ext'] != 'm4a',  # m4a preferred
                    -x.get('abr', 0)     # higher bitrate preferred
                ))
                
                if audio_formats:
                    stream_data = audio_formats[0]
                else:
                    # Fallback: extract from manifest
                    stream_data = {'url': info.get('url')}
                
                return {
                    'stream_url': stream_data.get('url'),
                    'format': stream_data.get('ext', 'm4a'),
                    'bitrate': stream_data.get('abr'),
                    'title': info.get('title'),
                    'duration': info.get('duration'),
                    'thumbnail': info.get('thumbnail'),
                    'video_id': info.get('id'),
                    'filesize': stream_data.get('filesize'),
                    'quality': 'audio'
                }
            
            else:  # video quality - 240p with audio
                # Get best format with video <= 240p and audio
                video_formats = []
                for f in formats:
                    height = f.get('height') or 0
                    if height <= 240 and f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                        video_formats.append({
                            'url': f.get('url'),
                            'ext': f.get('ext', 'mp4'),
                            'height': height,
                            'filesize': f.get('filesize'),
                            'format_id': f.get('format_id')
                        })
                
                # Sort by height (higher is better but still <=240)
                video_formats.sort(key=lambda x: -x['height'])
                
                if video_formats:
                    stream_data = video_formats[0]
                else:
                    stream_data = {'url': info.get('url')}
                
                return {
                    'stream_url': stream_data.get('url'),
                    'format': stream_data.get('ext', 'mp4'),
                    'height': stream_data.get('height', 240),
                    'title': info.get('title'),
                    'duration': info.get('duration'),
                    'thumbnail': info.get('thumbnail'),
                    'video_id': info.get('id'),
                    'filesize': stream_data.get('filesize'),
                    'quality': 'video'
                }
                
        except Exception as e:
            raise Exception(f"Error getting stream info: {e}")

def download_audio(video_url):
    """
    Download audio from the given YouTube video URL with caching.
    If the audio file was previously downloaded, return the cached file.
    """
    cache_key = get_cache_key(video_url)
    cached_files = glob.glob(os.path.join(CACHE_DIR, f"{cache_key}.*"))
    if cached_files:
        return cached_files[0]

    unique_id = str(uuid.uuid4())
    output_template = os.path.join(TEMP_DOWNLOAD_DIR, f"{unique_id}.%(ext)s")
    ydl_opts = {
        'format': 'worstaudio/worst',
        'outtmpl': output_template,
        'noplaylist': True,
        'quiet': True,
        'cookiefile': COOKIES_FILE,
        'socket_timeout': 60,
        'max_memory': 450000,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(video_url, download=True)
            downloaded_file = ydl.prepare_filename(info)
            ext = info.get("ext", "m4a")
            cached_file_path = os.path.join(CACHE_DIR, f"{cache_key}.{ext}")
            shutil.move(downloaded_file, cached_file_path)
            check_cache_size_and_cleanup()
            return cached_file_path
        except Exception as e:
            raise Exception(f"Error downloading audio: {e}")

def resolve_spotify_link(url):
    """
    If the URL is a Spotify link, use the search API to find the corresponding YouTube link.
    Otherwise, return the URL unchanged.
    """
    if "spotify.com" in url:
        response = requests.get(SEARCH_API_URL + url)
        if response.status_code != 200:
            raise Exception("Failed to fetch search results for the Spotify link")
        search_result = response.json()
        if not search_result or 'link' not in search_result:
            raise Exception("No YouTube link found for the given Spotify link")
        return search_result['link']
    return url

@app.route('/search', methods=['GET'])
def search_video():
    """
    Search for a YouTube video using the external API.
    """
    try:
        query = request.args.get('title')
        if not query:
            return jsonify({"error": "The 'title' parameter is required"}), 400

        response = requests.get(SEARCH_API_URL + query)
        if response.status_code != 200:
            return jsonify({"error": "Failed to fetch search results"}), 500

        search_result = response.json()
        if not search_result or 'link' not in search_result:
            return jsonify({"error": "No videos found for the given query"}), 404

        return jsonify({
            "title": search_result["title"],
            "url": search_result["link"],
            "duration": search_result.get("duration"),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

def download_video(video_url):
    """
    Download video (with audio) from the given YouTube video URL in 240p and worst audio quality with caching.
    If the video file was previously downloaded, return the cached file.
    """
    cache_key = hashlib.md5((video_url + "_video").encode('utf-8')).hexdigest()
    cached_files = glob.glob(os.path.join(CACHE_VIDEO_DIR, f"{cache_key}.*"))
    if cached_files:
        return cached_files[0]

    unique_id = str(uuid.uuid4())
    output_template = os.path.join(TEMP_DOWNLOAD_DIR, f"{unique_id}.%(ext)s")
    ydl_opts = {
        'format': 'bestvideo[height<=144]+worstaudio/worst',
        'outtmpl': output_template,
        'noplaylist': True,
        'quiet': True,
        'cookiefile': COOKIES_FILE,
        'socket_timeout': 60,
        'max_memory': 300000,
        'merge_output_format': 'mp4',
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        try:
            info = ydl.extract_info(video_url, download=True)
            downloaded_file = ydl.prepare_filename(info)
            cached_file_path = os.path.join(CACHE_VIDEO_DIR, f"{cache_key}.mp4")
            shutil.move(downloaded_file, cached_file_path)
            check_cache_size_and_cleanup()
            return cached_file_path
        except Exception as e:
            raise Exception(f"Error downloading video: {e}")

@app.route('/vdown', methods=['GET'])
def download_video_endpoint():
    """
    Download video from a YouTube video URL (or search by title) in 240p with worst audio.
    Works similarly to the /download endpoint, but returns the video file.
    """
    try:
        video_url = request.args.get('url')
        video_title = request.args.get('title')

        if not video_url and not video_title:
            return jsonify({"error": "Either 'url' or 'title' parameter is required"}), 400

        if video_title and not video_url:
            response = requests.get(SEARCH_API_URL + video_title)
            if response.status_code != 200:
                return jsonify({"error": "Failed to fetch search results"}), 500
            search_result = response.json()
            if not search_result or 'link' not in search_result:
                return jsonify({"error": "No videos found for the given query"}), 404
            video_url = search_result['link']

        if video_url and "spotify.com" in video_url:
            video_url = resolve_spotify_link(video_url)

        cached_file_path = download_video(video_url)

        return send_file(
            cached_file_path,
            as_attachment=True,
            download_name=os.path.basename(cached_file_path)
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        for file in os.listdir(TEMP_DOWNLOAD_DIR):
            file_path = os.path.join(TEMP_DOWNLOAD_DIR, file)
            try:
                os.remove(file_path)
            except Exception as cleanup_error:
                print(f"Error deleting file {file_path}: {cleanup_error}")

@app.route('/download', methods=['GET'])
def download_audio_endpoint():
    """
    Download audio from a YouTube video URL or search for it by title and download.
    Utilizes caching so repeated downloads for the same video are avoided.
    Also supports Spotify links by resolving them via the search API.
    """
    try:
        video_url = request.args.get('url')
        video_title = request.args.get('title')

        if not video_url and not video_title:
            return jsonify({"error": "Either 'url' or 'title' parameter is required"}), 400

        if video_title and not video_url:
            response = requests.get(SEARCH_API_URL + video_title)
            if response.status_code != 200:
                return jsonify({"error": "Failed to fetch search results"}), 500
            search_result = response.json()
            if not search_result or 'link' not in search_result:
                return jsonify({"error": "No videos found for the given query"}), 404
            video_url = search_result['link']

        if video_url and "spotify.com" in video_url:
            video_url = resolve_spotify_link(video_url)

        cached_file_path = download_audio(video_url)

        return send_file(
            cached_file_path,
            as_attachment=True,
            download_name=os.path.basename(cached_file_path)
        )
    except Exception as e:
        return jsonify({"error": str(e)}), 500
    finally:
        for file in os.listdir(TEMP_DOWNLOAD_DIR):
            file_path = os.path.join(TEMP_DOWNLOAD_DIR, file)
            try:
                os.remove(file_path)
            except Exception as cleanup_error:
                print(f"Error deleting file {file_path}: {cleanup_error}")

@app.route('/stream', methods=['GET'])
def stream_endpoint():
    """
    Get direct streaming URL/info for real-time playback in Telegram music bot.
    Returns streaming URLs that can be used directly.
    
    Query parameters:
    - url: YouTube video URL
    - title: Search by title
    - quality: 'audio' (default) or 'video'
    - type: 'info' (return metadata only) or 'url' (return streaming URL)
    """
    try:
        video_url = request.args.get('url')
        video_title = request.args.get('title')
        quality = request.args.get('quality', 'audio')
        resp_type = request.args.get('type', 'info')
        
        if not video_url and not video_title:
            return jsonify({"error": "Either 'url' or 'title' parameter is required"}), 400
        
        if video_title and not video_url:
            response = requests.get(SEARCH_API_URL + video_title)
            if response.status_code != 200:
                return jsonify({"error": "Failed to fetch search results"}), 500
            search_result = response.json()
            if not search_result or 'link' not in search_result:
                return jsonify({"error": "No videos found for the given query"}), 404
            video_url = search_result['link']
        
        if video_url and "spotify.com" in video_url:
            video_url = resolve_spotify_link(video_url)
        
        # Get stream information
        stream_info = get_stream_info(video_url, quality)
        
        if resp_type == 'url':
            # Return just the streaming URL (for direct playback)
            return jsonify({
                "stream_url": stream_info['stream_url'],
                "title": stream_info['title'],
                "duration": stream_info['duration'],
                "format": stream_info['format']
            })
        else:
            # Return full metadata with streaming info
            return jsonify({
                "status": "success",
                "stream_url": stream_info['stream_url'],
                "title": stream_info['title'],
                "duration": stream_info['duration'],
                "thumbnail": stream_info.get('thumbnail'),
                "video_id": stream_info.get('video_id'),
                "format": stream_info.get('format'),
                "filesize": stream_info.get('filesize'),
                "quality": stream_info.get('quality'),
                "bitrate": stream_info.get('bitrate'),
                "height": stream_info.get('height'),
                # Add headers that might be needed for streaming
                "headers": {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": "https://www.youtube.com/"
                }
            })
            
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/stream/download', methods=['GET'])
def stream_download_endpoint():
    """
    Proxy endpoint that streams the content directly.
    Useful for when you need to avoid CORS or want to stream through the API.
    
    Query parameters:
    - url: YouTube video URL
    - title: Search by title
    - quality: 'audio' (default) or 'video'
    """
    try:
        video_url = request.args.get('url')
        video_title = request.args.get('title')
        quality = request.args.get('quality', 'audio')
        
        if not video_url and not video_title:
            return jsonify({"error": "Either 'url' or 'title' parameter is required"}), 400
        
        if video_title and not video_url:
            response = requests.get(SEARCH_API_URL + video_title)
            if response.status_code != 200:
                return jsonify({"error": "Failed to fetch search results"}), 500
            search_result = response.json()
            if not search_result or 'link' not in search_result:
                return jsonify({"error": "No videos found for the given query"}), 404
            video_url = search_result['link']
        
        if video_url and "spotify.com" in video_url:
            video_url = resolve_spotify_link(video_url)
        
        stream_info = get_stream_info(video_url, quality)
        stream_url = stream_info['stream_url']
        
        # Stream the content from YouTube
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Referer': 'https://www.youtube.com/',
        }
        
        def generate():
            with requests.get(stream_url, headers=headers, stream=True) as r:
                r.raise_for_status()
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        yield chunk
        
        content_type = 'audio/mp4' if quality == 'audio' else 'video/mp4'
        return Response(
            stream_with_context(generate()),
            headers={
                'Content-Type': content_type,
                'Content-Disposition': f'inline; filename="{stream_info["title"]}.{stream_info["format"]}"'
            }
        )
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/')
def home():
    return """
    <h1>🎵 YouTube Audio/Video Downloader & Streaming API</h1>
    <p>Use this API to search, download, and stream audio/video from YouTube.</p>
    
    <h2>📡 Endpoints:</h2>
    <ul>
        <li><strong>GET /search</strong> - Search for a video by title<br>
            <code>?title=query</code></li>
        <li><strong>GET /download</strong> - Download audio by URL or title<br>
            <code>?url=URL</code> or <code>?title=query</code></li>
        <li><strong>GET /vdown</strong> - Download video (240p) by URL or title<br>
            <code>?url=URL</code> or <code>?title=query</code></li>
        <li><strong>GET /stream</strong> - Get streaming URL/info for real-time playback ⭐ NEW<br>
            <code>?url=URL</code> or <code>?title=query</code><br>
            Optional: <code>&quality=audio|video</code> | <code>&type=info|url</code></li>
        <li><strong>GET /stream/download</strong> - Direct proxy streaming endpoint ⭐ NEW<br>
            <code>?url=URL</code> or <code>?title=query</code><br>
            Optional: <code>&quality=audio|video</code></li>
    </ul>
    
    <h2>🎯 For Telegram Music Bot:</h2>
    <p>Use <code>/stream?title=Song%20Name&type=url</code> to get direct streaming URL:<br>
    <pre>{
  "stream_url": "https://rr2---...",
  "title": "Song Name",
  "duration": 210,
  "format": "m4a"
}</pre></p>
    
    <h2>📝 Examples:</h2>
    <ul>
        <li>Search: <code>/search?title=Imagine%20Dragons%20Believer</code></li>
        <li>Download Audio: <code>/download?url=https://youtu.be/...</code></li>
        <li>Download Video: <code>/vdown?title=Despacito</code></li>
        <li><strong>Get Stream URL:</strong> <code>/stream?title=Shape%20of%20You&type=url</code></li>
        <li><strong>Proxy Stream:</strong> <code>/stream/download?title=Blinding%20Lights</code></li>
        <li>Spotify Support: <code>/download?url=https://open.spotify.com/track/...</code></li>
    </ul>
    
    <h3>💡 Telegram Bot Integration:</h3>
    <pre>
# In your Telegram music bot:
async def play_song(query):
    response = await api.get(f"/stream?title={query}&type=url")
    data = response.json()
    stream_url = data["stream_url"]
    # Use this URL with youtube-dl or ffmpeg to play
    </pre>
    """

if __name__ == '__main__':
    try:
        from pyngrok import ngrok
        port = 5000
        public_url = ngrok.connect(port, "http")
        print(f"\n👉  ngrok tunnel: {public_url}\n")
    except Exception as e:
        print(f"\n⚠️  ngrok error: {e}")
        print("Running without ngrok tunnel...\n")
    
    app.run(host='0.0.0.0', port=5000, debug=False)

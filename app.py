# youtube_api_server.py
import os
import uuid
import asyncio
from pathlib import Path
from typing import Dict, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp as youtube_dl
import uvicorn

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Config
DOWNLOAD_DIR = Path("downloads")
DOWNLOAD_DIR.mkdir(exist_ok=True)

COOKIES_FILE = "cookies.txt"  # Your cookies file path

# Store active downloads
active_downloads: Dict[str, dict] = {}

class DownloadRequest(BaseModel):
    url: str
    type: str = "audio"

@app.get("/download")
async def start_download(url: str, type: str = "audio"):
    """Start download and return token"""
    video_id = url
    
    # Generate unique token
    token = str(uuid.uuid4())[:8]
    
    # Store download info
    active_downloads[token] = {
        "video_id": video_id,
        "type": type,
        "status": "downloading",
        "file_path": None
    }
    
    # Start background download
    asyncio.create_task(download_video(video_id, type, token))
    
    return JSONResponse({
        "download_token": token,
        "video_id": video_id,
        "message": "Download started"
    })

@app.get("/stream/{video_id}")
async def stream_video(video_id: str, type: str, token: str):
    """Stream the downloaded file"""
    
    # Verify token
    if token not in active_downloads:
        return JSONResponse({"error": "Invalid token"}, status_code=401)
    
    download_info = active_downloads[token]
    
    # Check if download is complete
    if download_info["status"] != "completed":
        return JSONResponse({
            "status": download_info["status"],
            "message": "Download in progress"
        }, status_code=202)
    
    # Get file path
    file_path = download_info["file_path"]
    
    if not file_path or not Path(file_path).exists():
        return JSONResponse({"error": "File not found"}, status_code=404)
    
    # Return file for download
    return FileResponse(
        path=file_path,
        filename=Path(file_path).name,
        media_type="audio/mpeg" if type == "audio" else "video/mp4"
    )

@app.get("/live")
async def get_live_stream(url: str):
    """Get live stream URL"""
    video_id = url
    
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'cookiefile': COOKIES_FILE,  # Use cookies
    }
    
    try:
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(f"https://youtube.com/watch?v={video_id}", download=False)
            
            # Get live stream URL
            if info.get('is_live'):
                formats = info.get('formats', [])
                for f in formats:
                    if f.get('protocol') in ['m3u8', 'm3u8_native']:
                        return JSONResponse({
                            "stream_url": f.get('url'),
                            "video_id": video_id
                        })
            
            return JSONResponse({"error": "Not a live stream"}, status_code=400)
            
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

async def download_video(video_id: str, file_type: str, token: str):
    """Background download task"""
    
    ext = "mp3" if file_type == "audio" else "mp4"
    output_path = DOWNLOAD_DIR / f"{video_id}.{ext}"
    
    # Configure yt-dlp options
    if file_type == "audio":
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
            'outtmpl': str(DOWNLOAD_DIR / f'{video_id}.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'cookiefile': COOKIES_FILE,  # IMPORTANT: Use cookies
            'retries': 10,
            'fragment_retries': 10,
        }
    else:
        ydl_opts = {
            'format': 'best[height<=1080][ext=mp4]/best[height<=1080]/best',
            'outtmpl': str(DOWNLOAD_DIR / f'{video_id}.%(ext)s'),
            'quiet': True,
            'no_warnings': True,
            'cookiefile': COOKIES_FILE,  # IMPORTANT: Use cookies
            'retries': 10,
            'fragment_retries': 10,
        }
    
    try:
        # Run download in thread pool
        loop = asyncio.get_event_loop()
        
        def download():
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                url = f"https://www.youtube.com/watch?v={video_id}"
                ydl.extract_info(url, download=True)
        
        await loop.run_in_executor(None, download)
        
        # Find the downloaded file
        downloaded_file = None
        for f in DOWNLOAD_DIR.glob(f"{video_id}.*"):
            if f.suffix in ['.mp3', '.mp4', '.webm', '.m4a']:
                downloaded_file = f
                break
        
        # Rename if needed
        if downloaded_file and downloaded_file.suffix != f".{ext}":
            new_path = DOWNLOAD_DIR / f"{video_id}.{ext}"
            downloaded_file.rename(new_path)
            downloaded_file = new_path
        
        # Update status
        active_downloads[token]["status"] = "completed"
        active_downloads[token]["file_path"] = str(downloaded_file)
        
        print(f"✅ Downloaded: {video_id}.{ext}")
        
    except Exception as e:
        print(f"❌ Download failed: {e}")
        active_downloads[token]["status"] = "failed"

# Cookies.txt format sahi hai ya nahi check karne ke liye
@app.get("/check-cookies")
async def check_cookies():
    """Check if cookies file is working"""
    try:
        ydl_opts = {
            'quiet': True,
            'cookiefile': COOKIES_FILE,
            'extract_flat': True,
        }
        
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            # Try to fetch a video info
            info = ydl.extract_info("https://www.youtube.com/watch?v=dQw4w9WgXcQ", download=False)
            
            return JSONResponse({
                "status": "success",
                "message": "Cookies working!",
                "video_title": info.get('title')
            })
    except Exception as e:
        return JSONResponse({
            "status": "error",
            "message": f"Cookies not working: {str(e)}"
        }, status_code=500)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

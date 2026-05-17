from flask import Flask, request, jsonify, Response, stream_with_context
import yt_dlp
import requests
import secrets
import time
import os

app = Flask(__name__)

# =========================
# CONFIG
# =========================

COOKIES_FILE = "cookies.txt"

TOKEN_EXPIRY = 600  # 10 minutes

TOKENS = {}

# =========================
# TOKEN SYSTEM
# =========================

def cleanup_tokens():
    now = time.time()

    expired = []

    for token, data in TOKENS.items():
        if now > data["expires"]:
            expired.append(token)

    for token in expired:
        del TOKENS[token]


def generate_token():
    return (
        "ShrutiMusic"
        + secrets.token_urlsafe(64)
        + "ShrutiBots"
    )


# =========================
# VIDEO ID
# =========================

def get_video_id(url):
    if "v=" in url:
        return url.split("v=")[-1].split("&")[0]

    if "youtu.be/" in url:
        return url.split("youtu.be/")[-1].split("?")[0]

    return url


# =========================
# STREAM EXTRACTION
# =========================

def extract_stream(video_url, media_type="audio"):

    ydl_opts = {
        "quiet": True,
        "noplaylist": True,
        "cookiefile": COOKIES_FILE,

        # IMPORTANT
        "extractor_args": {
            "youtube": {
                "player_client": [
                    "android",
                    "web"
                ]
            }
        },

        "socket_timeout": 30,
        "retries": 3,
        "fragment_retries": 3,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:

        info = ydl.extract_info(
            video_url,
            download=False
        )

        formats = info.get("formats", [])

        # =========================
        # AUDIO
        # =========================

        if media_type == "audio":

            audio_formats = []

            for f in formats:

                if (
                    f.get("acodec") != "none"
                    and f.get("vcodec") == "none"
                    and f.get("url")
                ):

                    audio_formats.append(f)

            if not audio_formats:
                raise Exception(
                    "No audio stream found"
                )

            # Prefer m4a
            audio_formats.sort(
                key=lambda x: (
                    x.get("ext") != "m4a",
                    -(x.get("abr") or 0)
                )
            )

            best = audio_formats[0]

        # =========================
        # VIDEO
        # =========================

        else:

            video_formats = []

            for f in formats:

                if (
                    f.get("vcodec") != "none"
                    and f.get("acodec") != "none"
                    and f.get("height")
                    and f.get("height") <= 360
                    and f.get("url")
                ):

                    video_formats.append(f)

            if not video_formats:
                raise Exception(
                    "No video stream found"
                )

            video_formats.sort(
                key=lambda x: (
                    -(x.get("height") or 0)
                )
            )

            best = video_formats[0]

        return {

            "stream_url": best["url"],

            "title": info.get("title"),

            "duration": info.get("duration"),

            "thumbnail": info.get("thumbnail"),

            "video_id": info.get("id"),

            "format": best.get("ext"),

            "filesize": best.get("filesize"),

            "quality": media_type,
        }


# =========================
# HOME
# =========================

@app.route("/")
def home():

    return jsonify({

        "status": "running",

        "service": "Shruti Style Stream API",

        "developer": "Custom"

    })


# =========================
# DOWNLOAD TOKEN
# =========================

@app.route("/download")
def download():

    cleanup_tokens()

    video_url = request.args.get("url")

    media_type = request.args.get(
        "type",
        "audio"
    )

    if not video_url:

        return jsonify({

            "error": "url parameter required"

        }), 400

    try:

        video_id = get_video_id(video_url)

        token = generate_token()

        TOKENS[token] = {

            "video_url": video_url,

            "type": media_type,

            "expires": time.time()
            + TOKEN_EXPIRY,
        }

        return jsonify({

            "status": "success",

            "video_id": video_id,

            "download_token": token,

            "usage":
            "Use token in X-Download-Token header"

        })

    except Exception as e:

        return jsonify({

            "error": str(e)

        }), 500


# =========================
# STREAM
# =========================

@app.route("/stream/<video_id>")
def stream(video_id):

    cleanup_tokens()

    token = request.headers.get(
        "X-Download-Token"
    )

    if not token:

        return jsonify({

            "error":
            "Missing X-Download-Token"

        }), 403

    if token not in TOKENS:

        return jsonify({

            "error": "Invalid token"

        }), 403

    token_data = TOKENS[token]

    if time.time() > token_data["expires"]:

        del TOKENS[token]

        return jsonify({

            "error": "Token expired"

        }), 403

    video_url = token_data["video_url"]

    media_type = request.args.get(
        "type",
        token_data["type"]
    )

    try:

        stream_info = extract_stream(
            video_url,
            media_type
        )

        youtube_stream_url = (
            stream_info["stream_url"]
        )

        headers = {

            "User-Agent":
            (
                "Mozilla/5.0 "
                "(Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            ),

            "Referer":
            "https://www.youtube.com/"
        }

        # =========================
        # CHUNK STREAMING
        # =========================

        def generate():

            with requests.get(
                youtube_stream_url,
                headers=headers,
                stream=True
            ) as r:

                r.raise_for_status()

                for chunk in r.iter_content(
                    chunk_size=16384
                ):

                    if chunk:
                        yield chunk

        content_type = (

            "audio/mp4"

            if media_type == "audio"

            else "video/mp4"
        )

        return Response(

            stream_with_context(
                generate()
            ),

            headers={

                "Content-Type":
                content_type,

                "Content-Disposition":
                (
                    f'inline; filename="{video_id}"'
                )
            }
        )

    except Exception as e:

        return jsonify({

            "error": str(e)

        }), 500


# =========================
# INFO
# =========================

@app.route("/info")
def info():

    video_url = request.args.get("url")

    media_type = request.args.get(
        "type",
        "audio"
    )

    if not video_url:

        return jsonify({

            "error":
            "url parameter required"

        }), 400

    try:

        data = extract_stream(
            video_url,
            media_type
        )

        return jsonify({

            "status": "success",

            **data
        })

    except Exception as e:

        return jsonify({

            "error": str(e)

        }), 500


# =========================
# RUN
# =========================

if __name__ == "__main__":

    port = int(
        os.environ.get("PORT", 5000)
    )

    app.run(
        host="0.0.0.0",
        port=port
    )

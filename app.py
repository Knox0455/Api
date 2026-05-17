from flask import Flask, request, jsonify, Response
import yt_dlp
import requests
import secrets
import time

app = Flask(__name__)

TOKENS = {}

YDL_OPTS = {
    "quiet": True,
    "nocheckcertificate": True,
    "geo_bypass": True,
    "noplaylist": True,
    "extractor_args": {
        "youtube": {
            "player_client": ["android", "web"]
        }
    },
}

# =========================
# TOKEN GENERATOR
# =========================

def generate_token(video_id: str):
    token = (
        "ShrutiMusic"
        + secrets.token_urlsafe(64)
        + "ShrutiBots"
    )

    TOKENS[token] = {
        "video_id": video_id,
        "time": time.time()
    }

    return token


def verify_token(video_id: str, token: str):

    if token not in TOKENS:
        return False

    data = TOKENS[token]

    if data["video_id"] != video_id:
        return False

    # 10 min expiry
    if time.time() - data["time"] > 600:
        del TOKENS[token]
        return False

    return True


# =========================
# SEARCH
# =========================

@app.route("/search")
def search():

    title = request.args.get("title")

    if not title:
        return jsonify({
            "error": "title required"
        }), 400

    try:

        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:

            info = ydl.extract_info(
                f"ytsearch1:{title}",
                download=False
            )

        if not info or not info.get("entries"):
            return jsonify({
                "error": "No results"
            }), 404

        data = info["entries"][0]

        return jsonify({
            "title": data.get("title"),
            "url": f"https://youtube.com/watch?v={data.get('id')}",
            "duration": data.get("duration"),
            "video_id": data.get("id"),
            "thumbnail": data.get("thumbnail")
        })

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500


# =========================
# DOWNLOAD TOKEN
# =========================

@app.route("/download")
def download():

    video_url = request.args.get("url")
    file_type = request.args.get(
        "type",
        "audio"
    )

    if not video_url:
        return jsonify({
            "error": "url required"
        }), 400

    try:

        if "youtube.com" in video_url:
            video_id = (
                video_url.split("v=")[-1]
                .split("&")[0]
            )

        elif "youtu.be" in video_url:
            video_id = (
                video_url.split("/")[-1]
                .split("?")[0]
            )

        else:
            video_id = video_url

        token = generate_token(video_id)

        return jsonify({
            "status": "success",
            "video_id": video_id,
            "download_token": token,
            "type": file_type,
            "usage": (
                "Use token in "
                "/stream/<video_id>?"
                "type=audio&token=TOKEN"
            )
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

    file_type = request.args.get(
        "type",
        "audio"
    )

    token = request.args.get("token")

    if not token:
        return jsonify({
            "error": "token required"
        }), 403

    if not verify_token(video_id, token):
        return jsonify({
            "error": "invalid token"
        }), 403

    try:

        youtube_url = (
            f"https://youtube.com/watch?v="
            f"{video_id}"
        )

        ydl_opts = {
            **YDL_OPTS
        }

        if file_type == "video":

            ydl_opts["format"] = (
                "bestvideo[height<=720]+"
                "bestaudio/"
                "best"
            )

        else:

            ydl_opts["format"] = (
                "bestaudio/"
                "best"
            )

        with yt_dlp.YoutubeDL(
            ydl_opts
        ) as ydl:

            info = ydl.extract_info(
                youtube_url,
                download=False
            )

        stream_url = None

        if info.get("url"):

            stream_url = info["url"]

        else:

            formats = info.get(
                "formats",
                []
            )

            for fmt in formats:

                if file_type == "audio":

                    if (
                        fmt.get("acodec") != "none"
                        and
                        fmt.get("url")
                    ):

                        stream_url = fmt["url"]
                        break

                else:

                    if (
                        fmt.get("vcodec") != "none"
                        and
                        fmt.get("url")
                    ):

                        stream_url = fmt["url"]
                        break

        if not stream_url:

            return jsonify({
                "error": "No stream found"
            }), 404

        headers = {
            "User-Agent": (
                "Mozilla/5.0"
            ),
            "Referer": (
                "https://youtube.com/"
            )
        }

        r = requests.get(
            stream_url,
            headers=headers,
            stream=True
        )

        content_type = (
            "video/mp4"
            if file_type == "video"
            else "audio/mp4"
        )

        return Response(
            r.iter_content(
                chunk_size=1024 * 64
            ),
            content_type=content_type,
            headers={
                "Accept-Ranges": "bytes"
            }
        )

    except Exception as e:

        return jsonify({
            "error": str(e)
        }), 500


# =========================
# HOME
# =========================

@app.route("/")
def home():

    return """
    <h1>YouTube API Running</h1>

    <h3>Search</h3>
    <pre>/search?title=believer</pre>

    <h3>Get Token</h3>
    <pre>/download?url=dQw4w9WgXcQ&type=audio</pre>

    <h3>Stream</h3>
    <pre>
/stream/dQw4w9WgXcQ?type=audio&token=TOKEN
    </pre>
    """


# =========================
# START
# =========================

if __name__ == "__main__":

    port = 5000

    import os

    if os.environ.get("PORT"):
        port = int(
            os.environ.get("PORT")
        )

    app.run(
        host="0.0.0.0",
        port=port
    )

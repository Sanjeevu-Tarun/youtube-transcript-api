from flask import Flask, jsonify, request
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
import urllib.request
import urllib.parse
import json
import re

app = Flask(__name__)

def search_youtube_video_id(query):
    """Search YouTube for a video and return the first video ID."""
    encoded = urllib.parse.quote(query + " review")
    url = f"https://www.youtube.com/results?search_query={encoded}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    })
    html = urllib.request.urlopen(req, timeout=8).read().decode("utf-8")
    # YouTube embeds video IDs in the search results page
    video_ids = re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html)
    # Return unique IDs, skip YouTube Shorts (they rarely have transcripts)
    seen = set()
    result = []
    for vid in video_ids:
        if vid not in seen:
            seen.add(vid)
            result.append(vid)
    return result[:5]  # top 5 candidates

def fetch_transcript_for_video(video_id):
    """Try to fetch transcript for a video ID. Returns text or empty string."""
    try:
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)

        fetched = None
        try:
            fetched = transcript_list.find_manually_created_transcript(
                ["en", "en-US", "en-GB"]
            ).fetch()
        except Exception:
            pass

        if fetched is None:
            try:
                fetched = transcript_list.find_generated_transcript(
                    ["en", "en-US", "en-GB"]
                ).fetch()
            except Exception:
                pass

        if fetched is None:
            try:
                for t in transcript_list:
                    if t.is_generated:
                        fetched = t.fetch()
                        break
            except Exception:
                pass

        if fetched is None:
            return ""

        parts = []
        for snippet in fetched:
            try:
                parts.append(snippet.text)
            except AttributeError:
                parts.append(snippet.get("text", ""))

        return " ".join(parts)[:5000]

    except (TranscriptsDisabled, NoTranscriptFound):
        return ""
    except Exception:
        return ""


@app.route("/transcript")
def transcript():
    """
    Two modes:
      1. ?videoId=XXX  — fetch transcript for a specific video
      2. ?device=Samsung+S25+Ultra  — search YouTube, fetch first available transcript
    """
    video_id = request.args.get("videoId")
    device   = request.args.get("device")

    if not video_id and not device:
        return jsonify({"error": "Provide videoId or device param"}), 400

    # Mode 1: direct video ID
    if video_id:
        text = fetch_transcript_for_video(video_id)
        return jsonify({"transcript": text, "videoId": video_id})

    # Mode 2: device name search
    try:
        video_ids = search_youtube_video_id(device)
    except Exception as e:
        return jsonify({"transcript": "", "error": f"Search failed: {str(e)}"}), 200

    if not video_ids:
        return jsonify({"transcript": "", "error": "No videos found"}), 200

    # Try each video until we get a transcript
    for vid in video_ids:
        text = fetch_transcript_for_video(vid)
        if text:
            return jsonify({"transcript": text, "videoId": vid})

    return jsonify({"transcript": "", "error": "No transcripts available for top results"}), 200


@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
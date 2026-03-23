from flask import Flask, jsonify, request
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
import urllib.request
import urllib.parse
import json
import os

app = Flask(__name__)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")


def search_youtube_video_ids(query: str, max_results: int = 5) -> list:
    """Search YouTube Data API v3 for video IDs matching the query."""
    if not YOUTUBE_API_KEY:
        return []
    params = urllib.parse.urlencode({
        "part": "snippet",
        "q": query,
        "type": "video",
        "maxResults": max_results,
        "relevanceLanguage": "en",
        "order": "relevance",
        "key": YOUTUBE_API_KEY
    })
    url = f"https://www.googleapis.com/youtube/v3/search?{params}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        resp = urllib.request.urlopen(req, timeout=8)
        data = json.loads(resp.read().decode("utf-8"))
        return [
            {"videoId": item["id"]["videoId"], "title": item["snippet"]["title"],
             "channel": item["snippet"]["channelTitle"]}
            for item in data.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
    except Exception as e:
        print(f"YouTube API search failed for '{query}': {e}")
        return []


def fetch_transcript(video_id: str) -> str:
    """Fetch transcript for a video ID. Returns text or empty string."""
    try:
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)

        fetched = None
        # Priority 1: manual English
        try:
            fetched = transcript_list.find_manually_created_transcript(
                ["en", "en-US", "en-GB"]
            ).fetch()
        except Exception:
            pass

        # Priority 2: auto-generated English
        if fetched is None:
            try:
                fetched = transcript_list.find_generated_transcript(
                    ["en", "en-US", "en-GB"]
                ).fetch()
            except Exception:
                pass

        # Priority 3: any auto-generated
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
    except Exception as e:
        print(f"Transcript fetch failed for {video_id}: {e}")
        return ""


@app.route("/transcript")
def transcript():
    """
    Two modes:
      1. ?videoId=XXX         — fetch transcript for a specific video ID
      2. ?device=Samsung+S25+Ultra — search YouTube API, return first transcript found
    """
    video_id = request.args.get("videoId")
    device   = request.args.get("device")

    if not video_id and not device:
        return jsonify({"error": "Provide videoId or device param"}), 400

    # Mode 1: direct video ID
    if video_id:
        text = fetch_transcript(video_id)
        return jsonify({"transcript": text, "videoId": video_id})

    # Mode 2: device name — search YouTube API then fetch transcripts
    if not YOUTUBE_API_KEY:
        return jsonify({
            "transcript": "",
            "error": "YOUTUBE_API_KEY not set on server"
        }), 200

    videos = search_youtube_video_ids(f"{device} review", max_results=5)

    if not videos:
        return jsonify({"transcript": "", "error": "No videos found"}), 200

    # Try each video until we get a transcript
    for video in videos:
        vid = video["videoId"]
        text = fetch_transcript(vid)
        if text:
            return jsonify({
                "transcript": text,
                "videoId": vid,
                "title": video.get("title", ""),
                "channel": video.get("channel", "")
            })

    return jsonify({
        "transcript": "",
        "error": f"No captions available for top {len(videos)} results"
    }), 200


@app.route("/health")
def health():
    api_key_set = bool(YOUTUBE_API_KEY)
    return jsonify({"status": "ok", "youtube_api_key_set": api_key_set}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
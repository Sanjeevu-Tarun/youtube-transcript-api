from flask import Flask, jsonify, request
import urllib.request
import urllib.parse
import json
import os
import re
import xml.etree.ElementTree as ET

app = Flask(__name__)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

# YouTube Innertube API — same API the official YouTube app uses internally.
# Not blocked by YouTube because it looks like a real client request.
INNERTUBE_API_KEY = "AIzaSyAO_FJ2SlqU8Q4STEHLGCilw_Y9_11qcW8"
INNERTUBE_URL = f"https://www.youtube.com/youtubei/v1/player?key={INNERTUBE_API_KEY}"
INNERTUBE_HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}
INNERTUBE_CONTEXT = {
    "client": {
        "clientName": "ANDROID",
        "clientVersion": "18.11.34",
        "androidSdkVersion": 34,
        "hl": "en",
        "gl": "US"
    }
}


def get_caption_url(video_id: str) -> str | None:
    """Use Innertube to get the caption track URL for a video."""
    payload = json.dumps({
        "videoId": video_id,
        "context": INNERTUBE_CONTEXT,
        "contentCheckOk": True,
        "racyCheckOk": True
    }).encode("utf-8")

    req = urllib.request.Request(
        INNERTUBE_URL,
        data=payload,
        headers=INNERTUBE_HEADERS,
        method="POST"
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        print(f"Innertube request failed for {video_id}: {e}")
        return None

    # Navigate: captions → playerCaptionsTracklistRenderer → captionTracks
    try:
        tracks = (
            data
            .get("captions", {})
            .get("playerCaptionsTracklistRenderer", {})
            .get("captionTracks", [])
        )
    except Exception:
        return None

    if not tracks:
        return None

    # Prefer English manual, then English auto-generated, then any
    def score(track):
        lang = track.get("languageCode", "")
        kind = track.get("kind", "")
        if lang.startswith("en") and kind != "asr":
            return 0   # manual English — best
        if lang.startswith("en") and kind == "asr":
            return 1   # auto English
        if kind == "asr":
            return 2   # auto other language
        return 3

    tracks.sort(key=score)
    best = tracks[0]
    url = best.get("baseUrl", "")
    return url if url else None


def fetch_caption_text(caption_url: str) -> str:
    """Download and parse caption XML into plain text."""
    try:
        # Add fmt=json3 for structured response, fallback to XML
        url = caption_url + "&fmt=json3" if "?" in caption_url else caption_url + "?fmt=json3"
        req = urllib.request.Request(url, headers={"User-Agent": INNERTUBE_HEADERS["User-Agent"]})
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8")

        # Try JSON format first
        try:
            data = json.loads(raw)
            events = data.get("events", [])
            parts = []
            for event in events:
                for seg in event.get("segs", []):
                    text = seg.get("utf8", "").strip()
                    if text and text != "\n":
                        parts.append(text)
            text = " ".join(parts)
            if text:
                return text[:5000]
        except json.JSONDecodeError:
            pass

        # Fallback: parse XML
        root = ET.fromstring(raw)
        parts = []
        for elem in root.iter("text"):
            t = (elem.text or "").strip()
            if t:
                parts.append(t)
        return " ".join(parts)[:5000]

    except Exception as e:
        print(f"Caption fetch failed: {e}")
        return ""


def get_transcript(video_id: str) -> tuple:
    """Returns (transcript_text, status)."""
    caption_url = get_caption_url(video_id)
    if not caption_url:
        return "", "no_caption_track_found"
    text = fetch_caption_text(caption_url)
    if text:
        return text, "success"
    return "", "caption_fetch_failed"


def search_youtube(query: str, max_results: int = 10) -> list:
    """Search YouTube Data API v3."""
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
    try:
        req = urllib.request.Request(
            f"https://www.googleapis.com/youtube/v3/search?{params}",
            headers={"Accept": "application/json"}
        )
        resp = urllib.request.urlopen(req, timeout=8)
        data = json.loads(resp.read().decode("utf-8"))
        return [
            {
                "videoId": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"]
            }
            for item in data.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
    except Exception as e:
        print(f"YouTube search failed: {e}")
        return []


@app.route("/transcript")
def transcript():
    video_id = request.args.get("videoId")
    device   = request.args.get("device")
    debug    = request.args.get("debug", "false").lower() == "true"

    if not video_id and not device:
        return jsonify({"error": "Provide videoId or device param"}), 400

    # Mode 1: direct video ID via Innertube
    if video_id:
        text, status = get_transcript(video_id)
        result = {"transcript": text, "videoId": video_id}
        if debug or not text:
            result["status"] = status
        return jsonify(result)

    # Mode 2: search then fetch
    videos = search_youtube(f"{device} review", max_results=10)
    if not videos:
        return jsonify({"transcript": "", "error": "No videos found"}), 200

    tried = []
    for video in videos:
        vid = video["videoId"]
        text, status = get_transcript(vid)
        tried.append({**video, "status": status, "transcript_length": len(text)})
        if text:
            result = {
                "transcript": text,
                "videoId": vid,
                "title": video.get("title", ""),
                "channel": video.get("channel", "")
            }
            if debug:
                result["all_tried"] = tried
            return jsonify(result)

    return jsonify({
        "transcript": "",
        "error": f"No captions in top {len(videos)} results",
        "videos_tried": tried if debug else []
    }), 200


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "youtube_api_key_set": bool(YOUTUBE_API_KEY),
        "innertube": "enabled"
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
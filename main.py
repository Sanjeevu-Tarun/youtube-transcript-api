from flask import Flask, jsonify, request
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound
import urllib.request
import urllib.parse
import json
import os

app = Flask(__name__)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")


def search_youtube_video_ids(query: str, max_results: int = 10) -> list:
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
            {
                "videoId": item["id"]["videoId"],
                "title": item["snippet"]["title"],
                "channel": item["snippet"]["channelTitle"]
            }
            for item in data.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
    except Exception as e:
        print(f"YouTube API search failed for '{query}': {e}")
        return []


def fetch_transcript(video_id: str) -> tuple:
    """
    Returns (transcript_text, status_reason).
    Tries manual English → auto English → any auto-generated.
    """
    try:
        ytt_api = YouTubeTranscriptApi()
        transcript_list = ytt_api.list(video_id)

        # List all available transcripts for debugging
        available = []
        try:
            for t in transcript_list:
                available.append(f"{t.language_code}(generated={t.is_generated})")
        except Exception:
            pass

        fetched = None
        method = ""

        try:
            fetched = transcript_list.find_manually_created_transcript(
                ["en", "en-US", "en-GB"]
            ).fetch()
            method = "manual-en"
        except Exception:
            pass

        if fetched is None:
            try:
                fetched = transcript_list.find_generated_transcript(
                    ["en", "en-US", "en-GB"]
                ).fetch()
                method = "auto-en"
            except Exception:
                pass

        if fetched is None:
            try:
                for t in transcript_list:
                    if t.is_generated:
                        fetched = t.fetch()
                        method = f"auto-{t.language_code}"
                        break
            except Exception:
                pass

        if fetched is None:
            return "", f"no_captions_available(found: {available})"

        parts = []
        for snippet in fetched:
            try:
                parts.append(snippet.text)
            except AttributeError:
                parts.append(snippet.get("text", ""))

        text = " ".join(parts)[:5000]
        return text, f"success-{method}"

    except TranscriptsDisabled:
        return "", "transcripts_disabled"
    except NoTranscriptFound:
        return "", "no_transcript_found"
    except Exception as e:
        return "", f"error: {str(e)}"


@app.route("/transcript")
def transcript():
    video_id = request.args.get("videoId")
    device   = request.args.get("device")
    debug    = request.args.get("debug", "false").lower() == "true"

    if not video_id and not device:
        return jsonify({"error": "Provide videoId or device param"}), 400

    # Mode 1: direct video ID
    if video_id:
        text, reason = fetch_transcript(video_id)
        result = {"transcript": text, "videoId": video_id}
        if debug or not text:
            result["reason"] = reason
        return jsonify(result)

    # Mode 2: device name search
    if not YOUTUBE_API_KEY:
        return jsonify({"transcript": "", "error": "YOUTUBE_API_KEY not set"}), 200

    videos = search_youtube_video_ids(f"{device} review", max_results=10)

    if not videos:
        return jsonify({"transcript": "", "error": "YouTube API returned no videos"}), 200

    results = []
    for video in videos:
        vid = video["videoId"]
        text, reason = fetch_transcript(vid)
        results.append({
            "videoId": vid,
            "title": video.get("title", ""),
            "channel": video.get("channel", ""),
            "transcript_length": len(text),
            "reason": reason,
            "transcript_preview": text[:200] if text else ""
        })
        if text:
            # Found one — return it (with debug info if requested)
            response = {
                "transcript": text,
                "videoId": vid,
                "title": video.get("title", ""),
                "channel": video.get("channel", "")
            }
            if debug:
                response["all_tried"] = results
            return jsonify(response)

    # None worked — return full debug info so we can see WHY
    return jsonify({
        "transcript": "",
        "error": f"No captions in top {len(videos)} results",
        "videos_tried": results
    }), 200


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "youtube_api_key_set": bool(YOUTUBE_API_KEY)
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
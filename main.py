from flask import Flask, jsonify, request
import urllib.request
import urllib.parse
import json
import os
import re
import xml.etree.ElementTree as ET
import html as html_lib

app = Flask(__name__)

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def get_caption_url_from_watch_page(video_id: str) -> str | None:
    """
    Fetch the YouTube watch page and extract caption track URL directly.
    The watch page HTML contains ytInitialPlayerResponse with captionTracks.
    This approach does not require PO tokens or Innertube API keys.
    """
    url = f"https://www.youtube.com/watch?v={video_id}&hl=en"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=12)
        html = resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"Watch page fetch failed for {video_id}: {e}")
        return None

    # Extract ytInitialPlayerResponse JSON from the page
    match = re.search(r'ytInitialPlayerResponse\s*=\s*(\{.+?\});(?:\s*</script>|\s*var\s)', html)
    if not match:
        # Fallback: broader match
        match = re.search(r'ytInitialPlayerResponse\s*=\s*(\{.+)', html)
        if not match:
            print(f"ytInitialPlayerResponse not found for {video_id}")
            return None

    try:
        # Find balanced JSON by counting braces
        raw = match.group(1)
        depth = 0
        end = 0
        for i, ch in enumerate(raw):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        player_data = json.loads(raw[:end])
    except Exception as e:
        print(f"JSON parse failed for {video_id}: {e}")
        return None

    tracks = (
        player_data
        .get("captions", {})
        .get("playerCaptionsTracklistRenderer", {})
        .get("captionTracks", [])
    )

    if not tracks:
        print(f"No captionTracks in player response for {video_id}")
        return None

    # Score: manual English = 0, auto English = 1, auto other = 2, rest = 3
    def score(t):
        lang = t.get("languageCode", "")
        kind = t.get("kind", "")
        if lang.startswith("en") and kind != "asr": return 0
        if lang.startswith("en") and kind == "asr": return 1
        if kind == "asr": return 2
        return 3

    tracks.sort(key=score)
    base_url = tracks[0].get("baseUrl", "")
    print(f"Found {len(tracks)} tracks for {video_id}, using lang={tracks[0].get('languageCode')} kind={tracks[0].get('kind','')}")
    return base_url if base_url else None


def fetch_caption_text(caption_url: str) -> str:
    """Download and parse caption XML/JSON into plain text."""
    try:
        # Request JSON3 format
        url = caption_url + ("&fmt=json3" if "?" in caption_url else "?fmt=json3")
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8", errors="replace")

        # Try JSON3 format
        try:
            data = json.loads(raw)
            events = data.get("events", [])
            parts = []
            for event in events:
                for seg in event.get("segs", []):
                    text = seg.get("utf8", "").strip()
                    if text and text != "\n":
                        parts.append(text)
            text = " ".join(parts).strip()
            if text:
                return text[:5000]
        except json.JSONDecodeError:
            pass

        # Fallback: XML
        try:
            root = ET.fromstring(raw)
            parts = [html_lib.unescape(elem.text or "").strip() for elem in root.iter("text")]
            return " ".join(p for p in parts if p)[:5000]
        except Exception:
            pass

        return ""
    except Exception as e:
        print(f"Caption fetch failed: {e}")
        return ""


def get_transcript(video_id: str) -> tuple:
    """Returns (transcript_text, status)."""
    caption_url = get_caption_url_from_watch_page(video_id)
    if not caption_url:
        return "", "no_caption_track_found"
    text = fetch_caption_text(caption_url)
    if text:
        return text, "success"
    return "", "caption_fetch_failed"


def search_youtube(query: str, max_results: int = 10) -> list:
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

    # Mode 1: direct video ID
    if video_id:
        text, status = get_transcript(video_id)
        result = {"transcript": text, "videoId": video_id}
        if debug or not text:
            result["status"] = status
        return jsonify(result)

    # Mode 2: device name search
    videos = search_youtube(f"{device} review", max_results=10)
    if not videos:
        return jsonify({"transcript": "", "error": "No videos found"}), 200

    tried = []
    for video in videos:
        vid = video["videoId"]
        text, status = get_transcript(vid)
        tried.append({**video, "status": status, "length": len(text)})
        if text:
            result = {
                "transcript": text,
                "videoId": vid,
                "title": video.get("title", ""),
                "channel": video.get("channel", "")
            }
            if debug:
                result["tried"] = tried
            return jsonify(result)

    return jsonify({
        "transcript": "",
        "error": f"No captions in top {len(videos)} results",
        "videos_tried": tried
    }), 200


@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "youtube_api_key_set": bool(YOUTUBE_API_KEY),
        "method": "watch_page_scrape"
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
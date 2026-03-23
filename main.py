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

# SOCS cookie bypasses YouTube's GDPR consent gate
# This is the same cookie a browser sets after clicking "Accept all"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Cookie": "SOCS=CAISNQgDEitib3FfaWRlbnRpdHlmcm9udGVuZHVpc2VydmVyXzIwMjMwODI5LjA3X3AwGgJlbiACIAME; CONSENT=YES+cb; GPS=1; YSC=x1234; VISITOR_INFO1_LIVE=x1234",
}


def fetch_watch_page(video_id: str) -> str | None:
    url = f"https://www.youtube.com/watch?v={video_id}&hl=en&gl=US"
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=12)
        return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"Watch page fetch failed for {video_id}: {e}")
        return None


def extract_caption_url(html: str, video_id: str) -> str | None:
    # Find ytInitialPlayerResponse
    match = re.search(r'ytInitialPlayerResponse\s*=\s*(\{)', html)
    if not match:
        print(f"ytInitialPlayerResponse not found for {video_id}")
        return None

    # Extract balanced JSON
    start = match.start(1)
    depth = 0
    end = start
    for i in range(start, min(start + 2_000_000, len(html))):
        c = html[i]
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    try:
        player_data = json.loads(html[start:end])
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
        print(f"No captionTracks for {video_id}")
        return None

    def score(t):
        lang = t.get("languageCode", "")
        kind = t.get("kind", "")
        if lang.startswith("en") and kind != "asr": return 0
        if lang.startswith("en") and kind == "asr": return 1
        if kind == "asr": return 2
        return 3

    tracks.sort(key=score)
    best = tracks[0]
    print(f"Using track lang={best.get('languageCode')} kind={best.get('kind','manual')} for {video_id}")
    return best.get("baseUrl", "") or None


def fetch_caption_text(caption_url: str) -> str:
    try:
        url = caption_url + ("&fmt=json3" if "?" in caption_url else "?fmt=json3")
        req = urllib.request.Request(url, headers=HEADERS)
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode("utf-8", errors="replace")

        # JSON3 format
        try:
            data = json.loads(raw)
            parts = []
            for event in data.get("events", []):
                for seg in event.get("segs", []):
                    text = seg.get("utf8", "").strip()
                    if text and text != "\n":
                        parts.append(text)
            text = " ".join(parts).strip()
            if text:
                return text[:5000]
        except json.JSONDecodeError:
            pass

        # XML fallback
        try:
            root = ET.fromstring(raw)
            parts = [html_lib.unescape(e.text or "").strip() for e in root.iter("text")]
            return " ".join(p for p in parts if p)[:5000]
        except Exception:
            pass

        return ""
    except Exception as e:
        print(f"Caption fetch failed: {e}")
        return ""


def get_transcript(video_id: str) -> tuple:
    html = fetch_watch_page(video_id)
    if not html:
        return "", "watch_page_fetch_failed"
    if "consent.youtube.com" in html and "captionTracks" not in html:
        return "", "consent_gate_not_bypassed"
    caption_url = extract_caption_url(html, video_id)
    if not caption_url:
        return "", "no_caption_track_found"
    text = fetch_caption_text(caption_url)
    return (text, "success") if text else ("", "caption_fetch_failed")


def search_youtube(query: str, max_results: int = 10) -> list:
    if not YOUTUBE_API_KEY:
        return []
    params = urllib.parse.urlencode({
        "part": "snippet", "q": query, "type": "video",
        "maxResults": max_results, "relevanceLanguage": "en",
        "order": "relevance", "key": YOUTUBE_API_KEY
    })
    try:
        req = urllib.request.Request(
            f"https://www.googleapis.com/youtube/v3/search?{params}",
            headers={"Accept": "application/json"}
        )
        data = json.loads(urllib.request.urlopen(req, timeout=8).read().decode("utf-8"))
        return [
            {"videoId": i["id"]["videoId"], "title": i["snippet"]["title"],
             "channel": i["snippet"]["channelTitle"]}
            for i in data.get("items", []) if i.get("id", {}).get("videoId")
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

    if video_id:
        text, status = get_transcript(video_id)
        result = {"transcript": text, "videoId": video_id}
        if debug or not text:
            result["status"] = status
        return jsonify(result)

    videos = search_youtube(f"{device} review", max_results=10)
    if not videos:
        return jsonify({"transcript": "", "error": "No videos found"}), 200

    tried = []
    for video in videos:
        vid = video["videoId"]
        text, status = get_transcript(vid)
        tried.append({**video, "status": status, "length": len(text)})
        if text:
            result = {"transcript": text, "videoId": vid,
                      "title": video.get("title", ""), "channel": video.get("channel", "")}
            if debug:
                result["tried"] = tried
            return jsonify(result)

    return jsonify({"transcript": "", "error": f"No captions in top {len(videos)} results",
                    "videos_tried": tried}), 200


@app.route("/debug-page")
def debug_page():
    video_id = request.args.get("videoId", "a4NJNdHqs_I")
    html = fetch_watch_page(video_id) or ""
    idx = html.find("captionTracks")
    return jsonify({
        "video_id": video_id,
        "page_length": len(html),
        "has_ytInitialPlayerResponse": "ytInitialPlayerResponse" in html,
        "has_captionTracks": "captionTracks" in html,
        "has_consent_redirect": "consent.youtube.com" in html,
        "caption_snippet": html[idx:idx+300] if idx > 0 else "",
        "page_start": html[:300]
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok", "youtube_api_key_set": bool(YOUTUBE_API_KEY)}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
from flask import Flask, jsonify, request
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import TranscriptsDisabled, NoTranscriptFound

app = Flask(__name__)

@app.route("/transcript")
def transcript():
    video_id = request.args.get("videoId")
    if not video_id:
        return jsonify({"error": "missing videoId"}), 400
    try:
        ytt_api = YouTubeTranscriptApi()
        fetched = ytt_api.fetch(video_id)
        # Handle both object-style and dict-style snippet formats
        parts = []
        for snippet in fetched:
            try:
                parts.append(snippet.text)
            except AttributeError:
                parts.append(snippet.get("text", ""))
        text = " ".join(parts)
        return jsonify({"transcript": text[:5000]})
    except (TranscriptsDisabled, NoTranscriptFound):
        return jsonify({"transcript": ""}), 200
    except Exception as e:
        # Return 200 with empty transcript so Android app falls back to description gracefully
        return jsonify({"transcript": "", "error": str(e)}), 200

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
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
        entries = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US", "en-GB"])
        text = " ".join(e["text"] for e in entries)
        return jsonify({"transcript": text[:5000]})
    except (TranscriptsDisabled, NoTranscriptFound):
        return jsonify({"transcript": ""}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)

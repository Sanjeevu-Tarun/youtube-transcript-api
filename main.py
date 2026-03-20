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
        transcript_list = ytt_api.list(video_id)

        fetched = None
        # Priority 1: manual English
        try:
            fetched = transcript_list.find_manually_created_transcript(["en", "en-US", "en-GB"]).fetch()
        except Exception:
            pass

        # Priority 2: auto-generated English
        if fetched is None:
            try:
                fetched = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"]).fetch()
            except Exception:
                pass

        # Priority 3: any auto-generated — still useful context for the AI
        if fetched is None:
            try:
                for t in transcript_list:
                    if t.is_generated:
                        fetched = t.fetch()
                        break
            except Exception:
                pass

        if fetched is None:
            return jsonify({"transcript": ""}), 200

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
        return jsonify({"transcript": "", "error": str(e)}), 200


@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
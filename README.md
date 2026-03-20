# YouTube Transcript Server

Simple Flask server that fetches YouTube video transcripts.

## Endpoint

`GET /transcript?videoId=VIDEO_ID`

Returns: `{"transcript": "full text..."}`

## Deploy on Render

1. Push this repo to GitHub
2. New Web Service on Render → connect repo
3. Build command: `pip install -r requirements.txt`
4. Start command: `gunicorn main:app --bind 0.0.0.0:$PORT`

# X-Space-Downloader

Downloads audio from X (Twitter) Spaces as MP3 and optionally generates a transcript.  
**No paid X API subscription required.**

---

## Requirements

| Tool | Purpose |
|---|---|
| Python 3.10+ | Runtime |
| [ffmpeg](https://ffmpeg.org/download.html) | Audio muxing (needed by yt-dlp's audio extractor and Whisper) |

---

## Installation

```bash
pip install -r requirements.txt
```

> **Note:** `openai-whisper` pulls in PyTorch, which can be large (~1–2 GB).  
> If you don't need transcription, install without it:
> ```bash
> pip install yt-dlp requests
> ```

---

## Usage

### Download audio only

```bash
# Using a full Space URL
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX

# Using just the Space ID
python downloader.py 1eaKbrBlPlbKX

# Custom output filename
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -o my_space.mp3
```

### Download + generate transcript

```bash
# Transcript saved as <space_id>.txt next to the audio file
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -t

# Custom transcript path
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -t --transcript-output notes.txt

# Use a larger (more accurate) Whisper model
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -t --whisper-model medium
```

### Members-only spaces

```bash
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -c cookies.txt -t
```

---

## All options

| Flag | Description |
|---|---|
| `SPACE_URL_OR_ID` | Full `https://x.com/i/spaces/…` URL or bare Space ID |
| `-o FILE` | Output audio file path (default: `<space_id>.m4a`) |
| `-c COOKIES_FILE` | Netscape-format `cookies.txt` for members-only spaces |
| `-t` / `--transcript` | Generate a plain-text transcript after downloading |
| `--transcript-output FILE` | Transcript file path (default: same stem as audio + `.txt`) |
| `--whisper-model MODEL` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large` (default: `base`) |

### Whisper model comparison

| Model | ~Size | Speed | Accuracy |
|---|---|---|---|
| `tiny` | 75 MB | Fastest | Lowest |
| `base` | 145 MB | Fast | Good (default) |
| `small` | 465 MB | Moderate | Better |
| `medium` | 1.5 GB | Slow | High |
| `large` | 3 GB | Slowest | Best |

The model weights are downloaded automatically on first use.

---

### Exporting cookies

If a space is members-only or requires login, export your browser cookies while logged in to X using a browser extension such as [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) (Chrome) or [Export Cookies](https://addons.mozilla.org/en-US/firefox/addon/export-cookies-txt/) (Firefox), then pass the resulting file with `-c cookies.txt`.

---

## How it works

1. **Primary** — passes the Space URL directly to [yt-dlp](https://github.com/yt-dlp/yt-dlp), which handles authentication, HLS playlist resolution, and audio extraction. No API key needed.
2. **Fallback** — if yt-dlp fails, the script resolves the HLS playlist URL itself using Twitter's own public (unauthenticated) guest-token endpoint, then downloads and muxes the audio with ffmpeg.
3. **Transcription** *(optional, `--transcript`)* — runs [OpenAI Whisper](https://github.com/openai/whisper) locally on the downloaded audio. No API key, no internet connection required after the model weights are downloaded.

All methods are completely free and require no X Developer account.

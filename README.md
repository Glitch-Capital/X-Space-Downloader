# X-Space-Downloader

Downloads audio from X (Twitter) Spaces as MP3, with optional transcription and metadata.  
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

> **Note:** `openai-whisper` pulls in PyTorch (~1–2 GB). If you don't need transcription:
> ```bash
> pip install yt-dlp requests
> ```

---

## Usage

### Single space

```bash
# Full URL
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX

# Bare Space ID
python downloader.py 1eaKbrBlPlbKX

# Custom output file
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -o my_space.mp3

# Save to a specific directory
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -d ~/spaces/
```

### Batch download

Create a file with one Space URL or ID per line (lines starting with `#` are ignored):

```text
# spaces.txt
https://x.com/i/spaces/1eaKbrBlPlbKX
https://x.com/i/spaces/1OyKAVnmZbIKb
1YpKkgVAQElKj
```

```bash
python downloader.py -i spaces.txt -d ~/spaces/
```

### Transcript

```bash
# Generate a .txt transcript alongside the audio
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -t

# Custom transcript path
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -t --transcript-output notes.txt

# More accurate model (slower)
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -t --whisper-model medium
```

### Metadata sidecar

```bash
# Saves <space_id>.json with title, host, state, timestamps, listener counts
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX --metadata
```

### Skip already-downloaded spaces

```bash
# Useful for re-running a batch without re-downloading completed entries
python downloader.py -i spaces.txt -d ~/spaces/ --skip-if-exists
```

### Members-only spaces

```bash
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -c cookies.txt
```

---

## All options

| Flag | Description |
|---|---|
| `SPACE_URL_OR_ID` | Full `https://x.com/i/spaces/…` URL or bare Space ID |
| `-i / --input-file FILE` | Text file of URLs/IDs (one per line) for batch downloads |
| `-o / --output FILE` | Output audio file path (single-space mode only; default: `<space_id>.m4a`) |
| `-d / --output-dir DIR` | Directory to save all output files (created if missing) |
| `-c / --cookies FILE` | Netscape `cookies.txt` for members-only spaces |
| `--skip-if-exists` | Skip download if audio file already exists on disk |
| `--metadata` | Save a JSON sidecar with space info (`<space_id>.json`) |
| `-t / --transcript` | Generate a plain-text transcript after downloading |
| `--transcript-output FILE` | Transcript file path (single-space mode only; default: `<audio_stem>.txt`) |
| `--whisper-model MODEL` | Whisper model: `tiny`, `base` *(default)*, `small`, `medium`, `large` |

### Whisper model comparison

| Model | ~Size | Speed | Accuracy |
|---|---|---|---|
| `tiny` | 75 MB | Fastest | Lowest |
| `base` | 145 MB | Fast | Good *(default)* |
| `small` | 465 MB | Moderate | Better |
| `medium` | 1.5 GB | Slow | High |
| `large` | 3 GB | Slowest | Best |

Model weights download automatically on first use.

---

### Exporting cookies

If a space requires login, export your cookies while logged in to X using a browser extension such as [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) (Chrome) or [Export Cookies](https://addons.mozilla.org/en-US/firefox/addon/export-cookies-txt/) (Firefox), then pass the file with `-c cookies.txt`.

---

## How it works

1. **Primary** — passes the Space URL to [yt-dlp](https://github.com/yt-dlp/yt-dlp), which handles authentication, HLS resolution, and audio extraction. No API key needed.
2. **Fallback** — if yt-dlp fails, the script resolves the HLS playlist using Twitter's own public guest-token endpoint, then downloads with ffmpeg.
3. **Transcription** *(optional, `-t`)* — runs [OpenAI Whisper](https://github.com/openai/whisper) locally on the downloaded audio. No API key or internet connection required after model weights are downloaded.
4. **Metadata** *(optional, `--metadata`)* — fetches the space title, host, state, timestamps, and listener counts via the public guest-token API and writes them to a JSON sidecar.

All methods are completely free and require no X Developer account.

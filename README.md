# X Space Downloader

Download audio from X (Twitter) Spaces — no paid API, no developer account required.

- Saves audio as MP3
- Optional local transcription via [OpenAI Whisper](https://github.com/openai/whisper) (no API key)
- Optional JSON metadata sidecar (title, host, timestamps, listener counts)
- Single space, batch file, or grab everything from a user

---

## Requirements

- **Python 3.10+**
- **[ffmpeg](https://ffmpeg.org/download.html)** — required for audio extraction

---

## Installation

```bash
pip install -r requirements.txt
```

> `openai-whisper` pulls in PyTorch (~1–2 GB). If you don't need transcription, install only what's required:
> ```bash
> pip install yt-dlp requests
> ```

---

## Usage

### Single space

```bash
# By URL
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX

# By bare Space ID
python downloader.py 1eaKbrBlPlbKX

# Custom output file or directory
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -o my_space.mp3
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -d ~/spaces/
```

### All spaces from a user

```bash
python downloader.py --user someuser -d ~/spaces/

# @ prefix is optional
python downloader.py -u @someuser -d ~/spaces/

# With transcripts, skip anything already downloaded
python downloader.py -u someuser -d ~/spaces/ -t --skip-if-exists
```

### Batch download from a file

One URL or Space ID per line; lines starting with `#` are ignored.

```text
# spaces.txt
https://x.com/i/spaces/1eaKbrBlPlbKX
https://x.com/i/spaces/1OyKAVnmZbIKb
1YpKkgVAQElKj
```

```bash
python downloader.py -i spaces.txt -d ~/spaces/
```

### Transcription

Runs [OpenAI Whisper](https://github.com/openai/whisper) locally. Model weights download automatically on first use.

```bash
# Transcript saved alongside the audio as <space_id>.txt
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -t

# Custom output path
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -t --transcript-output notes.txt

# Higher accuracy (slower)
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -t --whisper-model medium
```

| Model | Size | Speed | Accuracy |
|---|---|---|---|
| `tiny` | 75 MB | Fastest | Lowest |
| `base` | 145 MB | Fast | Good *(default)* |
| `small` | 465 MB | Moderate | Better |
| `medium` | 1.5 GB | Slow | High |
| `large` | 3 GB | Slowest | Best |

### Metadata

Saves a JSON sidecar with the space title, host, state, timestamps, and listener counts.

```bash
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX --metadata
# → <space_id>.json
```

### Members-only spaces

Export your cookies from a browser while logged in to X using [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) (Chrome) or [Export Cookies](https://addons.mozilla.org/en-US/firefox/addon/export-cookies-txt/) (Firefox), then pass the file:

```bash
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -c cookies.txt
```

---

## All options

| Flag | Description |
|---|---|
| `SPACE_URL_OR_ID` | Space URL or bare Space ID |
| `-u / --user USERNAME` | Download all recorded Spaces from a user (`@` prefix optional) |
| `-i / --input-file FILE` | Text file of URLs/IDs, one per line |
| `-o / --output FILE` | Output file path (single-space only; default: `<space_id>.m4a`) |
| `-d / --output-dir DIR` | Directory for all output files (created if missing) |
| `-c / --cookies FILE` | Netscape `cookies.txt` for members-only spaces |
| `--skip-if-exists` | Skip if the audio file already exists |
| `--metadata` | Save a JSON sidecar (`<space_id>.json`) |
| `-t / --transcript` | Generate a `.txt` transcript after downloading |
| `--transcript-output FILE` | Transcript path (single-space only; default: `<audio_stem>.txt`) |
| `--whisper-model MODEL` | `tiny`, `base` *(default)*, `small`, `medium`, `large` |

---

## How it works

1. **yt-dlp** — the Space URL is passed directly to [yt-dlp](https://github.com/yt-dlp/yt-dlp), which resolves the HLS stream and extracts audio. No API key needed.
2. **Fallback** — if yt-dlp fails, the script uses Twitter's own public guest-token endpoint to resolve the HLS playlist and downloads it with ffmpeg.
3. **Transcription** — [OpenAI Whisper](https://github.com/openai/whisper) runs locally on the downloaded audio. No API key or internet connection required after the model weights are downloaded.

Everything is free and works without an X Developer account.

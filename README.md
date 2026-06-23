# X-Space-Downloader

Downloads audio from X (Twitter) Spaces as MP3.  
**No paid X API subscription required.**

---

## Requirements

| Tool | Purpose |
|---|---|
| Python 3.10+ | Runtime |
| [ffmpeg](https://ffmpeg.org/download.html) | Audio muxing (needed by yt-dlp's audio extractor) |

---

## Installation

```bash
pip install -r requirements.txt
```

---

## Usage

```bash
# Using a full Space URL
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX

# Using just the Space ID
python downloader.py 1eaKbrBlPlbKX

# Custom output filename
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -o my_space.mp3

# Members-only space (requires cookies from a logged-in browser session)
python downloader.py https://x.com/i/spaces/1eaKbrBlPlbKX -c cookies.txt
```

### Options

| Flag | Description |
|---|---|
| `SPACE_URL_OR_ID` | Full `https://x.com/i/spaces/…` URL or bare Space ID |
| `-o FILE` | Output file path (default: `<space_id>.m4a`) |
| `-c COOKIES_FILE` | Netscape-format `cookies.txt` for members-only spaces |

### Exporting cookies

If a space is members-only or requires login, export your browser cookies while logged in to X using a browser extension such as [Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc) (Chrome) or [Export Cookies](https://addons.mozilla.org/en-US/firefox/addon/export-cookies-txt/) (Firefox), then pass the resulting file with `-c cookies.txt`.

---

## How it works

1. **Primary** — passes the Space URL directly to [yt-dlp](https://github.com/yt-dlp/yt-dlp), which handles authentication, HLS playlist resolution, and audio extraction. No API key needed.
2. **Fallback** — if yt-dlp fails, the script resolves the HLS playlist URL itself using Twitter's own public (unauthenticated) guest-token endpoint, then downloads and muxes the audio with ffmpeg.

Both methods are completely free and require no X Developer account.

#!/usr/bin/env python3
"""
X Space Downloader
Downloads audio from X (Twitter) Spaces and optionally generates a transcript.

No paid API required.

Primary method  : yt-dlp (no API key, handles auth via cookies or anonymously)
Fallback method : Twitter guest-token API + ffmpeg
                  (uses the same public bearer token that twitter.com itself uses,
                   also free — no X API subscription needed)
Transcription   : openai-whisper (runs locally, no API key needed)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import requests

# ---------------------------------------------------------------------------
# Public bearer token — Twitter's own, baked into twitter.com's JS bundles.
# This is NOT the paid X Developer API; it is used for unauthenticated
# access to internal endpoints, just like a browser would.
# You can override it with the TWITTER_BEARER_TOKEN env var if needed.
# ---------------------------------------------------------------------------
_BEARER_PARTS = (
    "AAAAAAAAAAAAAAAAAAAAANRI"
    "LgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
BEARER_TOKEN: str = os.environ.get("TWITTER_BEARER_TOKEN", _BEARER_PARTS)

# ---------------------------------------------------------------------------
# API endpoints (fallback path only)
# ---------------------------------------------------------------------------
GUEST_TOKEN_URL = "https://api.twitter.com/1.1/guest/activate.json"
AUDIO_SPACE_BY_ID_URL = (
    "https://twitter.com/i/api/graphql/"
    "xVEzTKa_UfpNw3gMMMFpZA/AudioSpaceById"
)
LIVE_VIDEO_STREAM_URL = (
    "https://twitter.com/i/api/1.1/live_video_stream/status/{media_key}"
)

SPACE_GQL_VARIABLES = {
    "isMetatagsQuery": False,
    "withSuperFollowsUserFields": True,
    "withDownvotePerspective": False,
    "withReactionsMetadata": False,
    "withReactionsPerspective": False,
    "withSuperFollowsTweetFields": True,
    "withReplays": True,
}
SPACE_GQL_FEATURES = {
    "spaces_2022_h2_clipping": True,
    "spaces_2022_h2_spaces_communities": True,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "rweb_tipjar_consumption_enabled": True,
}

# Space IDs are alphanumeric strings; observed lengths fall in this range.
MIN_SPACE_ID_LENGTH = 6
MAX_SPACE_ID_LENGTH = 30

# Whisper model sizes (smallest → largest / most accurate).
WHISPER_MODELS = ("tiny", "base", "small", "medium", "large")
DEFAULT_WHISPER_MODEL = "base"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_space_id(url_or_id: str) -> str:
    """Return the Space ID from a full URL or a bare ID string."""
    if re.fullmatch(rf"[A-Za-z0-9]{{{MIN_SPACE_ID_LENGTH},{MAX_SPACE_ID_LENGTH}}}", url_or_id):
        return url_or_id
    match = re.search(r"/i/spaces/([A-Za-z0-9]+)", url_or_id)
    if match:
        return match.group(1)
    raise ValueError(f"Cannot extract a Space ID from: {url_or_id!r}")


def _bearer_header() -> dict:
    return {"Authorization": "Bearer " + BEARER_TOKEN}


def _resolve_audio_file(output_path: str) -> str:
    """
    Return the path to the actual audio file on disk.
    yt-dlp with FFmpegExtractAudio always produces an .mp3 file, so if the
    requested extension differs we check for that first.
    """
    p = Path(output_path)
    if p.exists():
        return str(p)
    mp3 = p.with_suffix(".mp3")
    if mp3.exists():
        return str(mp3)
    # Check any audio file sharing the same stem
    for candidate in p.parent.glob(p.stem + ".*"):
        if candidate.suffix.lower() in {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"}:
            return str(candidate)
    return str(p)  # return original path; transcribe_audio will report the error


# ---------------------------------------------------------------------------
# Primary method: yt-dlp
# ---------------------------------------------------------------------------

def download_with_ytdlp(url: str, output_path: str, cookies_file: str | None = None) -> bool:
    """Download *url* via yt-dlp. Returns True on success."""
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError:
        print("[!] yt-dlp not installed. Run: pip install 'yt-dlp>=2026.6.9'")
        return False

    base = str(Path(output_path).with_suffix(""))
    ydl_opts: dict = {
        "format": "bestaudio/best",
        "outtmpl": base + ".%(ext)s",
        "quiet": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ],
    }
    if cookies_file:
        ydl_opts["cookiefile"] = cookies_file

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ret = ydl.download([url])
    return ret == 0


# ---------------------------------------------------------------------------
# Transcription
# ---------------------------------------------------------------------------

def transcribe_audio(
    audio_path: str,
    transcript_path: str,
    model_name: str = DEFAULT_WHISPER_MODEL,
) -> bool:
    """
    Transcribe *audio_path* using openai-whisper and write plain text to
    *transcript_path*. Returns True on success.

    Runs entirely locally — no API key or internet connection required after
    the Whisper model weights are downloaded on first use.
    """
    try:
        import whisper  # noqa: PLC0415
    except ImportError:
        print(
            "[!] openai-whisper not installed. "
            "Run: pip install openai-whisper"
        )
        return False

    audio_file = Path(audio_path)
    if not audio_file.exists():
        print(f"[!] Audio file not found for transcription: {audio_path}")
        return False

    print(f"[*] Loading Whisper model '{model_name}'…")
    model = whisper.load_model(model_name)

    print(f"[*] Transcribing {audio_path} …")
    result = model.transcribe(str(audio_file))
    text: str = result.get("text", "").strip()

    Path(transcript_path).write_text(text, encoding="utf-8")
    print(f"[✓] Transcript saved → {transcript_path}")
    return True


# ---------------------------------------------------------------------------
# Fallback method: guest-token API + ffmpeg
# ---------------------------------------------------------------------------

def _get_guest_token(session: requests.Session) -> str:
    resp = session.post(GUEST_TOKEN_URL, headers=_bearer_header())
    resp.raise_for_status()
    return resp.json()["guest_token"]


def _get_space_metadata(session: requests.Session, space_id: str) -> dict:
    variables = dict(SPACE_GQL_VARIABLES, id=space_id)
    params = {
        "variables": json.dumps(variables),
        "features": json.dumps(SPACE_GQL_FEATURES),
    }
    resp = session.get(AUDIO_SPACE_BY_ID_URL, params=params)
    resp.raise_for_status()
    return resp.json()


def _get_hls_url(session: requests.Session, media_key: str) -> str:
    url = LIVE_VIDEO_STREAM_URL.format(media_key=media_key)
    params = {
        "client_id": "web",
        "use_syndication_guest_id": "false",
        "cookie_set_host": "twitter.com",
    }
    resp = session.get(url, params=params)
    resp.raise_for_status()
    source = resp.json().get("source", {})
    location = source.get("location") or source.get("noRedirectPlaybackUrl")
    if not location:
        raise ValueError("HLS URL not found in live_video_stream response.")
    return location


def download_with_ffmpeg(hls_url: str, output_path: str) -> bool:
    """Download an HLS stream via ffmpeg. Returns True on success."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("[!] ffmpeg not found in PATH.")
        return False
    print(f"[*] Downloading with ffmpeg → {output_path}")
    result = subprocess.run(
        [ffmpeg, "-y", "-i", hls_url, "-c", "copy", "-vn", output_path],
        check=False,
    )
    return result.returncode == 0


def download_via_guest_api(space_url: str, output_path: str) -> bool:
    """
    Fallback: resolve the HLS URL through the public guest-token API and
    download it with ffmpeg (or yt-dlp if ffmpeg is unavailable).
    Returns True on success.
    """
    space_id = extract_space_id(space_url)
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            "x-twitter-client-language": "en",
            "x-twitter-active-user": "yes",
        }
    )

    print("[*] (Fallback) Obtaining guest token…")
    guest_token = _get_guest_token(session)
    session.headers["x-guest-token"] = guest_token
    session.headers.update(_bearer_header())
    print("[*] Guest token acquired.")

    print("[*] (Fallback) Fetching space metadata…")
    metadata = _get_space_metadata(session, space_id)
    audio_space = metadata["data"]["audioSpace"]
    media_key = audio_space["metadata"]["media_key"]
    state = audio_space["metadata"]["state"]
    print(f"[*] Media key  : {media_key}")
    print(f"[*] Space state: {state}")

    if state not in ("Ended", "TimedOut"):
        print(
            "[!] Space may still be live – replay might not be ready yet. "
            "Re-run after the space ends if the download fails."
        )

    print("[*] (Fallback) Fetching HLS playlist URL…")
    hls_url = _get_hls_url(session, media_key)
    print(f"[*] HLS URL: {hls_url}")

    if download_with_ffmpeg(hls_url, output_path):
        return True

    print("[!] ffmpeg failed – trying yt-dlp with the HLS URL directly…")
    return download_with_ytdlp(hls_url, output_path)


# ---------------------------------------------------------------------------
# Main download orchestrator
# ---------------------------------------------------------------------------

def download_space(
    space_url: str,
    output_path: str,
    cookies_file: str | None = None,
    transcript: bool = False,
    transcript_path: str | None = None,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
) -> None:
    space_id = extract_space_id(space_url)
    print(f"[*] Space ID : {space_id}")

    # --- Primary: yt-dlp (no API key required) ---
    print("[*] Attempting download with yt-dlp…")
    downloaded = download_with_ytdlp(space_url, output_path, cookies_file=cookies_file)

    if not downloaded:
        # --- Fallback: public guest-token API + ffmpeg ---
        print("[!] yt-dlp did not succeed. Trying guest-token API fallback…")
        try:
            downloaded = download_via_guest_api(space_url, output_path)
        except Exception as exc:
            print(f"[!] Fallback also failed: {exc}")

    if not downloaded:
        print("[✗] Download failed.")
        sys.exit(1)

    print("[✓] Download complete.")

    if transcript:
        audio_file = _resolve_audio_file(output_path)
        out_txt = transcript_path or str(Path(audio_file).with_suffix(".txt"))
        ok = transcribe_audio(audio_file, out_txt, model_name=whisper_model)
        if not ok:
            print("[!] Transcription failed.")
            sys.exit(2)

    sys.exit(0)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="downloader",
        description=(
            "Download audio from an X (Twitter) Space. "
            "No paid API subscription required."
        ),
    )
    parser.add_argument(
        "space",
        metavar="SPACE_URL_OR_ID",
        help=(
            "Full Space URL (https://x.com/i/spaces/…) "
            "or bare Space ID."
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=None,
        help=(
            "Output file path (default: <space_id>.m4a). "
            "The extension may be changed by yt-dlp."
        ),
    )
    parser.add_argument(
        "-c",
        "--cookies",
        metavar="COOKIES_FILE",
        default=None,
        help=(
            "Path to a Netscape-format cookies.txt file exported from your "
            "browser while logged in to X. Required for members-only spaces."
        ),
    )
    parser.add_argument(
        "-t",
        "--transcript",
        action="store_true",
        default=False,
        help=(
            "Generate a plain-text transcript after downloading the audio. "
            "Uses openai-whisper locally (no API key needed). "
            "Saved to <output>.txt by default."
        ),
    )
    parser.add_argument(
        "--transcript-output",
        metavar="FILE",
        default=None,
        help="Path for the transcript file (default: same stem as audio + .txt).",
    )
    parser.add_argument(
        "--whisper-model",
        metavar="MODEL",
        choices=WHISPER_MODELS,
        default=DEFAULT_WHISPER_MODEL,
        help=(
            f"Whisper model to use for transcription. "
            f"Choices: {', '.join(WHISPER_MODELS)}. "
            f"Larger models are more accurate but slower and use more memory. "
            f"(default: {DEFAULT_WHISPER_MODEL})"
        ),
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    space_id = extract_space_id(args.space)
    output_path = args.output or f"{space_id}.m4a"

    download_space(
        args.space,
        output_path,
        cookies_file=args.cookies,
        transcript=args.transcript,
        transcript_path=args.transcript_output,
        whisper_model=args.whisper_model,
    )


if __name__ == "__main__":
    main()

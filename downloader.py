#!/usr/bin/env python3
"""
X Space Downloader
Downloads audio from X (Twitter) Spaces.

Primary method  : Twitter API + ffmpeg (direct HLS download)
Fallback method : yt-dlp
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
# ******
# Twitter's own public bearer token (baked into twitter.com JavaScript).
# Not a secret — widely used by open-source clients for unauthenticated
# API access.  Override by setting the TWITTER_BEARER_TOKEN env variable.
# ---------------------------------------------------------------------------
_BEARER_PARTS = (
    "AAAAAAAAAAAAAAAAAAAAANRI"
    "LgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs"
    "%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA"
)
BEARER_TOKEN: str = os.environ.get("TWITTER_BEARER_TOKEN", _BEARER_PARTS)

# ---------------------------------------------------------------------------
# API endpoints
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_space_id(url_or_id: str) -> str:
    """Return the Space ID from a full URL or a bare ID string."""
    if re.fullmatch(r"[A-Za-z0-9]{6,30}", url_or_id):
        return url_or_id
    match = re.search(r"/i/spaces/([A-Za-z0-9]+)", url_or_id)
    if match:
        return match.group(1)
    raise ValueError(f"Cannot extract a Space ID from: {url_or_id!r}")


def _bearer_header() -> dict:
    return {"Authorization": "Bearer " + BEARER_TOKEN}


def get_guest_token(session: requests.Session) -> str:
    resp = session.post(GUEST_TOKEN_URL, headers=_bearer_header())
    resp.raise_for_status()
    return resp.json()["guest_token"]


def get_space_metadata(session: requests.Session, space_id: str) -> dict:
    variables = dict(SPACE_GQL_VARIABLES, id=space_id)
    params = {
        "variables": json.dumps(variables),
        "features": json.dumps(SPACE_GQL_FEATURES),
    }
    resp = session.get(AUDIO_SPACE_BY_ID_URL, params=params)
    resp.raise_for_status()
    return resp.json()


def get_hls_url(session: requests.Session, media_key: str) -> str:
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


# ---------------------------------------------------------------------------
# Downloaders
# ---------------------------------------------------------------------------

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


def download_with_ytdlp(url: str, output_path: str) -> bool:
    """Download via yt-dlp Python API. Returns True on success."""
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError:
        print("[!] yt-dlp not installed. Run: pip install 'yt-dlp>=2026.6.9'")
        return False

    base = str(Path(output_path).with_suffix(""))
    ydl_opts = {
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
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ret = ydl.download([url])
    return ret == 0


# ---------------------------------------------------------------------------
# Main download flow
# ---------------------------------------------------------------------------

def download_space(space_url: str, output_path: str) -> None:
    space_id = extract_space_id(space_url)
    print(f"[*] Space ID : {space_id}")

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

    hls_url = None
    try:
        print("[*] Obtaining guest token…")
        guest_token = get_guest_token(session)
        session.headers["x-guest-token"] = guest_token
        session.headers.update(_bearer_header())
        print("[*] Guest token acquired.")

        print("[*] Fetching space metadata…")
        metadata = get_space_metadata(session, space_id)
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

        print("[*] Fetching HLS playlist URL…")
        hls_url = get_hls_url(session, media_key)
        print(f"[*] HLS URL: {hls_url}")

    except Exception as exc:
        print(f"[!] Primary API flow failed: {exc}")

    if hls_url:
        if download_with_ffmpeg(hls_url, output_path):
            _finish(True)
        print("[!] ffmpeg failed – trying yt-dlp with the HLS URL…")
        if download_with_ytdlp(hls_url, output_path):
            _finish(True)

    print("[*] Falling back to yt-dlp with the original Space URL…")
    _finish(download_with_ytdlp(space_url, output_path))


def _finish(success: bool) -> None:
    if success:
        print("[✓] Download complete.")
        sys.exit(0)
    print("[✗] Download failed.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="downloader",
        description="Download audio from an X (Twitter) Space.",
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
            "The extension may be overridden by yt-dlp."
        ),
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    space_id = extract_space_id(args.space)
    output_path = args.output or f"{space_id}.m4a"

    download_space(args.space, output_path)


if __name__ == "__main__":
    main()

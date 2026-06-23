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
USER_BY_SCREEN_NAME_URL = (
    "https://twitter.com/i/api/graphql/"
    "oUZZZ8Oddwxs8Cd3iW3UEA/UserByScreenName"
)
SPACES_BY_CREATOR_URL = (
    "https://twitter.com/i/api/graphql/"
    "oCOFbBbYFiW6LHqjGfHmxA/AudioSpacesByCreatorId"
)

USER_LOOKUP_FEATURES = {
    "hidden_profile_likes_enabled": True,
    "hidden_profile_subscriptions_enabled": True,
    "responsive_web_graphql_exclude_directive_enabled": True,
    "verified_phone_label_enabled": False,
    "creator_subscriptions_tweet_preview_api_enabled": True,
    "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
    "responsive_web_graphql_timeline_navigation_enabled": True,
}

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


def _audio_exists(output_path: str) -> bool:
    """Return True if any audio file for this stem already exists on disk."""
    return _resolve_audio_file(output_path) != output_path or Path(output_path).exists()


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


def _build_guest_session() -> requests.Session:
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
    guest_token = _get_guest_token(session)
    session.headers["x-guest-token"] = guest_token
    session.headers.update(_bearer_header())
    return session


def _get_user_id(session: requests.Session, username: str) -> str:
    """Resolve a screen name to its numeric REST user ID."""
    variables = {"screen_name": username, "withSafetyModeUserFields": True}
    params = {
        "variables": json.dumps(variables),
        "features": json.dumps(USER_LOOKUP_FEATURES),
    }
    resp = session.get(USER_BY_SCREEN_NAME_URL, params=params)
    resp.raise_for_status()
    return resp.json()["data"]["user"]["result"]["rest_id"]


def _list_spaces_by_creator(session: requests.Session, user_id: str) -> list[str]:
    """Return Space IDs for the given creator user ID via the GraphQL API."""
    variables = {"creatorIds": [user_id], "isFromVault": False, "limit": 20}
    params = {
        "variables": json.dumps(variables),
        "features": json.dumps(SPACE_GQL_FEATURES),
    }
    resp = session.get(SPACES_BY_CREATOR_URL, params=params)
    resp.raise_for_status()
    data = resp.json()

    space_ids: list[str] = []
    spaces = data.get("data", {}).get("audioSpacesByCreatorId", [])
    for space in spaces:
        sid = (
            space.get("metadata", {}).get("rest_id")
            or space.get("rest_id")
        )
        if sid:
            space_ids.append(sid)
    return space_ids


def _list_spaces_via_ytdlp(
    username: str,
    cookies_file: str | None = None,
) -> list[str]:
    """Try to list a user's spaces via yt-dlp flat-playlist extraction."""
    try:
        import yt_dlp  # noqa: PLC0415
    except ImportError:
        return []

    profile_url = f"https://x.com/{username}/spaces"
    opts: dict = {
        "extract_flat": True,
        "quiet": True,
        "no_warnings": True,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(profile_url, download=False)
        if not info:
            return []
        urls: list[str] = []
        for entry in info.get("entries") or []:
            entry_url = entry.get("webpage_url") or entry.get("url", "")
            if "/i/spaces/" in entry_url:
                urls.append(entry_url)
        return urls
    except Exception:
        return []


def fetch_user_spaces(
    username: str,
    cookies_file: str | None = None,
) -> list[str]:
    """
    Return a list of Space URLs recorded by *username*.

    Tries yt-dlp flat-playlist extraction first; falls back to the
    guest-token GraphQL API (``AudioSpacesByCreatorId``).
    """
    # Primary: yt-dlp
    urls = _list_spaces_via_ytdlp(username, cookies_file)
    if urls:
        print(f"[✓] Found {len(urls)} space(s) via yt-dlp for @{username}.")
        return urls

    # Fallback: guest-token GraphQL
    print("[*] yt-dlp did not return spaces; trying guest-token API…")
    try:
        session = _build_guest_session()
        print(f"[*] Resolving user ID for @{username}…")
        user_id = _get_user_id(session, username)
        print(f"[*] User ID: {user_id}")
        space_ids = _list_spaces_by_creator(session, user_id)
        if not space_ids:
            print(f"[!] No recorded spaces found for @{username}.")
            return []
        urls = [f"https://x.com/i/spaces/{sid}" for sid in space_ids]
        print(f"[✓] Found {len(urls)} space(s) for @{username}.")
        return urls
    except Exception as exc:
        print(f"[!] Could not fetch spaces for @{username}: {exc}")
        return []


def save_metadata(space_id: str, metadata_path: str) -> bool:
    """
    Fetch space metadata via the guest-token API and write a JSON sidecar to
    *metadata_path*. Returns True on success.
    """
    try:
        print("[*] Fetching space metadata for sidecar…")
        session = _build_guest_session()
        raw = _get_space_metadata(session, space_id)
        audio_space = raw["data"]["audioSpace"]["metadata"]

        sidecar = {
            "space_id": space_id,
            "title": audio_space.get("title", ""),
            "state": audio_space.get("state", ""),
            "created_at": audio_space.get("created_at", ""),
            "started_at": audio_space.get("started_at", ""),
            "ended_at": audio_space.get("ended_at", ""),
            "media_key": audio_space.get("media_key", ""),
            "total_replay_watched": audio_space.get("total_replay_watched", 0),
            "total_live_listeners": audio_space.get("total_live_listeners", 0),
        }

        # Host info lives one level up
        host_results = (
            raw.get("data", {})
            .get("audioSpace", {})
            .get("participants", {})
            .get("admins", [])
        )
        if host_results:
            host = host_results[0].get("twitter_screen_name", "")
            sidecar["host"] = host

        Path(metadata_path).write_text(
            json.dumps(sidecar, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[✓] Metadata saved → {metadata_path}")
        return True
    except Exception as exc:
        print(f"[!] Could not save metadata: {exc}")
        return False


def download_via_guest_api(space_url: str, output_path: str) -> bool:
    """
    Fallback: resolve the HLS URL through the public guest-token API and
    download it with ffmpeg (or yt-dlp if ffmpeg is unavailable).
    Returns True on success.
    """
    space_id = extract_space_id(space_url)
    try:
        print("[*] (Fallback) Obtaining guest token…")
        session = _build_guest_session()
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
    except Exception as exc:
        print(f"[!] Guest API error: {exc}")
        return False

    if download_with_ffmpeg(hls_url, output_path):
        return True

    print("[!] ffmpeg failed – trying yt-dlp with the HLS URL directly…")
    return download_with_ytdlp(hls_url, output_path)


# ---------------------------------------------------------------------------
# Single-space download orchestrator
# ---------------------------------------------------------------------------

def download_space(
    space_url: str,
    output_path: str,
    cookies_file: str | None = None,
    transcript: bool = False,
    transcript_path: str | None = None,
    whisper_model: str = DEFAULT_WHISPER_MODEL,
    skip_if_exists: bool = False,
    metadata: bool = False,
    metadata_path: str | None = None,
) -> bool:
    """
    Download one X Space. Returns True on success, False on failure.
    (Does not call sys.exit — callers decide how to handle the result.)
    """
    space_id = extract_space_id(space_url)
    print(f"[*] Space ID : {space_id}")

    # --- Skip if already downloaded ---
    if skip_if_exists and _audio_exists(output_path):
        print(f"[~] Skipping download — file already exists: {_resolve_audio_file(output_path)}")
    else:
        # --- Primary: yt-dlp ---
        print("[*] Attempting download with yt-dlp…")
        downloaded = download_with_ytdlp(space_url, output_path, cookies_file=cookies_file)

        if not downloaded:
            print("[!] yt-dlp did not succeed. Trying guest-token API fallback…")
            downloaded = download_via_guest_api(space_url, output_path)

        if not downloaded:
            print("[✗] Download failed.")
            return False

        print("[✓] Download complete.")

    # --- Metadata sidecar ---
    if metadata:
        meta_file = metadata_path or str(Path(output_path).with_suffix(".json"))
        save_metadata(space_id, meta_file)

    # --- Transcript ---
    if transcript:
        audio_file = _resolve_audio_file(output_path)
        out_txt = transcript_path or str(Path(audio_file).with_suffix(".txt"))
        if not transcribe_audio(audio_file, out_txt, model_name=whisper_model):
            print("[!] Transcription failed.")
            return False

    return True


# ---------------------------------------------------------------------------
# Batch helpers
# ---------------------------------------------------------------------------

def read_input_file(path: str) -> list[str]:
    """Return non-empty, non-comment lines from a URL list file."""
    lines = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="downloader",
        description=(
            "Download audio from X (Twitter) Spaces. "
            "No paid API subscription required."
        ),
    )

    # --- Input ---
    input_group = parser.add_mutually_exclusive_group(required=True)
    input_group.add_argument(
        "space",
        metavar="SPACE_URL_OR_ID",
        nargs="?",
        help="Full Space URL (https://x.com/i/spaces/…) or bare Space ID.",
    )
    input_group.add_argument(
        "-i",
        "--input-file",
        metavar="FILE",
        default=None,
        help=(
            "Path to a text file containing one Space URL or ID per line. "
            "Lines starting with '#' are treated as comments."
        ),
    )
    input_group.add_argument(
        "-u",
        "--user",
        metavar="USERNAME",
        default=None,
        help=(
            "Download all recorded Spaces from this X username "
            "(the @ prefix is optional, e.g. elonmusk or @elonmusk)."
        ),
    )

    # --- Output ---
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        default=None,
        help=(
            "Output audio file path (default: <space_id>.m4a). "
            "Ignored when --input-file is used; filenames are derived from Space IDs."
        ),
    )
    parser.add_argument(
        "-d",
        "--output-dir",
        metavar="DIR",
        default=None,
        help=(
            "Directory to save all output files in. "
            "Created automatically if it does not exist. "
            "(default: current working directory)"
        ),
    )

    # --- Auth ---
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

    # --- Behaviour ---
    parser.add_argument(
        "--skip-if-exists",
        action="store_true",
        default=False,
        help="Skip the download if the audio file already exists on disk.",
    )
    parser.add_argument(
        "--metadata",
        action="store_true",
        default=False,
        help=(
            "Save a JSON sidecar file containing space title, host, state, "
            "timestamps, and listener counts (default: <space_id>.json)."
        ),
    )

    # --- Transcript ---
    parser.add_argument(
        "-t",
        "--transcript",
        action="store_true",
        default=False,
        help=(
            "Generate a plain-text transcript after downloading. "
            "Uses openai-whisper locally (no API key needed). "
            "Saved to <audio_stem>.txt by default."
        ),
    )
    parser.add_argument(
        "--transcript-output",
        metavar="FILE",
        default=None,
        help=(
            "Path for the transcript file (default: same stem as audio + .txt). "
            "Ignored when --input-file is used."
        ),
    )
    parser.add_argument(
        "--whisper-model",
        metavar="MODEL",
        choices=WHISPER_MODELS,
        default=DEFAULT_WHISPER_MODEL,
        help=(
            f"Whisper model size for transcription. "
            f"Choices: {', '.join(WHISPER_MODELS)}. "
            f"Larger = more accurate but slower. "
            f"(default: {DEFAULT_WHISPER_MODEL})"
        ),
    )
    return parser


def _output_path_for(space_id: str, output_dir: str | None) -> str:
    filename = f"{space_id}.m4a"
    if output_dir:
        return str(Path(output_dir) / filename)
    return filename


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # Create output directory if requested
    if args.output_dir:
        Path(args.output_dir).mkdir(parents=True, exist_ok=True)

    # Build the list of (space_url, output_path) pairs
    if args.input_file:
        entries_raw = read_input_file(args.input_file)
        if not entries_raw:
            print("[!] Input file is empty or contains only comments.")
            sys.exit(1)
        entries = []
        for entry in entries_raw:
            sid = extract_space_id(entry)
            entries.append((entry, _output_path_for(sid, args.output_dir)))
    elif args.user:
        username = args.user.lstrip("@")
        print(f"[*] Fetching recorded spaces for @{username}…")
        space_urls = fetch_user_spaces(username, cookies_file=args.cookies)
        if not space_urls:
            sys.exit(1)
        entries = []
        for url in space_urls:
            sid = extract_space_id(url)
            entries.append((url, _output_path_for(sid, args.output_dir)))
    else:
        space_url = args.space
        space_id = extract_space_id(space_url)
        output_path = args.output or _output_path_for(space_id, args.output_dir)
        entries = [(space_url, output_path)]

    # Process each space
    failures = 0
    for idx, (space_url, output_path) in enumerate(entries, start=1):
        if len(entries) > 1:
            print(f"\n[*] [{idx}/{len(entries)}] {space_url}")

        success = download_space(
            space_url=space_url,
            output_path=output_path,
            cookies_file=args.cookies,
            transcript=args.transcript,
            transcript_path=args.transcript_output if len(entries) == 1 else None,
            whisper_model=args.whisper_model,
            skip_if_exists=args.skip_if_exists,
            metadata=args.metadata,
        )
        if not success:
            failures += 1

    if failures:
        print(f"\n[✗] {failures}/{len(entries)} space(s) failed.")
        sys.exit(1)

    if len(entries) > 1:
        print(f"\n[✓] All {len(entries)} spaces downloaded successfully.")
    sys.exit(0)


if __name__ == "__main__":
    main()

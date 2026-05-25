"""Douyin live stream URL resolver — sync version for Qt integration."""
import json
import re

import httpx
from src.logger import get as _log

log = _log("douyin")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://live.douyin.com/",
}

RECORD_HEADERS = (
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36\\r\\n"
    "Origin: https://live.douyin.com\\r\\n"
    "Referer: https://live.douyin.com/\\r\\n"
)

MPV_HEADER_FIELDS = (
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36,"
    "Origin: https://live.douyin.com,"
    "Referer: https://live.douyin.com/"
)

_STATUS_LIVE = 2


def check_live(room_id: str) -> bool:
    """Check if Douyin room is live."""
    try:
        room = _extract_room(room_id)
        if not room:
            return False
        return room.get("status") == _STATUS_LIVE
    except Exception as e:
        log.error("check_live failed for room %s: %s", room_id, e)
        return False


def get_stream_url(room_id: str) -> str | None:
    """Get Douyin live FLV stream URL (highest quality). Returns None if not live."""
    try:
        room = _extract_room(room_id)
        if not room:
            return None

        if room.get("status") != _STATUS_LIVE:
            log.info("room %s: not live", room_id)
            return None

        flv = room.get("stream_url", {}).get("flv_pull_url", {})
        # Quality order: FULL_HD1 > HD1 > SD1 > SD2
        for key in ("FULL_HD1", "HD1", "SD1", "SD2"):
            url = flv.get(key)
            if url:
                url = re.sub(r"^https?://", "https://", url)
                log.info("room %s: got %s stream", room_id, key)
                return url

        log.warning("room %s: no stream URL found", room_id)
        return None

    except Exception as e:
        log.error("room %s: get stream failed: %s", room_id, e)
        return None


def _extract_room(room_id: str) -> dict | None:
    """Extract room data from Douyin page."""
    with httpx.Client(timeout=15, follow_redirects=True) as http:
        resp = http.get(f"https://live.douyin.com/{room_id}", headers=HEADERS)
        html = resp.text

    for m in re.finditer(r"self\.__pace_f\.push\((\[\d+),", html):
        start = m.start(1)
        depth = 0
        end = start
        for i in range(start, len(html)):
            if html[i] == "[":
                depth += 1
            elif html[i] == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break

        content = html[start:end]
        if "flv_pull_url" not in content:
            continue

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            continue

        prefixed = data[1]
        inner = re.sub(r"^\w+:", "", prefixed)
        try:
            inner_data = json.loads(inner)
        except json.JSONDecodeError:
            continue

        for item in inner_data:
            if isinstance(item, dict) and "state" in item:
                room_store = item["state"].get("roomStore", {})
                return room_store.get("roomInfo", {}).get("room", {})

    return None

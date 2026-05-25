"""Douyu live stream URL resolver — sync version for Qt integration."""
import hashlib
import time

import httpx
from src.logger import get as _log

log = _log("douyu")

DEFAULT_DID = "10000000000000000000000000001501"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.douyu.com/",
}

RECORD_HEADERS = (
    
)

MPV_HEADER_FIELDS = (
    
)

def check_live(room_id: str) -> bool:
    """Check if Douyu room is live."""
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(f"https://www.douyu.com/betard/{room_id}", headers=HEADERS)
            data = resp.json()
            return data.get("room", {}).get("show_status") == 1
    except Exception as e:
        log.error("check_live failed for room %s: %s", room_id, e)
        return False


def get_stream_url(room_id: str) -> str | None:
    """Get Douyu live stream URL (source quality). Returns None if not live."""
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            # 1. Check live status
            room_resp = http.get(f"https://www.douyu.com/betard/{room_id}", headers=HEADERS)
            room_data = room_resp.json().get("room", {})

            if room_data.get("show_status") != 1:
                log.info("room %s: not live", room_id)
                return None

            # 2. Get encryption params
            white = _get_encryption(http)

            # 3. Compute auth
            ts, auth = _compute_auth(room_id, white)

            # 4. Request stream (rate=0 = source)
            params = {
                "rate": "0",
                "ver": "219032101",
                "iar": "0",
                "ive": "0",
                "rid": room_id,
                "hevc": "0",
                "fa": "0",
                "sov": "0",
                "enc_data": white["enc_data"],
                "tt": str(ts),
                "did": DEFAULT_DID,
                "auth": auth,
            }

            play_resp = http.post(
                f"https://playweb.douyucdn.cn/lapi/live/getH5PlayV1/{room_id}",
                headers={**HEADERS, "Origin": "https://www.douyu.com",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data=params,
            )
            play_data = play_resp.json()

            if play_data.get("error") != 0:
                log.warning("room %s: stream request failed - %s", room_id, play_data.get("msg", "unknown"))
                return None

            info = play_data.get("data", {})
            rtmp_url = info.get("rtmp_url", "")
            rtmp_live = info.get("rtmp_live", "")

            if rtmp_url and rtmp_live:
                stream_url = f"{rtmp_url}/{rtmp_live}"
                log.info("room %s: got stream", room_id)
                return stream_url

            log.warning("room %s: no stream URL found", room_id)
            return None

    except Exception as e:
        log.error("room %s: get stream failed: %s", room_id, e)
        return None


def _get_encryption(http: httpx.Client) -> dict:
    url = f"https://www.douyu.com/wgapi/livenc/liveweb/websec/getEncryption?did={DEFAULT_DID}"
    resp = http.get(url, headers=HEADERS)
    data = resp.json()
    if data.get("error") != 0:
        raise RuntimeError(f"getEncryption failed: {data.get('msg', 'unknown')}")
    return data["data"]


def _compute_auth(rid: str, white: dict) -> tuple[int, str]:
    """Multi-round MD5 auth signature."""
    ts = int(time.time())
    secret = white["rand_str"]
    salt = f"{rid}{ts}" if not white.get("is_special") else ""

    key = white["key"]
    for _ in range(white["enc_time"]):
        secret = hashlib.md5((secret + key).encode()).hexdigest()

    auth = hashlib.md5((secret + key + salt).encode()).hexdigest()
    return ts, auth

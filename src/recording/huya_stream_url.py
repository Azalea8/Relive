"""Huya live stream URL resolver — sync version for Qt integration."""
import base64
import hashlib
import json
import random
import re
import time
import urllib.parse
from html import unescape as html_unescape

import httpx
from src.logger import get as _log

log = _log("huya")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.huya.com/",
}

_CONSTANTS = {
    "t": 100,
    "ver": 1,
    "sv": 2401090219,
    "codec": 264,
}

_STREAM_PARAM_KEYS = ("wsTime", "fm", "ctype", "fs")

RECORD_HEADERS = (
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36\\r\\n"
    "Origin: https://www.huya.com\\r\\n"
    "Referer: https://www.huya.com/\\r\\n"
)

MPV_HEADER_FIELDS = (
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36,"
    "Origin: https://www.huya.com,"
    "Referer: https://www.huya.com/"
)


def check_live(room_id: str) -> bool:
    """Check if Huya room is live."""
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            params = {
                "m": "Live",
                "do": "profileRoom",
                "roomid": room_id,
                "showSecret": "1",
            }
            resp = http.get(
                f"https://mp.huya.com/cache.php?{urllib.parse.urlencode(params)}",
                headers=HEADERS,
            )
            data = resp.json()
            profile = data.get("data")
            if not profile or isinstance(profile, list):
                return False
            return profile.get("realLiveStatus") == "ON"
    except Exception as e:
        log.error("check_live failed for room %s: %s", room_id, e)
        return False


def get_stream_url(room_id: str) -> str | None:
    """Get Huya live stream URL (FLV, source quality). Returns None if not live."""
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            resp = http.get(f"https://www.huya.com/{room_id}", headers=HEADERS)
            html = resp.text

            data = _parse_stream_json(html)
            if not data:
                log.warning("room %s: could not parse stream data", room_id)
                return None

            stream_list, multi_stream_info = data

            stream = _pick_cdn(stream_list, prefer="TX")
            if not stream:
                log.warning("room %s: no stream available", room_id)
                return None

            cdn_type, stream_name, hls_url, suffix, anti_code = stream

            qs = {k: v for k, v in dict(urllib.parse.parse_qsl(anti_code)).items()
                  if k in _STREAM_PARAM_KEYS}

            url = re.sub(r"^https?://", "https://", f"{hls_url}/{stream_name}.{suffix}")
            params = _get_stream_params(
                fm=qs.get("fm", ""),
                fs=qs.get("fs", ""),
                ctype=qs.get("ctype", "huya_live"),
                ws_time=qs.get("wsTime", ""),
                stream_name=stream_name,
                i_bit_rate=_source_bitrate(multi_stream_info),
            )
            full_url = f"{url}?{urllib.parse.urlencode(params)}"
            log.info("room %s: got FLV stream (%s)", room_id, cdn_type)
            return full_url

    except Exception as e:
        log.error("room %s: get stream failed: %s", room_id, e)
        return None


def _parse_stream_json(html: str):
    """Extract the stream: {...} JSON object from page HTML via brace counting."""
    pos = html.find("stream:")
    if pos == -1:
        return None

    start = html.index("{", pos)
    depth = 0
    end = start
    for i in range(start, len(html)):
        ch = html[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    try:
        stream_json = json.loads(html[start:end])
    except json.JSONDecodeError:
        return None

    data_list = stream_json.get("data", [])
    if not data_list:
        return None

    game_info = data_list[0]
    processed = []
    for s in game_info.get("gameStreamInfoList", []):
        anti_code = html_unescape(s.get("sFlvAntiCode", ""))
        processed.append((
            s.get("sCdnType", ""),
            s.get("sStreamName", ""),
            s.get("sFlvUrl", ""),
            s.get("sFlvUrlSuffix", ""),
            anti_code,
        ))

    multi = stream_json.get("vMultiStreamInfo", [])
    return processed, multi


def _source_bitrate(multi_stream_info: list) -> int:
    """Return 0 (source) if available, otherwise the highest iBitRate."""
    has_source = any(int(s.get("iBitRate", -1)) == 0 for s in multi_stream_info)
    if has_source:
        return 0
    return max((int(s.get("iBitRate", 0)) for s in multi_stream_info), default=0)


def _pick_cdn(stream_list: list, prefer: str = "TX"):
    """Pick preferred CDN, falling back to first available."""
    for s in stream_list:
        if s[0] == prefer:
            return s
    return stream_list[0] if stream_list else None


def _get_stream_params(fm: str, fs: str, ctype: str, ws_time: str,
                       stream_name: str, i_bit_rate: int) -> dict:
    """Build stream request params with anti-leech signature (streamlink approach)."""
    uid = random.randint(12340000, 12349999)
    convert_uid = (uid << 8 | uid >> (32 - 8)) & 0xFFFFFFFF
    timestamp = int(time.time() * 1000)
    seqid = uid + timestamp

    fm_decoded = base64.b64decode(urllib.parse.unquote(fm).encode()).decode()
    ws_secret_prefix = fm_decoded.split("_")[0]
    ws_secret_hash = hashlib.md5(
        f"{seqid}|{ctype}|{_CONSTANTS['t']}".encode()
    ).hexdigest()
    ws_secret = hashlib.md5(
        f"{ws_secret_prefix}_{convert_uid}_{stream_name}_{ws_secret_hash}_{ws_time}".encode()
    ).hexdigest()

    params = {
        "wsSecret": ws_secret,
        "wsTime": ws_time,
        "ctype": ctype,
        "fs": fs,
        "seqid": seqid,
        "u": convert_uid,
        "sdk_sid": timestamp,
        "ratio": i_bit_rate,
    }
    params.update(_CONSTANTS)
    return params

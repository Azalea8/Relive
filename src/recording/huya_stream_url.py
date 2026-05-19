"""Huya live stream URL resolver — sync version for Qt integration."""
import base64
import hashlib
import json
import random
import re
import time
import urllib.parse

import httpx
from src.logger import get as _log

log = _log("huya")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.huya.com/",
}


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


def get_stream_url(room_id: str, quality: str = "origin") -> str | None:
    """Get Huya live stream URL. Quality is ignored — Huya auto-selects highest."""
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            # Method 1: PC page stream JSON
            resp = http.get(f"https://www.huya.com/{room_id}", headers=HEADERS)
            html = resp.text

            match = re.search(r'stream: (\{"data".*?),"iWebDefaultBitRate"', html)
            if match:
                stream_json = json.loads(match.group(1) + "}")
                best_url = _pick_best_stream(stream_json)
                if best_url:
                    log.info("room %s: got stream from PC page", room_id)
                    return best_url

            # Method 2: WeChat mini-program API
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
                log.info("room %s: not live", room_id)
                return None
            if profile.get("realLiveStatus") != "ON":
                log.info("room %s: not live", room_id)
                return None

            stream_list = profile.get("stream", {}).get("baseSteamInfoList", [])
            if stream_list:
                best_url = _pick_from_stream_list(stream_list)
                if best_url:
                    log.info("room %s: got stream from API", room_id)
                    return best_url

            log.warning("room %s: no stream URL found", room_id)
            return None

    except Exception as e:
        log.error("room %s: get stream failed: %s", room_id, e)
        return None


def _build_anti_code(old_anti_code: str, stream_name: str) -> str:
    """Rebuild Huya anti-leech params with fresh wsTime so the URL
    does not expire before FFmpeg connects."""
    params_t = 100
    sdk_version = 2403051612
    t13 = int(time.time()) * 1000
    sdk_sid = t13
    init_uuid = (int(t13 % 10 ** 10 * 1000) + int(1000 * random.random())) % 4294967295
    uid = random.randint(1400000000000, 1400009999999)
    seq_id = uid + sdk_sid
    target_unix_time = (t13 + 110624) // 1000
    ws_time = f"{target_unix_time:x}".lower()

    url_query = urllib.parse.parse_qs(old_anti_code)
    fm_decoded = base64.b64decode(urllib.parse.unquote(url_query['fm'][0])).decode()
    ws_secret_pf = fm_decoded.split("_")[0]
    ws_secret_hash = hashlib.md5(f'{seq_id}|{url_query["ctype"][0]}|{params_t}'.encode()).hexdigest()
    ws_secret = f'{ws_secret_pf}_{uid}_{stream_name}_{ws_secret_hash}_{ws_time}'
    ws_secret_md5 = hashlib.md5(ws_secret.encode()).hexdigest()

    return (
        f'wsSecret={ws_secret_md5}&wsTime={ws_time}&seqid={seq_id}'
        f'&ctype={url_query["ctype"][0]}&ver=1&fs={url_query["fs"][0]}'
        f'&uuid={init_uuid}&u={uid}&t={params_t}&sv={sdk_version}'
        f'&sdk_sid={sdk_sid}&codec=264'
    )


def _pick_best_stream(stream_json: dict) -> str | None:
    """Pick highest bitrate HLS stream from PC page JSON."""
    data_list = stream_json.get("data", [])
    if not data_list:
        return None

    best_url = None
    best_bitrate = 0

    for game_info in data_list[0].get("gameStreamInfoList", []):
        hls_url = game_info.get("sHlsUrl", "")
        suffix = game_info.get("sHlsUrlSuffix", "")
        stream_name = game_info.get("sStreamName", "")
        anti_code = game_info.get("sHlsAntiCode", "")
        bitrate = int(game_info.get("iBitRate") or 0)

        if hls_url and suffix and anti_code and bitrate >= best_bitrate:
            best_bitrate = bitrate
            fresh = _build_anti_code(anti_code, stream_name)
            best_url = f"{hls_url}/{stream_name}.{suffix}?{fresh}"

    return best_url


def _pick_from_stream_list(stream_list: list) -> str | None:
    """Pick TX CDN HLS stream from API stream list."""
    selected = None
    for item in stream_list:
        if item.get("sCdnType") == "TX":
            selected = item
            break

    if not selected and stream_list:
        selected = stream_list[0]

    if not selected:
        return None

    hls_url = selected.get("sHlsUrl", "")
    stream_name = selected.get("sStreamName", "")
    anti_code = selected.get("sHlsAntiCode", "")

    if hls_url and stream_name and anti_code:
        fresh = _build_anti_code(anti_code, stream_name)
        return f"{hls_url}/{stream_name}.m3u8?{fresh}"

    return None

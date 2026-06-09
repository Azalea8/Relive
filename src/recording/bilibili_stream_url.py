"""Bilibili live stream URL resolver — sync version for Qt integration."""
import httpx
from src import config
from src.logger import get as _log

log = _log("bili")

_API_PLAYINFO = "https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo"

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://live.bilibili.com/",
}

def _req_headers() -> dict:
    h = dict(_HEADERS)
    cookie = config.BILIBILI_COOKIE.strip()
    if cookie:
        h["Cookie"] = cookie
    return h

RECORD_HEADERS = (
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36\\r\\n"
    "Origin: https://live.bilibili.com\\r\\n"
    "Referer: https://live.bilibili.com/\\r\\n"
)

MPV_HEADER_FIELDS = (
    "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36,"
    "Origin: https://live.bilibili.com,"
    "Referer: https://live.bilibili.com/"
)


def check_live(room_id: str) -> bool:
    """Check if Bilibili room is live."""
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            params = {
                "room_id": room_id,
                "no_playurl": 0,
                "mask": 1,
                "qn": 0,
                "platform": "web",
                "protocol": "0,1",
                "format": "0,1,2",
                "codec": "0,1,2",
                "dolby": 5,
                "panorama": 1,
            }
            resp = http.get(_API_PLAYINFO, params=params, headers=_req_headers())
            data = resp.json()
            if data.get("code") != 0:
                return False
            return data.get("data", {}).get("live_status") == 1
    except Exception as e:
        log.error("check_live failed for room %s: %s", room_id, e)
        return False


def get_stream_url(room_id: str) -> str | None:
    """Get Bilibili live HLS stream URL (source quality). Returns None if not live."""
    try:
        with httpx.Client(timeout=15, follow_redirects=True) as http:
            params = {
                "room_id": room_id,
                "no_playurl": 0,
                "mask": 1,
                "qn": 0,
                "platform": "web",
                "protocol": "0,1",
                "format": "0,1,2",
                "codec": "0,1,2",
                "dolby": 5,
                "panorama": 1,
            }
            resp = http.get(_API_PLAYINFO, params=params, headers=_req_headers())
            data = resp.json()

            if data.get("code") != 0:
                log.warning("room %s: API error code=%s", room_id, data.get("code"))
                return None

            live_data = data.get("data", {})
            live_status = live_data.get("live_status")
            log.info("room %s: live_status=%s", room_id, live_status)

            if live_status != 1:
                log.info("room %s: not live", room_id)
                return None

            playurl = live_data.get("playurl_info", {}).get("playurl", {})
            expected_qn = live_data.get("playurl_info", {}).get("expected_quality", {}).get("qn", "?")
            qn_desc = [f"{q['qn']}({q['desc']})" for q in playurl.get("g_qn_desc", [])]
            log.info("room %s: expected_qn=%s qn_desc=%s", room_id, expected_qn, qn_desc)

            streams = playurl.get("stream", [])

            best_url = ""
            best_qn = -1
            for s in streams:
                if s.get("protocol_name") != "http_hls":
                    continue
                for fmt in s.get("format", []):
                    if fmt.get("format_name") != "ts":
                        continue
                    for codec in fmt.get("codec", []):
                        qn = codec.get("current_qn", 0)
                        url_infos = codec.get("url_info", [])
                        if not url_infos or qn <= best_qn:
                            continue
                        url_info = url_infos[0]
                        best_qn = qn
                        best_url = f"{url_info['host']}{codec['base_url']}{url_info['extra']}"

            if best_url:
                log.info("room %s: got HLS stream (qn=%s)", room_id, best_qn)
                return best_url

            log.warning("room %s: no matching HLS TS stream", room_id)
            return None

    except Exception as e:
        log.error("room %s: get stream failed: %s", room_id, e)
        return None

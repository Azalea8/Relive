"""Douyu live stream URL resolver — sync version for Qt integration."""
import hashlib
import time
import json
from urllib.parse import urlparse, parse_qs
import httpx
from src import config

from src.logger import get as _log

log = _log("douyu")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://www.douyu.com",
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

def check_cookie(room_id, cookie):
    dy_did = cookie["dy_did"]
    url = ""

    try:
        with httpx.Client(timeout=15, follow_redirects=True, cookies=cookie) as http:
            # 1. Check live status
            room_resp = http.get(f"https://www.douyu.com/betard/{room_id}", headers=HEADERS)
            room_data = room_resp.json().get("room", {})

            show_status = room_data.get("show_status")
            # log.info("room %s: show_status=%s", room_id, show_status)

            if show_status != 1:
                log.info("room %s: not live", room_id)
                return None

            # 2. Get encryption params
            white = _get_encryption(http, dy_did)

            # 3. Compute auth
            ts, auth = _compute_auth(room_id, white)

            # 4. Request stream (rate=0 = source)
            """
                斗鱼常见直播流：
                "multirates": [
                    {"name": "原画1080P60", "rate": 0, "bit": 8916},
                    {"name": "蓝光4M", "rate": 4, "bit": 4000},
                    {"name": "超清", "rate": 3, "bit": 2000},
                    {"name": "高清", "rate": 2, "bit": 900}
                ]
            """
            params = {
                "rate": "0",
                "ver": "Douyu_new",
                "ive": "0",
                "hevc": "1",
                "fa": "0",
                "enc_data": white["enc_data"],
                "tt": str(ts),
                "did": dy_did,
                "auth": auth,
                "cdn": "",
            }

            play_resp = http.post(
                f"https://www.douyu.com/lapi/live/getH5PlayV1/{room_id}",
                headers={**HEADERS, 
                            "Origin": "https://www.douyu.com",
                            "Content-Type": "application/x-www-form-urlencoded",
                            },
                data=params,
            )
            play_data = play_resp.json()

            # log.info(f"play_data {play_data}")
            # print(f"\nplay_data:")
            # print(json.dumps(play_data, indent=2, ensure_ascii=False))

            if play_data.get("error") != 0:
                # log.warning("room %s: stream request failed - %s", room_id, play_data.get("msg", "unknown"))
                url = ""

            info = play_data.get("data", {})
            rtmp_url = info.get("rtmp_url", "")
            rtmp_live = info.get("rtmp_live", "")

            if rtmp_url and rtmp_live:
                stream_url = f"{rtmp_url}/{rtmp_live}"
                # log.info("room %s: got stream", room_id)
                url = stream_url

    except Exception as e:
        # log.error("room %s: get stream failed: %s", room_id, e)
        url = ""

    parsed = urlparse(url)
    qs = parse_qs(parsed.query)

    for key in sorted(qs.keys()):
        value = qs[key][0]

        if key == "token" and len(value) > 60:
            value = value[:60] + "..."

    expire = qs.get('expire', [''])[0]
    if expire == "0":
        return True
    else:
        return False

def get_stream_url(room_id: str) -> str | None:
    """Get Douyu live stream URL (source quality). Returns None if not live."""

    cookie = config.DOUYU_COOKIE
    if cookie.get("dy_did"):
        dy_did = cookie.get("dy_did")
        temp = "0"
    else:
        dy_did = "10000000000000000000000000001501" # 游客状态
        temp = "3"

    try:
        with httpx.Client(timeout=15, follow_redirects=True, cookies=cookie) as http:
            # 1. Check live status
            room_resp = http.get(f"https://www.douyu.com/betard/{room_id}", headers=HEADERS)
            room_data = room_resp.json().get("room", {})

            show_status = room_data.get("show_status")
            log.info("room %s: show_status=%s", room_id, show_status)

            if show_status != 1:
                log.info("room %s: not live", room_id)
                return None

            # 2. Get encryption params
            white = _get_encryption(http, dy_did)

            # 3. Compute auth
            ts, auth = _compute_auth(room_id, white)

            # 4. Request stream (rate=0 = source)
            """
                斗鱼常见直播流：
                "multirates": [
                    {"name": "原画1080P60", "rate": 0, "bit": 8916},
                    {"name": "蓝光4M", "rate": 4, "bit": 4000},
                    {"name": "超清", "rate": 3, "bit": 2000},
                    {"name": "高清", "rate": 2, "bit": 900}
                ]
            """
            params = {
                "rate": temp,
                "ver": "Douyu_new",
                "ive": "0",
                "hevc": "1",
                "fa": "0",
                "enc_data": white["enc_data"],
                "tt": str(ts),
                "did": dy_did,
                "auth": auth,
                "cdn": "",
            }

            play_resp = http.post(
                f"https://www.douyu.com/lapi/live/getH5PlayV1/{room_id}",
                headers={**HEADERS, 
                         "Origin": "https://www.douyu.com",
                         "Content-Type": "application/x-www-form-urlencoded",
                         },
                data=params,
            )
            play_data = play_resp.json()

            log.info(f"play_data {play_data}")
            # print(f"\nplay_data:")
            # print(json.dumps(play_data, indent=2, ensure_ascii=False))

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


def _get_encryption(http: httpx.Client, dy_did) -> dict:
    url = f"https://www.douyu.com/wgapi/livenc/liveweb/websec/getEncryption?did={dy_did}"
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

if __name__ == "__main__":
    import time

    ROOM_ID = "3487376"      # 修改为目标房间号
    INTERVAL = 60         # 间隔秒数

    print(f"Monitoring room: {ROOM_ID}")
    print(f"Interval: {INTERVAL}s")

    while True:
        try:
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            
            url = get_stream_url(ROOM_ID)

            print("\n" + "=" * 120)
            print(f"[{now}]")
            with httpx.Client(timeout=15, follow_redirects=True) as http:
                resp = http.get(f"https://www.douyu.com/betard/{ROOM_ID}", headers=HEADERS)
                data = resp.json()

                print(data.get("room", {}))
            if not url:
                print("Room offline or stream URL unavailable")
                time.sleep(INTERVAL)
                continue

            print("URL:")
            print(url)

            parsed = urlparse(url)
            qs = parse_qs(parsed.query)

            print("\nParameters:")
            for key in sorted(qs.keys()):
                value = qs[key][0]

                if key == "token" and len(value) > 60:
                    value = value[:60] + "..."

                print(f"{key:10s}: {value}")

            print("\nKey Fields:")
            print(f"origin     : {qs.get('origin', [''])[0]}")
            print(f"expire     : {qs.get('expire', [''])[0]}")
            print(f"sid        : {qs.get('sid', [''])[0]}")
            print(f"fcdn       : {qs.get('fcdn', [''])[0]}")
            print(f"isp        : {qs.get('isp', [''])[0]}")
            print(f"wsAuth     : {qs.get('wsAuth', [''])[0]}")

        except KeyboardInterrupt:
            print("\nStopped.")
            break

        except Exception as e:
            print(f"\nError: {e}")

        time.sleep(INTERVAL)
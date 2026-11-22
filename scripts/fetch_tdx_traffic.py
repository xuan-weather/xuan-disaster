import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

CLIENT_ID = os.environ["TDX_CLIENT_ID"]
CLIENT_SECRET = os.environ["TDX_CLIENT_SECRET"]

TOKEN_URL = (
    "https://tdx.transportdata.tw/auth/realms/"
    "TDXConnect/protocol/openid-connect/token"
)

DATA_URL = (
    "https://tdx.transportdata.tw/api/basic/v2/"
    "Road/Traffic/Live/City/Taichung"
)

OUTPUT_FILE = Path("data/traffic-city-Taichung.json")


def main():
    session = requests.Session()

    # 取得 TDX Access Token
    print("Requesting TDX access token...")

    token_response = session.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded"
        },
        timeout=30,
    )

    token_response.raise_for_status()

    token = token_response.json().get("access_token")

    if not token:
        raise RuntimeError("TDX access token missing")

    print("TDX authentication successful.")

    # 取得臺中市即時路況
    print("Fetching Taichung traffic data...")

    response = session.get(
        DATA_URL,
        params={
            "$top": "200",
            "$format": "JSON",
        },
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
        timeout=45,
    )

    print("TDX HTTP status:", response.status_code)
    response.raise_for_status()

    payload = response.json()

    if not isinstance(payload, dict):
        raise RuntimeError(
            "Unexpected TDX response type: "
            + type(payload).__name__
        )

    # TDX 縣市即時路況使用 LiveTraffics
    records = payload.get("LiveTraffics", [])

    if not isinstance(records, list):
        raise RuntimeError(
            "TDX LiveTraffics is not an array"
        )

    # 保留原始資料及官方更新時間
    update_time = payload.get("UpdateTime")
    update_interval = payload.get("UpdateInterval")

    result = {
        "success": True,
        "source": "TDX",
        "dataset": "City Live Traffic",
        "city": "Taichung",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "officialUpdateTime": update_time,
        "updateInterval": update_interval,
        "count": len(records),
        "records": records,
    }

    OUTPUT_FILE.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    OUTPUT_FILE.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )

    print("========== RESULT ==========")
    print("Official update time:", update_time)
    print("Update interval:", update_interval)
    print("Traffic records:", len(records))
    print("Output:", OUTPUT_FILE)

    if records:
        first = records[0]
        print("Sample SectionID:", first.get("SectionID"))
        print("Sample TravelSpeed:", first.get("TravelSpeed"))
        print("Sample CongestionLevel:", first.get("CongestionLevel"))
    else:
        print("WARNING: TDX returned an empty LiveTraffics array.")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("TDX fetch failed:", error, file=sys.stderr)
        raise

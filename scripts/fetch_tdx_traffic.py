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


def extract_records(payload):
    """從常見的 TDX JSON 結構擷取資料。"""

    if isinstance(payload, list):
        return payload, "root-array"

    if isinstance(payload, dict):
        possible_keys = [
            "value",
            "Value",
            "data",
            "Data",
            "records",
            "Records",
            "Traffic",
            "traffic",
        ]

        for key in possible_keys:
            value = payload.get(key)

            if isinstance(value, list):
                return value, f"field:{key}"

        # 找出其他可能包含資料的陣列欄位
        array_fields = [
            key for key, value in payload.items()
            if isinstance(value, list)
        ]

        return [], f"no-record-array; array-fields={array_fields}"

    return [], f"unsupported-type:{type(payload).__name__}"


def main():
    session = requests.Session()

    print("========== TDX AUTH ==========")

    token_response = session.post(
        TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
    )

    print("TOKEN HTTP:", token_response.status_code)
    token_response.raise_for_status()

    token_data = token_response.json()
    token = token_data.get("access_token")

    if not token:
        raise RuntimeError("TDX response did not contain access_token")

    print("TDX authentication successful.")

    print("========== TDX TRAFFIC ==========")

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

    print("TRAFFIC HTTP:", response.status_code)
    print("CONTENT TYPE:", response.headers.get("Content-Type"))
    print("FINAL URL:", response.url)

    response.raise_for_status()

    raw_text = response.text

    print("RAW RESPONSE LENGTH:", len(raw_text))
    print("RAW RESPONSE PREVIEW:")
    print(raw_text[:3000])

    try:
        payload = response.json()
    except ValueError as error:
        raise RuntimeError(
            "TDX response is not valid JSON"
        ) from error

    print("PAYLOAD TYPE:", type(payload).__name__)

    if isinstance(payload, dict):
        print("PAYLOAD KEYS:", list(payload.keys()))

    records, extraction_method = extract_records(payload)

    print("EXTRACTION METHOD:", extraction_method)
    print("RECORD COUNT:", len(records))

    result = {
        "success": True,
        "source": "TDX",
        "dataset": "City Live Traffic",
        "city": "Taichung",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
        "httpStatus": response.status_code,
        "payloadType": type(payload).__name__,
        "extractionMethod": extraction_method,
        "count": len(records),
        "records": records,
    }

    # 沒有資料時保留診斷資訊，方便確認真正原因
    if len(records) == 0:
        result["diagnostic"] = {
            "message": "TDX returned no records in recognized array fields",
            "payloadPreview": (
                json.dumps(
                    payload,
                    ensure_ascii=False,
                )[:3000]
            ),
        }

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT_FILE.write_text(
        json.dumps(
            result,
            ensure_ascii=False,
            separators=(",", ":"),
        ) + "\n",
        encoding="utf-8",
    )

    print("========== RESULT ==========")
    print("SUCCESS: JSON generated")
    print("RECORDS:", len(records))
    print("OUTPUT:", OUTPUT_FILE)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("========== ERROR ==========")
        print(str(error), file=sys.stderr)
        raise

"""取得臺北、新北、桃園近 30 天每日雨量，總共最多輸出 20 筆。

python -m pip install requests
python rainfall.py
站表快取 7 天；每次雨量只查詢一次，不分頁、不重試。
"""

import csv
from html.parser import HTMLParser
import os
from pathlib import Path
import ssl
import sys
import time

import requests


API_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/C-B0025-001"
STATIONS_URL = "https://hdps.cwa.gov.tw/static/state.html"
API_KEY = os.environ.get("CWA_API_KEY", "CWA-E934DC66-4DE9-4BDC-8D3A-3065C92D0CDE")
COUNTIES = {"臺北市", "新北市", "桃園市"}
LIMIT = 20  # API 限制測站資料包；整理為逐日紀錄後再限制總共 20 筆。
ROOT = Path(__file__).resolve().parent
STATIONS_CACHE = ROOT / "cwa_stations.csv"
STATION_FIELDS = ["stationid", "stationname", "county", "stationtype"]
RAIN_FIELDS = ["stationid", "stationname", "county", "monitordate", "precipitation", "unit"]


class CwaCertificateAdapter(requests.adapters.HTTPAdapter):
    """相容已實測的 Missing Subject Key Identifier 憑證格式問題。"""

    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context(cafile=requests.certs.where())
        # 保留 CA 信任、效期及網域驗證，僅放寬嚴格 X.509 格式檢查。
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


class StationTableParser(HTMLParser):
    """只解析頁面第一張「現存測站」表，不納入撤銷及暫停站。"""

    def __init__(self):
        super().__init__()
        self.rows = []
        self.in_table = False
        self.finished = False
        self.row = None
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table" and not self.finished:
            self.in_table = True
        elif tag == "tr" and self.in_table:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.in_table:
            self.in_table = False
            self.finished = True


def write_csv(path, fields, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_stations(session):
    if STATIONS_CACHE.exists() and time.time() - STATIONS_CACHE.stat().st_mtime < 7 * 86400:
        with STATIONS_CACHE.open(encoding="utf-8-sig", newline="") as file:
            reader = csv.DictReader(file)
            if not set(STATION_FIELDS).issubset(reader.fieldnames or []):
                raise ValueError("站表快取欄位錯誤，請刪除 cwa_stations.csv 後重新執行。")
            stations = list(reader)
        print("使用站表快取（不重複下載網頁）。")
    else:
        with session.get(STATIONS_URL, timeout=60) as response:
            response.raise_for_status()
            response.encoding = "utf-8"
            parser = StationTableParser()
            parser.feed(response.text)
        if not parser.rows or not {"站號", "站名", "城市"}.issubset(parser.rows[0]):
            raise ValueError("站表格式已變動，無法辨識現存測站表。")
        header = parser.rows[0]
        stations = []
        for row in parser.rows[1:]:
            cells = dict(zip(header, row))
            if not cells.get("站號") or not cells.get("城市"):
                continue
            stations.append({
                "stationid": cells["站號"],
                "stationname": cells["站名"],
                "county": cells["城市"].replace("台", "臺"),
                "stationtype": cells.get("站種", ""),
            })
        if not stations:
            raise ValueError("站表沒有可用測站，不建立快取。")
        write_csv(STATIONS_CACHE, STATION_FIELDS, stations)
        print(f"站表已快取：{STATIONS_CACHE}")

    selected = {row["stationid"]: row for row in stations if row["county"] in COUNTIES}
    if not selected:
        raise ValueError("站表中沒有符合縣市的測站。")
    return selected


def extract_rainfall(payload, stations, start, end):
    if str(payload.get("success")).lower() != "true":
        raise ValueError("氣象署 API 未成功回傳資料，請確認金鑰與查詢條件。")
    locations = payload.get("records", {}).get("location")
    if not isinstance(locations, list):
        raise ValueError("氣象署 API 的資料結構與預期不符。")
    rows = {}
    supported = set()
    for location in locations:
        station_id = location.get("station", {}).get("StationID")
        if station_id not in stations:
            continue
        supported.add(station_id)
        metadata = stations[station_id]
        for observation in location.get("stationObsTimes", {}).get("stationObsTime", []):
            day = observation.get("Date", "")
            if not start <= day < end:
                continue
            value = observation.get("weatherElements", {}).get("Precipitation")
            if value is None:
                continue
            rows[(station_id, day)] = {
                "stationid": station_id,
                "stationname": metadata["stationname"],
                "county": metadata["county"],
                "monitordate": day,
                # 保留 T（微量雨）及其他來源代碼，不將缺值誤轉成 0。
                "precipitation": value,
                "unit": "mm",
            }
    ordered = sorted(rows.values(), key=lambda row: (row["county"], row["stationid"]))
    ordered.sort(key=lambda row: row["monitordate"], reverse=True)
    return ordered[:LIMIT], supported


def main():
    # 沿用 PM2.5：臺灣日期，30 天前（含）至今天（不含）。
    now = time.time() + 8 * 3600
    start = time.strftime("%Y-%m-%d", time.gmtime(now - 30 * 86400))
    end = time.strftime("%Y-%m-%d", time.gmtime(now))
    # CWA 的 timeTo 包含當天，因此使用昨天。
    last_day = time.strftime("%Y-%m-%d", time.gmtime(now - 86400))
    with requests.Session() as session:
        session.mount("https://opendata.cwa.gov.tw/", CwaCertificateAdapter())
        session.mount("https://hdps.cwa.gov.tw/", CwaCertificateAdapter())
        stations = load_stations(session)
        params = {
            "Authorization": API_KEY,
            "format": "JSON",  # 此 API 僅提供 JSON 或 XML；下載後整理為 CSV。
            "offset": 0,
            "limit": LIMIT,
            "StationID": ",".join(stations),
            "timeFrom": start,
            "timeTo": last_day,
            "DataType": "stationObsTimes",
        }
        # 所有站點一次查詢，不逐站呼叫、不分頁、不重試。
        with session.get(API_URL, params=params, timeout=60) as response:
            response.raise_for_status()
            rows, supported = extract_rainfall(response.json(), stations, start, end)

    if not rows:
        raise ValueError("查詢區間沒有雨量資料，未建立或覆寫 CSV。")
    output = ROOT / f"rainfall_{start}_to_{end}_exclusive.csv"
    write_csv(output, RAIN_FIELDS, rows)
    print(f"查詢區間：{start}（含）至 {end}（不含）")
    print(f"縣市站表 {len(stations)} 站，本次 API 回傳其中 {len(supported)} 站。")
    print(f"本次可用資料最新日期：{rows[0]['monitordate']}（未發布日期不補值）")
    print(f"完成：共 {len(rows)} 筆，已儲存至 {output}")


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.SSLError:
        print("下載失敗：HTTPS 憑證驗證失敗。", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException:
        # 不列出包含 API key 的錯誤網址。
        print("下載失敗：請檢查網路、API 金鑰與氣象署服務狀態。", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, csv.Error) as error:
        print(f"下載失敗：{error}", file=sys.stderr)
        sys.exit(1)

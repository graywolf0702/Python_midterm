"""GUI 與命令列共用的下載功能：每類 API 只查一次，不分頁。"""

import calendar
import csv
from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
import time

import requests
import main_file.rainfall as rainfall

ROOT = Path(__file__).resolve().parent
PM25_KEY = os.environ.get("MOENV_API_KEY", "7f824e08-5251-4ab2-b2c2-5ac8dced4615")
CWA_KEY = rainfall.API_KEY
COUNTIES = (
    "臺北市", "新北市", "桃園市", "基隆市", "新竹市", "新竹縣",
    "苗栗縣", "臺中市", "彰化縣", "南投縣", "雲林縣", "嘉義市",
    "嘉義縣", "臺南市", "高雄市", "屏東縣", "宜蘭縣", "花蓮縣",
    "臺東縣", "澎湖縣", "金門縣", "連江縣",
)


def yesterday():
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() + 8 * 3600 - 86400))


def parse_limit(value):
    """空白使用 API 預設；有值時須為 1～1000 的整數。"""
    text = str(value).strip() if value is not None else ""
    if not text:
        return None
    try:
        limit = int(text)
    except ValueError:
        raise ValueError("筆數上限請留空或填入 1～1000 的整數。") from None
    if not 1 <= limit <= 1000:
        raise ValueError("筆數上限請留空或填入 1～1000 的整數。")
    return limit


def date_window(days, end_date):
    """回傳 [開始日, 結束日隔天)，以 time 計算，不依賴電腦時區。"""
    if not isinstance(days, int) or not 1 <= days <= 366:
        raise ValueError("回推天數請填入 1～366 的整數。")
    try:
        parsed = time.strptime(end_date, "%Y-%m-%d")
        if time.strftime("%Y-%m-%d", parsed) != end_date:
            raise ValueError
    except ValueError:
        raise ValueError("結束日期請填入有效日期，例如 2026-10-27。") from None
    if end_date > yesterday():
        raise ValueError("日資料的結束日期請選昨天或更早的日期。")
    exclusive = calendar.timegm(parsed) + 86400
    start = time.strftime("%Y-%m-%d", time.gmtime(exclusive - days * 86400))
    end = time.strftime("%Y-%m-%d", time.gmtime(exclusive))
    return start, end


@dataclass(frozen=True)
class DownloadOptions:
    days: int = 30
    end_date: str = ""
    limit: int | None = None
    counties: tuple = ("臺北市", "新北市", "桃園市")
    output_dir: Path = ROOT
    pm25_key: str = PM25_KEY
    cwa_key: str = CWA_KEY
    station_ids: str = ""
    certificate_compatibility: bool = True
    refresh_stations: bool = False

    def validate(self):
        date_window(self.days, self.end_date)
        if self.limit is not None and (not isinstance(self.limit, int) or not 1 <= self.limit <= 1000):
            raise ValueError("每類筆數上限請留空或填入 1～1000 的整數。")
        if not self.counties or any(county not in COUNTIES for county in self.counties):
            raise ValueError("請至少選擇一個有效縣市。")
        ids = re.split(r"[,，\s]+", self.station_ids.strip())
        if any(value and not re.fullmatch(r"[A-Za-z0-9]{6}", value) for value in ids):
            raise ValueError("雨量站 ID 應為 6 碼英數字，多站請用逗號隔開。")
        return self


@dataclass
class DownloadResult:
    source: str
    path: Path
    rows: list
    note: str


def create_session(options):
    session = requests.Session()
    if options.certificate_compatibility:
        for host in ("https://data.moenv.gov.tw/", "https://opendata.cwa.gov.tw/", "https://hdps.cwa.gov.tw/"):
            session.mount(host, rainfall.CwaCertificateAdapter())
    return session


def save_result(options, source, fields, rows, start, end, note):
    if not rows:
        raise ValueError(f"{source}：查詢範圍沒有可用資料，未建立或覆寫 CSV。")
    prefix = "pm25" if source == "PM2.5" else "rainfall"
    folder = Path(options.output_dir).expanduser().resolve()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{prefix}_{start}_to_{end}_exclusive.csv"
    temporary = path.with_suffix(".csv.tmp")
    rainfall.write_csv(temporary, fields, rows)
    temporary.replace(path)
    return DownloadResult(source, path, rows, note)


def download_pm25(options, log=print):
    options.validate()
    if not options.pm25_key.strip():
        raise ValueError("請填入環境部 API 金鑰。")
    start, end = date_window(options.days, options.end_date)
    counties = list(options.counties)
    if "臺北市" in counties:
        counties.append("台北市")
    filters = "|".join((
        "county,EQ," + ",".join(counties),
        f"monitordate,GR,{start}", f"monitordate,LT,{end}",
    ))
    params = {
        "format": "csv", "offset": 0,
        "api_key": options.pm25_key.strip(), "filters": filters,
        "sort": "monitordate desc,siteid asc",
    }
    if options.limit is not None:
        params["limit"] = options.limit
    limit_text = "使用 API 預設筆數" if options.limit is None else f"最多 {options.limit} 筆"
    log(f"PM2.5：{start} 至 {options.end_date}，{limit_text}；單次查詢。")
    with create_session(options) as session:
        with session.get("https://data.moenv.gov.tw/api/v2/aqx_p_322", params=params, timeout=60) as response:
            response.raise_for_status()
            response.encoding = "utf-8-sig"
            reader = csv.DictReader(io.StringIO(response.text, newline=""))
            fields = [name.strip().lower() for name in (reader.fieldnames or [])]
            if not {"siteid", "county", "monitordate", "concentration"}.issubset(fields):
                raise ValueError("PM2.5 API 未回傳預期的 CSV 欄位，請確認金鑰。")
            reader.fieldnames = fields
            rows = []
            for row in reader:
                normalized = {key: (row.get(key) or "").strip() for key in fields}
                day = normalized["monitordate"][:10].replace("/", "-")
                county = normalized["county"].replace("台", "臺")
                if start <= day < end and county in options.counties:
                    normalized.update(monitordate=day, county=county)
                    rows.append(normalized)
                if options.limit is not None and len(rows) >= options.limit:
                    break
    return save_result(options, "PM2.5", fields, rows, start, end,
                       "上限留空時使用 API 預設筆數；只取單次回傳資料，不補抓其他月份。")


def download_rainfall(options, log=print):
    options.validate()
    if not options.cwa_key.strip():
        raise ValueError("請填入氣象署 API 金鑰。")
    start, end = date_window(options.days, options.end_date)
    limit_text = "使用 API 預設筆數" if options.limit is None else f"最多 {options.limit} 筆"
    log(f"雨量：{start} 至 {options.end_date}，{limit_text}；單次查詢。")
    with create_session(options) as session:
        stations = rainfall.load_stations(session, counties=options.counties, log=log, refresh=options.refresh_stations)
        requested = {value.upper() for value in re.split(r"[,，\s]+", options.station_ids.strip()) if value}
        if requested:
            missing = requested - stations.keys()
            if missing:
                raise ValueError("以下雨量站不在所選縣市的現存站表中：" + ", ".join(sorted(missing)))
            stations = {key: row for key, row in stations.items() if key in requested}
        params = {
            "Authorization": options.cwa_key.strip(), "format": "JSON",
            "offset": 0, "StationID": ",".join(stations),
            "timeFrom": start, "timeTo": options.end_date, "DataType": "stationObsTimes",
        }
        if options.limit is not None:
            params["limit"] = options.limit
        with session.get(rainfall.API_URL, params=params, timeout=60) as response:
            response.raise_for_status()
            rows, supported = rainfall.extract_rainfall(response.json(), stations, start, end, limit=options.limit)
    latest = rows[0]["monitordate"] if rows else "無"
    cap_note = "使用 API 預設筆數，CSV 不另截斷。" if options.limit is None else "CSV 已另限制總筆數。"
    note = (f"站表 {len(stations)} 站，API 回傳 {len(supported)} 站；最新資料 {latest}。"
            f"{cap_note}T 代表微量雨，其他來源代碼原樣保留。")
    return save_result(options, "雨量", rainfall.RAIN_FIELDS, rows, start, end, note)


def error_message(error):
    """避免 Requests 的完整網址洩漏金鑰。"""
    if isinstance(error, requests.exceptions.SSLError):
        return "HTTPS 憑證驗證失敗，請確認系統時間，或勾選憑證格式相容處理。"
    if isinstance(error, requests.exceptions.Timeout):
        return "連線逾時，請稍後再試。"
    if isinstance(error, requests.exceptions.HTTPError):
        status = error.response.status_code if error.response is not None else "未知"
        return f"API 回傳 HTTP {status}，請確認金鑰、權限及服務狀態。"
    if isinstance(error, requests.exceptions.ConnectionError):
        return "無法連線，請檢查網路、DNS 或程式的網路權限。"
    if isinstance(error, requests.exceptions.JSONDecodeError):
        return "API 未回傳有效的 JSON 資料，請稍後再試。"
    if isinstance(error, requests.exceptions.RequestException):
        return "API 請求失敗，請確認網路與服務狀態。"
    if isinstance(error, (ValueError, OSError, csv.Error)):
        return str(error)
    return "發生未預期錯誤，請關閉後重新執行。"

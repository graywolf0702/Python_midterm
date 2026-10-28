"""使用 requests 取得近 30 天內最多 20 筆 PM2.5 日均值（不分頁）。

安裝套件：python -m pip install requests
執行方式：python main.py
"""

import csv
import io
import os
from pathlib import Path
import ssl
import sys
import time

import requests


API_URL = "https://data.moenv.gov.tw/api/v2"
DATASET = "aqx_p_322"
API_KEY = os.environ.get(
    "MOENV_API_KEY", "7f824e08-5251-4ab2-b2c2-5ac8dced4615"
)
FORMAT = "csv"
OFFSET = 0
LIMIT = 20


class MoenvCertificateAdapter(requests.adapters.HTTPAdapter):
    """相容環境部憑證鏈缺少 Subject Key Identifier 的格式。"""

    def init_poolmanager(self, *args, **kwargs):
        context = ssl.create_default_context(cafile=requests.certs.where())
        # 僅放寬嚴格 X.509 格式檢查，仍驗證憑證信任、效期與網域。
        context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        kwargs["ssl_context"] = context
        return super().init_poolmanager(*args, **kwargs)


def main():
    # 用 time 計算臺灣日期；查詢 30 天前（含）到今天（不含）。
    now = time.time() + 8 * 60 * 60
    end = time.strftime("%Y-%m-%d", time.gmtime(now))
    start = time.strftime("%Y-%m-%d", time.gmtime(now - 30 * 24 * 60 * 60))

    # 官方資料使用「臺北市」，同時接受「台北市」。
    filters = (
        "county,EQ,台北市,臺北市,新北市,桃園市"
        f"|monitordate,GR,{start}|monitordate,LT,{end}"
    )
    url = (
        f"{API_URL}/{DATASET}?format={FORMAT}&offset={OFFSET}"
        f"&limit={LIMIT}&api_key={API_KEY}&filters={filters}"
        "&sort=monitordate desc,siteid asc"
    )

    # Session 用來套用憑證相容設定；只呼叫一次 get，不分頁或重試。
    with requests.Session() as session:
        session.mount(f"{API_URL}/", MoenvCertificateAdapter())
        with session.get(url, timeout=60) as response:
            response.raise_for_status()
            response.encoding = "utf-8-sig"
            data = response.text

    reader = csv.DictReader(io.StringIO(data, newline=""))
    fields = {name.strip().lower() for name in (reader.fieldnames or [])}
    if not {"county", "monitordate", "concentration"}.issubset(fields):
        raise ValueError("API 未回傳預期的 CSV 資料，請確認金鑰與 API 狀態。")
    count = sum(1 for row in reader if any(row.values()))

    output = Path(__file__).resolve().parent / f"pm25_{start}_to_{end}_exclusive.csv"
    output.write_text(data, encoding="utf-8-sig", newline="")
    print(f"查詢區間：{start}（含）至 {end}（不含）")
    print(f"完成：共 {count} 筆，已儲存至 {output}")


if __name__ == "__main__":
    try:
        main()
    except requests.exceptions.SSLError:
        print("下載失敗：HTTPS 憑證驗證未通過（先前曾遇到 Missing Subject Key Identifier）。", file=sys.stderr)
        sys.exit(1)
    except requests.exceptions.RequestException:
        # 不印出包含 API key 的完整錯誤網址。
        print("下載失敗：API 請求失敗，請檢查網路、金鑰或 API 狀態。", file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, csv.Error) as error:
        print(f"下載失敗：{error}", file=sys.stderr)
        sys.exit(1)

"""執行 python main.py 開啟 GUI；加上 --cli 使用命令列。"""

import argparse
from pathlib import Path

from collectors import DownloadOptions, ROOT, download_pm25, download_rainfall, error_message, yesterday


def main():
    parser = argparse.ArgumentParser(description="PM2.5 與每日雨量資料下載")
    parser.add_argument("--cli", action="store_true", help="使用命令列，不開啟視窗")
    parser.add_argument("--source", choices=("both", "pm25", "rainfall"), default="both")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=None, help="每類資料的總筆數上限；省略則使用 API 預設")
    parser.add_argument("--end", default=yesterday(), help="結束日期（含當天），YYYY-MM-DD")
    parser.add_argument("--counties", default="臺北市,新北市,桃園市")
    parser.add_argument("--output", type=Path, default=ROOT)
    parser.add_argument("--station-ids", default="")
    args = parser.parse_args()
    if not args.cli:
        from gui import launch
        launch()
        return 0
    try:
        options = DownloadOptions(
            days=args.days, end_date=args.end, limit=args.limit,
            counties=tuple(value.strip().replace("台", "臺") for value in args.counties.split(",")),
            output_dir=args.output, station_ids=args.station_ids,
        ).validate()
    except ValueError as error:
        print(error_message(error))
        return 1
    failed = False
    for source, download in (("pm25", download_pm25), ("rainfall", download_rainfall)):
        if args.source in ("both", source):
            try:
                result = download(options)
                print(f"完成 {result.source}：{len(result.rows)} 筆 → {result.path}")
                print(result.note)
            except Exception as error:
                failed = True
                print(f"{source} 下載失敗：{error_message(error)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

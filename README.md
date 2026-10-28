# 空氣與雨量資料下載

# 點選 launch.pyw 開始使用

視窗使用 Python 內建的 Tkinter，無需額外 GUI 套件。

## 操作

1. 勾選 PM2.5、每日雨量，或同時下載。
2. 設定回推天數、結束日期（含當天）與每類筆數上限。預設近 30 個完整日曆日，上限留空表示 `None`：不傳送 `limit`，使用各 API 的預設筆數，程式也不額外截斷資料。填入數字時，每類仍依該筆數限制。
3. 在縣市清單點選多個縣市，預設臺北、新北、桃園。雨量站 ID 可留空自動選取，也可填入多個 ID，以逗號分隔。
4. 選擇 CSV 儲存資料夾，按「開始下載」。可在右側預覽資料、開啟 CSV 或查看執行紀錄。
5. 在「金鑰與連線」分頁更換 API 金鑰。金鑰預設隱藏，新輸入的金鑰只保留於這次程式執行記憶體。也可事先設定 `MOENV_API_KEY` 與 `CWA_API_KEY` 環境變數。

兩種結果分別儲存為 UTF-8 BOM CSV（方便 Excel 顯示中文）。同一資料夾、同一日期範圍的下載會更新原檔；下載失敗不會覆寫原 CSV。

## 筆數與日期

- 每類 API 固定 `offset=0`，單次查詢，不持續分頁或重試。若同時下載且上限為 20，會分別輸出最多 20 筆 PM2.5 與 20 筆雨量。
- 上限留空使用 API 預設，不等於保證抓取所有歷史資料；仍只有單次請求，實際筆數由 API 決定。命令列不指定 `--limit` 時也是 `None`。
- 筆數上限代表紀錄數，並不保證涵蓋每一天或每個測站。未發布日期不補值；雨量的 `T` 與缺值代碼原樣保留。
- 氣象署的 `limit` 限制巢狀測站資料包，因此雨量在整理為逐日資料後另套用總筆數上限。單次回傳的測站不一定包含所選縣市所有測站。
- PM2.5 使用單次 API 回傳資料，不另下載歷史月份。查詢較舊區間時，可能沒有資料。
- 氣象署站表快取於 `cwa_stations.csv`，有效 7 天。需要時勾選「本次更新雨量站表」；這會額外請求一次站表，不是分頁下載。
- 預設僅對三個資料來源網域套用憑證格式相容設定，處理已遇到的 `Missing Subject Key Identifier`；仍驗證 CA 信任、效期與網域。

## 命令列

```powershell
python main.py --cli --source both --days 30 --limit 20
python main.py --cli --source rainfall --station-ids 466920,467050 --counties 臺北市,桃園市
python -m unittest -v test_collectors.py
```

官方資料來源：[PM2.5 日均值](https://data.moenv.gov.tw/dataset/detail/AQX_P_322)、[每日雨量](https://opendata.cwa.gov.tw/dataset/climate/C-B0025-001)、[現存測站表](https://hdps.cwa.gov.tw/static/state.html#existing_station)。

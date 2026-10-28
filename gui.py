"""Tkinter 操作介面；下載執行緒透過佇列更新畫面。"""

from pathlib import Path
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from main_file.collectors import (
    APP_DIR, COUNTIES, CWA_KEY, PM25_KEY, DEFAULT_OUTPUT_DIR, DownloadOptions, date_window,
    download_pm25, download_rainfall, error_message, parse_limit, yesterday,
)

BG = "#f2f5f8"
NAVY = "#172d40"
MUTED = "#5b6b7b"
ACCENT = "#087f8c"


class DownloadApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("空氣與雨量資料下載")
        self.geometry("1120x850")
        self.minsize(1020, 850)
        self.configure(bg=BG)
        self.messages = queue.Queue()
        self.busy = False
        self.results = {}
        self.pm_enabled = tk.BooleanVar(value=True)
        self.rain_enabled = tk.BooleanVar(value=True)
        self.days = tk.StringVar(value="30")
        self.end_date = tk.StringVar(value=yesterday())
        self.limit = tk.StringVar(value="")
        self.output = tk.StringVar(value=str(DEFAULT_OUTPUT_DIR))
        self.station_ids = tk.StringVar()
        self.pm_key = tk.StringVar(value=PM25_KEY)
        self.cwa_key = tk.StringVar(value=CWA_KEY)
        self.compatibility = tk.BooleanVar(value=True)
        self.refresh_stations = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="準備就緒")
        self.range_label = tk.StringVar()
        self.pm_count = tk.StringVar(value="—")
        self.rain_count = tk.StringVar(value="—")
        self._style()
        self._build()
        self.days.trace_add("write", self._update_range)
        self.end_date.trace_add("write", self._update_range)
        self._update_range()
        self.after(100, self._poll)

    def _style(self):
        self.option_add("*Font", ("Microsoft JhengHei UI", 10))
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TFrame", background=BG)
        style.configure("TLabel", background=BG, foreground=NAVY)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Title.TLabel", font=("Microsoft JhengHei UI", 12, "bold"))
        style.configure("TCheckbutton", background=BG)
        style.configure("TButton", padding=(9, 6))
        style.configure("Primary.TButton", background=ACCENT, foreground="white", padding=(14, 10))
        style.map("Primary.TButton", background=[("active", "#05636e"), ("disabled", "#9bafb6")])
        style.configure("Treeview", rowheight=29, font=("Microsoft JhengHei UI", 10), background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=("Microsoft JhengHei UI", 10, "bold"), padding=(8, 9))
        style.configure("TNotebook.Tab", padding=(15, 8))
        style.configure("TNotebook", background=BG)

    def _build(self):
        header = tk.Frame(self, bg=NAVY, padx=24, pady=18)
        header.pack(fill="x")
        tk.Label(header, text="空氣與雨量資料下載", bg=NAVY, fg="white",
                 font=("Microsoft JhengHei UI", 21, "bold")).pack(anchor="w")
        tk.Label(header, text="PM2.5 日均值  /  每日雨量　·　一次查詢，分別匯出 CSV",
                 bg=NAVY, fg="#bed2df", font=("Microsoft JhengHei UI", 10)).pack(anchor="w", pady=(5, 0))

        body = ttk.Frame(self, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        left = ttk.Frame(body, width=340)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        left.grid_propagate(False)
        self.settings = ttk.Notebook(left)
        self.settings.pack(fill="both", expand=True)
        query = ttk.Frame(self.settings, padding=14)
        keys = ttk.Frame(self.settings, padding=14)
        self.settings.add(query, text="查詢條件")
        self.settings.add(keys, text="金鑰與連線")
        self._query_form(query)
        self._key_form(keys)
        self.start_button = ttk.Button(left, text="開始下載", style="Primary.TButton", command=self.start_download)
        self.start_button.pack(fill="x", pady=(12, 0))

        right = ttk.Frame(body)
        right.grid(row=0, column=1, sticky="nsew")
        summary = ttk.Frame(right)
        summary.pack(fill="x", pady=(0, 12))
        for title, value in (("PM2.5 筆數", self.pm_count), ("雨量筆數", self.rain_count)):
            card = tk.Frame(summary, bg="white", padx=20, pady=13)
            card.pack(side="left", fill="both", expand=True, padx=(0, 8))
            tk.Label(card, text=title, bg="white", fg=MUTED).pack(anchor="w")
            tk.Label(card, textvariable=value, bg="white", fg=ACCENT,
                     font=("Microsoft JhengHei UI", 24, "bold")).pack(anchor="w")
        result_toolbar = ttk.Frame(right)
        result_toolbar.pack(fill="x")
        ttk.Label(result_toolbar, text="下載結果", style="Title.TLabel").pack(side="left")
        ttk.Button(result_toolbar, text="開啟儲存資料夾", command=self.open_folder).pack(side="right")
        ttk.Label(right, text="上限留空使用 API 預設筆數；仍只查詢一次，不分頁。", style="Muted.TLabel").pack(anchor="w", pady=(3, 10))
        # 先保留狀態列空間，再讓結果表格使用剩餘高度。
        ttk.Label(right, textvariable=self.status, style="Muted.TLabel", wraplength=680).pack(side="bottom", anchor="w")
        self.progress = ttk.Progressbar(right, mode="indeterminate")
        self.progress.pack(side="bottom", fill="x", pady=(12, 6))
        self.result_tabs = ttk.Notebook(right)
        self.result_tabs.pack(fill="both", expand=True)
        self.trees = {}
        self.notes = {}
        self.result_frames = {}
        for source, columns in (
            ("PM2.5", (("monitordate", "日期", 105), ("county", "縣市", 80), ("sitename", "測站", 100), ("siteid", "站號", 70), ("concentration", "PM2.5", 80), ("itemunit", "單位", 80))),
            ("雨量", (("monitordate", "日期", 105), ("county", "縣市", 80), ("stationname", "測站", 100), ("stationid", "站號", 85), ("precipitation", "雨量", 75), ("unit", "單位", 65))),
        ):
            frame = ttk.Frame(self.result_tabs, padding=10)
            self.result_tabs.add(frame, text=source)
            self.result_frames[source] = frame
            self.notes[source] = tk.StringVar(value="尚未下載資料。")
            ttk.Label(frame, textvariable=self.notes[source], wraplength=650, style="Muted.TLabel").pack(fill="x", pady=(0, 10))
            ttk.Button(frame, text="開啟此 CSV", command=lambda selected=source: self.open_result(selected)).pack(anchor="e", pady=(0, 6))
            table = ttk.Frame(frame)
            table.pack(fill="both", expand=True)
            table.rowconfigure(0, weight=1)
            table.columnconfigure(0, weight=1)
            tree = ttk.Treeview(table, columns=[col[0] for col in columns], show="headings")
            for key, title, width in columns:
                tree.heading(key, text=title)
                tree.column(key, width=width, minwidth=60, anchor="center")
            tree.grid(row=0, column=0, sticky="nsew")
            vertical = ttk.Scrollbar(table, orient="vertical", command=tree.yview)
            horizontal = ttk.Scrollbar(table, orient="horizontal", command=tree.xview)
            vertical.grid(row=0, column=1, sticky="ns")
            horizontal.grid(row=1, column=0, sticky="ew")
            tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            self.trees[source] = tree
        log_frame = ttk.Frame(self.result_tabs, padding=10)
        self.result_tabs.add(log_frame, text="執行紀錄")
        self.log_text = tk.Text(log_frame, wrap="word", bg="white", fg=NAVY, relief="flat", state="disabled", padx=12, pady=12)
        log_scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(yscrollcommand=log_scroll.set)

    def _query_form(self, frame):
        ttk.Label(frame, text="下載項目", style="Title.TLabel").pack(anchor="w")
        checks = ttk.Frame(frame)
        checks.pack(fill="x", pady=(6, 10))
        ttk.Checkbutton(checks, text="PM2.5", variable=self.pm_enabled).pack(side="left", padx=(0, 20))
        ttk.Checkbutton(checks, text="每日雨量", variable=self.rain_enabled).pack(side="left")
        dates = ttk.Frame(frame)
        dates.pack(fill="x")
        dates.columnconfigure(1, weight=1)
        for row, (label, var, limits) in enumerate((
            ("回推天數", self.days, (1, 366)),
            ("結束日期（含）", self.end_date, None),
            ("上限（留空用預設）", self.limit, (1, 1000)),
        )):
            ttk.Label(dates, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 8))
            widget = ttk.Spinbox(dates, from_=limits[0], to=limits[1], textvariable=var, width=13) if limits else ttk.Entry(dates, textvariable=var, width=15)
            widget.grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(frame, textvariable=self.range_label, foreground=ACCENT, wraplength=280).pack(anchor="w", pady=(6, 4))
        ttk.Button(frame, text="重設近 30 天 / 預設筆數", command=self.reset_dates).pack(fill="x", pady=(0, 10))
        ttk.Label(frame, text="縣市（可多選）").pack(anchor="w")
        city_frame = ttk.Frame(frame)
        city_frame.pack(fill="x", pady=(5, 0))
        self.county_list = tk.Listbox(city_frame, selectmode="multiple", exportselection=False, height=4,
                                     bg="white", selectbackground=ACCENT, relief="solid", borderwidth=1)
        for county in COUNTIES:
            self.county_list.insert("end", county)
        self.county_list.selection_set(0, 2)
        self.county_list.pack(side="left", fill="x", expand=True)
        scroll = ttk.Scrollbar(city_frame, command=self.county_list.yview)
        scroll.pack(side="right", fill="y")
        self.county_list.configure(yscrollcommand=scroll.set)
        shortcuts = ttk.Frame(frame)
        shortcuts.pack(fill="x", pady=(4, 9))
        ttk.Button(shortcuts, text="北北桃", command=self.select_default_counties).pack(side="left")
        ttk.Button(shortcuts, text="全選", command=lambda: self.county_list.selection_set(0, "end")).pack(side="left", padx=5)
        ttk.Button(shortcuts, text="清除", command=lambda: self.county_list.selection_clear(0, "end")).pack(side="left")
        ttk.Label(frame, text="雨量站 ID（選填，多站以逗號分隔）").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.station_ids).pack(fill="x", pady=(5, 3))
        ttk.Label(frame, text="留空：自動取得所選縣市的站點。", style="Muted.TLabel").pack(anchor="w")
        ttk.Checkbutton(frame, text="本次更新雨量站表", variable=self.refresh_stations).pack(anchor="w", pady=(6, 8))
        ttk.Label(frame, text="CSV 儲存資料夾").pack(anchor="w")
        ttk.Entry(frame, textvariable=self.output).pack(fill="x", pady=5)
        ttk.Button(frame, text="選擇資料夾…", command=self.choose_folder).pack(fill="x")

    def _key_form(self, frame):
        ttk.Label(frame, text="API 金鑰", style="Title.TLabel").pack(anchor="w")
        self.key_entries = []
        for title, var in (("環境部（PM2.5）", self.pm_key), ("氣象署（每日雨量）", self.cwa_key)):
            ttk.Label(frame, text=title).pack(anchor="w")
            entry = ttk.Entry(frame, textvariable=var, show="●")
            entry.pack(fill="x", pady=(6, 16))
            self.key_entries.append(entry)
        ttk.Separator(frame).pack(fill="x", pady=20)
        ttk.Checkbutton(frame, text="憑證格式相容處理", variable=self.compatibility).pack(anchor="w")
        ttk.Label(frame, text="處理目前遇到的憑證格式問題，仍保留憑證信任、效期與網域驗證。僅套用於資料來源網站。",
                  wraplength=280, style="Muted.TLabel").pack(anchor="w", pady=(8, 18))
        ttk.Label(frame, text="下載方式", style="Title.TLabel").pack(anchor="w")
        ttk.Label(frame, text="每類資料只查詢一次，不持續分頁。\n雨量站表快取 7 天，必要時額外下載一次站表。\n\n金鑰不會顯示在執行紀錄中；關閉視窗後不儲存你輸入的新金鑰。",
                  wraplength=280, style="Muted.TLabel").pack(anchor="w", pady=8)

    def _update_range(self, *_):
        try:
            start, _ = date_window(int(self.days.get()), self.end_date.get().strip())
            self.range_label.set(f"查詢範圍：{start} ～ {self.end_date.get().strip()}")
        except (ValueError, tk.TclError):
            self.range_label.set("請輸入有效天數與 YYYY-MM-DD 日期。")

    def reset_dates(self):
        self.days.set("30")
        self.end_date.set(yesterday())
        self.limit.set("")

    def select_default_counties(self):
        self.county_list.selection_clear(0, "end")
        self.county_list.selection_set(0, 2)
        self.county_list.see(0)

    def choose_folder(self):
        current = Path(self.output.get().strip()).expanduser()
        selected = filedialog.askdirectory(
            parent=self, title="選擇 CSV 儲存資料夾",
            initialdir=str(current if current.is_dir() else APP_DIR), mustexist=False,
        )
        if selected:
            self.output.set(selected)

    def open_folder(self):
        self._open_path(Path(self.output.get().strip()).expanduser())

    def open_result(self, source):
        result = self.results.get(source)
        if result is None:
            messagebox.showinfo("尚無結果", "請先完成此類資料下載。", parent=self)
            return
        self._open_path(result.path)

    def _open_path(self, path):
        if not path.exists():
            messagebox.showinfo("找不到檔案或資料夾", "請先下載資料，或確認儲存位置。", parent=self)
            return
        try:
            os.startfile(str(path.resolve()))
        except OSError:
            messagebox.showerror("無法開啟", "請用檔案總管開啟此位置：\n" + str(path), parent=self)

    def read_options(self):
        if not self.pm_enabled.get() and not self.rain_enabled.get():
            raise ValueError("請至少勾選一種下載項目。")
        if not self.output.get().strip():
            raise ValueError("請選擇 CSV 儲存資料夾。")
        try:
            days = int(self.days.get())
        except ValueError:
            raise ValueError("回推天數請填入整數。") from None
        limit = parse_limit(self.limit.get())
        options = DownloadOptions(
            days=days, end_date=self.end_date.get().strip(), limit=limit,
            counties=tuple(self.county_list.get(index) for index in self.county_list.curselection()),
            output_dir=Path(self.output.get().strip()), pm25_key=self.pm_key.get().strip(),
            cwa_key=self.cwa_key.get().strip(), station_ids=self.station_ids.get().strip() if self.rain_enabled.get() else "",
            certificate_compatibility=self.compatibility.get(), refresh_stations=self.refresh_stations.get(),
        ).validate()
        if self.pm_enabled.get() and not options.pm25_key:
            raise ValueError("請在「金鑰與連線」填入環境部 API 金鑰。")
        if self.rain_enabled.get() and not options.cwa_key:
            raise ValueError("請在「金鑰與連線」填入氣象署 API 金鑰。")
        return options

    def start_download(self):
        if self.busy:
            return
        try:
            options = self.read_options()
        except ValueError as error:
            messagebox.showerror("請檢查查詢條件", str(error), parent=self)
            return
        jobs = []
        for enabled, source, function in ((self.pm_enabled.get(), "PM2.5", download_pm25), (self.rain_enabled.get(), "雨量", download_rainfall)):
            if enabled:
                jobs.append((source, function))
                self.trees[source].delete(*self.trees[source].get_children())
                self.notes[source].set("下載中…")
                self.results.pop(source, None)
                (self.pm_count if source == "PM2.5" else self.rain_count).set("…")
        self.busy = True
        self.start_button.configure(state="disabled", text="下載中…")
        self.progress.start(12)
        self.status.set("正在下載；本次使用按下開始時的設定。")
        limit_text = "使用 API 預設筆數（None）" if options.limit is None else f"每類最多 {options.limit} 筆"
        self._log("開始新下載；" + limit_text + "。")
        threading.Thread(target=self._worker, args=(options, jobs), daemon=True).start()

    def _worker(self, options, jobs):
        success = 0
        for source, function in jobs:
            try:
                result = function(options, log=lambda text: self.messages.put(("log", text)))
                self.messages.put(("result", result))
                success += 1
            except Exception as error:
                self.messages.put(("error", (source, error_message(error))))
        self.messages.put(("done", (success, len(jobs))))

    def _log(self, message):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{time.strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _poll(self):
        try:
            while True:
                event, data = self.messages.get_nowait()
                if event == "log":
                    self._log(data)
                elif event == "result":
                    self.results[data.source] = data
                    tree = self.trees[data.source]
                    columns = tree["columns"]
                    for row in data.rows:
                        tree.insert("", "end", values=[row.get(key, "") for key in columns])
                    (self.pm_count if data.source == "PM2.5" else self.rain_count).set(str(len(data.rows)))
                    self.notes[data.source].set(data.note)
                    self.result_tabs.select(self.result_frames[data.source])
                    self._log(f"{data.source} 完成：{len(data.rows)} 筆 → {data.path}")
                    self._log(data.note)
                elif event == "error":
                    source, message = data
                    (self.pm_count if source == "PM2.5" else self.rain_count).set("失敗")
                    self.notes[source].set(message)
                    self._log(source + " 下載失敗：" + message)
                elif event == "done":
                    self.busy = False
                    self.progress.stop()
                    self.start_button.configure(state="normal", text="開始下載")
                    success, total = data
                    text = f"完成 {success}/{total} 類下載。CSV 位置請見執行紀錄。"
                    if success != total:
                        text = f"{success}/{total} 類下載成功，失敗原因請見執行紀錄；成功資料已儲存。"
                        self.result_tabs.select(2)
                    self.status.set(text)
                    self._log(text)
        except queue.Empty:
            pass
        self.after(100, self._poll)


def launch():
    DownloadApp().mainloop()


if __name__ == "__main__":
    launch()

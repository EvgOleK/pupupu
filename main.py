import os
import threading
import queue
import json
import time
from pathlib import Path
from datetime import datetime
import customtkinter as ctk
from tkinter import filedialog, messagebox
from tkinter import ttk
import webbrowser

APP_DIR = Path.home() / ".file_finder_app"
APP_DIR.mkdir(exist_ok=True)
SETTINGS_PATH = APP_DIR / "settings.json"


def read_settings():
    try:
        if SETTINGS_PATH.exists():
            with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def write_settings(data):
    try:
        with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class FinderGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Finder — персональная утилита")
        self.geometry("1100x700")

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        s = read_settings()
        self.base_dir = s.get("base_dir", str(Path.cwd()))
        self.last_ext = s.get("last_ext", "*")
        self.exclude_dirs = s.get("exclude_dirs", ["node_modules", ".git"])

        self.result_q = queue.Queue()
        self._stop_flag = threading.Event()
        self.last_search_results = []

        self.sort_directions = {
            "path": True,
            "size": True,
            "modified": True
        }

        self.q_text = ctk.StringVar()
        self.ext_text = ctk.StringVar(value=self.last_ext)
        self.status_text = ctk.StringVar(value="Готово")

        self._build_ui()

    def _build_ui(self):
        top = ctk.CTkFrame(self)
        top.pack(fill="x", padx=14, pady=10)

        self.dir_label = ctk.CTkLabel(top, text=f"Папка: {self.base_dir}", anchor="w")
        self.dir_label.grid(row=0, column=0, columnspan=4, sticky="we", padx=6, pady=(0, 8))

        ctk.CTkLabel(top, text="Введите имя файла").grid(row=1, column=0, sticky="w", padx=6)
        ctk.CTkEntry(top, textvariable=self.q_text, width=320).grid(row=1, column=1, sticky="w", padx=6)

        ctk.CTkLabel(top, text="Расширение").grid(row=2, column=0, sticky="w", padx=6)
        ctk.CTkComboBox(
            top,
            variable=self.ext_text,
            values=["*", "*.txt", "*.py", "*.md", "*.pdf", "*.jpg", "*.png", "*.docx"],
            width=200
        ).grid(row=2, column=1, sticky="w", padx=6)

        ctk.CTkButton(top, text="Выбрать папку", command=self.choose_folder).grid(row=1, column=2, padx=6)
        ctk.CTkButton(top, text="Старт", command=self.start_search).grid(row=2, column=2, padx=6)
        ctk.CTkButton(top, text="Отмена", command=self.cancel_search).grid(row=2, column=3, padx=6)

        self.progress = ctk.CTkProgressBar(self)
        self.progress.set(0)
        self.progress.pack(fill="x", padx=14, pady=(6, 0))
        self.progress.pack_forget()

        status_frame = ctk.CTkFrame(self)
        status_frame.pack(fill="x", padx=14, pady=(6, 0))
        ctk.CTkLabel(status_frame, textvariable=self.status_text, anchor="w").pack(side="left", padx=6)

        table_frame = ctk.CTkFrame(self)
        table_frame.pack(fill="both", expand=True, padx=14, pady=10)

        cols = ("path", "size", "modified")
        self.tree = ttk.Treeview(table_frame, columns=cols, show="headings", height=18)
        self.tree.heading("path", text="Путь", command=lambda: self.sort_by("path"))
        self.tree.heading("size", text="Размер", command=lambda: self.sort_by("size"))
        self.tree.heading("modified", text="Дата изменения", command=lambda: self.sort_by("modified"))

        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self.on_double_click)

    def choose_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.base_dir)
        if chosen:
            self.base_dir = chosen
            self.dir_label.configure(text=f"Папка: {self.base_dir}")
            write_settings({"base_dir": self.base_dir, "last_ext": self.ext_text.get(), "exclude_dirs": self.exclude_dirs})

    def start_search(self):
        query = self.q_text.get().strip()
        ext = self.ext_text.get().strip() or "*"
        if not query and ext == "*":
            messagebox.showwarning("Внимание", "Введите запрос или выберите расширение.")
            return

        for i in self.tree.get_children():
            self.tree.delete(i)
        self.last_search_results.clear()

        self._stop_flag.clear()
        self.progress.pack(fill="x", padx=14, pady=(6, 0))
        self.progress.set(0)
        self.status_text.set("Запуск поиска...")

        t = threading.Thread(target=self._scan_files, args=(query, ext), daemon=True)
        t.start()
        self.after(150, self._process_queue)

        write_settings({"base_dir": self.base_dir, "last_ext": ext, "exclude_dirs": self.exclude_dirs})

    def cancel_search(self):
        if not self._stop_flag.is_set():
            self._stop_flag.set()
            self.status_text.set("Отмена...")

    def on_double_click(self, event):
        sel = self.tree.selection()
        if not sel:
            return
        item = self.tree.item(sel[0])
        path = item["values"][0]
        folder = os.path.dirname(path)
        try:
            webbrowser.open(f"file://{folder}")
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось открыть папку: {e}")

    def _scan_files(self, query, ext_pattern):
        start_time = time.time()
        try:
            root = Path(self.base_dir)
            if not root.exists() or not root.is_dir():
                self.result_q.put(("error", f"Папка недоступна: {self.base_dir}"))
                return

            total = 0
            for _d, _dirs, files in os.walk(root):
                total += len(files)
                if self._stop_flag.is_set():
                    self.result_q.put(("cancelled", None))
                    return

            scanned = 0
            found = []

            ext = ext_pattern if ext_pattern != "*" else None
            if ext:
                ext = ext.replace("*", "").lstrip(".").lower()

            last_progress_update = 0.0
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if d not in self.exclude_dirs]

                if self._stop_flag.is_set():
                    self.result_q.put(("cancelled", None))
                    return

                for fname in filenames:
                    scanned += 1
                    now = time.time()
                    if total and (now - last_progress_update) > 0.3:
                        self.result_q.put(("progress", scanned / total))
                        last_progress_update = now

                    if ext and not fname.lower().endswith(f".{ext}"):
                        continue

                    if query.lower() in fname.lower():
                        full = os.path.join(dirpath, fname)
                        try:
                            st = os.stat(full)
                            size = st.st_size
                            mtime = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                            found.append((full, size, mtime))
                        except PermissionError:
                            pass
                        except FileNotFoundError:
                            pass
                        except OSError:
                            pass

            self.result_q.put(("progress", 1.0))
            self.result_q.put(("result", found))

        except Exception as e:
            self.result_q.put(("error", str(e)))

    def _process_queue(self):
        try:
            while True:
                typ, payload = self.result_q.get_nowait()
                if typ == "progress":
                    self.progress.set(payload)
                    self.status_text.set(f"Поиск... {int(payload*100)}%")
                elif typ == "result":
                    self._show_results(payload)
                    self.status_text.set(f"Готово — {len(payload)} найдено")
                    self.progress.stop()
                    self.progress.pack_forget()
                elif typ == "cancelled":
                    self.status_text.set("Поиск отменён")
                    self.progress.stop()
                    self.progress.pack_forget()
                elif typ == "error":
                    messagebox.showerror("Ошибка", f"Во время поиска произошла ошибка:\n{payload}")
                    self.status_text.set("Ошибка")
                    self.progress.stop()
                    self.progress.pack_forget()
        except queue.Empty:
            self.after(150, self._process_queue)

    def _show_results(self, items):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for p, s, m in items:
            self.tree.insert("", "end", values=(p, s, m))
        self.last_search_results = list(items)

    def sort_by(self, key):
        if not self.last_search_results:
            return
        try:
            reverse_order = not self.sort_directions[key]

            if key == "size":
                self.last_search_results.sort(key=lambda x: int(x[1]), reverse=reverse_order)
            elif key == "modified":
                self.last_search_results.sort(
                    key=lambda x: datetime.strptime(x[2], "%Y-%m-%d %H:%M:%S"),
                    reverse=reverse_order
                )
            else:
                self.last_search_results.sort(key=lambda x: x[0].lower(), reverse=reverse_order)

            self.sort_directions[key] = not self.sort_directions[key]

            self._show_results(self.last_search_results)
        except Exception as e:
            messagebox.showerror("Ошибка сортировки", f"Не удалось отсортировать: {e}")


if __name__ == "__main__":
    app = FinderGUI()
    app.mainloop()
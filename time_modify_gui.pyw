#!/usr/bin/env python3
"""
Time Modify Tool — 时间修改工具
=================================
用于内网 Windows 电脑临时修改系统时间。
功能：设置目标日期/时间 → 自动倒计时 → 恢复到正确时间。

用法：
  双击运行 .pyw 文件（无控制台窗口）
  或命令行：python time_modify_gui.pyw

参考：time for Glodon.bat（原始批处理版本）
"""

# ============================================================
# SECTION 0: IMPORTS
# ============================================================
import ctypes
import datetime
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox

# ============================================================
# SECTION 1: ADMIN & SYSTEM FUNCTIONS
# ============================================================

def is_admin():
    """检查当前进程是否具有管理员权限。"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def elevate_to_admin():
    """以管理员权限重新启动当前脚本。"""
    script_path = os.path.abspath(sys.argv[0])
    ctypes.windll.shell32.ShellExecuteW(
        None,       # hwnd
        "runas",    # 提权动词
        sys.executable,  # python.exe 或 pythonw.exe
        f'"{script_path}"',  # 本脚本路径
        None,       # 工作目录
        1           # SW_SHOWNORMAL
    )


def create_single_instance_mutex():
    """创建命名 Mutex 确保单实例运行。返回 True 表示首个实例。"""
    mutex_name = r"Global\TimeModifyTool_SingleInstance"
    try:
        mutex = ctypes.windll.kernel32.CreateMutexW(None, True, mutex_name)
        if ctypes.windll.kernel32.GetLastError() == 183:
            # ERROR_ALREADY_EXISTS
            return False
        return True
    except Exception:
        return True  # 如果创建失败，允许运行


# ============================================================
# SECTION 2: TIME MANIPULATION (Win32 API)
# ============================================================

class SYSTEMTIME(ctypes.Structure):
    """Windows SYSTEMTIME 结构体，用于 SetLocalTime / GetLocalTime。"""
    _fields_ = [
        ("wYear",         ctypes.c_ushort),
        ("wMonth",        ctypes.c_ushort),
        ("wDayOfWeek",    ctypes.c_ushort),
        ("wDay",          ctypes.c_ushort),
        ("wHour",         ctypes.c_ushort),
        ("wMinute",       ctypes.c_ushort),
        ("wSecond",       ctypes.c_ushort),
        ("wMilliseconds", ctypes.c_ushort),
    ]


def _datetime_to_systemtime(dt):
    """将 Python datetime 转为 SYSTEMTIME 结构体。"""
    st = SYSTEMTIME()
    st.wYear = dt.year
    st.wMonth = dt.month
    st.wDay = dt.day
    st.wHour = dt.hour
    st.wMinute = dt.minute
    st.wSecond = dt.second
    st.wMilliseconds = dt.microsecond // 1000
    st.wDayOfWeek = 0  # 自动计算
    return st


def set_system_time_via_api(dt):
    """通过 kernel32.SetLocalTime 设置系统时间。成功返回 True。"""
    st = _datetime_to_systemtime(dt)
    result = ctypes.windll.kernel32.SetLocalTime(ctypes.byref(st))
    return result != 0


def set_system_time_via_powershell(dt):
    """通过 PowerShell Set-Date 设置系统时间（回退方案）。成功返回 True。"""
    try:
        iso_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", f"Set-Date -Date '{iso_str}'"],
            capture_output=True, text=True, timeout=15
        )
        return result.returncode == 0
    except Exception:
        return False


def set_system_time(dt):
    """
    设置系统时间。先尝试 kernel32 API，失败则回退到 PowerShell。
    成功返回 True。
    """
    if set_system_time_via_api(dt):
        return True
    # 回退到 PowerShell
    return set_system_time_via_powershell(dt)


def get_system_time():
    """获取当前系统本地时间。"""
    return datetime.datetime.now()


# ============================================================
# SECTION 3: RESTORE LOGIC
# ============================================================

class TimeState:
    """保存修改时间前的状态，用于恢复。"""
    __slots__ = ("real_datetime", "perf_counter_start")

    def __init__(self, real_dt, perf_start):
        self.real_datetime = real_dt
        self.perf_counter_start = perf_start


# 全局时间状态
_time_state = None


def save_time_state():
    """在修改系统时间前调用，保存原始时间和单调时钟起点。"""
    global _time_state
    _time_state = TimeState(
        real_dt=datetime.datetime.now(),
        perf_start=time.perf_counter()
    )
    return _time_state


def restore_time_calculated():
    """
    计算恢复：原始时间 + 已过物理时长。
    返回计算出的恢复时间（datetime），或 None（如果没有保存状态）。
    """
    global _time_state
    if _time_state is None:
        return None
    elapsed = time.perf_counter() - _time_state.perf_counter_start
    restore_dt = _time_state.real_datetime + datetime.timedelta(seconds=elapsed)
    return restore_dt


def restore_time_network(server_ip):
    """
    通过网络时间服务器恢复。先尝试 net time，再尝试 w32tm。
    成功返回 True，失败返回 False。
    """
    # 方法1: net time \\server /set /y
    try:
        result = subprocess.run(
            ["net", "time", f"\\\\{server_ip}", "/set", "/y"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    # 方法2: w32tm /resync /computer:server
    try:
        result = subprocess.run(
            ["w32tm", "/resync", f"/computer:{server_ip}"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass

    return False


def clear_time_state():
    """清除保存的时间状态。"""
    global _time_state
    _time_state = None


def has_time_state():
    """是否已保存时间状态（即时间已被修改）。"""
    return _time_state is not None


# ============================================================
# SECTION 4: CONFIG PERSISTENCE
# ============================================================

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "time_modify_config.json")

DEFAULT_CONFIG = {
    "target_date": "2024-11-20",
    "restore_interval": 60,
    "restore_method": "calculated",
    "network_server": "192.168.0.7",
}


def load_config():
    """从 JSON 文件加载配置，文件不存在或损坏则返回默认值。"""
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            # 合并默认值，防止缺少字段
            merged = dict(DEFAULT_CONFIG)
            merged.update(cfg)
            return merged
    except (json.JSONDecodeError, IOError):
        pass
    return dict(DEFAULT_CONFIG)


def save_config(cfg):
    """保存配置到 JSON 文件。"""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except IOError:
        pass  # 静默失败，不影响核心功能


# ============================================================
# SECTION 5: GUI - MAIN APPLICATION CLASS
# ============================================================

class TimeModifyApp:
    """时间修改工具 GUI 主类。"""

    def __init__(self, root):
        self.root = root
        self.state = "IDLE"
        self.countdown_remaining = 0
        self.interval_seconds = 60
        self._countdown_job = None
        self._restore_method = "calculated"
        self._network_server = "192.168.0.7"
        self._config = load_config()

        self.root.title("日期修改工具")
        self.root.geometry("500x580")
        self.root.minsize(440, 520)
        self.root.resizable(True, True)
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        # 图标（任务栏 + 标题栏）
        try:
            ico = os.path.join(os.path.dirname(os.path.abspath(__file__)), "TimeModify.ico")
            if not os.path.exists(ico):
                # PyInstaller onefile 环境，ico 嵌入在 exe 资源中
                ico = sys.executable
            self.root.iconbitmap(ico)
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("TimeModify.DateTool")
        except Exception:
            pass

        self._build_ui()
        self._load_config_to_ui()

    # ---------- UI 构建 ----------

    def _build_ui(self):
        """构建 GUI 布局。"""
        style = ttk.Style()
        style.theme_use("vista")

        # ── 全局字体 ──
        FONT = ("Segoe UI", 10)
        FONT_BOLD = ("Segoe UI", 10, "bold")
        FONT_SMALL = ("Segoe UI", 9)

        style.configure(".", font=FONT)
        style.configure("TLabelframe.Label", font=FONT_BOLD)
        style.configure("Tag.TButton", font=("Segoe UI", 9), padding=(10, 4))
        style.configure("TLabel", font=FONT)

        main = ttk.Frame(self.root, padding=14)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)

        r = 0

        # ── ① 目标日期 ──
        dcard = ttk.LabelFrame(main, text="  目标日期  ", padding=12)
        dcard.grid(row=r, column=0, sticky="ew", pady=(0, 10))
        dcard.columnconfigure(0, weight=1)

        # 日期行 + 快捷预设同行
        dtop = ttk.Frame(dcard)
        dtop.grid(row=0, column=0, sticky="ew")
        ttk.Label(dtop, text="快捷设定:").pack(side=tk.LEFT, padx=(0, 8))
        self._mk_prez(dtop, "3月前", -90)
        self._mk_prez(dtop, "6月前", -180)
        self._mk_prez(dtop, "1年前", -365)
        self._mk_prez(dtop, "2024/11/20", None, "2024-11-20")

        # 日期选择
        drow = ttk.Frame(dcard)
        drow.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        ttk.Label(drow, text="目标日期:").pack(side=tk.LEFT, padx=(0, 8))

        self.year_var = tk.StringVar()
        self.month_var = tk.StringVar()
        self.day_var = tk.StringVar()
        now = datetime.datetime.now()
        years = [str(y) for y in range(now.year - 20, now.year + 5)]
        months = [f"{m:02d}" for m in range(1, 13)]
        days = [f"{d:02d}" for d in range(1, 32)]

        self.year_combo = ttk.Combobox(drow, textvariable=self.year_var,
            values=years, width=6, state="readonly", font=FONT)
        self.year_combo.pack(side=tk.LEFT)
        ttk.Label(drow, text=" 年  ", font=FONT).pack(side=tk.LEFT)
        self.month_combo = ttk.Combobox(drow, textvariable=self.month_var,
            values=months, width=4, state="readonly", font=FONT)
        self.month_combo.pack(side=tk.LEFT)
        ttk.Label(drow, text=" 月  ", font=FONT).pack(side=tk.LEFT)
        self.day_combo = ttk.Combobox(drow, textvariable=self.day_var,
            values=days, width=4, state="readonly", font=FONT)
        self.day_combo.pack(side=tk.LEFT)
        ttk.Label(drow, text=" 日", font=FONT).pack(side=tk.LEFT)

        self.month_combo.bind("<<ComboboxSelected>>", self._update_day_range)
        self.year_combo.bind("<<ComboboxSelected>>", self._update_day_range)

        r += 1

        # ── ② 恢复设置 ──
        rcard = ttk.LabelFrame(main, text="  恢复设置  ", padding=12)
        rcard.grid(row=r, column=0, sticky="ew", pady=(0, 10))

        irow = ttk.Frame(rcard)
        irow.pack(fill=tk.X)
        ttk.Label(irow, text="自动恢复间隔:", font=FONT).pack(side=tk.LEFT, padx=(0, 8))
        self.interval_var = tk.IntVar(value=60)
        self.interval_spin = ttk.Spinbox(irow, from_=10, to=86400, increment=10,
            textvariable=self.interval_var, width=6, font=FONT)
        self.interval_spin.pack(side=tk.LEFT)
        ttk.Label(irow, text=" 秒", font=FONT).pack(side=tk.LEFT, padx=(0, 12))

        ipre = ttk.Frame(rcard)
        ipre.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(ipre, text="快捷:", font=FONT_SMALL).pack(side=tk.LEFT, padx=(0, 6))
        for lbl, sec in [("10秒", 10), ("30秒", 30), ("60秒", 60), ("5分钟", 300)]:
            tk.Button(ipre, text=lbl, width=7,
                command=lambda s=sec: self.interval_var.set(s),
                bg="white", fg="#2563EB", activebackground="#DBEAFE",
                activeforeground="#1D4ED8",
                font=("Segoe UI", 9), relief=tk.FLAT,
                cursor="hand2", padx=6, pady=4, borderwidth=1,
                highlightbackground="#93C5FD", highlightthickness=1).pack(
                    side=tk.LEFT, padx=(0, 5))

        ttk.Separator(rcard, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=10)

        self.restore_method_var = tk.StringVar(value="calculated")
        self.radio_calc = ttk.Radiobutton(rcard,
            text="计算恢复（原始时间 + 已过时长）",
            variable=self.restore_method_var, value="calculated",
            command=self._on_restore_method_changed)
        self.radio_calc.pack(anchor="w")

        nr = ttk.Frame(rcard)
        nr.pack(fill=tk.X, pady=(4, 0))
        self.radio_net = ttk.Radiobutton(nr, text="从时间服务器恢复:  \\\\",
            variable=self.restore_method_var, value="network",
            command=self._on_restore_method_changed)
        self.radio_net.pack(side=tk.LEFT)
        self.server_var = tk.StringVar(value="192.168.0.7")
        self.server_entry = ttk.Entry(nr, textvariable=self.server_var,
            width=16, font=("Segoe UI", 10))
        self.server_entry.pack(side=tk.LEFT)
        self._on_restore_method_changed()

        r += 1

        # ── ③ 操作按钮（椭圆按钮） ──
        btn_row = tk.Frame(main, bg="#F0F0F0")
        btn_row.grid(row=r, column=0, sticky="ew", pady=(2, 10))
        btn_row.columnconfigure(0, weight=1)
        btn_row.columnconfigure(1, weight=1)

        self.set_btn = tk.Button(btn_row, text="修 改 日 期",
            command=self.on_set_time,
            bg="#2563EB", fg="white", activebackground="#1D4ED8",
            activeforeground="white",
            font=("Segoe UI", 11, "bold"),
            relief=tk.FLAT, cursor="hand2", padx=20, pady=8,
            borderwidth=0, highlightthickness=0)
        self.set_btn.grid(row=0, column=0, padx=(0, 6), sticky="ew")

        self.restore_btn = tk.Button(btn_row, text="立 即 恢 复",
            command=self.on_restore_now, state=tk.DISABLED,
            bg="#E2E8F0", fg="#94A3B8", activebackground="#CBD5E1",
            activeforeground="#64748B",
            font=("Segoe UI", 11, "bold"),
            relief=tk.FLAT, cursor="hand2", padx=20, pady=8,
            borderwidth=0, highlightthickness=0)
        self.restore_btn.grid(row=0, column=1, padx=(6, 0), sticky="ew")

        r += 1

        # ── ④ 倒计时 ──
        ccard = ttk.LabelFrame(main, text="  倒计时  ", padding=12)
        ccard.grid(row=r, column=0, sticky="ew", pady=(0, 10))
        ccard.columnconfigure(0, weight=1)

        self.countdown_label = ttk.Label(ccard, text="-- : --",
            font=("Segoe UI", 30, "bold"), anchor="center")
        self.countdown_label.grid(row=0, column=0, sticky="ew", ipady=10)

        self.progress_bar = ttk.Progressbar(ccard, orient=tk.HORIZONTAL,
            mode="determinate")
        self.progress_bar.grid(row=1, column=0, sticky="ew", pady=(8, 2))

        self.progress_label = ttk.Label(ccard, text="等待操作", anchor="center")
        self.progress_label.grid(row=2, column=0, sticky="ew")

        self.status_label = ttk.Label(ccard, text="就绪，等待操作",
            anchor="center", font=FONT_SMALL)
        self.status_label.grid(row=3, column=0, sticky="ew", pady=(10, 0))

        r += 1

    def _mk_prez(self, parent, text, days_off, date_val=None):
        """快捷预设按钮。"""
        tk.Button(parent, text=text,
            command=lambda: self._apply_preset(days_off, date_val),
            bg="white", fg="#2563EB", activebackground="#DBEAFE",
            activeforeground="#1D4ED8",
            font=("Segoe UI", 9), relief=tk.FLAT,
            cursor="hand2", padx=10, pady=4, borderwidth=1,
            highlightbackground="#93C5FD", highlightthickness=1).pack(
                side=tk.LEFT, padx=(0, 6))

    def _apply_preset(self, days_offset, date_val):
        """应用快捷预设日期。"""
        if date_val:
            target = datetime.date.fromisoformat(date_val)
        else:
            target = datetime.date.today() + datetime.timedelta(days=days_offset)
        self.year_var.set(f"{target.year:04d}")
        self.month_var.set(f"{target.month:02d}")
        self._update_day_range()
        self.day_var.set(f"{target.day:02d}")

    def _update_day_range(self, event=None):
        """根据选中的年/月更新日下拉范围。"""
        try:
            year = int(self.year_var.get())
            month = int(self.month_var.get())
            # 计算该月天数
            if month == 12:
                next_month = datetime.date(year + 1, 1, 1)
            else:
                next_month = datetime.date(year, month + 1, 1)
            last_day = (next_month - datetime.timedelta(days=1)).day
            days = [f"{d:02d}" for d in range(1, last_day + 1)]
            self.day_combo["values"] = days
            # 如果当前选中日期无效，调整到该月最后一天
            try:
                current_day = int(self.day_var.get())
                if current_day > last_day:
                    self.day_var.set(f"{last_day:02d}")
            except (ValueError, tk.TclError):
                pass
        except (ValueError, tk.TclError):
            pass

    def _load_config_to_ui(self):
        """将加载的配置填入 UI 控件。"""
        cfg = self._config

        # 解析目标日期
        try:
            target_date = datetime.date.fromisoformat(cfg["target_date"])
            self.year_var.set(f"{target_date.year:04d}")
            self.month_var.set(f"{target_date.month:02d}")
            self._update_day_range()
            self.day_var.set(f"{target_date.day:02d}")
        except (ValueError, KeyError):
            now = datetime.datetime.now()
            self.year_var.set(f"{now.year:04d}")
            self.month_var.set(f"{now.month:02d}")
            self._update_day_range()
            self.day_var.set(f"{now.day:02d}")

        # 间隔
        self.interval_var.set(cfg.get("restore_interval", 60))

        # 恢复方式
        method = cfg.get("restore_method", "calculated")
        self.restore_method_var.set(method)
        self._restore_method = method

        # 服务器IP
        self.server_var.set(cfg.get("network_server", "192.168.0.7"))
        self._network_server = cfg.get("network_server", "192.168.0.7")

        # 根据恢复方式启用/禁用服务器输入
        self._on_restore_method_changed()

    def _on_restore_method_changed(self):
        """恢复方式切换时的处理。"""
        method = self.restore_method_var.get()
        self._restore_method = method
        if method == "network":
            self.server_entry.configure(state="normal")
        else:
            self.server_entry.configure(state="disabled")

    # ---------- 获取目标时间 ----------

    def _get_target_datetime(self):
        """获取目标日期时间（只修改日期，时间保留当前值）。验证失败返回 None。"""
        try:
            year = int(self.year_var.get())
            month = int(self.month_var.get())
            day = int(self.day_var.get())
            now = datetime.datetime.now()
            return datetime.datetime(year, month, day, now.hour, now.minute, now.second)
        except (ValueError, tk.TclError):
            return None

    # ---------- 核心操作 ----------

    def on_set_time(self):
        """点击「修改时间」按钮。"""
        if self.state != "IDLE":
            return

        target_dt = self._get_target_datetime()
        if target_dt is None:
            messagebox.showwarning("输入错误", "请输入有效的日期和时间。")
            return

        self.interval_seconds = self.interval_var.get()
        if self.interval_seconds < 10:
            self.interval_seconds = 10
            self.interval_var.set(10)

        self._restore_method = self.restore_method_var.get()
        self._network_server = self.server_var.get().strip()

        # 检查管理员权限（直接提权，不弹提示）
        if not is_admin():
            self._save_current_config()
            elevate_to_admin()
            self.root.destroy()
            return

        # 保存配置
        self._save_current_config()

        # 保存时间状态
        save_time_state()

        # 设置系统时间
        if not set_system_time(target_dt):
            clear_time_state()
            messagebox.showerror(
                "修改失败",
                "无法设置系统时间。\n请确认已以管理员身份运行此程序。"
            )
            return

        # 进入修改状态
        self.state = "MODIFIED"
        self.countdown_remaining = self.interval_seconds

        # 更新 UI
        self.set_btn.configure(state=tk.DISABLED, bg="#94A3B8")
        self.restore_btn.configure(state=tk.NORMAL,
            bg="#2563EB", fg="white", activebackground="#1D4ED8",
            activeforeground="white")
        self._disable_time_inputs(True)

        target_str = target_dt.strftime("%Y-%m-%d")
        self.status_label.configure(
            text=f"日期已修改为 {target_str}，将在 {self.countdown_remaining} 秒后恢复..."
        )

        # 启动倒计时
        self._start_countdown()

    def on_restore_now(self):
        """点击「立即恢复」按钮。"""
        if self.state != "MODIFIED":
            return
        self._do_restore()

    def _do_restore(self):
        """执行时间恢复。"""
        if self.state == "RESTORING":
            return  # 防止重复调用

        prev_state = self.state
        self.state = "RESTORING"  # 改变状态让后台线程自动退出

        success = False
        message = ""

        if self._restore_method == "network":
            self.status_label.configure(text="正在从时间服务器恢复...")
            self.root.update()
            if restore_time_network(self._network_server):
                success = True
                message = f"已从时间服务器 {self._network_server} 恢复时间。"
            else:
                # 网络恢复失败，回退到计算恢复
                self.status_label.configure(
                    text="服务器不可达，回退到计算恢复..."
                )
                self.root.update()
                restore_dt = restore_time_calculated()
                if restore_dt is not None and set_system_time(restore_dt):
                    success = True
                    message = f"服务器不可达，已通过计算方式恢复时间。"
                else:
                    message = "时间恢复失败，请手动检查系统时间。"
        else:
            # 计算恢复
            restore_dt = restore_time_calculated()
            if restore_dt is not None and set_system_time(restore_dt):
                success = True
                message = f"日期已恢复至 {restore_dt.strftime('%Y-%m-%d')}。"
            else:
                message = "时间恢复失败，请手动检查系统时间。"

        # 清除状态
        clear_time_state()

        # 重置 UI
        self.state = "IDLE"
        self.countdown_remaining = 0
        self.countdown_label.config(text="-- : --")
        self.progress_bar["value"] = 0
        self.progress_label.config(text="等待操作")
        self.set_btn.configure(state=tk.NORMAL, bg="#2563EB")
        self.restore_btn.configure(state=tk.DISABLED,
            bg="#E2E8F0", fg="#94A3B8")
        self._disable_time_inputs(False)
        self.status_label.config(text=message if success else f"⚠ {message}")

        if not success and prev_state == "MODIFIED":
            # 如果是窗口关闭触发的恢复失败，特别提示
            pass

    # ---------- 倒计时 ----------

    def _start_countdown(self):
        """启动倒计时（独立线程 + time.sleep，免疫系统时间跳变）。"""
        if self.state != "MODIFIED":
            return
        self._tick_start = time.perf_counter()
        self._tick_ui()
        t = threading.Thread(target=self._tick_thread, daemon=True)
        t.start()

    def _tick_ui(self):
        """更新倒计时 UI（必须在主线程调用）。"""
        if self.state != "MODIFIED":
            return
        elapsed = time.perf_counter() - self._tick_start
        remaining = max(0, self.interval_seconds - int(elapsed))
        if remaining != self.countdown_remaining:
            self.countdown_remaining = remaining
            m, s = divmod(remaining, 60)
            ts = f"{m:02d} : {s:02d}"
            self.countdown_label.config(text=ts)
            total = self.interval_seconds
            pct = int((total - remaining) / total * 100) if total > 0 else 100
            self.progress_bar["value"] = pct
            self.progress_label.config(text=f"剩余 {remaining} 秒 ({pct}%)")
            self.status_label.config(text=f"将在 {remaining} 秒后自动恢复...")
        if remaining <= 0:
            self.root.after(0, self._do_restore)

    def _tick_thread(self):
        """后台线程：每 0.3 秒触发一次 UI 刷新。"""
        while self.state == "MODIFIED":
            time.sleep(0.3)
            if self.state == "MODIFIED":
                try:
                    self.root.after(0, self._tick_ui)
                except Exception:
                    break

    # ---------- UI 辅助 ----------

    def _disable_time_inputs(self, disabled):
        """启用/禁用日期输入控件。"""
        st = "disabled" if disabled else "readonly"
        ns = "disabled" if disabled else "normal"

        self.year_combo.configure(state=st)
        self.month_combo.configure(state=st)
        self.day_combo.configure(state=st)
        self.interval_spin.configure(state=ns)
        self.radio_calc.configure(state=ns)
        self.radio_net.configure(state=ns)
        self.server_entry.configure(state=ns if self._restore_method == "network" else "disabled")

    def _save_current_config(self):
        """保存当前 UI 设置到配置文件。"""
        try:
            target_dt = self._get_target_datetime()
            target_date = target_dt.strftime("%Y-%m-%d") if target_dt else "2024-11-20"
            config = {
                "target_date": target_date,
                "restore_interval": self.interval_var.get(),
                "restore_method": self.restore_method_var.get(),
                "network_server": self.server_var.get().strip(),
            }
            save_config(config)
        except Exception:
            pass

    # ---------- 窗口关闭处理 ----------

    def on_closing(self):
        """窗口关闭时的处理。如果时间已被修改，先恢复再退出。"""
        if has_time_state() and self.state == "MODIFIED":
            answer = messagebox.askyesno(
                "确认退出",
                "系统时间尚未恢复！\n\n确定要退出吗？\n退出前会自动恢复系统时间。",
                icon="warning"
            )
            if answer:
                self._do_restore()
                # 给恢复操作一点时间生效
                self.root.after(500, self.root.destroy)
            # 如果用户选「否」，什么都不做
            return

        # 保存配置
        self._save_current_config()
        self.root.destroy()


# ============================================================
# SECTION 6: ENTRY POINT
# ============================================================

def main():
    """程序入口。"""

    # === 单实例检查 ===
    if not create_single_instance_mutex():
        try:
            import tkinter.messagebox as mb
            root = tk.Tk()
            root.withdraw()
            mb.showinfo("时间修改工具", "程序已在运行中。\n请查看系统托盘或任务栏。")
            root.destroy()
        except Exception:
            pass
        sys.exit(0)

    # === 管理员权限检查（直接提权，不弹提示） ===
    if not is_admin():
        elevate_to_admin()
        sys.exit(0)

    # === 启动 GUI ===
    root = tk.Tk()
    app = TimeModifyApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

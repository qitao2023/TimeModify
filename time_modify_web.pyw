#!/usr/bin/env python3
"""
TimeModify Web — 日期修改工具 (pywebview 版)
"""

import ctypes
import datetime
import json
import os
import subprocess
import sys
import threading
import time

import webview

# ============================================================
# TIME MANIPULATION
# ============================================================

class SYSTEMTIME(ctypes.Structure):
    _fields_ = [
        ("wYear", ctypes.c_ushort), ("wMonth", ctypes.c_ushort),
        ("wDayOfWeek", ctypes.c_ushort), ("wDay", ctypes.c_ushort),
        ("wHour", ctypes.c_ushort), ("wMinute", ctypes.c_ushort),
        ("wSecond", ctypes.c_ushort), ("wMilliseconds", ctypes.c_ushort),
    ]

def _datetime_to_systemtime(dt):
    st = SYSTEMTIME()
    st.wYear = dt.year; st.wMonth = dt.month; st.wDay = dt.day
    st.wHour = dt.hour; st.wMinute = dt.minute; st.wSecond = dt.second
    st.wMilliseconds = dt.microsecond // 1000
    return st

def set_system_time_via_api(dt):
    st = _datetime_to_systemtime(dt)
    return ctypes.windll.kernel32.SetLocalTime(ctypes.byref(st)) != 0

def set_system_time_via_ps(dt):
    try:
        iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
        r = subprocess.run(["powershell", "-NoProfile", "-Command", f"Set-Date -Date '{iso}'"],
                           capture_output=True, text=True, timeout=15)
        return r.returncode == 0
    except Exception:
        return False

def set_system_time(dt):
    return set_system_time_via_api(dt) or set_system_time_via_ps(dt)

def restore_time_network(server_ip):
    try:
        r = subprocess.run(["net", "time", f"\\\\{server_ip}", "/set", "/y"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0: return True
    except Exception:
        pass
    try:
        r = subprocess.run(["w32tm", "/resync", f"/computer:{server_ip}"],
                           capture_output=True, text=True, timeout=10)
        if r.returncode == 0: return True
    except Exception:
        pass
    return False

# ============================================================
# CONFIG
# ============================================================

CFG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "time_modify_config.json")
DEFAULT_CONFIG = {"target_date": "2024-11-20", "restore_interval": 60,
                  "restore_method": "calculated", "network_server": "192.168.0.7"}

def load_config():
    try:
        if os.path.exists(CFG_PATH):
            with open(CFG_PATH, "r", encoding="utf-8") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
    except Exception:
        pass
    return dict(DEFAULT_CONFIG)

def save_config(cfg):
    try:
        with open(CFG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception:
        pass

# ============================================================
# API (exposed to JS)
# ============================================================

class Api:
    def __init__(self):
        self._time_state = None
        self._interval = 60
        self._restore_method = "calculated"
        self._network_server = "192.168.0.7"
        self._ticking = False
        self._window = None

    def set_window(self, w):
        self._window = w

    def loadConfig(self):
        cfg = load_config()
        self._interval = cfg["restore_interval"]
        self._restore_method = cfg["restore_method"]
        self._network_server = cfg["network_server"]
        return cfg

    def saveConfig(self, target_date, interval, method, server):
        save_config({"target_date": target_date, "restore_interval": int(interval),
                      "restore_method": method, "network_server": server})
        self._interval = int(interval)
        self._restore_method = method
        self._network_server = server

    def modifyTime(self, date_str):
        try:
            d = date_str.split("-")
            now = datetime.datetime.now()
            target = datetime.datetime(int(d[0]), int(d[1]), int(d[2]),
                                       now.hour, now.minute, now.second)
        except Exception:
            return {"success": False, "message": "日期格式无效"}

        self._time_state = {"real_dt": datetime.datetime.now(),
                            "perf_start": time.perf_counter()}
        if not set_system_time(target):
            self._time_state = None
            return {"success": False, "message": "修改失败，请确认以管理员身份运行"}

        self._ticking = True
        threading.Thread(target=self._countdown_thread, daemon=True).start()
        return {"success": True, "message": f"日期已修改为 {target.strftime('%Y-%m-%d')}"}

    def restoreNow(self):
        return self._do_restore()

    def _do_restore(self):
        self._ticking = False
        success, msg = False, ""
        if self._restore_method == "network":
            if restore_time_network(self._network_server):
                success, msg = True, f"已从服务器 {self._network_server} 恢复时间"
            elif self._time_state:
                elapsed = time.perf_counter() - self._time_state["perf_start"]
                restore_dt = self._time_state["real_dt"] + datetime.timedelta(seconds=elapsed)
                if set_system_time(restore_dt):
                    success, msg = True, "服务器不可达，已通过计算恢复时间"
                else:
                    msg = "恢复失败"
        elif self._time_state:
            elapsed = time.perf_counter() - self._time_state["perf_start"]
            restore_dt = self._time_state["real_dt"] + datetime.timedelta(seconds=elapsed)
            if set_system_time(restore_dt):
                success, msg = True, f"已恢复至 {restore_dt.strftime('%Y-%m-%d')}"
            else:
                msg = "恢复失败"
        self._time_state = None
        if self._window:
            self._window.evaluate_js(f"onRestored('{msg}')")
        return {"success": success, "message": msg}

    def _countdown_thread(self):
        last = -1
        while self._ticking and self._window:
            time.sleep(0.3)
            if not self._ticking or not self._time_state:
                break
            elapsed = time.perf_counter() - self._time_state["perf_start"]
            remaining = max(0, self._interval - int(elapsed))
            if remaining != last:
                last = remaining
                total = self._interval
                pct = int((total - remaining) / total * 100) if total > 0 else 100
                try:
                    self._window.evaluate_js(
                        f"updateCountdown({remaining},{total},{pct})")
                except Exception:
                    pass
            if remaining <= 0:
                self._ticking = False
                try:
                    self._window.evaluate_js("countdownFinished()")
                except Exception:
                    pass
                break

# ============================================================
# HTML UI
# ============================================================

HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  :root {
    --bg: #f0f4f8;
    --card: #ffffff;
    --primary: #3b6df0;
    --primary-hover: #2851c8;
    --danger: #ef4444;
    --success: #10b981;
    --text: #1e293b;
    --text2: #64748b;
    --border: #e2e8f0;
    --radius: 14px;
    --shadow: 0 1px 3px rgba(0,0,0,.06), 0 1px 2px rgba(0,0,0,.04);
    --shadow-lg: 0 10px 40px rgba(0,0,0,.08);
  }
  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
    min-height: 100vh; padding: 14px; color: var(--text); font-size: 13px;
    -webkit-font-smoothing: antialiased;
  }
  .container { max-width: 520px; margin: 0 auto; }

  .card {
    background: var(--card); border-radius: var(--radius);
    box-shadow: var(--shadow); padding: 18px; margin-bottom: 12px;
    transition: box-shadow .2s;
  }
  .card:hover { box-shadow: var(--shadow-lg); }

  .card-title {
    font-size: 13px; font-weight: 700; color: var(--text2);
    text-transform: uppercase; letter-spacing: 1px; margin-bottom: 16px;
    display: flex; align-items: center; gap: 8px;
  }
  .card-title::before { content: ''; width: 4px; height: 18px;
    background: var(--primary); border-radius: 2px; }

  .preset-row { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
  .preset {
    padding: 6px 14px; border: 1.5px solid var(--border); border-radius: 20px;
    background: #fff; color: var(--primary); font-size: 13px; cursor: pointer;
    font-weight: 600; transition: all .15s; user-select: none;
  }
  .preset:hover { background: #eff6ff; border-color: var(--primary); }
  .preset:active { background: var(--primary); color: #fff; border-color: var(--primary); }

  .date-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .date-row input {
    padding: 10px 12px; border: 1.5px solid var(--border); border-radius: 10px;
    font-size: 14px; color: var(--text); background: #f8fafc;
    transition: border-color .15s; outline: none; font-family: inherit;
  }
  .date-row input:focus { border-color: var(--primary); background: #fff; }
  .date-row input[type="date"] { flex: 1; }
  .date-row input[type="time"] { width: 130px; }
  .date-row label { font-size: 13px; color: var(--text2); white-space: nowrap; }

  .interval-row { display: flex; align-items: center; gap: 12px; margin-bottom: 12px; }
  .interval-row input[type="range"] { flex: 1; accent-color: var(--primary); }
  .interval-val {
    background: #f1f5f9; padding: 8px 16px; border-radius: 10px;
    font-size: 14px; font-weight: 700; color: var(--primary); min-width: 70px;
    text-align: center;
  }

  .method-row { display: flex; gap: 16px; margin-bottom: 12px; }
  .method-row label { font-size: 13px; color: var(--text); cursor: pointer;
    display: flex; align-items: center; gap: 6px; }
  .method-row input[type="radio"] { accent-color: var(--primary); }

  .server-row { display: flex; align-items: center; gap: 8px; }
  .server-row input {
    padding: 8px 12px; border: 1.5px solid var(--border); border-radius: 10px;
    font-size: 14px; outline: none; width: 160px; font-family: 'Consolas', monospace;
  }
  .server-row input:focus { border-color: var(--primary); }

  .btn-row { display: flex; gap: 12px; margin-bottom: 16px; }
  .btn {
    flex: 1; padding: 12px 16px; border: none; border-radius: 50px;
    font-size: 14px; font-weight: 700; cursor: pointer;
    transition: all .2s; color: #fff; letter-spacing: .5px;
  }
  .btn-primary { background: var(--primary); }
  .btn-primary:hover { background: var(--primary-hover); transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(59,109,240,.35); }
  .btn-primary:disabled { background: #cbd5e1; cursor: not-allowed; transform: none; box-shadow: none; }
  .btn-danger { background: var(--danger); }
  .btn-danger:hover { background: #dc2626; transform: translateY(-1px);
    box-shadow: 0 4px 12px rgba(239,68,68,.35); }
  .btn-danger:disabled { background: #cbd5e1; cursor: not-allowed; transform: none; box-shadow: none; }

  .countdown-area { text-align: center; }
  .countdown-num {
    font-size: 44px; font-weight: 800; color: var(--text);
    font-variant-numeric: tabular-nums; line-height: 1.1;
  }
  .countdown-num.running { color: var(--primary); }
  .countdown-num.done { color: var(--success); }

  .progress-wrap {
    height: 8px; background: #f1f5f9; border-radius: 4px;
    overflow: hidden; margin: 16px 0;
  }
  .progress-fill {
    height: 100%; background: linear-gradient(90deg, #3b6df0, #8b5cf6);
    border-radius: 4px; transition: width .3s linear; width: 0%;
  }

  .status-text {
    font-size: 13px; color: var(--text2); margin-top: 8px;
  }

  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 3px; }
</style>
</head>
<body>
<div class="container">

  <!-- 日期卡片 -->
  <div class="card">
    <div class="card-title">📅 目标日期</div>
    <div class="preset-row">
      <span class="preset" onclick="setPresetDays(-90)">3月前</span>
      <span class="preset" onclick="setPresetDays(-180)">6月前</span>
      <span class="preset" onclick="setPresetDays(-365)">1年前</span>
      <span class="preset" onclick="setPresetDate('2024-11-20')">2024/11/20</span>
    </div>
    <div class="date-row">
      <label>日期</label>
      <input type="date" id="target-date" style="flex:1">
    </div>
  </div>

  <!-- 恢复设置 -->
  <div class="card">
    <div class="card-title">⚙ 恢复设置</div>
    <div class="interval-row">
      <label>间隔</label>
      <input type="range" id="interval" min="10" max="600" value="60" step="10">
      <span class="interval-val" id="interval-val">60秒</span>
    </div>
    <div class="preset-row">
      <span class="preset" onclick="presetInterval(10)">10秒</span>
      <span class="preset" onclick="presetInterval(30)">30秒</span>
      <span class="preset" onclick="presetInterval(60)">1分钟</span>
      <span class="preset" onclick="presetInterval(300)">5分钟</span>
      <span class="preset" onclick="presetInterval(3600)">1小时</span>
    </div>
    <div class="method-row">
      <label><input type="radio" name="method" value="calculated" checked> 计算恢复</label>
      <label><input type="radio" name="method" value="network"> 服务器恢复</label>
    </div>
    <div class="server-row" id="server-row" style="display:none">
      <label>\\\\</label>
      <input type="text" id="server-ip" value="192.168.0.7" placeholder="IP地址">
    </div>
  </div>

  <!-- 按钮 -->
  <div class="btn-row">
    <button class="btn btn-primary" id="btn-modify" onclick="doModify()">修改日期</button>
    <button class="btn btn-danger" id="btn-restore" onclick="doRestore()" disabled>立即恢复</button>
  </div>

  <!-- 倒计时 -->
  <div class="card countdown-area">
    <div class="card-title">⏱ 倒计时</div>
    <div class="countdown-num" id="countdown">-- : --</div>
    <div class="progress-wrap"><div class="progress-fill" id="progress-bar"></div></div>
    <div class="status-text" id="status">就绪，等待操作</div>
  </div>

</div>

<script>
  function getApi() { return window.pywebview && window.pywebview.api; }
  function resetUI() {
    document.getElementById('btn-modify').disabled = false;
    document.getElementById('btn-restore').disabled = true;

    document.getElementById('countdown').textContent = '-- : --';
    document.getElementById('countdown').classList.remove('running', 'done');
    document.getElementById('progress-bar').style.width = '0%';
  }

  // ---- init ----
  function init() {
    var api = getApi();
    if (!api) { setTimeout(init, 200); return; }
    api.loadConfig()
      .then(function(cfg) {
        document.getElementById('target-date').value = cfg.target_date || '2024-11-20';
        document.getElementById('interval').value = cfg.restore_interval || 60;
        updateIntervalLabel();
        var methodRadio = document.querySelector('input[name="method"][value="' + (cfg.restore_method || 'calculated') + '"]');
        if (methodRadio) methodRadio.checked = true;
        document.getElementById('server-ip').value = cfg.network_server || '192.168.0.7';
        toggleServerRow();
      })
      .catch(function(e) { console.error('Init failed:', e); });
  }

  // ---- preset helpers ----
  function setPresetDays(offset) {
    const d = new Date();
    d.setDate(d.getDate() + offset);
    const y = d.getFullYear(), m = String(d.getMonth()+1).padStart(2,'0'), day = String(d.getDate()).padStart(2,'0');
    document.getElementById('target-date').value = y + '-' + m + '-' + day;
  }
  function setPresetDate(dateStr) {
    document.getElementById('target-date').value = dateStr;
  }
  function presetInterval(sec) {
    var slider = document.getElementById('interval');
    // 超过 slider 范围则调整 max
    if (sec > parseInt(slider.max)) { slider.max = sec; }
    slider.value = sec;
    updateIntervalLabel();
    saveSettings();
  }

  // ---- interval ----
  document.getElementById('interval').addEventListener('input', function() {
    updateIntervalLabel();
  });
  document.getElementById('interval').addEventListener('change', function() {
    saveSettings();
  });
  function updateIntervalLabel() {
    const v = parseInt(document.getElementById('interval').value);
    const txt = v >= 60 ? `${v/60}分钟` : `${v}秒`;
    document.getElementById('interval-val').textContent = txt;
  }

  // ---- restore method ----
  document.querySelectorAll('input[name="method"]').forEach(r => {
    r.addEventListener('change', toggleServerRow);
  });
  function toggleServerRow() {
    const net = document.querySelector('input[name="method"]:checked').value === 'network';
    document.getElementById('server-row').style.display = net ? 'flex' : 'none';
  }

  // ---- modify ----
  function doModify() {
    var a = getApi(); if (!a) { alert('API 未就绪'); return; }
    var date = document.getElementById('target-date').value;
    if (!date) { alert('请选择目标日期'); return; }
    var iv = parseInt(document.getElementById('interval').value);
    var mt = document.querySelector('input[name="method"]:checked').value;
    var sv = document.getElementById('server-ip').value;
    a.saveConfig(date, iv, mt, sv)
     .then(function() { return a.modifyTime(date); })
     .then(function(result) {
        if (!result.success) { alert(result.message); return; }
        document.getElementById('btn-modify').disabled = true;
        document.getElementById('btn-restore').disabled = false;

        document.getElementById('status').textContent = result.message;
      })
     .catch(function(e) { alert('操作失败: ' + e.message); });
  }

  // ---- called from Python via evaluate_js ----
  function updateCountdown(remaining, total, pct) {
    var m = Math.floor(remaining / 60);
    var s = remaining % 60;
    document.getElementById('countdown').textContent =
      String(m).padStart(2,'0') + ' : ' + String(s).padStart(2,'0');
    document.getElementById('countdown').classList.add('running');
    document.getElementById('progress-bar').style.width = pct + '%';
    document.getElementById('status').textContent = '将在 ' + remaining + ' 秒后自动恢复...';
  }

  function countdownFinished() {
    document.getElementById('countdown').classList.add('done');
    document.getElementById('countdown').textContent = '00 : 00';
    document.getElementById('progress-bar').style.width = '100%';
    document.getElementById('status').textContent = '倒计时结束，正在恢复...';
    var a = getApi(); if (!a) { resetUI(); return; }
    a.restoreNow().then(function(r) { document.getElementById('status').textContent = r.message; resetUI(); })
                 .catch(function(){ resetUI(); });
  }

  function onRestored(msg) {
    document.getElementById('status').textContent = msg;
    resetUI();
  }

  function saveSettings() {
    var a = getApi(); if (!a) return;
    var d = document.getElementById('target-date').value || '2024-11-20';
    var iv = parseInt(document.getElementById('interval').value);
    var m = document.querySelector('input[name="method"]:checked').value;
    var s = document.getElementById('server-ip').value;
    a.saveConfig(d, iv, m, s).catch(function(){});
  }

  function onRestored(msg) {
    document.getElementById('status').textContent = msg;
    resetUI();
  }

  // ---- restore ----
  function doRestore() {
    var api = getApi(); if (!api) return;
    api.restoreNow()
      .then(function(result) {
        document.getElementById('status').textContent = result.message;
        resetUI();
      })
      .catch(function(e) { alert('恢复失败: ' + e.message); });
  }


  function updateCountdown(remaining, total) {
    const m = Math.floor(remaining / 60);
    const s = remaining % 60;
    document.getElementById('countdown').textContent =
      `${String(m).padStart(2,'0')} : ${String(s).padStart(2,'0')}`;
    document.getElementById('countdown').classList.add('running');
    const pct = total > 0 ? ((total - remaining) / total * 100) : 100;
    document.getElementById('progress-bar').style.width = pct + '%';
    document.getElementById('status').textContent = `将在 ${remaining} 秒后自动恢复...`;
  }


  init();
</script>
</body>
</html>
"""

# ============================================================
# ENTRY POINT
# ============================================================

def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False

def elevate_to_admin():
    script = os.path.abspath(sys.argv[0])
    ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, f'"{script}"', None, 1)

def main():
    if not is_admin():
        elevate_to_admin()
        sys.exit(0)

    api = Api()
    window = webview.create_window(
        "日期修改工具",
        html=HTML,
        js_api=api,
        width=560,
        height=680,
        min_size=(480, 620),
        resizable=True,
    )
    api.set_window(window)
    webview.start()

if __name__ == "__main__":
    main()

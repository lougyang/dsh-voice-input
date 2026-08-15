# -*- coding: utf-8 -*-
"""
系统级语音输入工具 (Windows) —— 独立桌面程序，也可作为 DSH 插件 `dsh-voice-input` 的内置引擎。

特性：
- 全局热键语音转写（默认 F2 按住说话、松开结束），结果自动粘贴到当前光标处
- 全局热键纯录音（默认 F3），保存 WAV 到录音目录
- 历史记录（文本/录音 + 时长 + 时间），托盘「设置」页面里可查看、可复制、可清空
- 托盘「设置」页面：改热键 / 模式 / 模型 / 语言 / 设备 / 录音目录
- 模型懒加载，首次使用自动下载（hf-mirror）；纯本地运行，语音不出本机
"""

import ctypes
import json
import os
import sys
import tempfile
import threading
import time
import wave
from collections import deque
from pathlib import Path

import numpy as np
import sounddevice as sd

APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "config.json"  # 随包默认配置

# 稳定数据目录：放在 LOCALAPPDATA 而不是程序目录里，这样 `dsh plugin update` 重装插件也不会丢
_LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
DATA_DIR = _LOCALAPPDATA / "dsh-voice-input"
USER_CONFIG_PATH = DATA_DIR / "config.json"   # 用户在设置页保存的配置（最高优先级）
HISTORY_PATH = DATA_DIR / "history.json"      # 历史记录持久化
LOG_PATH = DATA_DIR / "voice_input.log"       # 运行日志

# 持有 os.add_dll_directory 句柄，防止被 GC 后 CUDA DLL 搜索路径失效
_CUDA_DLL_HANDLES = []

# 单实例互斥量句柄（保持引用，防止内核对象被提前销毁）
_SINGLETON_HANDLE = None

DEFAULTS = {
    "hotkey": "f2",             # 语音热键
    "mode": "hold",             # hold / toggle
    "model": "small",           # tiny / base / small / medium / large-v3
    "language": "zh",           # zh / en / auto(自动)
    "device": "auto",           # auto / cpu / cuda
    "compute_type": "default",
    "samplerate": 16000,
    "beam_size": 5,
    "vad_filter": True,
    "insert_mode": "paste",     # paste / type / none
    "hf_endpoint": "https://hf-mirror.com",
    "initial_prompt_zh": "以下是普通话的句子。",
    "history_size": 500,        # 历史记录保留条数
    "record_enabled": True,     # 是否启用纯录音功能
    "record_hotkey": "f3",      # 录音热键
    "record_mode": "toggle",    # toggle / hold
    "record_dir": "",           # 录音保存目录；空则用 %USERPROFILE%\\Recordings
    "idle_unload_seconds": 120,  # 空闲多少秒后自动卸载模型释放显存/内存；0=常驻不卸载
}


def log(msg: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        line = time.strftime("%Y-%m-%d %H:%M:%S") + " " + str(msg)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        print(msg, flush=True)
    except Exception:
        pass


def _setup_stdout() -> None:
    # pythonw 下 stdout/stderr 为 None，print 会崩。
    # stdout 指向 devnull（避免 log() 的 print 与写文件重复），stderr 指向日志文件捕获报错。
    try:
        if sys.stdout is None:
            sys.stdout = open(os.devnull, "w", encoding="utf-8")
        if sys.stderr is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            sys.stderr = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
    except Exception:
        pass


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    # 1. 随包默认 engine/config.json
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            log("[warn] 读取 config.json 失败: %s" % e)
    # 2. DSH 插件通过环境变量注入 cordis.patch.yml 里的 config
    try:
        env_cfg = json.loads(os.environ.get("DSH_VOICE_CONFIG", "{}"))
        if isinstance(env_cfg, dict):
            cfg.update(env_cfg)
    except Exception:
        pass
    # 3. 用户在设置页保存的本地配置（最高优先级，稳定、不随插件更新丢失）
    if USER_CONFIG_PATH.exists():
        try:
            with open(USER_CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            log("[warn] 读取用户配置失败: %s" % e)
    if cfg.get("hf_endpoint"):
        os.environ["HF_ENDPOINT"] = cfg["hf_endpoint"]
    return cfg


def _ensure_single_instance() -> bool:
    """Windows 命名互斥量：保证同时只有一个实例在跑。返回 True 表示可继续。"""
    global _SINGLETON_HANDLE
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _SINGLETON_HANDLE = kernel32.CreateMutexW(None, False, "DSH_VOICE_INPUT_SINGLETON")
        return ctypes.get_last_error() != 183  # 183 = ERROR_ALREADY_EXISTS
    except Exception:
        return True


class VoiceInputApp:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.state = "idle"          # idle / recording / transcribing
        self.record_kind = None      # transcribe / audio
        self.record_start = 0.0
        self.frames = None
        self.stream = None
        self.model = None
        self.model_lock = threading.RLock()
        self.used_device = None
        self._last_use_time = 0.0       # 最近一次用到模型的时间（用于空闲卸载）
        self._last_unload_check = 0.0
        self.history = self._load_history()
        self.sr = int(cfg.get("samplerate", 16000))
        self._quit_flag = False
        self.icon = None
        self._tk_root = None
        self._settings_win = None
        self._open_settings = False

    # -- 错误提示（仅关键错误弹窗）-----------------------------------------
    def _notify(self, title, text):
        try:
            ctypes.windll.user32.MessageBoxW(0, str(text), str(title), 0x40)
        except Exception:
            log("%s: %s" % (title, text))

    # -- 托盘 --------------------------------------------------------------
    def _setup_tray(self):
        try:
            import pystray
            from pystray import Menu, MenuItem
            from PIL import Image, ImageDraw
        except Exception as e:
            log("[warn] 托盘不可用: %s" % e)
            return

        img = Image.new("RGBA", (64, 64), (31, 36, 48, 255))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([27, 8, 37, 30], radius=5, fill=(238, 240, 245, 255))
        d.arc([17, 15, 47, 46], 210, 330, fill=(238, 240, 245, 255), width=5)
        d.rectangle([25, 42, 39, 48], fill=(238, 240, 245, 255))
        d.rectangle([28, 48, 36, 55], fill=(238, 240, 245, 255))

        def copy_last(icon, item):
            self._copy_history(-1)

        def open_settings(icon, item):
            self._open_settings = True  # 主线程轮询到后打开设置窗口

        def quit_app(icon, item):
            self._quit_flag = True

        self.icon = pystray.Icon("voice_input", img, "语音输入", Menu(
            MenuItem("📋 复制最近识别", copy_last),
            MenuItem("⚙️ 设置…", open_settings, default=True),
            MenuItem("退出", quit_app),
        ))
        self.icon.run_detached()

    def _copy_history(self, idx):
        try:
            import pyperclip
            if self.history:
                entry = self.history[idx] if -len(self.history) <= idx < len(self.history) else None
                if entry and entry.get("kind") == "text":
                    pyperclip.copy(entry["text"])
        except Exception as e:
            log("[warn] 复制历史失败: %s" % e)

    # -- 历史记录 ----------------------------------------------------------
    def _history_maxlen(self):
        return max(10, int(self.cfg.get("history_size", 500)))

    def _load_history(self) -> deque:
        try:
            if HISTORY_PATH.exists():
                data = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    return deque(data, maxlen=self._history_maxlen())
        except Exception as e:
            log("[warn] 读取历史失败: %s" % e)
        return deque(maxlen=self._history_maxlen())

    def _save_history(self):
        try:
            HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            HISTORY_PATH.write_text(json.dumps(list(self.history), ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log("[warn] 保存历史失败: %s" % e)

    def _add_history_entry(self, kind, text=None, file=None, duration=0.0):
        entry = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind, "duration": round(float(duration), 1)}
        if text is not None:
            entry["text"] = text
        if file is not None:
            entry["file"] = file
        self.history.append(entry)
        self._save_history()

    def _reload_history_size(self):
        newmax = self._history_maxlen()
        if self.history.maxlen != newmax:
            self.history = deque(list(self.history), maxlen=newmax)

    # -- 录音 --------------------------------------------------------------
    def _audio_callback(self, indata, frames, time_info, status):
        if self.frames is not None:
            self.frames.append(indata.copy())

    def _snapshot(self):
        if not self.frames:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(list(self.frames))

    def _start_recording(self, kind="transcribe"):
        if self.state != "idle":
            return
        self.state = "recording"
        self.record_kind = kind
        self.record_start = time.time()
        self.frames = []
        try:
            self.stream = sd.InputStream(
                samplerate=self.sr, channels=1, dtype="int16", callback=self._audio_callback,
            )
            self.stream.start()
        except Exception as e:
            self.state = "idle"
            self.frames = None
            self._notify("语音输入", "无法打开麦克风：%s" % e)

    def _stop_recording(self):
        if self.state != "recording":
            return
        duration = time.time() - self.record_start
        kind = self.record_kind
        self.state = "transcribing"
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        self.stream = None
        data = self._snapshot()
        self.frames = None
        if kind == "audio":
            threading.Thread(target=self._save_recording, args=(data, duration), daemon=True).start()
        else:
            threading.Thread(target=self._transcribe_and_paste, args=(data, duration), daemon=True).start()

    # -- CUDA / 模型 / 转写 ------------------------------------------------
    @staticmethod
    def _setup_cuda_dll_dirs():
        import importlib.util
        bin_dirs = []
        for pkg in ("nvidia.cublas", "nvidia.cudnn", "nvidia.cuda_runtime"):
            try:
                spec = importlib.util.find_spec(pkg)
            except Exception:
                continue
            if spec is None or not spec.submodule_search_locations:
                continue
            for loc in spec.submodule_search_locations:
                bin_dir = Path(loc) / "bin"
                if bin_dir.is_dir():
                    bin_dirs.append(str(bin_dir))
                    try:
                        _CUDA_DLL_HANDLES.append(os.add_dll_directory(str(bin_dir)))
                    except Exception:
                        pass
        if bin_dirs:
            os.environ["PATH"] = os.pathsep.join(bin_dirs) + os.pathsep + os.environ.get("PATH", "")

    def _get_model(self):
        self._last_use_time = time.time()  # 记录最近一次使用时间（用于空闲卸载）
        if self.model is not None:
            return self.model
        with self.model_lock:
            if self.model is not None:
                return self.model
            from faster_whisper import WhisperModel
            size = self.cfg["model"]
            device = self.cfg["device"]
            compute_type = self.cfg["compute_type"]
            if device == "auto":
                self._setup_cuda_dll_dirs()
                try:
                    m = WhisperModel(size, device="cuda", compute_type="float16")
                    self.used_device = "cuda"
                except Exception:
                    m = WhisperModel(size, device="cpu", compute_type="int8")
                    self.used_device = "cpu"
            else:
                if device == "cuda":
                    self._setup_cuda_dll_dirs()
                if compute_type == "default":
                    compute_type = "float16" if device == "cuda" else "int8"
                m = WhisperModel(size, device=device, compute_type=compute_type)
                self.used_device = device
            self.model = m
            log("[info] 模型加载完成，设备=%s" % self.used_device)
            return m

    def _unload_model_if_idle(self):
        """空闲超时后卸载模型，释放显存/内存；下次用到再懒加载。"""
        timeout = int(self.cfg.get("idle_unload_seconds", 120))
        if timeout <= 0 or self.model is None or self.state != "idle":
            return
        if time.time() - self._last_use_time >= timeout:
            self.model = None
            self.used_device = None
            import gc
            gc.collect()
            log("[info] 模型已空闲自动卸载，释放显存/内存")

    def _write_wav(self, path: Path, data: np.ndarray):
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sr)
            w.writeframes(data.tobytes())

    def _save_wav(self, data: np.ndarray) -> str:
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="voice_input_")
        os.close(fd)
        self._write_wav(Path(path), data)
        return path

    def _transcribe_data(self, data: np.ndarray, beam_size=None) -> str:
        model = self._get_model()
        wav = self._save_wav(data)
        try:
            lang = self.cfg.get("language") or None
            if lang in ("auto", "null", ""):
                lang = None
            kwargs = dict(
                language=lang,
                beam_size=int(beam_size or self.cfg.get("beam_size", 5)),
                vad_filter=bool(self.cfg.get("vad_filter", True)),
            )
            if lang == "zh" and self.cfg.get("initial_prompt_zh"):
                kwargs["initial_prompt"] = self.cfg["initial_prompt_zh"]
            segments, _info = model.transcribe(wav, **kwargs)
            return "".join(seg.text for seg in segments).strip()
        finally:
            try:
                os.remove(wav)
            except OSError:
                pass

    def _transcribe_and_paste(self, data: np.ndarray, duration: float):
        try:
            text = self._transcribe_data(data)
            text = (text or "").strip()
            if not text:
                log("[warn] 没有识别到内容")
                return
            self._insert_text(text)
            self._add_history_entry(kind="text", text=text, duration=duration)
        except Exception as e:
            log("[error] 识别失败: %s" % e)
        finally:
            self.state = "idle"

    def _insert_text(self, text: str):
        import pyperclip
        pyperclip.copy(text)
        mode = self.cfg.get("insert_mode", "paste")
        if mode == "paste":
            import keyboard
            time.sleep(0.05)
            keyboard.send("ctrl+v")
        elif mode == "type":
            import keyboard
            keyboard.write(text)
        # mode == "none"：只进剪贴板，不自动粘贴

    # -- 纯录音 ------------------------------------------------------------
    def _record_dir(self) -> Path:
        d = self.cfg.get("record_dir") or ""
        if d:
            return Path(os.path.expandvars(os.path.expanduser(d)))
        return Path.home() / "Recordings"

    def _save_recording(self, data: np.ndarray, duration: float):
        try:
            rec_dir = self._record_dir()
            fname = time.strftime("recording_%Y%m%d_%H%M%S.wav")
            path = rec_dir / fname
            self._write_wav(path, data)
            self._add_history_entry(kind="record", file=str(path), duration=duration)
            log("[info] 录音已保存: %s (%.1fs)" % (path, duration))
        except Exception as e:
            log("[error] 录音保存失败: %s" % e)
            self._notify("语音输入", "录音保存失败：%s" % e)
        finally:
            self.state = "idle"

    # -- 热键 --------------------------------------------------------------
    def _register_hotkeys(self):
        import keyboard
        try:
            keyboard.unhook_all()  # 清掉旧热键，重注册
        except Exception:
            pass
        mode = self.cfg.get("mode", "toggle")
        hotkey = self.cfg.get("hotkey", "f2")
        try:
            if mode == "hold":
                keyboard.on_press_key(hotkey, lambda _e: self._start_recording("transcribe"), suppress=False)
                keyboard.on_release_key(hotkey, lambda _e: self._stop_recording(), suppress=False)
            else:
                keyboard.add_hotkey(hotkey, lambda: self._on_hotkey())
        except Exception as e:
            log("[error] 语音热键注册失败: %s" % e)
            self._notify("语音输入", "语音热键注册失败：%s" % e)

        if self.cfg.get("record_enabled", True):
            rk = self.cfg.get("record_hotkey", "f3")
            rm = self.cfg.get("record_mode", "toggle")
            try:
                if rm == "hold":
                    keyboard.on_press_key(rk, lambda _e: self._start_recording("audio"), suppress=False)
                    keyboard.on_release_key(rk, lambda _e: self._stop_recording(), suppress=False)
                else:
                    keyboard.add_hotkey(rk, lambda: self._on_record_hotkey())
            except Exception as e:
                log("[error] 录音热键注册失败: %s" % e)
                self._notify("语音输入", "录音热键注册失败：%s" % e)
            log("热键已注册：语音=%s(%s) 录音=%s(%s)" % (hotkey, mode, rk, rm))
        else:
            log("热键已注册：语音=%s(%s) 录音=关闭" % (hotkey, mode))

    def _on_hotkey(self):
        if self.state == "idle":
            self._start_recording("transcribe")
        elif self.state == "recording" and self.record_kind == "transcribe":
            self._stop_recording()

    def _on_record_hotkey(self):
        if self.state == "idle":
            self._start_recording("audio")
        elif self.state == "recording" and self.record_kind == "audio":
            self._stop_recording()

    # -- 配置保存 ----------------------------------------------------------
    def _save_user_config(self):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            USER_CONFIG_PATH.write_text(json.dumps(self.cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            log("[warn] 保存配置失败: %s" % e)
            self._notify("语音输入", "保存配置失败：%s" % e)

    # -- 设置页面 ----------------------------------------------------------
    def _show_settings(self):
        import tkinter as tk
        from tkinter import ttk, filedialog

        win = self._settings_win
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            return

        win = tk.Toplevel(self._tk_root)
        self._settings_win = win
        win.title("语音输入设置")
        win.geometry("680x560")
        win.minsize(620, 480)

        v_hotkey = tk.StringVar(value=str(self.cfg.get("hotkey", "f2")))
        v_mode = tk.StringVar(value=str(self.cfg.get("mode", "toggle")))
        v_record_enabled = tk.BooleanVar(value=bool(self.cfg.get("record_enabled", True)))
        v_record_hotkey = tk.StringVar(value=str(self.cfg.get("record_hotkey", "f3")))
        v_record_mode = tk.StringVar(value=str(self.cfg.get("record_mode", "toggle")))
        v_record_dir = tk.StringVar(value=str(self.cfg.get("record_dir", "") or ""))
        v_model = tk.StringVar(value=str(self.cfg.get("model", "small")))
        v_language = tk.StringVar(value=str(self.cfg.get("language", "zh")))
        v_device = tk.StringVar(value=str(self.cfg.get("device", "auto")))
        v_idle_unload = tk.StringVar(value=str(self.cfg.get("idle_unload_seconds", 120)))

        nb = ttk.Notebook(win)
        nb.pack(fill="both", expand=True, padx=10, pady=10)

        # ---- 热键 / 录音 tab ----
        f1 = ttk.Frame(nb, padding=12)
        nb.add(f1, text=" 热键与录音 ")

        g1 = ttk.LabelFrame(f1, text="语音转写", padding=10)
        g1.pack(fill="x", pady=4)
        ttk.Label(g1, text="语音热键：").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(g1, textvariable=v_hotkey, width=20).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(g1, text="（如 f2 / f8 / ctrl+alt+space）").grid(row=0, column=2, sticky="w", padx=4)
        ttk.Label(g1, text="触发方式：").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(g1, textvariable=v_mode, values=["hold", "toggle"], state="readonly", width=12).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(g1, text="hold=按住说话松开结束；toggle=按一次开/再按关").grid(row=1, column=2, sticky="w", padx=4)

        g2 = ttk.LabelFrame(f1, text="纯录音（只录声音、不转写）", padding=10)
        g2.pack(fill="x", pady=4)
        ttk.Checkbutton(g2, text="启用录音功能", variable=v_record_enabled).grid(row=0, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(g2, text="录音热键：").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(g2, textvariable=v_record_hotkey, width=20).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(g2, text="触发方式：").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(g2, textvariable=v_record_mode, values=["toggle", "hold"], state="readonly", width=12).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(g2, text="保存目录：").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(g2, textvariable=v_record_dir, width=46).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Button(g2, text="浏览…", command=lambda: v_record_dir.set(filedialog.askdirectory() or v_record_dir.get())).grid(row=3, column=2, padx=4)

        # ---- 识别 tab ----
        f2 = ttk.Frame(nb, padding=12)
        nb.add(f2, text=" 识别 ")
        g3 = ttk.LabelFrame(f2, text="模型与设备", padding=10)
        g3.pack(fill="x", pady=4)
        ttk.Label(g3, text="模型：").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Combobox(g3, textvariable=v_model, values=["tiny", "base", "small", "medium", "large-v3"], state="readonly", width=12).grid(row=0, column=1, sticky="w", padx=6)
        ttk.Label(g3, text="语言：").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Combobox(g3, textvariable=v_language, values=["zh", "en", "auto"], state="readonly", width=12).grid(row=1, column=1, sticky="w", padx=6)
        ttk.Label(g3, text="设备：").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Combobox(g3, textvariable=v_device, values=["auto", "cuda", "cpu"], state="readonly", width=12).grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(g3, text="空闲卸载(秒)：").grid(row=3, column=0, sticky="w", pady=4)
        ttk.Entry(g3, textvariable=v_idle_unload, width=12).grid(row=3, column=1, sticky="w", padx=6)
        ttk.Label(g3, text="0=常驻不卸载；>0 表示闲置这么久后自动释放显存/内存（下次用时重新加载）").grid(row=3, column=2, sticky="w", padx=4)
        ttk.Label(f2, text="说明：模型越大越准越慢；small 是中文准确率与速度的平衡点。\n模型只在第一次用时才加载（懒加载），闲置后按上面设置自动卸载。", foreground="#666").pack(anchor="w", pady=8)

        # ---- 历史记录 tab ----
        f3 = ttk.Frame(nb, padding=10)
        nb.add(f3, text=" 历史记录 ")
        cols = ("time", "dur", "content")
        tree = ttk.Treeview(f3, columns=cols, show="headings", height=16)
        tree.heading("time", text="时间")
        tree.heading("dur", text="时长")
        tree.heading("content", text="内容")
        tree.column("time", width=140, anchor="center")
        tree.column("dur", width=60, anchor="center")
        tree.column("content", width=400)

        def refresh():
            tree.delete(*tree.get_children())
            for e in reversed(list(self.history)):
                ts = e.get("ts", "")
                dur = "%.1fs" % e.get("duration", 0)
                if e.get("kind") == "record":
                    content = "🎙 " + e.get("file", "")
                else:
                    content = e.get("text", "")
                tree.insert("", "end", values=(ts, dur, content))

        refresh()
        vsb = ttk.Scrollbar(f3, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="left", fill="y")

        btns = ttk.Frame(f3)
        btns.pack(fill="x", pady=6)

        def copy_sel():
            import pyperclip
            sel = tree.selection()
            if not sel:
                return
            vals = tree.item(sel[0], "values")
            if vals and len(vals) > 2 and vals[2]:
                pyperclip.copy(vals[2].replace("🎙 ", ""))

        def clear_hist():
            self.history.clear()
            self._save_history()
            refresh()

        def open_rec_dir():
            d = self._record_dir()
            d.mkdir(parents=True, exist_ok=True)
            os.startfile(str(d))

        ttk.Button(btns, text="复制选中", command=copy_sel).pack(side="left", padx=4)
        ttk.Button(btns, text="打开录音目录", command=open_rec_dir).pack(side="left", padx=4)
        ttk.Button(btns, text="刷新", command=refresh).pack(side="left", padx=4)
        ttk.Button(btns, text="清空历史", command=clear_hist).pack(side="left", padx=4)

        # ---- 底部按钮 ----
        def on_save():
            self.cfg["hotkey"] = v_hotkey.get().strip() or "f2"
            self.cfg["mode"] = v_mode.get()
            self.cfg["record_enabled"] = bool(v_record_enabled.get())
            self.cfg["record_hotkey"] = v_record_hotkey.get().strip() or "f3"
            self.cfg["record_mode"] = v_record_mode.get()
            self.cfg["record_dir"] = v_record_dir.get().strip()
            self.cfg["model"] = v_model.get()
            self.cfg["language"] = v_language.get()
            self.cfg["device"] = v_device.get()
            try:
                self.cfg["idle_unload_seconds"] = max(0, int(float(v_idle_unload.get().strip() or 0)))
            except Exception:
                self.cfg["idle_unload_seconds"] = 120
            self._save_user_config()
            self._reload_history_size()
            self._register_hotkeys()
            log("[info] 设置已保存")

        bar = ttk.Frame(win)
        bar.pack(fill="x", padx=10, pady=10)
        ttk.Button(bar, text="关闭", command=win.destroy).pack(side="right")
        ttk.Button(bar, text="保存并应用", command=on_save).pack(side="right", padx=6)

    # -- 生命周期 ----------------------------------------------------------
    def _shutdown(self):
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        try:
            if self.icon is not None:
                self.icon.stop()
        except Exception:
            pass

    def run(self):
        self._register_hotkeys()
        self._setup_tray()

        # tkinter 主线程 root（隐藏），用于设置窗口；失败则退化为纯事件循环
        try:
            import tkinter as tk
            self._tk_root = tk.Tk()
            self._tk_root.withdraw()
            self._tk_root.title("语音输入")
        except Exception as e:
            log("[warn] tkinter 不可用，设置页面被禁用: %s" % e)
            self._tk_root = None

        log("语音输入已启动：热键=%s 模式=%s 模型=%s" % (self.cfg.get("hotkey"), self.cfg.get("mode"), self.cfg.get("model")))
        while not self._quit_flag:
            if self._tk_root is not None:
                try:
                    self._tk_root.update()
                except Exception:
                    pass
            if self._open_settings:
                self._open_settings = False
                try:
                    self._show_settings()
                except Exception as e:
                    log("[error] 打开设置失败: %s" % e)
            now = time.time()
            if now - self._last_unload_check >= 5.0:
                self._last_unload_check = now
                try:
                    self._unload_model_if_idle()
                except Exception as e:
                    log("[warn] 空闲卸载检查失败: %s" % e)
            time.sleep(0.05)
        self._shutdown()
        log("语音输入已退出")


def main():
    _setup_stdout()
    if not _ensure_single_instance():
        log("已有语音输入实例在运行，本次启动自动退出")
        return
    cfg = load_config()
    app = VoiceInputApp(cfg)
    app.run()


if __name__ == "__main__":
    main()

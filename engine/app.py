# -*- coding: utf-8 -*-
"""
系统级语音输入工具 v2 (Windows) —— DSH 插件 `dsh-voice-input` 的内置引擎。

特性：
- 全局热键录音（F9 开/关，或按住说话）
- 录音中每 ~1.6s 用 faster-whisper「分块滚动」实时显示（一段一段，不逐字蹦）
- 悬浮框跟随光标所在屏幕（多屏正确）
- 识别结果自动粘贴，并始终留在剪贴板 + 保留历史（托盘可取回）
- 托盘常驻（无控制台窗口时用 pythonw 启动），右键可退出/看占用
- CPU / GPU 占用监测（空闲基线 + 识别峰值）
- 模型懒加载：不用时不占资源；首次使用自动下载（hf-mirror）
"""

import ctypes
import json
import os
import queue
import subprocess
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
CONFIG_PATH = APP_DIR / "config.json"
LOG_PATH = APP_DIR / "voice_input.log"

# 持有 os.add_dll_directory 句柄，防止被 GC 后 CUDA DLL 搜索路径失效
_CUDA_DLL_HANDLES = []

DEFAULTS = {
    "hotkey": "f9",
    "mode": "toggle",            # toggle / hold
    "model": "small",            # tiny / base / small / medium / large-v3
    "language": "zh",            # zh / en / null(自动)
    "device": "auto",            # auto / cpu / cuda
    "compute_type": "default",
    "samplerate": 16000,
    "beam_size": 5,
    "vad_filter": True,
    "insert_mode": "paste",      # paste / type / none
    "hf_endpoint": "https://hf-mirror.com",
    "initial_prompt_zh": "以下是普通话的句子。",
    "preview_interval": 1.6,
    "history_size": 20,
    "overlay_duration": 2.6,
}


def log(msg: str) -> None:
    try:
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
    # pythonw 下 stdout/stderr 为 None，print 会崩；重定向到日志文件
    try:
        if sys.stdout is None or sys.stderr is None:
            f = open(LOG_PATH, "a", encoding="utf-8", buffering=1)
            if sys.stdout is None:
                sys.stdout = f
            if sys.stderr is None:
                sys.stderr = f
    except Exception:
        pass


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            log("[warn] 读取 config.json 失败: %s" % e)
    # DSH 插件通过环境变量注入 cordis.patch.yml 里的 config，优先级最高
    try:
        env_cfg = json.loads(os.environ.get("DSH_VOICE_CONFIG", "{}"))
        if isinstance(env_cfg, dict):
            cfg.update(env_cfg)
    except Exception:
        pass
    if cfg.get("hf_endpoint"):
        os.environ["HF_ENDPOINT"] = cfg["hf_endpoint"]
    return cfg


# ---------------------------------------------------------------------------
# 光标位置（ctypes，跟随光标所在屏）
# ---------------------------------------------------------------------------

class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def get_cursor_and_screen():
    """返回 (cx, cy, vleft, vtop, vwidth, vheight)。cx/cy 为光标，其余为虚拟屏边界。"""
    try:
        user32 = ctypes.windll.user32
        pt = _POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
        SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
        left = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        top = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        width = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        height = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        return pt.x, pt.y, left, top, width, height
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 悬浮框（tkinter，仅主线程操作）
# ---------------------------------------------------------------------------

class Overlay:
    def __init__(self):
        self.root = None
        self.label = None
        self._hide_job = None

    def _ensure(self):
        if self.root is not None:
            return
        import tkinter as tk
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.92)
        self.root.configure(bg="#1f2430")
        self.label = tk.Label(
            self.root, text="", bg="#1f2430", fg="#eef0f5",
            font=("Microsoft YaHei UI", 14), padx=20, pady=12,
            wraplength=600, justify="left",
        )
        self.label.pack()
        self.root.withdraw()

    def show(self, text: str, seconds: float):
        self._ensure()
        self.label.config(text=text)
        self.root.update_idletasks()
        w = self.root.winfo_reqwidth()
        h = self.root.winfo_reqheight()
        pos = get_cursor_and_screen()
        if pos:
            cx, cy, vleft, vtop, vw, vh = pos
            x = cx - w // 2
            y = cy - h - 36  # 光标上方
            x = max(vleft, min(x, vleft + vw - w))
            y = max(vtop, min(y, vtop + vh - h))
        else:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            x = (sw - w) // 2
            y = sh - h - 140
        self.root.geometry(f"+{x}+{y}")
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        if self._hide_job is not None:
            self.root.after_cancel(self._hide_job)
            self._hide_job = None
        if seconds and seconds > 0 and seconds < 100000:
            self._hide_job = self.root.after(int(seconds * 1000), self._hide)

    def _hide(self):
        if self.root is not None:
            self.root.withdraw()
        self._hide_job = None


# ---------------------------------------------------------------------------
# 占用监测
# ---------------------------------------------------------------------------

class Stats:
    def __init__(self):
        import psutil
        self.proc = psutil.Process()
        self.proc.cpu_percent(None)  # 预热
        self.idle_cpu = None
        self.idle_mem = None
        self.peak = {"cpu": 0.0, "gpu": None}

    def sample_baseline(self):
        try:
            time.sleep(0.5)
            self.idle_cpu = self.proc.cpu_percent(None)
            self.idle_mem = self.proc.memory_info().rss / 1024 / 1024
        except Exception:
            self.idle_cpu = 0.0
            self.idle_mem = 0.0

    @staticmethod
    def gpu_util():
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            ).stdout.strip()
            return float(out.splitlines()[0].strip())
        except Exception:
            return None

    def poll(self):
        try:
            c = self.proc.cpu_percent(None)
            if c is not None:
                self.peak["cpu"] = max(self.peak["cpu"], c)
        except Exception:
            pass
        g = self.gpu_util()
        if g is not None:
            self.peak["gpu"] = g if self.peak["gpu"] is None else max(self.peak["gpu"], g)

    def report(self) -> str:
        lines = ["📊 占用统计"]
        lines.append("空闲：CPU %.0f%%  内存 %.0f MB" % (self.idle_cpu or 0, self.idle_mem or 0))
        gpu = "%.0f%%" % self.peak["gpu"] if self.peak["gpu"] is not None else "无/未用"
        lines.append("识别峰值：CPU %.0f%%  GPU %s" % (self.peak["cpu"], gpu))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 主程序
# ---------------------------------------------------------------------------

class VoiceInputApp:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.state = "idle"          # idle / recording / transcribing
        self.frames = None
        self.stream = None
        self.model = None
        self.model_lock = threading.Lock()
        self.used_device = None
        self.ui_queue = queue.Queue()
        self.overlay = Overlay()
        self.stats = Stats()
        self.history = deque(maxlen=int(cfg.get("history_size", 20)))
        self.sr = int(cfg.get("samplerate", 16000))
        self._preview_stop = threading.Event()
        self._preview_thread = None
        self._quit_flag = False
        self.icon = None

    # -- UI ----------------------------------------------------------------
    def _ui(self, action, payload=None):
        self.ui_queue.put((action, payload or {}))

    def _poll_ui(self):
        try:
            while True:
                action, payload = self.ui_queue.get_nowait()
                if action == "show":
                    self.overlay.show(payload.get("text", ""), payload.get("seconds", 2))
                elif action == "hide":
                    self.overlay._hide()
        except queue.Empty:
            pass
        if self._quit_flag and self.overlay.root is not None:
            self._shutdown()
            return
        if self.overlay.root is not None:
            self.overlay.root.after(80, self._poll_ui)

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

        def show_stats(icon, item):
            self._ui("show", {"text": self.stats.report(), "seconds": 6})

        def quit_app(icon, item):
            self._quit_flag = True

        self.icon = pystray.Icon("voice_input", img, "语音输入", Menu(
            MenuItem("📋 复制最近识别", copy_last),
            MenuItem("📊 占用统计", show_stats),
            MenuItem("退出", quit_app),
        ))
        self.icon.run_detached()

    def _copy_history(self, idx):
        try:
            import pyperclip
            if self.history:
                text = self.history[idx] if -len(self.history) <= idx < len(self.history) else None
                if text:
                    pyperclip.copy(text)
        except Exception as e:
            log("[warn] 复制历史失败: %s" % e)

    # -- 录音 --------------------------------------------------------------
    def _audio_callback(self, indata, frames, time_info, status):
        if self.frames is not None:
            self.frames.append(indata.copy())

    def _snapshot(self):
        if not self.frames:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate(list(self.frames))

    def _start_recording(self):
        if self.state != "idle":
            return
        self.state = "recording"
        self.frames = []
        hotkey = self.cfg["hotkey"]
        hint = ("🎤 正在聆听…（松开 %s 结束）" % hotkey) if self.cfg["mode"] == "hold" else ("🎤 正在聆听…（再按 %s 结束）" % hotkey)
        self._ui("show", {"text": hint, "seconds": 0})
        try:
            self.stream = sd.InputStream(
                samplerate=self.sr, channels=1, dtype="int16", callback=self._audio_callback,
            )
            self.stream.start()
        except Exception as e:
            self.state = "idle"
            self.frames = None
            self._ui("show", {"text": "⚠️ 无法打开麦克风：%s" % e, "seconds": 5})
            return
        self._preview_stop.clear()
        self._preview_thread = threading.Thread(target=self._preview_loop, daemon=True)
        self._preview_thread.start()

    def _stop_recording(self):
        if self.state != "recording":
            return
        self.state = "transcribing"
        try:
            if self.stream is not None:
                self.stream.stop()
                self.stream.close()
        except Exception:
            pass
        self.stream = None
        self._preview_stop.set()
        if self._preview_thread is not None:
            self._preview_thread.join(timeout=3.0)
        data = self._snapshot()
        self.frames = None
        self._ui("show", {"text": "⏳ 识别中…", "seconds": 0})
        threading.Thread(target=self._transcribe_and_paste, args=(data,), daemon=True).start()

    # -- 分块滚动预览 ------------------------------------------------------
    def _preview_loop(self):
        interval = float(self.cfg.get("preview_interval", 1.6))
        while not self._preview_stop.is_set():
            self._preview_stop.wait(interval)
            if self._preview_stop.is_set():
                break
            data = self._snapshot()
            if data.size < self.sr * 0.5:
                continue
            if not self.model_lock.acquire(blocking=False):
                continue
            try:
                text = self._transcribe_data(data, beam_size=1)
            except Exception:
                text = None
            finally:
                self.model_lock.release()
            if text and not self._preview_stop.is_set():
                self._ui("show", {"text": text + " ▍", "seconds": 0})

    # -- 识别与粘贴 --------------------------------------------------------
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

    def _save_wav(self, data: np.ndarray) -> str:
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="voice_input_")
        os.close(fd)
        with wave.open(path, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(self.sr)
            w.writeframes(data.tobytes())
        return path

    def _transcribe_data(self, data: np.ndarray, beam_size=None) -> str:
        model = self._get_model()
        wav = self._save_wav(data)
        try:
            lang = self.cfg.get("language") or None
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

    def _transcribe_and_paste(self, data: np.ndarray):
        sampler_stop = threading.Event()

        def _sample():
            while not sampler_stop.is_set():
                self.stats.poll()
                sampler_stop.wait(0.4)

        sampler = threading.Thread(target=_sample, daemon=True)
        sampler.start()
        try:
            if self.model is None:
                self._ui("show", {"text": "⏳ 首次加载模型，请稍候…", "seconds": 0})
            text = self._transcribe_data(data)
            text = (text or "").strip()
            if not text:
                self._ui("show", {"text": "🤔 没有识别到内容（试试离麦克风近一点）", "seconds": 2.5})
                return
            self._insert_text(text)
            shown = text if len(text) <= 44 else text[:44] + "…"
            self._ui("show", {"text": "✅ 已输入：%s" % shown, "seconds": self.cfg.get("overlay_duration", 2.6)})
        except Exception as e:
            log("[error] 识别失败: %s" % e)
            self._ui("show", {"text": "❌ 识别出错：%s" % e, "seconds": 5})
        finally:
            sampler_stop.set()
            self.state = "idle"

    def _insert_text(self, text: str):
        # 始终保留在剪贴板（永不丢失）+ 进历史
        import pyperclip
        pyperclip.copy(text)
        self.history.append(text)

        mode = self.cfg.get("insert_mode", "paste")
        if mode == "paste":
            import keyboard
            time.sleep(0.05)
            keyboard.send("ctrl+v")
        elif mode == "type":
            import keyboard
            keyboard.write(text)
        # mode == "none"：只进剪贴板，不自动粘贴

    # -- 热键 --------------------------------------------------------------
    def _on_hotkey(self):
        if self.state == "idle":
            self._start_recording()
        elif self.state == "recording":
            self._stop_recording()

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
        if self.overlay.root is not None:
            self.overlay.root.quit()

    def run(self):
        import keyboard
        mode = self.cfg.get("mode", "toggle")
        hotkey = self.cfg["hotkey"]
        log("语音输入已启动：热键=%s 模式=%s 模型=%s" % (hotkey, mode, self.cfg["model"]))

        try:
            if mode == "hold":
                keyboard.add_hotkey(hotkey, self._start_recording, suppress=False, trigger_on_release=False)
                keyboard.add_hotkey(hotkey, self._stop_recording, suppress=False, trigger_on_release=True)
            else:
                keyboard.add_hotkey(hotkey, self._on_hotkey)
        except Exception as e:
            log("[error] 热键注册失败: %s" % e)
            self._ui("show", {"text": "⚠️ 热键注册失败：%s" % e, "seconds": 5})

        threading.Thread(target=self.stats.sample_baseline, daemon=True).start()
        self._setup_tray()
        self.overlay._ensure()
        self.overlay.root.after(80, self._poll_ui)
        self.overlay.root.mainloop()
        log("语音输入已退出")


def main():
    _setup_stdout()
    cfg = load_config()
    app = VoiceInputApp(cfg)
    app.run()


if __name__ == "__main__":
    main()

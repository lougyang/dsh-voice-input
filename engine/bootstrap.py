# -*- coding: utf-8 -*-
"""bootstrap.py —— 语音引擎启动器（由 dsh-voice-input 插件的 Host 代码拉起）。

职责：
1. 确保稳定的 Python 虚拟环境存在（%LOCALAPPDATA%\\dsh-voice-input\\venv）。
   放在 LOCALAPPDATA 而不是插件目录里，是因为 `dsh plugin update` 会重新克隆并
   清空插件目录——venv 放插件里每次更新都会被删掉、被迫重装。
2. 首次创建 venv 并安装依赖（清华 PyPI 镜像，国内加速）。
3. 用 venv 的 pythonw.exe 无窗口拉起 app.py 并等待其退出。
   本进程是 app.py 的父进程；宿主要停止时用 taskkill /F /T 整树终止，两个一起退出。

模型（Whisper small，约 500MB）由 app.py 首次使用时懒加载自动下载（hf-mirror 镜像），
不在这里预下载，以免把首启时间拉得太长。
"""

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
APP = ENGINE / "app.py"
REQ = ENGINE / "requirements.txt"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
DATA_DIR = LOCALAPPDATA / "dsh-voice-input"
VENV = DATA_DIR / "venv"
PY = VENV / "Scripts" / "python.exe"
PYW = VENV / "Scripts" / "pythonw.exe"
LOG = DATA_DIR / "bootstrap.log"

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"


def log(msg: str) -> None:
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


def requirements_hash() -> str:
    return hashlib.sha1(REQ.read_bytes()).hexdigest()[:12]


def ensure_venv() -> None:
    marker = DATA_DIR / ".bootstrap-ok"
    if PY.exists() and marker.exists() and marker.read_text(encoding="utf-8").strip() == requirements_hash():
        return  # 环境已就绪且 requirements 未变
    log("creating venv at %s" % VENV)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    log("upgrading pip ...")
    subprocess.check_call([str(PY), "-m", "pip", "install", "--upgrade", "pip", "-i", MIRROR])
    log("installing dependencies (this may take a few minutes) ...")
    subprocess.check_call([str(PY), "-m", "pip", "install", "-r", str(REQ), "-i", MIRROR])
    marker.write_text(requirements_hash(), encoding="utf-8")
    log("dependencies installed")


def run_engine() -> int:
    env = dict(os.environ)
    proc = subprocess.Popen([str(PYW), str(APP)], cwd=str(ENGINE), env=env)
    return proc.wait()


def main() -> int:
    try:
        ensure_venv()
    except Exception as e:
        log("bootstrap failed: %s" % e)
        return 1
    log("starting app.py via %s" % PYW)
    return run_engine()


if __name__ == "__main__":
    sys.exit(main())

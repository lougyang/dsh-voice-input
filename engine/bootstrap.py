# -*- coding: utf-8 -*-
"""bootstrap.py —— 首次运行初始化：创建 venv、安装依赖、预下载模型。

由 DSH 插件的 Host 代码在 apply() 时调用（仅当 engine/.venv 不存在时）。
运行完成后 engine/.venv/Scripts/pythonw.exe 即存在，插件随后用它启动 app.py。
"""

import os
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent
VENV = ENGINE / ".venv"
PY = VENV / "Scripts" / "python.exe"
MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
HF_MIRROR = "https://hf-mirror.com"


def main() -> None:
    if not PY.exists():
        print("[bootstrap] creating venv ...", flush=True)
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])

    print("[bootstrap] installing dependencies ...", flush=True)
    subprocess.check_call([str(PY), "-m", "pip", "install", "--upgrade", "pip", "-i", MIRROR])
    subprocess.check_call(
        [str(PY), "-m", "pip", "install", "-r", str(ENGINE / "requirements.txt"), "-i", MIRROR]
    )

    print("[bootstrap] pre-downloading model (small) ...", flush=True)
    env = dict(os.environ)
    env["HF_ENDPOINT"] = HF_MIRROR
    preload = (
        "from faster_whisper import WhisperModel\n"
        "try:\n"
        "    WhisperModel('small', device='cuda', compute_type='float16')\n"
        "except Exception:\n"
        "    WhisperModel('small', device='cpu', compute_type='int8')\n"
    )
    subprocess.check_call([str(PY), "-c", preload], cwd=str(ENGINE), env=env)

    print("[bootstrap] done", flush=True)


if __name__ == "__main__":
    main()

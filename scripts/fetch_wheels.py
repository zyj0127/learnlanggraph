# -*- coding: utf-8 -*-
"""预下载 Linux(manylinux) wheel 到 build_wheels/，让 docker build 转为完全离线。

⚠️ 必须在 Linux 或 WSL 里运行，**不能在 Windows 宿主机的 Python 里跑**。
   原因是 pip 的 `--platform` 只影响 wheel 的选择，**不影响 environment marker 的求值**：
   像 mcp 声明的 `pywin32>=310; sys_platform == "win32"` 仍会按宿主平台求值为真，
   pip 于是去找 pywin32，又被 manylinux 平台过滤掉，最后直接报
       ERROR: No matching distribution found for pywin32>=310; sys_platform == "win32" ...
   在 WSL 里 `sys_platform` 是 linux，该分支自然不求值，问题不存在。
   （Windows 上请直接 `docker build`，Dockerfile 会自动走在线安装分支。）

为什么单独一个脚本，而不是直接在 Dockerfile 里 pip install：
  * 容器内在线装依赖时，弱网中断就要从零重来（Docker 只在 RUN 成功后才固化缓存层）；
    在 Linux 侧预下载可以断点续传、单独重试，wheel 还能被后续多次构建复用。
  * torch 必须取 PyTorch 的 CPU 专用索引 —— Linux 平台 PyPI 的 torch wheel 会连带
    拉入 nvidia-cu12-* / triton 等数 GB 的 CUDA 运行时依赖，纯 CPU 推理场景下既
    拖垮构建时间又让镜像虚胖。PEP 440 中 2.13.0+cpu > 2.13.0，故同时给出两个索引时
    pip 会优先选中 CPU 版。

用法（在 WSL / Linux 下）：
    python scripts/fetch_wheels.py            # 增量下载
    python scripts/fetch_wheels.py --refresh  # 清空后重新下载

产物：build_wheels/*.whl（不入库，见 .gitignore；Dockerfile 会自动检测并离线安装）
"""
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WHEEL_DIR = PROJECT_ROOT / "build_wheels"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
CONSTRAINTS = WHEEL_DIR / "constraints.txt"

# 目标平台：容器基础镜像 python:3.13-slim (Debian, glibc) → CPython 3.13 / manylinux。
# 多写几个 manylinux 标签是为了兼容能力不同步的包（新旧 tagging 混用很常见）。
PLATFORMS = [
    "manylinux_2_28_x86_64",
    "manylinux2014_x86_64",
    "manylinux_2_17_x86_64",
    "manylinux1_x86_64",
]

PYPI_MIRROR = "https://mirrors.aliyun.com/pypi/simple/"
PYTORCH_CPU_INDEX = "https://mirrors.aliyun.com/pytorch-wheels/cpu/"


def main() -> int:
    if sys.platform.startswith("win"):
        print("[fetch_wheels] 需在 Linux / WSL 下运行：Windows 宿主上 pip 会按 win32 求值 "
              "environment marker（如 mcp 的 pywin32 依赖），与 manylinux 平台过滤冲突而报错。",
              file=sys.stderr)
        print("[fetch_wheels] Windows 请直接执行 `docker build -t hr-agent .` —— "
              "Dockerfile 的 BuildKit cache mount 已保证重复构建不必重下大包。", file=sys.stderr)
        return 2

    if "--refresh" in sys.argv:
        for whl in WHEEL_DIR.glob("*.whl"):
            whl.unlink()
        print("[fetch_wheels] 已清空旧 wheel")

    WHEEL_DIR.mkdir(parents=True, exist_ok=True)

    cmd = [
        sys.executable, "-m", "pip", "download",
        "-r", str(REQUIREMENTS),
        "-d", str(WHEEL_DIR),
        # 平台与解释器目标：与容器一致，避免下成 Windows wheel
        "--only-binary=:all:",
        "--python-version", "3.13",
        "--implementation", "cp",
        # 刻意不传 --abi：chromadb / numpy 只发布 cp39-abi3 与 manylinux_2_28 标签，
        # 硬钉 cp313 会把它们全部过滤掉，导致 ResolutionImpossible
        "-i", PYPI_MIRROR,
        "--find-links", PYTORCH_CPU_INDEX,
        "--timeout", "120",
        "--retries", "5",
    ]
    if CONSTRAINTS.exists():
        cmd += ["-c", str(CONSTRAINTS)]
    for p in PLATFORMS:
        cmd += ["--platform", p]

    print("[fetch_wheels] 开始下载 Linux wheel →", WHEEL_DIR)
    print("[fetch_wheels] 参考耗时：torch(CPU) 约 180MB，全量约 700MB~1GB")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(f"[fetch_wheels] 失败，pip 退出码 {proc.returncode}", file=sys.stderr)
        return proc.returncode

    wheels = sorted(WHEEL_DIR.glob("*.whl"))
    total = sum(w.stat().st_size for w in wheels)
    print(f"[fetch_wheels] 完成：{len(wheels)} 个 wheel，共 {total / 1024 / 1024:.1f} MB")
    print("[fetch_wheels] 现在可以执行：docker build -t hr-agent .")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

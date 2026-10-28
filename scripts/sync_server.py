#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提交后把运行时文件同步到 ECS（/opt/dsa/code）。

用户裁决（2026-10-28）：服务器侧依据本地 git 同步，每次提交后自动同步。

机制：
- 基准 = 上次成功同步的 commit（``.git/dsa_sync_state.json``）
- 清单 = ``git diff --name-status <base>..HEAD``，仅保留运行时文件
  （``main.py`` / ``src/**/*.py`` / ``data_provider/**/*.py``）；删除同步为远端 rm
- 传输 = ``workbench upload``（OSS 中转，``--force`` 覆盖），逐文件
- 校验 = 远端 ``md5sum`` 与本地 md5 逐文件比对，任一不符即中止且不推进基准
- 收尾 = ``chown dsa:dsa`` + 清受影响 ``__pycache__`` + 远端 ``py_compile``

用法：
    python scripts/sync_server.py --auto    # post-commit hook 调用（静默成功）
    python scripts/sync_server.py --check   # 只打印待同步清单
    python scripts/sync_server.py --init    # 仅在确认服务器已与 HEAD 一致时重置基准

失败语义：非零退出、不推进基准，下次提交自动重试（幂等，重复 upload 无害）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

INSTANCE = os.getenv("DSA_ECS_INSTANCE_ID", "i-uf6bi5wzzb9lqh27mkgf")
REMOTE_ROOT = os.getenv("DSA_ECS_CODE_DIR", "/opt/dsa/code")
REMOTE_PY = os.getenv("DSA_ECS_PYTHON", "/opt/dsa/venv/bin/python")
RUNTIME_DIRS = ("src/", "data_provider/")
RUNTIME_FILES = {"main.py"}


def _run(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def git(*args: str) -> str:
    p = _run(["git", *args], timeout=60)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or f"git {' '.join(args)} failed")
    return p.stdout


def state_path() -> Path:
    return Path(git("rev-parse", "--git-dir").strip()).resolve() / "dsa_sync_state.json"


def load_base() -> str | None:
    try:
        return json.loads(state_path().read_text(encoding="utf-8")).get("commit")
    except (OSError, ValueError):
        return None


def save_base(commit: str) -> None:
    state_path().write_text(
        json.dumps({"commit": commit, "instance": INSTANCE}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def is_runtime(path: str) -> bool:
    return path in RUNTIME_FILES or (path.endswith(".py") and path.startswith(RUNTIME_DIRS))


def changed(base: str, head: str) -> tuple[list[str], list[str]]:
    """返回 (需上传/覆盖的路径, 需远端删除的路径)。"""
    ups: list[str] = []
    dels: list[str] = []
    for line in git("diff", "--name-status", "-M", f"{base}..{head}").splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        status, paths = parts[0], parts[1:]
        if status.startswith("R") and len(paths) >= 2:
            old, new = paths[0], paths[1]
            if is_runtime(old):
                dels.append(old)
            if is_runtime(new):
                ups.append(new)
            continue
        path = paths[0]
        if not is_runtime(path):
            continue
        (dels if status.startswith("D") else ups).append(path)
    return sorted(set(ups)), sorted(set(dels))


def local_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def upload(path: str) -> None:
    p = _run([
        "workbench", "upload", path, f"{REMOTE_ROOT}/{path}",
        "-i", INSTANCE, "--force", "--output", "json",
    ])
    if p.returncode != 0:
        raise RuntimeError(f"upload 失败 {path}: {(p.stderr or p.stdout).strip()[:300]}")


def remote(command: str) -> str:
    p = _run(["workbench", "exec", "-i", INSTANCE, "--command", command, "--output", "json"])
    if p.returncode != 0:
        raise RuntimeError(f"远程命令失败: {(p.stderr or p.stdout).strip()[:300]}")
    data = json.loads(p.stdout)
    if data.get("exit_code", 0) != 0:
        raise RuntimeError(f"远程命令退出码 {data.get('exit_code')}: {(data.get('stderr') or data.get('output') or '').strip()[:300]}")
    return data.get("output", "")


def verify_remote(files: list[str]) -> None:
    quoted = " ".join(shlex.quote(f) for f in files)
    out = remote(f"cd {shlex.quote(REMOTE_ROOT)} && md5sum {quoted}")
    got = {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            got[parts[-1].lstrip("*")] = parts[0]
    for f in files:
        want = local_md5(f)
        if got.get(f) != want:
            raise RuntimeError(f"md5 不一致 {f}: remote={got.get(f)} local={want}")


def finalize(files: list[str]) -> None:
    quoted = " ".join(shlex.quote(f) for f in files)
    dirs = sorted({str(Path(f).parent) for f in files})
    rm = "; ".join(f"rm -rf {shlex.quote(d)}/__pycache__" for d in dirs)
    remote(
        f"cd {shlex.quote(REMOTE_ROOT)} && chown dsa:dsa {quoted} && {rm} ; "
        f"sudo -u dsa {shlex.quote(REMOTE_PY)} -m py_compile {quoted}"
    )


def remove_remote(files: list[str]) -> None:
    quoted = " ".join(shlex.quote(f) for f in files)
    remote(f"cd {shlex.quote(REMOTE_ROOT)} && rm -f {quoted}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只打印待同步清单，不传输")
    ap.add_argument("--init", action="store_true", help="重置基准为当前 HEAD（不做传输）")
    ap.add_argument("--auto", action="store_true", help="hook 模式：无变更时静默")
    args = ap.parse_args()

    head = git("rev-parse", "HEAD").strip()
    if args.init:
        save_base(head)
        print(f"[sync] 基准已重置 = {head[:8]}")
        return 0

    base = load_base()
    if not base:
        print("[sync][skip] 无同步基准，先在确认服务器一致的机器上执行 --init", file=sys.stderr)
        return 0
    if base == head:
        return 0

    ups, dels = changed(base, head)
    if not ups and not dels:
        save_base(head)
        return 0

    print(f"[sync] {base[:8]}..{head[:8]}: 上传 {len(ups)} / 删除 {len(dels)}")
    for f in ups:
        print(f"  ↑ {f}")
    for f in dels:
        print(f"  ✕ {f}")
    if args.check:
        return 0

    for f in ups:
        upload(f)
    if ups:
        verify_remote(ups)
        finalize(ups)
    if dels:
        remove_remote(dels)
    save_base(head)
    print(f"[sync] OK，基准推进到 {head[:8]}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 - hook 路径必须只告警不阻断
        print(f"[sync][error] {e}", file=sys.stderr)
        sys.exit(1)

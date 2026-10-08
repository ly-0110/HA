from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, JSONResponse

# Only experimental documents/data may be served or passed to a desktop application.
ARTIFACT_SUFFIXES = {
    ".json", ".jsonl", ".yaml", ".yml", ".log", ".txt", ".csv", ".tsv", ".md",
    ".png", ".jpg", ".jpeg", ".xml", ".pdf", ".pcap", ".pcapng",
}
TEXT_SUFFIXES = {".jsonl", ".yaml", ".yml", ".log", ".txt", ".csv", ".tsv", ".md", ".xml"}
MAX_PREVIEW_BYTES = 2 * 1024 * 1024


def text_preview(path: Path) -> JSONResponse:
    if path.suffix.lower() not in TEXT_SUFFIXES | {".json"}:
        raise HTTPException(415, "该文件不支持文本预览")
    try:
        with path.open("rb") as handle:
            content = handle.read(MAX_PREVIEW_BYTES + 1)
        size = path.stat().st_size
    except OSError as exc:
        raise HTTPException(404, "产物暂时无法读取") from exc
    return JSONResponse(
        {"text": content[:MAX_PREVIEW_BYTES].decode("utf-8-sig", errors="replace"),
         "truncated": len(content) > MAX_PREVIEW_BYTES, "size_bytes": size},
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def resolve_artifact(directory: Path, relative: str) -> Path:
    """Resolve a listed file, rejecting traversal, drive paths and escaping symlinks."""
    if not relative or "\\" in relative or ":" in relative:
        raise HTTPException(400, "产物路径无效")
    parts = relative.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise HTTPException(400, "产物路径无效")
    base = directory.resolve()
    path = (base / relative).resolve()
    if not path.is_relative_to(base):
        raise HTTPException(400, "产物路径超出会话目录")
    if path.suffix.lower() not in ARTIFACT_SUFFIXES:
        raise HTTPException(404, "该文件不可查看")
    if not path.is_file():
        raise HTTPException(404, "产物不存在")
    return path


def artifact_response(path: Path, *, download: bool = False) -> FileResponse:
    suffix = path.suffix.lower()
    media_type = "text/plain; charset=utf-8" if suffix in TEXT_SUFFIXES else None
    if suffix == ".json":
        media_type = "application/json"
    if suffix in {".pcap", ".pcapng"}:
        media_type = "application/octet-stream"
    return FileResponse(
        path,
        filename=path.name,
        media_type=media_type,
        content_disposition_type="attachment" if download else "inline",
        headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"},
    )


def open_local_path(path: Path) -> None:
    """Open the original file/folder on the local service host, without copying it."""
    try:
        if platform.system() == "Windows":
            os.startfile(str(path))
            return
        executable = "open" if platform.system() == "Darwin" else "xdg-open"
        if not shutil.which(executable):
            raise HTTPException(409, "当前主机没有桌面打开工具，请使用预览或下载")
        result = subprocess.run(
            [executable, str(path)],
            capture_output=True, text=True, timeout=10, check=False,
        )
        if result.returncode:
            raise HTTPException(409, "无法打开本地文件，请检查默认应用或使用预览")
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(409, "无法打开本地文件，请检查默认应用或使用预览") from exc

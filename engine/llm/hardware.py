"""
하드웨어 감지 — CPU / RAM / GPU / VRAM
=========================================
로컬 LLM 권장 모델 결정용.

DeepSeek-R1 Distill 7B Q4_K_M 기준 메모리 요구:
  - GPU 가속(전체 layer)   : VRAM ≥ 6 GB
  - 하이브리드(일부 GPU)    : VRAM 4-6 GB + RAM ≥ 16 GB
  - CPU 전용               : RAM ≥ 16 GB (느림 ~5-10 tok/s)
  - 부족                   : 1.5B 모델로 다운그레이드 권장

Windows에서는 nvidia-smi(NVIDIA), wmic(범용 GPU)를 시도하고,
실패 시에도 CPU 정보는 항상 반환한다.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional


def _bytes_to_gb(b: int) -> float:
    return round(b / (1024 ** 3), 1)


def _detect_cpu_ram() -> Dict[str, Any]:
    """psutil 기반 CPU/RAM."""
    import psutil
    return {
        "cpu_cores_physical": psutil.cpu_count(logical=False) or 0,
        "cpu_cores_logical":  psutil.cpu_count(logical=True) or 0,
        "ram_total_gb":       _bytes_to_gb(psutil.virtual_memory().total),
        "ram_available_gb":   _bytes_to_gb(psutil.virtual_memory().available),
    }


def _detect_cpu_name() -> str:
    """CPU 모델명 (Windows)."""
    try:
        out = subprocess.run(
            ["wmic", "cpu", "get", "Name", "/value"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        for ln in out.stdout.splitlines():
            if ln.startswith("Name="):
                return ln.split("=", 1)[1].strip()
    except Exception:
        pass
    try:
        import platform
        return platform.processor() or "Unknown CPU"
    except Exception:
        return "Unknown CPU"


def _detect_nvidia() -> Optional[List[Dict[str, Any]]]:
    """nvidia-smi → GPU 리스트. NVIDIA 없으면 None."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,memory.total,memory.free,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        gpus = []
        for ln in out.stdout.strip().splitlines():
            parts = [p.strip() for p in ln.split(",")]
            if len(parts) >= 3:
                gpus.append({
                    "vendor": "NVIDIA",
                    "name": parts[0],
                    "vram_total_mb": int(float(parts[1])),
                    "vram_free_mb":  int(float(parts[2])),
                    "driver":        parts[3] if len(parts) > 3 else "",
                })
        return gpus or None
    except Exception:
        return None


def _detect_wmic_gpu() -> Optional[List[Dict[str, Any]]]:
    """Windows WMIC fallback — AMD/Intel/NVIDIA 모두 감지."""
    try:
        out = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get",
             "Name,AdapterRAM", "/format:csv"],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        gpus = []
        for ln in out.stdout.splitlines():
            ln = ln.strip()
            if not ln or "Node" in ln or "Name" in ln:
                continue
            parts = ln.split(",")
            # CSV: Node,AdapterRAM,Name
            if len(parts) >= 3:
                try:
                    ram_bytes = int(parts[1].strip() or 0)
                except ValueError:
                    ram_bytes = 0
                name = parts[2].strip()
                if not name or name.lower().startswith("microsoft"):
                    continue
                vendor = ("NVIDIA" if "nvidia" in name.lower()
                          else "AMD" if "amd" in name.lower()
                                       or "radeon" in name.lower()
                          else "Intel" if "intel" in name.lower()
                          else "Unknown")
                gpus.append({
                    "vendor": vendor,
                    "name": name,
                    "vram_total_mb": ram_bytes // (1024 * 1024),
                    "vram_free_mb": None,
                    "driver": "",
                })
        return gpus or None
    except Exception:
        return None


def detect_hardware() -> Dict[str, Any]:
    """전체 하드웨어 정보 반환."""
    info: Dict[str, Any] = {
        "cpu_name": _detect_cpu_name(),
        **_detect_cpu_ram(),
        "gpus": [],
        "primary_gpu": None,
        "primary_vram_mb": 0,
    }
    gpus = _detect_nvidia() or _detect_wmic_gpu() or []
    info["gpus"] = gpus
    if gpus:
        # 가장 VRAM 큰 GPU를 primary로
        primary = max(gpus, key=lambda g: g.get("vram_total_mb") or 0)
        info["primary_gpu"] = primary["name"]
        info["primary_vram_mb"] = primary.get("vram_total_mb") or 0
    return info


# ── 권장 모델 결정 ────────────────────────────────────────────────
# (모델 ID, 표시명, 디스크 GB, 최소 RAM GB, 최소 VRAM MB, GPU 권장 여부)
_MODELS = [
    {
        "id": "deepseek-r1:7b",
        "label": "DeepSeek-R1 Distill 7B (Q4_K_M)",
        "disk_gb": 4.7,
        "min_ram_gb": 16,
        "ideal_vram_mb": 6000,
        "min_vram_mb": 4000,
        "reasoning_grade": "A",
        "speed_grade_cpu": "C",
        "speed_grade_gpu": "A",
    },
    {
        "id": "deepseek-r1:1.5b",
        "label": "DeepSeek-R1 Distill 1.5B (Q4_K_M, 경량)",
        "disk_gb": 1.1,
        "min_ram_gb": 8,
        "ideal_vram_mb": 2000,
        "min_vram_mb": 0,
        "reasoning_grade": "B",
        "speed_grade_cpu": "B",
        "speed_grade_gpu": "A",
    },
]

DEFAULT_MODEL_ID = "deepseek-r1:7b"


def recommend_model(hw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    감지 결과 기반 권장 모델.

    Returns
    -------
    {
        "primary": {model dict},          # 권장 1순위
        "alternative": {...} or None,     # 대안
        "rationale": "한글 설명",
        "mode": "gpu" | "hybrid" | "cpu",
        "warnings": ["VRAM 부족 등"],
    }
    """
    hw = hw or detect_hardware()
    ram = hw["ram_total_gb"]
    vram = hw["primary_vram_mb"]
    warnings: List[str] = []

    r1_7b = _MODELS[0]
    r1_15b = _MODELS[1]

    # 7B 풀 GPU
    if vram >= r1_7b["ideal_vram_mb"]:
        return {
            "primary": r1_7b,
            "alternative": r1_15b,
            "rationale": f"VRAM {vram}MB로 7B 모델 전체 layer를 GPU에서 "
                         "가속 가능. 추론 속도 우수.",
            "mode": "gpu",
            "warnings": warnings,
        }
    # 7B 하이브리드
    if vram >= r1_7b["min_vram_mb"] and ram >= r1_7b["min_ram_gb"]:
        warnings.append(
            f"VRAM {vram}MB — 7B 일부 layer를 RAM에서 처리(하이브리드).")
        return {
            "primary": r1_7b,
            "alternative": r1_15b,
            "rationale": "GPU/CPU 하이브리드 모드로 7B 실행 가능.",
            "mode": "hybrid",
            "warnings": warnings,
        }
    # 7B CPU
    if ram >= r1_7b["min_ram_gb"]:
        warnings.append("GPU 가속 부족 — CPU 전용 추론(느림, 5-10 tok/s).")
        return {
            "primary": r1_7b,
            "alternative": r1_15b,
            "rationale": f"RAM {ram}GB로 7B CPU 실행 가능. "
                         "응답 지연이 길 수 있음.",
            "mode": "cpu",
            "warnings": warnings,
        }
    # 1.5B로 다운그레이드
    warnings.append(f"RAM {ram}GB — 7B 모델 실행 불가, 1.5B 권장.")
    return {
        "primary": r1_15b,
        "alternative": None,
        "rationale": "경량 1.5B 모델로 다운그레이드.",
        "mode": "cpu" if vram < 2000 else "gpu",
        "warnings": warnings,
    }

"""Write a build manifest next to a built mobile APK.

The manifest records exactly what was released: the git commit (and dirty
state), the APK SHA-256, its version/build number, the SHA-256 of every
bundled dictionary and the result of the packaged-core check
(``tools/check_apk_core.py``). The APK job in CI uploads it together with
the artifact, so a later "which build is on the phone?" question has an
answer.

Usage::

    python tools/apk_manifest.py [apk] [--out manifest.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tomllib
import zipfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_APK = REPO / "mobile" / "build" / "apk" / "slovnik-mobile.apk"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    try:
        return subprocess.run(
            ["git", *args], cwd=REPO, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        ).stdout.strip()
    except OSError:
        return ""


def _mobile_metadata() -> dict:
    data = tomllib.loads((REPO / "mobile" / "pyproject.toml").read_text("utf-8"))
    flet_cfg = data.get("tool", {}).get("flet", {})
    return {
        "app_version": data.get("project", {}).get("version", ""),
        "build_number": flet_cfg.get("build_number"),
        "build_version": flet_cfg.get("build_version",
                                      data.get("project", {}).get("version", "")),
    }


def _dict_hashes() -> dict[str, str]:
    out: dict[str, str] = {}
    base = REPO / "mobile" / "src" / "assets" / "dicts"
    for dic in sorted(base.glob("*/*.dic")):
        out[dic.relative_to(base).as_posix()] = _sha256(dic)
    return out


def _core_drift() -> list[str]:
    sys.path.insert(0, str(REPO / "tools"))
    try:
        from check_apk_core import check            # noqa: PLC0415
    finally:
        sys.path.pop(0)
    drift, missing = check(DEFAULT_APK, REPO / "mobile" / "src")
    return [f"drift: {d}" for d in drift] + [f"missing: {m}" for m in missing]


def build_manifest(apk_path: Path) -> dict:
    meta = _mobile_metadata()
    apk_sha = _sha256(apk_path)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "apk": {
            "file": apk_path.name,
            "size_bytes": apk_path.stat().st_size,
            "sha256": apk_sha,
        },
        "app": meta,
        "dicts": _dict_hashes(),
        "core_check": _core_drift(),
    }


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("apk", nargs="?", default=str(DEFAULT_APK))
    parser.add_argument("--out", default=None,
                        help="cesta k manifestu (výchozí <apk>.manifest.json)")
    args = parser.parse_args(argv)
    apk_path = Path(args.apk)
    if not apk_path.exists():
        print(f"CHYBA: APK nenalezen: {apk_path}", file=sys.stderr)
        return 2
    manifest = build_manifest(apk_path)
    out = Path(args.out) if args.out else apk_path.with_suffix(
        apk_path.suffix + ".manifest.json")
    out.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print(f"Manifest: {out}")
    if manifest["core_check"]:
        print("POZOR: zabalené jádro neodpovídá zdrojům:")
        for line in manifest["core_check"]:
            print(f"  {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

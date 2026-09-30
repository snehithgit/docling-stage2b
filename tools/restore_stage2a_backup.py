#!/usr/bin/env python3
"""Restore a backup created by reset_to_stage2a.py."""
from __future__ import annotations
import argparse, shutil, subprocess, sys
from pathlib import Path

SERVICE="docling-autoconvert"

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("backup")
    ap.add_argument("--project-dir",default=".")
    ap.add_argument("--apply",action="store_true")
    ns=ap.parse_args()
    project=Path(ns.project_dir).resolve(); backup=Path(ns.backup).resolve()
    for p in (backup/"manifest.json", backup/"data", backup/"processed", backup/"config.yaml"):
        if not p.exists():
            print(f"ERROR: backup is incomplete: {p}",file=sys.stderr); return 2
    if not ns.apply:
        print(f"Would restore {backup} into {project}. Re-run with --apply."); return 0
    compose_file=project/"docker-compose.yml"
    if compose_file.is_file():
        subprocess.run(["docker","compose","stop",SERVICE],cwd=project,check=True)
    else:
        subprocess.run(["docker","stop",SERVICE],cwd=project,check=True)
    for name in ("data","processed"):
        dst=project/name
        if dst.exists(): shutil.rmtree(dst)
        shutil.copytree(backup/name,dst)
    shutil.copy2(backup/"config.yaml",project/"config.yaml")
    if compose_file.is_file():
        subprocess.run(["docker","compose","up","-d",SERVICE],cwd=project,check=True)
    else:
        subprocess.run(["docker","start",SERVICE],cwd=project,check=True)
    print("Backup restored and app restarted.")
    return 0

if __name__=="__main__": raise SystemExit(main())

#!/usr/bin/env python3
"""Safely reset Marine Pipeline Studio to fresh Stage 2A from existing Docling ZIPs.

Preserves the Stage-1 conversion ledger and /converted Docling outputs, backs up all
Stage-2+ state, removes derived processing/verification state, and optionally forces
Stage 2B execution to the configured dynamic Colab worker registry.

Run from the project directory:
    python3 tools/reset_to_stage2a.py --apply --colab-only
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

SERVICE = "docling-autoconvert"


def die(message: str, code: int = 2) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(code)


def docker_control(project: Path, action: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Stop/start the deployed app with or without a compose file.

    ZimaOS deployments may expose only the bind-mounted app-data directory
    (config/data/converted/processed) and keep docker-compose.yml elsewhere.
    In that layout, operate on the already-created container directly.
    """
    compose_file = project / "docker-compose.yml"
    if compose_file.is_file():
        cmd = ["docker", "compose", action if action == "stop" else "up"]
        if action == "start":
            cmd += ["-d"]
        cmd += [SERVICE]
    else:
        cmd = ["docker", "stop" if action == "stop" else "start", SERVICE]
    return subprocess.run(
        cmd, cwd=project, check=check,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def table_count(conn: sqlite3.Connection, name: str) -> int:
    if not table_exists(conn, name):
        return 0
    return int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])


def read_registry(data_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = data_dir / "worker_registry.json"
    if not path.is_file():
        die("data/worker_registry.json is missing. Configure/test Colab on the Workers page first.")
    try:
        registry = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        die(f"Could not read worker registry: {exc}")
    workers = registry.get("colab_workers") or []
    ready: list[dict[str, Any]] = []
    for worker in workers:
        wid = str(worker.get("id") or "")
        key_path = data_dir / "colab_workers" / f"{wid}.key"
        try:
            key = key_path.read_text(encoding="utf-8").strip()
            key.encode("ascii")
        except Exception:
            key = ""
        if (
            wid and worker.get("enabled") and not worker.get("remove_requested")
            and str(worker.get("url") or "").strip() and key
        ):
            ready.append(worker)
    if not ready:
        die("No enabled Colab worker has both a tunnel URL and an ASCII API key. Test one on Workers first.")
    # Prefer an already-unpaused worker; otherwise use the first configured worker.
    chosen = next((w for w in ready if not w.get("paused")), ready[0])
    return registry, chosen


def write_registry_colab_only(data_dir: Path, registry: dict[str, Any], chosen_id: str) -> None:
    local = registry.setdefault("local", {})
    for worker_id in ("pi5", "oneplus"):
        item = dict(local.get(worker_id) or {})
        item["paused"] = True
        item["artifact_enabled"] = False
        local[worker_id] = item
    for worker in registry.get("colab_workers") or []:
        if str(worker.get("id")) == chosen_id:
            worker["enabled"] = True
            worker["paused"] = False
            worker["artifact_enabled"] = True
    registry["updated_at_epoch"] = dt.datetime.now().timestamp()
    path = data_dir / "worker_registry.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def replace_yaml_scalar(path: Path, key: str, value: str) -> bool:
    text = path.read_text(encoding="utf-8")
    pattern = re.compile(rf"^(\s*{re.escape(key)}\s*:\s*).*$", re.MULTILINE)
    if pattern.search(text):
        revised = pattern.sub(rf"\g<1>{value}", text, count=1)
    else:
        revised = text.rstrip() + f"\n{key}: {value}\n"
    changed = revised != text
    if changed:
        path.write_text(revised, encoding="utf-8")
    return changed


def converted_manifest(converted_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for path in sorted(converted_dir.glob("*.zip"), key=lambda p: p.name.lower()):
        try:
            st = path.stat()
        except OSError:
            continue
        rows.append({"name": path.name, "size": st.st_size, "mtime_ns": st.st_mtime_ns})
    return rows


def db_counts(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path) as conn:
        return {
            "jobs": table_count(conn, "jobs"),
            "postprocess_jobs": table_count(conn, "postprocess_jobs"),
            "verification_jobs": table_count(conn, "verification_jobs"),
            "review_assistant_jobs": table_count(conn, "review_assistant_jobs"),
        }


def reset_database(db_path: Path) -> dict[str, int]:
    with sqlite3.connect(db_path, timeout=30) as conn:
        conn.execute("PRAGMA wal_checkpoint(FULL)")
        before = {
            "jobs": table_count(conn, "jobs"),
            "postprocess_jobs": table_count(conn, "postprocess_jobs"),
            "verification_jobs": table_count(conn, "verification_jobs"),
            "review_assistant_jobs": table_count(conn, "review_assistant_jobs"),
        }
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Review jobs and verification jobs depend on postprocess identity.
            if table_exists(conn, "review_assistant_jobs"):
                conn.execute("DELETE FROM review_assistant_jobs")
            if table_exists(conn, "verification_jobs"):
                conn.execute("DELETE FROM verification_jobs")
            if table_exists(conn, "postprocess_jobs"):
                conn.execute("DELETE FROM postprocess_jobs")
            # Intentionally KEEP jobs: completed conversion rows are the Stage-1
            # ledger that lets the app rediscover /converted ZIPs without Docling.
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        after = {
            "jobs": table_count(conn, "jobs"),
            "postprocess_jobs": table_count(conn, "postprocess_jobs"),
            "verification_jobs": table_count(conn, "verification_jobs"),
            "review_assistant_jobs": table_count(conn, "review_assistant_jobs"),
        }
    if after["jobs"] != before["jobs"]:
        raise RuntimeError("Stage-1 conversion ledger changed unexpectedly; refusing reset")
    if any(after[k] != 0 for k in ("postprocess_jobs", "verification_jobs", "review_assistant_jobs")):
        raise RuntimeError(f"Downstream database reset incomplete: {after}")
    return before


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-dir", default=".", help="Marine Pipeline Studio compose directory")
    parser.add_argument("--apply", action="store_true", help="Actually perform the reset (without this, only validate/preview)")
    parser.add_argument("--colab-only", action="store_true", help="Set Stage 2B text+vision providers to Colab and pause local workers")
    parser.add_argument("--no-restart", action="store_true", help="Leave the app stopped after reset")
    args = parser.parse_args()

    project = Path(args.project_dir).expanduser().resolve()
    compose_file = project / "docker-compose.yml"
    data_dir = project / "data"
    processed_dir = project / "processed"
    converted_dir = project / "converted"
    config_path = project / "config.yaml"
    db_path = data_dir / "jobs.db"

    for path, label in (
        (data_dir, "data/"),
        (processed_dir, "processed/"), (converted_dir, "converted/"),
        (config_path, "config.yaml"), (db_path, "data/jobs.db"),
    ):
        if not path.exists():
            die(f"Expected {label} at {path}")

    converted_before = converted_manifest(converted_dir)
    if not converted_before:
        die("No Docling ZIPs found in converted/. Nothing can restart from Stage 2A.")
    counts = db_counts(db_path)

    registry = chosen = None
    if args.colab_only:
        registry, chosen = read_registry(data_dir)

    print("Stage-2+ reset preview")
    print(f"  Project: {project}")
    print(f"  Docling ZIPs preserved: {len(converted_before)}")
    print(f"  Stage-1 DB rows preserved: {counts['jobs']}")
    print(f"  Stage-2A rows to clear: {counts['postprocess_jobs']}")
    print(f"  Stage-2B rows to clear: {counts['verification_jobs']}")
    print(f"  Review-assistant rows to clear: {counts['review_assistant_jobs']}")
    if chosen:
        print(f"  Colab worker: {chosen.get('id')} · {chosen.get('name')} · {chosen.get('model')} · {chosen.get('url')}")
        print("  Local Pi5/OnePlus Stage-2B workers: will be paused")
    if not args.apply:
        print("\nDry run only. Re-run with --apply to execute.")
        return 0

    timestamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = project / "backups" / f"stage2a-reset-{timestamp}"
    backup_root.mkdir(parents=True, exist_ok=False)

    stopped = False
    processed_moved = False
    try:
        print(f"\nStopping {SERVICE}...")
        result = docker_control(project, "stop", check=False)
        print(result.stdout.strip())
        if result.returncode != 0:
            raise RuntimeError("docker compose stop failed")
        stopped = True

        # Back up small mutable state exactly. The heavy processed tree is moved,
        # not copied, so reset is fast and rollback remains lossless.
        shutil.copytree(data_dir, backup_root / "data")
        shutil.copy2(config_path, backup_root / "config.yaml")
        if compose_file.is_file():
            shutil.copy2(compose_file, backup_root / "docker-compose.yml")
        manifest = {
            "schema": "marine-stage2a-reset-backup/v1",
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "project": str(project),
            "db_counts_before": counts,
            "converted_untouched": converted_before,
            "colab_only": bool(args.colab_only),
            "chosen_colab_worker": chosen,
        }
        (backup_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        target_processed = backup_root / "processed"
        processed_dir.rename(target_processed)
        processed_moved = True
        processed_dir.mkdir(parents=True, exist_ok=False)

        before = reset_database(db_path)
        if args.colab_only:
            assert registry is not None and chosen is not None
            write_registry_colab_only(data_dir, registry, str(chosen["id"]))
            replace_yaml_scalar(config_path, "text_verifier_provider", "colab")
            replace_yaml_scalar(config_path, "vision_verifier_provider", "colab")

        converted_after = converted_manifest(converted_dir)
        if converted_after != converted_before:
            raise RuntimeError("converted/ changed during reset; rolling back")

        print("Reset complete on disk.")
        print(f"  Backup: {backup_root}")
        print(f"  Preserved converted/: {len(converted_after)} Docling ZIPs")
        print(f"  Cleared: {before['postprocess_jobs']} Stage-2A, {before['verification_jobs']} Stage-2B, {before['review_assistant_jobs']} review rows")
        print(f"  Preserved: {before['jobs']} Stage-1 conversion rows")

        if not args.no_restart:
            print(f"Starting {SERVICE}...")
            result = docker_control(project, "start", check=False)
            print(result.stdout.strip())
            if result.returncode != 0:
                raise RuntimeError("Reset succeeded but docker compose start failed; backup is intact")
            stopped = False
            print("The app will rediscover every completed Docling conversion and queue fresh Stage 2A automatically.")
            if args.colab_only:
                print("When Stage 2B routes appear, text and vision are configured for the selected Colab worker.")
        else:
            print("App left stopped because --no-restart was requested.")
        return 0

    except Exception as exc:
        print(f"ERROR during reset: {exc}", file=sys.stderr)
        print("Attempting automatic rollback...", file=sys.stderr)
        try:
            # Restore DB/registry/config from exact backup snapshot.
            if (backup_root / "data").is_dir():
                if data_dir.exists():
                    shutil.rmtree(data_dir)
                shutil.copytree(backup_root / "data", data_dir)
            if (backup_root / "config.yaml").is_file():
                shutil.copy2(backup_root / "config.yaml", config_path)
            if processed_moved and (backup_root / "processed").exists():
                if processed_dir.exists():
                    shutil.rmtree(processed_dir)
                (backup_root / "processed").rename(processed_dir)
            if stopped:
                compose(project, "up", "-d", SERVICE, check=False)
            print("Rollback completed.", file=sys.stderr)
        except Exception as rollback_exc:
            print(f"ROLLBACK ERROR: {rollback_exc}. Backup remains at {backup_root}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

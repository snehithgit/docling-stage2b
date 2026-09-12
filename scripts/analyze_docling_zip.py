#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import AppConfig
from app.postprocess import build_diagnostics, build_profile, build_routes, inspect_docling_zip


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Stage 2A quality profiling on an existing Docling ZIP")
    parser.add_argument("zip_path")
    parser.add_argument("--output", default=None, help="Directory for profile/diagnostics/routes JSON")
    args = parser.parse_args()

    source = Path(args.zip_path).resolve()
    out = Path(args.output).resolve() if args.output else source.with_name(source.stem + "_quality")
    out.mkdir(parents=True, exist_ok=True)

    doc, json_member, members, integrity = inspect_docling_zip(source)
    cfg = AppConfig()
    profile = build_profile(doc)
    diagnostics = build_diagnostics(doc, cfg, integrity)
    routes = build_routes(doc, diagnostics, cfg)

    files = {
        "integrity.json": integrity,
        "coverage.json": diagnostics["coverage"],
        "profile.json": profile,
        "diagnostics.json": diagnostics,
        "routes.json": routes,
        "summary.json": {
            "docling_json_member": json_member,
            "archive_member_count": len(members),
            "primary_kind": profile["primary_kind"],
            "counts": profile["counts"],
            "integrity": {
                "status": integrity["status"],
                "display_label": integrity["display_label"],
                "missing_referenced_artifacts": integrity["referenced_artifacts"]["missing_internal_references"],
            },
            "coverage": {
                "status": diagnostics["coverage"]["overall_status"],
                "display_label": diagnostics["coverage"]["overall_display_label"],
                "warnings": len(diagnostics["coverage"]["warnings"]),
            },
            "diagnostics": diagnostics["summary"],
            "routes": routes["summary"],
        },
    }
    for name, payload in files.items():
        (out / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(files["summary.json"], indent=2, ensure_ascii=False))
    print(f"Written to: {out}")


if __name__ == "__main__":
    main()

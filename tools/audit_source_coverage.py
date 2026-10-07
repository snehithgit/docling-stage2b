"""Read-only source coverage report. Run from the repository root."""
import argparse
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.source_coverage import measure_source_coverage


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True, help="Immutable Docling JSON or converted ZIP")
    parser.add_argument("--result-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if zipfile.is_zipfile(args.source):
        with zipfile.ZipFile(args.source) as archive:
            documents = []
            for name in archive.namelist():
                if name.endswith(".json"):
                    value = json.loads(archive.read(name))
                    if isinstance(value, dict) and any(key in value for key in ("texts", "tables", "pictures")):
                        documents.append(value)
            if len(documents) != 1:
                raise ValueError("Expected exactly one Docling document; supply its JSON explicitly")
            document = documents[0]
    else:
        document = json.loads(args.source.read_text(encoding="utf-8"))
    from app.evidence_contract import read_source_rows
    ledger = args.result_dir / "technical_evidence_ledger.json"
    candidates = json.loads(ledger.read_text(encoding="utf-8")).get("entries", []) if ledger.exists() else []
    report = measure_source_coverage(document, read_source_rows(args.result_dir), candidates)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("source_items", "search_referenced_items", "missing_by_kind")}))


if __name__ == "__main__":
    main()

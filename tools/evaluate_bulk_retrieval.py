"""Evaluate frozen equipment indexes with split-aware source-span ground truth.

Embeddings are batched/cached; production ranking runs unchanged in CPU workers.
No answer generator, Colab endpoint, ledger writer or cloud API is called.
"""
from __future__ import annotations
import argparse, collections, concurrent.futures, hashlib, json, os, pathlib, re, sys, time
for key in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ[key] = "1"
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import numpy as np
from app import hybrid_retrieval as hybrid
from app import retrieval

ROOT = None
VECTORS = None
EQUIPMENT = None
BOOKS = None
CANONICAL = {}
LOOKUP = {}
LEX_IDS = None
LEX_SCORES = None


def norm(text):
    return re.sub(r"\s+", " ", str(text).casefold()).strip()


def initialize(root, vector_path):
    global ROOT, VECTORS, EQUIPMENT, BOOKS, CANONICAL, LOOKUP, LEX_IDS, LEX_SCORES
    ROOT = pathlib.Path(root)
    VECTORS = np.load(vector_path, mmap_mode="r") if vector_path else None
    EQUIPMENT = {e["equipment_id"]: e for e in json.loads((ROOT / "retrieval-equipment-audit.json").read_text(encoding="utf-8-sig"))["equipment"]}
    BOOKS = {b["source_filename"]: b for b in json.loads((ROOT / "retrieval-status-audit.json").read_text(encoding="utf-8-sig"))["books"]}
    for eid, e in EQUIPMENT.items():
        rows = []
        for manual in e['manuals']:
            if manual.get('active_for_rag'):
                rows.extend(retrieval._load_index(ROOT / 'retrieval-audit-corpus' / manual['result_dir'] / 'retrieval_index.jsonl'))
        CANONICAL[eid] = rows
        LOOKUP[eid] = {hybrid._row_key(r): i for i, r in enumerate(rows)}
    if vector_path and (ROOT / 'bulk-lexical-ids.npy').exists():
        LEX_IDS = np.load(ROOT / 'bulk-lexical-ids.npy', mmap_mode='r')
        LEX_SCORES = np.load(ROOT / 'bulk-lexical-scores.npy', mmap_mode='r')
        completed_path = ROOT / 'bulk-lexical-completed.json'
        for attempt in range(20):
            try:
                completed = set(json.loads(completed_path.read_text()))
                break
            except json.JSONDecodeError:
                time.sleep(0.1)
        else:
            completed = set()
        marker_checked = time.monotonic()
        original_search = hybrid.search_indices
        def cached_search(paths, query, *, top_k=5):
            nonlocal completed, marker_checked
            if time.monotonic() - marker_checked > 10:
                try:
                    completed = set(json.loads(completed_path.read_text()))
                except json.JSONDecodeError:
                    pass
                marker_checked = time.monotonic()
            slot = CURRENT_SLOT
            eid = CURRENT_EQUIPMENT
            if slot in completed and int(LEX_IDS[slot,0]) >= 0:
                return [{**CANONICAL[eid][int(idx)], 'rank': rank, 'score': float(score)}
                        for rank,(idx,score) in enumerate(zip(LEX_IDS[slot],LEX_SCORES[slot]),1) if int(idx)>=0][:top_k]
            return original_search(paths,query,top_k=top_k)
        hybrid.search_indices = cached_search
    # The benchmark corpus is a frozen copy, with a status check once per scope.
    # Cache ONLY freshness validation; query scoring remains production code.
    original = hybrid.equipment_hybrid_index_status
    cached = {}
    def status(*args, **kwargs):
        key = args[1]
        if key not in cached:
            cached[key] = original(*args, **kwargs)
        return dict(cached[key])
    hybrid.equipment_hybrid_index_status = status
    try:
        import psutil
        if os.name == "nt": psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except (ImportError, OSError):
        pass


def evaluate(item):
    global CURRENT_SLOT, CURRENT_EQUIPMENT
    ordinal, q = item
    CURRENT_SLOT, CURRENT_EQUIPMENT = ordinal, q["equipment_id"]
    began = time.monotonic()
    e = EQUIPMENT[q["equipment_id"]]
    paths = [ROOT / "retrieval-audit-corpus" / m["result_dir"] / "retrieval_index.jsonl" for m in e["manuals"] if m.get("active_for_rag")]
    rows, metadata = hybrid.hybrid_search_equipment(
        ROOT / "retrieval-audit-corpus", q["equipment_id"], paths, q["query"],
        base_url=os.environ.get("BULK_EMBEDDING_URL", "http://192.168.68.63:8090"), model="BAAI/bge-small-en-v1.5",
        query_prefix="Represent this sentence for searching relevant passages: ",
        document_prefix="", timeout_seconds=180, top_k=10,
        manual_types={m["postprocess_job_id"]: m.get("manual_type", "other") for m in e["manuals"]},
        query_vector=VECTORS[ordinal].tolist(),
    )
    expected = norm(q["evidence_span"])
    source = BOOKS[q["source_filename"]]
    canonical = next((r for r in retrieval._load_index(ROOT / "retrieval-audit-corpus" / source["result_dir"] / "retrieval_index.jsonl") if r["chunk_id"] == q["chunk_id"]), None)
    source_valid = bool(canonical and expected in norm(canonical["text"]))
    strict = next((r["rank"] for r in rows if r.get("source_filename") == q["source_filename"] and (r["chunk_id"] == q["chunk_id"] or q["chunk_id"] in r.get("source_chunk_ids", []))), None)
    support = next((r["rank"] for r in rows if expected in norm(r.get("text", ""))), None)
    def citation_matches(row):
        index = LOOKUP[q["equipment_id"]].get(hybrid._row_key(row))
        if index is None:
            return False
        original = CANONICAL[q["equipment_id"]][index]
        return bool(row.get("page_numbers")) and set(row["page_numbers"]) == set(original.get("page_numbers", [])) and row.get("source_filename") == original.get("source_filename")
    citation_ok = all(citation_matches(r) for r in rows[:5])
    return {
        "id": q["id"], "split": q["split"], "tier": q["tier"], "kind": q["kind"],
        "equipment_id": q["equipment_id"], "book": q["source_filename"], "query": q["query"],
        "evaluation_eligible": bool(q.get("evaluation_eligible")), "source_valid": source_valid,
        "strict_rank": strict, "support_rank": support, "citation_metadata_present": bool(citation_ok),
        "semantic_intent_available": bool(metadata.get("semantic_intent", {}).get("available")),
        "seconds": round(time.monotonic() - began, 5),
        "target_page_match": bool(next((set(q.get("page_numbers",[])).issubset(set(r.get("page_numbers",[]))) for r in rows if r.get("source_filename")==q["source_filename"] and (r["chunk_id"]==q["chunk_id"] or q["chunk_id"] in r.get("source_chunk_ids",[]))),False)),
        "top": [{"rank": r["rank"], "book": r.get("source_filename"), "chunk_id": r["chunk_id"], "pages": r.get("page_numbers", []), "score": r.get("score"), "preview": r.get("text", "")[:240]} for r in rows[:5]],
    }


def evaluate_lexical(item):
    ordinal,q=item
    e=EQUIPMENT[q['equipment_id']]
    paths=[ROOT/'retrieval-audit-corpus'/m['result_dir']/'retrieval_index.jsonl' for m in e['manuals'] if m.get('active_for_rag')]
    rows=retrieval.search_indices(paths,q['query'],top_k=60)
    compact=[(LOOKUP[q['equipment_id']][hybrid._row_key(r)],r['score']) for r in rows[:60]]
    return ordinal,compact


def summarize(rows):
    groups = collections.defaultdict(list)
    for r in rows:
        if r["evaluation_eligible"] and r["source_valid"]:
            groups["all_gold"].append(r)
            groups["split:" + r["split"]].append(r)
            groups["kind:" + r["kind"]].append(r)
            groups["equipment:" + r["equipment_id"]].append(r)
    summary = {}
    for name, data in groups.items():
        metrics = {"count": len(data)}
        for metric in ("strict_rank", "support_rank"):
            for k in (1, 3, 5, 10):
                metrics[f"{metric}_top{k}_percent"] = round(100 * sum(r[metric] is not None and r[metric] <= k for r in data) / len(data), 3)
            metrics[metric + "_mrr10"] = round(sum(1 / r[metric] if r[metric] else 0 for r in data) / len(data), 5)
        summary[name] = metrics
    return {"rows": len(rows), "changed_source_rows": sum(not r["source_valid"] for r in rows), "silver_rows": sum(not r["evaluation_eligible"] for r in rows), "metrics": summary}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=pathlib.Path, required=True)
    parser.add_argument("--questions", type=pathlib.Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--split", choices=("train", "dev", "test"))
    parser.add_argument("--name", default="bulk-hybrid")
    parser.add_argument("--lexical-only", action="store_true")
    parser.add_argument("--embedding-url", default=os.environ.get("BULK_EMBEDDING_URL", "http://127.0.0.1:8090"))
    args = parser.parse_args()
    os.environ["BULK_EMBEDDING_URL"] = args.embedding_url
    questions = [json.loads(line) for line in args.questions.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    fingerprint = hashlib.sha256(json.dumps([q["query"] for q in questions], ensure_ascii=False).encode()).hexdigest()
    cache = args.root / "bulk-query-vectors.npy"
    manifest = args.root / "bulk-query-vectors-manifest.json"
    if args.lexical_only:
        id_path=args.root/'bulk-lexical-ids.npy';score_path=args.root/'bulk-lexical-scores.npy'
        ids=np.lib.format.open_memmap(id_path,mode='r+' if id_path.exists() else 'w+',dtype=np.int32,shape=(len(questions),60))
        scores=np.lib.format.open_memmap(score_path,mode='r+' if score_path.exists() else 'w+',dtype=np.float64,shape=(len(questions),60))
        marker=args.root/'bulk-lexical-completed.json'
        done=set(json.loads(marker.read_text())) if marker.exists() else set()
        if not done: ids[:]=-1
        selected=sorted([(i,q) for i,q in enumerate(questions) if i not in done],key=lambda pair:pair[1]['equipment_id'])
        started=time.monotonic()
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers,initializer=initialize,initargs=(str(args.root),'')) as pool:
            for count,(ordinal,rows) in enumerate(pool.map(evaluate_lexical,selected,chunksize=8),1):
                if rows:
                    ids[ordinal,:len(rows)]=[r[0] for r in rows]
                    scores[ordinal,:len(rows)]=[r[1] for r in rows]
                done.add(ordinal)
                if count%100==0:
                    ids.flush();scores.flush();marker.write_text(json.dumps(sorted(done)))
                    print(f'Lexical ranked {len(done)}/{len(questions)} in {time.monotonic()-started:.1f}s',flush=True)
        ids.flush();scores.flush();marker.write_text(json.dumps(sorted(done)));return
    if not cache.exists() or not manifest.exists() or json.loads(manifest.read_text())["query_fingerprint"] != fingerprint:
        matrix = np.empty((len(questions), 384), dtype=np.float32)
        start = time.monotonic()
        for offset in range(0, len(questions), 32):
            texts = ["Represent this sentence for searching relevant passages: " + q["query"] for q in questions[offset:offset+32]]
            matrix[offset:offset+len(texts)] = hybrid.embed_texts(args.embedding_url, texts, timeout_seconds=180)
            if offset % 512 == 0: print(f"Embedded {offset+len(texts)}/{len(questions)} in {time.monotonic()-start:.1f}s", flush=True)
        np.save(cache, matrix)
        manifest.write_text(json.dumps({"query_fingerprint":fingerprint,"rows":len(questions),"model":"BAAI/bge-small-en-v1.5","prefix":"Represent this sentence for searching relevant passages: "}))
    selected = [(i,q) for i,q in enumerate(questions) if not args.split or q["split"] == args.split]
    if args.limit: selected = selected[:args.limit]
    output = args.root / (args.name + "-results.jsonl")
    existing = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()] if output.exists() else []
    done = {r["id"] for r in existing}
    selected = sorted([(i,q) for i,q in selected if q["id"] not in done],key=lambda pair:pair[1]["equipment_id"])
    # Complete any legacy metadata migration serially before parallel readers.
    initialize(str(args.root), str(cache))
    for eid, equipment in EQUIPMENT.items():
        paths = [args.root / "retrieval-audit-corpus" / m["result_dir"] / "retrieval_index.jsonl" for m in equipment["manuals"] if m.get("active_for_rag")]
        hybrid.equipment_hybrid_index_status(args.root / "retrieval-audit-corpus", eid, paths, model="BAAI/bge-small-en-v1.5", document_prefix="", manual_types={m["postprocess_job_id"]: m.get("manual_type", "other") for m in equipment["manuals"]})
    started = time.monotonic()
    with output.open("a", encoding="utf-8") as handle, concurrent.futures.ProcessPoolExecutor(max_workers=args.workers, initializer=initialize, initargs=(str(args.root), str(cache))) as pool:
        for i, result in enumerate(pool.map(evaluate, selected, chunksize=8), 1):
            handle.write(json.dumps(result, ensure_ascii=False) + "\n")
            if i % 50 == 0:
                handle.flush()
                print(f"Ranked {len(existing)+i}/{len(existing)+len(selected)} in {time.monotonic()-started:.1f}s", flush=True)
    all_rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
    summary = summarize(all_rows)
    (args.root / (args.name + "-summary.json")).write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary["metrics"].get("all_gold", {}), indent=2), flush=True)

if __name__ == "__main__": main()

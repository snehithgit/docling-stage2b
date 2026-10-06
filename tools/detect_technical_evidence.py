"""CPU-only detection report; --write creates a derived ledger, not corrections."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.technical_evidence import annotate_rows,write_evidence_ledger
p=argparse.ArgumentParser();p.add_argument('root',type=Path);p.add_argument('--write',action='store_true');args=p.parse_args()
for index in args.root.glob('*/retrieval_index.jsonl'):
 rows=[json.loads(line) for line in index.read_text(encoding='utf-8').splitlines() if line.strip()]
 _,records=annotate_rows(rows)
 if args.write:write_evidence_ledger(index.parent,rows)
 print(json.dumps({'book':index.parent.name,'records':len(records),'needs_visual_parse':sum(r['validation_status']=='needs_visual_parse' for r in records),'fault_cause_remedy_rows':sum(len(r['relationships']) for r in records),'written':args.write}))

"""A/B retrieval on source-span questions. No generator or corrections called.

Example: python tools/evaluate_structural_retrieval.py --root outputs --limit 300
The corpus is a frozen copy; metrics are not a claim about newer live content.
"""
import argparse,collections,json,re,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.retrieval import search_indices
from app.manual_structure import rebuild_structure,read_rows
from app.structural_retrieval import hierarchical_results

def norm(text):return re.sub(r'\s+',' ',str(text).casefold()).strip()

def run(root,limit,output):
 root=Path(root);corpus=root/'retrieval-audit-corpus'
 equipment={e['equipment_id']:e for e in json.loads((root/'retrieval-equipment-audit.json').read_text(encoding='utf-8-sig'))['equipment']}
 groups=collections.defaultdict(list)
 for line in (root/'bulk-inputs/questions_30000_v4.jsonl').read_text(encoding='utf-8').splitlines():
  q=json.loads(line)
  if q.get('evaluation_eligible'):groups[(q['source_filename'],q['kind'],q['split'])].append(q)
 selected=[]
 while groups and (not limit or len(selected)<limit):
  for key in sorted(list(groups)):
   selected.append(groups[key].pop(0))
   if not groups[key]:del groups[key]
   if limit and len(selected)>=limit:break
 for index in corpus.glob('*/retrieval_index.jsonl'):rebuild_structure(index.parent)
 cases=[];start=time.monotonic()
 for number,q in enumerate(selected,1):
  manuals=[m for m in equipment[q['equipment_id']]['manuals'] if m.get('active_for_rag')]
  paths=[corpus/m['result_dir']/'retrieval_index.jsonl' for m in manuals]
  original=next((r for path in paths for r in read_rows(path.parent) if r.get('source_filename')==q['source_filename'] and r['chunk_id']==q['chunk_id']),None)
  valid=bool(original and norm(q['evidence_span']) in norm(original['text']))
  baseline=search_indices(paths,q['query'],top_k=20)
  candidate,trace=hierarchical_results(paths,q['query'],baseline,top_k=10)
  def rank(rows):return next((i for i,r in enumerate(rows,1) if r.get('source_filename')==q['source_filename'] and norm(q['evidence_span']) in norm(r.get('text'))),None)
  old,new=rank(baseline[:10]),rank(candidate)
  cases.append({'id':q['id'],'query':q['query'],'split':q['split'],'kind':q['kind'],'source_valid':valid,
   'current_rank':old,'structural_rank':new,'expected_book':q['source_filename'],'expected_pages':q['page_numbers'],
   'failure_stage':None if new and new<=5 else 'CHANGED_EXPECTED_SOURCE' if not valid else 'NO_RELEVANT_EVIDENCE' if not candidate else 'WRONG_MANUAL' if candidate[0].get('source_filename')!=q['source_filename'] else 'CITATION_RANK_FAILURE',
   'section_accuracy':None,'section_gold_available':False,'trace':trace,
   'current_top':[{'chunk_id':r['chunk_id'],'book':r.get('source_filename'),'pages':r.get('page_numbers')} for r in baseline[:5]],
   'structural_top':[{'chunk_id':r['chunk_id'],'book':r.get('source_filename'),'pages':r.get('page_numbers')} for r in candidate[:5]]})
  if number%25==0:print(f'{number}/{len(selected)} questions',flush=True)
 valid=[r for r in cases if r['source_valid']]
 def metrics(field):
  return {'cases':len(valid),**{f'recall_at_{k}':sum(r[field] is not None and r[field]<=k for r in valid)/max(1,len(valid)) for k in (1,3,5)},'mrr_at_10':sum(1/r[field] if r[field] else 0 for r in valid)/max(1,len(valid))}
 report={'scope':'frozen machine-scoped lexical corpus','selection':'round_robin_by_manual_kind_split','questions':len(cases),'current':metrics('current_rank'),'structural':metrics('structural_rank'),
  'top5_gains':sum((r['current_rank'] or 99)>5 and (r['structural_rank'] or 99)<=5 for r in valid),
  'top5_losses':sum((r['current_rank'] or 99)<=5 and (r['structural_rank'] or 99)>5 for r in valid),
  'elapsed_seconds':time.monotonic()-start,'section_gold_available':False,'generated_answer_accuracy_measured':False,'cases':cases}
 Path(output).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
 print(json.dumps({k:v for k,v in report.items() if k!='cases'},indent=2))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--limit',type=int,default=300);p.add_argument('--output',type=Path,required=True);a=p.parse_args();run(a.root,a.limit,a.output)

"""Optional hierarchy preference with preserved global candidates and citations."""
from pathlib import Path
from .manual_structure import route_sections, chunk_context, read_rows

def _key(row):
 return (row.get('postprocess_job_id'),row.get('source_filename'),row.get('chunk_id'))

def hierarchical_results(index_paths, query, baseline, *, top_k=5, section_limit=5, section_candidates=15, boost=0.03, adjacent=True, query_vector=None, model=None):
 from .retrieval import search_indices
 preferred,reports,trace=route_sections(index_paths,query,section_limit=section_limit,query_vector=query_vector,model=model)
 if not reports:
  return baseline[:top_k],{**trace,'status':'no_current_maps','preferred_sections':[]}
 scoped=search_indices(index_paths,query,top_k=section_candidates,allowed_chunks=preferred) if preferred else []
 contexts={}; source_rows={}; section_for={}
 for path in index_paths:
  report=reports.get(str(Path(path).parent))
  if not report:continue
  for row in read_rows(Path(path).parent):
   key=_key(row); contexts[key]=chunk_context(row,report); source_rows[key]=row
   section_for[key]=report['chunk_section_map'].get(str(row.get('chunk_id')))
 section_ranks={_key(r):i+1 for i,r in enumerate(scoped)}
 result={}
 # Keep the production relevance score. Reciprocal ranks alone flatten the
 # difference between a strong literal hit and a weak broad chapter match.
 for rank,row in enumerate(baseline,1):
  key=_key(row); score=float(row.get('score') or 1/(60+rank))
  if key in section_ranks:score*=1+max(0,min(float(boost),0.25))
  result[key]={**contexts.get(key,{}),**row,'structural_score':score}
 for rank,row in enumerate(scoped,1):
  key=_key(row)
  if key not in result:
   floor=min((r['structural_score'] for r in result.values()),default=float(row.get('score') or 0))
   result[key]={**contexts.get(key,{}),**row,'structural_score':floor*0.9/(1+0.01*rank)}
 ranked=sorted(result.values(),key=lambda r:-r['structural_score'])[:top_k]
 for rank,row in enumerate(ranked,1):
  row['rank']=rank
  if adjacent:
   anchor=source_rows.get(_key(row)); sid=section_for.get(_key(row))
   neighbors=[]
   if anchor and sid:
    ap=set(anchor.get('page_numbers') or []); order=int(anchor.get('chunk_index') or 0)
    for key,other in source_rows.items():
     if key==_key(row) or key[:2]!=_key(row)[:2] or section_for.get(key)!=sid:continue
     pages=set(other.get('page_numbers') or [])
     if pages and ap and min(abs(a-b) for a in ap for b in pages)<=1 and abs(int(other.get('chunk_index') or 0)-order)<=2:
      neighbors.append({'chunk_id':other['chunk_id'],'postprocess_job_id':other.get('postprocess_job_id'),
        'source_filename':other.get('source_filename'),'page_numbers':other.get('page_numbers'),
        'doc_items':other.get('doc_items'),'text':other.get('text'), 'headings':other.get('headings'),
        'relationship':'same_section_continuation','relationship_verified':False})
   if anchor and sid: row['context_neighbors']=neighbors[:2]
 trace.update(status='current',global_candidates=len(baseline),section_candidates=len(scoped),
    manual_maps=len(reports),score_method='source_score_with_bounded_section_preference',automatic_acceptance=False)
 return ranked,trace

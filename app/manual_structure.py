"""Derived manual hierarchy. Section routing is context, never answer evidence."""
from __future__ import annotations
import hashlib
import json
import re
import time
import math
from pathlib import Path
from functools import lru_cache
from .evidence_contract import atomic_json, source_signature

VERSION = 'manual-structure/v1'
CATEGORIES = {
 'GENERAL': r'general|introduction', 'DESCRIPTION': r'description|overview|function',
 'SAFETY': r'safety|warning|precaution', 'INSTALLATION': r'installation|mounting',
 'COMMISSIONING': r'commissioning', 'STARTUP': r'start.?up|starting', 'SHUTDOWN': r'shut.?down|stopping',
 'TROUBLESHOOTING': r'trouble\s*shoot|fault finding|fault diagnosis|abnormal operation',
 'FAULT_CODES': r'fault codes?|error codes?', 'ALARMS': r'alarm', 'SETTINGS': r'settings?|adjust',
 'PARAMETERS': r'parameters?|setpoints?', 'SPECIFICATIONS': r'specifications?|technical data|ratings?',
 'MAINTENANCE': r'maintenance|servicing|repair', 'INSPECTION': r'inspection',
 'ELECTRICAL': r'electrical|electric', 'HYDRAULIC': r'hydraulic', 'PNEUMATIC': r'pneumatic',
 'PARTS': r'spare parts|replacement parts|parts catalog|part numbers?', 'WIRING': r'wiring|cables?',
 'TERMINALS': r'terminals?', 'DRAWINGS': r'drawings?|schematic|diagrams?',
 'PROCEDURES': r'procedures?|instructions?', 'TESTING': r'testing|tests?',
 'CALIBRATION': r'calibration', 'OPERATION': r'operation|operating', 'OTHER': r'(?!)',
}

def normalized(value):
 return re.sub(r'\s+', ' ', str(value or '')).strip().casefold()

def category(title):
 for name, pattern in CATEGORIES.items():
  if re.search(r'\b(?:' + pattern + r')', normalized(title)): return name
 return 'OTHER'

def query_categories(query):
 result = [name for name, pattern in CATEGORIES.items() if re.search(r'\b(?:'+pattern+r')', normalized(query))]
 if re.search(r'\bwhy|not start|does not|won.t|stops|failure|fault', normalized(query)):
  result.append('TROUBLESHOOTING')
 if re.search(r'\bhow.*(?:set|adjust)|\bsetting', normalized(query)):
  result.extend(['SETTINGS', 'MAINTENANCE'])
 return list(dict.fromkeys(result))

def read_rows(directory):
 path = Path(directory) / 'retrieval_index.jsonl'
 stat=path.stat()
 return list(_read_rows_cached(str(path),stat.st_mtime_ns,stat.st_size))

@lru_cache(maxsize=64)
def _read_rows_cached(path, modified, size):
 return tuple(json.loads(line) for line in Path(path).read_text(encoding='utf-8').splitlines() if line.strip())

def _sid(path):
 return 's-' + hashlib.sha256(json.dumps(path, ensure_ascii=False).encode()).hexdigest()[:20]

def pdf_outline(path):
 if not path or not Path(path).is_file(): return []
 import fitz
 with fitz.open(path) as doc:
  return [{'level':level, 'title':title, 'page':page, 'source':'pdf_bookmark'}
          for level,title,page in doc.get_toc() if 1 <= page <= len(doc)]

def build_structure(rows, *, outline=None, overrides=None):
 sections = {}; assignments = {}; unresolved = []; toc = []
 maximum_page = max((int(p) for r in rows for p in r.get('page_numbers',[]) if str(p).isdigit()), default=0)
 numbered = {}
 for order, row in enumerate(rows):
  pages = [int(p) for p in row.get('page_numbers',[]) if str(p).isdigit() and int(p)>0]
  headings = [re.sub(r'\s+',' ',str(h)).strip() for h in row.get('headings',[]) if str(h).strip()]
  parent = None; path = []
  for title in headings:
   # An explicit numbered parent may supplement a flattened heading path.
   number = re.match(r'^\s*(\d+(?:\.\d+)*)(?:\s|[.)]\s)',title)
   if parent is None and number and '.' in number[1]:
    parent = numbered.get(number[1].rsplit('.',1)[0])
    if parent: path = list(sections[parent]['normalized_path'])
   path.append(normalized(title)); sid = _sid(path)
   if sid not in sections:
    sections[sid] = {'section_id':sid, 'title':title, 'normalized_title':normalized(title),
      'normalized_path':list(path), 'category':category(title), 'parent_section_id':parent,
      'level':len(path), 'start_order':order, 'end_order':order,
      'start_page':min(pages) if pages else None, 'end_page':max(pages) if pages else None,
      'structure_source':['chunk_heading'], 'source_refs':[], 'chunk_ids':[], 'children':[],
      'manual_override':False, 'confidence':None}
   section = sections[sid]; section['end_order'] = order
   if pages:
    section['start_page'] = min(section['start_page'] or min(pages), min(pages))
    section['end_page'] = max(section['end_page'] or max(pages), max(pages))
   section['chunk_ids'].append(str(row['chunk_id']))
   section['source_refs'] = list(dict.fromkeys(section['source_refs'] + list(row.get('doc_items') or [])))
   if number: numbered[number[1]] = sid
   parent = sid
  if parent: assignments[str(row['chunk_id'])] = parent
  for line in str(row.get('text') or '').splitlines():
   m = re.match(r'^\s*(.{3,100}?)\s*\.{3,}\s*(\d+)\s*$',line)
   if m: toc.append({'title':m[1].strip(),'printed_page':int(m[2]),'source_chunk_id':row['chunk_id'],'page_numbers':pages})
 # Confirm bookmarks against actual heading anchors. Unmatched bookmarks remain
 # inspectable hints; do not invent a page offset or silently override headings.
 bookmark_stack=[]
 for bookmark in outline or []:
  while bookmark_stack and bookmark_stack[-1][0]>=bookmark['level']: bookmark_stack.pop()
  matches = [s for s in sections.values() if s['normalized_title']==normalized(bookmark['title'])]
  if len(matches)==1 and matches[0]['start_page']==bookmark['page']:
   matches[0]['structure_source'].append('pdf_bookmark')
   bookmark_stack.append((bookmark['level'],matches[0]['section_id']))
  elif not matches and 1<=bookmark['page']<=maximum_page:
   parent=bookmark_stack[-1][1] if bookmark_stack else None
   path=(sections[parent]['normalized_path'] if parent else [])+[normalized(bookmark['title'])]
   sid=_sid(['bookmark']+path+[str(bookmark['page'])])
   order=next((i for i,r in enumerate(rows) if any(int(p)>=bookmark['page'] for p in r.get('page_numbers',[]) if str(p).isdigit())),len(rows)-1)
   sections[sid]={'section_id':sid,'title':bookmark['title'],'normalized_title':normalized(bookmark['title']),
    'normalized_path':path,'category':category(bookmark['title']),'parent_section_id':parent,'level':len(path),
    'start_order':order,'end_order':order,'start_page':bookmark['page'],'end_page':maximum_page,
    'structure_source':['pdf_bookmark'],'source_refs':[],'chunk_ids':[],'children':[],
    'manual_override':False,'confidence':None}
   bookmark_stack.append((bookmark['level'],sid))
  else: unresolved.append({'kind':'bookmark_not_confirmed','evidence':bookmark})
 for hint in toc:
  matches = [s for s in sections.values() if s['normalized_title']==normalized(hint['title'])]
  if len(matches)==1:
   matches[0]['structure_source'].append('toc_title_match')
   hint['resolved_pdf_page'] = matches[0]['start_page']
  else: unresolved.append({'kind':'toc_title_not_confirmed','evidence':hint})
 # Sequential end ranges stop at the next peer/ancestor heading, but chunk
 # assignment uses source order, because two sections can share a PDF page.
 ordered = sorted(sections.values(), key=lambda s:(s['start_order'],s['level']))
 for pos, section in enumerate(ordered):
  next_section = next((s for s in ordered[pos+1:] if s['level']<=section['level'] and s['start_order']>section['start_order']),None)
  if next_section:
   section['end_order'] = max(section['end_order'],next_section['start_order']-1)
   end_pages = [int(p) for r in rows[section['start_order']:next_section['start_order']] for p in r.get('page_numbers',[]) if str(p).isdigit()]
   if end_pages: section['end_page']=max(max(end_pages), (next_section['start_page'] or max(end_pages))-1)
  elif section['start_page'] is not None: section['end_page']=max(section['end_page'] or 0,maximum_page)
 for sid, override in (overrides or {}).items():
  if sid not in sections:
   unresolved.append({'kind':'orphaned_manual_override','section_id':sid}); continue
  sections[sid].update({k:v for k,v in override.items() if k in {'title','category','start_page','end_page'}})
  sections[sid].update(manual_override=True, override_actor=override.get('actor'), override_at=override.get('updated_at'))
 for row in rows:
  if str(row['chunk_id']) in assignments: continue
  pages=[int(p) for p in row.get('page_numbers',[]) if str(p).isdigit()]
  candidates=[s for s in sections.values() if 'pdf_bookmark' in s['structure_source'] and pages and s['start_page']<=min(pages) and max(pages)<=s['end_page']]
  if candidates:
   depth=max(s['level'] for s in candidates); candidates=[s for s in candidates if s['level']==depth]
   if len(candidates)==1:assignments[str(row['chunk_id'])]=candidates[0]['section_id']
   else:unresolved.append({'kind':'ambiguous_bookmark_range','chunk_id':row['chunk_id']})
 # Apply explicit human range overrides only when they unambiguously select a
 # deepest section. Ambiguous ranges remain diagnostic, not guessed evidence.
 for row in rows:
  pages = [int(p) for p in row.get('page_numbers',[]) if str(p).isdigit()]
  candidates = [s for s in sections.values() if s['manual_override'] and pages and s['start_page']<=min(pages) and max(pages)<=s['end_page']]
  if candidates:
   deepest=max(s['level'] for s in candidates); candidates=[s for s in candidates if s['level']==deepest]
   if len(candidates)==1: assignments[str(row['chunk_id'])]=candidates[0]['section_id']
   else: unresolved.append({'kind':'ambiguous_override_range','chunk_id':row['chunk_id']})
 for s in sections.values(): s['chunk_ids']=[]; s['children']=[]
 for cid,sid in assignments.items():
  current=sid; seen=set()
  while current and current not in seen:
   seen.add(current); sections[current]['chunk_ids'].append(cid); current=sections[current]['parent_section_id']
 for s in sections.values():
  parent=s['parent_section_id']
  if parent:
   if parent not in sections: unresolved.append({'kind':'invalid_parent','section_id':s['section_id']})
   else:
    sections[parent]['children'].append(s['section_id'])
    ancestor=sections[parent]
    if all(isinstance(v,int) for v in (s['start_page'],s['end_page'],ancestor['start_page'],ancestor['end_page'])) and (s['start_page']<ancestor['start_page'] or s['end_page']>ancestor['end_page']):
     unresolved.append({'kind':'child_outside_parent_range','section_id':s['section_id'],'parent_section_id':parent})
  path=[]; current=s; seen=set()
  while current and current['section_id'] not in seen:
   seen.add(current['section_id']); path.insert(0,current['title']); current=sections.get(current['parent_section_id'])
  s['breadcrumb']=path
  if not s['start_page'] or not s['end_page'] or s['start_page']>s['end_page'] or s['end_page']>maximum_page:
   unresolved.append({'kind':'invalid_page_range','section_id':s['section_id']})
  s['search_text']=' > '.join(path)+'\nCategory: '+s['category']
 for i,left in enumerate(ordered):
  for right in ordered[i+1:]:
   if left['parent_section_id']==right['parent_section_id'] and left['start_page'] and right['start_page'] and left['end_page']>right['start_page'] and right['end_page']>=left['start_page']:
    unresolved.append({'kind':'sibling_page_overlap','section_ids':[left['section_id'],right['section_id']]})
 return {'schema':VERSION,'source_signature':source_signature(rows),'created_at':time.time(),
   'sections':list(sections.values()),'chunk_section_map':assignments,'toc_evidence':toc,'outline_evidence':outline or [],
   'diagnostics':{'sections':len(sections),'assigned_chunks':len(assignments),'total_chunks':len(rows),
      'unassigned_chunk_ids':[str(r['chunk_id']) for r in rows if str(r['chunk_id']) not in assignments],
      'unresolved':unresolved,'manual_overrides':len(overrides or {}),'model_calls':0},
   'semantic_accuracy_verified':False}

def rebuild_structure(directory, *, pdf_path=None):
 directory=Path(directory); rows=read_rows(directory)
 overrides_path=directory/'manual_structure_overrides.json'
 overrides=json.loads(overrides_path.read_text(encoding='utf-8')) if overrides_path.is_file() else {}
 prior_path=directory/'manual_structure.json'
 prior=json.loads(prior_path.read_text(encoding='utf-8')) if prior_path.is_file() else {}
 outline=pdf_outline(pdf_path) if pdf_path else prior.get('outline_evidence',[])
 report=build_structure(rows, outline=outline, overrides=overrides)
 atomic_json(prior_path,report)
 return report

def structure_status(directory):
 path=Path(directory)/'manual_structure.json'
 if not path.is_file(): return {'status':'not_built','sections':[]}
 stat=path.stat(); index=(Path(directory)/'retrieval_index.jsonl').stat()
 return dict(_structure_cached(str(path),stat.st_mtime_ns,stat.st_size,index.st_mtime_ns,index.st_size))

@lru_cache(maxsize=64)
def _structure_cached(path, modified, size, index_modified, index_size):
 report=json.loads(Path(path).read_text(encoding='utf-8'))
 report['status']='current' if report.get('source_signature')==source_signature(read_rows(Path(path).parent)) else 'stale'
 return report

def save_override(directory, section_id, values, *, actor, reset=False):
 if reset: values={}
 directory=Path(directory); report=structure_status(directory)
 if report['status']!='current': raise ValueError('Rebuild the manual map before editing it.')
 if not any(s['section_id']==section_id for s in report['sections']): raise ValueError('Section not found')
 if not str(actor).strip(): raise ValueError('Your name is required')
 allowed={'title','category','start_page','end_page'}
 if set(values)-allowed: raise ValueError('Unknown section field')
 sections={s['section_id']:s for s in report['sections']}; merged={**sections[section_id],**values}
 if merged['category'] not in CATEGORIES: raise ValueError('Unknown category')
 if not isinstance(merged['title'],str) or not merged['title'].strip() or len(merged['title'])>300: raise ValueError('Invalid title')
 maximum=max((int(p) for r in read_rows(directory) for p in r.get('page_numbers',[]) if str(p).isdigit()),default=0)
 if any(not isinstance(merged[k],int) or isinstance(merged[k],bool) for k in ('start_page','end_page')) or not 1<=merged['start_page']<=merged['end_page']<=maximum: raise ValueError('Invalid PDF page range')
 path=directory/'manual_structure_overrides.json'
 overrides=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
 history=directory/'manual_structure_override_history.json'
 previous=json.loads(history.read_text(encoding='utf-8')) if history.is_file() else []
 previous.append({'section_id':section_id,'before':overrides.get(section_id),'after':None if reset else values,'actor':actor.strip(),'at':time.time()})
 atomic_json(history,previous)
 if reset: overrides.pop(section_id,None)
 else: overrides[section_id]={k:merged[k] for k in allowed}|{'actor':actor.strip(),'updated_at':time.time()}
 atomic_json(path,overrides)
 return rebuild_structure(directory)

def chunk_context(row, report):
 sid=report.get('chunk_section_map',{}).get(str(row.get('chunk_id')))
 section=next((s for s in report.get('sections',[]) if s['section_id']==sid),None)
 if not section:return dict(row)
 return {**row,'section_id':sid,'section_title':section['title'],'section_category':section['category'],
  'section_start_page':section['start_page'],'section_end_page':section['end_page'],
  'parent_section_id':section['parent_section_id'],'breadcrumb':section['breadcrumb'],
  'structure_source':section['structure_source'],'structural_index_version':VERSION,
  'original_text':row.get('text',''),'search_text':section['search_text']+'\n'+str(row.get('text') or '')}

def section_vector_path(directory, model):
 return Path(directory)/('manual_section_vectors.'+hashlib.sha256(model.encode()).hexdigest()[:16]+'.json')

def build_section_embeddings(directory, *, base_url, model, document_prefix='', timeout_seconds=180):
 from .hybrid_retrieval import embed_texts
 report=structure_status(directory)
 if report['status']!='current':raise ValueError('Rebuild the manual map before building its section embeddings.')
 path=section_vector_path(directory,model)
 old=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
 reusable=old.get('records',{}) if old.get('model')==model and old.get('document_prefix')==document_prefix else {}
 records={}; pending=[]
 for s in report['sections']:
  text=document_prefix+s['search_text']; fingerprint=hashlib.sha256(text.encode()).hexdigest()
  previous=reusable.get(s['section_id'],{})
  if previous.get('fingerprint')==fingerprint:
   records[s['section_id']]=previous
  else:pending.append((s['section_id'],text,fingerprint))
 for start in range(0,len(pending),32):
  batch=pending[start:start+32];vectors=embed_texts(base_url,[r[1] for r in batch],timeout_seconds=timeout_seconds)
  if len(vectors)!=len(batch):raise ValueError('Section embedding count mismatch')
  for (sid,text,fingerprint),vector in zip(batch,vectors):
   if not vector or any(not isinstance(v,(float,int)) or not math.isfinite(v) for v in vector):raise ValueError('Invalid section embedding')
   records[sid]={'fingerprint':fingerprint,'vector':vector}
 dimensions={len(r['vector']) for r in records.values()}
 if len(dimensions)>1:raise ValueError('Section embedding dimensions changed; rebuild with the current model.')
 current=structure_status(directory)
 if current['status']!='current' or current.get('source_signature')!=report['source_signature'] or current.get('sections')!=report['sections']:
  raise ValueError('The manual map changed during embedding; rebuild before retrying.')
 atomic_json(path,{'schema':'manual-section-vectors/v1','source_signature':report['source_signature'],
  'structure_signature':hashlib.sha256(json.dumps(report['sections'],sort_keys=True).encode()).hexdigest(),
  'model':model,'document_prefix':document_prefix,'records':records})
 return {'sections':len(records),'new_vectors':len(pending),'reused_vectors':len(records)-len(pending),'model':model,'source_text_changed':False}

def route_sections(index_paths, query, *, section_limit=5, query_vector=None, model=None):
 words=set(re.findall(r'[a-z0-9]+(?:[_.-][a-z0-9]+)*',normalized(query))) - set('what why how is the a an of to for does do can not manual find describe description information explain tell me about'.split())
 intents=query_categories(query); routes=[]; reports={}; preferred=set()
 for path in index_paths:
  report=structure_status(Path(path).parent)
  if report['status']!='current':continue
  reports[str(Path(path).parent)]=report
  vectors={}
  if query_vector and model:
   vector_path=section_vector_path(Path(path).parent,model)
   if vector_path.is_file():
    data=json.loads(vector_path.read_text(encoding='utf-8'))
    if data.get('model')==model and data.get('source_signature')==report['source_signature']:
     for s in report['sections']:
      item=data.get('records',{}).get(s['section_id'],{});text=str(data.get('document_prefix') or '')+s['search_text']
      if item.get('fingerprint')==hashlib.sha256(text.encode()).hexdigest():vectors[s['section_id']]=item.get('vector')
  for section in report['sections']:
   terms=set(re.findall(r'[a-z0-9]+(?:[_.-][a-z0-9]+)*',normalized(' '.join(section['breadcrumb']))))
   matches=words & terms; coverage=len(matches)/max(1,len(words)); score=len(matches)*2 + (1 if section['category'] in intents else 0)
   vector=vectors.get(section['section_id']); semantic_match=False
   if vector and len(vector)==len(query_vector):
    denominator=math.sqrt(sum(v*v for v in vector)*sum(v*v for v in query_vector))
    similarity=sum(a*b for a,b in zip(vector,query_vector))/denominator if denominator else 0
    if similarity>0.4:score+=similarity
    semantic_match=similarity>=0.75
   if score>0 and (coverage>=0.6 or semantic_match) and section['chunk_ids']:
    routes.append({'score':score,'directory':str(Path(path).parent),'section_id':section['section_id'],
      'query_term_coverage':coverage,'semantic_match':semantic_match,
      'title':section['title'],'breadcrumb':section['breadcrumb'],'category':section['category'],
      'start_page':section['start_page'],'end_page':section['end_page'],'chunk_ids':section['chunk_ids']})
 routes.sort(key=lambda r:(-r['score'],len(r['chunk_ids']),r['directory'],r['section_id']))
 for route in routes[:section_limit]:
  preferred.update((route['directory'],cid) for cid in route['chunk_ids'])
 return preferred, reports, {'mode':'structural','intents':intents,'preferred_sections':[{k:v for k,v in r.items() if k not in {'chunk_ids','directory','score'}}|{'manual':Path(r['directory']).name} for r in routes[:section_limit]],'global_fallback':True,'routing_is_evidence':False}

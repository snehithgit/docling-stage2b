"""Image-bound graph candidates. Geometry and model confidence are not proof."""
import hashlib
import json
import math
import time
from .evidence_contract import normalize_ledger, read_source_rows, source_signature, atomic_json, normalized_record

VERSION = 'visual-graph/v1'
GRAPH_SCHEMA = {'type':'object','additionalProperties':False,'properties':{
 'nodes':{'type':'array','items':{'type':'object','additionalProperties':False,'properties':{'id':{'type':'string'},'text':{'type':'string'},'bbox':{'type':'array','items':{'type':'number'}}},'required':['id','text','bbox']}},
 'edges':{'type':'array','items':{'type':'object','additionalProperties':False,'properties':{'source':{'type':'string'},'target':{'type':'string'},'label':{'type':'string'},'direction':{'type':'string','enum':['forward','undirected','unknown']}},'required':['source','target','label','direction']}},
 'unresolved':{'type':'array','items':{'type':'string'}}},'required':['nodes','edges','unresolved']}
PROMPT = '''Read this original manual image only. Return one JSON object with nodes, edges, unresolved.
Nodes: up to 40 visible labeled boxes, components or callouts, each {id,text,bbox}. Copy exact visible text, preserving numbers, units, negation and part identifiers. bbox is [left,top,right,bottom] normalized 0..1 in this FULL image. Never infer hidden labels or component functions.
Edges: up to 60 visible connections, each {source,target,label,direction}; endpoints must be node IDs. label is exact branch text or empty. direction is forward only when an arrowhead visibly points from source to target; undirected for a clearly visible line with no arrow; unknown when unclear. Crossing lines do not imply a junction. Do not invent causes, remedies or merge separate branches.
Record unreadable labels, uncertain arrowheads, missing continuation and omitted detail in unresolved. No outside engineering knowledge, markdown, summary or confidence. Empty lists are allowed.'''

def graph_hash(graph):
 return hashlib.sha256(json.dumps(graph,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()

def normalize_graph(value):
 if not isinstance(value,dict) or set(value) != {'nodes','edges','unresolved'}: raise ValueError('Invalid visual graph object')
 nodes=value['nodes']; edges=value['edges']; unresolved=value['unresolved']
 if not isinstance(nodes,list) or len(nodes)>40 or not isinstance(edges,list) or len(edges)>60: raise ValueError('Visual graph exceeds limits')
 if not isinstance(unresolved,list) or len(unresolved)>40 or any(not isinstance(s,str) or len(s)>500 for s in unresolved): raise ValueError('Invalid unresolved details')
 ids=set()
 for node in nodes:
  if not isinstance(node,dict) or set(node) != {'id','text','bbox'}: raise ValueError('Invalid visual node')
  if not isinstance(node['id'],str) or not node['id'].strip() or len(node['id'])>80 or node['id'] in ids: raise ValueError('Duplicate or missing node identity')
  ids.add(node['id'])
  if not isinstance(node['text'],str) or not node['text'].strip() or len(node['text'])>1500: raise ValueError('Missing or oversized node text')
  box=node['bbox']
  if not isinstance(box,list) or len(box)!=4 or any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) or not 0<=v<=1 for v in box) or box[0]>=box[2] or box[1]>=box[3]: raise ValueError('Invalid node geometry')
 seen=set()
 for edge in edges:
  if not isinstance(edge,dict) or set(edge) != {'source','target','label','direction'}: raise ValueError('Invalid visual edge')
  if not isinstance(edge['source'],str) or not isinstance(edge['target'],str) or edge['source'] not in ids or edge['target'] not in ids: raise ValueError('Dangling visual edge')
  if edge['direction'] not in {'forward','undirected','unknown'} or not isinstance(edge['label'],str) or len(edge['label'])>300: raise ValueError('Invalid visual direction or label')
  key=tuple(edge[k] for k in ('source','target','label','direction'))
  if key in seen: raise ValueError('Duplicate visual edge')
  seen.add(key)
 return json.loads(json.dumps(value,allow_nan=False))

def current_entry(directory, entry_id):
 ledger=normalize_ledger(json.loads((directory/'technical_evidence_ledger.json').read_text(encoding='utf-8')))
 rows=read_source_rows(directory)
 if ledger.get('source_index_signature') != source_signature(rows): raise ValueError('Technical source index is stale; refresh detection first')
 matches=[r for r in ledger['entries'] if r['entry_id']==entry_id and not r.get('superseded')]
 if len(matches)!=1: raise ValueError('Current evidence entry not found')
 record=matches[0]
 row=next((r for r in rows if r['chunk_id']==record['source_chunk_id']),None)
 if row is None or any(record.get(k)!=row.get(v) for k,v in [('source_text','text'),('page_numbers','page_numbers'),('doc_items','doc_items')]): raise ValueError('Evidence source changed')
 return ledger,record

def save_graph(directory, entry_id, graph, picture_index, image_sha256, expected_source_sha256, provider, model):
 graph=normalize_graph(graph)
 ledger,record=current_entry(directory,entry_id)
 if record['source_sha256']!=expected_source_sha256 or f'#/pictures/{picture_index}' not in record.get('doc_items',[]): raise ValueError('Visual source changed during extraction')
 if record['validation']['state'] in {'rejected','validated'}: raise ValueError('Explicitly reset reviewed evidence before replacing extraction')
 extraction={'schema':VERSION,'graph':graph,'graph_sha256':graph_hash(graph),'image_sha256':image_sha256,'picture_index':picture_index,'source_sha256':expected_source_sha256,'provider':provider,'model':model,'created_at_epoch':time.time(),'state':'needs_review','coordinate_space':'full_image_normalized'}
 if record.get('visual_extraction'):
  record.setdefault('visual_extraction_history',[]).append(record['visual_extraction'])
 record['visual_extraction']=extraction
 record['validation'].update(state='needs_review',method=None,actor=None,validated_at=None,visual_graph_checked=False,reason='visual_graph_requires_pixel_review')
 record['pending_requirements']=['visual_labels_and_geometry_review','visual_arrows_and_branches_review']
 record.update(normalized_record(record));atomic_json(directory/'technical_evidence_ledger.json',ledger)
 return extraction

def validate_graph(directory, entry_id, expected_graph_hash, expected_image_hash, actor):
 if not isinstance(actor,str) or not actor.strip() or len(actor)>100: raise ValueError('Reviewer name is required')
 ledger,record=current_entry(directory,entry_id)
 extraction=record.get('visual_extraction') or {}
 graph=normalize_graph(extraction.get('graph'))
 if extraction.get('graph_sha256') != expected_graph_hash or graph_hash(graph)!=expected_graph_hash or extraction.get('image_sha256')!=expected_image_hash: raise ValueError('Extraction changed; reload review')
 if record['validation']['state'] in {'rejected','validated'}: raise ValueError('Evidence is already reviewed')
 if not graph['nodes'] or graph['unresolved'] or any(e['direction']=='unknown' for e in graph['edges']): raise ValueError('Unresolved visual details must be repaired before validation')
 if len([ref for ref in record.get('doc_items',[]) if str(ref).startswith('#/pictures/')]) != 1: raise ValueError('Multi-picture chunks require separate per-image validation')
 if record.get('structured_records') or record.get('relationships'): raise ValueError('This mixed entry also requires separate structured-field validation')
 record['validation'].update(state='validated',method='human',actor=actor.strip(),validated_at=time.time(),provenance_checked=True,relationships_checked=True,source_sha256=record['source_sha256'],visual_graph_checked=True,visual_graph_sha256=expected_graph_hash,visual_image_sha256=expected_image_hash)
 extraction.update(state='validated',reviewer=actor.strip(),reviewed_at_epoch=record['validation']['validated_at'])
 record['pending_requirements']=[]
 record.update(normalized_record(record));atomic_json(directory/'technical_evidence_ledger.json',ledger)
 return record

from functools import lru_cache
import zipfile
from pathlib import Path
from .archive import select_docling_document

@lru_cache(maxsize=128)
def _picture_hash(path, mtime, size, index):
 with zipfile.ZipFile(path) as archive:
  document, _ = select_docling_document(archive)
  picture=document['pictures'][index]
  uri=Path(str((picture.get('image') or {}).get('uri') or '')).as_posix()
  return hashlib.sha256(archive.read(uri)).hexdigest()

def current_visual_image(directory, output_dir, extraction):
 if output_dir is None: return False
 try:
  manifest=json.loads((directory/'source_manifest.json').read_text(encoding='utf-8'))
  name=Path(str(manifest.get('converted_zip') or '')).name
  if not name: return False
  archive=Path(output_dir)/name; stat=archive.stat()
  return _picture_hash(str(archive),stat.st_mtime_ns,stat.st_size,extraction['picture_index'])==extraction['image_sha256']
 except (OSError,ValueError,KeyError,IndexError,TypeError,zipfile.BadZipFile): return False

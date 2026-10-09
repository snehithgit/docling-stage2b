"""Verified evidence overlays. Raw chunks and equipment assignments stay authoritative."""
from __future__ import annotations
import hashlib
import json
from functools import lru_cache
from pathlib import Path
from .evidence_contract import normalize_ledger, is_validated_record, source_signature, read_source_rows

DERIVED_FIELDS = ('verified_search_text','verified_evidence_ids','verified_applicability','verified_evidence_fingerprint')

@lru_cache(maxsize=64)
def _ledger(path: str, mtime: int, size: int):
    return normalize_ledger(json.loads(Path(path).read_text(encoding='utf-8')))

def configured_output_dir():
    from .config import load_config
    try:
        return Path(load_config().output_dir)
    except (OSError, ValueError):
        return None

def record_text(record):
    lines, applicability = [], []
    for item in record.get('structured_records') or []:
        fields = item.get('fields') or {}
        if not isinstance(fields, dict) or any(not isinstance(v,str) for v in fields.values()):
            continue
        lines.append(str(item.get('kind') or 'Technical record') + ': ' + '; '.join(k.replace('_',' ') + ': ' + v for k,v in fields.items()))
        if str(fields.get('parameter') or '').strip().casefold() in {'model','model number','applicable models'} and fields.get('value'):
            applicability.append({'field':'model','value':fields['value'],'scope':'source_chunk_only','evidence_id':record['entry_id']})
    for relation in record.get('relationships') or []:
        fields = {k:relation[k] for k in ('symptom','cause','remedy') if isinstance(relation.get(k),str)}
        if len(fields)==3:
            lines.append('Troubleshooting: ' + '; '.join(k + ': ' + v for k,v in fields.items()))
    graph = (record.get('visual_extraction') or {}).get('graph') or {}
    labels={node['id']:node['text'] for node in graph.get('nodes',[])}
    lines.extend('Node ' + key + ': ' + text for key,text in labels.items())
    for edge in graph.get('edges',[]):
        lines.append('Connection: ' + labels[edge['source']] + ' [' + edge['direction'] + '; branch: ' + edge['label'] + '] ' + labels[edge['target']])
    return '\n'.join(lines), applicability

def verified_overlays(rows: list[dict], directory: Path):
    """Rebind validation on each load; only ledger parsing is cached."""
    output=[]
    try:
        path=directory/'technical_evidence_ledger.json';stat=path.stat()
        ledger=_ledger(str(path),stat.st_mtime_ns,stat.st_size)
        current=ledger.get('source_index_signature') == source_signature(read_source_rows(directory))
        records={r.get('source_chunk_id'):r for r in ledger['entries'] if not r.get('superseded')}
    except (OSError, ValueError, TypeError, KeyError):
        current=False; records={}
    output_dir=None
    for original in rows:
        row=dict(original)
        for key in DERIVED_FIELDS: row.pop(key,None)
        record=records.get(row.get('chunk_id')) if current else None
        same=bool(record and record.get('source_text')==row.get('text') and record.get('source_sha256')==hashlib.sha256(str(row.get('text') or '').encode()).hexdigest() and all(record.get(k)==row.get(k) for k in ('page_numbers','doc_items','postprocess_job_id','source_filename')))
        if same and is_validated_record(record):
            image_current=True
            if record.get('visual_extraction'):
                from .visual_graph import current_visual_image
                if output_dir is None: output_dir=configured_output_dir()
                image_current=current_visual_image(directory,output_dir,record['visual_extraction'])
            if image_current:
                try: text,applicability=record_text(record)
                except (TypeError,KeyError,ValueError): text='';applicability=[]
                if text:
                    row.update(verified_search_text=text,verified_evidence_ids=[record['entry_id']],verified_applicability=applicability,verified_evidence_fingerprint=hashlib.sha256(text.encode()).hexdigest())
        output.append(row)
    return output

def overlay_signature(rows):
    facts=[(row.get('chunk_id'),row['verified_evidence_fingerprint']) for row in rows if row.get('verified_evidence_fingerprint')]
    return hashlib.sha256(json.dumps(facts,separators=(',',':')).encode()).hexdigest() if facts else None

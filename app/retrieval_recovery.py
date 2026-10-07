"""Bounded query planning and same-scope candidate recovery, without inference."""
from __future__ import annotations
import re
from .retrieval import search_indices, _tokens, _PARTS_NUMBER_COLUMN_RE

VERSION = 'scoped-recovery/v1'
EXPANSIONS = {
 'troubleshooting': ('troubleshooting causes remedies', 'fault cause corrective action'),
 'alarm': ('alarm fault code meaning', 'alarm corrective action'),
 'procedure': ('procedure steps operating instructions', 'operation start stop instructions'),
 'parts': ('parts list article number description', 'spare part number'),
 'specification': ('technical data specifications rated value',),
 'safety': ('warning caution safety interlock',),
 'reference': ('section drawing cross reference',),
 'definition': ('description purpose function',),
}
STOP = set('why how what which where when does do did can could should would the a an of to for in on at is are be it this that my machine manual book please find tell me need information about reason cause causes remedy remedies troubleshooting fault alarm problem code error part number article spare parts operation procedure steps instructions specification specifications value rated safety warning caution section drawing reference description purpose function not working fix repair reset check'.split())
ALIASES = {'overheating':'overheat','overheated':'overheat','overheats':'overheat','hot':'overheat','temp':'temperature','cooling':'cool','failed':'failure','fails':'failure'}

def measurement_pattern(query):
 """Requested quantity family, never a guessed value or device relationship."""
 q=str(query).lower()
 if re.search(r'\b(?:resistance|resistor|ohms?)\b|[ΩΩ]',q):return r'\d+(?:[.,]\d+)?\s*(?:[kKmM]?\s*[ΩΩ]|(?:kilo|mega)?ohms?\b)'
 if re.search(r'\b(?:voltage|volts?)\b',q):return r'\d+(?:[.,]\d+)?\s*(?:[kKmM]?V\b|volts?\b)'
 if re.search(r'\b(?:current|amps?|amperes?)\b',q):return r'\d+(?:[.,]\d+)?\s*(?:[muµ]?A\b|amperes?\b|amps?\b)'
 return None

def matches_requested_measurement(text, query):
 pattern=measurement_pattern(query)
 return bool(pattern and re.search(pattern,str(text),re.I))

def question_plan(query):
 q=query.lower()
 if re.search(r'part\s*(?:no\.?|number)|article\s*(?:no\.?|number)|spare\s+part',q):intent='parts';roles=['identifier','description']
 elif re.search(r'\bwhy\b|reason|\bcause\b|not (?:working|starting|running|moving|operating)|won.t|does not|how to (?:fix|troubleshoot)|\bproblem\b',q):
  intent='troubleshooting';roles=['cause']
  if re.search(r'fix|repair|remedy|what to (?:do|check)|troubleshoot',q):roles.append('action')
 elif re.search(r'\balarm\b|fault code|error code',q):intent='alarm';roles=['meaning']
 elif re.search(r'\bhow (?:to|do)\b|\bprocedure\b|steps|\b(?:replace|install|adjust|calibrate|inspect)\b',q):intent='procedure';roles=['action']
 elif re.search(r'overheat|too hot|too (?:high|low)',q) and not re.search(r'\b(?:what|which)\b.*(?:temperature|pressure|value|limit|means?|meaning)',q):intent='troubleshooting';roles=['cause','action']
 elif re.search(r'\b(?:pressure|voltage|current|resistance|resistor|ohm|ohms|torque|rating|rated|limit|temperature|capacity|specification)\b',q):intent='specification';roles=['value']
 elif re.search(r'\b(?:safety|warning|caution|interlock)\b',q):intent='safety';roles=['constraint']
 elif re.search(r'\b(?:refer|reference|section|chapter|drawing)\b',q):intent='reference';roles=['reference']
 else:intent='definition';roles=['description']
 from .rag_generation import _literal_measurements
 values=sorted(_literal_measurements(query))
 if values:roles.append('requested_value')
 return {'requested_values':values,'version':VERSION,'intent':intent,'required_candidate_roles':roles,'original_query':query,'coverage_is_heuristic':True}

def topic_words(query):
 return {ALIASES.get(token,token) for token in _tokens(query) if token not in STOP}

def evidence_text(row):
 if row.get('source_kind')=='visual':
  return ' '.join(row.get('visible_text') or [])+' '+' '.join(row.get('validated_source_literals') or [])
 text=str(row.get('text') or '')+' '+' '.join(row.get('headings') or [])
 # These overlays are produced server-side by the verified-index loader.
 return text+' '+str(row.get('verified_search_text') or '')+' '+' '.join(row.get('validated_source_literals') or [])

def candidate_roles(row):
 text=evidence_text(row).lower();roles=set()
 if re.search(r'cause|due to|because|result(?:s|ed)? from',text):roles.add('cause')
 if row.get('technical_validation_status')=='needs_visual_parse' and not row.get('verified_search_text'):roles.discard('cause')
 if re.search(r'\b(?:check|inspect|replace|repair|clean|set|press|start|stop|reset|install|adjust|disconnect|tighten)\b',text):roles.add('action')
 if re.search(r'alarm|meaning|indicat|fault code|error code',text):roles.add('meaning')
 if (re.search(r'(?:part(?:\s*/\s*order)?|article|order)\s*(?:no\.?|number)|part_number',text) or _PARTS_NUMBER_COLUMN_RE.search(text)) and re.search(r'\d',text):roles.add('identifier')
 if len(_tokens(text))>=4:roles.add('description')
 if re.search(r'[-+]?\d+(?:[.,]\d+)?\s*(?:bar|pa|mpa|v|volt|a|amp|mm|cm|rpm|°c|deg|nm|n\.m|hz|kw|kg|ohms?|[km]?[ΩΩ]|m\b)',text):roles.add('value')
 if re.search(r'warning|caution|must not|do not|interlock',text):roles.add('constraint')
 if re.search(r'(?:see|refer|section|chapter|drawing)\s+[A-Za-z]*\d',text):roles.add('reference')
 return roles

def topical(row,query):
 topics=topic_words(query)
 if not topics:return False
 found=topic_words(evidence_text(row))
 return len(topics & found)>=min(2,len(topics))

def assess_candidates(rows,query):
 plan=question_plan(query);roles=set();found_values=set()
 from .rag_generation import _literal_measurements
 for row in rows:
  if topical(row,query):
   roles.update(candidate_roles(row));found_values.update(_literal_measurements(evidence_text(row)))
 missing_values=set(map(tuple,plan['requested_values']))-found_values
 if plan['requested_values'] and not missing_values:roles.add('requested_value')
 missing=[role for role in plan['required_candidate_roles'] if role not in roles]
 return {**plan,'missing_requested_values':sorted(missing_values),'candidate_roles_found':sorted(roles),'missing_candidate_roles':missing,'status':'needs_more_evidence' if missing else 'candidate_roles_located','answer_correctness_verified':False}

def protected_ids(query):
 values=re.findall(r'(?<![A-Za-z0-9])(?:[A-Za-z0-9]+(?:[-_./:][A-Za-z0-9]+)+|[A-Za-z]+\d+[A-Za-z0-9]*|\d{6,})(?![A-Za-z0-9])',query)
 return [v for v in values if (any(c.isalpha() for c in v) and any(c.isdigit() for c in v)) or v.isdigit()]

def _key(row):
 return (row.get('postprocess_job_id'),row.get('result_dir'),row.get('chunk_id'))

def recover_scoped_evidence(paths,query,primary,top_k):
 assessment=assess_candidates(primary,query);report={**assessment,'recovery_queries':[],'recovered_candidates':0,'recovery_mode':'scoped_lexical_expansion','model_calls':0}
 output=[{**r} for r in primary]
 if not assessment['missing_candidate_roles']:return output,report
 seen={_key(r) for r in output};recovered=[];identifiers=protected_ids(query)
 for suffix in EXPANSIONS[assessment['intent']][:2]:
  variant=query+' '+suffix;report['recovery_queries'].append(variant)
  for row in search_indices(paths,variant,top_k=12):
   if _key(row) in seen or not topical(row,query):continue
   text=evidence_text(row)
   if identifiers and not all(re.search(r'(?<![A-Za-z0-9_./:+-])'+re.escape(value)+r'(?![A-Za-z0-9_./:+-])',text,re.I) for value in identifiers):continue
   roles=candidate_roles(row)
   from .rag_generation import _literal_measurements
   if set(map(tuple,assessment['missing_requested_values'])) & _literal_measurements(text):roles.add('requested_value')
   if not set(assessment['missing_candidate_roles']) & roles:continue
   recovered.append({**row,'recovery_query':variant,'recovery_origin':VERSION});seen.add(_key(row))
   if len(recovered)>=6:break
  if len(recovered)>=6:break
 report['recovered_candidates']=len(recovered)
 report['post_recovery_coverage']=assess_candidates(output+recovered,query)
 if output:
  output[0]['recovery_candidates']=recovered
 else:
  output=recovered[:max(1,int(top_k))]
  for rank,row in enumerate(output,1):row['rank']=rank
 return output,report

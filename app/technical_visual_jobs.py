"""Persistent explicit diagram work; shared physical reservations stay authoritative."""
import asyncio
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager


class TechnicalVisualJobs:
    def __init__(self, database, execute):
        self.database = database
        self.execute = execute
        self.tasks = []

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.database, timeout=20)
        db.row_factory = sqlite3.Row
        db.execute('''CREATE TABLE IF NOT EXISTS technical_visual_jobs
          (id TEXT PRIMARY KEY, dedupe TEXT UNIQUE, payload TEXT NOT NULL,
           status TEXT NOT NULL, phase TEXT, worker TEXT, created REAL,
           updated REAL, retry_at REAL DEFAULT 0, attempts INTEGER DEFAULT 0,
           error TEXT, result TEXT)''')
        db.execute('CREATE TABLE IF NOT EXISTS technical_visual_queue_control (id INTEGER PRIMARY KEY, enabled INTEGER NOT NULL)')
        db.execute('INSERT OR IGNORE INTO technical_visual_queue_control VALUES(1,1)')
        db.commit()
        try:
            with db: yield db
        finally:
            db.close()

    def enqueue(self, payload, key):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            for active in db.execute("SELECT id,payload FROM technical_visual_jobs WHERE status IN ('queued','waiting','running')").fetchall():
                old=json.loads(active['payload'])
                if all(old.get(k)==payload.get(k) for k in ['book','entry','picture','kind']):return active['id']
            prior = db.execute('SELECT * FROM technical_visual_jobs WHERE dedupe=?', (key,)).fetchone()
            if prior:
                if prior['status'] in {'failed','cancelled'}:
                    db.execute("UPDATE technical_visual_jobs SET status='queued',phase='Waiting for an available worker',retry_at=0,error=NULL,updated=? WHERE id=?", (time.time(),prior['id']))
                return prior['id']
            identity = uuid.uuid4().hex
            now = time.time()
            db.execute("INSERT INTO technical_visual_jobs(id,dedupe,payload,status,phase,created,updated) VALUES(?,?,?,'queued','Waiting for an available worker',?,?)", (identity,key,json.dumps(payload),now,now))
            return identity

    def update(self, identity, **values):
        allowed = {'status','phase','worker','retry_at','error','result'}
        if set(values)-allowed:
            raise ValueError('Invalid queue update')
        values['updated'] = time.time()
        if isinstance(values.get('result'),dict): values['result']=json.dumps(values['result'])
        with self.connect() as db:
            db.execute('UPDATE technical_visual_jobs SET '+','.join(k+'=?' for k in values)+' WHERE id=?', (*values.values(),identity))

    def list(self, book, limit=100, view="all"):
        with self.connect() as db:
            rows=db.execute('SELECT * FROM technical_visual_jobs ORDER BY created DESC').fetchall()
        rows=[r for r in rows if book is None or json.loads(r['payload'])['book']==book]
        counts={state:sum(r['status']==state for r in rows) for state in ['queued','waiting','running','completed','failed','cancelled']}
        comparisons={r['id']:(json.loads(r['result']).get('comparison') or {}) if r['result'] else {} for r in rows}
        review_summary={state:sum(r['status']=='completed' and json.loads(r['payload']).get('kind')=='review' and comparisons[r['id']].get('status')==state for r in rows) for state in ['agreement','needs_attention']}
        if view=='attention': selected=[r for r in rows if r['status']=='failed' or comparisons[r['id']].get('status')=='needs_attention']
        elif view=='waiting': selected=[r for r in rows if r['status'] in {'queued','waiting','running'}]
        elif view=='saved': selected=[r for r in rows if r['status']=='completed']
        else:selected=rows
        selected=sorted(selected,key=lambda r:(r['status'] not in {'queued','waiting','running'},r['status']!='failed' and comparisons[r['id']].get('status')!='needs_attention',-r['created']))
        result=[]
        for r in selected[:limit]:
            item=dict(r);item.pop('dedupe');item['payload']=json.loads(item['payload'])
            item['result']=json.loads(item['result']) if item['result'] else None
            result.append(item)
        with self.connect() as db:
            enabled=bool(db.execute('SELECT enabled FROM technical_visual_queue_control WHERE id=1').fetchone()[0])
        return {'jobs':result,'counts':counts,'total':len(rows),'dispatch_enabled':enabled,'review_summary':review_summary,'matching':len(selected)}

    def set_enabled(self,enabled):
        with self.connect() as db:
            db.execute('UPDATE technical_visual_queue_control SET enabled=? WHERE id=1',(int(bool(enabled)),))

    def cancel(self, book):
        with self.connect() as db:
            for row in db.execute("SELECT id,payload FROM technical_visual_jobs WHERE status IN ('queued','waiting')").fetchall():
                if json.loads(row['payload'])['book']==book:
                    db.execute("UPDATE technical_visual_jobs SET status='cancelled',phase='Cancelled before dispatch',updated=? WHERE id=?",(time.time(),row['id']))

    def claim(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT enabled FROM technical_visual_queue_control WHERE id=1').fetchone()[0]:return None
            row=db.execute("SELECT * FROM technical_visual_jobs WHERE status IN ('queued','waiting') AND retry_at<=? ORDER BY created LIMIT 1",(time.time(),)).fetchone()
            if row:
                db.execute("UPDATE technical_visual_jobs SET status='running',phase='Selecting worker',worker=NULL,updated=?,attempts=attempts+1 WHERE id=?",(time.time(),row['id']))
            return dict(row) if row else None

    async def start(self):
        with self.connect() as db:
            db.execute("UPDATE technical_visual_jobs SET status='queued',worker=NULL,phase='Resuming after app restart',retry_at=0 WHERE status='running'")
        self.tasks=[asyncio.create_task(self.loop()) for _ in range(4)]

    async def stop(self):
        for task in self.tasks: task.cancel()
        await asyncio.gather(*self.tasks,return_exceptions=True)
        self.tasks=[]

    async def loop(self):
        while True:
            claim=asyncio.create_task(asyncio.to_thread(self.claim))
            try:
                row=await asyncio.shield(claim)
            except asyncio.CancelledError:
                row=await claim
                if row:await asyncio.to_thread(self.update,row['id'],status='queued',phase='Interrupted before dispatch')
                raise
            if not row:
                await asyncio.sleep(1)
                continue
            identity=row['id']
            async def progress(phase,worker=None):
                await asyncio.to_thread(self.update,identity,phase=phase,worker=worker)
            try:
                result=await self.execute(json.loads(row['payload']),progress)
                await asyncio.to_thread(self.update,identity,status='completed',phase='Saved',error=None,result=result)
            except asyncio.CancelledError:
                await asyncio.to_thread(self.update,identity,status='queued',worker=None,phase='Interrupted; waiting to resume')
                raise
            except RuntimeError as exc:
                message=str(exc)
                if any(word in message.lower() for word in ['busy','unavailable','available','ready']):
                    await asyncio.to_thread(self.update,identity,status='waiting',phase='Waiting for an available worker',worker=None,error=message,retry_at=time.time()+5)
                else:
                    await asyncio.to_thread(self.update,identity,status='failed',phase='Needs attention',error=message)
            except Exception as exc:
                await asyncio.to_thread(self.update,identity,status='failed',phase='Needs attention',error=str(exc))


def compare_graphs(old,new):
    """Compare literal labels/connectivity independent of model-assigned node IDs."""
    def labels(graph): return [n['text'] for n in graph['nodes']]
    def edges(graph):
        lookup={n['id']:n['text'] for n in graph['nodes']}
        result=set()
        for e in graph['edges']:
            a,b=lookup[e['source']],lookup[e['target']]
            if e['direction']=='undirected': a,b=sorted((a,b))
            result.add((a,b,e['label'],e['direction']))
        return result
    from collections import Counter
    removed=list((Counter(labels(old))-Counter(labels(new))).elements())
    added=list((Counter(labels(new))-Counter(labels(old))).elements())
    changed_geometry=[]
    for node in new['nodes']:
        matches=[n for n in old['nodes'] if n['text']==node['text']]
        if matches and min(max(abs(a-b) for a,b in zip(n['bbox'],node['bbox'])) for n in matches)>.05:
            changed_geometry.append(node['text'])
    removed_edges=sorted(edges(old)-edges(new));added_edges=sorted(edges(new)-edges(old))
    ambiguous=any(v>1 for v in Counter(labels(old)).values()) or any(v>1 for v in Counter(labels(new)).values())
    unclear=bool(old['unresolved'] or new['unresolved'] or ambiguous or not old['nodes'] or not new['nodes'] or any(e['direction']=='unknown' for g in [old,new] for e in g['edges']))
    return {'status':'agreement' if not any([removed,added,changed_geometry,removed_edges,added_edges,unclear]) else 'needs_attention',
      'added_labels':added,'removed_labels':removed,'changed_geometry':changed_geometry,
      'added_connections':added_edges,'removed_connections':removed_edges,
      'unresolved':new['unresolved'],'duplicate_labels_need_inspection':ambiguous,
      'human_confirmation_recorded':False,'semantic_accuracy_verified':False}

import json, sqlite3, time, uuid, threading, os, tempfile
from pathlib import Path
from contextlib import contextmanager
from .config import DATA

LOCK = threading.RLock()

def uid(): return uuid.uuid4().hex
def now(): return time.time()

@contextmanager
def db():
    conn=sqlite3.connect(DATA/'studio.sqlite', timeout=30)
    conn.row_factory=sqlite3.Row
    try: yield conn; conn.commit()
    except: conn.rollback(); raise
    finally: conn.close()

def initialize():
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.execute('CREATE TABLE IF NOT EXISTS entities (kind TEXT,id TEXT PRIMARY KEY,data TEXT,updated REAL)')
        c.execute('CREATE INDEX IF NOT EXISTS kind_updated ON entities(kind,updated)')
        c.execute('CREATE TABLE IF NOT EXISTS requests (scope TEXT,token TEXT,result_id TEXT,PRIMARY KEY(scope,token))')
        c.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT,task_id TEXT,data TEXT)')
        c.execute("CREATE INDEX IF NOT EXISTS entity_project_updated ON entities(kind,json_extract(data,'$.project_id'),updated)")

def put(kind, obj):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT data FROM entities WHERE id=? AND kind=?',(obj['id'],kind)).fetchone()
        revision=json.loads(row['data']).get('revision',0) if row else 0
        obj={**obj,'updated_at':now(),'revision':revision+1}
        c.execute('INSERT OR REPLACE INTO entities VALUES(?,?,?,?)',(kind,obj['id'],json.dumps(obj,ensure_ascii=False,allow_nan=False),obj['updated_at']))
    return obj

def get(kind,id):
    with db() as c: row=c.execute('SELECT data FROM entities WHERE id=? AND kind=?',(id,kind)).fetchone()
    if not row: raise KeyError(f'{kind} {id} 不存在')
    return json.loads(row['data'])

class RevisionConflict(ValueError):
    """The caller edited an older snapshot; the current record is preserved."""


def mutate(kind,id,transform,expected_revision=None,create=None):
    # The read and write share a SQLite write transaction across processes.
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT data FROM entities WHERE id=? AND kind=?',(id,kind)).fetchone()
        if not row and create is None:raise KeyError(f'{kind} {id} 不存在')
        value=json.loads(row['data']) if row else {**create,'id':id,'revision':0}
        revision=value.get('revision',0)
        if expected_revision is not None and expected_revision!=revision:
            raise RevisionConflict('配置已在另一窗口更新，请重新读取后合并修改')
        updated=transform(value)
        if updated is None:return value
        updated={**updated,'id':id,'updated_at':now(),'revision':revision+1}
        c.execute('INSERT OR REPLACE INTO entities VALUES(?,?,?,?)',(kind,id,json.dumps(updated,ensure_ascii=False,allow_nan=False),updated['updated_at']))
        return updated


def update(kind,id,**patch):
    return mutate(kind,id,lambda value:{**value,**patch})


def mutate_many(records,transform):
    """Atomically edit related entities (draft/project or export/publication)."""
    with db() as c:
        c.execute('BEGIN IMMEDIATE');values={}
        for (kind,id),initial in records.items():
            row=c.execute('SELECT data FROM entities WHERE kind=? AND id=?',(kind,id)).fetchone()
            if not row and initial is None:raise KeyError(f'{kind} {id} 不存在')
            values[kind,id]=json.loads(row['data']) if row else {**initial,'id':id,'revision':0}
        updates=transform(values);result={}
        for (kind,id),value in updates.items():
            if (kind,id) not in values:raise ValueError('Transaction attempted an undeclared entity')
            value={**value,'id':id,'revision':values[kind,id].get('revision',0)+1,'updated_at':now()}
            c.execute('INSERT OR REPLACE INTO entities VALUES(?,?,?,?)',(kind,id,json.dumps(value,ensure_ascii=False,allow_nan=False),value['updated_at']))
            result[kind,id]=value
        return result

def listing(kind,limit=100,*,offset=0,project_id=None,model_id=None,search=None,filters=None):
    clauses=['kind=?'];params=[kind]
    if project_id is not None:
        key='origin_project_id' if kind=='analysis_run' else 'project_id'
        clauses.append("json_extract(data,'$."+key+"')=?");params.append(project_id)
    if model_id is not None:clauses.append("json_extract(data,'$.model_id')=?");params.append(model_id)
    if search:
        clauses.append("(coalesce(json_extract(data,'$.name'),json_extract(data,'$.title'),'') LIKE ? OR id LIKE ?)")
        params.extend(['%'+search+'%']*2)
    for key,value in (filters or {}).items():
        if key not in {'status','example_family','origin_project_id','project_id','model_id'}:raise ValueError('Unsupported record filter')
        clauses.append("json_extract(data,'$."+key+"')=?");params.append(value)
    params.extend([max(1,int(limit)),max(0,int(offset))])
    with db() as c: rows=c.execute('SELECT data FROM entities WHERE '+' AND '.join(clauses)+' ORDER BY updated DESC,id DESC LIMIT ? OFFSET ?',params).fetchall()
    return [json.loads(r['data']) for r in rows]

def request_lookup(scope,token):
    with db() as c: r=c.execute('SELECT result_id FROM requests WHERE scope=? AND token=?',(scope,token)).fetchone()
    return r[0] if r else None

def request_save(scope,token,id):
    with db() as c: c.execute('INSERT INTO requests VALUES(?,?,?)',(scope,token,id))

def event(id,stage,text,details=None,status='completed'):
    e={'time':now(),'stage':stage,'text':text,'details':details,'status':status}
    with db() as c: cur=c.execute('INSERT INTO events(task_id,data) VALUES(?,?)',(id,json.dumps(e,ensure_ascii=False,allow_nan=False))); e['seq']=cur.lastrowid
    return e

def events(id,after=0):
    with db() as c: rows=c.execute('SELECT seq,data FROM events WHERE task_id=? AND seq>? ORDER BY seq',(id,after)).fetchall()
    return [{**json.loads(r['data']),'seq':r['seq']} for r in rows]

def write_json(path,value):
    path=Path(path)
    payload=json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=None
    try:
        # Each writer owns its staging file; replacing the destination remains atomic.
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,
                prefix='.'+path.name+'.',suffix='.tmp',delete=False) as stream:
            tmp=Path(stream.name)
            stream.write(payload)
        for attempt in range(24):
            try:
                tmp.replace(path)
                return
            except OSError as exc:
                # Windows readers or scanners may briefly deny replacement even
                # though directory permissions are valid. Never remove the old file.
                if os.name!='nt' or getattr(exc,'winerror',None) not in (5,32,33) or attempt==23:
                    raise
                time.sleep(min(.01*2**attempt,.25))
    finally:
        if tmp is not None:
            try:tmp.unlink(missing_ok=True)
            except OSError:pass  # Do not hide the original write/replace error.

initialize()

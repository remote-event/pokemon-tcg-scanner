"""Desktop-authoritative collections, reusing the scan journal and image cache."""
from datetime import datetime, timezone
import hashlib
import json
from time import time
from tempfile import TemporaryFile
from zipfile import ZipFile, ZIP_DEFLATED
from uuid import uuid4

from .history import History, ID


class Conflict(ValueError):
    pass


class CollectionStore(History):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.db.execute("SELECT 1 FROM settings WHERE key='collections_version'").fetchone():
            with self.db:
                self.db.execute('BEGIN IMMEDIATE')
                self.db.execute('CREATE TABLE IF NOT EXISTS collections(id TEXT PRIMARY KEY,name TEXT NOT NULL,created INTEGER NOT NULL)')
                self.db.execute('CREATE TABLE IF NOT EXISTS deleted_scans(id TEXT PRIMARY KEY)')
                self.db.execute('ALTER TABLE scans ADD COLUMN collection_id TEXT REFERENCES collections(id)')
                self.db.execute("ALTER TABLE scans ADD COLUMN matches TEXT NOT NULL DEFAULT '[]'")
                self.db.execute('CREATE INDEX scans_collection ON scans(collection_id,time DESC,id DESC)')
                if self.db.execute('SELECT count(*) FROM scans').fetchone()[0]:
                    self._ensure_imported()
                    self.db.execute("UPDATE scans SET collection_id='imported'")
                self._remaining_copies([dict(card=json.loads(r['card']),quantity=r['quantity']) for r in self.db.execute('SELECT * FROM collection')])
                self.db.execute('DELETE FROM collection')  # No second quantity system after migration.
                self.db.execute("INSERT INTO settings VALUES('collections_version','1')")
                self.db.execute("INSERT OR REPLACE INTO settings VALUES('active_collection','')")

    def _ensure_imported(self):
        self.db.execute("INSERT OR IGNORE INTO collections VALUES('imported','Imported collection',?)", (int(time()*1000),))
        return 'imported'

    def _remaining_copies(self, entries):
        """Preserve owned copies lacking a scan, once each; never fabricate a photo."""
        for item in entries:
            card, quantity = item['card'], item['quantity']
            if not isinstance(quantity, int) or isinstance(quantity, bool) or not 0 <= quantity <= 100000:
                raise ValueError('Invalid legacy quantity')
            self._validate_card(card)
            count = self.db.execute("SELECT count(*) FROM scans WHERE json_extract(card,'$.id')=?", (card['id'],)).fetchone()[0]
            for _ in range(max(0, quantity-count)):
                self._insert('imported-copy-'+uuid4().hex, int(time()*1000), card, self._ensure_imported(), 'identified')

    @staticmethod
    def _validate_card(card):
        if not isinstance(card, dict) or any(not isinstance(card.get(k), str) or len(card[k]) > 2048 for k in ('id','name','set','number','image_url')):
            raise ValueError('Invalid card')

    def _insert(self, scan_id, timestamp, card, collection, verification, matches=None):
        url, key = self._reference(card)
        self.db.execute('INSERT INTO scans(id,time,card,verification,reference_url,reference_key,revision,collection_id,matches) VALUES(?,?,?,?,?,?,?,?,?)',
                        (scan_id,timestamp,json.dumps(card),verification,url,key,self._next(),collection,json.dumps(matches or [dict(card=card,score=0)])))

    def active(self):
        with self.lock:
            row = self.db.execute("SELECT c.id FROM collections c JOIN settings s ON c.id=s.value WHERE s.key='active_collection'").fetchone()
            return row[0] if row else None

    def _wire(self, row):
        result = super()._wire(row)
        if result is not None and 'collection_id' in row.keys():
            result.update(collection_id=row['collection_id'], matches=json.loads(row['matches']))
        return result

    def save(self, result, crop):
        with self.lock:
            scan_id=result['recognition_id']
            if self.db.execute('SELECT 1 FROM deleted_scans WHERE id=?',(scan_id,)).fetchone() or self.get(scan_id):
                return
            collection=self.active()
            if not collection:
                raise ValueError('Choose a collection before scanning')
            if not ID.fullmatch(scan_id) or not crop:
                raise ValueError('Invalid scan or missing crop')
            original,thumb=self._store_crop(crop)
            with self.db:
                self._insert(scan_id,int(time()*1000),result['matches'][0]['card'],collection,result.get('verification','identified'),result['matches'])
                self.db.execute("UPDATE scans SET scanned=?,thumbnail=?,image_kind='yolo_crop' WHERE id=?",(original,thumb,scan_id))
        self.changed()

    def view(self, collection_id='', before='', query='', limit=40):
        limit=max(1,min(limit,100))
        with self.lock:
            collections=[dict(r) for r in self.db.execute('SELECT c.*,count(s.id) AS count,max(s.time) AS last_scan FROM collections c LEFT JOIN scans s ON s.collection_id=c.id GROUP BY c.id ORDER BY c.created DESC,c.id')]
            selected=next((c for c in collections if c['id']==collection_id),None)
            rows=[];next_page=None;total=0
            if selected:
                where='collection_id=? AND (instr(lower(json_extract(card,\'$.name\')),lower(?))>0 OR instr(lower(json_extract(card,\'$.set\')),lower(?))>0 OR instr(lower(json_extract(card,\'$.number\')),lower(?))>0)'
                args=[collection_id,query[:200],query[:200],query[:200]]
                total=self.db.execute('SELECT count(*) FROM scans WHERE '+where,args).fetchone()[0]
                if before:
                    stamp,scan_id=before.split(':',1)
                    if not ID.fullmatch(scan_id):raise ValueError('Invalid cursor')
                    where+=' AND (time,id)<(?,?)';args += [int(stamp),scan_id]
                found=self.db.execute('SELECT * FROM scans WHERE '+where+' ORDER BY time DESC,id DESC LIMIT ?',[*args,limit+1]).fetchall()
                rows=[self._wire(r) for r in found[:limit]]
                if len(found)>limit: next_page=f"{found[limit-1]['time']}:{found[limit-1]['id']}"
            return dict(collections=collections,active_collection=self.active(),collection=selected,rows=rows,next=next_page,total=total,revision=self.revision())

    def export_zip(self, collection_id):
        """Return a seekable ZIP snapshot; the caller owns and closes the file."""
        archive = TemporaryFile()
        try:
            # Keep edits/deletions from changing metadata or removing photos mid-export.
            with self.lock:
                self._collection(collection_id)
                collection = dict(self.db.execute(
                    'SELECT * FROM collections WHERE id=?', (collection_id,)).fetchone())
                entries = []
                included = set()
                with ZipFile(archive, 'w') as bundle:
                    bundle.writestr('images/', b'')
                    for row in self.db.execute(
                            'SELECT * FROM scans WHERE collection_id=? ORDER BY time DESC,id DESC',
                            (collection_id,)):
                        card = json.loads(row['card'])
                        image_path = None
                        if row['scanned']:
                            try:
                                source = self.media(row['scanned'])
                            except FileNotFoundError:
                                pass
                            else:
                                image_path = 'images/' + source.name
                                if image_path not in included:
                                    bundle.write(source, image_path)
                                    included.add(image_path)
                        entries.append(dict(
                            id=row['id'], name=card['name'], set=card['set'],
                            number=card['number'], image_path=image_path,
                            card_id=card['id'], set_code=card.get('code'),
                            set_total=card.get('total'), scanned_at=row['time'],
                            verification=row['verification']))
                    manifest = dict(
                        format='tcg-scanner-collection', version=1,
                        exported_at=datetime.now(timezone.utc).isoformat(),
                        collection=collection, count=len(entries),
                        missing_image_count=sum(e['image_path'] is None for e in entries),
                        cards=entries)
                    bundle.writestr('collection.json',
                                    json.dumps(manifest, ensure_ascii=False, indent=2) + '\n',
                                    compress_type=ZIP_DEFLATED)
            archive.seek(0)
            return archive
        except BaseException:
            archive.close()
            raise

    def _collection(self, id):
        if not isinstance(id,str) or not self.db.execute('SELECT 1 FROM collections WHERE id=?',(id,)).fetchone():
            raise ValueError('Collection no longer exists. Refresh and choose another.')
        return id

    @staticmethod
    def _name(value):
        if not isinstance(value,str) or not 1<=len(value.strip())<=100:
            raise ValueError('Enter a collection name of 1–100 characters')
        return value.strip()

    def _scan_rows(self, value):
        ids=value.get('ids',[])
        if not isinstance(ids,list) or not 1<=len(ids)<=1000 or any(not isinstance(i,str) for i in ids) or len(set(ids))!=len(ids):
            raise ValueError('Select 1–1,000 scans')
        rows=[]
        for id in ids:
            row=self.db.execute('SELECT * FROM scans WHERE id=?',(id,)).fetchone()
            if not row: raise Conflict('A selected scan was deleted. Refresh and try again.')
            expected=value.get('versions',{}).get(id)
            if expected is not None and expected!=row['revision']:
                raise Conflict('A selected scan changed. Refresh and try again.')
            rows.append(row)
        return rows

    def _delete(self, rows):
        candidates=set()
        for row in rows:
            candidates.update(p for p in (row['scanned'],row['thumbnail']) if p)
            self.db.execute('INSERT OR IGNORE INTO deleted_scans VALUES(?)',(row['id'],))
            self.db.execute('DELETE FROM scans WHERE id=?',(row['id'],))
        return candidates

    def action(self, value):
        if not isinstance(value,dict):raise ValueError('Invalid action')
        action=value.get('action');files=set();result={}
        with self.lock:
            with self.db:
                if action=='create':
                    name=self._name(value.get('name'));id=value.get('id',uuid4().hex)
                    if not isinstance(id,str) or not ID.fullmatch(id):raise ValueError('Invalid collection ID')
                    existing=self.db.execute('SELECT name FROM collections WHERE id=?',(id,)).fetchone()
                    if existing and existing[0]!=name:raise Conflict('Collection ID already exists')
                    self.db.execute('INSERT OR IGNORE INTO collections VALUES(?,?,?)',(id,name,int(time()*1000)))
                    result['id']=id
                elif action in ('rename','select','delete_collection'):
                    id=self._collection(value.get('id'))
                    if action=='rename':self.db.execute('UPDATE collections SET name=? WHERE id=?',(self._name(value.get('name')),id))
                    elif action=='select':self.db.execute("INSERT OR REPLACE INTO settings VALUES('active_collection',?)",(id,))
                    else:
                        files=self._delete(self.db.execute('SELECT * FROM scans WHERE collection_id=?',(id,)).fetchall())
                        self.db.execute('DELETE FROM collections WHERE id=?',(id,))
                        self.db.execute("UPDATE settings SET value='' WHERE key='active_collection' AND value=?",(id,))
                elif action in ('delete_scans','move','edit'):
                    rows=self._scan_rows(value)
                    if action=='delete_scans':files=self._delete(rows)
                    elif action=='move':
                        target=self._collection(value.get('collection_id'))
                        for row in rows:self.db.execute('UPDATE scans SET collection_id=?,revision=? WHERE id=?',(target,self._next(),row['id']))
                    else:
                        if len(rows)!=1:raise ValueError('Edit one scan at a time')
                        row=rows[0];fields=value.get('fields',{});card=json.loads(row['card'])
                        for key,maximum in (('name',200),('set',200),('number',32),('code',32)):
                            field=fields.get(key,'')
                            if not isinstance(field,str) or len(field.strip())>maximum or key!='code' and not field.strip():raise ValueError('Enter a valid '+key)
                        total=fields.get('total')
                        if total is not None and (isinstance(total,bool) or not isinstance(total,int) or not 1<=total<=100000):raise ValueError('Set size must be a whole number from 1 to 100,000')
                        changed=dict(name=fields['name'].strip(),set=fields['set'].strip(),number=fields['number'].strip(),code=fields.get('code','').strip() or None,total=total)
                        matches=json.loads(row['matches'])
                        known=next((c for c in [card]+[m['card'] for m in matches] if all(c.get(k)==v for k,v in changed.items())),None)
                        card=known or dict(changed,id='manual-'+hashlib.sha256(json.dumps(changed,sort_keys=True).encode()).hexdigest()[:32],image_url='')
                        matches=([dict(card=card,score=0)]+[m for m in matches if m['card']['id']!=card['id']])[:10]
                        url,key=self._reference(card)
                        self.db.execute("UPDATE scans SET card=?,matches=?,verification='manual',reference_url=?,reference_key=?,revision=? WHERE id=?",(json.dumps(card),json.dumps(matches),url,key,self._next(),row['id']))
                        result['row']=self.get(row['id'])
                else:raise ValueError('Unknown collection action')
                result['revision']=self._next()
            # Delete only unreferenced crops after the transaction commits; identical photos are shared.
            for name in files:
                if not self.db.execute('SELECT 1 FROM scans WHERE scanned=? OR thumbnail=?',(name,name)).fetchone():
                    (self.root/name).unlink(missing_ok=True)
        self.changed()
        return result

    def migrate_phone(self, value):
        client=value.get('client','')
        if not isinstance(client,str) or not ID.fullmatch(client):raise ValueError('Invalid library ID')
        marker='phone-migration-'+client
        with self.lock:
            if self.db.execute('SELECT 1 FROM settings WHERE key=?',(marker,)).fetchone():return {'migrated':True}
            backup=value.get('backup',{})
            if backup.get('format')!='tcg-scanner-library' or backup.get('version') not in (1,2):raise ValueError('Invalid legacy backup')
            entries=backup.get('history',[]);owned=backup.get('collection',[]);batches=backup.get('batches',[])
            if not all(isinstance(x,list) and len(x)<=100000 for x in (entries,owned,batches)):raise ValueError('Legacy library too large')
            with self.db:
                mapped={}
                used={e.get('batch_id') for e in entries}
                for b in batches:
                    if b['id'] not in used:continue
                    id='session-'+hashlib.sha256((client+b['id']).encode()).hexdigest()[:32]
                    name='Imported '+datetime.fromtimestamp(b['started']/1000,timezone.utc).strftime('%Y-%m-%d %H:%M')
                    self.db.execute('INSERT OR IGNORE INTO collections VALUES(?,?,?)',(id,name,b['started']));mapped[b['id']]=id
                for e in entries:
                    self._validate_card(e['card'])
                    source=e['id'];id=source[7:] if source.startswith('remote:') else 'legacy-'+hashlib.sha256(source.encode()).hexdigest()
                    if not ID.fullmatch(id):raise ValueError('Invalid legacy scan')
                    if self.db.execute('SELECT 1 FROM deleted_scans WHERE id=?',(id,)).fetchone():continue
                    collection=mapped.get(e.get('batch_id')) or self._ensure_imported()
                    old=self.db.execute('SELECT * FROM scans WHERE id=?',(id,)).fetchone()
                    if old:
                        # Preserve newer web edits/moves and all original image paths.
                        if old['collection_id']=='imported':self.db.execute('UPDATE scans SET collection_id=?,matches=?,revision=? WHERE id=?',(collection,json.dumps(e.get('matches',[])),self._next(),id))
                    else:
                        placeholder=self.db.execute("SELECT id FROM scans WHERE id LIKE 'imported-copy-%' AND json_extract(card,'$.id')=? LIMIT 1",(e['card']['id'],)).fetchone()
                        if placeholder:self.db.execute('DELETE FROM scans WHERE id=?',(placeholder[0],))
                        self._insert(id,e['time'],e['card'],collection,e['verification'],e.get('matches'))
                self._remaining_copies(owned)
                self.db.execute('INSERT INTO settings VALUES(?,?)',(marker,'1'))
                self._next()
        self.changed()
        return {'migrated':True}

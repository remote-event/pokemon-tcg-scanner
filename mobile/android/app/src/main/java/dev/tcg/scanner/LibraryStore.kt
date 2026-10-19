package dev.tcg.scanner

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import org.json.JSONArray
import org.json.JSONObject
import java.io.InputStream
import java.util.UUID

internal fun cardJson(c:Card)=JSONObject().put("id",c.id).put("name",c.name).put("set",c.set)
    .put("number",c.number).put("code",c.code ?: JSONObject.NULL).put("total",c.total ?: JSONObject.NULL).put("image_url",c.imageUrl)
internal fun parseCard(o:JSONObject):Card {
    fun field(key:String)=o.getString(key).also { require(it.length<=4096) { "Card field too long" } }
    val id=field("id");require(id.isNotBlank())
    val total=if(o.isNull("total")) null else o.getInt("total").also { require(it in 0..100000) }
    return Card(-1,id,field("name"),field("set"),field("number"),if(o.isNull("code")) null else field("code"),total,field("image_url"))
}
internal fun matchesJson(matches:List<Match>)=JSONArray(matches.map { JSONObject().put("card",cardJson(it.card)).put("score",it.similarity) })
internal fun parseMatches(a:JSONArray):List<Match> {
    require(a.length() in 1..10)
    return (0 until a.length()).map { i -> val o=a.getJSONObject(i);val score=o.getDouble("score")
        require(score.isFinite() && score in -1.01..1.01)
        Match(parseCard(o.getJSONObject("card")),score.toFloat())
    }.also { require(it.map { m->m.card.id }.distinct().size==it.size) }
}
data class HistoryEntry(val id:String,val time:Long,val card:Card,val matches:List<Match>,val verification:String,
                        val batchId:String?,val added:Boolean,val undone:Boolean)
data class CollectionEntry(val card:Card,val quantity:Int)
data class BatchEntry(val id:String,val started:Long,val ended:Long?,val count:Int=0)
data class LibraryBackup(val history:List<HistoryEntry>,val collection:List<CollectionEntry>,val batches:List<BatchEntry>)

/** User data only. Bundled references and models are never modified here. */
class LibraryStore(context:Context,name:String="library.sqlite3"):SQLiteOpenHelper(context,name,null,1) {
    companion object {
        val changes=java.util.concurrent.atomic.AtomicLong()
        val restorations=java.util.concurrent.atomic.AtomicLong()
    }
    override fun onConfigure(db:SQLiteDatabase) { db.setForeignKeyConstraintsEnabled(true) }
    override fun onCreate(db:SQLiteDatabase) {
        db.execSQL("CREATE TABLE batches(id TEXT PRIMARY KEY, started INTEGER NOT NULL, ended INTEGER)")
        db.execSQL("CREATE UNIQUE INDEX one_active_batch ON batches((1)) WHERE ended IS NULL")
        db.execSQL("CREATE TABLE history(id TEXT PRIMARY KEY, time INTEGER NOT NULL, card TEXT NOT NULL, matches TEXT NOT NULL, verification TEXT NOT NULL, batch_id TEXT REFERENCES batches(id), added INTEGER NOT NULL CHECK(added IN (0,1)), undone INTEGER NOT NULL CHECK(undone IN (0,1)))")
        db.execSQL("CREATE INDEX history_time ON history(time DESC)")
        db.execSQL("CREATE INDEX history_batch ON history(batch_id)")
        db.execSQL("CREATE TABLE collection(card_id TEXT PRIMARY KEY, card TEXT NOT NULL, quantity INTEGER NOT NULL CHECK(quantity BETWEEN 0 AND 1000000))")
    }
    override fun onUpgrade(db:SQLiteDatabase,oldVersion:Int,newVersion:Int) { error("Unsupported library migration") }
    private fun <T> transaction(write:Boolean=true,action:(SQLiteDatabase)->T):T {
        val db=writableDatabase;db.beginTransaction()
        var committed=false
        try { val result=action(db);db.setTransactionSuccessful();committed=true;return result }
        finally { db.endTransaction();if(committed && write) changes.incrementAndGet() }
    }
    private fun history(c:Cursor)=HistoryEntry(c.getString(0),c.getLong(1),parseCard(JSONObject(c.getString(2))),parseMatches(JSONArray(c.getString(3))),c.getString(4),if(c.isNull(5)) null else c.getString(5),c.getInt(6)==1,c.getInt(7)==1)
    private fun entry(db:SQLiteDatabase,id:String):HistoryEntry?=db.rawQuery("SELECT * FROM history WHERE id=?",arrayOf(id)).use { if(it.moveToFirst()) history(it) else null }
    fun entry(id:String)=entry(readableDatabase,id)
    fun history(query:String="",batch:String?=null,offset:Int=0,limit:Int=100,reviewOnly:Boolean=false):List<HistoryEntry> {
        val args=mutableListOf(query);val filter=(if(batch!=null) " AND batch_id=?".also { args+=batch } else "") +
            if(reviewOnly) " AND verification='possible' AND undone=0" else ""
        return readableDatabase.rawQuery("SELECT * FROM history WHERE instr(lower(card),lower(?))>0$filter ORDER BY time DESC,id DESC LIMIT $limit OFFSET $offset",args.toTypedArray()).use { c-> buildList { while(c.moveToNext()) add(history(c)) } }
    }
    fun historyAfter(time:Long,id:String,limit:Int=50):List<HistoryEntry> = readableDatabase.rawQuery(
        "SELECT * FROM history WHERE time>? OR (time=? AND id>?) ORDER BY time,id LIMIT ?",arrayOf(time.toString(),time.toString(),id,limit.toString())
    ).use { c->buildList { while(c.moveToNext()) add(history(c)) } }
    fun collection(query:String="",offset:Int=0,limit:Int=100):List<CollectionEntry> = readableDatabase.rawQuery("SELECT card,quantity FROM collection WHERE quantity>0 AND instr(lower(card),lower(?))>0 ORDER BY card_id LIMIT $limit OFFSET $offset",arrayOf(query)).use { c->buildList { while(c.moveToNext()) add(CollectionEntry(parseCard(JSONObject(c.getString(0))),c.getInt(1))) } }
    fun collectionPayload():String=transaction(write=false) {
        val rows=collection(limit=100001);require(rows.size<=100000)
        JSONArray(rows.map { JSONObject().put("card",cardJson(it.card)).put("quantity",it.quantity) }).toString().also {
            require(it.toByteArray(Charsets.UTF_8).size<=LibraryFiles.MAX_BYTES) { "Collection exceeds sync limit" }
        }
    }
    private fun quantity(db:SQLiteDatabase,id:String)=db.rawQuery("SELECT quantity FROM collection WHERE card_id=?",arrayOf(id)).use { if(it.moveToFirst()) it.getInt(0) else 0 }
    fun quantity(id:String)=quantity(readableDatabase,id)
    private fun adjust(db:SQLiteDatabase,card:Card,delta:Int) {
        val next=quantity(db,card.id).toLong()+delta;require(next in 0..1000000) { "Quantity must be between 0 and 1,000,000" }
        db.insertWithOnConflict("collection",null,ContentValues().apply { put("card_id",card.id);put("card",cardJson(card).toString());put("quantity",next) },SQLiteDatabase.CONFLICT_REPLACE).also { check(it!=-1L) }
    }
    fun adjust(card:Card,delta:Int)=transaction { adjust(it,card,delta) }
    fun record(decision:Decision,batchId:String?=null,id:String=UUID.randomUUID().toString()):HistoryEntry=transaction { db->
        entry(db,id)?.let { return@transaction it } // Replayed desktop result: exactly one durable addition.
        val card=decision.matches.first().card
        val activeBatch=batchId?.takeIf { id->db.rawQuery("SELECT 1 FROM batches WHERE id=? AND ended IS NULL",arrayOf(id)).use { it.moveToFirst() } }
        val added=activeBatch!=null
        val time=db.rawQuery("SELECT coalesce(max(time),0) FROM history",null).use { it.moveToFirst();maxOf(System.currentTimeMillis(),it.getLong(0)+1) }
        val e=HistoryEntry(id,time,card,decision.matches,if(decision.status=="identified") "identified" else if(decision.verified) "verified" else "possible",activeBatch,added,false)
        insertHistory(db,e);if(added) adjust(db,card,1);e
    }
    private fun insertHistory(db:SQLiteDatabase,e:HistoryEntry) {
        db.insertOrThrow("history",null,ContentValues().apply { put("id",e.id);put("time",e.time);put("card",cardJson(e.card).toString());put("matches",matchesJson(e.matches).toString());put("verification",e.verification);put("batch_id",e.batchId);put("added",if(e.added) 1 else 0);put("undone",if(e.undone) 1 else 0) })
    }
    /** Add at most once per history entry; alternative selection is an explicit user action. */
    fun add(id:String,selectedId:String):Boolean=transaction { db->
        val e=requireNotNull(entry(db,id)) { "Scan no longer exists" };if(e.added) return@transaction false
        val card=e.matches.first { it.card.id==selectedId }.card
        val verification=if(e.card.id==card.id && e.verification in setOf("verified","identified")) e.verification else "manual"
        adjust(db,card,1)
        db.update("history",ContentValues().apply { put("card",cardJson(card).toString());put("verification",verification);put("added",1);put("undone",0) },"id=?",arrayOf(id));true
    }
    /** Correct one scan and transfer its existing copy, never insert another history row.
     * Human corrections and undos take precedence over a late OCR result. */
    fun refine(id:String,decision:Decision,expectedRestoration:Long=restorations.get()):HistoryEntry?=transaction { db->
        if(expectedRestoration!=restorations.get()) return@transaction null
        val e=entry(db,id) ?: return@transaction null
        if(e.verification!="possible" || e.undone || !decision.verified) return@transaction e
        replacePrinting(db,e,decision.matches.first().card,"verified",decision.matches)
    }
    fun resolve(id:String,selectedId:String):HistoryEntry=transaction { db->
        val e=requireNotNull(entry(db,id)) { "Scan no longer exists" }
        val card=e.matches.first { it.card.id==selectedId }.card
        replacePrinting(db,e,card,"manual",e.matches)
    }
    /** Edit one saved scan; retain its photo, timestamp, undo state, and copy count. */
    fun edit(id:String,fields:JSONObject):HistoryEntry=transaction { db->
        val e=requireNotNull(entry(db,id)) { "Scan no longer exists" }
        fun text(key:String,max:Int,required:Boolean=false):String {
            val value=fields.getString(key).trim()
            require(value.length<=max && (!required || value.isNotBlank())) { "Enter a valid $key" }
            return value
        }
        val name=text("name",200,true);val set=text("set",200,true);val number=text("number",32,true)
        val code=text("code",32).ifBlank { null }
        val total=if(fields.isNull("total")) null else fields.getDouble("total").let {
            require(it.isFinite() && it>=1 && it<=100000 && it==it.toInt().toDouble()) { "Enter a whole set size from 1 to 100,000" };it.toInt()
        }
        val known=e.matches.map { it.card }.plus(e.card).firstOrNull {
            it.name==name && it.set==set && it.number==number && it.code==code && it.total==total
        }
        // A freeform correction must not keep a different catalog card's price/reference.
        val identity=JSONArray(listOf(name.lowercase(java.util.Locale.ROOT),set.lowercase(java.util.Locale.ROOT),number,code,total)).toString()
        val card=known ?: Card(-1,"manual-"+UUID.nameUUIDFromBytes(identity.toByteArray(Charsets.UTF_8)),name,set,number,code,total,"")
        val matches=(listOf(Match(card,e.matches.first().similarity))+e.matches.filter { it.card.id!=card.id }).take(10)
        replacePrinting(db,e,card,"manual",matches)
    }
    private fun replacePrinting(db:SQLiteDatabase,e:HistoryEntry,card:Card,verification:String,matches:List<Match>):HistoryEntry {
        if(e.added && card.id!=e.card.id && quantity(db,e.card.id)>0) {
            adjust(db,e.card,-1);adjust(db,card,1)
        }
        if(e.added && card.id==e.card.id && quantity(db,card.id)>0) adjust(db,card,0)
        db.update("history",ContentValues().apply {
            put("card",cardJson(card).toString());put("matches",matchesJson(matches).toString());put("verification",verification)
        },"id=?",arrayOf(e.id))
        return e.copy(card=card,matches=matches,verification=verification)
    }
    fun reviewCount()=readableDatabase.rawQuery("SELECT count(*) FROM history WHERE verification='possible' AND undone=0",null).use { it.moveToFirst();it.getInt(0) }
    fun totals():Pair<Int,Long> =readableDatabase.rawQuery("SELECT count(*),coalesce(sum(quantity),0) FROM collection WHERE quantity>0",null).use { it.moveToFirst();it.getInt(0) to it.getLong(1) }
    fun setQuantity(card:Card,value:Int)=transaction { db->
        require(value in 0..1000000)
        adjust(db,card,value-quantity(db,card.id))
    }
    private fun undo(db:SQLiteDatabase,e:HistoryEntry):Boolean {
        if(!e.added) return false
        if(quantity(db,e.card.id)>0) adjust(db,e.card,-1)
        db.update("history",ContentValues().apply { put("added",0);put("undone",1) },"id=?",arrayOf(e.id));return true
    }
    fun undo(id:String)=transaction { db->entry(db,id)?.let { undo(db,it) } ?: false }
    fun undoLast(batch:String)=transaction { db->
        val e=db.rawQuery("SELECT * FROM history WHERE batch_id=? AND added=1 ORDER BY time DESC,id DESC LIMIT 1",arrayOf(batch)).use { if(it.moveToFirst()) history(it) else null }
        e?.let { undo(db,it) } ?: false
    }
    fun activeBatch():String?=readableDatabase.rawQuery("SELECT id FROM batches WHERE ended IS NULL",null).use { if(it.moveToFirst()) it.getString(0) else null }
    fun startBatch():String=transaction { db->
        db.rawQuery("SELECT id FROM batches WHERE ended IS NULL",null).use { if(it.moveToFirst()) return@transaction it.getString(0) }
        val id=UUID.randomUUID().toString();db.insertOrThrow("batches",null,ContentValues().apply { put("id",id);put("started",System.currentTimeMillis()) });id
    }
    fun finishBatch(id:String) { writableDatabase.execSQL("UPDATE batches SET ended=max(started,?) WHERE id=? AND ended IS NULL",arrayOf(System.currentTimeMillis(),id));changes.incrementAndGet() }
    fun batchCount(id:String)=readableDatabase.rawQuery("SELECT count(*) FROM history WHERE batch_id=? AND added=1",arrayOf(id)).use { it.moveToFirst();it.getInt(0) }
    fun batches(offset:Int=0,limit:Int=100):List<BatchEntry> =readableDatabase.rawQuery("SELECT b.id,b.started,b.ended,(SELECT count(*) FROM history h WHERE h.batch_id=b.id AND added=1) FROM batches b ORDER BY b.started DESC LIMIT $limit OFFSET $offset",null).use { c->buildList { while(c.moveToNext()) add(BatchEntry(c.getString(0),c.getLong(1),if(c.isNull(2)) null else c.getLong(2),c.getInt(3))) } }
    fun backup():String=transaction(write=false) { db->
        val payload=db.rawQuery("SELECT (SELECT coalesce(sum(length(cast(card AS BLOB))+length(cast(matches AS BLOB))),0) FROM history)+(SELECT coalesce(sum(length(cast(card AS BLOB))),0) FROM collection)",null).use { it.moveToFirst();it.getLong(0) }
        require(payload<=LibraryFiles.MAX_BYTES/2) { "Library is too large for a single backup" }
        LibraryFiles.encode(LibraryBackup(history(limit=100001),collection(limit=100001),batches(limit=100001)))
    }
    fun restore(backup:LibraryBackup)=transaction { db->
        // Validate again at the storage boundary; malformed input cannot partially erase data.
        LibraryFiles.validate(backup)
        restorations.incrementAndGet() // Invalidate OCR started before this replacement.
        db.delete("history",null,null);db.delete("collection",null,null);db.delete("batches",null,null)
        for(b in backup.batches) db.insertOrThrow("batches",null,ContentValues().apply { put("id",b.id);put("started",b.started);if(b.ended!=null) put("ended",b.ended) })
        for(e in backup.history) insertHistory(db,e)
        for(e in backup.collection) adjust(db,e.card,e.quantity)
    }
    fun csv():String=transaction(write=false) { LibraryFiles.csv(collection(limit=100001)) }
}

object LibraryFiles {
    const val MAX_BYTES=32*1024*1024
    private fun id(value:String) { require(value.length in 1..128 && value.isNotBlank()) }
    fun validate(b:LibraryBackup) {
        require(b.history.size<=100000 && b.collection.size<=100000 && b.batches.size<=100000) { "Library exceeds backup limit" }
        require(b.history.map { it.id }.distinct().size==b.history.size)
        require(b.collection.map { it.card.id }.distinct().size==b.collection.size)
        require(b.batches.map { it.id }.distinct().size==b.batches.size)
        require(b.batches.count { it.ended==null }<=1)
        val batchIds=b.batches.map { it.id }.toSet()
        for(batch in b.batches) { id(batch.id);require(batch.started>=0 && (batch.ended==null || batch.ended>=batch.started)) }
        for(e in b.collection) { parseCard(cardJson(e.card));require(e.quantity in 1..1000000) }
        for(e in b.history) {
            id(e.id);require(e.time>=0 && e.verification in setOf("verified","possible","manual","identified"))
            parseCard(cardJson(e.card));parseMatches(matchesJson(e.matches))
            require(e.matches.any { it.card.copy(ordinal=-1)==e.card.copy(ordinal=-1) } && (e.batchId==null || e.batchId in batchIds))
            require(!(e.added && e.undone))
        }
    }
    fun encode(b:LibraryBackup):String {
        validate(b)
        val result=JSONObject().put("format","tcg-scanner-library").put("version",2)
            .put("batches",JSONArray(b.batches.map { JSONObject().put("id",it.id).put("started",it.started).put("ended",it.ended ?: JSONObject.NULL) }))
            .put("collection",JSONArray(b.collection.map { JSONObject().put("card",cardJson(it.card)).put("quantity",it.quantity) }))
            .put("history",JSONArray(b.history.map { JSONObject().put("id",it.id).put("time",it.time).put("card",cardJson(it.card)).put("matches",matchesJson(it.matches)).put("verification",it.verification).put("batch_id",it.batchId ?: JSONObject.NULL).put("added",it.added).put("undone",it.undone) })).toString(2)
        require(result.toByteArray(Charsets.UTF_8).size<=MAX_BYTES) { "Backup exceeds 32 MiB limit" };return result
    }
    fun read(input:InputStream):LibraryBackup {
        val bytes=input.readBytesLimited(MAX_BYTES)
        return decode(bytes.toString(Charsets.UTF_8))
    }
    fun decode(text:String):LibraryBackup {
        require(text.toByteArray(Charsets.UTF_8).size<=MAX_BYTES)
        val o=JSONObject(text);require(o.getString("format")=="tcg-scanner-library" && o.getInt("version") in 1..2) { "Unsupported backup format" }
        fun <T> rows(key:String,read:(JSONObject)->T):List<T> { val a=o.getJSONArray(key);require(a.length()<=100000);return (0 until a.length()).map { read(a.getJSONObject(it)) } }
        return LibraryBackup(rows("history") { e->HistoryEntry(e.getString("id"),e.getLong("time"),parseCard(e.getJSONObject("card")),parseMatches(e.getJSONArray("matches")),e.getString("verification"),if(e.isNull("batch_id")) null else e.getString("batch_id"),e.getBoolean("added"),e.getBoolean("undone")) },
            rows("collection") { CollectionEntry(parseCard(it.getJSONObject("card")),it.getInt("quantity")) },
            rows("batches") { BatchEntry(it.getString("id"),it.getLong("started"),if(it.isNull("ended")) null else it.getLong("ended")) }).also { validate(it) }
    }
    private fun InputStream.readBytesLimited(limit:Int):ByteArray {
        val out=java.io.ByteArrayOutputStream();val buffer=ByteArray(8192)
        while(true) { val n=read(buffer);if(n<0) break;require(out.size()+n<=limit) { "Backup exceeds 32 MiB limit" };out.write(buffer,0,n) };return out.toByteArray()
    }
    fun csv(rows:List<CollectionEntry>):String {
        require(rows.size<=100000)
        // Quote every cell and neutralize spreadsheet formula prefixes in imported metadata.
        fun cell(s:String):String { val safe=if(s.trimStart().firstOrNull() in listOf('=','+','-','@','\t','\r','\n')) "'$s" else s;return "\""+safe.replace("\"","\"\"")+"\"" }
        return "card_id,name,set,number,quantity,reference_image_url\r\n"+rows.joinToString("") { e->listOf(e.card.id,e.card.name,e.card.set,e.card.number,e.quantity.toString(),e.card.imageUrl).joinToString(",") { cell(it) }+"\r\n" }
    }
}

package dev.tcg.scanner

import kotlinx.coroutines.flow.StateFlow
import org.json.JSONArray
import org.json.JSONObject

internal fun JSONArray.objects() = (0 until length()).map { getJSONObject(it) }
internal fun JSONObject.stringOrNull(key:String) = if(isNull(key)) null else optString(key).takeIf { it.isNotBlank() }

internal data class ScanEntry(val id:String,val time:Long,val card:Card,val collectionId:String,val revision:Long,
                              val scanMs:Long?=null,val processingMs:Double?=null) {
    fun retainTiming(row:JSONObject) {
        if(row.optString("id")!=id)return
        scanMs?.let { row.put("scan_ms",it) }
        processingMs?.let { row.put("processing_ms",it) }
    }
    companion object {
        fun parse(row:JSONObject)=ScanEntry(row.getString("id"),row.getLong("time"),
            parseCard(row.getJSONObject("card")),row.optString("collection_id"),row.optLong("revision"),
            row.optLong("scan_ms",-1).takeIf { it>=0 },
            row.optDouble("processing_ms").takeIf { it.isFinite() && it>=0 })
    }
}
internal data class ScanCollection(val id:String,val name:String,val count:Int)
internal data class CollectionView(val collections:List<ScanCollection>,val active:String?,val collection:ScanCollection?,
                                   val rows:List<ScanEntry>,val next:String?,val total:Int,val revision:Long) {
    companion object {
        private fun collection(o:JSONObject)=ScanCollection(o.getString("id"),o.getString("name"),o.optInt("count"))
        fun parse(o:JSONObject)=CollectionView(o.getJSONArray("collections").objects().map(::collection),
            o.stringOrNull("active_collection"),o.optJSONObject("collection")?.let(::collection),
            o.getJSONArray("rows").objects().map(ScanEntry::parse),o.stringOrNull("next"),o.optInt("total"),o.optLong("revision"))
    }
}
internal data class ScannerPreferences(val sound:Boolean=true,val vibration:Boolean=true,val information:Boolean=true,
                                      val threshold:Float=.5f,val serverIp:String="")
internal data class PendingScan(val id:String,val candidates:List<Card>) {
    companion object { fun parse(o:JSONObject)=PendingScan(o.getString("request_id"),o.getJSONArray("matches").objects().map { parseCard(it.getJSONObject("card")) }) }
}
internal data class ScannerState(val preferences:ScannerPreferences=ScannerPreferences(),val entry:ScanEntry?=null,
                                val notice:String="Connecting to desktop…",val hint:String="",val cameraNotice:String="",
                                val hasLight:Boolean=false,val server:String="",val catalog:CollectionView?=null,
                                val revision:Long=0,val pending:PendingScan?=null)

internal interface ScannerBackend {
    val state:StateFlow<ScannerState>
    suspend fun request(action:String,value:JSONObject=JSONObject()):JSONObject
}

internal data class CardDraft(val name:String,val set:String,val number:String,val code:String,val total:String) {
    fun validation():String? {
        if(name.isBlank() || set.isBlank() || number.isBlank())return "Enter the card name, set name, and card number."
        if(name.trim().length>200 || set.trim().length>200 || number.trim().length>32 || code.trim().length>32)return "One or more fields are too long."
        if(total.isNotBlank() && total.trim().toIntOrNull()?.let { it in 1..100000 }!=true)return "Set size must be a whole number from 1 to 100,000."
        return null
    }
    fun fields():JSONObject {
        require(validation()==null) { validation().orEmpty() }
        return JSONObject().put("name",name.trim()).put("set",set.trim()).put("number",number.trim())
            .put("code",code.trim()).put("total",total.trim().toIntOrNull() ?: JSONObject.NULL)
    }
}

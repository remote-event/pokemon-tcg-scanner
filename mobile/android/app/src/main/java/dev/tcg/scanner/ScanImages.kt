package dev.tcg.scanner

import android.content.Context
import android.net.Uri
import android.util.Log
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File
import java.security.MessageDigest
import java.util.concurrent.Executors

/** Desktop is the durable image store. Phone files are a bounded, offline-readable cache. */
internal class ScanImages(context:Context,private val connection:LanConnection) {
    private val images=Executors.newFixedThreadPool(2).asCoroutineDispatcher()
    private val stripes=Array(16) { Any() }
    private val manifests=File(context.filesDir,"scan-media").apply { mkdirs() }
    private val cache=File(context.cacheDir,"scan-images").apply { mkdirs() }
    private fun hash(value:String)=MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }
    private fun image(path:String?):String? {
        if(path.isNullOrBlank()) return null
        require(path.matches(Regex("/(media|reference)/[a-f0-9]{64}(-thumb)?\\.jpg")))
        val target=File(cache,hash(path)+".jpg")
        synchronized(stripes[(path.hashCode() and Int.MAX_VALUE)%stripes.size]) {
            if(!target.exists()) {
                val bytes=connection.historyRequest(path)
                val temporary=File(cache,target.name+".part")
                temporary.writeBytes(bytes)
                check(temporary.renameTo(target)) { "Could not cache scan image" }
            }
            target.setLastModified(System.currentTimeMillis())
        }
        return Uri.fromFile(target).toString()
    }
    suspend fun request(id:String,full:Boolean):JSONObject = withContext(images) {
        val key=id.removePrefix("remote:")
        require(key.matches(Regex("[a-zA-Z0-9_-]{1,80}"))) { "Invalid scan ID" }
        val manifest=File(manifests,key+".json")
        var row=runCatching { JSONObject(manifest.readText()) }.getOrNull()
        try {
            val fresh=JSONObject(String(connection.historyRequest("/api/history/$key")))
            manifest.writeText(fresh.toString());row=fresh
        } catch(e:Exception) { Log.d("TCGScanner","Using cached scan images: ${e.message}") }
        fun load(key:String):String? = runCatching { image(row?.optString(key)?.takeIf { it!="null" }) }.getOrNull()
        val scanned=load(if(full)"scanned_image" else "scanned_thumbnail")
        val reference=if(full)load("reference_image") else null
        JSONObject().put("scanned_uri",scanned ?: JSONObject.NULL).put("reference_uri",reference ?: JSONObject.NULL)
            .put("has_photo",row!=null && !row.isNull("scanned_image")).put("synced",row!=null).also { trim() }
    }
    suspend fun pricing(cardId:String?):JSONObject = withContext(images) {
        val path="/api/prices/"+(cardId?.let { Uri.encode(it) } ?: "collection")
        val cached=File(manifests,"price-"+hash(path)+".json")
        try {
            JSONObject(String(connection.historyRequest(path))).also { cached.writeText(it.toString()) }
        } catch(e:Exception) {
            JSONObject(cached.readText()).also {
                if(cardId==null)it.put("stale_copies",it.optLong("priced_copies")).put("pending",0)
                else it.put("stale",true).put("status",if(it.optJSONArray("quotes")?.length()?.let { n->n>0 }==true)"ready" else "unavailable")
            }
        }
    }
    @Synchronized private fun trim() {
        val files=cache.listFiles()?.filter { it.extension=="jpg" }?.sortedBy { it.lastModified() } ?: return
        var size=files.sumOf { it.length() }
        for(file in files) { if(size<=96L*1024*1024) break;val length=file.length();if(file.delete()) size-=length }
    }
    fun close() { images.close() }
}

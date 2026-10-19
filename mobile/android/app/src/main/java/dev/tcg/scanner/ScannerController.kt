package dev.tcg.scanner

import android.app.Application
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import org.json.JSONArray
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.Executors

/** Activity-scoped native state. Network/SQLite work never runs on the UI thread. */
internal class ScannerController(application:Application):AndroidViewModel(application),ScannerBackend {
    private val worker=Executors.newSingleThreadExecutor().asCoroutineDispatcher()
    private val main=Handler(Looper.getMainLooper())
    private val prefs=application.getSharedPreferences("scanner",0)
    private val feedback=ScanFeedback(application,prefs)
    @Volatile private var latest=prefs.getString("latest_id",null)?.removePrefix("remote:")
    private var alive=true
    private val mutable=MutableStateFlow(ScannerState(preferences=preferences(),
        entry=runCatching { ScanEntry.parse(JSONObject(prefs.getString("last_remote_entry","").orEmpty())) }.getOrNull()))
    override val state=mutable.asStateFlow()
    internal val connection=LanConnection(application,prefs,::hello,::remote) { notice->mutable.update { it.copy(notice=notice) } }
    private val scanImages=ScanImages(application,connection)
    private var polling:Job?=null
    internal var cameraFeedback:((JSONObject)->Unit)?=null

    init {
        if(!prefs.contains("desktop_session"))prefs.edit().putString("desktop_session",UUID.randomUUID().toString()).apply()
        if(!prefs.contains("collection_client_id"))prefs.edit().putString("collection_client_id",UUID.randomUUID().toString()).apply()
    }
    private fun preferences()=ScannerPreferences(prefs.getBoolean("sound",true),prefs.getBoolean("vibration",true),
        prefs.getBoolean("show_information",true),prefs.getFloat("detection_threshold",.5f),prefs.getString("server_ip","").orEmpty())
    internal fun cameraStatus(notice:String,hasLight:Boolean) { mutable.update { it.copy(cameraNotice=notice,hasLight=hasLight) } }
    private fun changed() { mutable.update { it.copy(revision=it.revision+1) } }
    private fun hello()=JSONObject().put("session",prefs.getString("desktop_session",null))
        .put("resume_aliases",JSONArray(prefs.getString("presentation_aliases","[]"))).put("resume_id",latest ?: "")
    private fun api(path:String,body:JSONObject?=null)=JSONObject(String(connection.historyRequest(path,body?.toString())))
    private fun migrate(instance:String) {
        if(prefs.getString("collections_migrated","")==instance)return
        val backup=LibraryStore(getApplication()).use { JSONObject(it.backup()) }
        api("/api/library/migrate",JSONObject().put("client",prefs.getString("collection_client_id","")).put("backup",backup))
        prefs.edit().putString("collections_migrated",instance).commit()
    }
    private fun remote(message:JSONObject) {
        if(!alive)return
        viewModelScope.launch(worker) {
            try {
                when(message.optString("type")) {
                    "ready"->{
                        require(message.optInt("collections_version")==1) { "Update the desktop server to use collections" }
                        migrate(message.getString("history_id"))
                        message.optJSONObject("result")?.let(::accept)
                        mutable.update { it.copy(pending=message.optJSONObject("pending")?.let(PendingScan::parse)) }
                        mutable.update { it.copy(server="${message.stringOrNull("gpu") ?: "CPU"} · ${message.optInt("references")} references") }
                        changed()
                    }
                    "result"->{
                        message.optJSONObject("camera")?.let { value->main.post { if(alive)cameraFeedback?.invoke(value) } }
                        when(message.optString("event")) {
                            "recognized"->accept(message)
                            "confirmation"->mutable.update { it.copy(pending=PendingScan.parse(message)) }
                            "removed"->{prefs.edit().putString("presentation_aliases","[]").apply();mutable.update { it.copy(pending=null) }}
                        }
                        mutable.update { it.copy(hint=message.optString("hint","")) }
                    }
                    "confirmation"->{
                        if(message.optString("event")=="recognized")accept(message)
                        mutable.update { it.copy(pending=null,hint="") }
                    }
                }
            } catch(e:CancellationException) { throw e }
            catch(e:Exception) {
                android.util.Log.w("TCGScanner","Desktop update deferred",e)
                mutable.update { it.copy(notice=e.message ?: "Desktop disconnected") }
            }
        }
    }
    private fun accept(result:JSONObject) {
        val id=result.getString("recognition_id")
        require(id.matches(Regex("[a-f0-9]{32}")))
        val entry=runCatching { api("/api/history/$id") }.getOrNull() ?: return
        val fresh=latest!=id
        state.value.entry?.retainTiming(entry)
        if(entry.isNull("processing_ms"))result.optDouble("processing_ms").takeIf { it.isFinite() && it>=0 }
            ?.let { entry.put("processing_ms",it) }
        // Include network, stabilization/retries and fetching the saved result. Replays keep their original timing.
        if(fresh && result.has("_scan_started_at_ms")) {
            val elapsed=SystemClock.elapsedRealtime()-result.getLong("_scan_started_at_ms")
            if(elapsed>=0)entry.put("scan_ms",elapsed)
        }
        latest=id
        val aliases=if(fresh)JSONArray() else JSONArray(prefs.getString("presentation_aliases","[]"))
        val cardId=result.getJSONArray("matches").getJSONObject(0).getJSONObject("card").getString("id")
        if((0 until aliases.length()).none { aliases.getString(it)==cardId })aliases.put(cardId)
        prefs.edit().putString("latest_id",id).putString("last_remote_entry",entry.toString()).putString("presentation_aliases",aliases.toString()).apply()
        mutable.update { it.copy(entry=ScanEntry.parse(entry),hint="",pending=null,revision=it.revision+1) }
        if(fresh)main.post { if(alive)feedback.play() }
    }
    override suspend fun request(action:String,value:JSONObject):JSONObject {
        if(action=="scanMedia")return scanImages.request(value.getString("id"),value.optBoolean("full"))
        if(action=="price")return scanImages.pricing(value.stringOrNull("id"))
        return withContext(worker) {
            when(action) {
                "preference"->{
                    val key=value.getString("key");val edit=prefs.edit()
                    when(key) {
                        "sound","vibration","show_information"->edit.putBoolean(key,value.getBoolean("value"))
                        "detection_threshold"->{val v=value.getDouble("value");require(v.isFinite() && v in .5.. .95);edit.putFloat(key,v.toFloat())}
                        "server_ip"->{val address=value.getString("value").trim();if(address.isNotEmpty())LanAddress.parse(address);edit.putString(key,address)}
                        else->error("Unknown setting")
                    }
                    edit.apply()
                    mutable.update { it.copy(preferences=preferences()) }
                    if(key=="server_ip" || key=="detection_threshold")connection.settingsChanged()
                    JSONObject()
                }
                "library"->api("/api/library?"+listOf("collection_id","before","query","limit").joinToString("&") {
                    it+"="+Uri.encode(value.optString(it,if(it=="limit")"40" else "")) })
                "collectionAction"->api("/api/library/action",value).also {
                    // Reflect the confirmed destination before the scanner resumes; polling follows later.
                    mutable.update { state->state.copy(revision=state.revision+1,
                        catalog=if(value.optString("action")=="select")state.catalog?.copy(active=value.getString("id")) else state.catalog) }
                }
                "confirm"->{
                    try {
                        api("/api/library/confirm",value).also { if(it.optString("event")=="recognized")accept(it);mutable.update { s->s.copy(pending=null,hint="") } }
                    } catch(e:Exception) {
                        if(e.message?.contains("expired")==true || e.message?.contains("no longer")==true)mutable.update { it.copy(pending=null) }
                        throw e
                    }
                }
                "detail"->{
                    val id=value.getString("id")
                    try {
                        api("/api/history/"+Uri.encode(id)).also { row->
                            if(id==latest) {
                                state.value.entry?.retainTiming(row)
                                prefs.edit().putString("last_remote_entry",row.toString()).apply()
                                mutable.update { it.copy(entry=ScanEntry.parse(row)) }
                            }
                        }
                    } catch(e:Exception) {
                        if(id==latest && e.message?.contains("Scan not saved")==true) {
                            latest=null;prefs.edit().remove("latest_id").remove("last_remote_entry").apply()
                            mutable.update { it.copy(entry=null) }
                        }
                        throw e
                    }
                }
                else->error("Unknown action")
            }
        }
    }
    fun start() {
        if(!alive)return
        connection.start()
        if(polling?.isActive==true)return
        polling=viewModelScope.launch {
            while(isActive) {
                try {
                    val catalog=CollectionView.parse(request("library"))
                    mutable.update { it.copy(catalog=catalog,revision=if(it.catalog?.revision!=catalog.revision)it.revision+1 else it.revision) }
                    state.value.entry?.let { request("detail",JSONObject().put("id",it.id)) }
                } catch(e:CancellationException) { throw e }
                catch(_:Exception) { /* Retain the last collection and result during reconnection. */ }
                delay(2000)
            }
        }
    }
    fun stop() { polling?.cancel();polling=null;connection.stop() }
    override fun onCleared() {
        alive=false;stop();cameraFeedback=null;feedback.close();scanImages.close();worker.close()
        super.onCleared()
    }
}

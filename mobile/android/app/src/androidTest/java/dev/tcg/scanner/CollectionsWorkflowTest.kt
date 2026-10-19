package dev.tcg.scanner

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.os.SystemClock
import android.view.View
import android.view.ViewGroup
import androidx.test.core.app.ActivityScenario
import androidx.lifecycle.Lifecycle
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.ByteArrayOutputStream
import java.util.UUID

/** Explicit opt-in. Uses temporary named collections only and deletes them in finally. */
@RunWith(AndroidJUnit4::class)
class CollectionsWorkflowTest {
    private val inst get()=InstrumentationRegistry.getInstrumentation()
    private fun views(v:View):List<View> = listOf(v)+(if(v is ViewGroup)(0 until v.childCount).flatMap{views(v.getChildAt(it))} else emptyList())
    private fun waitFor(label:String,check:()->Boolean){val until=SystemClock.elapsedRealtime()+45000;while(SystemClock.elapsedRealtime()<until){if(runCatching(check).getOrDefault(false))return;Thread.sleep(100)};fail("Timed out: $label")}
    private fun frame(name:String):ByteArray {
        val image=inst.context.assets.open(name).use{BitmapFactory.decodeStream(it)}
        val scale=1280f/maxOf(image.width,image.height)
        val small=Bitmap.createScaledBitmap(image,(image.width*scale).toInt(),(image.height*scale).toInt(),true)
        val cropped=CardDetector(inst.targetContext).use { requireNotNull(it.detect(small,.5f).crop) }
        return ByteArrayOutputStream().use{cropped.compress(Bitmap.CompressFormat.JPEG,78,it);cropped.recycle();if(small!==image)small.recycle();image.recycle();it.toByteArray()}
    }
    @Test fun sharedCollectionsOverPhoneWifi(){
        assumeTrue(InstrumentationRegistry.getArguments().getString("collections")=="true")
        lateinit var module:ScannerController
        val ids=mutableListOf<String>();var previous:String?=null
        val prefs=inst.targetContext.getSharedPreferences("scanner",0)
        val original=prefs.all.toMap()
        // Every run needs a fresh presentation; reconnect deduplication within the run is still tested.
        prefs.edit().putString("desktop_session",UUID.randomUUID().toString()).putString("presentation_aliases","[]")
            .remove("latest_id").remove("last_remote_entry").apply {
                InstrumentationRegistry.getArguments().getString("server")?.let { putString("server_ip",it) }
            }.commit()
        try { ActivityScenario.launch<MainActivity>(Intent(inst.targetContext,MainActivity::class.java)).use{scenario->
            lateinit var camera:CameraPreview
            waitFor("Native camera and connection") {
                var found=false
                scenario.onActivity{a->views(a.window.decorView).filterIsInstance<CameraPreview>().firstOrNull()?.let{found=true;camera=it;module=a.controller}}
                if(!found)return@waitFor false
                JSONObject(String(module.connection.historyRequest("/api/library"))).has("collections")
            }
            fun api(path:String,body:JSONObject?=null)=JSONObject(String(module.connection.historyRequest(path,body?.toString())))
            fun act(action:String,body:JSONObject=JSONObject())=api("/api/library/action",body.put("action",action))
            fun view(id:String)=api("/api/library?collection_id=$id")
            val initial=api("/api/library");previous=initial.optString("active_collection").takeUnless{it=="null"||it.isBlank()}
            try{
                val suffix=UUID.randomUUID().toString().take(8)
                val a=act("create",JSONObject().put("name","Phone validation $suffix")).getString("id");ids.add(a)
                val b=act("create",JSONObject().put("name","Move validation $suffix")).getString("id");ids.add(b)
                act("select",JSONObject().put("id",a))
                waitFor("Live CameraX frames") { camera.framesSent>=2 }
                scenario.moveToState(Lifecycle.State.CREATED)
                Thread.sleep(300)
                val backgroundFrames=camera.framesSent
                Thread.sleep(350)
                assertEquals("Camera must stop sending in the background",backgroundFrames,camera.framesSent)
                scenario.moveToState(Lifecycle.State.RESUMED)
                waitFor("Camera frames after foreground resume") { camera.framesSent>backgroundFrames }
                // Stop CameraX after validating real frames so fixture frames cannot race it.
                scenario.onActivity{camera.dispose()};Thread.sleep(500)
                fun send(body:ByteArray,count:Int){repeat(count){waitFor("Frame credit"){module.connection.reserveFrame()};module.connection.sendFrame(body);Thread.sleep(300)}}
                val first=frame("card-a.jpg");val second=frame("card-b.jpg")
                send(first,7);waitFor("First scan in selected collection"){view(a).getInt("total")==1}
                val row=view(a).getJSONArray("rows").getJSONObject(0)
                assertEquals("me02.5-211",row.getJSONObject("card").getString("id"))
                val crop=module.connection.historyRequest(row.getString("scanned_image"));val bitmap=BitmapFactory.decodeByteArray(crop,0,crop.size)
                assertEquals(630,bitmap.width);assertEquals(880,bitmap.height);bitmap.recycle()
                assertTrue(module.connection.historyRequest(row.getString("reference_image")).size>1000)
                send(second,6);waitFor("Replacement scan"){view(a).getInt("total")==2}
                // Reconnect through the same LAN transport, without toggling device Wi-Fi.
                inst.runOnMainSync{module.connection.stop();module.connection.start()}
                send(second,5);assertEquals(2,view(a).getInt("total"))
                // Re-presenting the first card creates a distinct entry, even for the same catalog ID.
                send(first,6);waitFor("Duplicate retained as separate scan"){view(a).getInt("total")==3}
                val latest=view(a).getJSONArray("rows").getJSONObject(0);val scanId=latest.getString("id")
                act("edit",JSONObject().put("ids",JSONArray(listOf(scanId))).put("fields",JSONObject().put("name","Corrected phone card").put("set","Corrected set").put("number","7").put("code","").put("total",JSONObject.NULL)))
                val edited=api("/api/history/$scanId");assertEquals(latest.getString("scanned_image"),edited.getString("scanned_image"));assertEquals("Corrected phone card",edited.getJSONObject("card").getString("name"))
                val moveIds=JSONArray(listOf(scanId,row.getString("id")))
                act("move",JSONObject().put("ids",moveIds).put("collection_id",b));assertEquals(1,view(a).getInt("total"));assertEquals(2,view(b).getInt("total"))
                act("delete_scans",JSONObject().put("ids",moveIds));assertEquals(0,view(b).getInt("total"))
                android.util.Log.i("TCG_TEST","PASS collections: CameraX frames, phone Wi-Fi JPEGs, GPU IDs, crop/reference, reconnect dedupe, duplicate entries, edit, bulk move/delete")
            }finally{
                scenario.onActivity{camera.scanning=false}
                for(id in ids)runCatching{act("delete_collection",JSONObject().put("id",id))}
                previous?.let{runCatching{act("select",JSONObject().put("id",it))}}
            }
        } } finally {
            val restore=prefs.edit().clear()
            original.forEach { (key,value)->
                when(value) {
                    is String->restore.putString(key,value)
                    is Boolean->restore.putBoolean(key,value)
                    is Float->restore.putFloat(key,value)
                    is Int->restore.putInt(key,value)
                    is Long->restore.putLong(key,value)
                    is Set<*>->restore.putStringSet(key,value.filterIsInstance<String>().toSet())
                }
            }
            restore.commit()
        }
    }
}

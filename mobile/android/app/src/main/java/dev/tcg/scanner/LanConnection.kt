package dev.tcg.scanner

import android.content.Context
import android.content.SharedPreferences
import android.net.*
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import okio.ByteString.Companion.toByteString
import org.json.JSONObject
import java.nio.ByteBuffer
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

internal object LanAddress {
    fun parse(input:String):Pair<String,Int> {
        val parts=input.trim().split(":")
        require(parts.size in 1..2) { "Enter a private IPv4 address, optionally followed by :8765" }
        val octets=parts[0].split(".").map { part->
            require(part.matches(Regex("[0-9]{1,3}"))) { "Use a numeric local IPv4 address" }
            part.toInt().also { require(it in 0..255) }
        }
        require(octets.size==4)
        require(octets[0]==10 || (octets[0]==172 && octets[1] in 16..31) || (octets[0]==192 && octets[1]==168)) { "Only local network addresses are allowed" }
        val port=if(parts.size==2) parts[1].toInt() else 8765
        require(port in 1..65535)
        return octets.joinToString(".") to port
    }
}

/** JPEG bytes stay native. At most one frame awaits a server response. */
internal class LanConnection(private val context:Context,private val prefs:SharedPreferences,
                             private val hello:()->JSONObject,private val message:(JSONObject)->Unit,
                             private val status:(String)->Unit) {
    private val main=Handler(Looper.getMainLooper())
    private val connectivity=context.getSystemService(ConnectivityManager::class.java)
    private val nsd=context.getSystemService(NsdManager::class.java)
    private var discovery:NsdManager.DiscoveryListener?=null
    private var multicast:WifiManager.MulticastLock?=null
    @Volatile private var http:OkHttpClient?=null
    @Volatile private var endpoint=""
    private var socket:WebSocket?=null
    private var network:Network?=null
    private var running=false
    private var retry=500L
    private var sequence=0L
    private var epoch=0
    private var resolved=""
    @Volatile private var ready=false
    @Volatile var nativeCrop=false
        private set
    @Volatile private var interval=250L
    @Volatile private var nextFrame=0L
    @Volatile private var sentAt=0L
    private val inFlight=AtomicBoolean(false)
    private val scanTiming=ScanTimingTracker()
    fun resetScanTiming() { main.post { scanTiming.reset() } }
    private val reconnect=Runnable { connect() }
    private val watchdog=object:Runnable {
        override fun run() {
            if(!running) return
            if(inFlight.get() && SystemClock.elapsedRealtime()-sentAt>8000) restart("Connection interrupted. Reconnecting…")
            main.postDelayed(this,1000)
        }
    }
    private val callback=object:ConnectivityManager.NetworkCallback() {
        override fun onAvailable(n:Network) { main.post { if(running) { network=n;restart("Connecting to desktop…") } } }
        override fun onLost(n:Network) { main.post { if(network==n) { network=null;restart("Connect to local Wi-Fi") } } }
    }
    fun start() {
        check(Looper.myLooper()==Looper.getMainLooper())
        if(running) return
        running=true
        network=connectivity.allNetworks.firstOrNull { connectivity.getNetworkCapabilities(it)?.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)==true }
        connectivity.registerNetworkCallback(NetworkRequest.Builder().addTransportType(NetworkCapabilities.TRANSPORT_WIFI).build(),callback)
        discover();connect();main.post(watchdog)
    }
    fun stop() {
        if(!running) return
        running=false;epoch++;ready=false;inFlight.set(false);scanTiming.reset()
        main.removeCallbacks(reconnect);main.removeCallbacks(watchdog)
        socket?.cancel();socket=null
        http?.dispatcher?.executorService?.shutdown();http?.connectionPool?.evictAll();http=null
        runCatching { connectivity.unregisterNetworkCallback(callback) }
        discovery?.let { runCatching { nsd.stopServiceDiscovery(it) } };discovery=null
        multicast?.let { if(it.isHeld) it.release() };multicast=null
    }
    fun settingsChanged() { main.post { if(running) { resolved="";restart("Connecting to desktop…") } } }
    private fun restart(label:String) {
        epoch++;ready=false;inFlight.set(false);scanTiming.reset();socket?.cancel();socket=null
        status(label);main.removeCallbacks(reconnect)
        if(running) main.postDelayed(reconnect,250)
    }
    private fun later(label:String) {
        ready=false;inFlight.set(false);scanTiming.reset();socket=null;status(label)
        if(running) { main.removeCallbacks(reconnect);main.postDelayed(reconnect,retry);retry=(retry*2).coerceAtMost(8000) }
    }
    private fun connect() {
        if(!running || socket!=null) return
        val wifi=network
        if(wifi==null) { later("Connect to local Wi-Fi");return }
        val manual=prefs.getString("server_ip","").orEmpty()
        val target=manual.ifBlank { resolved.ifBlank { prefs.getString("last_server","").orEmpty() } }
        if(target.isBlank()) { later("Looking for desktop… Set its IP in settings if needed.");return }
        val (host,port)=try { LanAddress.parse(target) } catch(e:Exception) { later("Enter a local desktop IP in settings");return }
        val own=++epoch
        http?.dispatcher?.executorService?.shutdown();http?.connectionPool?.evictAll()
        http=OkHttpClient.Builder().socketFactory(wifi.socketFactory).proxy(java.net.Proxy.NO_PROXY)
            .followRedirects(false).followSslRedirects(false).connectTimeout(3,TimeUnit.SECONDS)
            .readTimeout(0,TimeUnit.MILLISECONDS).pingInterval(10,TimeUnit.SECONDS).build()
        status("Connecting to desktop…")
        socket=http!!.newWebSocket(Request.Builder().url("ws://$host:$port/scan").build(),object:WebSocketListener() {
            override fun onOpen(ws:WebSocket,response:Response) { main.post {
                if(own!=epoch || !running) { ws.cancel();return@post }
                ws.send(hello().put("protocol",1).put("input_mode","card_crop_v1").put("threshold",prefs.getFloat("detection_threshold",.5f).toDouble()).toString())
                // Handshake timeout uses the same watchdog as outstanding frames.
                sentAt=SystemClock.elapsedRealtime();inFlight.set(true)
            } }
            override fun onMessage(ws:WebSocket,text:String) { main.post {
                if(own!=epoch || !running) return@post
                try {
                    require(text.length<128*1024)
                    val value=JSONObject(text)
                    when(value.getString("type")) {
                        "ready"->{
                            require(value.getInt("protocol")==1)
                            nativeCrop=value.optJSONArray("input_modes")?.let { modes->(0 until modes.length()).any { modes.optString(it)=="card_crop_v1" } } ?: false
                            endpoint="http://$host:$port"
                            ready=true;inFlight.set(false);retry=500;nextFrame=0
                            prefs.edit().putString("last_server","$host:$port").apply()
                            status("");Log.i("TCGScanner","Desktop connected $host:$port ${value.optString("gpu")}")
                        }
                        "result"->{
                            if(value.getLong("sequence")!=sequence) return@post
                            scanTiming.observe(value.optString("event"),value.optString("state"),sentAt)?.let {
                                value.put("_scan_started_at_ms",it)
                            }
                            interval=value.optLong("interval_ms",250).coerceIn(125,1000)
                            nextFrame=SystemClock.elapsedRealtime()+interval
                            inFlight.set(false)
                        }
                    }
                    message(value)
                } catch(e:Exception) { Log.w("TCGScanner","Invalid server response",e);restart("Desktop protocol error. Reconnecting…") }
            } }
            override fun onFailure(ws:WebSocket,t:Throwable,response:Response?) { main.post {
                if(own==epoch && running) { Log.i("TCGScanner","Disconnected: ${t.message}");later("Desktop unavailable. Reconnecting…") }
            } }
            override fun onClosed(ws:WebSocket,code:Int,reason:String) { main.post { if(own==epoch && running) later("Desktop disconnected. Reconnecting…") } }
            override fun onClosing(ws:WebSocket,code:Int,reason:String) { ws.close(code,null) }
        })
    }
    @Suppress("DEPRECATION")
    private fun discover() {
        multicast=(context.applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager)
            .createMulticastLock("tcg-discovery").apply { setReferenceCounted(false);acquire() }
        val listener=object:NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(type:String) {}
            override fun onDiscoveryStopped(type:String) {}
            override fun onStartDiscoveryFailed(type:String,code:Int) {
                Log.w("TCGScanner","Discovery failed $code; retrying")
                main.postDelayed({ if(running && discovery===this) {
                    runCatching { nsd.stopServiceDiscovery(this) };discovery=null
                    multicast?.let { if(it.isHeld) it.release() };multicast=null
                    discover()
                } },5000)
            }
            override fun onStopDiscoveryFailed(type:String,code:Int) {}
            override fun onServiceLost(info:NsdServiceInfo) {}
            override fun onServiceFound(info:NsdServiceInfo) {
                if(!info.serviceType.contains("_tcgscanner._tcp")) return
                nsd.resolveService(info,object:NsdManager.ResolveListener {
                    override fun onResolveFailed(info:NsdServiceInfo,code:Int) { Log.d("TCGScanner","Discovery resolve $code") }
                    override fun onServiceResolved(info:NsdServiceInfo) { main.post {
                        if(!running || prefs.getString("server_ip","").orEmpty().isNotBlank()) return@post
                        val address="${info.host?.hostAddress}:${info.port}"
                        if(runCatching { LanAddress.parse(address) }.isSuccess) {
                            resolved=address
                            if(!ready) restart("Desktop found. Connecting…")
                        }
                    } }
                })
            }
        }
        discovery=listener
        runCatching { nsd.discoverServices("_tcgscanner._tcp.",NsdManager.PROTOCOL_DNS_SD,listener) }
            .onFailure { Log.w("TCGScanner","Discovery unavailable",it) }
    }
    /** History and images use the same Wi-Fi-bound, proxy-free LAN transport. */
    fun historyRequest(path:String,body:String?=null):ByteArray {
        require(path.startsWith("/api/library") || path.startsWith("/api/history") || path=="/api/collection/sync" || path.startsWith("/api/prices/") || path.matches(Regex("/(media|reference)/[a-f0-9]{64}(-thumb)?\\.jpg")))
        check(ready && endpoint.isNotBlank()) { "Desktop disconnected" }
        val client=requireNotNull(http).newBuilder().readTimeout(8,TimeUnit.SECONDS).callTimeout(12,TimeUnit.SECONDS).build()
        val request=Request.Builder().url(endpoint+path)
        if(body!=null) request.post(body.toRequestBody("application/json".toMediaType()))
        return client.newCall(request.build()).execute().use { response->
            if(!response.isSuccessful) { val detail=runCatching { JSONObject(response.body?.string().orEmpty()).optString("detail") }.getOrDefault("");error(detail.ifBlank { "Desktop request failed (${response.code})" }) }
            requireNotNull(response.body).byteStream().use { input->
                val output=java.io.ByteArrayOutputStream();val buffer=ByteArray(8192)
                while(true) { val count=input.read(buffer);if(count<0) break;output.write(buffer,0,count);check(output.size()<=2*1024*1024) { "History response too large" } }
                output.toByteArray()
            }
        }
    }
    fun reserveFrame():Boolean {
        val now=SystemClock.elapsedRealtime()
        if(!ready || now<nextFrame || !inFlight.compareAndSet(false,true)) return false
        sentAt=now
        return true
    }
    fun cancelFrame() { inFlight.set(false) }
    fun sendFrame(bytes:ByteArray) { main.post {
        if(!ready || bytes.size>1024*1024) { inFlight.set(false);return@post }
        sequence++
        val packet=ByteBuffer.allocate(8+bytes.size).putLong(sequence).put(bytes).array()
        if(socket?.send(packet.toByteString())!=true) restart("Connection interrupted. Reconnecting…")
    } }
}

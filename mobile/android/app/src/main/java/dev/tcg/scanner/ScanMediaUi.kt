package dev.tcg.scanner

import android.graphics.BitmapFactory
import android.net.Uri
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.ImageBitmap
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.util.Locale

@Composable internal fun Comparison(backend:ScannerBackend,entry:ScanEntry,revision:Long,compact:Boolean=false,
                                    full:Boolean=false,artwork:Boolean=false,modifier:Modifier=Modifier) {
    val requestFull=full || artwork
    var media by remember(entry.id,entry.card.id,requestFull) { mutableStateOf<JSONObject?>(null) }
    var loaded by remember(entry.id,requestFull) { mutableStateOf(false) }
    var retry by remember { mutableIntStateOf(0) }
    LaunchedEffect(entry.id,entry.card.id,revision,requestFull,retry) {
        try { media=backend.request("scanMedia",JSONObject().put("id",entry.id).put("full",requestFull)) }
        catch(e:CancellationException){throw e}
        catch(_:Exception){media=null}
        loaded=true
    }
    val height=if(compact)84.dp else if(artwork)335.dp else if(full)300.dp else 205.dp
    Column(modifier) {
        Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            val labels=if(full)listOf("Scanned Image","Reference Image") else if(artwork)listOf("Card artwork") else listOf("Scanned Image")
            labels.forEachIndexed { index,label->
                Column(Modifier.weight(1f)) {
                    if(!compact && !artwork)Info(label)
                    val uri=if(artwork)media?.stringOrNull("reference_uri") ?: media?.stringOrNull("scanned_uri")
                        else media?.stringOrNull(if(index==0)"scanned_uri" else "reference_uri")
                    var bitmap by remember(uri) { mutableStateOf<ImageBitmap?>(null) }
                    var decoded by remember(uri) { mutableStateOf(false) }
                    LaunchedEffect(uri) {
                        bitmap=withContext(Dispatchers.IO) {
                            uri?.let { runCatching { BitmapFactory.decodeFile(Uri.parse(it).path)?.asImageBitmap() }.getOrNull() }
                        }
                        decoded=true
                    }
                    Box(Modifier.fillMaxWidth().height(height).clip(RoundedCornerShape(8.dp)).background(Background),contentAlignment=Alignment.Center) {
                        if(bitmap!=null)Image(bitmap!!,label,Modifier.fillMaxSize(),contentScale=ContentScale.Fit)
                        else Info(if(compact)"—" else if(!loaded || !decoded)"Loading…"
                            else if(!artwork && index==0 && media?.optBoolean("synced")==true && media?.optBoolean("has_photo")==false)"No saved photo"
                            else "Image unavailable",Modifier.padding(8.dp))
                    }
                }
            }
        }
        if(!compact && loaded && (if(artwork)media?.stringOrNull("reference_uri")==null && media?.stringOrNull("scanned_uri")==null
            else media?.stringOrNull("scanned_uri")==null || full && media?.stringOrNull("reference_uri")==null))
            Action("Retry images",onClick={retry++})
    }
}
@Composable internal fun ColumnScope.CardPresentation(backend:ScannerBackend,entry:ScanEntry,revision:Long) {
    var compare by remember(entry.id) { mutableStateOf(false) }
    Comparison(backend,entry,revision,artwork=true,modifier=Modifier.align(Alignment.CenterHorizontally).width(240.dp))
    MarketPrice(backend,entry.card.id,full=true)
    TextButton(onClick={compare=!compare}) { Text(if(compare)"Hide image comparison" else "Compare scanned and reference images") }
    if(compare)Comparison(backend,entry,revision,full=true)
}

private fun money(cents:Long)="$"+String.format(Locale.US,"%.2f",cents/100.0)
@Composable internal fun MarketPrice(backend:ScannerBackend,cardId:String,compact:Boolean=false,full:Boolean=false) {
    val lifecycle=LocalLifecycleOwner.current.lifecycle
    var price by remember(cardId) { mutableStateOf<JSONObject?>(null) }
    var loaded by remember(cardId) { mutableStateOf(false) }
    LaunchedEffect(cardId) {
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while(true) {
                try { price=backend.request("price",JSONObject().put("id",cardId)) }
                catch(e:CancellationException){throw e}
                catch(_:Exception){price=price?.let { JSONObject(it.toString()).put("stale",true) }}
                loaded=true
                delay(if(price?.optString("status")=="loading")2000 else 60000)
            }
        }
    }
    val available=price?.let { !it.isNull("min_cents") }==true
    val range=if(available) {
        val p=price!!
        money(p.getLong("min_cents"))+(if(!p.isNull("max_cents") && p.getLong("max_cents")!=p.getLong("min_cents"))"–"+money(p.getLong("max_cents")) else "")
    } else ""
    val text=if(available)(if(compact)"~" else "Est. ")+range+(if(compact)"" else " USD")+
        (if(!compact && price?.optBoolean("stale")==true)" · Older data" else "")
        else if(!loaded || price?.optString("status")=="loading")"Pricing…" else if(compact)"—" else "Price unavailable"
    Column(verticalArrangement=Arrangement.spacedBy(4.dp)) {
        Text(text,color=if(available)Accent else Muted,
            style=if(full)MaterialTheme.typography.headlineSmall else if(compact)MaterialTheme.typography.titleMedium else MaterialTheme.typography.bodyLarge)
        if(full) {
            price?.optJSONArray("quotes")?.objects()?.forEach { Info("${it.optString("label")} · ${money(it.optLong("cents"))} USD") }
            Info("TCGplayer market · via TCGdex")
            price?.stringOrNull("updated")?.let { Info("Source updated $it") }
            price?.takeUnless { it.isNull("fetched_at") }?.optLong("fetched_at")?.let { Info("Checked "+date(it)) }
            Info("Estimate for an ungraded card. Confirm the printing and finish; condition affects value.")
        }
    }
}

package dev.tcg.scanner

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.launch
import org.json.JSONObject

@Composable internal fun ScannerCaptureScreen(
    backend:ScannerBackend,state:ScannerState,cameraAllowed:Boolean,scanning:Boolean,paused:Boolean,torch:Boolean,
    pause:()->Unit,light:()->Unit,settings:()->Unit,collections:()->Unit,details:(ScanEntry)->Unit,
    allowCamera:()->Unit,camera:@Composable (Modifier,Boolean,Boolean)->Unit
) {
    val active=state.catalog?.collections?.find { it.id==state.catalog?.active }?.name
    Box(Modifier.fillMaxSize()) {
        if(cameraAllowed)camera(Modifier.fillMaxSize(),scanning,torch && scanning)
        // A scrim improves contrast without intercepting camera focus gestures.
        Box(Modifier.fillMaxSize().background(Brush.verticalGradient(
            0f to Color.Black.copy(alpha=.45f),.25f to Color.Transparent,
            .55f to Color.Transparent,1f to Color.Black.copy(alpha=.75f))))
        Row(Modifier.align(Alignment.TopCenter).widthIn(max=640.dp).fillMaxWidth()
            .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Top+WindowInsetsSides.Horizontal))
            .padding(16.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            val status=when {
                !cameraAllowed->"Camera access"
                state.cameraNotice.isNotBlank()->"Camera issue"
                paused->"Paused"
                state.notice.isNotBlank()->"Connecting"
                active==null->"Choose collection"
                else->"Scanning"
            }
            Box(Modifier.weight(1f)) {
                Surface(color=Background.copy(alpha=.78f),shape=CircleShape) {
                    Row(Modifier.padding(horizontal=12.dp,vertical=9.dp),verticalAlignment=Alignment.CenterVertically,
                        horizontalArrangement=Arrangement.spacedBy(7.dp)) {
                        Box(Modifier.size(6.dp).background(if(scanning && state.notice.isBlank() && state.cameraNotice.isBlank())Accent else Muted,CircleShape))
                        Text(status,fontSize=12.sp,maxLines=1,overflow=TextOverflow.Ellipsis)
                    }
                }
            }
            if(state.hasLight)CameraControl("Camera light",ScannerIcons.Flash,enabled=scanning,active=torch,stateLabel=if(torch)"On" else "Off",onClick=light)
            CameraControl(if(paused)"Resume scanning" else "Pause scanning",if(paused)ScannerIcons.Play else ScannerIcons.Pause,
                enabled=cameraAllowed && active!=null,active=paused,onClick=pause)
            CameraControl("Scanner settings",ScannerIcons.Settings,onClick=settings)
        }
        Column(Modifier.align(Alignment.BottomCenter).widthIn(max=520.dp).fillMaxWidth()
            .windowInsetsPadding(WindowInsets.safeDrawing.only(WindowInsetsSides.Bottom+WindowInsetsSides.Horizontal))
            .padding(16.dp),verticalArrangement=Arrangement.spacedBy(10.dp),horizontalAlignment=Alignment.CenterHorizontally) {
            val message=when {
                !cameraAllowed->"Allow camera access to scan cards."
                state.cameraNotice.isNotBlank()->state.cameraNotice
                paused->"Scanning paused"
                state.notice.isNotBlank()->state.notice
                active==null->"Choose a collection to start scanning"
                else->state.hint.ifBlank { "Hold a card steady" }
            }
            Text(message,color=Color.White.copy(alpha=.9f),fontSize=12.sp,textAlign=TextAlign.Center,
                modifier=Modifier.padding(horizontal=12.dp,vertical=4.dp))
            if(!cameraAllowed)Action("Allow camera access",primary=true,onClick=allowCamera)
            state.entry?.takeIf { state.preferences.information }?.let { entry->
                ScannerResult(backend,entry,state,details)
            }
            Surface(onClick=collections,shape=CircleShape,color=Background.copy(alpha=.92f),
                border=BorderStroke(1.dp,Color.White.copy(alpha=.12f)),
                modifier=Modifier.heightIn(min=48.dp).semantics { contentDescription="Choose scanning collection" }) {
                Row(Modifier.padding(horizontal=16.dp,vertical=12.dp),verticalAlignment=Alignment.CenterVertically,
                    horizontalArrangement=Arrangement.spacedBy(10.dp)) {
                    Icon(ScannerIcons.Collection,null,tint=Accent,modifier=Modifier.size(20.dp))
                    Text(active?.let { "Scanning into $it" } ?: "Choose a collection",fontSize=13.sp,
                        modifier=Modifier.weight(1f,false),maxLines=1,overflow=TextOverflow.Ellipsis)
                    Icon(ScannerIcons.Down,null,tint=Muted,modifier=Modifier.size(16.dp))
                }
            }
        }
    }
}

@Composable private fun CameraControl(label:String,icon:ImageVector,enabled:Boolean=true,active:Boolean=false,stateLabel:String?=null,onClick:()->Unit) {
    FilledIconButton(onClick=onClick,enabled=enabled,modifier=Modifier.size(48.dp).semantics { stateLabel?.let { stateDescription=it } },
        colors=IconButtonDefaults.filledIconButtonColors(
            containerColor=if(active)Accent else Background.copy(alpha=.78f),
            contentColor=if(active)Background else Color.White,
            disabledContainerColor=Background.copy(alpha=.5f),disabledContentColor=Muted.copy(alpha=.5f))) {
        Icon(icon,contentDescription=label,modifier=Modifier.size(22.dp))
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable private fun ScannerResult(backend:ScannerBackend,entry:ScanEntry,state:ScannerState,details:(ScanEntry)->Unit) {
    Surface(onClick={details(entry)},color=SurfaceColor.copy(alpha=.96f),shape=RoundedCornerShape(24.dp),
        border=BorderStroke(1.dp,Color.White.copy(alpha=.1f)),
        modifier=Modifier.fillMaxWidth().semantics { contentDescription="View details for ${entry.card.name}" }) {
        Column(Modifier.padding(14.dp),verticalArrangement=Arrangement.spacedBy(9.dp)) {
            Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                Comparison(backend,entry,state.revision,compact=true,modifier=Modifier.width(60.dp))
                Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(4.dp)) {
                    Text(entry.card.name,style=MaterialTheme.typography.titleMedium,maxLines=1,overflow=TextOverflow.Ellipsis)
                    Text(subtitle(entry.card),color=Muted,fontSize=12.sp,maxLines=1,overflow=TextOverflow.Ellipsis)
                    MarketPrice(backend,entry.card.id,compact=true)
                }
                Icon(ScannerIcons.Right,null,tint=Muted,modifier=Modifier.size(18.dp))
            }
            val saved=state.catalog?.collections?.find { it.id==entry.collectionId }?.name
            FlowRow(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.SpaceBetween,
                verticalArrangement=Arrangement.spacedBy(4.dp)) {
                Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(5.dp)) {
                    Icon(ScannerIcons.Check,null,tint=Accent,modifier=Modifier.size(14.dp))
                    Text(if(entry.collectionId==state.catalog?.active)"Saved" else saved?.let { "Saved in $it" } ?: "Saved in another collection",
                        color=Muted,fontSize=11.sp,maxLines=1,overflow=TextOverflow.Ellipsis)
                }
                val timing=scanTimingLabel(entry.scanMs,entry.processingMs)
                if(timing.isNotBlank())Text(timing,color=Muted,fontSize=11.sp)
            }
        }
    }
}

@Composable internal fun ScanResultSheet(backend:ScannerBackend,entry:ScanEntry,state:ScannerState,close:()->Unit,manage:()->Unit) {
    Sheet(entry.card.name,close) {
        Info(subtitle(entry.card))
        CardPresentation(backend,entry,state.revision)
        state.catalog?.collections?.find { it.id==entry.collectionId }?.let { Info("Saved in ${it.name}") }
        val timing=scanTimingLabel(entry.scanMs,entry.processingMs)
        if(timing.isNotBlank())Info(timing)
        Action("Manage saved card",modifier=Modifier.fillMaxWidth(),onClick=manage)
        Action("Back to scanning",primary=true,modifier=Modifier.fillMaxWidth(),onClick=close)
    }
}

@Composable internal fun CollectionPicker(backend:ScannerBackend,state:ScannerState,close:()->Unit,manage:()->Unit) {
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    val scope=rememberCoroutineScope()
    Sheet("Scan into",{if(!busy)close()}) {
        val groups=state.catalog?.collections.orEmpty()
        if(groups.isEmpty())Info("Create your first collection to start scanning.")
        groups.forEach { collection->
            Surface(onClick={
                if(!busy) {
                    busy=true;error=null
                    scope.launch {
                        try { backend.request("collectionAction",JSONObject().put("action","select").put("id",collection.id));close() }
                        catch(e:CancellationException){throw e}
                        catch(e:Exception){error=e.message ?: "Could not select collection"}
                        finally { busy=false }
                    }
                }
            },enabled=!busy,shape=RoundedCornerShape(16.dp),color=if(collection.id==state.catalog?.active)Raised else Background,
                modifier=Modifier.fillMaxWidth().heightIn(min=56.dp)) {
                Row(Modifier.padding(16.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                    Icon(ScannerIcons.Collection,null,tint=Muted)
                    Column(Modifier.weight(1f)) {
                        Text(collection.name,maxLines=2,overflow=TextOverflow.Ellipsis)
                        Info("${collection.count} scans")
                    }
                    if(collection.id==state.catalog?.active)Icon(ScannerIcons.Check,"Selected collection",tint=Accent)
                }
            }
        }
        error?.let { Text(it,color=MaterialTheme.colorScheme.error) }
        Action("Manage collections",enabled=!busy,modifier=Modifier.fillMaxWidth(),onClick=manage)
    }
}

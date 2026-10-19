package dev.tcg.scanner

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Shadow
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.platform.LocalConfiguration
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import org.json.JSONArray
import org.json.JSONObject
import java.text.DateFormat
import java.util.Date
import kotlin.math.roundToInt

internal val Background=Color(0xff0d1518)
internal val SurfaceColor=Color(0xff17252a)
internal val Raised=Color(0xff22353b)
internal val Accent=Color(0xff6de1c2)
internal val Muted=Color(0xffa4b7b6)
internal fun subtitle(card:Card)="${card.set} · ${card.number}"+(card.total?.let { "/$it" } ?: "")
internal fun date(time:Long)=DateFormat.getDateTimeInstance(DateFormat.SHORT,DateFormat.SHORT).format(Date(time))

@Composable internal fun ScannerTheme(content:@Composable ()->Unit) {
    MaterialTheme(colorScheme=darkColorScheme(primary=Accent,onPrimary=Background,background=Background,
        surface=SurfaceColor,surfaceVariant=Raised,onSurface=Color(0xfff2f7f5),onSurfaceVariant=Muted),content=content)
}
@Composable internal fun Action(text:String,enabled:Boolean=true,primary:Boolean=false,modifier:Modifier=Modifier,onClick:()->Unit) {
    Button(onClick=onClick,enabled=enabled,modifier=modifier.heightIn(min=48.dp),shape=RoundedCornerShape(12.dp),
        colors=ButtonDefaults.buttonColors(containerColor=if(primary)Accent else Raised,contentColor=if(primary)Background else Color.White)) {
        Text(text)
    }
}
@Composable internal fun Info(text:String,modifier:Modifier=Modifier) { Text(text,color=Muted,fontSize=13.sp,modifier=modifier) }
@OptIn(ExperimentalMaterial3Api::class)
@Composable internal fun Sheet(title:String,close:()->Unit,content:@Composable ColumnScope.()->Unit) {
    val sheet=rememberModalBottomSheetState(skipPartiallyExpanded=true)
    ModalBottomSheet(onDismissRequest=close,sheetState=sheet,containerColor=SurfaceColor) {
        Column(Modifier.fillMaxWidth().heightIn(max=(LocalConfiguration.current.screenHeightDp*.85f).dp)
            .imePadding().verticalScroll(rememberScrollState()).padding(horizontal=20.dp).padding(bottom=28.dp),
            verticalArrangement=Arrangement.spacedBy(12.dp)) {
            Text(title,style=MaterialTheme.typography.headlineSmall)
            content()
        }
    }
}
@Composable internal fun Failure(message:String?,close:()->Unit) {
    if(message!=null)AlertDialog(onDismissRequest=close,title={Text(stringResource(R.string.app_name))},text={Text(message)},
        confirmButton={TextButton(onClick=close){Text("OK")}})
}

@Composable internal fun ScannerApp(backend:ScannerBackend,cameraAllowed:Boolean,foreground:Boolean,allowCamera:()->Unit,
                                    camera:@Composable (Modifier,Boolean,Boolean)->Unit) {
    val state by backend.state.collectAsStateWithLifecycle()
    var library by rememberSaveable { mutableStateOf(false) }
    var settings by rememberSaveable { mutableStateOf(false) }
    var selected by rememberSaveable { mutableStateOf<String?>(null) }
    var torch by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(foreground) { if(!foreground)torch=false }
    BackHandler(library && selected==null && !settings) { library=false }
    ScannerTheme {
        Surface(Modifier.fillMaxSize(),color=Background) {
            if(library) {
                CollectionScreen(backend,state,foreground,selected,{selected=it},{selected=null;library=false})
            } else Box(Modifier.fillMaxSize()) {
                if(cameraAllowed)camera(Modifier.fillMaxSize(),foreground && !settings && state.catalog?.active!=null,torch)
                Row(Modifier.fillMaxWidth().safeDrawingPadding().padding(12.dp),horizontalArrangement=Arrangement.End) {
                    Surface(onClick={settings=true},color=SurfaceColor.copy(alpha=.85f),shape=RoundedCornerShape(24.dp),
                        modifier=Modifier.size(48.dp).semantics { contentDescription="Scanner settings" }) {
                        Box(contentAlignment=Alignment.Center) { Text("⚙",fontSize=22.sp) }
                    }
                }
                Column(Modifier.align(Alignment.BottomCenter).fillMaxWidth().safeDrawingPadding().padding(16.dp),
                    verticalArrangement=Arrangement.spacedBy(8.dp)) {
                    val message=if(!cameraAllowed)"Allow camera access to scan cards."
                        else state.cameraNotice.ifBlank { state.notice.ifBlank { state.hint } }
                    if(message.isNotBlank())Text(message,Modifier.fillMaxWidth().padding(horizontal=8.dp,vertical=4.dp),
                        color=Color.White,fontSize=13.sp,textAlign=TextAlign.Center,
                        style=MaterialTheme.typography.bodySmall.copy(shadow=Shadow(Color.Black,Offset(0f,2f),6f)))
                    if(!cameraAllowed)Action("Allow camera access",primary=true,modifier=Modifier.align(Alignment.CenterHorizontally),onClick=allowCamera)
                    Surface(color=SurfaceColor.copy(alpha=.94f),shape=RoundedCornerShape(18.dp)) {
                        Column {
                            state.entry?.takeIf { state.preferences.information }?.let { entry->
                                Row(Modifier.fillMaxWidth().clickable { selected=entry.id;library=true }.padding(12.dp),
                                    verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(12.dp)) {
                                    Comparison(backend,entry,state.revision,compact=true,modifier=Modifier.width(52.dp))
                                    Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(3.dp)) {
                                        Text(entry.card.name,maxLines=1,overflow=TextOverflow.Ellipsis,style=MaterialTheme.typography.titleMedium)
                                        Text(subtitle(entry.card),color=Muted,fontSize=12.sp,maxLines=2,overflow=TextOverflow.Ellipsis)
                                        MarketPrice(backend,entry.card.id,compact=true)
                                        val timing=scanTimingLabel(entry.scanMs,entry.processingMs)
                                        if(timing.isNotBlank())Text(timing,color=Muted,fontSize=12.sp)
                                        if(entry.collectionId!=state.catalog?.active) {
                                            val saved=state.catalog?.collections?.find { it.id==entry.collectionId }?.name
                                            Text(saved?.let { "Saved in $it" } ?: "Saved in another collection",color=Muted,fontSize=12.sp,
                                                maxLines=1,overflow=TextOverflow.Ellipsis)
                                        }
                                    }
                                    Text("›",color=Muted,fontSize=22.sp)
                                }
                                HorizontalDivider(color=Muted.copy(alpha=.15f),modifier=Modifier.padding(horizontal=12.dp))
                            }
                            val active=state.catalog?.collections?.find { it.id==state.catalog?.active }?.name
                            Row(Modifier.fillMaxWidth().heightIn(min=48.dp).clickable { library=true }
                                .semantics { contentDescription="Choose scanning collection" }.padding(horizontal=14.dp,vertical=10.dp),
                                verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                                Text(active?.let { "Scanning into $it" } ?: "Choose a collection to scan",color=Muted,fontSize=13.sp,
                                    modifier=Modifier.weight(1f),maxLines=1,overflow=TextOverflow.Ellipsis)
                                Text("⌄",color=Accent,fontSize=18.sp)
                            }
                        }
                    }
                }
            }
            if(settings)SettingsSheet(backend,state,torch,{torch=it},{settings=false},{settings=false;library=true})
        }
    }
}
@Composable private fun SettingsSheet(backend:ScannerBackend,state:ScannerState,torch:Boolean,setTorch:(Boolean)->Unit,
                                      close:()->Unit,library:()->Unit) {
    val scope=rememberCoroutineScope()
    var error by remember { mutableStateOf<String?>(null) }
    var ip by rememberSaveable { mutableStateOf(state.preferences.serverIp) }
    var threshold by remember { mutableFloatStateOf(state.preferences.threshold) }
    fun preference(key:String,value:Any) {
        scope.launch {
            try { backend.request("preference",JSONObject().put("key",key).put("value",value)) }
            catch(e:CancellationException){throw e}
            catch(e:Exception){error=e.message ?: "Could not save setting"}
        }
    }
    Sheet("Scan settings",close) {
        listOf(Triple("sound","Detection sound",state.preferences.sound),
            Triple("vibration","Vibration",state.preferences.vibration),
            Triple("show_information","Card information",state.preferences.information)).forEach { (key,label,value)->
            Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
                Text(label,Modifier.weight(1f));Switch(value,{preference(key,it)},modifier=Modifier.semantics { contentDescription=label })
            }
        }
        if(state.hasLight)Row(Modifier.fillMaxWidth(),verticalAlignment=Alignment.CenterVertically) {
            Text("Camera light",Modifier.weight(1f));Switch(torch,setTorch,modifier=Modifier.semantics { contentDescription="Camera light" })
        }
        Text("Detection threshold · %.2f".format(threshold))
        Slider(value=threshold,onValueChange={threshold=(it*100).roundToInt()/100f},valueRange=.5f.. .95f,
            onValueChangeFinished={preference("detection_threshold",threshold.toDouble())},
            modifier=Modifier.semantics { contentDescription="Detection confidence threshold" })
        Info("Higher values require clearer detections. Sound follows the phone’s sound mode.")
        Action("Collections",onClick=library)
        Text("Desktop connection",style=MaterialTheme.typography.titleMedium)
        Info(state.notice.ifBlank { "Connected over local Wi-Fi · "+state.server })
        OutlinedTextField(ip,{ip=it},label={Text("Desktop IP address")},placeholder={Text("Automatic discovery")},
            modifier=Modifier.fillMaxWidth(),singleLine=true,keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Ascii))
        Action(if(ip.isBlank())"Use automatic discovery" else "Connect to this IP",onClick={preference("server_ip",ip.trim())})
        Info("Use the same Wi-Fi as your desktop. Leave the address empty for automatic discovery.")
        Action("Done",onClick=close)
    }
    Failure(error){error=null}
}

private data class Deletion(val title:String,val message:String,val payload:JSONObject,val done:()->Unit)

@Composable private fun CollectionScreen(backend:ScannerBackend,state:ScannerState,foreground:Boolean,
                                        selected:String?,select:(String?)->Unit,close:()->Unit) {
    var group by rememberSaveable { mutableStateOf("") }
    var query by rememberSaveable { mutableStateOf("") }
    var data by remember { mutableStateOf<CollectionView?>(null) }
    var rows by remember { mutableStateOf(emptyList<ScanEntry>()) }
    var chosen by remember { mutableStateOf(emptySet<String>()) }
    var detail by remember { mutableStateOf<ScanEntry?>(null) }
    var editing by rememberSaveable { mutableStateOf(false) }
    var editVersion by remember { mutableLongStateOf(0) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    var message by remember { mutableStateOf("") }
    var reload by remember { mutableIntStateOf(0) }
    var nameForm by rememberSaveable { mutableStateOf<String?>(null) }
    var name by rememberSaveable { mutableStateOf("") }
    var moveIds by remember { mutableStateOf(emptyList<String>()) }
    var deletion by remember { mutableStateOf<Deletion?>(null) }
    val scope=rememberCoroutineScope()
    fun openGroup(id:String) { group=id;query="";chosen=emptySet();rows=emptyList();data=null }
    fun versions(ids:List<String>)=JSONObject().apply {
        ids.forEach { id->(detail?.takeIf { it.id==id } ?: rows.find { it.id==id })?.let { put(id,it.revision) } }
    }
    fun act(payload:JSONObject,done:(JSONObject)->Unit={}) {
        if(busy)return
        busy=true
        scope.launch {
            try {
                val result=backend.request("collectionAction",payload)
                reload++;message="";done(result)
            } catch(e:CancellationException){throw e}
            catch(e:Exception){error=e.message ?: "Collection update failed"}
            finally{busy=false}
        }
    }
    fun remove(ids:List<String>) {
        deletion=Deletion("Delete scans?","Delete ${ids.size} scans and their saved photos? This cannot be undone.",
            JSONObject().put("action","delete_scans").put("ids",JSONArray(ids)).put("versions",versions(ids))) {
            chosen=emptySet();select(null)
        }
    }
    LaunchedEffect(group,query,state.revision,reload,foreground) {
        if(!foreground)return@LaunchedEffect
        delay(150)
        try {
            val view=CollectionView.parse(backend.request("library",JSONObject().put("collection_id",group).put("query",query)))
            data=view;rows=view.rows;chosen=chosen.intersect(rows.map { it.id }.toSet());message=""
            if(group.isNotEmpty() && view.collection==null)openGroup("")
        } catch(e:CancellationException){throw e}
        catch(e:Exception){message=e.message ?: "Desktop disconnected"}
    }
    LaunchedEffect(selected) { editing=false;detail=null }
    LaunchedEffect(selected,state.revision,reload,foreground) {
        if(selected==null || !foreground)return@LaunchedEffect
        try { detail=ScanEntry.parse(backend.request("detail",JSONObject().put("id",selected))) }
        catch(e:CancellationException){throw e}
        catch(e:Exception) {
            if(e.message?.contains("Scan not saved")==true){select(null);detail=null}
            else message=e.message ?: "Could not load scan"
        }
    }
    BackHandler(selected==null && group.isNotEmpty() && nameForm==null && moveIds.isEmpty()) { openGroup("") }
    Column(Modifier.fillMaxSize().safeDrawingPadding().padding(horizontal=16.dp),verticalArrangement=Arrangement.spacedBy(8.dp)) {
        Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.spacedBy(8.dp)) {
            Action("Back to scanner",modifier=Modifier.weight(1f),onClick=close)
            Action("New collection",enabled=!busy,onClick={name="";nameForm="create"})
        }
        Text(data?.collection?.name ?: "Collections",style=MaterialTheme.typography.headlineSmall)
        if(message.isNotBlank())Info("$message · Changes need the desktop connection.")
        if(group.isNotEmpty()) {
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                Action("All collections",onClick={openGroup("")})
                Action("Scan into this collection",enabled=!busy,primary=true,modifier=Modifier.weight(1f),
                    onClick={act(JSONObject().put("action","select").put("id",group)){close()}})
            }
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                Action("Rename",enabled=!busy,onClick={name=data?.collection?.name.orEmpty();nameForm="rename"})
                Action("Delete collection",enabled=!busy,onClick={
                    deletion=Deletion("Delete collection?","Delete “${data?.collection?.name}” and all ${data?.collection?.count ?: 0} scans? This cannot be undone.",
                        JSONObject().put("action","delete_collection").put("id",group)){openGroup("")}
                })
            }
            OutlinedTextField(query,{query=it},label={Text("Search cards")},placeholder={Text("Name, set or card number")},
                modifier=Modifier.fillMaxWidth(),singleLine=true)
            Info("${data?.total ?: 0} scans · ${chosen.size} selected")
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                Action(if(rows.isNotEmpty() && chosen.size==rows.size)"Clear selection" else "Select loaded",
                    onClick={chosen=if(chosen.size==rows.size)emptySet() else rows.map { it.id }.toSet()})
                Action("Move",enabled=chosen.isNotEmpty() && !busy,onClick={moveIds=chosen.toList()})
                Action("Delete",enabled=chosen.isNotEmpty() && !busy,onClick={remove(chosen.toList())})
            }
        }
        LazyColumn(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(8.dp),contentPadding=PaddingValues(bottom=20.dp)) {
            if(group.isEmpty()) {
                val collections=data?.collections.orEmpty()
                if(collections.isEmpty())item { Info(if(data==null && message.isEmpty())"Loading collections…" else "Create a collection, then select it before scanning.") }
                items(collections,key={it.id}) { collection->
                    Surface(onClick={openGroup(collection.id)},shape=RoundedCornerShape(20.dp),modifier=Modifier.fillMaxWidth()) {
                        Column(Modifier.padding(16.dp)) {
                            Text(collection.name,style=MaterialTheme.typography.titleLarge)
                            Info("${collection.count} scans"+if(collection.id==data?.active)" · Selected for scanning" else "")
                        }
                    }
                }
            } else {
                if(rows.isEmpty())item { Info(if(query.isNotBlank())"No matching scans." else "No scans yet. Select this collection and present a card.") }
                items(rows,key={it.id}) { entry->
                    Surface(shape=RoundedCornerShape(20.dp)) {
                        Column(Modifier.padding(12.dp)) {
                            Row(verticalAlignment=Alignment.CenterVertically) {
                                Checkbox(entry.id in chosen,{checked->chosen=if(checked)chosen+entry.id else chosen-entry.id},
                                    modifier=Modifier.semantics { contentDescription="Select ${entry.card.name}" })
                                Info("Select scan")
                            }
                            Column(Modifier.fillMaxWidth().clickable { select(entry.id) }) {
                                Comparison(backend,entry,state.revision)
                                Text(entry.card.name,style=MaterialTheme.typography.titleLarge)
                                Info(subtitle(entry.card));Info(date(entry.time))
                            }
                        }
                    }
                }
                if(data?.next!=null)item {
                    Action("Load more scans",enabled=!busy,onClick={
                        val snapshot=data ?: return@Action
                        busy=true
                        scope.launch {
                            try {
                                val page=CollectionView.parse(backend.request("library",JSONObject().put("collection_id",group)
                                    .put("query",query).put("before",snapshot.next)))
                                if(page.revision!=snapshot.revision)reload++
                                else { data=page;rows=(rows+page.rows).distinctBy { it.id } }
                            } catch(e:CancellationException){throw e}
                            catch(e:Exception){error=e.message}
                            finally{busy=false}
                        }
                    })
                }
            }
        }
    }
    if(selected!=null && moveIds.isEmpty()) {
        Sheet(if(editing)"Edit card details" else detail?.card?.name ?: "Card details",
            {if(editing)editing=false else select(null)}) {
            val entry=detail
            if(entry==null) {
                Info(message.ifBlank { "Loading scan…" })
                Action("Close",onClick={select(null)})
            } else if(editing) {
                key(entry.id) {
                    EditCard(entry.card,busy,{editing=false}) { fields->
                        act(JSONObject().put("action","edit").put("ids",JSONArray(listOf(entry.id)))
                            .put("versions",JSONObject().put(entry.id,editVersion)).put("fields",fields)){editing=false}
                    }
                }
            } else {
                Info(subtitle(entry.card)+" · "+date(entry.time))
                Comparison(backend,entry,state.revision,full=true)
                MarketPrice(backend,entry.card.id,full=true)
                Action("Edit details",primary=true,onClick={editVersion=entry.revision;editing=true})
                Action("Move to collection",enabled=!busy,onClick={moveIds=listOf(entry.id)})
                Action("Delete scan",enabled=!busy,onClick={remove(listOf(entry.id))})
                Action("Done",onClick={select(null)})
            }
        }
    }
    if(nameForm!=null)Sheet(if(nameForm=="create")"New collection" else "Rename collection",{if(!busy)nameForm=null}) {
        OutlinedTextField(name,{name=it.take(100)},label={Text("Collection name")},singleLine=true,enabled=!busy,modifier=Modifier.fillMaxWidth())
        Action("Save",primary=true,enabled=!busy && name.isNotBlank(),onClick={
            val kind=nameForm
            act(JSONObject().put("action",kind).put("name",name.trim()).apply { if(kind=="rename")put("id",group) }) {
                nameForm=null;if(kind=="create")openGroup(it.getString("id"))
            }
        })
        Action("Cancel",enabled=!busy,onClick={nameForm=null})
    }
    if(moveIds.isNotEmpty())Sheet("Move scans",{if(!busy)moveIds=emptyList()}) {
        val options=(data?.collections ?: state.catalog?.collections).orEmpty().filter { it.id!=(detail?.collectionId ?: group) }
        options.forEach { collection->
            Action(collection.name,enabled=!busy,onClick={
                act(JSONObject().put("action","move").put("ids",JSONArray(moveIds)).put("versions",versions(moveIds))
                    .put("collection_id",collection.id)){moveIds=emptyList();chosen=emptySet();select(null)}
            })
        }
        if(options.isEmpty())Info("Create another collection first.")
        Action("Cancel",enabled=!busy,onClick={moveIds=emptyList()})
    }
    deletion?.let { pending->
        AlertDialog(onDismissRequest={deletion=null},title={Text(pending.title)},text={Text(pending.message)},
            dismissButton={TextButton(onClick={deletion=null}){Text("Cancel")}},
            confirmButton={TextButton(enabled=!busy,onClick={deletion=null;act(pending.payload){pending.done()}}){Text("Delete")}})
    }
    Failure(error){error=null}
}

@Composable private fun ColumnScope.EditCard(card:Card,busy:Boolean,cancel:()->Unit,save:(JSONObject)->Unit) {
    var name by rememberSaveable { mutableStateOf(card.name) }
    var set by rememberSaveable { mutableStateOf(card.set) }
    var number by rememberSaveable { mutableStateOf(card.number) }
    var code by rememberSaveable { mutableStateOf(card.code.orEmpty()) }
    var total by rememberSaveable { mutableStateOf(card.total?.toString().orEmpty()) }
    var validation by remember { mutableStateOf<String?>(null) }
    @Composable fun field(label:String,value:String,limit:Int,change:(String)->Unit,numeric:Boolean=false) {
        OutlinedTextField(value,{change(it.take(limit))},label={Text(label)},enabled=!busy,modifier=Modifier.fillMaxWidth(),
            singleLine=true,keyboardOptions=KeyboardOptions(keyboardType=if(numeric)KeyboardType.Number else KeyboardType.Text))
    }
    field("Card name",name,200,{name=it})
    field("Set name",set,200,{set=it})
    field("Card number",number,32,{number=it})
    field("Set code (optional)",code,32,{code=it})
    field("Cards in set (optional)",total,6,{total=it},true)
    Info("Your scanned photo stays saved. Custom details may no longer have a matching reference image or price.")
    validation?.let { Text(it,color=MaterialTheme.colorScheme.error) }
    Row(horizontalArrangement=Arrangement.spacedBy(8.dp)) {
        Action("Cancel",enabled=!busy,modifier=Modifier.weight(1f),onClick=cancel)
        Action(if(busy)"Saving…" else "Save changes",primary=true,enabled=!busy,modifier=Modifier.weight(1f),onClick={
            val draft=CardDraft(name,set,number,code,total)
            validation=draft.validation()
            if(validation==null)save(draft.fields())
        })
    }
}

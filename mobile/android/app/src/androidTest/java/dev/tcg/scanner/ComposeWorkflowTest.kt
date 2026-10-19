package dev.tcg.scanner

import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.SideEffect
import androidx.compose.ui.test.*
import androidx.compose.ui.semantics.SemanticsActions
import androidx.compose.ui.test.junit4.createComposeRule
import kotlinx.coroutines.flow.MutableStateFlow
import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test

/** UI fixtures are confined to the test APK; production always uses the LAN controller. */
class ComposeWorkflowTest {
    @get:Rule val compose=createComposeRule()
    private val backend=FixtureBackend()
    private fun launch() {
        compose.setContent { ScannerApp(backend,true,true,{}) { modifier,_,_->Box(modifier) } }
    }
    private fun click(text:String) {
        // IME inset animations can move a touch target between scrolling and a coordinate click.
        compose.onNodeWithText(text).performScrollToIfNeeded().performSemanticsAction(SemanticsActions.OnClick) { it() }
    }
    private fun SemanticsNodeInteraction.performScrollToIfNeeded():SemanticsNodeInteraction {
        try { performScrollTo() } catch(_:AssertionError) { }
        return this
    }
    private fun waitText(text:String) {
        compose.waitUntil(10000) { compose.onAllNodesWithText(text).fetchSemanticsNodes().isNotEmpty() }
    }
    private fun openCollections() { compose.onNodeWithContentDescription("Choose scanning collection").performClick() }
    private fun openBinder() { openCollections();waitText("Binder");click("Binder");waitText("Pikachu") }
    @Test fun settingsAndNavigationUseNativeState() {
        launch()
        compose.onNodeWithContentDescription("Scanner settings").performClick()
        compose.onNodeWithContentDescription("Detection sound").performClick()
        compose.runOnIdle { assertFalse(backend.state.value.preferences.sound) }
        compose.onNodeWithText("Desktop IP address").performTextReplacement("192.168.50.10")
        click("Connect to this IP")
        compose.runOnIdle { assertEquals("192.168.50.10",backend.state.value.preferences.serverIp) }
        click("Done")
        openBinder()
        click("Back to scanner")
        compose.onNodeWithContentDescription("Choose scanning collection").assertIsDisplayed()
    }
    @Test fun createRenameAndSelectCollection() {
        launch();openCollections();click("New collection")
        compose.onNodeWithText("Collection name").performTextInput("New binder")
        click("Save");waitText("New binder")
        click("Rename")
        compose.onNodeWithText("Collection name").performTextReplacement("Renamed binder")
        click("Save");waitText("Renamed binder")
        click("Scan into this collection")
        compose.runOnIdle { assertEquals("created",backend.active) }
        waitText("Scanning into Renamed binder")
    }
    @Test fun editValidatesAndKeepsLeadingZeroesThenMove() {
        launch();openBinder();click("Pikachu");waitText("Edit details");click("Edit details")
        compose.onNodeWithText("Cards in set (optional)").performTextReplacement("0")
        compose.onNodeWithText("Cards in set (optional)").assertTextContains("0")
        click("Save changes")
        waitText("Set size must be a whole number from 1 to 100,000.")
        compose.onNodeWithText("Cards in set (optional)").performTextReplacement("100")
        compose.onNodeWithText("Card number").performTextReplacement("007")
        compose.onNodeWithText("Card name").performTextReplacement("Corrected card")
        click("Save changes");waitText("Edit details")
        compose.runOnIdle {
            assertEquals("007",backend.scan!!.getJSONObject("card").getString("number"))
            assertEquals("Corrected card",backend.scan!!.getJSONObject("card").getString("name"))
        }
        click("Move to collection");waitText("Trades");click("Trades")
        compose.runOnIdle { assertEquals("trades",backend.scan!!.getString("collection_id")) }
        waitText("No scans yet. Select this collection and present a card.")
    }
    @Test fun deletingScansRequiresConfirmationAndSupportsBulkSelection() {
        launch();openBinder()
        click("Select loaded");click("Delete");waitText("Delete scans?")
        click("Cancel")
        compose.runOnIdle { assertNotNull(backend.scan) }
        click("Delete");waitText("Delete scans?")
        compose.onAllNodesWithText("Delete").onLast().performClick()
        compose.runOnIdle { assertNull(backend.scan) }
        waitText("No scans yet. Select this collection and present a card.")
    }

    @Test fun uncertainScanKeepsCameraRunningWithoutPopupOrForcedChoice() {
        val card=Card(0,"test-007","Candidate Pikachu","Another set","007",null,null,"")
        backend.state.value=backend.state.value.copy(pending=PendingScan("request-1",listOf(card)),
            hint="Reading card…",catalog=backend.state.value.catalog!!.copy(active="binder"))
        var scanning=false
        compose.setContent { ScannerApp(backend,true,true,{}) { modifier,enabled,_->
            SideEffect { scanning=enabled };Box(modifier)
        } }
        waitText("Reading card…")
        compose.onNodeWithText("Confirm this card").assertDoesNotExist()
        compose.onNodeWithText("Candidate Pikachu").assertDoesNotExist()
        compose.runOnIdle { assertTrue(scanning);assertNull(backend.confirmed) }
        openCollections();waitText("Binder")
    }

    @Test fun resultShowsTimingAndDistinguishesSavedCollectionFromDestination() {
        val row=JSONObject(backend.scan.toString()).put("scan_ms",1420).put("processing_ms",49.2)
        val saved=ScanEntry.parse(row)
        // A normal history refresh must retain timing, while another scan must not inherit it.
        val refreshed=JSONObject(backend.scan.toString())
        saved.retainTiming(refreshed)
        assertEquals(1420L,ScanEntry.parse(refreshed).scanMs)
        assertEquals(49.2,ScanEntry.parse(refreshed).processingMs!!,0.001)
        val other=JSONObject(backend.scan.toString()).put("id","other")
        saved.retainTiming(other)
        assertNull(ScanEntry.parse(other).scanMs)
        backend.state.value=backend.state.value.copy(entry=saved,
            catalog=backend.state.value.catalog!!.copy(active="trades"),hint="Tilt the card slightly for a clearer scan")
        launch()
        compose.onNodeWithText("Scan 1.42 s · Processing 49 ms").assertIsDisplayed()
        compose.onNodeWithText("Saved in Binder").assertIsDisplayed()
        compose.onNodeWithText("Scanning into Trades").assertIsDisplayed()
        compose.onNodeWithText("Tilt the card slightly for a clearer scan").assertIsDisplayed()
        val instrumentation=androidx.test.platform.app.InstrumentationRegistry.getInstrumentation()
        val screenshot=instrumentation.uiAutomation.takeScreenshot()
        try {
            java.io.File(instrumentation.targetContext.getExternalFilesDir(null),"scanner-result.png").outputStream().use {
                screenshot.compress(android.graphics.Bitmap.CompressFormat.PNG,100,it)
            }
        } finally { screenshot.recycle() }
        compose.onNodeWithContentDescription("Choose scanning collection").performClick()
        waitText("Binder")
    }
    @Test fun destinationRemainsAvailableWithInformationHiddenAndOldScansHaveNoInventedTiming() {
        backend.state.value=backend.state.value.copy(entry=ScanEntry.parse(backend.scan!!),
            catalog=backend.state.value.catalog!!.copy(active="binder"))
        launch()
        compose.onNodeWithText("Pikachu").assertIsDisplayed()
        compose.onAllNodesWithText("Processing",substring=true).assertCountEquals(0)
        compose.runOnIdle { backend.state.value=backend.state.value.copy(preferences=ScannerPreferences(information=false)) }
        compose.onNodeWithText("Pikachu").assertDoesNotExist()
        compose.onNodeWithText("Scanning into Binder").assertIsDisplayed()
        compose.onNodeWithContentDescription("Choose scanning collection").performClick()
        waitText("Binder")
    }

    private class FixtureBackend:ScannerBackend {
        private val groups=linkedMapOf("binder" to "Binder","trades" to "Trades")
        var active:String?=null
        var confirmed:String?=null
        var scan:JSONObject?=JSONObject().put("id","scan").put("time",1000).put("collection_id","binder")
            .put("revision",1).put("card",JSONObject().put("id","test-001").put("name","Pikachu")
                .put("set","Test set").put("number","1").put("total",100).put("code","T").put("image_url",""))
        private var revision=1L
        override val state=MutableStateFlow(ScannerState(notice="",catalog=CollectionView.parse(view(""))))
        private fun view(group:String):JSONObject {
            fun collection(id:String)=JSONObject().put("id",id).put("name",groups[id])
                .put("count",if(scan?.optString("collection_id")==id)1 else 0)
            val rows=scan?.takeIf { it.optString("collection_id")==group }?.let { listOf(it) }.orEmpty()
            return JSONObject().put("collections",JSONArray(groups.keys.map(::collection)))
                .put("active_collection",active ?: JSONObject.NULL).put("collection",if(groups.containsKey(group))collection(group) else JSONObject.NULL)
                .put("rows",JSONArray(rows)).put("total",rows.size).put("next",JSONObject.NULL).put("revision",revision)
        }
        override suspend fun request(action:String,value:JSONObject):JSONObject=when(action) {
            "confirm"->{
                confirmed=if(value.isNull("card_id"))"skip" else value.getString("card_id")
                state.value=state.value.copy(pending=null)
                JSONObject()
            }
            "library"->view(value.optString("collection_id"))
            "detail"->requireNotNull(scan)
            "scanMedia"->JSONObject().put("synced",true).put("has_photo",false)
            "price"->JSONObject().put("min_cents",JSONObject.NULL).put("status","unavailable").put("quotes",JSONArray())
            "preference"->{
                val old=state.value.preferences
                state.value=state.value.copy(preferences=when(value.getString("key")) {
                    "sound"->old.copy(sound=value.getBoolean("value"))
                    "server_ip"->old.copy(serverIp=value.getString("value"))
                    else->old
                })
                JSONObject()
            }
            "collectionAction"->{
                val result=JSONObject()
                when(value.getString("action")) {
                    "create"->{groups["created"]=value.getString("name");result.put("id","created")}
                    "rename"->groups[value.getString("id")]=value.getString("name")
                    "select"->active=value.getString("id")
                    "edit"->value.getJSONObject("fields").let { fields->
                        fields.keys().forEach { key->scan!!.getJSONObject("card").put(key,fields.get(key)) }
                    }
                    "move"->scan!!.put("collection_id",value.getString("collection_id"))
                    "delete_scans"->scan=null
                    "delete_collection"->groups.remove(value.getString("id"))
                }
                revision++;scan?.put("revision",revision)
                state.value=state.value.copy(catalog=CollectionView.parse(view("")),revision=revision)
                result
            }
            else->error(action)
        }
    }
}

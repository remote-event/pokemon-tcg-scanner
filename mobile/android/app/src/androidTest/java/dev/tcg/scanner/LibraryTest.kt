package dev.tcg.scanner

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.json.JSONObject
import java.util.UUID

@RunWith(AndroidJUnit4::class)
class LibraryTest {
    private val context get()=InstrumentationRegistry.getInstrumentation().targetContext
    private fun card(id:String)=Card(-1,id,"Pokémon \"A\",\nB","Set","001","ASC",217,"https://example.invalid/$id")
    private fun decision(verified:Boolean)=Decision(listOf(Match(card("a"),.88f),Match(card("b"),.86f)),if(verified) "verified" else "unreadable",verified)
    private fun isolated(action:(LibraryStore,String)->Unit) {
        val name="test-library-${UUID.randomUUID()}.sqlite3"
        try { LibraryStore(context,name).use { action(it,name) } } finally { context.deleteDatabase(name) }
    }
    @Test fun batchCountsUndoAndManualReviewAreTransactional()=isolated { store,_->
        val batch=store.startBatch();assertEquals(batch,store.startBatch())
        val first=store.record(decision(true),batch)
        assertEquals(1,store.quantity("a"));assertEquals(1,store.batchCount(batch))
        assertFalse(store.add(first.id,"a"));assertEquals(1,store.quantity("a"))
        val possible=store.record(decision(false),batch)
        assertTrue(possible.added);assertEquals(2,store.quantity("a"));assertEquals(1,store.reviewCount())
        assertFalse(store.add(possible.id,"a"))
        store.resolve(possible.id,"b");assertEquals("manual",store.entry(possible.id)!!.verification)
        assertEquals(1,store.quantity("a"));assertEquals(0,store.reviewCount())
        assertEquals(1,store.quantity("b"));assertEquals(2,store.batchCount(batch))
        assertTrue(store.undoLast(batch));assertEquals(0,store.quantity("b"));assertEquals(1,store.batchCount(batch))
        assertTrue(store.undo(first.id));assertFalse(store.undo(first.id));assertEquals(0,store.quantity("a"))
        assertEquals(2,store.history().size)
        store.finishBatch(batch);assertNull(store.activeBatch())
        assertFalse(store.record(decision(true),batch).added)
    }
    @Test fun backgroundCorrectionMovesOneCopyAndRespectsHumanActions()=isolated { store,_->
        val batch=store.startBatch()
        val provisional=store.record(decision(false),batch)
        val corrected=Decision(decision(false).matches.reversed(),"verified",true)
        assertEquals(1,store.reviewCount())
        assertEquals(provisional.id,store.refine(provisional.id,corrected)!!.id)
        assertEquals(0,store.quantity("a"));assertEquals(1,store.quantity("b"))
        assertEquals(1,store.batchCount(batch));assertEquals(1,store.history().size);assertEquals(0,store.reviewCount())
        store.refine(provisional.id,corrected);assertEquals(1,store.quantity("b"))
        val manual=store.record(decision(false),batch)
        store.resolve(manual.id,"a");store.refine(manual.id,corrected)
        assertEquals("a",store.entry(manual.id)!!.card.id);assertEquals("manual",store.entry(manual.id)!!.verification)
        val undone=store.record(decision(false),batch)
        store.undo(undone.id);store.refine(undone.id,corrected)
        assertTrue(store.entry(undone.id)!!.undone)
        assertEquals(1,store.quantity("a"));assertEquals(1,store.quantity("b"))
        assertEquals(2,store.batchCount(batch));assertEquals(0,store.reviewCount())
    }
    @Test fun tentativeCopiesSurviveBackupAndReviewFiltering()=isolated { store,_->
        val batch=store.startBatch();val e=store.record(decision(false),batch)
        val backup=store.backup();assertEquals(2,JSONObject(backup).getInt("version"))
        val oldEpoch=LibraryStore.restorations.get()
        store.restore(LibraryFiles.decode(backup))
        assertNull(store.refine(e.id,Decision(decision(false).matches.reversed(),"verified",true),oldEpoch))
        assertEquals(1,store.quantity("a"));assertEquals(e.id,store.history(reviewOnly=true).single().id)
        assertTrue(store.history(query="absent",reviewOnly=true).isEmpty())
        store.resolve(e.id,"a");assertEquals(1,store.quantity("a"));assertTrue(store.history(reviewOnly=true).isEmpty())
        // Earlier v1 backups remain importable.
        val legacy=JSONObject(store.backup()).put("version",1).toString()
        store.restore(LibraryFiles.decode(legacy));assertEquals(1,store.quantity("a"))
        store.setQuantity(card("a"),12);assertEquals(1 to 12L,store.totals())
        store.setQuantity(card("a"),0);assertEquals(0 to 0L,store.totals())
        try { store.setQuantity(card("a"),-1);fail("Negative quantity accepted") } catch(_:IllegalArgumentException) {}
    }
    @Test fun backupRoundTripPersistsAndRepeatedRestoreDoesNotDuplicate()=isolated { store,name->
        val batch=store.startBatch();val entry=store.record(decision(true),batch)
        store.adjust(card("a"),2)
        val text=store.backup();val parsed=LibraryFiles.read(text.byteInputStream())
        store.adjust(card("a"),5);store.restore(parsed);store.restore(parsed)
        assertEquals(3,store.quantity("a"));assertEquals(1,store.history().size)
        LibraryStore(context,name).use { reopened->
            assertEquals(batch,reopened.activeBatch());assertEquals(entry.id,reopened.history().single().id)
            assertEquals(3,reopened.quantity("a"));assertTrue(reopened.undoLast(batch));assertEquals(2,reopened.quantity("a"))
        }
        assertTrue(text.contains("Pokémon"))
    }
    @Test fun malformedRestoreLeavesOriginalLibraryUntouched()=isolated { store,_->
        val b=store.startBatch();store.record(decision(true),b)
        val before=store.backup()
        val good=LibraryFiles.decode(before)
        val invalid=good.copy(collection=listOf(CollectionEntry(card("a"),-1)))
        try { store.restore(invalid);fail("Expected invalid quantity rejection") } catch(_:IllegalArgumentException) {}
        assertEquals(before,store.backup())
        for(text in listOf("not json",JSONObject(before).put("version",99).toString(),JSONObject(before).put("batches",org.json.JSONArray()).toString())) {
            try { LibraryFiles.decode(text);fail("Expected rejection") } catch(_:Exception) {}
        }
        assertEquals(before,store.backup())
        // Force a database write failure after restore has deleted the old rows.
        // The entire replacement must roll back, including collection and batches.
        store.writableDatabase.execSQL("CREATE TRIGGER fail_restore BEFORE INSERT ON history BEGIN SELECT RAISE(ABORT,'test restore failure'); END")
        try { store.restore(good);fail("Expected forced database failure") } catch(_:android.database.SQLException) {}
        finally { store.writableDatabase.execSQL("DROP TRIGGER fail_restore") }
        assertEquals(before,store.backup())
        try { store.adjust(card("a"),-2);fail("Negative quantity accepted") } catch(_:IllegalArgumentException) {}
        assertEquals(1,store.quantity("a"))
    }
    @Test fun manualFormEditsPersistMoveOneCopyAndSurviveLateResults()=isolated { store,name->
        val batch=store.startBatch()
        val d=decision(false).copy(status="identified")
        val first=store.record(d,batch);store.record(d,batch)
        assertEquals("identified",first.verification);assertEquals(0,store.reviewCount())
        val fields=JSONObject().put("name","Corrected card").put("set","Corrected set").put("number","070")
            .put("code","ASC").put("total",217)
        val edited=store.edit(first.id,fields)
        assertEquals(first.id,edited.id);assertEquals(first.time,edited.time)
        assertEquals("manual",edited.verification);assertEquals("Corrected card",edited.card.name)
        assertTrue(edited.card.id.startsWith("manual-"));assertEquals("",edited.card.imageUrl)
        assertEquals(1,store.quantity("a"));assertEquals(1,store.quantity(edited.card.id))
        store.edit(first.id,fields);assertEquals(1,store.quantity(edited.card.id));assertEquals(2,store.history().size)
        store.refine(first.id,decision(true));assertEquals(edited,store.entry(first.id))
        val backup=store.backup();store.restore(LibraryFiles.decode(backup))
        LibraryStore(context,name).use { reopened->assertEquals(edited,reopened.entry(first.id)) }
        assertTrue(store.csv().contains("Corrected card"))
        store.undo(first.id);store.edit(first.id,fields.put("number","071"))
        assertTrue(store.entry(first.id)!!.undone);assertEquals(1 to 1L,store.totals())
    }
    @Test fun invalidEditsAreAtomicAndKnownMatchesKeepReference()=isolated { store,_->
        val accepted=store.record(Decision(decision(false).matches,"identified",false))
        assertTrue(store.add(accepted.id,"a"))
        assertEquals("identified",store.entry(accepted.id)!!.verification)
        assertTrue(store.undo(accepted.id))

        val e=store.record(decision(false),store.startBatch())
        val before=store.backup()
        val fields=JSONObject().put("name","").put("set","Set").put("number","1").put("code","").put("total",JSONObject.NULL)
        try {store.edit(e.id,fields);fail("Empty name accepted")} catch(_:IllegalArgumentException) {}
        assertEquals(before,store.backup())
        fields.put("name","Name").put("total",1.5)
        try {store.edit(e.id,fields);fail("Fractional size accepted")} catch(_:IllegalArgumentException) {}
        assertEquals(before,store.backup())
        val known=card("a")
        val unchanged=store.edit(e.id,JSONObject().put("name",known.name).put("set",known.set).put("number",known.number).put("code",known.code).put("total",known.total))
        assertEquals(known,unchanged.card);assertEquals(1,store.quantity("a"))
    }
    @Test fun csvEscapesQuotesNewlinesAndSpreadsheetFormulas()=isolated { store,_->
        store.adjust(card("a"),2)
        val csv=store.csv()
        assertTrue(csv.contains("\"Pokémon \"\"A\"\",\nB\""));assertTrue(csv.contains("\"2\""))
        val formula=LibraryFiles.csv(listOf(CollectionEntry(card("b").copy(name="=SUM(1,2)"),1)))
        assertTrue(formula.contains("\"'=SUM(1,2)\""))
    }
}

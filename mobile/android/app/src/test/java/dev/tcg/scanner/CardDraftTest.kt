package dev.tcg.scanner

import org.junit.Assert.*
import org.junit.Test

class CardDraftTest {
    private fun draft(number:String="007",total:String="100")=CardDraft("Pikachu","Test set",number,"T",total)
    @Test fun collectorNumbersStayStringsAndOptionalFieldsCanBeBlank() {
        assertNull(draft(number="TG01").validation())
        assertNull(draft().copy(code="",total="").validation())
        assertEquals("007",draft().number)
    }
    @Test fun invalidFieldsAreRejectedBeforeNetworkRequests() {
        for(total in listOf("0","-1","1.5","100001","NaN","1e2"))assertNotNull(total,draft(total=total).validation())
        assertNotNull(draft().copy(name=" ").validation())
        assertNotNull(draft().copy(set=" ").validation())
        assertNotNull(draft().copy(number=" ").validation())
        assertNotNull(draft().copy(number="x".repeat(33)).validation())
        assertNull(draft(total="100000").validation())
    }
}

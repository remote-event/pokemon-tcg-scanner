package dev.tcg.scanner

import org.junit.Assert.*
import org.junit.Test

class ScanTimingTest {
    @Test fun includesStabilizationAndUncertainRetries() {
        val clock=ScanTimingTracker()
        assertNull(clock.observe("observing","searching",100))
        clock.observe("observing","stabilizing",500)
        clock.observe("confirmation","searching",900)
        clock.observe("observing","stabilizing",2200)
        assertEquals(500L,clock.observe("recognized","locked",2600))
        clock.observe("observing","locked",3000)
        clock.observe("observing","stabilizing",3500)
        assertEquals(3500L,clock.observe("recognized","locked",4000))
    }
    @Test fun removalPauseAndExhaustedAttemptsDoNotInflateNextScan() {
        val clock=ScanTimingTracker()
        clock.observe("observing","stabilizing",100)
        clock.observe("removed","searching",500)
        assertEquals(900L,clock.observe("recognized","locked",900))
        clock.observe("observing","stabilizing",1000)
        clock.reset()
        assertEquals(2000L,clock.observe("recognized","locked",2000))
        clock.observe("observing","stabilizing",3000)
        clock.observe("confirmation","locked",4000)
        assertEquals(5000L,clock.observe("recognized","locked",5000))
        clock.observe("observing","stabilizing",6000)
        assertEquals(15000L,clock.observe("recognized","locked",15000))
    }
    @Test fun timingLabelsOmitUnknownValuesAndUseReadableUnits() {
        assertEquals("",scanTimingLabel(null,null))
        assertEquals("Processing 49 ms",scanTimingLabel(null,49.2))
        assertEquals("Scan 1.42 s · Processing 49 ms",scanTimingLabel(1420,49.2))
    }
}

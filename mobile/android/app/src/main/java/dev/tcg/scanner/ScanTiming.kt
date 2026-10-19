package dev.tcg.scanner

import java.util.Locale
import kotlin.math.roundToLong

/** One active attempt, from its first usable frame. No wall-clock or server clock mixing. */
internal class ScanTimingTracker {
    private var started:Long?=null
    private var previousFrame:Long?=null
    fun reset() { started=null;previousFrame=null }
    fun observe(event:String,state:String,frameStarted:Long):Long? {
        // A paused camera or a stalled connection must not inflate the next attempt.
        if(previousFrame?.let { frameStarted-it>8000 }==true)reset()
        previousFrame=frameStarted
        if(event=="removed") { reset();return null }
        if(event=="recognized") {
            val result=started ?: frameStarted
            reset()
            return result
        }
        if(state=="locked") { reset();return null }
        if(started==null && (state=="stabilizing" || state=="identifying" || event=="confirmation"))started=frameStarted
        return null
    }
}

internal fun durationLabel(ms:Double):String = if(ms<1000) "${ms.roundToLong()} ms"
    else String.format(Locale.US,"%.2f s",ms/1000)

internal fun scanTimingLabel(scanMs:Long?,processingMs:Double?):String = listOfNotNull(
    scanMs?.let { "Scan ${durationLabel(it.toDouble())}" },
    processingMs?.let { "Processing ${durationLabel(it)}" }).joinToString(" · ")

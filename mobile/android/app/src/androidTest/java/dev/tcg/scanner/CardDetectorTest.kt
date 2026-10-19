package dev.tcg.scanner

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import android.os.SystemClock
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

/** Run on ARM64 hardware as well as emulators: CPU instruction dispatch differs.
 * Uses fixture photos only; does not connect to a server or change collections. */
@RunWith(AndroidJUnit4::class)
class CardDetectorTest {
    private val inst get()=InstrumentationRegistry.getInstrumentation()

    @Test fun realPhotosSurviveRepeatedInferenceAndSessionRecreation() {
        val photos=listOf("card-a.jpg","card-b.jpg").map { name->
            val original=inst.context.assets.open(name).use { BitmapFactory.decodeStream(it) }
            val scale=1280f/maxOf(original.width,original.height)
            val resized=Bitmap.createScaledBitmap(original,(original.width*scale).toInt(),(original.height*scale).toInt(),true)
            if(resized!==original)original.recycle()
            resized
        }
        val timings=mutableListOf<Long>()
        try {
            repeat(3) {
                CardDetector(inst.targetContext).use { detector->
                    repeat(20) { index->
                        val started=SystemClock.elapsedRealtimeNanos()
                        val result=detector.detect(photos[index%photos.size],.5f)
                        timings.add(SystemClock.elapsedRealtimeNanos()-started)
                        val crop=requireNotNull(result.crop) { "Missing card crop on iteration $index" }
                        try {
                            assertEquals(630,crop.width);assertEquals(880,crop.height)
                            assertFalse(result.empty)
                        } finally { crop.recycle() }
                    }
                }
            }
            timings.sort()
            android.util.Log.i("TCG_TEST","60 real-photo detections across 3 sessions; median=${timings[timings.size/2]/1e6} ms")
        } finally { photos.forEach { it.recycle() } }
    }

    @Test fun coveredLensDoesNotCountAsCardRemoval() {
        val image=Bitmap.createBitmap(480,960,Bitmap.Config.ARGB_8888)
        try {
            CardDetector(inst.targetContext).use { detector->
                image.eraseColor(Color.BLACK)
                val covered=detector.detect(image,.5f)
                assertNull(covered.crop);assertFalse(covered.empty)
                image.eraseColor(Color.WHITE)
                val empty=detector.detect(image,.5f)
                assertNull(empty.crop);assertTrue(empty.empty)
            }
        } finally { image.recycle() }
    }
}

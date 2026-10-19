package dev.tcg.scanner

import android.content.Context
import android.content.SharedPreferences
import android.media.AudioAttributes
import android.media.AudioManager
import android.media.SoundPool
import android.os.*
import java.io.Closeable

/** One preloaded short chime; invoked only for a newly recorded card. */
internal class ScanFeedback(private val context:Context,private val prefs:SharedPreferences):Closeable {
    private val pool=SoundPool.Builder().setMaxStreams(1).setAudioAttributes(AudioAttributes.Builder()
        .setUsage(AudioAttributes.USAGE_ASSISTANCE_SONIFICATION).setContentType(AudioAttributes.CONTENT_TYPE_SONIFICATION).build()).build()
    @Volatile private var ready=false
    private val sound:Int
    init {
        pool.setOnLoadCompleteListener { _,_,status->ready=status==0 }
        sound=pool.load(context,R.raw.scan_ding,1)
    }
    @Suppress("DEPRECATION")
    fun play() {
        try {
            if(prefs.getBoolean("vibration",true)) {
                val vibrator=context.getSystemService(Vibrator::class.java)
                if(Build.VERSION.SDK_INT>=33) vibrator.vibrate(VibrationEffect.createOneShot(35,VibrationEffect.DEFAULT_AMPLITUDE),VibrationAttributes.createForUsage(VibrationAttributes.USAGE_NOTIFICATION))
                else vibrator.vibrate(VibrationEffect.createOneShot(35,VibrationEffect.DEFAULT_AMPLITUDE),AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_NOTIFICATION).build())
            }
            val audio=context.getSystemService(AudioManager::class.java)
            if(ready && prefs.getBoolean("sound",true) && audio.ringerMode==AudioManager.RINGER_MODE_NORMAL)
                pool.play(sound,.65f,.65f,1,0,1f)
        } catch(e:RuntimeException) { android.util.Log.w("TCGScanner","Scan feedback unavailable",e) }
    }
    override fun close() { ready=false;pool.release() }
}

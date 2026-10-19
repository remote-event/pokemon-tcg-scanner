package dev.tcg.scanner

import android.Manifest
import android.content.pm.PackageManager
import android.graphics.*
import android.util.Size
import android.os.SystemClock
import android.view.MotionEvent
import org.json.JSONObject
import java.util.concurrent.TimeUnit
import kotlin.math.abs
import kotlin.math.roundToInt
import android.widget.FrameLayout
import androidx.camera.core.*
import androidx.camera.core.resolutionselector.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.core.content.ContextCompat
import androidx.lifecycle.LifecycleOwner
import android.content.Context
import androidx.lifecycle.DefaultLifecycleObserver
import androidx.lifecycle.Lifecycle
import java.io.ByteArrayOutputStream
import java.util.concurrent.Executors

internal object FrameImages {
    fun cropAndRotate(source:Bitmap,crop:Rect,rotation:Int):Bitmap {
        var current=source
        try {
            val bounds=Rect(crop);require(bounds.intersect(0,0,source.width,source.height))
            val cropped=Bitmap.createBitmap(current,bounds.left,bounds.top,bounds.width(),bounds.height())
            if(cropped!==current) { current.recycle();current=cropped }
            if(rotation!=0) {
                val rotated=Bitmap.createBitmap(current,0,0,current.width,current.height,Matrix().apply { postRotate(rotation.toFloat()) },true)
                if(rotated!==current) { current.recycle();current=rotated }
            }
            return current
        } catch(e:Throwable) { current.recycle();throw e }
    }
}

internal class CameraPreview(context:Context, private val owner:LifecycleOwner,
                             private val controller:ScannerController):FrameLayout(context),DefaultLifecycleObserver {
    val preview=PreviewView(context).apply { scaleType=PreviewView.ScaleType.FILL_CENTER;implementationMode=PreviewView.ImplementationMode.COMPATIBLE }
    private val worker=Executors.newSingleThreadExecutor()
    private var provider:ProcessCameraProvider?=null
    private var camera:androidx.camera.core.Camera?=null
    private var analysis:ImageAnalysis?=null
    private var previewCase:Preview?=null
    private var disposed=false
    private var starting=false
    private var foreground=owner.lifecycle.currentState.isAtLeast(Lifecycle.State.STARTED)
    private var lastMeterAt=0L
    private var meterX=.5f
    private var meterY=.5f
    private var exposureVote=0
    private var exposureVotes=0
    private var detector:CardDetector?=null
    @Volatile var framesSent=0
        private set
    @Volatile var scanning=true
        set(value) {
            if(field && !value)controller.connection.resetScanTiming()
            field=value;camera?.cameraControl?.enableTorch(torch && foreground && value)
        }
    var torch=false
        set(value) { field=value;camera?.cameraControl?.enableTorch(value && foreground && scanning) }
    init {
        addView(preview,LayoutParams(-1,-1));owner.lifecycle.addObserver(this)
        preview.setOnTouchListener { _,event ->
            if(event.action==MotionEvent.ACTION_UP) {
                meter(event.x/preview.width.coerceAtLeast(1),event.y/preview.height.coerceAtLeast(1),true)
                preview.performClick()
            }
            true
        }
    }
    private fun meter(x:Float,y:Float,force:Boolean=false) {
        val current=camera ?: return
        if(!foreground || !scanning || preview.width==0 || preview.height==0) return
        val now=SystemClock.elapsedRealtime()
        if(!force && (now-lastMeterAt<3000 || abs(x-meterX)+abs(y-meterY)<.15f)) return
        meterX=x.coerceIn(0f,1f);meterY=y.coerceIn(0f,1f);lastMeterAt=now
        val point=preview.meteringPointFactory.createPoint(meterX*preview.width,meterY*preview.height,.25f)
        val action=FocusMeteringAction.Builder(point,FocusMeteringAction.FLAG_AF or FocusMeteringAction.FLAG_AE or FocusMeteringAction.FLAG_AWB)
            .setAutoCancelDuration(5,TimeUnit.SECONDS).build()
        current.cameraControl.startFocusAndMetering(action)
    }
    private fun feedback(value:JSONObject) {
        val current=camera ?: return
        if(!foreground || !scanning) return
        if(value.optBoolean("reset")) {
            exposureVotes=0;exposureVote=0
            if(current.cameraInfo.exposureState.isExposureCompensationSupported) current.cameraControl.setExposureCompensationIndex(0)
            return
        }
        meter(value.optDouble("x",.5).toFloat(),value.optDouble("y",.5).toFloat())
        // Three agreeing observations; small absolute EV changes, never an accumulating ramp.
        if(value.isNull("exposure")) { exposureVotes=0;return }
        val state=current.cameraInfo.exposureState
        if(!state.isExposureCompensationSupported) return
        val step=state.exposureCompensationStep.toDouble()
        if(step<=0) return
        val index=(value.optDouble("exposure",0.0).coerceIn(-.5,.5)/step).roundToInt()
            .coerceIn(state.exposureCompensationRange.lower,state.exposureCompensationRange.upper)
        exposureVotes=if(index==exposureVote) exposureVotes+1 else 1;exposureVote=index
        if(exposureVotes==3 && index!=state.exposureCompensationIndex) {
            current.cameraControl.setExposureCompensationIndex(index)
            android.util.Log.d("TCGScanner","Card exposure compensation: $index")
        }
    }
    override fun onAttachedToWindow() { super.onAttachedToWindow();post { start() } }
    override fun onLayout(changed:Boolean,l:Int,t:Int,r:Int,b:Int) { super.onLayout(changed,l,t,r,b);if(camera==null) post { start() } }
    private fun start() {
        if(disposed || starting || !foreground || !isAttachedToWindow || width==0 || camera!=null) return
        if(ContextCompat.checkSelfPermission(context,Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED) return
        starting=true
        val future=ProcessCameraProvider.getInstance(context)
        future.addListener({
            starting=false
            if(disposed || !foreground || !isAttachedToWindow) return@addListener
            try {
                provider=future.get()
                val selector=ResolutionSelector.Builder().setAspectRatioStrategy(AspectRatioStrategy.RATIO_4_3_FALLBACK_AUTO_STRATEGY)
                    .setResolutionStrategy(ResolutionStrategy(Size(1280,960),ResolutionStrategy.FALLBACK_RULE_CLOSEST_HIGHER_THEN_LOWER)).build()
                val p=Preview.Builder().setResolutionSelector(selector).build().also { it.setSurfaceProvider(preview.surfaceProvider) }
                val a=ImageAnalysis.Builder().setResolutionSelector(selector).setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build()
                a.setAnalyzer(worker,::analyze)
                previewCase=p;analysis=a
                val group=UseCaseGroup.Builder().setViewPort(requireNotNull(preview.viewPort)).addUseCase(p).addUseCase(a).build()
                camera=provider!!.bindToLifecycle(owner,CameraSelector.DEFAULT_BACK_CAMERA,group)
                camera!!.cameraControl.enableTorch(torch && scanning)
                controller.cameraFeedback=::feedback
                meter(.5f,.5f,true)
                controller.cameraStatus("",camera!!.cameraInfo.hasFlashUnit())
            } catch(e:Exception) { android.util.Log.w("TCGScanner","Camera unavailable",e);controller.cameraStatus("Camera unavailable. Reopen the scanner.",false) }
        },ContextCompat.getMainExecutor(context))
    }
    private fun analyze(frame:ImageProxy) {
        var image:Bitmap?=null
        val transport=controller.connection
        var reserved=false
        var released=false
        try {
            if(!scanning || !transport.reserveFrame()) return
            reserved=true
            image=FrameImages.cropAndRotate(frame.toBitmap(),frame.cropRect,frame.imageInfo.rotationDegrees)
            frame.close();released=true // Camera buffers are free before JPEG compression/network work.
            if(transport.nativeCrop) {
                val local=detector ?: CardDetector(context).also { detector=it }
                val detection=local.detect(image,controller.state.value.preferences.threshold)
                if(detection.crop==null) {
                    transport.sendFrame(if(detection.empty) "TCGEMPTY".toByteArray() else "TCGUNCERTAIN".toByteArray())
                    framesSent++
                    return
                }
                image.recycle();image=detection.crop
                post { meter(detection.centerX,detection.centerY) }
            }
            val longest=maxOf(image.width,image.height)
            if(longest>1280) {
                val scaled=Bitmap.createScaledBitmap(image,image.width*1280/longest,image.height*1280/longest,true)
                if(scaled!==image) { image.recycle();image=scaled }
            }
            val bytes=ByteArrayOutputStream().use { image.compress(Bitmap.CompressFormat.JPEG,78,it);it.toByteArray() }
            if(!scanning) transport.cancelFrame() else { transport.sendFrame(bytes);framesSent++ }
        } catch(e:Exception) { if(reserved) transport.cancelFrame();android.util.Log.w("TCGScanner","Frame capture failed",e) }
        finally { image?.recycle();if(!released) frame.close() }
    }
    fun dispose() {
        if(disposed) return
        disposed=true;scanning=false;controller.cameraFeedback=null;owner.lifecycle.removeObserver(this)
        analysis?.clearAnalyzer()
        val cases=listOfNotNull(previewCase,analysis).toTypedArray()
        provider?.unbind(*cases);camera=null
        worker.execute { detector?.close();detector=null };worker.shutdown()
    }
    override fun onStart(owner:LifecycleOwner) { foreground=true;post { start() };camera?.cameraControl?.enableTorch(torch && scanning) }
    override fun onStop(owner:LifecycleOwner) { foreground=false;camera?.cameraControl?.enableTorch(false) }
    override fun onDestroy(owner:LifecycleOwner) { dispose() }
}

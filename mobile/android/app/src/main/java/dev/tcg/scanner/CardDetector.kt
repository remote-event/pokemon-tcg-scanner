package dev.tcg.scanner

import ai.onnxruntime.*
import android.content.Context
import android.graphics.*
import java.nio.FloatBuffer
import kotlin.math.*

/** The existing four-corner YOLO weights, exported to fixed-size ONNX. */
internal class CardDetector(context:Context):AutoCloseable {
    private val environment=OrtEnvironment.getEnvironment()
    private val session=OrtSession.SessionOptions().use { options->
        options.setIntraOpNumThreads(2)
        environment.createSession(context.assets.open("card_pose_yolov8n.onnx").use { it.readBytes() },options)
    }
    data class Result(val crop:Bitmap?,val empty:Boolean,val centerX:Float=.5f,val centerY:Float=.5f)
    fun detect(source:Bitmap,threshold:Float):Result {
        val size=480;val scale=min(size.toFloat()/source.width,size.toFloat()/source.height)
        val w=(source.width*scale).roundToInt();val h=(source.height*scale).roundToInt()
        val dx=(size-w)/2;val dy=(size-h)/2
        val input=Bitmap.createBitmap(size,size,Bitmap.Config.ARGB_8888)
        val canvas=Canvas(input);canvas.drawColor(Color.rgb(114,114,114))
        canvas.drawBitmap(source,null,Rect(dx,dy,dx+w,dy+h),Paint(Paint.FILTER_BITMAP_FLAG))
        val pixels=IntArray(size*size);input.getPixels(pixels,0,size,0,0,size,size);input.recycle()
        val values=FloatArray(3*size*size)
        for(i in pixels.indices) { val c=pixels[i];values[i]=Color.red(c)/255f;values[i+pixels.size]=Color.green(c)/255f;values[i+2*pixels.size]=Color.blue(c)/255f }
        val output=OnnxTensor.createTensor(environment,FloatBuffer.wrap(values),longArrayOf(1,3,size.toLong(),size.toLong())).use { tensor->
            session.run(mapOf(session.inputNames.first() to tensor)).use { results->
                @Suppress("UNCHECKED_CAST")
                (results[0].value as Array<Array<FloatArray>>)[0]
            }
        }
        val candidates=output[4].indices.filter { output[4][it]>=.25f }.sortedByDescending { output[4][it] }
        if(candidates.isEmpty()) {
            // Letterbox gray must not make a covered/dark lens look like card removal.
            var light=0L;var count=0
            for(y in dy until dy+h step 17)for(x in dx until dx+w step 17) {
                val c=pixels[y*size+x];light+=Color.red(c)+Color.green(c)+Color.blue(c);count++
            }
            val brightness=light.toDouble()/(3*count.coerceAtLeast(1))
            return Result(null,brightness>30)
        }
        val retained=mutableListOf<Int>()
        fun overlap(a:Int,b:Int):Float {
            val ax=output[0][a];val ay=output[1][a];val aw=output[2][a];val ah=output[3][a]
            val bx=output[0][b];val by=output[1][b];val bw=output[2][b];val bh=output[3][b]
            val area=max(0f,min(ax+aw/2,bx+bw/2)-max(ax-aw/2,bx-bw/2))*max(0f,min(ay+ah/2,by+bh/2)-max(ay-ah/2,by-bh/2))
            return area/(aw*ah+bw*bh-area).coerceAtLeast(1f)
        }
        for(i in candidates)if(retained.none { overlap(i,it)>.5f })retained.add(i)
        val reliable=retained.filter { output[4][it]>=.5f }
        if(reliable.size!=1 || output[4][reliable[0]]<threshold)return Result(null,false)
        val best=reliable[0];val corners=FloatArray(8)
        for(k in 0..3) {
            if(output[7+3*k][best]<.5f)return Result(null,false)
            corners[2*k]=(output[5+3*k][best]-dx)/scale
            corners[2*k+1]=(output[6+3*k][best]-dy)/scale
            if(!corners[2*k].isFinite() || !corners[2*k+1].isFinite() || corners[2*k] !in 0f..(source.width-1).toFloat() || corners[2*k+1] !in 0f..(source.height-1).toFloat())return Result(null,false)
        }
        for(k in 0..3) {
            val n=(k+1)%4;val m=(k+2)%4
            val ax=corners[2*n]-corners[2*k];val ay=corners[2*n+1]-corners[2*k+1]
            val bx=corners[2*m]-corners[2*n];val by=corners[2*m+1]-corners[2*n+1]
            if(ax*by-ay*bx<=0 || hypot(ax,ay)<12)return Result(null,false)
        }
        val transform=Matrix()
        if(!transform.setPolyToPoly(corners,0,floatArrayOf(8f,8f,621f,8f,621f,871f,8f,871f),0,4))return Result(null,false)
        val crop=Bitmap.createBitmap(630,880,Bitmap.Config.ARGB_8888)
        Canvas(crop).apply { drawColor(Color.rgb(124,116,104));drawBitmap(source,transform,Paint(Paint.FILTER_BITMAP_FLAG)) }
        return Result(crop,false,(0..3).sumOf { corners[it*2].toDouble() }.toFloat()/4/source.width,
            (0..3).sumOf { corners[it*2+1].toDouble() }.toFloat()/4/source.height)
    }
    override fun close() { session.close() }
}

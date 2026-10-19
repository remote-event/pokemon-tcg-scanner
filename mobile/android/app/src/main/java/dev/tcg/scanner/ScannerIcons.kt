package dev.tcg.scanner

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.PathBuilder
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.unit.dp

/** Small, consistent stroke icons for the camera controls. */
internal object ScannerIcons {
    private fun glyph(name:String,draw:PathBuilder.()->Unit)=ImageVector.Builder(
        name=name,defaultWidth=24.dp,defaultHeight=24.dp,viewportWidth=24f,viewportHeight=24f
    ).apply {
        path(fill=null,stroke=SolidColor(Color.White),strokeLineWidth=1.8f,
            strokeLineCap=StrokeCap.Round,strokeLineJoin=StrokeJoin.Round,pathBuilder=draw)
    }.build()
    val Pause=glyph("Pause") { moveTo(8f,5f);lineTo(8f,19f);moveTo(16f,5f);lineTo(16f,19f) }
    val Play=glyph("Play") { moveTo(8f,4f);lineTo(20f,12f);lineTo(8f,20f);close() }
    val Flash=glyph("Flash") { moveTo(13f,2f);lineTo(4f,14f);lineTo(11f,14f);lineTo(10f,22f);lineTo(20f,9f);lineTo(13f,9f);close() }
    val Collection=glyph("Collection") {
        moveTo(3f,6f);lineTo(9f,6f);lineTo(11f,8f);lineTo(21f,8f);lineTo(21f,20f);lineTo(3f,20f);close()
        moveTo(5f,3f);lineTo(11f,3f);lineTo(13f,5f);lineTo(19f,5f)
    }
    val Down=glyph("Expand") { moveTo(6f,9f);lineTo(12f,15f);lineTo(18f,9f) }
    val Right=glyph("Details") { moveTo(9f,6f);lineTo(15f,12f);lineTo(9f,18f) }
    val Check=glyph("Saved") { moveTo(5f,12f);lineTo(10f,17f);lineTo(19f,7f) }
    val Settings=glyph("Settings") {
        moveTo(9f,3f);lineTo(15f,3f);lineTo(16f,6f);lineTo(19f,7f);lineTo(21f,12f)
        lineTo(19f,17f);lineTo(16f,18f);lineTo(15f,21f);lineTo(9f,21f);lineTo(8f,18f)
        lineTo(5f,17f);lineTo(3f,12f);lineTo(5f,7f);lineTo(8f,6f);close()
        moveTo(15.5f,12f);arcToRelative(3.5f,3.5f,0f,true,true,-7f,0f)
        arcToRelative(3.5f,3.5f,0f,true,true,7f,0f);close()
    }
}

package dev.tcg.scanner

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.runtime.*
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat

class MainActivity:ComponentActivity() {
    internal val controller:ScannerController by viewModels()
    private var cameraAllowed by mutableStateOf(false)
    private var foreground by mutableStateOf(false)
    private val permission=registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted->cameraAllowed=granted }
    private fun allowCamera() {
        val prefs=getSharedPreferences("scanner",0)
        if(prefs.getBoolean("camera_permission_requested",false) && !shouldShowRequestPermissionRationale(Manifest.permission.CAMERA) &&
            ContextCompat.checkSelfPermission(this,Manifest.permission.CAMERA)!=PackageManager.PERMISSION_GRANTED) {
            startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,Uri.parse("package:$packageName")))
        } else {
            prefs.edit().putBoolean("camera_permission_requested",true).apply()
            permission.launch(Manifest.permission.CAMERA)
        }
    }
    override fun onCreate(savedInstanceState:Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        WindowCompat.setDecorFitsSystemWindows(window,false)
        cameraAllowed=ContextCompat.checkSelfPermission(this,Manifest.permission.CAMERA)==PackageManager.PERMISSION_GRANTED
        setContent {
            ScannerApp(controller,cameraAllowed,foreground,::allowCamera) { modifier,scanning,torch->
                AndroidView(modifier=modifier,factory={CameraPreview(it,this,controller)},
                    update={it.scanning=scanning;it.torch=torch},onRelease={it.dispose()})
            }
        }
        if(savedInstanceState==null && !cameraAllowed && !getSharedPreferences("scanner",0).getBoolean("camera_permission_requested",false))allowCamera()
    }
    override fun onStart() {
        super.onStart();foreground=true
        cameraAllowed=ContextCompat.checkSelfPermission(this,Manifest.permission.CAMERA)==PackageManager.PERMISSION_GRANTED
        controller.start()
    }
    override fun onResume() {
        super.onResume()
        WindowInsetsControllerCompat(window,window.decorView).apply {
            systemBarsBehavior=WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
            hide(WindowInsetsCompat.Type.systemBars())
        }
    }
    override fun onStop() { foreground=false;controller.stop();super.onStop() }
}

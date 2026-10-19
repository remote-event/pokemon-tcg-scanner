plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}
android {
    namespace = "dev.tcg.scanner"
    compileSdk = 36
    defaultConfig {
        applicationId = "dev.tcg.scanner"
        minSdk = 26
        targetSdk = 36
        val ciBuild = providers.environmentVariable("APK_BUILD_NUMBER").orNull?.toInt()
        versionCode = ciBuild?.let { 1000 + it } ?: 23
        versionName = providers.environmentVariable("APK_VERSION_NAME").orNull
            ?: ciBuild?.let { "1.1.5-build.$it" } ?: "1.1.5"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    buildFeatures { compose = true; buildConfig = true }
    testOptions { unitTests.isReturnDefaultValues = true }
}
tasks.matching { it.name == "mergeReleaseAssets" }.configureEach {
    doFirst {
        check(file("src/main/assets/card_pose_yolov8n.onnx").isFile) {
            "Phone detector missing. Run scripts/prepare_android_model.py before building a release APK."
        }
    }
}
dependencies {
    // 1.23.2 emits unsupported SME instructions on some Snapdragon ARM64 devices.
    // Keep >=1.25: https://github.com/microsoft/onnxruntime/issues/27884
    implementation("com.microsoft.onnxruntime:onnxruntime-android:1.25.1")
    val composeBom = platform("androidx.compose:compose-bom:2025.04.01")
    implementation(composeBom)
    androidTestImplementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.activity:activity-compose:1.10.1")
    implementation("androidx.lifecycle:lifecycle-viewmodel-ktx:2.8.7")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.7")
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.camera:camera-camera2:1.4.2")
    implementation("androidx.camera:camera-lifecycle:1.4.2")
    implementation("androidx.camera:camera-view:1.4.2")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.9.0")
    testImplementation("junit:junit:4.13.2")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.compose.ui:ui-test-junit4")
    debugImplementation("androidx.compose.ui:ui-test-manifest")
}

// :core — pure-Kotlin module mirroring JessicaCore on iOS.
//
// M0 ships a hand-written Kotlin port of the Rust mobile core's public
// API so the UI compiles end-to-end. M1 swaps the bodies for `external
// fun` JNI declarations against `libjessica_ffi.so` (jniLibs/<abi>/);
// the public Kotlin API stays stable.

plugins {
    id("org.jetbrains.kotlin.jvm")
}

java {
    sourceCompatibility = JavaVersion.VERSION_17
    targetCompatibility = JavaVersion.VERSION_17
}

kotlin {
    jvmToolchain(17)
}

dependencies {
    testImplementation("org.jetbrains.kotlin:kotlin-test")
    testImplementation("org.junit.jupiter:junit-jupiter:5.10.2")
    testRuntimeOnly("org.junit.platform:junit-platform-launcher:1.10.2")
}

tasks.test {
    useJUnitPlatform()
}

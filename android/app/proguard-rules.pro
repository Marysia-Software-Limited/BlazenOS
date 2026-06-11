# Keep JNI bridge classes — names are matched by C++ symbol lookup
# (Java_os_blazen_jessica_core_JessicaCoreNative_*).
-keep class os.blazen.jessica.core.JessicaCoreNative { *; }
-keepclassmembers class os.blazen.jessica.core.JessicaCoreNative {
    native <methods>;
}

# Keep data classes used over JNI (serde JSON round-trips).
-keep class os.blazen.jessica.core.IntentMatch { *; }
-keep class os.blazen.jessica.core.IntentMatch$* { *; }

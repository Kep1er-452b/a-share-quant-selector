# Add project specific ProGuard rules here.
# You can control the set of applied configuration files using the
# proguardFiles setting in build.gradle.
#
# For more details, see
#   http://developer.android.com/guide/developing/tools/proguard.html

# If your project uses WebView with JS, uncomment the following
# and specify the fully qualified class name to the JavaScript interface
# class:
#-keepclassmembers class fqcn.of.javascript.interface.for.webview {
#   public *;
#}

# Uncomment this to preserve the line number information for
# debugging stack traces.
#-keepattributes SourceFile,LineNumberTable

# If you keep the line number information, uncomment this to
# hide the original source file name.
#-renameSourcefileattribute SourceFile

# Chaquopy
-keep class com.chaquo.** { *; }
-keepclassmembers class com.chaquo.** { *; }

# Python
-keep class org.python.** { *; }
-keepclassmembers class org.python.** { *; }

# Flask
-keep class flask.** { *; }
-keepclassmembers class flask.** { *; }

# Pandas
-keep class pandas.** { *; }
-keepclassmembers class pandas.** { *; }

# NumPy
-keep class numpy.** { *; }
-keepclassmembers class numpy.** { *; }

# AkShare
-keep class akshare.** { *; }
-keepclassmembers class akshare.** { *; }

# Tushare
-keep class tushare.** { *; }
-keepclassmembers class tushare.** { *; }

# Keep Python entry points
-keep class com.quant.aselector.FlaskService { *; }
-keep class com.quant.aselector.NotificationHelper { *; }

# Keep native methods
-keepclasseswithmembernames class * {
    native <methods>;
}

# Keep JavaScript interface for WebView
-keepclassmembers class * {
    @android.webkit.JavascriptInterface <methods>;
}

# Optimize
-optimizations !code/simplification/arithmetic,!code/simplification/cast,!field/*,!class/merging/*
-optimizationpasses 5
-allowaccessmodification

# Remove logging in release
-assumenosideeffects class android.util.Log {
    public static int v(...);
    public static int d(...);
    public static int i(...);
}

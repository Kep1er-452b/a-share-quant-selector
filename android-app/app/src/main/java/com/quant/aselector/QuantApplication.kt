package com.quant.aselector

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.os.Build
import android.util.Log
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform

class QuantApplication : Application() {

    companion object {
        private const val TAG = "QuantApplication"
        const val CHANNEL_ID_SERVICE = "quant_service_channel"
        const val CHANNEL_ID_NOTIFICATION = "quant_notification_channel"
        
        lateinit var instance: QuantApplication
            private set
    }

    override fun onCreate() {
        super.onCreate()
        instance = this
        
        // 初始化Python
        initPython()
        
        // 创建通知渠道
        createNotificationChannels()
        
        Log.d(TAG, "Application initialized")
    }

    private fun initPython() {
        if (!Python.isStarted()) {
            Python.start(AndroidPlatform(this))
            Log.d(TAG, "Python initialized")
        }
    }

    private fun createNotificationChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val manager = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            
            // 服务通知渠道（前台服务用）
            val serviceChannel = NotificationChannel(
                CHANNEL_ID_SERVICE,
                "量化选股服务",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "用于显示量化选股服务运行状态"
                setShowBadge(false)
            }
            manager.createNotificationChannel(serviceChannel)
            
            // 选股结果通知渠道
            val notificationChannel = NotificationChannel(
                CHANNEL_ID_NOTIFICATION,
                "选股结果通知",
                NotificationManager.IMPORTANCE_DEFAULT
            ).apply {
                description = "用于推送选股结果和系统通知"
                enableVibration(true)
            }
            manager.createNotificationChannel(notificationChannel)
        }
    }
}

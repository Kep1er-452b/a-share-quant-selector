package com.quant.aselector

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat
import com.chaquo.python.PyObject
import com.chaquo.python.Python
import kotlinx.coroutines.*

class FlaskService : Service() {

    companion object {
        private const val TAG = "FlaskService"
        private const val NOTIFICATION_ID = 1001
        private const val FLASK_PORT = 5080
    }

    private val binder = LocalBinder()
    private val serviceScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    private var flaskJob: Job? = null
    private var python: Python? = null
    private var webServerModule: PyObject? = null
    private var isRunning = false

    inner class LocalBinder : Binder() {
        fun getService(): FlaskService = this@FlaskService
    }

    override fun onBind(intent: Intent): IBinder {
        return binder
    }

    override fun onCreate() {
        super.onCreate()
        python = Python.getInstance()
        Log.d(TAG, "FlaskService created")
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        Log.d(TAG, "FlaskService onStartCommand")
        startForeground(NOTIFICATION_ID, createNotification("量化选股服务运行中"))
        return START_STICKY
    }

    override fun onDestroy() {
        super.onDestroy()
        stopFlask()
        serviceScope.cancel()
        Log.d(TAG, "FlaskService destroyed")
    }

    fun startFlask(callback: (Boolean) -> Unit) {
        if (isRunning) {
            callback(true)
            return
        }

        flaskJob = serviceScope.launch {
            try {
                Log.d(TAG, "Starting Flask server...")
                
                // 获取Python模块
                val sys = python?.getModule("sys")
                
                // 添加Python路径
                val appPath = filesDir.absolutePath + "/python"
                sys?.callAttr("path", "insert", 0, appPath)
                
                // 导入web_server模块
                webServerModule = python?.getModule("web_server")
                
                // 配置Flask
                webServerModule?.callAttr(
                    "configure_for_android",
                    filesDir.absolutePath,
                    FLASK_PORT
                )
                
                // 启动Flask服务
                withContext(Dispatchers.IO) {
                    webServerModule?.callAttr("run_flask_server")
                }
                
                isRunning = true
                Log.d(TAG, "Flask server started on port $FLASK_PORT")
                
                withContext(Dispatchers.Main) {
                    callback(true)
                }
            } catch (e: Exception) {
                Log.e(TAG, "Failed to start Flask server", e)
                isRunning = false
                
                withContext(Dispatchers.Main) {
                    callback(false)
                }
            }
        }
    }

    fun stopFlask() {
        if (!isRunning) return
        
        flaskJob?.cancel()
        
        serviceScope.launch {
            try {
                webServerModule?.callAttr("shutdown_server")
                Log.d(TAG, "Flask server stopped")
            } catch (e: Exception) {
                Log.e(TAG, "Error stopping Flask server", e)
            } finally {
                isRunning = false
            }
        }
    }

    fun isFlaskRunning(): Boolean {
        return isRunning
    }

    fun getFlaskPort(): Int {
        return FLASK_PORT
    }

    private fun createNotification(content: String): Notification {
        val pendingIntent = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )

        return NotificationCompat.Builder(this, QuantApplication.CHANNEL_ID_SERVICE)
            .setContentTitle("A股量化选股")
            .setContentText(content)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentIntent(pendingIntent)
            .setOngoing(true)
            .build()
    }

    fun updateNotification(content: String) {
        val notification = createNotification(content)
        val notificationManager = getSystemService(NOTIFICATION_SERVICE) as android.app.NotificationManager
        notificationManager.notify(NOTIFICATION_ID, notification)
    }
}

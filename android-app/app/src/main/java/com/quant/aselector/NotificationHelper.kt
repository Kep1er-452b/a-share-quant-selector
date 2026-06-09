package com.quant.aselector

import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.work.*
import java.util.concurrent.TimeUnit

class NotificationHelper(private val context: Context) {

    companion object {
        private const val TAG = "NotificationHelper"
        private const val NOTIFICATION_ID_SELECTION = 2001
        private const val NOTIFICATION_ID_UPDATE = 2002
        private const val NOTIFICATION_ID_ERROR = 2003
    }

    private val notificationManager = 
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager

    /**
     * 发送选股结果通知
     */
    fun sendSelectionResultNotification(
        title: String,
        content: String,
        stockCount: Int,
        strategyName: String
    ) {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
            putExtra("page", "selection")
        }
        
        val pendingIntent = PendingIntent.getActivity(
            context,
            0,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val notification = NotificationCompat.Builder(context, QuantApplication.CHANNEL_ID_NOTIFICATION)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(content)
            .setStyle(
                NotificationCompat.BigTextStyle()
                    .bigText("$content\n策略: $strategyName\n选出: ${stockCount}只")
            )
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .build()

        notificationManager.notify(NOTIFICATION_ID_SELECTION, notification)
    }

    /**
     * 发送数据更新通知
     */
    fun sendUpdateNotification(
        title: String,
        content: String,
        progress: Int = -1
    ) {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
            putExtra("page", "update")
        }
        
        val pendingIntent = PendingIntent.getActivity(
            context,
            0,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val builder = NotificationCompat.Builder(context, QuantApplication.CHANNEL_ID_NOTIFICATION)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(content)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)

        if (progress >= 0) {
            builder.setProgress(100, progress, false)
        }

        notificationManager.notify(NOTIFICATION_ID_UPDATE, builder.build())
    }

    /**
     * 发送错误通知
     */
    fun sendErrorNotification(
        title: String,
        content: String,
        errorMessage: String
    ) {
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK
            putExtra("page", "error")
        }
        
        val pendingIntent = PendingIntent.getActivity(
            context,
            0,
            intent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val notification = NotificationCompat.Builder(context, QuantApplication.CHANNEL_ID_NOTIFICATION)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(content)
            .setStyle(
                NotificationCompat.BigTextStyle()
                    .bigText("$content\n错误详情: $errorMessage")
            )
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .build()

        notificationManager.notify(NOTIFICATION_ID_ERROR, notification)
    }

    /**
     * 取消所有通知
     */
    fun cancelAllNotifications() {
        notificationManager.cancelAll()
    }

    /**
     * 取消特定通知
     */
    fun cancelNotification(notificationId: Int) {
        notificationManager.cancel(notificationId)
    }
}

/**
 * 后台任务Worker，用于定时执行选股任务
 */
class SelectionWorker(
    context: Context,
    params: WorkerParameters
) : CoroutineWorker(context, params) {

    companion object {
        private const val TAG = "SelectionWorker"
        const val WORK_NAME = "selection_work"
        
        fun enqueueOneTimeWork(context: Context) {
            val workRequest = OneTimeWorkRequestBuilder<SelectionWorker>()
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .build()
            
            WorkManager.getInstance(context)
                .enqueueUniqueWork(
                    WORK_NAME,
                    ExistingWorkPolicy.REPLACE,
                    workRequest
                )
        }
        
        fun enqueuePeriodicWork(context: Context) {
            val workRequest = PeriodicWorkRequestBuilder<SelectionWorker>(
                1, TimeUnit.DAYS
            )
                .setInitialDelay(calculateInitialDelay(), TimeUnit.MILLISECONDS)
                .setConstraints(
                    Constraints.Builder()
                        .setRequiredNetworkType(NetworkType.CONNECTED)
                        .build()
                )
                .build()
            
            WorkManager.getInstance(context)
                .enqueueUniquePeriodicWork(
                    WORK_NAME,
                    ExistingPeriodicWorkPolicy.KEEP,
                    workRequest
                )
        }
        
        private fun calculateInitialDelay(): Long {
            // 计算到下一个15:05的延迟
            val now = java.util.Calendar.getInstance()
            val target = java.util.Calendar.getInstance().apply {
                set(java.util.Calendar.HOUR_OF_DAY, 15)
                set(java.util.Calendar.MINUTE, 5)
                set(java.util.Calendar.SECOND, 0)
                
                if (before(now)) {
                    add(java.util.Calendar.DAY_OF_MONTH, 1)
                }
            }
            
            return target.timeInMillis - now.timeInMillis
        }
    }

    override suspend fun doWork(): Result {
        return try {
            Log.d(TAG, "Starting selection work...")
            
            val notificationHelper = NotificationHelper(applicationContext)
            notificationHelper.sendUpdateNotification(
                "选股任务执行中",
                "正在执行选股策略...",
                0
            )
            
            // 这里调用Python选股逻辑
            val python = Python.getInstance()
            val sys = python.getModule("sys")
            val appPath = applicationContext.filesDir.absolutePath + "/python"
            sys.callAttr("path", "insert", 0, appPath)
            
            val mainModule = python.getModule("main")
            val result = mainModule.callAttr("run_selection_android")
            
            val resultMap = result.toMap(Map::class.java)
            val stockCount = resultMap["count"] as? Int ?: 0
            val strategyName = resultMap["strategy"] as? String ?: "Unknown"
            
            notificationHelper.sendSelectionResultNotification(
                "选股完成",
                "共选出 ${stockCount} 只股票",
                stockCount,
                strategyName
            )
            
            Log.d(TAG, "Selection work completed: $stockCount stocks")
            Result.success()
        } catch (e: Exception) {
            Log.e(TAG, "Selection work failed", e)
            
            val notificationHelper = NotificationHelper(applicationContext)
            notificationHelper.sendErrorNotification(
                "选股失败",
                "选股任务执行失败",
                e.message ?: "未知错误"
            )
            
            Result.failure()
        }
    }
}

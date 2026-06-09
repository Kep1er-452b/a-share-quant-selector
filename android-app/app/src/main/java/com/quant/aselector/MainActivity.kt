package com.quant.aselector

import android.Manifest
import android.annotation.SuppressLint
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.Bundle
import android.os.IBinder
import android.util.Log
import android.view.View
import android.webkit.WebChromeClient
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.quant.aselector.databinding.ActivityMainBinding

class MainActivity : AppCompatActivity() {

    companion object {
        private const val TAG = "MainActivity"
        private const val PERMISSION_REQUEST_CODE = 1001
        private const val FLASK_URL = "http://127.0.0.1:5080"
    }

    private lateinit var binding: ActivityMainBinding
    private var flaskService: FlaskService? = null
    private var isBound = false
    private var isFlaskStarted = false

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, service: IBinder?) {
            val binder = service as FlaskService.LocalBinder
            flaskService = binder.getService()
            isBound = true
            Log.d(TAG, "FlaskService bound")
            
            // 启动Flask服务
            startFlaskServer()
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            flaskService = null
            isBound = false
            Log.d(TAG, "FlaskService unbound")
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        // 请求权限
        requestPermissions()
        
        // 初始化WebView
        setupWebView()
        
        // 绑定Flask服务
        bindFlaskService()
        
        // 设置刷新按钮
        binding.btnRefresh.setOnClickListener {
            binding.webView.reload()
        }
        
        // 设置停止按钮
        binding.btnStop.setOnClickListener {
            stopFlaskService()
        }
    }

    override fun onResume() {
        super.onResume()
        if (isFlaskStarted) {
            binding.webView.reload()
        }
    }

    override fun onDestroy() {
        super.onDestroy()
        if (isBound) {
            unbindService(connection)
            isBound = false
        }
    }

    private fun requestPermissions() {
        val permissions = mutableListOf<String>()
        
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.INTERNET) 
            != PackageManager.PERMISSION_GRANTED) {
            permissions.add(Manifest.permission.INTERNET)
        }
        
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) 
                != PackageManager.PERMISSION_GRANTED) {
                permissions.add(Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        
        if (permissions.isNotEmpty()) {
            ActivityCompat.requestPermissions(
                this,
                permissions.toTypedArray(),
                PERMISSION_REQUEST_CODE
            )
        }
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun setupWebView() {
        binding.webView.apply {
            settings.apply {
                javaScriptEnabled = true
                domStorageEnabled = true
                allowFileAccess = true
                allowContentAccess = true
                mixedContentMode = WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
                cacheMode = WebSettings.LOAD_DEFAULT
                useWideViewPort = true
                loadWithOverviewMode = true
                setSupportZoom(true)
                builtInZoomControls = true
                displayZoomControls = false
            }
            
            webViewClient = object : WebViewClient() {
                override fun onPageFinished(view: WebView?, url: String?) {
                    super.onPageFinished(view, url)
                    binding.progressBar.visibility = View.GONE
                    binding.tvStatus.text = "就绪"
                }
                
                override fun onReceivedError(
                    view: WebView?,
                    errorCode: Int,
                    description: String?,
                    failingUrl: String?
                ) {
                    super.onReceivedError(view, errorCode, description, failingUrl)
                    Log.e(TAG, "WebView error: $errorCode - $description")
                    binding.tvStatus.text = "加载失败: $description"
                }
            }
            
            webChromeClient = object : WebChromeClient() {
                override fun onProgressChanged(view: WebView?, newProgress: Int) {
                    super.onProgressChanged(view, newProgress)
                    binding.progressBar.progress = newProgress
                    if (newProgress < 100) {
                        binding.progressBar.visibility = View.VISIBLE
                        binding.tvStatus.text = "加载中... $newProgress%"
                    }
                }
            }
        }
    }

    private fun bindFlaskService() {
        Intent(this, FlaskService::class.java).also { intent ->
            bindService(intent, connection, Context.BIND_AUTO_CREATE)
        }
    }

    private fun startFlaskServer() {
        flaskService?.let { service ->
            if (!isFlaskStarted) {
                service.startFlask { success ->
                    runOnUiThread {
                        if (success) {
                            isFlaskStarted = true
                            binding.tvStatus.text = "Flask服务已启动"
                            loadFlaskUrl()
                        } else {
                            binding.tvStatus.text = "Flask服务启动失败"
                            Toast.makeText(this, "Flask服务启动失败", Toast.LENGTH_SHORT).show()
                        }
                    }
                }
            } else {
                loadFlaskUrl()
            }
        }
    }

    private fun loadFlaskUrl() {
        if (isNetworkAvailable()) {
            binding.webView.loadUrl(FLASK_URL)
        } else {
            binding.tvStatus.text = "网络不可用"
            Toast.makeText(this, "请检查网络连接", Toast.LENGTH_SHORT).show()
        }
    }

    private fun stopFlaskService() {
        flaskService?.stopFlask()
        isFlaskStarted = false
        binding.tvStatus.text = "服务已停止"
        binding.webView.loadUrl("about:blank")
    }

    private fun isNetworkAvailable(): Boolean {
        val connectivityManager = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val network = connectivityManager.activeNetwork ?: return false
        val capabilities = connectivityManager.getNetworkCapabilities(network) ?: return false
        return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
    }

    override fun onBackPressed() {
        if (binding.webView.canGoBack()) {
            binding.webView.goBack()
        } else {
            super.onBackPressed()
        }
    }
}

# 密钥配置指南

## 概述

本文档说明如何在Android版A股量化选股系统中配置Tushare和DeepSeek密钥。

## 密钥说明

### 1. Tushare Token

**用途**：用于获取A股历史数据和实时行情

**获取方式**：
1. 访问 https://tushare.pro/
2. 注册账号并登录
3. 在个人中心获取Token

**配置位置**：
- 环境变量：`TUSHARE_TOKEN`
- 配置文件：`config/config.yaml`

### 2. DeepSeek API Key

**用途**：用于威科夫AI分析功能

**获取方式**：
1. 访问 https://platform.deepseek.com/
2. 注册账号并登录
3. 在API密钥页面创建新密钥

**配置位置**：
- 环境变量：`DEEPSEEK_API_KEY`
- 配置文件：`config/config.yaml`

## 配置方法

### 方法一：修改Python代码（推荐）

由于本系统仅供个人使用，可以直接将密钥硬编码到Python代码中。

#### 1.1 修改 `android_adapter.py`

打开文件 `app/src/main/python/android_adapter.py`，找到以下代码：

```python
def get_android_config():
    """获取Android配置"""
    global _android_config
    
    if _android_config is None:
        data_dir = get_android_data_dir()
        
        # 默认配置
        _android_config = {
            'data_dir': os.path.join(data_dir, 'data'),
            'web': {
                'host': '127.0.0.1',
                'port': 5080,
                'auto_port': False,
                'allow_lan': False
            },
            'data_source': {
                'default_provider': 'akshare',
                'tushare': {
                    'token': os.environ.get('TUSHARE_TOKEN', '')
                },
                'akshare': {
                    'allow_mock_data': False
                }
            },
            'dingtalk': {
                'enabled': False,
                'webhook_url': '',
                'secret': ''
            },
            'wyckoff_ai': {
                'provider': 'deepseek',
                'base_url': 'https://api.deepseek.com',
                'model': 'deepseek-v4-pro',
                'timeout_seconds': 90,
                'deepseek_api_key': os.environ.get('DEEPSEEK_API_KEY', '')
            },
            'selection': {
                'mode': 'parallel',
                'backend': 'thread',
                'max_workers': 4,
                'chunk_size': 50
            }
        }
    
    return _android_config
```

将密钥替换为你的实际密钥：

```python
'tushare': {
    'token': 'YOUR_ACTUAL_TUSHARE_TOKEN'  # 替换为你的Tushare Token
},
```

```python
'deepseek_api_key': 'YOUR_ACTUAL_DEEPSEEK_API_KEY'  # 替换为你的DeepSeek API Key
```

#### 1.2 示例配置

```python
# Tushare Token示例
'tushare': {
    'token': 'abc123def456ghi789jkl012mno345pqr678stu901vwx234yz'
},

# DeepSeek API Key示例
'deepseek_api_key': 'sk-abcdefghijklmnopqrstuvwxyz1234567890'
```

### 方法二：修改配置文件模板

#### 2.1 修改 `config.yaml.template`

打开文件 `app/src/main/assets/config/config.yaml.template`，修改以下内容：

```yaml
# 数据源配置
data_source:
  default_provider: "akshare"
  tushare:
    token: "YOUR_ACTUAL_TUSHARE_TOKEN"  # 替换为你的Tushare Token
  akshare:
    allow_mock_data: false

# 威科夫 AI 分析配置
wyckoff_ai:
  provider: "deepseek"
  base_url: "https://api.deepseek.com"
  model: "deepseek-v4-pro"
  timeout_seconds: 90
  deepseek_api_key: "YOUR_ACTUAL_DEEPSEEK_API_KEY"  # 替换为你的DeepSeek API Key
```

#### 2.2 重新构建APK

修改配置文件后，需要重新构建APK：

```bash
cd android-app
./scripts/build_apk.sh debug
```

### 方法三：运行时配置（高级）

如果需要运行时配置密钥，可以添加一个配置界面。

#### 3.1 添加配置Activity

创建 `SettingsActivity.kt`：

```kotlin
package com.quant.aselector

import android.os.Bundle
import android.widget.Button
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity

class SettingsActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_settings)
        
        val etTushareToken = findViewById<EditText>(R.id.etTushareToken)
        val etDeepseekKey = findViewById<EditText>(R.id.etDeepseekKey)
        val btnSave = findViewById<Button>(R.id.btnSave)
        
        // 加载已保存的密钥
        val prefs = getSharedPreferences("quant_config", MODE_PRIVATE)
        etTushareToken.setText(prefs.getString("tushare_token", ""))
        etDeepseekKey.setText(prefs.getString("deepseek_api_key", ""))
        
        btnSave.setOnClickListener {
            val tushareToken = etTushareToken.text.toString().trim()
            val deepseekKey = etDeepseekKey.text.toString().trim()
            
            // 保存密钥
            prefs.edit()
                .putString("tushare_token", tushareToken)
                .putString("deepseek_api_key", deepseekKey)
                .apply()
            
            Toast.makeText(this, "配置已保存，请重启应用", Toast.LENGTH_SHORT).show()
            finish()
        }
    }
}
```

#### 3.2 添加布局文件

创建 `activity_settings.xml`：

```xml
<?xml version="1.0" encoding="utf-8"?>
<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"
    android:layout_width="match_parent"
    android:layout_height="match_parent"
    android:orientation="vertical"
    android:padding="16dp">

    <TextView
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="密钥配置"
        android:textSize="24sp"
        android:textStyle="bold"
        android:layout_marginBottom="24dp" />

    <TextView
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="Tushare Token"
        android:textSize="16sp"
        android:layout_marginBottom="8dp" />

    <EditText
        android:id="@+id/etTushareToken"
        android:layout_width="match_parent"
        android:layout_height="wrap_content"
        android:hint="请输入Tushare Token"
        android:inputType="textPassword"
        android:layout_marginBottom="16dp" />

    <TextView
        android:layout_width="wrap_content"
        android:layout_height="wrap_content"
        android:text="DeepSeek API Key"
        android:textSize="16sp"
        android:layout_marginBottom="8dp" />

    <EditText
        android:id="@+id/etDeepseekKey"
        android:layout_width="match_parent"
        android:layout_height="wrap_content"
        android:hint="请输入DeepSeek API Key"
        android:inputType="textPassword"
        android:layout_marginBottom="24dp" />

    <Button
        android:id="@+id/btnSave"
        android:layout_width="match_parent"
        android:layout_height="wrap_content"
        android:text="保存配置" />

</LinearLayout>
```

#### 3.3 修改 `android_adapter.py`

修改配置加载逻辑，优先使用SharedPreferences中的密钥：

```python
def get_android_config():
    """获取Android配置"""
    global _android_config
    
    if _android_config is None:
        data_dir = get_android_data_dir()
        
        # 尝试从SharedPreferences读取密钥
        tushare_token = read_shared_preference('tushare_token') or os.environ.get('TUSHARE_TOKEN', '')
        deepseek_key = read_shared_preference('deepseek_api_key') or os.environ.get('DEEPSEEK_API_KEY', '')
        
        # 默认配置
        _android_config = {
            # ... 其他配置 ...
            'data_source': {
                'default_provider': 'akshare',
                'tushare': {
                    'token': tushare_token
                },
                'akshare': {
                    'allow_mock_data': False
                }
            },
            'wyckoff_ai': {
                # ... 其他配置 ...
                'deepseek_api_key': deepseek_key
            },
        }
    
    return _android_config

def read_shared_preference(key):
    """读取SharedPreferences（需要Android JNI调用）"""
    # 这里需要通过JNI调用Android SharedPreferences
    # 暂时返回None，使用环境变量
    return None
```

## 安全注意事项

### 1. 密钥安全

- **不要将密钥提交到版本控制系统**
- **不要分享包含密钥的APK文件**
- **定期更换密钥**

### 2. 存储安全

- 密钥存储在应用内部存储，其他应用无法访问
- 使用Android Keystore可以进一步增强安全性

### 3. 网络安全

- 所有API调用都使用HTTPS
- 验证SSL证书
- 防止中间人攻击

## 验证配置

### 1. 验证Tushare Token

在Python代码中添加验证逻辑：

```python
def verify_tushare_token():
    """验证Tushare Token"""
    import tushare as ts
    
    token = get_android_config()['data_source']['tushare']['token']
    
    try:
        ts.set_token(token)
        pro = ts.pro_api()
        
        # 测试获取交易日历
        df = pro.trade_cal(exchange='SSE', start_date='20260101', end_date='20260110')
        
        if df is not None and not df.empty:
            print("Tushare Token验证成功")
            return True
        else:
            print("Tushare Token验证失败：无法获取数据")
            return False
    except Exception as e:
        print(f"Tushare Token验证失败: {e}")
        return False
```

### 2. 验证DeepSeek API Key

```python
def verify_deepseek_api_key():
    """验证DeepSeek API Key"""
    import openai
    
    api_key = get_android_config()['wyckoff_ai']['deepseek_api_key']
    
    try:
        client = openai.OpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com"
        )
        
        # 测试API调用
        response = client.chat.completions.create(
            model="deepseek-v4-pro",
            messages=[{"role": "user", "content": "Hello"}],
            max_tokens=10
        )
        
        if response and response.choices:
            print("DeepSeek API Key验证成功")
            return True
        else:
            print("DeepSeek API Key验证失败：无法获取响应")
            return False
    except Exception as e:
        print(f"DeepSeek API Key验证失败: {e}")
        return False
```

## 常见问题

### 1. Token无效

**问题**：提示"Token无效"或"认证失败"

**解决**：
- 检查Token是否正确复制
- 确认Token是否过期
- 重新生成Token

### 2. API配额不足

**问题**：提示"API配额不足"或"请求频率过高"

**解决**：
- 检查API配额
- 降低请求频率
- 升级API套餐

### 3. 网络连接失败

**问题**：提示"网络连接失败"

**解决**：
- 检查网络连接
- 检查防火墙设置
- 使用VPN或代理

## 下一步

配置完密钥后，请继续阅读：

- `TESTING_GUIDE.md` - 测试指南
- `TROUBLESHOOTING.md` - 故障排除指南

---

**文档版本**：1.0  
**创建日期**：2026-06-09  
**最后更新**：2026-06-09

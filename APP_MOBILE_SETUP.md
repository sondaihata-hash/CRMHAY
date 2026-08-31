# Hướng dẫn tạo App Mobile CRM cho Sales

## Tổng quan

App mobile này là giải pháp dành cho sales đi thị trường:
- Chỉ cần cài 1 app trên điện thoại
- App tự chứa CRM + local sync + Zalo integration
- Sales không cần cài Python, terminal, hay môi trường kỹ thuật
- Hoạt động trên Android

---

## Yêu cầu hệ thống để build app

### Trên máy tính (phía developer/IT)
- Node.js 18+ (https://nodejs.org/)
- npm hoặc yarn
- Android Studio (nếu build APK)
- Java JDK 11+
- Capacitor CLI

### Trên điện thoại sales
- Android 8.0+
- ~80MB dung lượng trống
- Kết nối mạng ổn định

---

## Bước 1: Chuẩn bị môi trường (trên máy developer)

### 1.1 Cài Node.js
```bash
# Kiểm tra
node --version
npm --version
```

### 1.2 Cài Capacitor CLI
```bash
npm install -g @capacitor/cli
```

### 1.3 Tạo folder project app
```bash
mkdir crm-sales-app
cd crm-sales-app
```

---

## Bước 2: Tạo project React + Vite (giao diện app)

### 2.1 Khởi tạo Vite project
```bash
npm create vite@latest . -- --template react
npm install
```

### 2.2 Cài Capacitor vào project
```bash
npm install @capacitor/core @capacitor/cli
npx cap init
```

Khi prompt yêu cầu:
- **App name**: CRM HAY Sales
- **App ID**: com.crmhay.sales
- **Directory**: .

### 2.3 Cài package Capacitor cho Android
```bash
npm install @capacitor/android
npx cap add android
```

---

## Bước 3: Tạo giao diện mobile cho CRM

### 3.1 Cấu trúc folder
```
crm-sales-app/
├── src/
│   ├── pages/
│   │   ├── Login.jsx           # Màn hình đăng nhập
│   │   ├── Dashboard.jsx       # Dashboard sales
│   │   ├── Customers.jsx       # Danh sách khách hàng
│   │   ├── CustomerDetail.jsx  # Chi tiết khách
│   │   ├── Reminders.jsx       # Nhắc việc
│   │   ├── Orders.jsx          # Đơn hàng
│   │   └── Settings.jsx        # Cài đặt
│   ├── services/
│   │   ├── api.js              # Gọi API tới CRM
│   │   ├── zaloSync.js         # Đồng bộ Zalo
│   │   └── localStorage.js     # Lưu dữ liệu local
│   ├── components/
│   │   ├── Header.jsx
│   │   ├── Navigation.jsx
│   │   └── MessageThread.jsx
│   ├── App.jsx
│   └── main.jsx
├── android/                    # Native Android code
├── capacitor.config.json       # Cấu hình Capacitor
└── package.json
```

### 3.2 File App.jsx cơ bản
```jsx
import { useState, useEffect } from 'react'
import { App as CapApp } from '@capacitor/app'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import './App.css'

function App() {
  const [isLoggedIn, setIsLoggedIn] = useState(false)
  const [user, setUser] = useState(null)

  useEffect(() => {
    // Kiểm tra token lưu trữ
    const token = localStorage.getItem('crm_token')
    if (token) {
      setIsLoggedIn(true)
      setUser(JSON.parse(localStorage.getItem('crm_user') || '{}'))
    }

    // Xử lý back button
    CapApp.addListener('backButton', () => {
      if (!isLoggedIn) {
        CapApp.exitApp()
      }
    })
  }, [])

  const handleLogin = (userData, token) => {
    localStorage.setItem('crm_token', token)
    localStorage.setItem('crm_user', JSON.stringify(userData))
    setUser(userData)
    setIsLoggedIn(true)
  }

  const handleLogout = () => {
    localStorage.clear()
    setUser(null)
    setIsLoggedIn(false)
  }

  return (
    <div className="app">
      {isLoggedIn ? (
        <Dashboard user={user} onLogout={handleLogout} />
      ) : (
        <Login onLogin={handleLogin} />
      )}
    </div>
  )
}

export default App
```

---

## Bước 4: Tích hợp API CRM

### 4.1 File services/api.js
```javascript
const CRM_BASE_URL = 'https://crmhay.cloud'

export const api = {
  async login(username, password) {
    const formData = new FormData()
    formData.append('username', username)
    formData.append('password', password)
    
    const response = await fetch(`${CRM_BASE_URL}/login`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
    })
    
    if (!response.ok) throw new Error('Login failed')
    return response.text()
  },

  async getCustomers() {
    const response = await fetch(`${CRM_BASE_URL}/customers?format=json`, {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('crm_token')}` },
      credentials: 'include',
    })
    if (!response.ok) throw new Error('Failed to fetch customers')
    return response.json()
  },

  async getCustomer(customerId) {
    const response = await fetch(`${CRM_BASE_URL}/customers/${customerId}`, {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('crm_token')}` },
      credentials: 'include',
    })
    if (!response.ok) throw new Error('Failed to fetch customer')
    return response.json()
  },

  async sendZaloMessage(customerId, message) {
    const formData = new FormData()
    formData.append('zalo_message', message)
    formData.append('_csrf_token', localStorage.getItem('csrf_token'))

    const response = await fetch(`${CRM_BASE_URL}/customers/${customerId}/send-zalo`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
    })
    if (!response.ok) throw new Error('Failed to send message')
    return response.json()
  },

  async createReminder(customerId, data) {
    const formData = new FormData()
    Object.keys(data).forEach(key => {
      formData.append(key, data[key])
    })
    formData.append('_csrf_token', localStorage.getItem('csrf_token'))

    const response = await fetch(`${CRM_BASE_URL}/customers/${customerId}/reminder`, {
      method: 'POST',
      body: formData,
      credentials: 'include',
    })
    if (!response.ok) throw new Error('Failed to create reminder')
    return response.json()
  },

  async webhookZalo(payload) {
    const response = await fetch(`${CRM_BASE_URL}/api/zalo/webhook`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    })
    return response.json()
  }
}
```

---

## Bước 5: Tích hợp Zalo Local Sync

### 5.1 File services/zaloSync.js
```javascript
import { Plugins } from '@capacitor/core'

const { App } = Plugins

export const zaloSync = {
  async initListener() {
    // Nghe notification từ Zalo
    // Dùng Notification Plugin để catch Zalo messages
    console.log('Zalo sync initialized')
  },

  async syncMessage(phoneNumber, senderName, messageText) {
    // Đẩy tin nhắn lên CRM
    const response = await fetch('https://crmhay.cloud/api/zalo/webhook', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        customer_phone: phoneNumber,
        name: senderName,
        message: messageText,
        channel: 'zalo_personal',
        source: 'mobile_app',
        sent_at: new Date().toISOString(),
      }),
    })
    return response.json()
  },

  async sendReply(customerId, message) {
    // Gửi trả lời từ app
    return fetch(`https://crmhay.cloud/customers/${customerId}/send-zalo`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        zalo_message: message,
      }),
    })
  }
}
```

---

## Bước 6: Build APK cho Android

### 6.1 Chuẩn bị build
```bash
# Build web assets
npm run build

# Copy vào Capacitor
npx cap copy android

# Open Android Studio để build APK
npx cap open android
```

### 6.2 Trong Android Studio
1. Mở project `android/`
2. Menu: Build → Generate Signed Bundle / APK
3. Chọn APK
4. Tạo keystore hoặc dùng existing
5. Chọn Build Variant: release
6. Build APK

### 6.3 APK sẽ nằm tại
```
android/app/release/app-release.apk
```

---

## Bước 7: Cài app trên điện thoại sales

### 7.1 Cách 1: Qua USB cable
```bash
# Connect điện thoại
adb install android/app/release/app-release.apk
```

### 7.2 Cách 2: Gửi file APK
- Copy file `app-release.apk` sang điện thoại
- Mở file manager
- Bấm vào APK file
- Cho phép cài app

### 7.3 Cách 3: Upload lên server
- Upload APK lên CRM (tạo route `/downloads/app`)
- Sales tải về từ CRM
- Cài app

---

## Bước 8: Sales sử dụng app

### 8.1 Khi mở app lần đầu
1. Nhập username (tài khoản sales được admin tạo)
2. Nhập password
3. Bấm Đăng nhập

### 8.2 App sẽ
- Lưu token vào local storage
- Hiển thị dashboard
- Bắt đầu nghe Zalo notification

### 8.3 Sales làm việc
- Mở app mỗi ngày
- Xem danh sách khách hàng
- Xem nhắc việc
- Xem tin nhắn từ Zalo cá nhân (sync tự động)
- Trả lời khách từ app

---

## Cấu hình thêm (tùy chọn)

### Push Notification
Cài Capacitor Push Notifications:
```bash
npm install @capacitor/push-notifications
npx cap sync
```

### Background Sync
Cài Capacitor Background Tasks:
```bash
npm install @capacitor/background-tasks
npx cap sync
```

---

## Troubleshooting

### App không kết nối được CRM
- Kiểm tra domain: https://crmhay.cloud
- Kiểm tra internet
- Kiểm tra CORS trên server

### Zalo notification không thấy
- Cần permission `RECEIVE_NOTIFICATIONS` trên Android
- Cần cài `Capacitor Push Notifications` plugin

### APK quá lớn
- Dùng minify: `npm run build`
- Dùng Proguard trên Android

---

## File cần chuẩn bị

1. ✅ Landing page (đã có)
2. ✅ Backend CRM (đã có)
3. ❌ Mobile app code (tạo từ guide này)
4. ❌ Build APK
5. ❌ Deploy lên server

---

## Kế tiếp

Khi bạn đã setup xong môi trường và tạo project, tôi sẽ giúp bạn:
1. Tạo chi tiết giao diện từng trang
2. Tích hợp API
3. Test trên emulator
4. Build và test trên điện thoại thật
5. Tối ưu hóa performance

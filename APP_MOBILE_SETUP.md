# Hướng dẫn sử dụng CRM HAY trên Android và iPhone

## Truy cập CRM trên điện thoại

Mở trình duyệt và truy cập:

```text
https://crmhay.cloud
```

Đăng nhập bằng tài khoản Sales được cấp. CRM HAY có hai cách sử dụng:

- **Android:** dùng APK CRM HAY Mobile hoặc mở trực tiếp bằng trình duyệt.
- **iPhone:** mở bằng Safari và thêm CRM vào Màn hình chính như một app web.

## Cài CRM như app trên iPhone

1. Mở `https://crmhay.cloud` bằng **Safari**.
2. Đăng nhập CRM.
3. Bấm nút **Chia sẻ** trên Safari.
4. Chọn **Thêm vào Màn hình chính**.
5. Bấm **Thêm**.
6. Mở biểu tượng CRM HAY từ Màn hình chính ở những lần sau.

Đây là app web/PWA, không phải ứng dụng iOS đóng gói từ App Store. iPhone
không cần cài app SIP và khách hàng cũng không cần cài ứng dụng nào.

## Cài và cập nhật trên Android

### Cách 1: cập nhật bằng APK CRM HAY Mobile

1. Mở CRM HAY Mobile.
2. Vào **Tài khoản**.
3. Bấm **Kiểm tra cập nhật app**.
4. Xác nhận tải bản mới.
5. Mở file APK và chọn **Cài đặt/Cập nhật**.

APK hiện tại:

```text
https://crmhay.cloud/downloads/crmhay-mobile.apk
```

### Cách 2: dùng trình duyệt

Mở Chrome, truy cập `https://crmhay.cloud`, đăng nhập và có thể chọn
**Thêm vào màn hình chính**.

## Gọi điện và ghi nhật ký trên iPhone

1. Vào **Khách hàng** và mở khách cần gọi.
2. Bấm **Gọi điện**.
3. CRM lưu thời gian bắt đầu rồi mở ứng dụng Điện thoại của iPhone.
4. Thực hiện cuộc gọi như bình thường.
5. Quay lại CRM.
6. Khi CRM hỏi **“Cuộc gọi với khách hàng đã kết thúc?”**, chỉ bấm **OK** sau
   khi cuộc gọi thực sự kết thúc.

CRM sẽ lưu thời gian bắt đầu, thời gian Sales xác nhận kết thúc, thời lượng
ước tính, Sales thực hiện và ghi chú. Nếu chưa kết thúc, chọn **Hủy**; nhật ký
vẫn ở trạng thái chờ để xác nhận sau.

> iOS không cho ứng dụng đọc Call Log hoặc biết chính xác lúc cuộc gọi SIM
> kết thúc. Vì vậy thời gian kết thúc trên iPhone là thời điểm Sales bấm **OK**.

## Gọi điện và ghi nhật ký trên Android

1. Mở CRM HAY Mobile và đăng nhập.
2. Vào **Khách hàng**, mở khách rồi bấm **Gọi điện**.
3. Cho phép quyền **đọc nhật ký cuộc gọi** khi Android hỏi.
4. Gọi khách như bình thường.
5. Quay lại CRM.

Android sẽ đọc cuộc gọi gần nhất từ Call Log và tự đồng bộ thời gian bắt đầu,
thời gian kết thúc, thời lượng và trạng thái cuộc gọi. Nếu không cấp quyền,
CRM vẫn ghi nhận cuộc gọi nhưng không thể lấy thời lượng tự động.

## Xem lịch sử cuộc gọi

Admin mở hồ sơ khách hàng trên CRM để xem **Nhật ký chăm sóc**. Mỗi dòng có:

- Sales thực hiện;
- thời gian bắt đầu;
- thời gian kết thúc hoặc trạng thái chờ;
- thời lượng;
- ghi chú;
- trạng thái cuộc gọi nhỡ/từ chối/đã hoàn tất.

## Lưu ý sử dụng

- Không đóng hoặc xóa dữ liệu trình duyệt khi còn cuộc gọi đang chờ xác nhận.
- iPhone cần quay lại đúng trình duyệt CRM sau cuộc gọi để xác nhận kết thúc.
- Android cần bật quyền đọc nhật ký cuộc gọi cho CRM Mobile.
- Cuộc gọi vẫn là cuộc gọi SIM thông thường; khách hàng không cần cài app.
- Tài khoản Sales chỉ xem và ghi nhật ký khách được phân công; Admin xem được
  toàn bộ lịch sử.

---

# Hướng dẫn kỹ thuật tạo App Mobile CRM cho Sales

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

async function requestJson(path, options = {}) {
  const controller = new AbortController()
  const timeoutId = setTimeout(() => controller.abort(), 30000)
  try {
    const response = await fetch(`${CRM_BASE_URL}${path}`, {
      ...options,
      signal: controller.signal,
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    })
    const data = await response.json()
    if (!response.ok) throw new Error(data.error || data.message || `HTTP ${response.status}`)
    return data
  } finally {
    clearTimeout(timeoutId)
  }
}

export const api = {
  async login(username, password) {
    const data = await requestJson('/api/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    })
    localStorage.setItem('crm_token', data.token)
    return data
  },

  async getCustomers() {
    return requestJson('/api/customers', {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('crm_token')}` },
    })
  },

  async getCustomer(customerId) {
    return requestJson(`/api/customers/${customerId}`, {
      headers: { 'Authorization': `Bearer ${localStorage.getItem('crm_token')}` },
    })
  },
  async logActivity(customerId, type, channel, note = '') {
    return requestJson(`/api/customers/${customerId}/activities`, {
      method: 'POST',
      headers: { 'Authorization': `******'crm_token')}` },
      body: JSON.stringify({ type, channel, note }),
    })
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

### 4.2 Nút cập nhật ứng dụng

Cài plugin mở link tải APK:

```bash
npm install @capacitor/app-launcher
npx cap sync android
```

Thêm nút vào màn hình Cài đặt:

```jsx
import { App } from '@capacitor/app'
import { AppLauncher } from '@capacitor/app-launcher'

async function checkForAppUpdate() {
  const currentVersion = await App.getInfo().then(info => info.version)
  const result = await requestJson('/api/mobile/version')
  if (result.version === currentVersion || !result.download_url) {
    alert('Bạn đang dùng phiên bản mới nhất.')
    return
  }
  const confirmed = window.confirm(`Có bản ${result.version} mới. Cập nhật ngay?`)
  if (confirmed) {
    await AppLauncher.openUrl({ url: result.download_url })
  }
}

<button type="button" onClick={checkForAppUpdate}>
  Cập nhật ứng dụng
</button>
```

Cấu hình link APK và phiên bản trên server:

```text
CRM_MOBILE_VERSION=1.0.1
CRM_MOBILE_MIN_VERSION=1.0.0
CRM_MOBILE_UPDATE_URL=https://crmhay.cloud/downloads/crmhay-sales-1.0.1.apk
```

APK mới phải giữ nguyên package name `com.crmhay.sales`, dùng cùng signing key và tăng
`versionCode`; khi đó Android sẽ cài đè, giữ nguyên dữ liệu, không cần gỡ app cũ.

#### Phát hành APK mới bằng một lệnh

Sau khi build APK release từ mã nguồn mobile, chạy trên PC CRM:

```powershell
.\scripts\publish-mobile-apk.ps1 `
  -ApkPath "C:\duong-dan\app-release.apk" `
  -Version "1.4.1" `
  -VersionCode 5
```

Script chép APK vào tên cố định `downloads\crmhay-mobile.apk`, tạo manifest
`mobile-release.json` và cập nhật phiên bản cho server. Không cần đổi URL trong
ứng dụng. Khởi động lại CRM sau khi phát hành để supervisor nạp biến môi trường;
ứng dụng mobile chỉ cần bấm **Cập nhật ứng dụng**.

Repository CRM hiện chỉ chứa APK phát hành, không chứa mã nguồn Android/React
Native. Muốn tự động build APK sau mỗi thay đổi giao diện, cần đưa thư mục mã
nguồn mobile và quy trình Android signing (keystore) vào máy build/CI; không nên
đưa keystore vào Git.

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

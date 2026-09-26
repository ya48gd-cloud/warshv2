import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import { AuthProvider } from './store/auth'
import { ToastProvider } from './store/toast'

// تعطيل تغيير قيمة خانات الأرقام عند عمل scroll عليها بدون قصد (سلوك متصفح مزعج بشكل افتراضي)
document.addEventListener('wheel', (e) => {
  if (document.activeElement?.type === 'number') document.activeElement.blur()
}, { passive: true })

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <AuthProvider>
      <ToastProvider>
        <App />
      </ToastProvider>
    </AuthProvider>
  </StrictMode>
)

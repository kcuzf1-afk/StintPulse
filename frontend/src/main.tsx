import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import '@fontsource/titillium-web/latin-700.css'
import '@fontsource/titillium-web/latin-900.css'
import './tokens.css'
import './styles.css'
ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
if (import.meta.env.PROD && 'serviceWorker' in navigator)
  navigator.serviceWorker.register('/sw.js').catch(() => {})

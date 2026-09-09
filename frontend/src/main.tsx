import React from 'react'
import ReactDOM from 'react-dom/client'
import { createBrowserRouter, RouterProvider } from 'react-router-dom'
import App from './App'
import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css'
import './styles.css'
import './theme.css'
import 'react-grid-layout/css/styles.css'
import './focused-shell.css'

// Keep the route tree at module scope so the data router is created exactly
// once. App currently owns the legacy feature rendering; later phases can
// split this catch-all route into lazy feature adapters without another router
// migration.
const router = createBrowserRouter([
  { path: '*', Component: App },
])

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
)

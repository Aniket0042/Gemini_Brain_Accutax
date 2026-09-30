import React from 'react'
import ReactDOM from 'react-dom/client'
import { Auth0Provider } from '@auth0/auth0-react'
import App from './App.jsx'
import './index.css'

const domain = import.meta.env.VITE_AUTH0_DOMAIN;
const clientId = import.meta.env.VITE_AUTH0_CLIENT_ID;

// auth0-spa-js throws on a non-secure origin (plain http on a non-localhost
// host), which would blank the whole app. Skip Auth0 there: Accutax SSO
// handoff and the email/password LoginPage don't depend on it, and without
// a provider useAuth0() reports isLoading, so App's Auth0 effect stays idle.
const auth0Enabled = window.isSecureContext && domain && clientId;

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    {auth0Enabled ? (
      <Auth0Provider
        domain={domain}
        clientId={clientId}
        authorizationParams={{
          redirect_uri: window.location.origin
        }}
      >
        <App />
      </Auth0Provider>
    ) : (
      <App />
    )}
  </React.StrictMode>,
)

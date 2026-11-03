import React from 'react';
import ReactDOM from 'react-dom/client';
import SessionRoot from './auth/SessionRoot';
import { installSessionTransport } from './auth/session';
import { ErrorBoundary } from './components/ErrorBoundary';
import './index.css';
installSessionTransport();
ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode><ErrorBoundary><SessionRoot/></ErrorBoundary></React.StrictMode>,
);

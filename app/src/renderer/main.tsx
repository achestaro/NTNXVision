import React from 'react';
import { createRoot } from 'react-dom/client';
// Self-hosted fonts (offline app — no CDN): Figtree UI, JetBrains Mono data, Montserrat 200/300 display numerals.
import '@fontsource-variable/figtree/wght.css';
import '@fontsource/jetbrains-mono/latin-400.css';
import '@fontsource/jetbrains-mono/latin-500.css';
import '@fontsource/montserrat/latin-200.css';
import '@fontsource/montserrat/latin-300.css';
import './styles/tokens.css';
import './styles/app.css';
import './styles/email.css';
import { App } from './App';

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

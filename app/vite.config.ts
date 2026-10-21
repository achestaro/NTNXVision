import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

const DEV_HTTP = 'http://localhost:5273';
const DEV_WS = 'ws://localhost:5273';

/**
 * Content-Security-Policy for the renderer, injected per mode (SEC-14).
 *
 * Production (the packaged app and `vite build`, served as app://ntnxvision): nothing but the app's
 * own files. `style-src` needs 'unsafe-inline' because React components set inline `style`
 * attributes (about 230 of them), which CSP treats as inline styles; scripts stay strictly
 * 'self' (no inline scripts, no eval). Fonts and images are bundled files (assets are never
 * inlined, see build.assetsInlineLimit), with data: allowed for images only.
 *
 * Development (Vite dev server): HMR needs the dev server origin, its websocket, and the
 * inline module that the React Fast Refresh plugin injects.
 */
function cspFor(mode: 'production' | 'development'): string {
  const directives =
    mode === 'production'
      ? [
          "default-src 'none'",
          "script-src 'self'",
          "style-src 'self' 'unsafe-inline'",
          "img-src 'self' data:",
          "font-src 'self'",
          "connect-src 'self'",
          "object-src 'none'",
          "base-uri 'none'",
          "form-action 'none'",
          "frame-src 'none'",
        ]
      : [
          "default-src 'none'",
          `script-src 'self' 'unsafe-inline' ${DEV_HTTP}`,
          `style-src 'self' 'unsafe-inline' ${DEV_HTTP}`,
          `img-src 'self' data: blob: ${DEV_HTTP}`,
          `font-src 'self' data: ${DEV_HTTP}`,
          `connect-src 'self' ${DEV_HTTP} ${DEV_WS}`,
          "object-src 'none'",
          "base-uri 'none'",
          "form-action 'none'",
          "frame-src 'none'",
        ];
  return directives.join('; ');
}

function cspPlugin(): Plugin {
  return {
    name: 'ntnxvision-csp',
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        const policy = cspFor(ctx.server ? 'development' : 'production');
        const meta = `<meta http-equiv="Content-Security-Policy" content="${policy}" />`;
        return html.replace('<meta charset="UTF-8" />', `<meta charset="UTF-8" />\n    ${meta}`);
      },
    },
  };
}

// Renderer-only Vite config. The Electron main process is compiled separately by tsc.
export default defineConfig({
  root: resolve(__dirname, 'src/renderer'),
  base: './',
  plugins: [react(), cspPlugin()],
  resolve: {
    alias: { '@shared': resolve(__dirname, 'src/shared') },
  },
  build: {
    outDir: resolve(__dirname, 'dist/renderer'),
    emptyOutDir: true,
    // Never inline assets as data: URIs: the production CSP allows data: for images only.
    assetsInlineLimit: 0,
  },
  server: { port: 5273 },
});

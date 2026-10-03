import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
const backend='http://127.0.0.1:8877';
const legacyAssets=['/vendor','/app.js','/style.css','/cpu.css','/cpu.html','/cpu.js','/guide.html','/thermal-animation-template.html','/thermal-animation-ui.js','/thermal-studio-logo.png','/workflow-agent-ui.js','/workflow-ui.js'];
export default defineConfig({plugins:[react()],server:{proxy:Object.fromEntries(['/api','/legacy',...legacyAssets].map(path=>[path,backend]))},build:{rollupOptions:{output:{manualChunks:{three:['three'],react:['react','react-dom']}}}}});

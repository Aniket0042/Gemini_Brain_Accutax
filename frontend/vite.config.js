import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({command,mode})=>{
  const env=loadEnv(mode,process.cwd(),'');
  // VITE_AUTH_MODE=legacy (the VM): no BFF; the browser calls this origin's /api with the AI backend's own token.
  const legacy=env.VITE_AUTH_MODE==='legacy';
  if(command==='build'&&mode==='production'&&!legacy){
    if(env.ALLOW_INSECURE_BFF!=='1'){
      let url;
      try{url=new URL(env.VITE_BFF_API_URL);}
      catch{throw new Error('Production AI frontend requires VITE_BFF_API_URL set to the HTTPS Nest API origin');}
      if(url.protocol!=='https:'||url.username||url.password||url.search||url.hash)
        throw new Error('Production AI frontend requires a credential-free HTTPS VITE_BFF_API_URL');
    }
  }
  return {
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // The PDF.js worker ships as .mjs; many servers (nginx 1.20) send that as octet-stream and browsers refuse it.
        assetFileNames: (asset) => (/\.mjs$/.test(asset.names?.[0] || asset.name || '')
          ? 'assets/[name]-[hash].js' : 'assets/[name]-[hash][extname]'),
      },
    },
  },
  server: {
    port: 5174,
    strictPort: true,
    host: true,
    ...(legacy?{proxy:{'/api':{target:'http://127.0.0.1:8000',changeOrigin:true,secure:false}}}:{}),
  },
  };
})

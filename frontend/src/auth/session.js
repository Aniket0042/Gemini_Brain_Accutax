import { createSessionClient,clearLegacyCredentials } from './session-client.mjs';

const base=import.meta.env.VITE_BFF_API_URL||'http://localhost:8081';
const originalFetch=window.fetch.bind(window);
clearLegacyCredentials(localStorage,sessionStorage);
// Old unscoped transcripts must never be displayed under a different identity.
for(const key of ['accutax_chat_history','gemini_brain_active_org','gemini_brain_active_orgs','accutax_active_session'])localStorage.removeItem(key);
export const sessionClient=createSessionClient({apiBase:base,fetcher:originalFetch});
const hubBase=import.meta.env.VITE_HUB_URL||'http://localhost:5176';
// The one sign-in page for every Accutax product (the Hub). It sets the shared session, then returns here.
export function hubSignInUrl(returnTo=window.location.href){
  const url=new URL('/signin',hubBase);
  url.searchParams.set('launch','assistant');
  url.searchParams.set('returnTo',returnTo);
  return url.href;
}
export const aiEndpoint=path=>sessionClient.endpoint(`ai${path}`);
export function installSessionTransport(){
  if(window.__accutaxAiSessionTransport)return;window.__accutaxAiSessionTransport=true;
  window.fetch=async(input,init={})=>{
    const source=typeof input==='object'&&input?.url?input.url:String(input);
    const url=new URL(source,window.location.origin);
    const rewritten=url.origin===window.location.origin&&url.pathname.startsWith('/api/v1/')?aiEndpoint(url.pathname+url.search):source;
    if(!sessionClient.isApi(rewritten))return originalFetch(input,init);
    const requestInput=typeof input==='object'&&input?.url?new Request(rewritten,input):rewritten;
    return sessionClient.observe(await originalFetch(requestInput,await sessionClient.prepare(requestInput,init)));
  };
}
export const tabStorage={
  key:key=>`accutax:${sessionClient.getSnapshot()?.user?.id||'anonymous'}:${key}`,
  getItem(key){return sessionStorage.getItem(this.key(key));},
  setItem(key,value){sessionStorage.setItem(this.key(key),value);},
  removeItem(key){sessionStorage.removeItem(this.key(key));},
};
export function clearFinanceStorage(){
  for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith('accutax:')||key?.startsWith('accutax_')||key?.startsWith('gemini_brain_'))sessionStorage.removeItem(key);}
}

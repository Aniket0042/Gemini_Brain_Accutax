import { createSessionClient,clearLegacyCredentials } from './session-client.mjs';
import { createLegacyClient } from './legacy-client.mjs';

// 'bff' (default): the shared Accutax sign-in through the Nest BFF and AI gateway (EC2 / zereco.ai).
// 'legacy': the AI backend's own login and the dashboard widget handoff, for deployments without the BFF (the VM).
export const AUTH_MODE=import.meta.env.VITE_AUTH_MODE==='legacy'?'legacy':'bff';
const base=import.meta.env.VITE_BFF_API_URL||'http://localhost:8081';
const originalFetch=window.fetch.bind(window);
if(AUTH_MODE==='bff'){
  clearLegacyCredentials(localStorage,sessionStorage);
  // Old unscoped transcripts must never be displayed under a different identity.
  for(const key of ['accutax_chat_history','gemini_brain_active_org','gemini_brain_active_orgs','accutax_active_session'])localStorage.removeItem(key);
}
export const sessionClient=AUTH_MODE==='legacy'
  ?createLegacyClient({fetcher:originalFetch,storage:localStorage})
  :createSessionClient({apiBase:base,fetcher:originalFetch});
/** Headers an XMLHttpRequest to the AI API needs (fetch gets them from the transport below); null: not signed in. */
export function xhrAuthHeaders(){
  if(AUTH_MODE==='legacy')return sessionClient.getSnapshot()?sessionClient.authHeaders():null;
  const csrf=sessionClient.getSnapshot()?.csrfToken;
  return csrf?{'X-CSRF-Token':csrf}:null;
}
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
const bffStorage={
  key:key=>`accutax:${sessionClient.getSnapshot()?.user?.id||'anonymous'}:${key}`,
  getItem(key){return sessionStorage.getItem(this.key(key));},
  setItem(key,value){sessionStorage.setItem(this.key(key),value);},
  removeItem(key){sessionStorage.removeItem(this.key(key));},
};
// Legacy (the VM): chats and the chosen organizations stay where the app always kept them, in the browser's
// local storage under their plain names, so the sidebar still lists earlier chats. Everything else is per tab.
const LEGACY_LOCAL_KEYS=new Set(['accutax_chat_history','gemini_brain_active_org','gemini_brain_active_orgs']);
const legacyArea=key=>LEGACY_LOCAL_KEYS.has(key)?localStorage:sessionStorage;
const legacyStorage={
  getItem:key=>legacyArea(key).getItem(key),
  setItem:(key,value)=>legacyArea(key).setItem(key,value),
  removeItem:key=>legacyArea(key).removeItem(key),
};
export const tabStorage=AUTH_MODE==='legacy'?legacyStorage:bffStorage;
export function clearFinanceStorage(){
  for(let i=sessionStorage.length-1;i>=0;i--){const key=sessionStorage.key(i);if(key?.startsWith('accutax:')||key?.startsWith('accutax_')||key?.startsWith('gemini_brain_'))sessionStorage.removeItem(key);}
}

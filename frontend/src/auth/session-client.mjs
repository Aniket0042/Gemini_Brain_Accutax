// BFF browser contract v1. Keep this module identical in both independently built apps.
export class SessionError extends Error {
  constructor(message,status=0,code=''){super(message);this.name='SessionError';this.status=status;this.code=code;}
}
export function createSessionClient({apiBase,fetcher,location=globalThis.location,onChange=()=>{}}){
  const base=new URL(String(apiBase).replace(/\/$/,'')+'/',location.origin);
  if(base.username||base.password||base.search||base.hash||!['http:','https:'].includes(base.protocol))throw new Error('Invalid BFF API URL');
  if(base.protocol!=='https:'&&!['localhost','127.0.0.1','[::1]'].includes(base.hostname))throw new Error('BFF browser sessions require HTTPS');
  let profile=null,inFlight=null,epoch=0;
  const listeners=new Set();
  const notify=()=>{onChange(profile);for(const listener of listeners)listener(profile);};
  const setProfile=value=>{profile=value?.authenticated===true?value:null;notify();};
  const endpoint=path=>new URL(String(path).replace(/^\//,''),base).href;
  const isApi=input=>{try{const url=new URL(typeof input==='object'&&input?.url?input.url:String(input),location.origin);return url.origin===base.origin&&(base.pathname==='/'||url.pathname===base.pathname.slice(0,-1)||url.pathname.startsWith(base.pathname));}catch{return false;}};
  const read=async response=>{
    const body=await response.json().catch(()=>null);
    if(!response.ok){const code=typeof body?.code==='string'?body.code:'';throw new SessionError(response.status===401?'Your session has expired. Sign in to continue.':response.status===403?'You do not have permission for this action.':response.status>=500?'The sign-in service is temporarily unavailable. Try again.':typeof body?.message==='string'?body.message:'The request could not be completed.',response.status,code);}
    return body;
  };
  const load=({force=false}={})=>{
    if(inFlight&&!force)return inFlight;
    const expected=epoch;
    const pending=fetcher(endpoint('auth/session'),{credentials:'include',cache:'no-store',headers:{Accept:'application/json'}})
      .then(async response=>{
        if(response.status===401){if(expected===epoch)setProfile(null);return null;}
        const body=await read(response);if(expected===epoch)setProfile(body);return expected===epoch?profile:null;
      }).finally(()=>{if(inFlight===pending)inFlight=null;});
    inFlight=pending;return pending;
  };
  const prepare=async(input,init={})=>{
    if(!isApi(input))return init;
    const headers=new Headers(typeof input==='object'&&input?.headers?input.headers:undefined);
    new Headers(init.headers).forEach((value,key)=>headers.set(key,value));
    headers.delete('Authorization');headers.delete('X-Accutax-Delegation');headers.delete('X-Handoff-Service-Key');
    const method=String(init.method||input?.method||'GET').toUpperCase();
    if(!['GET','HEAD','OPTIONS'].includes(method)){
      const current=profile||await load();
      if(current?.csrfToken)headers.set('X-CSRF-Token',current.csrfToken);
    }
    return{...init,headers,credentials:'include',cache:'no-store'};
  };
  const observe=response=>{if(response.status===401){epoch++;setProfile(null);}return response;};
  const request=async(path,init={})=>{const url=endpoint(path);return observe(await fetcher(url,await prepare(url,init)));};
  return{
    endpoint,isApi,prepare,observe,load,getSnapshot:()=>profile,
    subscribe(listener){listeners.add(listener);return()=>listeners.delete(listener);},
    async json(path,init={}){return read(await request(path,init));},request,
    login({returnTo=location.origin+'/',reauthenticate=false}={}){
      const target=new URL(returnTo,location.origin);
      if(target.origin!==location.origin||target.hash)throw new Error('Invalid sign-in return path');
      const url=new URL(endpoint('auth/oidc/login'));url.searchParams.set('returnTo',target.href);
      if(reauthenticate)url.searchParams.set('reauthenticate','1');location.assign(url.href);
    },
    async logout(){
      let body;
      try{body=await read(await request('auth/oidc/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({returnTo:location.origin+'/'})}));}
      catch(error){try{await load({force:true});}catch{/* Preserve uncertainty when offline. */}throw error;}
      epoch++;setProfile(null);
      const url=new URL(body.logoutUrl);
      if(!['https:','http:'].includes(url.protocol)||url.username||url.password)throw new SessionError('The provider sign-out address is invalid');
      location.assign(url.href);
    },
    async activity(){if(profile)await read(await request('auth/session/activity',{method:'POST'}));},
  };
}

export function clearLegacyCredentials(local,session){
  for(const storage of [local,session]){
    if(!storage)continue;
    for(const key of ['userdata','token','access_token','refresh_token','id_token','gemini_brain_user','accutax_sso_token','accutax_widget_handoff','isRegistrationComplete'])storage.removeItem(key);
    for(let i=storage.length-1;i>=0;i--){const key=storage.key(i);if(key?.startsWith('@@auth0spajs@@'))storage.removeItem(key);}
  }
}

/** Polls verify authority, while only real foreground input can extend idle expiry. */
export function watchBrowserSession(client,{window,document,onError=()=>{}}){
  let stopped=false,busy=false,lastActivity=0;
  const verify=async()=>{if(stopped||busy||document.visibilityState==='hidden')return;busy=true;try{await client.load();}catch(error){onError(error);}finally{busy=false;}};
  const activity=event=>{if(!event.isTrusted||document.visibilityState==='hidden'||Date.now()-lastActivity<60000)return;lastActivity=Date.now();void client.activity().catch(onError);};
  window.addEventListener('focus',verify);document.addEventListener('visibilitychange',verify);
  for(const name of ['pointerdown','keydown'])document.addEventListener(name,activity,{passive:true});
  const timer=window.setInterval(verify,15000);
  return()=>{stopped=true;window.clearInterval(timer);window.removeEventListener('focus',verify);document.removeEventListener('visibilitychange',verify);for(const name of ['pointerdown','keydown'])document.removeEventListener(name,activity);};
}

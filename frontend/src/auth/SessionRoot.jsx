import { useEffect,useState } from 'react';
import App from '../App';
import { clearFinanceStorage,hubSignInUrl,sessionClient } from './session';
import { watchBrowserSession } from './session-client.mjs';
import { clearUploadBlobs } from '../services/uploadBlobs';
import AuthScreen,{ AuthLoading,describeProblem } from './AuthScreen';

export default function SessionRoot(){
  const [profile,setProfile]=useState(null),[loading,setLoading]=useState(true),[error,setError]=useState(null);
  const [scope,setScope]=useState(null);
  const reason=new URLSearchParams(window.location.search).get('auth_error');
  useEffect(()=>{
    let previous=null;
    const unsubscribe=sessionClient.subscribe(current=>{
      if(previous&&previous!==current?.user?.id){clearFinanceStorage();clearUploadBlobs();}
      previous=current?.user?.id;setProfile(current);setLoading(false);setError(null);
    });
    sessionClient.load().catch(setError).finally(()=>setLoading(false));
    const stop=watchBrowserSession(sessionClient,{window,document,onError:setError});
    return()=>{unsubscribe();stop();};
  },[]);
  const userId=profile?.user?.id;
  // Not signed in: go to the shared Accutax sign-in. A single guarded attempt stops a loop if the session never sticks.
  const goToSignIn=()=>{
    const target=new URL(window.location.href);target.searchParams.delete('auth_error');
    window.location.assign(hubSignInUrl(target.href));
  };
  useEffect(()=>{
    if(loading||profile||error||reason)return;
    try{
      const last=Number(sessionStorage.getItem('accutax_ai_signin_attempt')||0);
      if(Date.now()-last<20000)return;
      sessionStorage.setItem('accutax_ai_signin_attempt',String(Date.now()));
    }catch{/* storage blocked: still redirect once per load */}
    goToSignIn();
  },[loading,profile,error,reason]);
  useEffect(()=>{
    if(!userId){setScope(null);return;}
    let active=true,busy=false;
    const verify=async()=>{
      if(!active||busy||document.visibilityState==='hidden')return;
      busy=true;
      try{
        const data=await sessionClient.json('ai/api/v1/tenants');
        const ids=[...new Set((data?.tenants||[]).map(item=>Number(item.id??item.organization_id)).filter(Number.isSafeInteger))].sort((a,b)=>a-b);
        if(!ids.length)throw Object.assign(new Error('You do not currently have AI finance access for a company.'),{status:403});
        if(!active)return;
        const key=ids.join(',');
        setScope(previous=>{
          if(previous?.userId===userId&&previous.key!==key){clearFinanceStorage();clearUploadBlobs();}
          return{userId,key};
        });
        setError(null);
      }catch(reason){
        if(!active)return;
        clearFinanceStorage();clearUploadBlobs();setScope(null);setError(reason);
      }finally{busy=false;}
    };
    void verify();
    const timer=window.setInterval(verify,15000);
    window.addEventListener('focus',verify);
    return()=>{active=false;window.clearInterval(timer);window.removeEventListener('focus',verify);};
  },[userId]);
  const logout=async()=>{setError(null);try{await sessionClient.logout();}catch(reason){setError(reason);}};
  if(loading)return <AuthLoading label="Checking your session…"/>;
  if(!profile||error||reason)return <AuthScreen kind={describeProblem({reason,error,profile})} profile={profile} onSignIn={goToSignIn} onRetry={()=>window.location.reload()} onSignOut={logout}/>;
  if(!scope||scope.userId!==userId)return <AuthLoading label="Opening your companies…"/>;
  return <App key={`${userId}:${scope.key}`} sessionProfile={profile} onSessionLogout={logout}/>;
}

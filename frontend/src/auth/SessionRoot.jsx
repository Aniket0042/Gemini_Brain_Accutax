import { useEffect,useState } from 'react';
import App from '../App';
import { clearFinanceStorage,sessionClient } from './session';
import { watchBrowserSession } from './session-client.mjs';
import { clearUploadBlobs } from '../services/uploadBlobs';

const messages={identity_link_required:'Your verified identity needs an approved Accutax account link or onboarding. Contact your account administrator.',unavailable:'Sign-in is temporarily unavailable. Try again.',login_failed:'Sign-in could not be completed. Start again.'};
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
  if(loading)return <main style={{padding:40,textAlign:'center'}} role="status">Checking your session…</main>;
  if(!profile||error||reason)return <main style={{minHeight:'100vh',display:'grid',placeItems:'center',background:'#f7faf9',color:'#102c28'}}><section style={{width:'min(420px,90vw)',padding:32,border:'1px solid #dde7e3',borderRadius:16,background:'white'}}>
    <h1 style={{fontSize:24}}>Accutax AI</h1><p style={{lineHeight:1.6}}>Sign in once to use your Accutax companies and AI assistant.</p>
    {(error||reason)&&<p role="alert" style={{color:'#805e1c'}}>{messages[reason]||error?.message||'We could not verify your session. Please try again.'}</p>}
    {!profile&&<button style={{padding:'12px 18px',background:'#087c6c',color:'white',border:0,borderRadius:8}} onClick={()=>{
      const target=new URL(window.location.href);target.searchParams.delete('auth_error');sessionClient.login({returnTo:target.href});
    }}>Continue to secure sign-in</button>}
    {error&&<button style={{display:'block',marginTop:18}} onClick={()=>window.location.reload()}>Retry access check</button>}
    {profile&&<button style={{display:'block',marginTop:18}} onClick={logout}>Sign out</button>}
  </section></main>;
  if(!scope||scope.userId!==userId)return <main style={{padding:40,textAlign:'center'}} role="status">Checking AI company access…</main>;
  return <App key={`${userId}:${scope.key}`} sessionProfile={profile} onSessionLogout={logout}/>;
}

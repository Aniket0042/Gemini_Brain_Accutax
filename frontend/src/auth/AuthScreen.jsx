// The full-page screen shown while the chat checks the Accutax session, and when it cannot open.
const COPY={
  signin:{title:'Sign in to continue',body:'Use the same Accutax account you use for Bookkeeping. You will come straight back here.'},
  expired:{title:'Your session has ended',body:'For your security you were signed out. Sign in again to pick up where you left off.'},
  nocompany:{title:'No company to work with yet',body:'Accutax AI needs a company on your account. Add one in Bookkeeping, or ask your administrator to invite you to one.'},
  link:{title:'Your account needs approval',body:'Your verified identity is not linked to an Accutax account yet. Ask your account administrator to finish the setup.'},
  unavailable:{title:'Accutax is not reachable right now',body:'This is usually brief. Check your connection and try again in a moment.'},
  trouble:{title:'We could not open Accutax AI',body:'We could not confirm your access just now. Try again, or sign in once more if it keeps happening.'},
};
// Which of the messages above fits what went wrong.
export function describeProblem({reason,error,profile}){
  if(reason==='identity_link_required')return'link';
  if(reason==='unavailable')return'unavailable';
  if(reason==='login_failed')return'trouble';
  if(!error)return profile?'trouble':'signin';
  if(error.status===401)return'expired';
  if(error.status===403&&/finance access for a company/i.test(error.message||''))return'nocompany';
  if(error.status===0||error.status>=500||!error.status)return'unavailable';
  return'trouble';
}
export default function AuthScreen({kind,profile,onSignIn,onRetry,onSignOut}){
  const copy=COPY[kind]||COPY.trouble;
  const canSignIn=!profile&&kind!=='unavailable';
  return(
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand"><span className="auth-mark" aria-hidden="true">A</span><span>Accutax AI</span></div>
        <h1 id="auth-title">{copy.title}</h1>
        <p role={kind==='signin'?undefined:'alert'}>{copy.body}</p>
        <div className="auth-actions">
          {canSignIn&&<button type="button" className="auth-primary" onClick={onSignIn}>Sign in with Accutax</button>}
          {kind!=='signin'&&<button type="button" className={canSignIn?'auth-secondary':'auth-primary'} onClick={onRetry}>Try again</button>}
          {profile&&<button type="button" className="auth-secondary" onClick={onSignOut}>Sign out</button>}
        </div>
      </section>
    </main>
  );
}
export function AuthLoading({label}){
  return<main className="auth-screen"><div className="auth-loading" role="status"><span className="auth-spinner" aria-hidden="true"/>{label}</div></main>;
}

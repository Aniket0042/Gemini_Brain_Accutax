import { sessionClient } from '../auth/session';
export function LoginPage(){return <button onClick={()=>sessionClient.login()}>Continue to secure sign-in</button>;}

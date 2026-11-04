import { describe,it } from 'node:test';
import assert from 'node:assert/strict';
import { createLegacyClient,tokenUserId } from './legacy-client.mjs';

const response=(status,body)=>({status,ok:status>=200&&status<300,json:async()=>body});
const location={origin:'http://localhost:5174'};
const memory=()=>{const data=new Map();return{getItem:k=>data.get(k)??null,setItem:(k,v)=>data.set(k,v),removeItem:k=>data.delete(k)};};
const jwt=claims=>`h.${Buffer.from(JSON.stringify(claims)).toString('base64url')}.s`;

describe('legacy AI sign-in (no BFF)',()=>{
  it('signs in, sends the bearer only to this origin\'s /api/v1, and keeps the session across a reload',async()=>{
    const storage=memory();
    const client=createLegacyClient({location,storage,fetcher:async()=>response(200,{access_token:'tok',user_id:9,email:'a@example.test'})});
    await client.signIn('a@example.test','pw');
    assert.deepEqual(client.getSnapshot().user,{id:9,email:'a@example.test',name:'a@example.test'});
    const api=await client.prepare('http://localhost:5174/api/v1/tenants',{headers:{Accept:'application/json'}});
    assert.equal(api.headers.get('Authorization'),'Bearer tok');
    assert.equal(api.headers.get('Accept'),'application/json');
    const other=await client.prepare('https://elsewhere.example/api/v1/tenants',{});
    assert.equal(new Headers(other.headers).get('Authorization'),null);
    assert.equal(createLegacyClient({location,storage,fetcher:async()=>null}).getSnapshot().token,'tok');
  });

  it('maps BFF-style ai/ paths to this origin and drops the session on a 401',async()=>{
    const client=createLegacyClient({location,storage:memory(),fetcher:async()=>response(401,{detail:'expired'})});
    assert.equal(client.endpoint('ai/api/v1/tenants'),'http://localhost:5174/api/v1/tenants');
    assert.equal(client.endpoint('/api/v1/tenants'),'http://localhost:5174/api/v1/tenants');
    client.useToken(jwt({userId:15}),{email:'b@example.test'});
    assert.equal(client.getSnapshot().user.id,15);
    await assert.rejects(client.json('ai/api/v1/tenants'),/expired/);
    assert.equal(client.getSnapshot(),null);
  });

  it('rejects a failed sign-in with the backend message',async()=>{
    const client=createLegacyClient({location,storage:memory(),fetcher:async()=>response(401,{detail:'Invalid email or password'})});
    await assert.rejects(client.signIn('a','b'),/Invalid email or password/);
    assert.equal(client.getSnapshot(),null);
  });

  it('reads the user id from the usual token claims',()=>{
    assert.equal(tokenUserId(jwt({user_id:3})),3);
    assert.equal(tokenUserId(jwt({sub:'4'})),4);
    assert.equal(tokenUserId('not-a-jwt'),null);
  });
});

import { describe,it } from 'node:test';
import assert from 'node:assert/strict';
import { createSessionClient,clearLegacyCredentials } from './session-client.mjs';

const profile={authenticated:true,user:{id:7,email:'u@example.test'},organizations:[{id:10}],csrfToken:'csrf-only-in-memory'};
const response=(status,body)=>({status,ok:status>=200&&status<300,json:async()=>body});
const location=()=>({origin:'http://localhost:5173',assign(url){this.navigated=url;}});

describe('shared BFF browser contract',()=>{
  it('uses one server profile load for concurrent callers and does not recover browser JWTs',async()=>{
    let count=0;
    const client=createSessionClient({apiBase:'http://localhost:8081',location:location(),fetcher:async()=>{count++;return response(200,profile);}});
    const [first,second]=await Promise.all([client.load(),client.load()]);
    assert.equal(count,1);assert.deepEqual(first,profile);assert.deepEqual(second,profile);
    const storage=values=>{const data=new Map(Object.entries(values));return{get length(){return data.size;},key:i=>[...data.keys()][i],removeItem:key=>data.delete(key),getItem:key=>data.get(key)};};
    const local=storage({userdata:'forged',token:'jwt','@@auth0spajs@@x':'old'});
    clearLegacyCredentials(local,storage({refresh_token:'old'}));
    assert.equal(local.length,0);
  });
  it('sends cookie and in-memory CSRF only to the exact API origin',async()=>{
    const client=createSessionClient({apiBase:'http://localhost:8081',location:location(),fetcher:async()=>response(200,profile)});
    await client.load();
    const request=new Request('http://localhost:8081/income/create',{method:'POST',headers:{Authorization:'Bearer old','X-Organization-ID':'10'}});
    const prepared=await client.prepare(request,{method:'POST',headers:{'X-Requested-With':'fetch'}});
    assert.equal(prepared.credentials,'include');
    assert.equal(prepared.headers.get('X-CSRF-Token'),profile.csrfToken);
    assert.equal(prepared.headers.get('X-Organization-ID'),'10');
    assert.equal(prepared.headers.get('Authorization'),null);
    assert.equal(prepared.headers.get('X-Requested-With'),'fetch');
    assert.equal(client.isApi('http://localhost:8081.evil.test/income/create'),false);
    assert.equal(client.isApi('https://evil.test/'),false);
    assert.deepEqual(await client.prepare('https://evil.test/',{method:'POST',headers:{Authorization:'external'}}),{method:'POST',headers:{Authorization:'external'}});
  });
  it('clears on 401, retains on 403 and fails closed without a refresh loop on 503',async()=>{
    let status=200,calls=0;
    const client=createSessionClient({apiBase:'http://localhost:8081',location:location(),fetcher:async()=>{calls++;return response(status,profile);}});
    await client.load();
    client.observe(response(403,{}));assert.equal(client.getSnapshot()?.user.id,7);
    status=503;await assert.rejects(client.load({force:true}),error=>error.status===503);
    assert.equal(client.getSnapshot()?.user.id,7);
    client.observe(response(401,{}));assert.equal(client.getSnapshot(),null);
    assert.equal(calls,2);
  });
  it('logs out through the BFF and follows only a server logout URL',async()=>{
    const where=location();let sawPost=false;
    const client=createSessionClient({apiBase:'http://localhost:8081',location:where,fetcher:async(url,init)=>{
      if(url.endsWith('/auth/session'))return response(200,profile);
      if(url.endsWith('/auth/oidc/logout')){
        sawPost=init.method==='POST'&&init.credentials==='include'&&init.headers.get('X-CSRF-Token')===profile.csrfToken;
        return response(201,{logoutUrl:'http://localhost:8180/ui/v2/login/logout'});
      }
      throw new Error('Unexpected route');
    }});
    await client.load();await client.logout();
    assert.equal(sawPost,true);assert.equal(client.getSnapshot(),null);
    assert.equal(where.navigated,'http://localhost:8180/ui/v2/login/logout');
  });
});

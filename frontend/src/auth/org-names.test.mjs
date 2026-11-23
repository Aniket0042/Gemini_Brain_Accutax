import test from 'node:test';
import assert from 'node:assert/strict';
import { applySessionOrgNames,isPlaceholderName } from './org-names.mjs';

const session=[{id:6,name:'connectify.global'},{id:7,name:'acme corp'}];
test('replaces invented names with the real ones',()=>{
  const out=applySessionOrgNames([{id:6,name:'Organization 6'},{id:7,name:'Organization 7'}],session);
  assert.deepEqual(out.map(t=>t.name),['connectify.global','acme corp']);
});
test('keeps a real name the AI service already has',()=>{
  const out=applySessionOrgNames([{id:6,name:'Connectify Ltd'}],session);
  assert.equal(out[0].name,'Connectify Ltd');
});
test('fills a missing name and reads organization_id',()=>{
  assert.equal(applySessionOrgNames([{organization_id:7}],session)[0].name,'acme corp');
});
test('leaves unknown companies alone and does not mutate',()=>{
  const input=[{id:99,name:'Organization 99'}];
  const out=applySessionOrgNames(input,session);
  assert.equal(out[0].name,'Organization 99');
  assert.equal(input[0].name,'Organization 99');
});
test('copes with no session organizations',()=>{
  assert.deepEqual(applySessionOrgNames([{id:1,name:'Organization 1'}],undefined),[{id:1,name:'Organization 1'}]);
  assert.deepEqual(applySessionOrgNames(undefined,session),[]);
});
test('placeholder detection',()=>{
  assert.ok(isPlaceholderName('Organization 12'));assert.ok(isPlaceholderName(''));assert.ok(isPlaceholderName(undefined));
  assert.ok(!isPlaceholderName('Organic Farms'));
});

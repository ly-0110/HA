const {test}=require('node:test');const assert=require('node:assert/strict');
const {allowedRequest,safeRelative,trustedEvidence}=require('../src/policy.cjs');
test('desktop gateway rejects arbitrary endpoints, methods and traversal',()=>{
  assert.equal(allowedRequest('/api/v1/tasks?limit=20'),'/api/v1/tasks?limit=20');
  for(const url of ['https://evil.invalid','/api/v1/tasks/../tasks','/api/v1/desktop/tools','/api/v1/tasks?command=rm','/api/v1/sessions/x/artifacts/%2e%2e/secrets'])assert.throws(()=>allowedRequest(url));
  assert.throws(()=>allowedRequest('/api/v1/tasks','DELETE'));
});
test('file capabilities accept only relative data paths',()=>{
  assert.equal(safeRelative('brightness/actions.jsonl'),'brightness/actions.jsonl');
  for(const value of ['/etc/passwd','../secrets','C:/data','..\\secrets'])assert.throws(()=>safeRelative(value));
});
test('evidence streams require non-spoofable workbench initiator',()=>{
  assert.equal(trustedEvidence({method:'GET',initiatorOrigin:'app://workbench'}),true);
  for(const origin of [undefined,null,'https://evil.invalid','app://evidence'])assert.equal(trustedEvidence({method:'GET',initiatorOrigin:origin}),false);
});

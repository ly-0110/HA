const {test}=require('node:test');
const assert=require('node:assert/strict');
const {validateReady,validateHealth,handshakeDeadline}=require('../src/backend.cjs');
test('readiness requires this child, instance, protocol and a valid loopback port',()=>{
 const ready={instance_id:'owned',protocol_version:1,pid:123,port:4723};
 validateReady(ready,'owned',123);
 for(const change of [{instance_id:'other'},{protocol_version:2},{pid:999},{port:0},{port:65536},{port:'4723'}])assert.throws(()=>validateReady({...ready,...change},'owned',123));
});
test('a reachable service cannot become ready with unauthenticated or wrong health data',()=>{
 const health={instance_id:'owned',protocol_version:1};
 validateHealth(true,health,'owned');
 assert.throws(()=>validateHealth(false,health,'owned'));
 assert.throws(()=>validateHealth(true,{...health,instance_id:'spoofed'},'owned'));
 assert.throws(()=>validateHealth(true,{...health,protocol_version:2},'owned'));
});
test('the handshake has a 30 second deadline and closes only the owned pipe',()=>{
 const previous=global.setTimeout;let callback,delay,closed=0,error;
 global.setTimeout=(fn,ms)=>{callback=fn;delay=ms;return 123;};
 try{
  handshakeDeadline({stdin:{end:()=>closed++}},value=>error=value);
  assert.equal(delay,30000);callback();assert.equal(closed,1);assert.match(error.message,/30秒/);
 }finally{global.setTimeout=previous;}
});

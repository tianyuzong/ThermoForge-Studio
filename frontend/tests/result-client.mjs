import assert from 'node:assert/strict';
import fs from 'node:fs';
import ts from 'typescript';

function loadModule(name,api){
 const source=fs.readFileSync(new URL(`../src/${name}.ts`,import.meta.url),'utf8');
 const js=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
 const exports={};new Function('exports','require',js)(exports,()=>({api}));return exports;
}
const calls=[];
let version='a';
const client=loadModule('resultClient',async(path,init={})=>{
 calls.push(path);if(init.signal?.aborted)throw new DOMException('aborted','AbortError');
 if(path.endsWith('/topology'))return {topology_id:'mesh',points:[[0,0,0]],triangles:[],node_ids:[1]};
 return {topology_id:'mesh',fields:{temperature:[version==='a'?25:50]},frame_index:Number(path.match(/frames\/(\d+)/)[1])};
});
client.registerResultVersion('study','a');
const first=await client.resultFrame('study',0,undefined,['temperature']);
const second=await client.resultFrame('study',1,undefined,['temperature']);
assert.equal(first.points,second.points,'frames must share immutable geometry by identity');
assert.equal(calls.filter(p=>p.endsWith('/topology')).length,1,'topology fetched once across frames');
assert(calls.at(-1).endsWith('?fields=temperature'),'only the selected field requested');
await client.resultFrame('study',1,undefined,['temperature']);assert.equal(calls.length,3,'recent frame is cached');
for(let i=2;i<20;i++)await client.resultFrame('study',i,undefined,['temperature']);
const before=calls.length;await client.resultFrame('study',0,undefined,['temperature']);assert.equal(calls.length,before+1,'old frames evicted by bounded cache');
const controller=new AbortController();controller.abort();await assert.rejects(client.resultFrame('study',30,controller.signal,['temperature']),{name:'AbortError'});
const beforeRetry=calls.length;await client.resultFrame('study',30,undefined,['temperature']);assert.equal(calls.length,beforeRetry+1,'an aborted request cannot poison cache');
version='b';client.registerResultVersion('study','b');const changed=await client.resultFrame('study',0,undefined,['temperature']);assert.equal(changed.fields.temperature[0],50,'result hash changes invalidate saved frames');
assert.equal(client.frameDelay([0,1,4],0),3750);assert.equal(client.frameDelay([0,1,4],1),11250,'non-uniform times retain physical time ratio');

class MemoryStorage{data=new Map();get length(){return this.data.size}key(i){return [...this.data.keys()][i]}getItem(k){return this.data.get(k)||null}setItem(k,v){this.data.set(k,v)}}
globalThis.localStorage=new MemoryStorage();
const writes=[];let server={case:{analysis_type:'static'},revision:3,model_id:'model'};
const drafts=loadModule('draftStore',async(path,init={})=>{if(init.method==='PUT'){const body=JSON.parse(init.body);writes.push(body);return {...server,...body,revision:server.revision+1}}return server});
const local={case:{analysis_type:'static',name:'my edits'},model_id:'model',dirty:true,revision:2};
assert.equal(drafts.chooseDraft(server,local,'model').conflict,true,'concurrent edits must not silently overwrite');
assert.equal(drafts.chooseDraft(server,local,'other').chosen,server,'local edits bound to geometry');
const project={id:'project',model_id:'model'},key=drafts.draftStorageKey(project,'static');localStorage.setItem(key,JSON.stringify({...local,edited_at:10}));
await assert.rejects(drafts.flushPendingDraft(project),/草稿/);assert.equal(writes.length,0);
localStorage.setItem(key,JSON.stringify({...local,revision:3,edited_at:10}));await drafts.flushPendingDraft(project);assert.equal(writes[0].expected_revision,3);assert.equal(writes[0].case.name,'my edits');assert.equal(JSON.parse(localStorage.getItem(key)).dirty,false);
assert.equal(drafts.chooseDraft(server,{...local,adopted:true},'model').conflict,false,'explicit copy is an intentional new draft');
console.log('PASS: topology reuse, field filtering, LRU, abort recovery, hash invalidation, physical timing, draft geometry scope, revision conflict, prompt flush');

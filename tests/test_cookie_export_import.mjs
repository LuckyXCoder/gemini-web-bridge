import test from 'node:test';
import assert from 'node:assert/strict';
import {accountIndex, makeExport, readSession} from '../extensions/gemini-session-exporter/core.mjs';
import {parseCredentialImport} from '../web/src/credentialImport.mjs';

const session={cookies:{'__Secure-1PSID':'synthetic-psid','__Secure-1PSIDTS':'synthetic-ts'},candidates:['test@example.com']};
test('export/import roundtrip only fills allowed fields',()=>{
 const data=makeExport(session,'test@example.com',true);
 assert.deepEqual(parseCredentialImport(JSON.stringify({...data,id:'injected',cookies_file:'/tmp/evil',enabled:true})),{display_name:'test@example.com',secure_1psid:'synthetic-psid',secure_1psidts:'synthetic-ts'});
});
test('requires email and explicit confirmation',()=>{
 assert.throws(()=>makeExport(session,'test@example.com',false));
 assert.throws(()=>makeExport(session,'not-an-email',true));
});
test('rejects non-Gemini and secondary account routes',()=>{
 assert.equal(accountIndex('https://gemini.google.com/app'),0);
 for(const url of ['https://evil.example','http://gemini.google.com','https://gemini.google.com/u/1/app','https://gemini.google.com/app?authuser=1','https://gemini.google.com/u/0/app?authuser=1','https://gemini.google.com/app?authuser=0&authuser=1'])assert.throws(()=>accountIndex(url));
});
test('import supports old cookie JSON without inventing a name',()=>{
 assert.deepEqual(parseCredentialImport(JSON.stringify(session.cookies)),{secure_1psid:'synthetic-psid',secure_1psidts:'synthetic-ts'});
});
test('rejects malformed, partial, oversized and nondefault imports',()=>{
 for(const value of ['bad','[]',JSON.stringify({cookies:{'__Secure-1PAPISID':'wrong'}}),JSON.stringify({...makeExport(session,'test@example.com',true),account_index:1}), ' '.repeat(65537)])assert.throws(()=>parseCredentialImport(value));
});
function api({duplicate=false,navigated=false}={}){
 const calls=[];
 return {calls,tabs:{query:async()=>[{id:4,url:'https://gemini.google.com/app'}],get:async()=>({url:navigated?'https://gemini.google.com/u/1/app':'https://gemini.google.com/app'})},
 cookies:{getAllCookieStores:async()=>[{id:'normal',tabIds:[1]},{id:'private',tabIds:[4]}],getAll:async args=>{calls.push(args);const cookie={domain:'.google.com',path:'/',name:args.name,value:session.cookies[args.name],httpOnly:true};return duplicate?[cookie,cookie]:[cookie];}},
 scripting:{executeScript:async()=>[{result:['test@example.com']}]}};
}
test('reads exactly two names from the active tab cookie store, including HttpOnly',async()=>{
 const mock=api();const result=await readSession(mock);
 assert.deepEqual(result,session);
 assert.deepEqual(mock.calls.map(x=>x.name),['__Secure-1PSID','__Secure-1PSIDTS']);
 assert.ok(mock.calls.every(x=>x.storeId==='private'&&x.url==='https://gemini.google.com/'));
});
test('rejects ambiguous cookies and navigation during read',async()=>{
 await assert.rejects(()=>readSession(api({duplicate:true})));
 await assert.rejects(()=>readSession(api({navigated:true})));
});

test('email extraction is scoped to visible account controls', async()=>{
 const {readEmailCandidates}=await import('../extensions/gemini-session-exporter/core.mjs');
 const oldDocument=globalThis.document, oldStyle=globalThis.getComputedStyle;
 try{
  globalThis.document={querySelectorAll:selector=>{
   assert.ok(selector.includes('SignOutOptions'));assert.ok(!selector.includes('body'));
   return [{getClientRects:()=>[{}],getAttribute:name=>name==='aria-label'?'Google Account: User (current@example.com)':null,textContent:'User'},
     {getClientRects:()=>[],getAttribute:()=> 'hidden@example.com',textContent:'hidden@example.com'}];
  }};
  globalThis.getComputedStyle=()=>({visibility:'visible'});
  assert.deepEqual(readEmailCandidates(),['current@example.com']);
 }finally{
  if(oldDocument===undefined)delete globalThis.document;else globalThis.document=oldDocument;
  if(oldStyle===undefined)delete globalThis.getComputedStyle;else globalThis.getComputedStyle=oldStyle;
 }
});

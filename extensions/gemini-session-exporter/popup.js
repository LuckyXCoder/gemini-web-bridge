import {readSession, makeExport} from './core.mjs';
const byId=id=>document.getElementById(id);
let session=null;
function status(text,error=false){byId('status').textContent=text;byId('status').className=error?'error':'';}
function update(){const ready=Boolean(session&&byId('confirm').checked&&byId('email').validity.valid&&byId('email').value.trim());byId('download').disabled=!ready;byId('copy').disabled=!ready;}
byId('read').addEventListener('click',async()=>{
 session=null;byId('confirm').checked=false;byId('confirm').disabled=true;byId('email').value='';byId('email').disabled=true;byId('cookie-status').textContent='Cookie 尚未读取';update();byId('read').disabled=true;status('正在读取…');
 try{
  session=await readSession(chrome);byId('email').disabled=false;byId('confirm').disabled=false;
  byId('email').value=session.candidates.length===1?session.candidates[0]:'';
  byId('email-note').textContent=session.candidates.length===1?'已识别页面邮箱，请确认与目标账号一致。':'无法唯一识别邮箱，请手动填写目标账号邮箱。';
  byId('cookie-status').textContent='__Secure-1PSID ✓    __Secure-1PSIDTS ✓（值不显示）';
  status('读取完成，确认邮箱后可导出。');
 }catch(error){session=null;status(error instanceof Error?error.message:'读取失败',true);}
 finally{byId('read').disabled=false;update();}
});
byId('email').addEventListener('input',()=>{byId('confirm').checked=false;update();});
byId('confirm').addEventListener('change',update);
function payload(){return JSON.stringify(makeExport(session,byId('email').value,byId('confirm').checked),null,2);}
byId('copy').addEventListener('click',async()=>{
 try{await navigator.clipboard.writeText(payload());status('已复制 JSON，请粘贴到 Bridge 的“导入 JSON”。');}
 catch{status('复制失败，请改用下载 JSON。',true);}
});
byId('download').addEventListener('click',()=>{
 try{const url=URL.createObjectURL(new Blob([payload()],{type:'application/json'}));const link=document.createElement('a');link.href=url;link.download='gemini-account-'+Date.now()+'.json';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);status('已发起下载，请在 Bridge 中导入文件。');}
 catch(error){status(error.message,true);}
});

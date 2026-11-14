const $ = id => document.getElementById(id);
// Режим фабрики real не означает, что вызывается Qwen: показываем отдельный demo-флаг.
fetch('/health').then(r=>r.json()).then(h=>{
  $('core-mode').textContent=h.core_demo?'Подключено ядро Данька: demo-база и тестовая модель, без Qwen.':
    h.core_mode==='stub'?'Тестовый режим backend: ядро не используется.':
    h.core_ready?'Ядро подключено. Доступность модели проверяется при отправке сообщения.':'Ядро пока не подключено.';
}).catch(()=>{$('core-mode').textContent='Не удалось проверить состояние сервера.';});
let csrf = '', active = null, card = null;
async function api(path, method='GET', body) {
  const response = await fetch('/api'+path, {method, credentials:'same-origin', headers:{'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:body === undefined ? undefined : JSON.stringify(body)});
  if (!response.ok) { const error=await response.json(); throw Error(typeof error.detail==='string'?error.detail:'Проверьте заполненные поля'); }
  return response.status===204 ? null : response.json();
}
function report(error) { $('status').textContent=error.message; }
function button(text, fn) { const b=document.createElement('button'); b.textContent=text; b.onclick=()=>fn().catch(report); return b; }
async function refreshDialogs() {
  const rows=await api('/dialogs'); $('dialogs').replaceChildren();
  rows.forEach(d=>$('dialogs').append(button(new Date(d.created_at).toLocaleString('ru-RU',{timeZone:'Europe/Moscow'})+' · '+(d.status==='active'?'Активный':'Архив'), ()=>openDialog(d.id))));
}
async function openDialog(id) {
  const d=await api('/dialogs/'+id); active=d; card=null; $('card').textContent=''; $('confirm').disabled=true; $('consent').checked=false;
  ['full_name','phone','company','summary'].forEach(k=>$(k).value='');
  $('messages').replaceChildren();
  d.messages.forEach(m=>{const p=document.createElement('div');p.className='bubble';p.textContent=(m.role==='user'?'Вы: ':'Консультант: ')+m.content+(m.status==='failed'?' [ошибка обработки]':'');$('messages').append(p);});
  $('debug').textContent=JSON.stringify(d.messages.filter(m=>m.role==='assistant').map(m=>({elapsed_ms:m.elapsed_ms,sources:m.sources})),null,2);
  $('message-form').hidden=d.status!=='active';
  try { card=await api('/dialogs/'+id+'/application'); showCard(); } catch(e) { if(e.message!=='Application not found') throw e; }
}
function showCard() {
  ['full_name','phone','company','summary'].forEach(k=>$(k).value=card[k]);
  $('card').textContent='ФИО: '+card.full_name+'\nТелефон: '+card.phone+'\nКомпания: '+card.company+'\nРезюме: '+card.summary+'\n'+(card.status==='created'?'Заявка сохранена. Номер: '+card.id:'Проверьте данные перед подтверждением.'); $('confirm').disabled=card.status!=='draft';
}
async function signedIn(result) {
  csrf=result.csrf_token; $('auth').hidden=true; $('workspace').hidden=false;
  $('identity').textContent=result.user.email; $('staff').hidden=!result.user.is_staff;
  $('password').value=''; await refreshDialogs();
}
$('auth-form').onsubmit=async e=>{e.preventDefault();try{await signedIn(await api('/auth/'+e.submitter.value,'POST',{email:$('email').value,password:$('password').value}));$('status').textContent='';}catch(e){report(e);}};
$('logout').onclick=async()=>{try{await api('/auth/logout','POST');location.reload();}catch(e){report(e);}};
$('new').onclick=async()=>{try{const d=await api('/dialogs','POST');await refreshDialogs();await openDialog(d.id);}catch(e){report(e);}};
$('close').onclick=async()=>{try{if(active){await api('/dialogs/'+active.id+'/close','POST');await refreshDialogs();await openDialog(active.id);}}catch(e){report(e);}};
$('message-form').onsubmit=async e=>{e.preventDefault();if(!active)return;const b=e.submitter;b.disabled=true;try{const result=await api('/dialogs/'+active.id+'/messages','POST',{request_id:crypto.randomUUID(),content:$('message').value});$('message').value='';await openDialog(active.id);if(result.summary_draft)$('summary').value=result.summary_draft;$('status').textContent='';}catch(e){report(e);if(active)await openDialog(active.id).catch(report);}finally{b.disabled=false;}};
$('card-form').onsubmit=async e=>{e.preventDefault();if(!active)return;try{const body=Object.fromEntries(['full_name','phone','company','summary'].map(k=>[k,$(k).value]));card=await api('/dialogs/'+active.id+'/application','PUT',body);$('consent').checked=false;showCard();}catch(e){report(e);}};
$('confirm').onclick=async()=>{try{if(!card||!active)return;card=await api('/dialogs/'+active.id+'/application/confirm','POST',{version:card.version,consent:$('consent').checked});showCard();$('status').textContent='Заявка сохранена: '+card.id;}catch(e){report(e);}};
$('staff-load').onclick=async()=>{try{const dialogs=await api('/staff/dialogs');$('staff-dialogs').replaceChildren();dialogs.forEach(d=>$('staff-dialogs').append(button(d.id,async()=>{$('staff-data').textContent=JSON.stringify(await api('/staff/dialogs/'+d.id),null,2);})));$('staff-data').textContent=JSON.stringify(await api('/staff/applications'),null,2);}catch(e){report(e);}};
api('/auth/me').then(signedIn).catch(()=>{});

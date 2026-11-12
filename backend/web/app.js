'use strict';
let current = null;
let busy = false;
let pending = null;
const el = id => document.getElementById(id);
async function api(path, options = {}) {
  const response = await fetch(path, {credentials: 'same-origin', ...options,
    headers: {'Content-Type': 'application/json', ...options.headers}});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Ошибка запроса');
  return body;
}
function report(error) { el('error').textContent = error ? `Ошибка: ${error.message}` : ''; }
function controls() {
  el('send').disabled = busy || !current || current.archived;
  el('text').disabled = busy || !current || current.archived;
  el('new').disabled = busy;
  el('archive').disabled = busy || !current || current.archived;
  el('reopen').hidden = !current?.archived;
}
async function list() {
  const {chats} = await api('/api/chats');
  el('chats').replaceChildren();
  for (const chat of chats) {
    const button = document.createElement('button');
    button.textContent = `${chat.archived ? 'Архив · ' : ''}${chat.title}`;
    button.disabled = busy;
    button.onclick = () => open(chat.id).catch(report);
    el('chats').append(button);
  }
}
async function open(id) {
  current = await api(`/api/chats/${id}`);
  pending = null;
  el('title').textContent = current.title + (current.archived ? ' · архив' : '');
  el('messages').replaceChildren();
  for (const message of current.messages) {
    const article = document.createElement('article');
    article.className = message.role;
    const label = document.createElement('strong');
    label.textContent = message.role === 'user' ? 'Вы' : 'Тестовый консультант';
    const text = document.createElement('p');
    text.textContent = message.content;
    article.append(label, text);
    if (message.state === 'failed') article.append(document.createTextNode('Ответ не получен.'));
    el('messages').append(article);
  }
  controls();
}
el('new').onclick = async () => {
  try { report(null); const chat = await api('/api/chats', {method: 'POST', body: JSON.stringify({})});
    await open(chat.id); await list(); } catch (error) { report(error); }
};
el('form').onsubmit = async event => {
  event.preventDefault();
  if (!current || busy) return;
  const content = el('text').value;
  if (!content.trim()) return;
  const chatId = current.id;
  pending = pending?.content === content && pending.chatId === chatId ? pending : {chatId, content, request_id: crypto.randomUUID()};
  busy = true; controls(); report(null);
  try {
    await list();
    await api(`/api/chats/${chatId}/messages`, {method: 'POST', body: JSON.stringify({content, request_id: pending.request_id})});
    el('text').value = ''; pending = null; await open(chatId);
  } catch (error) { report(error); }
  finally { busy = false; controls(); await list().catch(report); }
};
el('archive').onclick = async () => {
  try { await api(`/api/chats/${current.id}/archive`, {method: 'POST', body: '{}'}); await open(current.id); await list(); }
  catch (error) { report(error); }
};
el('reopen').onclick = async () => {
  try { const chat = await api(`/api/chats/${current.id}/reopen`, {method: 'POST', body: '{}'}); await open(chat.id); await list(); }
  catch (error) { report(error); }
};
(async () => { await api('/api/session', {method: 'POST', body: '{}'}); await list(); controls(); })().catch(report);

const puppeteer = require('puppeteer');
const BASE = 'http://127.0.0.1:8000';
let PASS=0, FAIL=0;
const ok=(name,cond,extra='')=>{ if(cond){PASS++;console.log('  ok  '+name);} else {FAIL++;console.log('FAIL  '+name+'  '+extra);} };
const sleep=ms=>new Promise(r=>setTimeout(r,ms));

(async()=>{
 const browser = await puppeteer.launch({headless:'new',args:['--no-sandbox','--disable-dev-shm-usage']});
 const page = await browser.newPage();
 await page.setViewport({width:1440,height:900});
 const errors=[];
 page.on('pageerror',e=>errors.push('pageerror: '+e.message));
 page.on('console',m=>{ if(m.type()==='error' && !/401|403|400|429|Failed to load resource/.test(m.text())) errors.push('console: '+m.text()); });

 // эталонный набор до и после испытаний: UI-тесты не зависят от прежнего состояния БД
 const rpcAs = async (cookie,action,payload)=>{
  const r = await fetch(BASE+'/api/rpc',{method:'POST',headers:{'Content-Type':'application/json',cookie},
   body:JSON.stringify({action,payload:payload||{}})});
  return r.json();
 };
 const admSess = await fetch(BASE+'/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({login:'admin',password:'demo'})});
 const admCookie = (admSess.headers.get('set-cookie')||'').split(';')[0];
 await rpcAs(admCookie,'admin.reset');

 console.log('== Загрузка и вход ==');
 await page.goto(BASE,{waitUntil:'networkidle0'});
 ok('экран входа', await page.$('.login-box')!==null);
 ok('8 демо-карточек', (await page.$$('.user-pick')).length===8);
 await page.evaluate(()=>{ App.pickUser('volkova'); });
 await page.click('.login-row .btn.pri');
 await page.waitForSelector('.shell',{timeout:10000});
 ok('оболочка после входа', await page.$('.sidebar')!==null);
 await sleep(400);
 ok('дашборд: KPI виджеты', (await page.$$('.kpi')).length>=4);
 ok('роль РП видна', (await page.evaluate(()=>App.user.role))==='pm');
 await page.screenshot({path:'/tmp/e2e/shot_dash.png'});

 console.log('== Модули: рендер ==');
 for(const [key,sel] of [['portfolio','.tbl'],['tasks','.tbl'],['kanban','.kb-col'],['gantt','svg'],['resources','.card'],['time','.tbl'],['control','.card'],['risks','.tbl'],['docs','.tbl'],['calcs','.tbl'],['analytics','svg'],['calendar','.cal-cell'],['reports','.card'],['meetings','.tbl'],['journal','.tbl'],['builder','.scenario']]){
  await page.evaluate(k=>nav(k),key);
  await sleep(250);
  const has = await page.$(sel)!==null;
  ok('рендер '+key, has);
 }

 console.log('== Создание проекта через форму ==');
 await page.evaluate(()=>nav('portfolio'));
 await sleep(250);
 await page.evaluate(()=>App.projModal(null));
 await sleep(150);
 await page.evaluate(()=>{
  document.getElementById('pm-name').value='E2E проект браузера';
  document.getElementById('pm-start').value='2026-12-01';
  document.getElementById('pm-end').value='2027-03-01';
  document.getElementById('pm-budget').value='500';
 });
 await page.evaluate(()=>App.projSave(null));
 await sleep(700);
 const pcode = await page.evaluate(()=>{const p=DB.projects.find(x=>x.name==='E2E проект браузера');return p?p.code:null;});
 ok('проект создан на сервере', !!pcode, pcode);
 ok('drawer открыт с кодом', (await page.evaluate(()=>document.getElementById('drawer-root').textContent)).includes(pcode||'@@'));

 console.log('== Задача + канбан + гант ==');
 const tid = await page.evaluate(async()=>{
  const p=DB.projects.find(x=>x.name==='E2E проект браузера');
  const r=await rpc('task.create',{projectId:p.id,title:'E2E задача',assignee:'u5',start:'2026-12-01',end:'2026-12-10',planHours:24,priority:'Высокий',initialStatus:'Готова к выполнению'});
  return r?r.id:null;
 });
 ok('задача создана', !!tid);
 await page.evaluate(()=>nav('kanban'));
 await sleep(300);
 await page.evaluate(t=>App.kbApply(t,KB_COLS.find(c=>c.key==='work'),['В работе']),tid);
 await sleep(600);
 ok('канбан: карточка В работе', (await page.evaluate(t=>T_(t).status,tid))==='В работе');
 await page.screenshot({path:'/tmp/e2e/shot_kanban.png'});
 await page.evaluate(()=>nav('gantt'));
 await sleep(300);
 ok('гант отрисован', await page.$('svg')!==null);

 console.log('== Трудозатраты через форму ==');
 await page.evaluate(()=>nav('time'));
 await sleep(250);
 await page.evaluate(()=>App.timeModal(null));
 await sleep(150);
 await page.evaluate(t=>{
  document.getElementById('te-date').value='2026-11-24';
  document.getElementById('te-hours').value='4';
  const sel=document.getElementById('te-task');
  sel.innerHTML='<option value="'+t+'">E2E</option>'; sel.value=t;
 },tid);
 await page.evaluate(()=>App.timeSave(null));
 await sleep(600);
 ok('часы записаны (сервер)', await page.evaluate(()=>DB.time.some(e=>e.comment!==undefined&&e.hours===4&&e.date==='2026-11-24')));

 console.log('== Расчёт через мастер ==');
 const calcOk = await page.evaluate(async()=>{
  const p=DB.projects.find(x=>x.name==='E2E проект браузера');
  const r=await rpc('calc.run',{model:'link',title:'E2E бюджет',projectId:p.id,inputs:{f:5000,d:10,ptx:20,gtx:20,grx:20,lmisc:5,sens:-90}});
  return r && r.outputs && r.outputs.ok===true;
 });
 ok('расчёт выполнен сервером', calcOk===true);

 console.log('== Документ: реальная загрузка файла ==');
 const fs=require('fs');
 fs.writeFileSync('/tmp/e2e/test_doc.txt','E2E file content — проверка хранилища M09');
 await page.evaluate(()=>nav('docs'));
 await sleep(250);
 await page.evaluate(()=>App.docModal(null,false));
 await sleep(150);
 await page.evaluate(()=>{
  document.getElementById('dm-title').value='E2E документ';
  const p=DB.projects.find(x=>x.name==='E2E проект браузера');
  document.getElementById('dm-proj').value=p.id;
 });
 const fi = await page.$('#dm-file');
 await fi.uploadFile('/tmp/e2e/test_doc.txt');
 await page.evaluate(()=>App.docSave(null,false));
 await sleep(900);
 const docId = await page.evaluate(()=>{const d=DB.docs.find(x=>x.title==='E2E документ');return d?d.id:null;});
 ok('документ создан', !!docId);
 const fileCheck = await page.evaluate(async id=>{
  const d=DB.docs.find(x=>x.id===id);
  const r=await fetch('/api/files/'+id+'/1'); const txt=await r.text();
  return {stored:d.versions[0].stored, match:txt.includes('E2E file content')};
 },docId);
 ok('файл сохранён на сервере (stored)', fileCheck.stored===true);
 ok('скачивание = загруженному', fileCheck.match===true);

 console.log('== Отчёт и журнал ==');
 await page.evaluate(()=>nav('reports'));
 await sleep(250);
 await page.evaluate(()=>App.reportGen());
 await sleep(700);
 ok('отчёт открыт в модале', (await page.evaluate(()=>document.getElementById('modal-root').textContent)).includes('ОТ-'));
 await page.evaluate(()=>App.closeModal(true));
 await page.evaluate(()=>nav('journal'));
 await sleep(300);
 ok('журнал содержит BUSINESS', (await page.evaluate(()=>document.getElementById('view').textContent)).includes('BUSINESS'));

 console.log('== Центр уведомлений и напоминаний (M12 §8) ==');
 // совещание создаёт другой пользователь (администратор) — приглашение должно прийти в браузер РП
 const mtRes = await rpcAs(admCookie,'meeting.create',{title:'E2E совещание уведомлений',type:'Оперативное',
  date:new Date(Date.now()+864e5).toISOString().slice(0,10),time:'11:00',chair:'u2',secretary:'u4',attendees:['u3','u5']});
 const mid = (mtRes.result||{}).id;
 ok('совещание создано вторым пользователем', !!mid);
 await page.evaluate(()=>pollState());
 await sleep(400);
 ok('счётчик непрочитанных обновлён', (await page.evaluate(()=>DB.notifUnread))>=1);
 ok('бейдж уведомлений видим', await page.evaluate(()=>{const b=document.getElementById('notif-bdg');return b&&b.style.display!=='none';}));
 await page.evaluate(()=>App.notifs());
 await sleep(200);
 ok('попап центра уведомлений открыт', await page.$('.notif-pop')!==null);
 ok('приглашение доставлено сервером', (await page.evaluate(()=>document.getElementById('notif-pop').textContent)).includes('Приглашение: E2E совещание уведомлений'));
 ok('есть раздел подсказок из состояния', (await page.evaluate(()=>document.getElementById('notif-pop').textContent)).includes('Требует внимания'));
 await page.screenshot({path:'/tmp/e2e/shot_notifs.png'});
 const unreadBefore = await page.evaluate(()=>DB.notifUnread);
 await page.evaluate(()=>{ const i=App._notifs.findIndex(n=>n.title.includes('Приглашение')); App.notifGo(i,'srv'); });
 await sleep(600);
 ok('переход к совещанию из уведомления', (await page.evaluate(()=>document.getElementById('drawer-root').textContent)).includes('E2E совещание уведомлений'));
 ok('уведомление отмечено прочитанным', (await page.evaluate(()=>DB.notifUnread))<unreadBefore);
 await page.evaluate(()=>{App.closeDrawer&&App.closeDrawer(true);App.notifs();});
 await sleep(200);
 await page.evaluate(()=>App.notifReadAll());
 await sleep(600);
 ok('«Прочитать все» обнулило счётчик', (await page.evaluate(()=>DB.notifUnread))===0);
 ok('бейдж скрыт после прочтения', await page.evaluate(()=>{const b=document.getElementById('notif-bdg');return b&&b.style.display==='none';}));

 await page.evaluate(()=>App.notifPrefs());
 await sleep(400);
 ok('модалка настроек уведомлений', (await page.evaluate(()=>document.getElementById('modal-root').textContent)).includes('Настройка уведомлений'));
 ok('чекбоксы типов отрисованы', await page.$('#np-task\\.overdue')!==null);
 await page.evaluate(()=>{document.getElementById('np-task.overdue').checked=false;});
 await page.evaluate(()=>App.notifPrefsSave());
 await sleep(600);
 const prefsOff = await page.evaluate(async()=>{const r=await rpc('notif.list',{limit:1});return r&&r.prefs['task.overdue']===false;});
 ok('отключённый тип сохранён на сервере', prefsOff===true);
 await page.evaluate(()=>rpc('notif.prefs',{prefs:{'task.overdue':true}}));
 await sleep(300);

 console.log('== Напоминания совещания и события ==');
 await page.evaluate(m=>{App.f.meetTab='info';openMeeting(m);},mid);
 await sleep(400);
 ok('блок напоминаний в карточке совещания', (await page.evaluate(()=>document.getElementById('drawer-root').textContent)).includes('Напоминания'));
 ok('типовые интервалы отмечены', await page.evaluate(()=>{const a=document.getElementById('rem-meeting-1440'),b=document.getElementById('rem-meeting-60');return a&&a.checked&&b&&b.checked;}));
 await page.evaluate(()=>{document.getElementById('rem-meeting-10080').checked=true;});
 await page.evaluate(m=>App.reminderSave('meeting',m),mid);
 await sleep(700);
 const remM = await page.evaluate(m=>DB.reminders.filter(r=>r.owner_id===m).map(r=>r.offset_min),mid);
 ok('напоминания пересчитаны сервером (3 интервала × 4 участника)', remM.length===12, remM.length);
 ok('новый интервал сохранён', [...new Set(remM)].sort((a,b)=>a-b).join(',')==='60,1440,10080');
 // перенос совещания другим пользователем: оповещение участников и пересчёт напоминаний
 const fireBefore = await page.evaluate(m=>DB.reminders.filter(r=>r.owner_id===m).map(r=>r.fire_at).sort().join(','),mid);
 await rpcAs(admCookie,'meeting.update',{id:mid,date:new Date(Date.now()+3*864e5).toISOString().slice(0,10),time:'12:30'});
 await page.evaluate(()=>pollState());
 await sleep(400);
 ok('участник оповещён о переносе', await page.evaluate(()=>DB.notifications.some(n=>n.title.includes('Совещание перенесено'))));
 const fireAfter = await page.evaluate(m=>DB.reminders.filter(r=>r.owner_id===m).map(r=>r.fire_at).sort().join(','),mid);
 ok('напоминания пересчитаны от новой даты', fireBefore!==fireAfter && fireAfter.split(',').length===12);

 await page.evaluate(()=>{App.closeDrawer&&App.closeDrawer(true);App.calEventModal(D(2));});
 await sleep(300);
 ok('чекбоксы напоминаний в форме события', await page.evaluate(()=>{const a=document.getElementById('cev-rem-1440'),b=document.getElementById('cev-rem-60');return a&&a.checked&&b&&b.checked;}));
 await page.evaluate(()=>{
  document.getElementById('cev-title').value='E2E событие с напоминаниями';
  document.getElementById('cev-rem-60').checked=false;
  document.getElementById('cev-rem-10080').checked=true;
 });
 await page.evaluate(()=>App.calEventSave());
 await sleep(900);
 const evInfo = await page.evaluate(()=>{
  const e=DB.calEvents.find(x=>x.title==='E2E событие с напоминаниями');
  return e?{id:e.id,offs:[...new Set(DB.reminders.filter(r=>r.owner_id===e.id).map(r=>r.offset_min))].sort((a,b)=>a-b)}:null;
 });
 ok('событие календаря создано', !!(evInfo&&evInfo.id));
 ok('выбранные интервалы напоминаний применены', evInfo&&evInfo.offs.join(',')==='1440,10080', evInfo&&evInfo.offs.join(','));
 await page.evaluate(()=>nav('meetings'));
 await sleep(250);

 console.log('== Права: наблюдатель ==');
 await page.evaluate(()=>App.logout());
 await page.waitForSelector('.login-box',{timeout:10000});
 await page.evaluate(()=>{ App.pickUser('zaytseva'); });
 await page.click('.login-row .btn.pri');
 await page.waitForSelector('.shell',{timeout:10000});
 await sleep(300);
 ok('наблюдатель: нет M14 в навигации', !(await page.evaluate(()=>[...document.querySelectorAll('.nav a')].some(a=>a.textContent.includes('Администрирование')))));
 const denied = await page.evaluate(async()=>{ const r=await rpc('project.create',{name:'x',cat:'НИОКР',pm:'u3',start:'2026-12-01',end:'2026-12-10',budgetHours:10}); return r===false; });
 ok('наблюдатель: создание отклонено', denied===true);
 await sleep(200);
 ok('тост 403 показан', (await page.evaluate(()=>document.getElementById('toasts').textContent)).includes('403'));

 console.log('== M14: планировщик уведомлений ==');
 await page.evaluate(()=>App.logout());
 await page.waitForSelector('.login-box',{timeout:10000});
 await page.evaluate(()=>{ App.pickUser('admin'); });
 await page.click('.login-row .btn.pri');
 await page.waitForSelector('.shell',{timeout:10000});
 await page.evaluate(()=>{ nav('admin'); App.f.adm='sys'; renderView(); });
 await sleep(400);
 ok('карточка планировщика в M14', (await page.evaluate(()=>document.getElementById('view').textContent)).includes('Планировщик уведомлений и напоминаний'));
 await page.evaluate(()=>App.notifTick());
 await sleep(800);
 ok('внеочередной цикл планировщика отработал', (await page.evaluate(()=>document.getElementById('toasts').textContent)).includes('Планировщик отработал'));
 await page.screenshot({path:'/tmp/e2e/shot_notif_admin.png'});

 console.log('== M14: файловые журналы ==');
 ok('карточка файловых журналов', (await page.evaluate(()=>document.getElementById('view').textContent)).includes('Файловые журналы'));
 await page.evaluate(()=>App.logs('supip.log'));
 await sleep(700);
 const logTxt = await page.evaluate(()=>document.getElementById('modal-root').textContent);
 ok('журнал работы открыт в окне', logTxt.includes('Файловые журналы') && logTxt.includes('supip.log'));
 ok('строки журнала отображаются', /\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/.test(logTxt));
 ok('есть ссылка скачивания журнала', await page.$('a[href="/api/logs/supip.log"]')!==null);
 await page.screenshot({path:'/tmp/e2e/shot_logs.png'});
 await page.evaluate(()=>App.logs('setup.log'));
 await sleep(600);
 ok('переключение на журнал настройки', (await page.evaluate(()=>document.getElementById('modal-root').textContent)).includes('setup.log'));
 await page.evaluate(()=>App.closeModal(true));
 await page.evaluate(()=>App.clientLog());
 await sleep(400);
 ok('журнал клиента (буфер браузера)', (await page.evaluate(()=>document.getElementById('modal-root').textContent)).includes('Журнал клиента'));
 await page.evaluate(()=>App.closeModal(true));

 console.log('== Сессия и выход ==');
 await page.evaluate(()=>App.logout());
 await sleep(300);
 ok('после выхода экран входа', await page.$('.login-box')!==null);

 console.log('== Ошибки страницы ==');
 ok('нет pageerror/console.error', errors.length===0, errors.slice(0,4).join(' | '));

 await browser.close();
 await rpcAs(admCookie,'admin.reset');   // возврат к эталонному набору
 console.log('\nИТОГ E2E: '+PASS+' ok, '+FAIL+' FAIL');
 process.exit(FAIL?1:0);
})().catch(e=>{console.error('E2E CRASH:',e);process.exit(2);});

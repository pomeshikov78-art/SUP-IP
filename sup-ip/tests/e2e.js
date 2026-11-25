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

 console.log('== M16 §2.5: планирование совещаний ==');
 const planBase = Date.now();
 const planDate = new Date(planBase+14*864e5).toISOString().slice(0,10);
 const moveDate = new Date(planBase+17*864e5).toISOString().slice(0,10);
 const planWd = new Date(planDate+'T00:00:00').getDay()||7;
 const drawerTxt = ()=>page.evaluate(()=>document.getElementById('drawer-root').textContent);
 const modalTxt = ()=>page.evaluate(()=>document.getElementById('modal-root').textContent);
 const txtOf = sel => page.$eval(sel,e=>e.textContent);
 const serCount = mid => page.evaluate(m=>{
  const r=(DB.meetings.find(x=>x.id===m)||{}).recurrence||{};
  return DB.meetings.filter(x=>(x.recurrence||{}).seriesId&&x.recurrence.seriesId===r.seriesId).length;
 },mid);
 await page.evaluate(()=>{App.closeDrawer();App.f.mt={tab:'all'};nav('meetings');});
 await sleep(400);
 ok('реестр: сводка планирования §2.5', await page.$('#mt-plan-stat')!==null);
 ok('реестр: фильтр по связанному проекту', await page.$('#mtf-proj')!==null);
 ok('реестр: режимы «Регулярные» и «Отменённые»', (await page.$('#mt-tab-series'))!==null && (await page.$('#mt-tab-canc'))!==null);

 // создание совещания: связанный проект и еженедельное повторение
 await page.evaluate(()=>document.getElementById('mt-new').click());
 await sleep(350);
 ok('форма создания: проекты и правило повторения', (await page.$('.mt-prj'))!==null && (await page.$('#mt-freq'))!==null);
 await page.evaluate((d,w)=>{
  document.getElementById('mt-title').value='E2E плановое совещание';
  document.getElementById('mt-date').value=d;
  document.getElementById('mt-time').value='11:30';
  document.querySelector('.mt-prj[value="p1"]').checked=true;
  document.getElementById('mt-freq').value='weekly';
  document.getElementById('mt-int').value='1';
  document.querySelector('.mt-wd[value="'+w+'"]').checked=true;
 },planDate,planWd);
 await page.evaluate(()=>App.meetSave(null));
 await sleep(900);
 const planM = await page.evaluate(()=>{
  const m=DB.meetings.find(x=>x.title==='E2E плановое совещание');
  if(!m)return null;
  const r=m.recurrence||{};
  return {id:m.id,code:m.code,prj:m.projects||[],master:!!r.master,sid:r.seriesId||'',freq:r.freq};
 });
 const planMid = planM?planM.id:'';
 ok('совещание §2.5 создано с кодом СОВ', !!(planM&&/^СОВ-\d{4}-\d+$/.test(planM.code)), planM&&planM.code);
 ok('связанный проект зарегистрирован при создании', planM&&planM.prj.join(',')==='p1');
 ok('головная карточка регулярной серии', !!(planM&&planM.master&&planM.sid&&planM.freq==='weekly'));
 ok('ближайшие совещания серии созданы автоматически', (await serCount(planMid))>=3, String(await serCount(planMid)));

 // вкладка «Планирование» карточки совещания
 await page.evaluate(m=>{App.f.meetTab='plan';openMeeting(m);},planMid);
 await sleep(450);
 ok('карточка: вкладка «Планирование»', (await page.$('#mt-tab-plan'))!==null && (await page.$('#meet-plan'))!==null);
 ok('планирование: дата, время и правило повторения', (await txtOf('#mp-when')).includes('11:30') && (await txtOf('#mp-rec')).includes('Еженедельно'));
 ok('планирование: счётчики проектов и материалов', (await txtOf('#mp-prj-n'))==='1' && (await txtOf('#mp-mat-n'))==='0');
 ok('кнопки переноса, отмены и расписания', (await page.$('#mp-move'))!==null && (await page.$('#mp-cancel'))!==null && (await page.$('#mp-rec-edit'))!==null);
 ok('управление серией: создать, пропустить, состав', (await page.$('#mp-series-gen'))!==null && (await page.$('#mp-series-skip'))!==null && (await page.$('#mp-series-view'))!==null);

 // предварительные материалы: документ M09 и ссылка на файл ЛВС
 await page.evaluate(()=>document.getElementById('mp-mat-add').click());
 await sleep(300);
 ok('окно материала: документ M09 или ссылка', (await page.$('#mat-kind'))!==null && (await page.$('#mat-doc'))!==null);
 await page.evaluate(()=>{document.getElementById('mat-doc').value='d1';document.getElementById('mat-note').value='Материал к рассмотрению';});
 await page.evaluate(()=>document.getElementById('mat-save').click());
 await sleep(700);
 ok('документ M09 прикреплён как материал', (await txtOf('#mp-mat-n'))==='1' && (await drawerTxt()).includes('Д-001'));
 await page.evaluate(m=>App.meetMatModal(m),planMid);
 await sleep(300);
 await page.evaluate(()=>{
  document.getElementById('mat-kind').value='link';App.meetMatKind();
  document.getElementById('mat-name').value='Смета на сетевом ресурсе';
  document.getElementById('mat-url').value='\\\\lvs-srv\\share\\smeta.pdf';
 });
 ok('переключение вида материала на ссылку ЛВС', await page.$eval('#mat-link-box',e=>e.style.display!=='none'));
 await page.evaluate(()=>document.getElementById('mat-save').click());
 await sleep(700);
 ok('ссылка ЛВС прикреплена как материал', (await txtOf('#mp-mat-n'))==='2' && (await drawerTxt()).includes('Смета на сетевом ресурсе'));
 const dlgPlan = d=>d.accept();
 page.on('dialog',dlgPlan);
 await page.evaluate(()=>document.querySelectorAll('.mp-mat-del')[1].click());
 await sleep(700);
 page.off('dialog',dlgPlan);
 ok('материал открепляется от совещания', (await txtOf('#mp-mat-n'))==='1');

 // связанные проекты: изменение списка
 await page.evaluate(()=>document.getElementById('mp-prj-edit').click());
 await sleep(300);
 await page.evaluate(()=>{document.querySelector('.mpp-chk[value="p2"]').checked=true;});
 await page.evaluate(()=>document.getElementById('mpp-save').click());
 await sleep(700);
 ok('список связанных проектов изменён', (await txtOf('#mp-prj-n'))==='2' && (await txtOf('#mp-log-n'))==='1');

 // перенос с основанием и пересчётом напоминаний
 const remBefore = await page.evaluate(m=>DB.reminders.filter(r=>r.owner_id===m).map(r=>r.fire_at).sort((a,b)=>a-b),planMid);
 await page.evaluate(()=>document.getElementById('mp-move').click());
 await sleep(300);
 ok('окно переноса требует основание', (await modalTxt()).includes('Основание переноса'));
 await page.evaluate(d=>{
  document.getElementById('mv-date').value=d;
  document.getElementById('mv-time').value='12:15';
  document.getElementById('mv-reason').value='Перенос по просьбе заказчика';
 },moveDate);
 await page.evaluate(()=>document.getElementById('mv-go').click());
 await sleep(800);
 const remAfter = await page.evaluate(m=>DB.reminders.filter(r=>r.owner_id===m).map(r=>r.fire_at).sort((a,b)=>a-b),planMid);
 ok('перенос применён к дате и времени', (await txtOf('#mp-when')).includes('12:15'));
 ok('перенос записан в историю планирования', (await txtOf('#mp-log-n'))==='2' && (await drawerTxt()).includes('Перенос по просьбе заказчика'));
 ok('напоминания пересчитаны от новой даты', remBefore.length>0 && remAfter.length===remBefore.length &&
    remAfter.every((v,i)=>v-remBefore[i]===3*864e5+45*6e4), remBefore.length+'/'+remAfter.length);

 // отмена и возобновление
 await page.evaluate(()=>document.getElementById('mp-cancel').click());
 await sleep(300);
 await page.evaluate(()=>{document.getElementById('mc-reason').value='Отменено из-за отпуска председателя';});
 await page.evaluate(()=>document.getElementById('mc-go').click());
 await sleep(800);
 const cancState = await page.evaluate(m=>({st:DB.meetings.find(x=>x.id===m).status,rem:DB.reminders.filter(r=>r.owner_id===m).length}),planMid);
 ok('совещание отменено, напоминания сняты', cancState.st==='Отменено'&&cancState.rem===0, JSON.stringify(cancState));
 ok('отменённое: возобновление вместо переноса', (await page.$('#mp-restore'))!==null && (await page.$('#mp-move'))===null);
 await page.evaluate(()=>document.getElementById('mp-restore').click());
 await sleep(300);
 await page.evaluate(d=>{
  document.getElementById('mr-date').value=d;
  document.getElementById('mr-time').value='11:30';
  document.getElementById('mr-reason').value='Возобновлено по решению председателя';
 },moveDate);
 await page.evaluate(()=>document.getElementById('mr-go').click());
 await sleep(800);
 ok('совещание возобновлено в расписании', (await page.evaluate(m=>DB.meetings.find(x=>x.id===m).status,planMid))==='Запланировано');
 ok('история: проекты, перенос, отмена, возобновление', (await txtOf('#mp-log-n'))==='4');

 // расписание серии, генерация дат, состав и пропуск
 await page.evaluate(()=>document.getElementById('mp-rec-edit').click());
 await sleep(300);
 ok('окно расписания: периодичность и интервал', (await page.$eval('#rec-freq',e=>e.value))==='weekly' && (await page.$('#rec-save'))!==null);
 await page.evaluate(()=>{document.getElementById('rec-int').value='2';});
 await page.evaluate(()=>document.getElementById('rec-save').click());
 await sleep(800);
 ok('расписание серии изменено', (await txtOf('#mp-rec')).includes('Каждые 2 нед'));
 const serBefore = await serCount(planMid);
 await page.evaluate(()=>document.getElementById('mp-series-gen').click());
 await sleep(900);
 const serAfter = await serCount(planMid);
 ok('генерация ближайших дат не создаёт дублей', serAfter>=serBefore && serAfter<=serBefore+8, serBefore+'→'+serAfter);
 await page.evaluate(()=>document.getElementById('mp-series-view').click());
 await sleep(400);
 ok('состав серии: отдельная карточка на каждую дату', (await page.$$eval('#mp-series-table tbody tr',rs=>rs.length))===serAfter);
 await page.evaluate(()=>App.closeModal(true));
 await sleep(300);
 const skipDate = await page.evaluate(m=>{
  const r=(DB.meetings.find(x=>x.id===m)||{}).recurrence||{};
  const s=DB.meetings.filter(x=>(x.recurrence||{}).seriesId===r.seriesId&&x.id!==m&&x.status==='Запланировано')
   .sort((a,b)=>a.date<b.date?-1:1);
  return s.length?s[0].date:'';
 },planMid);
 await page.evaluate(()=>document.getElementById('mp-series-skip').click());
 await sleep(300);
 await page.evaluate(d=>{document.getElementById('sk-date').value=d;document.getElementById('sk-reason').value='Праздничный день';},skipDate);
 await page.evaluate(()=>document.getElementById('sk-go').click());
 await sleep(900);
 const skipState = await page.evaluate((m,d)=>{
  const r=(DB.meetings.find(x=>x.id===m)||{}).recurrence||{};
  const c=DB.meetings.find(x=>(x.recurrence||{}).seriesId===r.seriesId&&x.date===d);
  return {sk:(r.skipped||[]).length,st:c?c.status:'нет'};
 },planMid,skipDate);
 ok('дата серии пропущена с основанием', skipState.sk===1&&skipState.st==='Отменено', JSON.stringify(skipState));

 // реестр: режимы и фильтр по проекту
 await page.evaluate(()=>{App.closeDrawer();App.f.mt={tab:'canc'};renderView();});
 await sleep(400);
 const cancExpect = await page.evaluate(()=>DB.meetings.filter(m=>m.status==='Отменено').length);
 ok('реестр: режим «Отменённые»', (await page.$$eval('#view table.tbl tbody tr',rs=>rs.length))===cancExpect, String(cancExpect));
 await page.evaluate(()=>{App.f.mt={tab:'series'};renderView();});
 await sleep(400);
 const serExpect = await page.evaluate(()=>DB.meetings.filter(m=>(m.recurrence||{}).seriesId).length);
 ok('реестр: режим «Регулярные»', (await page.$$eval('#view table.tbl tbody tr',rs=>rs.length))===serExpect, String(serExpect));
 await page.evaluate(()=>{App.f.mt={tab:'all',pj:'p1'};renderView();});
 await sleep(400);
 const pjExpect = await page.evaluate(()=>DB.meetings.filter(m=>(m.projects||[]).includes('p1')).length);
 ok('реестр: фильтр по связанному проекту', (await page.$$eval('#view table.tbl tbody tr',rs=>rs.length))===pjExpect, String(pjExpect));
 await page.evaluate(()=>{App.f.mt={tab:'next'};renderView();});
 await sleep(300);

 console.log('== M16 §2.7 / MTG-05: участники совещания ==');
 const ptToday = new Date().toISOString().slice(0,10);
 const ptDate = off => new Date(Date.now()+off*864e5).toISOString().slice(0,10);
 const ptRes = await rpcAs(admCookie,'meeting.create',{title:'E2E участники MTG-05',type:'Техническое',
  date:ptToday,time:'12:00',chair:'u2',secretary:'u3',attendees:['u2','u3','u5']});
 const ptMid = (ptRes.result||{}).id;
 ok('совещание для участников создано', !!ptMid);
 await page.evaluate(()=>pollState());
 await sleep(400);
 await page.evaluate(m=>{App.f.meetTab='part';openMeeting(m);},ptMid);
 await sleep(450);
 ok('карточка: вкладка «Участники»', (await page.$('#mt-tab-part'))!==null && (await page.$('#meet-parts'))!==null);
 ok('председатель и секретарь в составе', (await txtOf('#mpa-n'))==='3' && (await txtOf('#mpa-req'))==='2');
 ok('приглашения при создании направлены', (await txtOf('#mpa-inv'))==='3');
 ok('таблица MTG-05 8.1: подразделение и подтверждение', (await page.$$('#mpa-table tbody tr')).length===3 &&
    (await page.$eval('#mpa-table thead',e=>e.textContent)).includes('Подразделение') &&
    (await page.$eval('#mpa-table thead',e=>e.textContent)).includes('Подтверждение участия'));
 ok('фактическое участие изначально не определено', (await drawerTxt()).includes('Не определено') && (await txtOf('#mpa-actual')).includes('не зарегистрирован'));
 ok('история состава участников на экране', await page.$('#meet-parts-hist')!==null);

 // добавление участника из справочника
 await page.evaluate(()=>document.getElementById('mpa-add').click());
 await sleep(350);
 ok('форма выбора из справочника сотрудников', (await page.$$('.pa-user')).length>0 && (await page.$('#pa-role'))!==null);
 ok('в форме видны должность и подразделение', (await modalTxt()).includes('Расчётная группа'));
 await page.evaluate(()=>{
  document.querySelector('.pa-user[value="u6"]').checked=true;
  document.getElementById('pa-role').value='Докладчик';
  document.getElementById('pa-req').checked=true;
  document.getElementById('pa-comment').value='Доклад по бюджету радиолинии';
 });
 await page.evaluate(()=>document.getElementById('pa-save').click());
 await sleep(900);
 ok('участник добавлен в состав', (await txtOf('#mpa-n'))==='4' && (await page.$('#mpa-row-u6'))!==null);
 ok('роль и обязательность сохранены', (await page.$eval('#mpa-row-u6',e=>e.textContent)).includes('Докладчик') &&
    (await page.$eval('#mpa-row-u6',e=>e.textContent)).includes('обязательный'));
 ok('добавленному приглашение не направлено', (await txtOf('#mpa-inv'))==='3');

 // направление приглашения и ответ участника
 await page.evaluate(()=>document.querySelector('#mpa-row-u6 .mpa-inv').click());
 await sleep(350);
 ok('окно приглашения', (await page.$('#pi-note'))!==null && (await page.$$('.pi-chk')).length===4);
 await page.evaluate(()=>{document.getElementById('pi-note').value='Подготовьте расчёт к совещанию';});
 await page.evaluate(()=>document.getElementById('pi-save').click());
 await sleep(900);
 ok('приглашение направлено', (await txtOf('#mpa-inv'))==='4' && (await page.$eval('#mpa-row-u6',e=>e.textContent)).includes('Направлено'));
 await page.evaluate(()=>document.querySelector('#mpa-row-u6 .mpa-ans').click());
 await sleep(350);
 ok('окно ответа на приглашение', (await page.$('#pr-st'))!==null && (await modalTxt()).includes('Подтверждение участия'));
 await page.evaluate(()=>{document.getElementById('pr-st').value='Подтверждено';document.getElementById('pr-note').value='Буду лично';});
 await page.evaluate(()=>document.getElementById('pr-save').click());
 await sleep(900);
 ok('ответ участника зарегистрирован', (await txtOf('#mpa-ans')).startsWith('1 /') && (await page.$eval('#mpa-row-u6',e=>e.textContent)).includes('Подтверждено'));
 ok('свой ответ доступен участнику', await page.$('#mpa-my')!==null);
 await page.evaluate(()=>document.getElementById('mpa-my').click());
 await sleep(300);
 await page.evaluate(()=>{document.getElementById('pr-st').value='Требует уточнения';document.getElementById('pr-note').value='Уточните время начала';});
 await page.evaluate(()=>document.getElementById('pr-save').click());
 await sleep(900);
 ok('ответ «Требует уточнения» зарегистрирован', (await txtOf('#mpa-ans')).endsWith('/ 1'));

 // изменение роли и сведений
 await page.evaluate(()=>document.querySelector('#mpa-row-u6 .mpa-edit').click());
 await sleep(300);
 ok('окно изменения сведений участника', (await page.$('#pe-role'))!==null && (await page.$('#pe-req'))!==null);
 await page.evaluate(()=>{document.getElementById('pe-role').value='Эксперт';});
 await page.evaluate(()=>document.getElementById('pe-save').click());
 await sleep(900);
 ok('роль участника изменена', (await page.$eval('#mpa-row-u6',e=>e.textContent)).includes('Эксперт'));

 // до даты совещания участие не отмечается
 const ptFutRes = await rpcAs(admCookie,'meeting.create',{title:'E2E будущее совещание',type:'Оперативное',
  date:ptDate(6),time:'09:00',chair:'u2',secretary:'u3',attendees:['u2','u3']});
 const ptFut = (ptFutRes.result||{}).id;
 await page.evaluate(()=>pollState());
 await sleep(350);
 await page.evaluate(m=>{App.f.meetTab='part';openMeeting(m);},ptFut);
 await sleep(400);
 ok('до даты совещания отметки участия недоступны', (await page.$$('.mpa-att')).length===0 && (await page.$('#mpa-actual-btn'))===null);

 // фактическое участие после проведения
 await rpcAs(admCookie,'meeting.conduct',{id:ptMid});
 await page.evaluate(()=>pollState());
 await sleep(400);
 await page.evaluate(m=>{App.f.meetTab='part';openMeeting(m);},ptMid);
 await sleep(450);
 ok('проведение не отмечает присутствие автоматически', (await txtOf('#mpa-att')).endsWith('/ 4'), await txtOf('#mpa-att'));
 for(const [uid_,st] of [['u2','Присутствовал'],['u3','Подключился дистанционно'],['u5','Отсутствовал'],['u6','Участвовал частично']]){
  await page.evaluate(u=>document.querySelector('#mpa-row-'+u+' .mpa-att').click(),uid_);
  await sleep(300);
  await page.evaluate(s=>{document.getElementById('pt-st').value=s;},st);
  await page.evaluate(()=>document.getElementById('pt-save').click());
  await sleep(800);
 }
 ok('фактическое участие отмечено по всем участникам', (await txtOf('#mpa-att'))==='3 / 1 / 0', await txtOf('#mpa-att'));
 ok('дистанционное и частичное участие отображаются', (await drawerTxt()).includes('Подключился дистанционно') && (await drawerTxt()).includes('Участвовал частично'));

 // массовая операция с подтверждением
 const dlgPt = d=>{ d.accept().catch(()=>{}); };
 await page.evaluate(()=>document.getElementById('mpa-bulk').click());
 await sleep(350);
 ok('окно массовой операции', (await page.$('#pb-kind'))!==null && (await page.$$('.pb-chk')).length===4);
 await page.evaluate(()=>{
  document.getElementById('pb-kind').value='participation'; App.partBulkKind();
  document.getElementById('pb-st').value='Присутствовал';
  document.querySelectorAll('.pb-chk').forEach(x=>{x.checked=(x.value==='u5');});
  document.getElementById('pb-comment').value='Вернулся с объекта';
 });
 page.on('dialog',dlgPt);
 await page.evaluate(()=>document.getElementById('pb-save').click());
 await sleep(1400);
 page.off('dialog',dlgPt);
 ok('массовая операция выполнена после подтверждения', (await txtOf('#mpa-att'))==='4 / 0 / 0', await txtOf('#mpa-att'));
 ok('комментарий массовой операции применён', (await page.$eval('#mpa-row-u5',e=>e.textContent)).includes('Вернулся с объекта'));

 // регистрация фактического состава
 await page.evaluate(()=>document.getElementById('mpa-actual-btn').click());
 await sleep(350);
 ok('окно регистрации фактического состава', (await page.$('#pac-note'))!==null && (await modalTxt()).includes('Участвовали'));
 await page.evaluate(()=>{document.getElementById('pac-note').value='Состав зафиксирован секретарём';});
 await page.evaluate(()=>document.getElementById('pac-save').click());
 await sleep(1000);
 ok('фактический состав зарегистрирован', (await txtOf('#mpa-actual')).includes('зарегистрирован'), await txtOf('#mpa-actual'));

 // карточка участника и исключение из состава
 await page.evaluate(()=>document.querySelector('#mpa-row-u6 .mpa-view').click());
 await sleep(350);
 ok('карточка участника с историей', (await page.$('#mpa-card'))!==null && (await page.$('#mpa-hist'))!==null);
 ok('в карточке видны приглашение и участие', (await modalTxt()).includes('Ответ на приглашение') && (await modalTxt()).includes('Фактическое участие'));
 await page.evaluate(()=>App.closeModal(true));
 await sleep(250);
 await page.evaluate(m=>App.partAdd(m),ptMid);
 await sleep(300);
 await page.evaluate(()=>{document.querySelector('.pa-user[value="u4"]').checked=true;});
 await page.evaluate(()=>document.getElementById('pa-save').click());
 await sleep(900);
 ok('ещё один участник добавлен', (await txtOf('#mpa-n'))==='5');
 page.on('dialog',dlgPt);
 await page.evaluate(()=>document.querySelector('#mpa-row-u4 .mpa-del').click());
 await sleep(1000);
 page.off('dialog',dlgPt);
 ok('участник без отметки исключён из состава', (await txtOf('#mpa-n'))==='4');
 ok('у председателя и секретаря нет кнопки исключения', (await page.$('#mpa-row-u2 .mpa-del'))===null && (await page.$('#mpa-row-u3 .mpa-del'))===null);
 ok('история состава заполнена', (await page.$$eval('#meet-parts-hist tbody tr',rs=>rs.length))>5);

 // протокол с фактическим составом
 await page.evaluate(m=>{App.f.meetTab='prot';openMeeting(m);},ptMid);
 await sleep(500);
 const ptProt = await drawerTxt();
 ok('протокол: состав с подразделением и ролями', ptProt.includes('Присутствовали') && ptProt.includes('Подразделение') && ptProt.includes('Роль на совещании'));
 ok('протокол: регистрация фактического состава', ptProt.includes('Фактический состав зарегистрирован'));

 // справочник сотрудников в M05
 await page.evaluate(()=>{App.closeDrawer();nav('resources');});
 await sleep(450);
 ok('M05: справочник сотрудников с подразделением', (await page.$('#res-directory'))!==null &&
    (await page.$eval('#res-directory',e=>e.textContent)).includes('Расчётная группа'));
 await page.evaluate(()=>{App.f.mt={tab:'next'};nav('meetings');});
 await sleep(300);

 console.log('== M16 §2.6 / MTG-06: формирование повестки ==');
 const agToday = new Date().toISOString().slice(0,10);
 const agDate = off => new Date(Date.now()+off*864e5).toISOString().slice(0,10);
 const agRes = await rpcAs(admCookie,'meeting.create',{title:'E2E повестка MTG-06',type:'Техническое',
  date:agToday,time:'10:00',chair:'u2',secretary:'u3',attendees:['u2','u3','u5'],projects:['p1']});
 const agMid = (agRes.result||{}).id;
 ok('совещание для повестки создано', !!agMid);
 const agTargetRes = await rpcAs(admCookie,'meeting.create',{title:'E2E получатель переноса',type:'Техническое',
  date:agDate(7),time:'10:00',chair:'u2',secretary:'u3',attendees:['u2','u3','u5']});
 const agTarget = (agTargetRes.result||{}).id;
 await page.evaluate(()=>pollState());
 await sleep(400);
 await page.evaluate(m=>{App.f.meetTab='agenda';openMeeting(m);},agMid);
 await sleep(450);
 ok('карточка: вкладка «Повестка»', (await page.$('#mt-tab-agenda'))!==null && (await page.$('#meet-agenda'))!==null);
 ok('повестка пуста: счётчики обнулены', (await txtOf('#ma-n'))==='0' && (await txtOf('#ma-open'))==='0');
 ok('кнопка добавления вопроса', await page.$('#ma-add')!==null);
 ok('история повестки на экране', await page.$('#meet-agenda-hist')!==null);

 // добавление вопроса со всеми полями §2.6 / MTG-06 9.2
 await page.evaluate(()=>document.getElementById('ma-add').click());
 await sleep(350);
 ok('форма вопроса: полный состав полей', (await page.$('#ag-title'))!==null && (await page.$('#ag-content'))!==null &&
    (await page.$('#ag-rep'))!==null && (await page.$('#ag-prep'))!==null && (await page.$('#ag-dur'))!==null &&
    (await page.$('#ag-prj'))!==null && (await page.$('#ag-risk'))!==null && (await page.$('#ag-expected'))!==null);
 await page.evaluate(()=>{
  document.getElementById('ag-title').value='Отклонение по расчёту МПП-7';
  document.getElementById('ag-content').value='Сравнение расчёта и стендовых измерений';
  document.getElementById('ag-rep').value='u6';
  document.getElementById('ag-prep').value='u3';
  document.getElementById('ag-dur').value='25';
  document.getElementById('ag-prj').value='p1'; App.agendaPrj();
  document.getElementById('ag-risk').value='r1';
  document.getElementById('ag-expected').value='Утвердить корректировку алгоритма';
 });
 await sleep(200);
 ok('этап и задачи выбираются по связанному проекту', (await page.$('#ag-stage'))!==null && (await page.$$('.ag-task')).length>0);
 await page.evaluate(()=>{
  const st=document.getElementById('ag-stage');
  if(st.options.length>1)st.value=st.options[1].value;
  const t=document.querySelector('.ag-task'); if(t)t.checked=true;
 });
 await page.evaluate(()=>document.getElementById('ag-save').click());
 await sleep(900);
 const ag1 = await page.evaluate(m=>{const a=(DB.meetings.find(x=>x.id===m).agenda||[])[0];return a?{id:a.id,n:a.n,st:a.status,dur:a.durationMin,prj:a.projectId,tasks:a.taskIds,risk:a.riskId,prep:a.prep}:null;},agMid);
 ok('вопрос добавлен в повестку', !!ag1 && ag1.n===1, JSON.stringify(ag1));
 ok('докладчик, подготовка, связи и продолжительность сохранены', !!ag1 && ag1.dur===25 && ag1.prj==='p1' && ag1.risk==='r1' && ag1.prep==='u3' && ag1.tasks.length===1);
 ok('счётчик вопросов и плановое время', (await txtOf('#ma-n'))==='1' && (await txtOf('#ma-min')).includes('25 мин'));
 ok('таблица повестки MTG-06 9.1', (await page.$$('#ma-table tbody tr')).length===1 && (await drawerTxt()).includes('Отклонение по расчёту МПП-7'));
 ok('ожидаемый результат виден в списке', (await drawerTxt()).includes('Утвердить корректировку алгоритма'));

 // ещё два вопроса
 for(const [t,dur] of [['Готовность стендовых измерений',15],['План работ по замечаниям',10]]){
  await page.evaluate(m=>App.agendaAdd(m),agMid);
  await sleep(250);
  await page.evaluate((t,d)=>{document.getElementById('ag-title').value=t;document.getElementById('ag-dur').value=String(d);document.getElementById('ag-rep').value='u5';},t,dur);
  await page.evaluate(()=>document.getElementById('ag-save').click());
  await sleep(700);
 }
 ok('в повестке три вопроса', (await txtOf('#ma-n'))==='3' && (await txtOf('#ma-min')).includes('50 мин'));

 // карточка вопроса и изменение
 await page.evaluate(()=>document.querySelector('.ma-view').click());
 await sleep(350);
 ok('карточка вопроса MTG-06 9.2', (await page.$('#ag-card'))!==null && (await modalTxt()).includes('Ответственный за подготовку'));
 ok('в карточке видны связи и история', (await modalTxt()).includes('Связанные задачи') && (await page.$('#ag-hist'))!==null);
 await page.evaluate(()=>document.getElementById('agc-edit').click());
 await sleep(300);
 await page.evaluate(()=>{document.getElementById('ag-dur').value='30';});
 await page.evaluate(()=>document.getElementById('ag-save').click());
 await sleep(800);
 ok('вопрос изменён через карточку', (await txtOf('#ma-min')).includes('55 мин'), await txtOf('#ma-min'));

 // порядок вопросов: вверх/вниз и на позицию
 const agOrder = ()=>page.evaluate(m=>(DB.meetings.find(x=>x.id===m).agenda||[]).map(a=>a.title),agMid);
 const ord0 = await agOrder();
 await page.evaluate(()=>document.querySelectorAll('.ma-down')[0].click());
 await sleep(800);
 const ord1 = await agOrder();
 ok('порядок вопросов изменён кнопкой «вниз»', ord1[0]===ord0[1] && ord1[1]===ord0[0], ord1.join(' | '));
 await page.evaluate(()=>document.querySelectorAll('.ma-up')[0].click());
 await sleep(800);
 ok('порядок восстановлен кнопкой «вверх»', (await agOrder()).join('|')===ord0.join('|'));
 await page.evaluate(()=>document.querySelectorAll('.ma-pos')[0].click());
 await sleep(300);
 ok('окно перемещения на позицию', (await page.$('#agp-to'))!==null && (await modalTxt()).includes('ссылки решений и поручений'));
 await page.evaluate(()=>{document.getElementById('agp-to').value='3';});
 await page.evaluate(()=>document.getElementById('agp-save').click());
 await sleep(800);
 ok('вопрос перемещён на указанную позицию', (await agOrder())[2]===ord0[0]);
 ok('изменение порядка сохранено в истории', (await page.$eval('#meet-agenda-hist',e=>e.textContent)).includes('Изменён порядок'));

 // материалы вопроса с фиксацией версии документа M09
 await page.evaluate(()=>document.querySelector('.ma-mat').click());
 await sleep(350);
 ok('окно материалов вопроса', (await page.$('#agm-kind'))!==null && (await modalTxt()).includes('фиксируется конкретная версия'));
 await page.evaluate(()=>{document.getElementById('agm-doc').value='d1';document.getElementById('agm-note').value='Расчёт к вопросу';});
 await page.evaluate(()=>document.getElementById('agm-save').click());
 await sleep(900);
 ok('документ M09 прикреплён к вопросу', (await txtOf('#ma-mat'))==='1' && (await modalTxt()).includes('версия'));
 const agDocV = await page.evaluate(m=>{const a=DB.meetings.find(x=>x.id===m).agenda.find(x=>(x.materials||[]).length);return a.materials[0].docV;},agMid);
 await rpcAs(admCookie,'doc.version',{id:'d1',fileName:'raschet_v2.pdf',note:'Обновление расчёта'});
 await page.evaluate(()=>pollState());
 await sleep(400);
 const agDocV2 = await page.evaluate(m=>{const a=DB.meetings.find(x=>x.id===m).agenda.find(x=>(x.materials||[]).length);return a.materials[0].docV;},agMid);
 ok('версия документа в комплекте материалов не изменилась', agDocV2===agDocV, agDocV+'→'+agDocV2);
 await page.evaluate(()=>{document.getElementById('agm-kind').value='link';App.agendaMatKind();
  document.getElementById('agm-name').value='Протокол измерений';document.getElementById('agm-url').value='\\\\lvs-srv\\share\\izmer.pdf';});
 ok('переключение вида материала на ссылку ЛВС', await page.$eval('#agm-link-box',e=>e.style.display!=='none'));
 await page.evaluate(()=>document.getElementById('agm-save').click());
 await sleep(900);
 ok('ссылка ЛВС прикреплена к вопросу', (await txtOf('#ma-mat'))==='2');
 const dlgAg = d=>{ d.accept().catch(()=>{}); };
 page.on('dialog',dlgAg);
 await page.evaluate(()=>document.querySelectorAll('.agm-del')[1].click());
 await sleep(900);
 ok('материал откреплён от вопроса', (await txtOf('#ma-mat'))==='1');
 page.off('dialog',dlgAg);
 await page.evaluate(()=>App.closeModal(true));
 await sleep(250);

 // статусы рассмотрения (MTG-06 9.3)
 await page.evaluate(()=>document.querySelector('.ma-st').click());
 await sleep(300);
 const agStOpts = await page.$$eval('#ags-st option',os=>os.map(o=>o.value));
 ok('переходы статусов ограничены правилами', agStOpts.includes('Рассматривается') && !agStOpts.includes('Перенесён'), agStOpts.join(','));
 await page.evaluate(()=>{document.getElementById('ags-st').value='Рассматривается';App.agendaStKind();});
 await page.evaluate(()=>document.getElementById('ags-save').click());
 await sleep(800);
 ok('вопрос переведён в «Рассматривается»', (await drawerTxt()).includes('Рассматривается'));
 await page.evaluate(()=>document.querySelector('.ma-st').click());
 await sleep(300);
 await page.evaluate(()=>{document.getElementById('ags-st').value='Рассмотрен';App.agendaStKind();
  document.getElementById('ags-fact').value='Корректировка алгоритма утверждена';});
 ok('поле фактического результата показано', await page.$eval('#ags-fact-box',e=>e.style.display!=='none'));
 await page.evaluate(()=>document.getElementById('ags-save').click());
 await sleep(800);
 ok('вопрос рассмотрен с фактическим результатом', (await page.evaluate(m=>DB.meetings.find(x=>x.id===m).agenda[0].status,agMid))==='Рассмотрен');
 ok('фактический результат сохранён', (await page.evaluate(m=>DB.meetings.find(x=>x.id===m).agenda[0].fact,agMid)).includes('утверждена'));

 // решение по вопросу: пункт повестки подставляется автоматически
 await page.evaluate(()=>document.querySelector('.ma-dec').click());
 await sleep(350);
 ok('решение привязывается к пункту повестки', (await page.$eval('#de-ag',e=>e.value))==='1');
 await page.evaluate(d=>{document.getElementById('de-text').value='E2E решение по вопросу повестки';
  document.getElementById('de-resp').value='u5';document.getElementById('de-due').value=d;},agDate(5));
 await page.evaluate(m=>App.decisionSave(m),agMid);
 await sleep(900);
 const agDec = await page.evaluate(m=>{const d=(DB.meetings.find(x=>x.id===m).decisions||[])[0];return d?{n:d.agendaN,id:d.agendaId}:null;},agMid);
 ok('решение зарегистрировано по вопросу №1', !!agDec && agDec.n===1, JSON.stringify(agDec));

 // снятие с рассмотрения требует основания
 await page.evaluate(()=>document.querySelectorAll('.ma-st')[2].click());
 await sleep(300);
 await page.evaluate(()=>{document.getElementById('ags-st').value='Снят с рассмотрения';App.agendaStKind();});
 ok('основание снятия с рассмотрения запрашивается', await page.$eval('#ags-reason-box',e=>e.style.display!=='none'));
 await page.evaluate(()=>{document.getElementById('ags-reason').value='Вопрос снят по решению председателя';});
 await page.evaluate(()=>document.getElementById('ags-save').click());
 await sleep(800);
 ok('вопрос снят с рассмотрения', (await page.evaluate(m=>DB.meetings.find(x=>x.id===m).agenda[2].status,agMid))==='Снят с рассмотрения');

 // удаление вопроса без решений
 page.on('dialog',dlgAg);
 const agN0 = await page.evaluate(m=>DB.meetings.find(x=>x.id===m).agenda.length,agMid);
 await page.evaluate(()=>document.querySelectorAll('.ma-del')[1].click());
 await sleep(900);
 page.off('dialog',dlgAg);
 ok('вопрос без решений удалён из повестки', (await page.evaluate(m=>DB.meetings.find(x=>x.id===m).agenda.length,agMid))===agN0-1);
 ok('нумерация после удаления сплошная', (await page.evaluate(m=>DB.meetings.find(x=>x.id===m).agenda.map(a=>a.n),agMid)).join(',')==='1,2');

 // перенос нерассмотренных вопросов на следующее совещание
 await page.evaluate(m=>App.agendaAdd(m),agMid);
 await sleep(300);
 await page.evaluate(()=>{document.getElementById('ag-title').value='Готовность проектной документации';
  document.getElementById('ag-rep').value='u3';document.getElementById('ag-dur').value='20';});
 await page.evaluate(()=>document.getElementById('ag-save').click());
 await sleep(800);
 ok('кнопка переноса нерассмотренных', await page.$('#ma-carry')!==null);
 await page.evaluate(()=>document.getElementById('ma-carry').click());
 await sleep(350);
 ok('окно переноса: получатель и основание', (await page.$('#agc-target'))!==null && (await page.$('#agc-reason'))!==null && (await page.$$('.agc-chk')).length>0);
 await page.evaluate(t=>{document.getElementById('agc-target').value=t;App.agendaCarryKind();
  document.getElementById('agc-reason').value='Не хватило времени совещания';},agTarget);
 await page.evaluate(()=>document.getElementById('agc-save').click());
 await sleep(1000);
 const agCarry = await page.evaluate((m,t)=>{
  const src=DB.meetings.find(x=>x.id===m), dst=DB.meetings.find(x=>x.id===t);
  return {src:(src.agenda||[]).map(a=>a.status),dst:(dst.agenda||[]).map(a=>({n:a.n,st:a.status,from:(a.carryFrom||{}).code}))};
 },agMid,agTarget);
 ok('нерассмотренный вопрос перенесён', agCarry.src.includes('Перенесён') && agCarry.dst.length===1, JSON.stringify(agCarry));
 ok('в получателе вопрос включён в повестку со ссылкой на источник', agCarry.dst[0].st==='Включён в повестку' && /^СОВ-/.test(agCarry.dst[0].from||''), JSON.stringify(agCarry.dst));
 ok('нерассмотренных в исходной повестке не осталось', (await txtOf('#ma-open'))==='0');

 // перенос в новое совещание
 await page.evaluate(m=>App.agendaAdd(m),agMid);
 await sleep(300);
 await page.evaluate(()=>{document.getElementById('ag-title').value='Вопрос для нового совещания';document.getElementById('ag-rep').value='u3';});
 await page.evaluate(()=>document.getElementById('ag-save').click());
 await sleep(800);
 await page.evaluate(()=>document.getElementById('ma-carry').click());
 await sleep(350);
 await page.evaluate(d=>{document.getElementById('agc-target').value='new';App.agendaCarryKind();
  document.getElementById('agc-date').value=d;document.getElementById('agc-time').value='11:00';
  document.getElementById('agc-reason').value='Продолжение на следующей неделе';},agDate(14));
 ok('режим создания нового совещания', await page.$eval('#agc-new-box',e=>e.style.display!=='none'));
 await page.evaluate(()=>document.getElementById('agc-save').click());
 await sleep(1100);
 const agNew = await page.evaluate(d=>{const m=DB.meetings.find(x=>x.date===d&&x.time==='11:00'&&/перенесённые вопросы/.test(x.title));
  return m?{code:m.code,n:(m.agenda||[]).length,from:((m.agenda||[])[0]||{}).carryFrom}:null;},agDate(14));
 ok('создано новое совещание-получатель', !!agNew && agNew.n===1, JSON.stringify(agNew));
 ok('перенесённый вопрос помнит источник', !!agNew && !!agNew.from && /^СОВ-/.test(agNew.from.code||''));

 // повестка в печатной форме протокола
 await page.evaluate(m=>{App.f.meetTab='prot';openMeeting(m);},agMid);
 await sleep(500);
 const agProt = await drawerTxt();
 ok('повестка попала в протокол совещания', agProt.includes('Повестка дня') && agProt.includes('Отклонение по расчёту МПП-7'));
 ok('протокол: рассмотрение вопросов с фактическим результатом', agProt.includes('Рассмотрение вопросов') && agProt.includes('Корректировка алгоритма утверждена'));
 ok('протокол: плановое время и перенос вопросов', agProt.includes('Планируемая продолжительность рассмотрения') && agProt.includes('Перенесён на совещание'));
 await page.evaluate(()=>{App.closeDrawer();App.f.mt={tab:'next'};nav('meetings');});
 await sleep(300);

 console.log('== M16 §2.9: маршрут согласования и версии протокола ==');
 // совещание готовит администратор, секретарь — текущий пользователь браузера (volkova, u3)
 const sessOf = async login => {
  const r = await fetch(BASE+'/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({login,password:'demo'})});
  return (r.headers.get('set-cookie')||'').split(';')[0];
 };
 const chairCookie = await sessOf('morozov');
 const prRes = await rpcAs(admCookie,'meeting.create',{title:'E2E протокол согласования',type:'Техническое',
  date:new Date().toISOString().slice(0,10),time:'09:00',chair:'u2',secretary:'u3',attendees:['u4']});
 const pmid = (prRes.result||{}).id;
 ok('совещание для протокола создано', !!pmid);
 await rpcAs(admCookie,'meeting.conduct',{id:pmid});
 await rpcAs(admCookie,'meeting.decision.add',{id:pmid,text:'E2E решение протокола',resp:'u5',due:new Date(Date.now()+5*864e5).toISOString().slice(0,10)});
 await page.evaluate(()=>pollState());
 await sleep(400);
 await page.evaluate(m=>{App.f.meetTab='prot';openMeeting(m);},pmid);
 await sleep(400);
 const protTxt = ()=>page.evaluate(()=>document.getElementById('drawer-root').textContent);
 ok('вкладка «Протокол»: блок согласования', (await protTxt()).includes('Согласование протокола'));
 ok('исходный статус «Черновик»', (await protTxt()).includes('Черновик'));
 ok('кнопка настройки маршрута', await page.$('#pr-route-btn')!==null);
 await page.evaluate(()=>document.getElementById('pr-route-btn').click());
 await sleep(300);
 ok('окно маршрута открыто', (await page.evaluate(()=>document.getElementById('modal-root').textContent)).includes('Маршрут согласования протокола'));
 await page.evaluate(m=>App.protRouteDefault(m),pmid);
 await sleep(250);
 ok('типовой маршрут: секретарь и председатель', await page.evaluate(()=>App._pr.length===2 && App._pr[0].role==='Секретарь' && App._pr[1].role==='Председатель'));
 await page.evaluate(()=>App.protRouteAdd());
 await sleep(200);
 ok('шаг маршрута добавляется', await page.evaluate(()=>App._pr.length===3));
 await page.evaluate(()=>App.protRouteDel(2));
 await sleep(200);
 ok('шаг маршрута удаляется', await page.evaluate(()=>App._pr.length===2));
 await page.evaluate(()=>document.getElementById('pr-route-save').click());
 await sleep(700);
 ok('маршрут сохранён на сервере', await page.evaluate(m=>{const x=DB.meetings.find(y=>y.id===m);return x.protocol.route.length===2;},pmid));
 ok('таблица маршрута отрисована', await page.evaluate(()=>document.querySelectorAll('#pr-route tbody tr').length===2));
 await page.evaluate(()=>document.getElementById('pr-send').click());
 await sleep(250);
 await page.evaluate(()=>{document.getElementById('pr-note').value='E2E направление';});
 await page.evaluate(()=>document.getElementById('pr-send-go').click());
 await sleep(800);
 ok('статус «Направлен на согласование»', (await protTxt()).includes('Направлен на согласование'));
 ok('дата направления зарегистрирована', await page.evaluate(m=>{const x=DB.meetings.find(y=>y.id===m);return !!x.protocol.sentAt;},pmid));
 ok('кнопки результата согласования доступны', await page.evaluate(()=>!!document.getElementById('pr-agree')&&!!document.getElementById('pr-rework')));
 await page.screenshot({path:'/tmp/e2e/shot_protocol_route.png'});
 await page.evaluate(()=>document.getElementById('pr-rework').click());
 await sleep(250);
 await page.evaluate(()=>document.getElementById('pr-rework-go').click());
 await sleep(300);
 ok('замечания без текста не принимаются', (await page.evaluate(()=>document.getElementById('toasts').textContent)).includes('Замечания должны содержать текст'));
 await page.evaluate(()=>{document.getElementById('pr-cmt').value='E2E замечание к протоколу';});
 await page.evaluate(()=>document.getElementById('pr-rework-go').click());
 await sleep(800);
 ok('статус «На доработке»', (await protTxt()).includes('На доработке'));
 ok('замечание показано в истории отметок', (await protTxt()).includes('E2E замечание к протоколу'));
 await page.evaluate(m=>App.protSend(m),pmid);
 await sleep(250);
 await page.evaluate(()=>document.getElementById('pr-send-go').click());
 await sleep(800);
 await page.evaluate(()=>document.getElementById('pr-agree').click());
 await sleep(250);
 await page.evaluate(()=>document.getElementById('pr-agree-go').click());
 await sleep(800);
 ok('секретарь согласовал в интерфейсе', await page.evaluate(m=>{const x=DB.meetings.find(y=>y.id===m);return x.protocol.route[0].state==='Согласовано';},pmid));
 ok('очередь передана председателю', await page.evaluate(m=>{const r=DB.meetings.find(y=>y.id===m).protocol.route;return r[0].state==='Согласовано'&&r[1].state==='Ожидает';},pmid));
 await rpcAs(chairCookie,'protocol.review',{id:pmid,decision:'Согласовано'});
 await page.evaluate(()=>pollState());
 await sleep(400);
 await page.evaluate(m=>{App.f.meetTab='prot';openMeeting(m);},pmid);
 await sleep(300);
 ok('статус «Согласован» после маршрута', (await protTxt()).includes('Согласован'));
 await rpcAs(chairCookie,'protocol.approve',{id:pmid,note:'E2E утверждение'});
 await page.evaluate(()=>pollState());
 await sleep(500);
 await page.evaluate(m=>{App.f.meetTab='prot';openMeeting(m);},pmid);
 await sleep(400);
 ok('утверждённая редакция 1 отображена', (await protTxt()).includes('Редакция 1'));
 ok('кнопки DOCX и PDF редакции', (await protTxt()).includes('DOCX') && (await protTxt()).includes('PDF'));
 const pv = await page.evaluate(m=>{const x=DB.meetings.find(y=>y.id===m);return x.protocol.versions[0];},pmid);
 ok('редакция содержит снимок решений', pv && pv.decisions.length===1, pv&&pv.decisions.length);
 const prFile = await page.evaluate(async v=>{const r=await fetch('/api/files/'+v.docId+'/'+v.docV+'?fmt=pdf');const b=await r.arrayBuffer();return String.fromCharCode(...new Uint8Array(b).slice(0,4));},pv);
 ok('PDF утверждённой редакции скачивается из карточки', prFile==='%PDF', prFile);
 await page.evaluate((m,v)=>App.protVersion(m,v),pmid,1);
 await sleep(300);
 ok('окно утверждённой редакции со снимком', (await page.evaluate(()=>document.getElementById('modal-root').textContent)).includes('E2E решение протокола'));
 await page.evaluate(()=>App.closeModal(true));
 await rpcAs(admCookie,'protocol.publish',{id:pmid});
 await page.evaluate(()=>pollState());
 await sleep(400);
 await page.evaluate(m=>{App.f.meetTab='prot';openMeeting(m);},pmid);
 await sleep(300);
 ok('статус «Опубликован»', (await protTxt()).includes('Опубликован'));
 const dlg = async d=>{ await d.accept(); };
 page.on('dialog',dlg);
 await page.evaluate(()=>document.getElementById('pr-newver').click());
 await sleep(800);
 page.off('dialog',dlg);
 ok('новая редакция создана из интерфейса', await page.evaluate(m=>{const x=DB.meetings.find(y=>y.id===m);return x.protocol.status==='Черновик'&&x.protocol.versions.length===1;},pmid));
 ok('история редакций сохранена', (await protTxt()).includes('История редакций (1)'));
 await page.evaluate(()=>{App.closeDrawer();nav('meetings');App.f.mt.tab='prot';renderView();});
 await sleep(350);
 ok('режим «Протоколы» в реестре совещаний', (await page.evaluate(()=>document.getElementById('view').textContent)).includes('E2E протокол согласования'));
 await page.screenshot({path:'/tmp/e2e/shot_protocol_versions.png'});

 console.log('== M16 §2.10: поручения из решений совещаний ==');
 const drRes = await rpcAs(admCookie,'meeting.create',{title:'E2E поручения из решений',type:'Оперативное',
  date:new Date().toISOString().slice(0,10),time:'08:00',chair:'u2',secretary:'u3',attendees:['u5','u6']});
 const dmid=(drRes.result||{}).id;
 await rpcAs(admCookie,'meeting.agenda.add',{id:dmid,title:'Вопрос E2E по поручениям',reporter:'u3'});
 const decRes = await rpcAs(admCookie,'meeting.decision.add',{id:dmid,text:'E2E решение для поручения',resp:'u5',
  due:new Date(Date.now()+6*864e5).toISOString().slice(0,10),agendaN:1});
 const ddid=((decRes.result||{}).decisions||[{}])[0].id;
 ok('решение зарегистрировано вторым пользователем', !!ddid);
 await page.evaluate(()=>pollState());
 await sleep(400);
 await page.evaluate(m=>{App.f.meetTab='dec';openMeeting(m);},dmid);
 await sleep(300);
 ok('кнопка «В поручение» в решении', (await page.evaluate(()=>document.getElementById('drawer-root').textContent)).includes('В поручение'));
 await page.evaluate((m,d)=>App.directiveModal(m,d),dmid,ddid);
 await sleep(300);
 const drTxt = await page.evaluate(()=>document.getElementById('modal-root').textContent);
 ok('форма поручения: поля ТЗ §2.10', ['Наименование','Описание','Проект','Исполнитель','Соисполнители','Дата постановки','Срок исполнения','Приоритет','Критерии завершения','Источник поручения'].every(x=>drTxt.includes(x)));
 ok('исполнитель и проект предзаполнены', await page.evaluate(()=>val('dr-as')==='u5'&&!!val('dr-proj')));
 ok('источник подставлен из решения', await page.evaluate(()=>val('dr-src').includes('Решение №1')));
 await page.evaluate(()=>{
  document.getElementById('dr-title').value='E2E поручение браузера';
  document.getElementById('dr-crit').value='Отчёт подписан испытательной группой';
  document.querySelector('.dr-co[value="u6"]').checked=true;
 });
 await page.evaluate(()=>document.getElementById('dr-save').click());
 await sleep(900);
 const dt = await page.evaluate(m=>{
  const d=DB.meetings.find(x=>x.id===m).decisions[0];
  const t=DB.tasks.find(x=>x.id===(d.taskIds||[])[0]);
  return t?{id:t.id,code:t.code,type:t.type,dr:t.directive}:null;
 },dmid);
 ok('поручение создано как задача M02', !!dt && dt.type==='directive', dt&&dt.code);
 ok('соисполнитель сохранён', dt&&dt.dr.coassignees.join(',')==='u6', dt&&dt.dr.coassignees);
 ok('критерии завершения сохранены', dt&&dt.dr.criteria==='Отчёт подписан испытательной группой');
 ok('ссылки на совещание, решение и пункт повестки', dt&&dt.dr.meetingId===dmid&&dt.dr.decisionN===1&&dt.dr.agendaN===1);
 await page.screenshot({path:'/tmp/e2e/shot_directive_form.png'});
 await page.evaluate(t=>openTask(t),dt.id);
 await sleep(400);
 ok('карточка задачи: блок «Основание поручения»', await page.$('#dir-basis')!==null);
 const basisTxt = await page.evaluate(()=>(document.getElementById('dir-basis')||{}).textContent||'');
 ok('основание содержит критерии и источник', basisTxt.includes('Отчёт подписан')&&basisTxt.includes('Решение №1'));
 await page.evaluate(()=>{const el=[...document.querySelectorAll('#dir-basis .link')].find(x=>x.textContent.includes('СОВ-'));if(el)el.click();});
 await sleep(500);
 ok('обратный переход из задачи к совещанию', (await page.evaluate(()=>document.getElementById('drawer-root').textContent)).includes('E2E поручения из решений'));
 await page.evaluate((m,d)=>{App.f.meetTab='dec';openMeeting(m);App.directiveModal(m,d);},dmid,ddid);
 await sleep(350);
 ok('предупреждение о повторном создании', (await page.evaluate(()=>document.getElementById('modal-root').textContent)).includes('уже создано поручение'));
 const dlg2 = async d=>{ await d.accept(); };
 page.on('dialog',dlg2);
 await page.evaluate(()=>document.getElementById('dr-save').click());
 await sleep(1100);
 page.off('dialog',dlg2);
 ok('второе поручение создано после подтверждения', await page.evaluate(m=>DB.meetings.find(x=>x.id===m).decisions[0].taskIds.length===2,dmid));
 await page.evaluate((m,d)=>App.decisionEdit(m,d),dmid,ddid);
 await sleep(300);
 await page.evaluate(()=>{document.getElementById('de-text').value='E2E решение изменено без нового поручения';});
 await page.evaluate((m,d)=>App.decisionEditSave(m,d),dmid,ddid);
 await sleep(800);
 ok('правка решения не создаёт поручение повторно', await page.evaluate(m=>{
  const d=DB.meetings.find(x=>x.id===m).decisions[0];
  return d.taskIds.length===2 && d.text.includes('изменено');
 },dmid));
 await page.evaluate(m=>{App.f.meetTab='dir';openMeeting(m);},dmid);
 await sleep(350);
 ok('вкладка «Поручения» показывает соисполнителей', (await page.evaluate(()=>document.getElementById('drawer-root').textContent)).includes('соисполнители'));
 await page.screenshot({path:'/tmp/e2e/shot_directive_list.png'});
 await page.evaluate(()=>App.closeDrawer());

 console.log('== M16 §2.11: реестр поручений и приёмка результата ==');
 await page.evaluate(()=>{nav('meetings');App.f.mt.tab='dir';App.f.mt.df={};renderView();});
 await sleep(450);
 ok('реестр поручений открыт', await page.$('#dir-registry')!==null);
 const regHead = await page.evaluate(()=>(document.querySelector('#dir-registry thead')||{}).textContent||'');
 ok('колонки реестра по ТЗ §2.11', ['Совещание','Протокол','Решение','Исполнитель','Соисполнители','Срок','Статус','Дата факта','Результат','Документы'].every(x=>regHead.includes(x)));
 ok('созданное поручение видно в реестре', (await page.evaluate(()=>document.getElementById('dir-registry').textContent)).includes('E2E поручение браузера'));
 await page.screenshot({path:'/tmp/e2e/shot_directive_registry.png'});
 await page.evaluate(()=>{const el=document.getElementById('drf-s');el.value='Не начато';el.dispatchEvent(new Event('change'));});
 await sleep(400);
 ok('фильтр реестра по статусу применён', await page.evaluate(()=>App.f.mt.df.s==='Не начато'));
 await page.evaluate(()=>{const el=document.getElementById('drf-s');el.value='';el.dispatchEvent(new Event('change'));});
 await sleep(350);
 const rtid = await page.evaluate(()=>DB.tasks.find(t=>t.title==='E2E поручение браузера').id);
 await page.evaluate(t=>{App.f.taskTab='dir';openTask(t);},rtid);
 await sleep(450);
 ok('вкладка «Поручение» в карточке задачи', await page.$('#dir-card')!==null);
 const dirTxt = await page.evaluate(()=>document.getElementById('drawer-root').textContent);
 ok('контроль исполнения и приёмка в карточке', dirTxt.includes('Контроль исполнения поручения')&&dirTxt.includes('Приёмка результата руководителем'));
 await page.evaluate(()=>document.getElementById('dir-extend-btn').click());
 await sleep(350);
 await page.evaluate(()=>{
  document.getElementById('dir-due').value=new Date(Date.now()+20*864e5).toISOString().slice(0,10);
  document.getElementById('dir-reason').value='E2E продление по решению председателя';
 });
 await page.evaluate(()=>document.getElementById('dir-extend-go').click());
 await sleep(1000);
 const afterExt = await page.evaluate(()=>document.getElementById('drawer-root').textContent);
 ok('продление зафиксировано в истории', afterExt.includes('История продления срока (1)'));
 ok('основание продления показано', afterExt.includes('E2E продление по решению председателя'));
 await page.evaluate(()=>document.getElementById('dir-attach-btn').click());
 await sleep(350);
 await page.evaluate(()=>document.getElementById('dir-attach-go').click());
 await sleep(900);
 ok('подтверждающий документ приложен', (await page.evaluate(()=>document.getElementById('drawer-root').textContent)).includes('Документы, подтверждающие выполнение (1)'));
 await page.evaluate(()=>{document.getElementById('dir-result').value='E2E результат поручения';document.getElementById('dir-prog').value='60';});
 await page.evaluate(()=>document.getElementById('dir-save').click());
 await sleep(900);
 ok('результат поручения сохранён', await page.evaluate(t=>DB.tasks.find(x=>x.id===t).result==='E2E результат поручения',rtid));
 await page.screenshot({path:'/tmp/e2e/shot_directive_card.png'});
 // приёмка результата — руководитель подразделения
 await page.evaluate(()=>{App.closeDrawer();App.closeModal(true);App.logout();});
 await page.waitForSelector('.login-box',{timeout:10000});
 await page.evaluate(()=>{App.pickUser('morozov');});
 await page.click('.login-row .btn.pri');
 await page.waitForSelector('.shell',{timeout:10000});
 await sleep(500);
 await page.evaluate(async t=>{await rpc('task.status',{id:t,to:'В работе'});await rpc('task.status',{id:t,to:'На проверке',result:'E2E результат поручения'});},rtid);
 await sleep(700);
 await page.evaluate(t=>{App.f.taskTab='dir';openTask(t);},rtid);
 await sleep(400);
 ok('кнопка приёмки доступна руководителю', await page.$('#dir-accept-btn')!==null);
 await page.evaluate(()=>document.getElementById('dir-accept-btn').click());
 await sleep(350);
 await page.evaluate(()=>{document.getElementById('dir-acc-cmt').value='E2E решение исполнено и принято';});
 await page.evaluate(()=>document.getElementById('dir-accept-go').click());
 await sleep(1000);
 const accTxt = await page.evaluate(()=>document.getElementById('drawer-root').textContent);
 ok('приёмка зафиксирована в карточке', accTxt.includes('Принято')&&accTxt.includes('E2E решение исполнено и принято'));
 ok('статус поручения «Выполнено»', await page.evaluate(t=>dirStatus(DB.tasks.find(x=>x.id===t))==='Выполнено',rtid));
 ok('дата фактического исполнения проставлена', await page.evaluate(t=>!!(DB.tasks.find(x=>x.id===t).directive||{}).factDate,rtid));
 await page.evaluate(()=>{App.closeDrawer();nav('meetings');App.f.mt.tab='over';renderView();});
 await sleep(450);
 ok('режим «Просроченные поручения»', (await page.evaluate(()=>document.getElementById('view').textContent)).includes('Просроченные поручения'));
 await page.evaluate(()=>{App.f.mt.tab='dir';App.f.mt.df={};renderView();});
 await sleep(350);

 console.log('== M16 §2.11.1: контроль сроков поручений ==');
 await page.evaluate(()=>{App.closeDrawer();App.closeModal(true);nav('meetings');App.f.mt.tab='ctl';App.dirCtl=null;renderView();});
 await sleep(900);
 ok('режим «Контроль сроков» открыт', await page.$('#dir-control')!==null);
 const ctlTxt = await page.evaluate(()=>document.getElementById('view').textContent);
 ok('заголовок контроля сроков', ctlTxt.includes('Контроль сроков поручений'));
 const ckCards = await page.evaluate(()=>['dueSoon','overdue','noAssignee','noDue','stale','needReview'].filter(k=>!!document.getElementById('dck-'+k)).length);
 ok('шесть категорий отклонений на экране', ckCards===6, ckCards);
 ok('пороги контроля показаны', ctlTxt.includes('Порог приближения срока')&&ctlTxt.includes('без обновления состояния'));
 ok('состав получателей показан', ctlTxt.includes('получатели:'));
 await page.screenshot({path:'/tmp/e2e/shot_dir_control.png'});
 // поручение без исполнителя и без срока — выявляется автоматически
 const ctlTid = await page.evaluate(async()=>{
  const t=DB.tasks.find(x=>x.type==='directive'&&x.code==='З-027')||DB.tasks.find(x=>x.type==='directive');
  await rpc('directive.update',{id:t.id,assignee:null,due:null});
  await App.dirCtlLoad();
  return t.id;
 });
 await sleep(700);
 const ctlRowTxt = await page.evaluate(t=>{const r=(App.dirCtl.rows||[]).find(x=>x.id===t);return r?(r.issues||[]).join(','):'нет строки';},ctlTid);
 ok('поручение без исполнителя выявлено', ctlRowTxt.includes('noAssignee'), ctlRowTxt);
 ok('поручение без срока выявлено', ctlRowTxt.includes('noDue'), ctlRowTxt);
 ok('отклонения показаны в таблице', (await page.evaluate(()=>document.getElementById('dir-control').textContent)).includes('Без исполнителя'));
 // поручение без срока не ломает диаграмму Ганта
 await page.evaluate(()=>{const t=DB.tasks.find(x=>x.type==='directive'&&!x.end);nav('gantt');App.gantt.proj=t.projectId;renderView();});
 await sleep(600);
 ok('гант отображает поручение без срока', await page.$('#gsvg')!==null);
 ok('в ганте нет NaN-координат', !(await page.evaluate(()=>document.getElementById('gsvg').innerHTML)).includes('NaN'));
 await page.evaluate(()=>{nav('meetings');App.f.mt.tab='ctl';renderView();});
 await sleep(700);
 await page.evaluate(()=>document.getElementById('dck-noAssignee').click());
 await sleep(400);
 ok('отбор по категории отклонения применён', await page.evaluate(()=>App.f.mt.cf.check==='noAssignee'));
 ok('в таблице только выбранная категория', await page.evaluate(()=>[...document.querySelectorAll('#dir-control tbody tr')].every(tr=>tr.textContent.includes('Без исполнителя'))));
 await page.evaluate(()=>document.getElementById('dck-noAssignee').click());
 await sleep(400);
 ok('повторное нажатие снимает отбор', await page.evaluate(()=>App.f.mt.cf.check===''));
 ok('руководителю доступна рассылка контроля', await page.$('#dir-ctl-run')!==null);
 ok('руководителю недоступна системная настройка', await page.$('#dir-ctl-cfg')===null);
 await page.evaluate(()=>document.getElementById('dir-ctl-run').click());
 await sleep(1200);
 ok('контроль выполнен и рассылка отправлена', (await page.evaluate(()=>document.getElementById('toasts').textContent)).includes('Контроль сроков выполнен'));
 await page.evaluate(()=>document.getElementById('dir-ctl-refresh').click());
 await sleep(700);
 ok('обновление данных контроля работает', await page.$('#dir-control')!==null);

 console.log('== M16 §2.11.2: отчётность по поручениям ==');
 await page.evaluate(()=>{App.f.mt.tab='rep';App.dirRep=null;renderView();});
 await sleep(500);
 ok('режим «Отчётность» открыт', await page.$('#drp-kind')!==null);
 const repKinds = await page.evaluate(()=>[...document.querySelectorAll('#drp-kind option')].map(o=>o.value));
 ok('семь видов отчётов в списке', repKinds.length===7&&['registry','overdue','assignees','meetings','projects','period','unfulfilled'].every(k=>repKinds.includes(k)), repKinds.join(','));
 await page.evaluate(()=>document.getElementById('drp-go').click());
 await sleep(900);
 ok('реестр всех поручений сформирован', await page.$('#dir-rep-table')!==null);
 const repTxt = await page.evaluate(()=>document.getElementById('view').textContent);
 ok('сводные показатели отчёта показаны', repTxt.includes('Сводные показатели')&&repTxt.includes('Всего поручений'));
 await page.screenshot({path:'/tmp/e2e/shot_dir_report.png'});
 await page.evaluate(()=>{const el=document.getElementById('drp-kind');el.value='overdue';el.dispatchEvent(new Event('change'));});
 await sleep(400);
 await page.evaluate(()=>document.getElementById('drp-go').click());
 await sleep(900);
 ok('перечень просроченных поручений сформирован', (await page.evaluate(()=>document.querySelector('#dir-rep-table thead').textContent)).includes('Просрочка'));
 await page.evaluate(()=>{const el=document.getElementById('drp-kind');el.value='period';el.dispatchEvent(new Event('change'));});
 await sleep(400);
 ok('для отчёта за период задаются даты', await page.$('#drp-from')!==null&&await page.$('#drp-to')!==null);
 await page.evaluate(()=>document.getElementById('drp-go').click());
 await sleep(900);
 ok('отчёт по исполнению за период сформирован', (await page.evaluate(()=>document.getElementById('view').textContent)).includes('Дисциплина исполнения'));
 await page.evaluate(()=>{const el=document.getElementById('drp-kind');el.value='unfulfilled';el.dispatchEvent(new Event('change'));});
 await sleep(350);
 await page.evaluate(()=>document.getElementById('drp-go').click());
 await sleep(900);
 ok('отчёт по неисполненным решениям сформирован', (await page.evaluate(()=>document.getElementById('view').textContent)).includes('Неисполненных решений'));
 const nRep0 = await page.evaluate(()=>DB.reports.length);
 await page.evaluate(()=>document.getElementById('drp-save').click());
 await sleep(1000);
 ok('снимок отчёта сохранён в реестре M13', await page.evaluate(n=>DB.reports.length===n+1,nRep0));
 await page.evaluate(()=>document.getElementById('drp-csv').click());
 await sleep(1200);
 ok('выгрузка отчёта сервером (CSV)', (await page.evaluate(()=>document.getElementById('toasts').textContent)).includes('Документ сформирован сервером'));

 console.log('== M16 §2.11.1: настройка контроля администратором ==');
 await page.evaluate(()=>{App.closeDrawer();App.closeModal(true);App.logout();});
 await page.waitForSelector('.login-box',{timeout:10000});
 await page.evaluate(()=>{App.pickUser('admin');});
 await page.click('.login-row .btn.pri');
 await page.waitForSelector('.shell',{timeout:10000});
 await sleep(500);
 await page.evaluate(()=>{nav('meetings');App.f.mt.tab='ctl';App.dirCtl=null;renderView();});
 await sleep(900);
 ok('администратору доступна настройка контроля', await page.$('#dir-ctl-cfg')!==null);
 await page.evaluate(()=>document.getElementById('dir-ctl-cfg').click());
 await sleep(400);
 const cfgTxt = await page.evaluate(()=>document.getElementById('modal-root').textContent);
 ok('окно настройки контроля открыто', cfgTxt.includes('Настройка контроля поручений'));
 ok('в настройке шесть категорий получателей', await page.evaluate(()=>document.querySelectorAll('.dcfg-rec').length===6));
 ok('в настройке шесть проверок', await page.evaluate(()=>document.querySelectorAll('.dcfg-chk').length===6));
 await page.evaluate(()=>{
  document.getElementById('dcfg-soon').value='5';
  document.getElementById('dcfg-stale').value='10';
  const el=document.getElementById('dcfg-ctrl'); el.value='u4'; el.dispatchEvent(new Event('change'));
  const cb=[...document.querySelectorAll('.dcfg-rec')].find(x=>x.value==='coassignees'); cb.checked=false;
 });
 await page.evaluate(()=>document.getElementById('dcfg-save').click());
 await sleep(1200);
 ok('настройка контроля сохранена', (await page.evaluate(()=>document.getElementById('toasts').textContent)).includes('Настройки контроля сохранены'));
 ok('новые пороги применены', await page.evaluate(()=>App.dirCtl.cfg.dueSoonDays===5&&App.dirCtl.cfg.staleDays===10));
 ok('контролирующее лицо назначено', await page.evaluate(()=>App.dirCtl.cfg.controller==='u4'));
 ok('соисполнители исключены из получателей', (await page.evaluate(()=>document.getElementById('view').textContent)).includes('получатели:')&&
    !(await page.evaluate(()=>App.dirCtl.cfg.recipients.coassignees)));
 // контролирующее лицо в карточке поручения
 await page.evaluate(t=>{App.f.taskTab='dir';openTask(t);},ctlTid);
 await sleep(600);
 ok('в карточке показано контролирующее лицо', await page.$('#dir-ctrl-view')!==null);
 ok('в карточке выбирается контролирующее лицо', await page.$('#dir-ctrl')!==null);
 await page.evaluate(()=>{const el=document.getElementById('dir-ctrl');el.value='u4';el.dispatchEvent(new Event('change'));});
 await page.evaluate(()=>document.getElementById('dir-save').click());
 await sleep(1000);
 ok('контролирующее лицо поручения сохранено', await page.evaluate(t=>(DB.tasks.find(x=>x.id===t).directive||{}).controller==='u4',ctlTid));
 await page.evaluate(()=>{App.closeDrawer();App.closeModal(true);});
 await sleep(300);

 console.log('== Фильтры-списки: обработчик изменения ==');
 await page.evaluate(()=>{nav('tasks');App.f.tk.proj='';renderView();});
 await sleep(300);
 const filterWorks = await page.evaluate(()=>{
  const el=document.getElementById('tk-proj'); if(!el)return 'нет списка';
  el.value=DB.projects[0].id;
  el.dispatchEvent(new Event('change'));
  return App.f.tk.proj===DB.projects[0].id;
 });
 ok('выбор в списке фильтра применяется', filterWorks===true, filterWorks);
 await page.evaluate(()=>{App.f.tk.proj='';renderView();});
 await sleep(200);

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
 // v1.9.0: счётчик доставленных учитывает только реально созданные уведомления (без повторов)
 const nl1 = await rpcAs(admCookie,'notif.list');
 const cnt1 = ((nl1.result||{}).items||[]).length;
 await page.evaluate(()=>App.notifTick());
 await sleep(800);
 const t2 = await page.evaluate(()=>document.getElementById('toasts').textContent);
 ok('повторный цикл планировщика: доставлено 0', t2.includes('Доставлено уведомлений: 0'), t2.slice(-160));
 const nl2 = await rpcAs(admCookie,'notif.list');
 ok('число уведомлений не выросло от повторного цикла', ((nl2.result||{}).items||[]).length===cnt1, cnt1+' → '+((nl2.result||{}).items||[]).length);
 await page.evaluate(()=>App.notifTick());
 await sleep(800);
 ok('третий цикл также без дублей', (await page.evaluate(()=>document.getElementById('toasts').textContent)).includes('Доставлено уведомлений: 0'));
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

 console.log('== M05: правка справочника сотрудников администратором ==');
 await page.evaluate(()=>nav('resources'));
 await sleep(450);
 ok('администратору доступна правка справочника', (await page.$$('.res-edit')).length>0);
 await page.evaluate(()=>document.querySelector('#res-row-u7 .res-edit').click());
 await sleep(300);
 ok('форма справочника: ФИО, должность, подразделение', (await page.$('#ud-name'))!==null && (await page.$('#ud-pos'))!==null && (await page.$('#ud-dept'))!==null);
 await page.evaluate(()=>{document.getElementById('ud-dept').value='Группа испытаний и измерений';});
 await page.evaluate(()=>document.getElementById('ud-save').click());
 await sleep(900);
 ok('подразделение сотрудника сохранено', (await page.$eval('#res-row-u7',e=>e.textContent)).includes('Группа испытаний и измерений'));


 console.log('== M16 §2.8: библиотека шаблонов протокола (MTG-10) ==');
 await page.evaluate(()=>{nav('meetings');App.f.mt=App.f.mt||{};App.f.mt.tab='next';renderView();});
 await sleep(450);
 await page.click('#mt-tab-tpl');
 await sleep(450);
 ok('каталог шаблонов открыт', await page.$('#tpl-table')!==null);
 ok('в каталоге 6 базовых шаблонов', (await page.$$('#tpl-table tbody tr')).length>=6);
 ok('виден признак «по умолчанию»', (await page.$eval('#tpl-catalog',e=>e.textContent)).includes('по умолчанию'));
 await page.evaluate(()=>App.tplNew());
 await sleep(350);
 ok('конструктор: наименование и блоки', (await page.$('#tc-name'))!==null && (await page.$$('#tc-blocks .tc-block')).length>=10);
 await page.evaluate(()=>{document.getElementById('tc-name').value='E2E шаблон протокола';});
 await page.evaluate(()=>App.tplBlockAdd());
 await sleep(250);
 ok('блок добавлен в конструкторе', (await page.$$('#tc-blocks .tc-block')).length>=11);
 await page.evaluate(()=>document.getElementById('tc-save').click());
 await sleep(1000);
 const tpl28Id = await page.evaluate(()=>{const t=(DB.templates||[]).find(x=>x.name==='E2E шаблон протокола');return t?t.id:null;});
 ok('шаблон создан через интерфейс', !!tpl28Id, tpl28Id);
 ok('строка нового шаблона в каталоге', !!tpl28Id && await page.$('#tpl-row-'+tpl28Id)!==null);
 await page.evaluate(id=>App.tplPreview(id), tpl28Id);
 await sleep(700);
 ok('предпросмотр шаблона', (await page.$eval('#tp-plain',e=>e.textContent)).includes('Протокол №'));
 await page.evaluate(()=>App.closeModal(true));
 await page.evaluate(id=>App.tplEdit(id), tpl28Id);
 await sleep(700);
 ok('конструктор открыт с версией шаблона', (await page.$eval('.mh',e=>e.textContent)).includes('версия'));
 await page.evaluate(()=>{document.getElementById('tc-note').value='Правка через интерфейс';});
 await page.evaluate(()=>document.getElementById('tc-save').click());
 await sleep(1000);
 ok('версия шаблона увеличена после правки', (await page.evaluate(id=>(DB.templates.find(t=>t.id===id)||{}).version, tpl28Id))===2);
 await page.evaluate(id=>App.tplDefault(id), tpl28Id);
 await sleep(800);
 ok('шаблон назначен по умолчанию', (await page.evaluate(id=>(DB.templates.find(t=>t.id===id)||{}).isDefault, tpl28Id))===true);
 await page.evaluate(()=>App.tplDefault('tpl1'));
 await sleep(800);
 const dlg28b8a = d=>{d.accept().catch(()=>{});};
 page.on('dialog', dlg28b8a);
 await page.evaluate(id=>App.tplArchive(id), tpl28Id);
 await sleep(900);
 page.off('dialog', dlg28b8a);
 ok('шаблон переведён в архив', (await page.evaluate(id=>(DB.templates.find(t=>t.id===id)||{}).status, tpl28Id))==='Архивный');
 await page.evaluate(id=>App.tplRestore(id), tpl28Id);
 await sleep(800);
 ok('шаблон восстановлен из архива', (await page.evaluate(id=>(DB.templates.find(t=>t.id===id)||{}).status, tpl28Id))==='Действует');
 await page.evaluate(id=>App.tplRollback(id), tpl28Id);
 await sleep(700);
 ok('окно версий шаблона', (await page.$('#tpl-hist'))!==null);
 await page.evaluate(()=>document.querySelector('#tpl-hist .tpl-rb').click());
 await sleep(1000);
 ok('версия шаблона восстановлена', (await page.evaluate(id=>(DB.templates.find(t=>t.id===id)||{}).version, tpl28Id))===3);
 await page.screenshot({path:'/tmp/e2e/shot_templates.png'});

 console.log('== M16 §2.8: редактор протокола (MTG-09) ==');
 const p28mid = await page.evaluate(async()=>{
  const m = await rpc('meeting.create',{title:'E2E протокол §2.8',type:'Техническое',
   date:new Date().toISOString().slice(0,10),time:'12:00',chair:'u2',secretary:'u3',attendees:['u2','u3','u5']});
  if(!m) return null;
  await rpc('meeting.agenda.add',{id:m.id,title:'Вопрос E2E по протоколу',reporter:'u5',content:'Проверка редактора протокола.'});
  await rpc('meeting.decision.add',{id:m.id,text:'Решение E2E по протоколу',resp:'u5',due:new Date(Date.now()+5*86400000).toISOString().slice(0,10),agendaN:1});
  return m.id;
 });
 ok('совещание для протокола создано', !!p28mid, p28mid);
 await page.evaluate(id=>{App.f.meetTab='agenda';openMeeting(id);}, p28mid);
 await sleep(600);
 ok('кнопка рассмотрения вопроса в повестке', (await page.$$('.ma-disc')).length>0);
 await page.evaluate(()=>document.querySelector('.ma-disc').click());
 await sleep(400);
 ok('форма рассмотрения вопроса', (await page.$('#ad-discussion'))!==null && (await page.$('#ad-remarks'))!==null && (await page.$('#ad-problems'))!==null);
 await page.evaluate(()=>{
  document.getElementById('ad-discussion').value='Обсуждение E2E: вопрос рассмотрен.';
  document.getElementById('ad-remarks').value='Замечание E2E: оформить акт.';
  document.getElementById('ad-problems').value='Проблема E2E: не хватает ЗИП.';
 });
 await page.evaluate(()=>document.getElementById('ad-save').click());
 await sleep(1000);
 ok('рассмотрение вопроса сохранено', (await page.evaluate(id=>{const m=DB.meetings.find(x=>x.id===id);return (m.agenda[0]||{}).remarks||'';}, p28mid)).includes('оформить акт'));
 await page.evaluate(id=>{App.f.meetTab='prot';openMeeting(id);}, p28mid);
 await sleep(1200);
 ok('реквизиты протокола отображены', (await page.$('#pd-req'))!==null);
 ok('номер протокола заполнен автоматически', (await page.$eval('#pd-number',e=>e.value)).startsWith('П-'));
 ok('разделы протокола отображены', (await page.$$('#pd-sections .pd-sec')).length>=8);
 await page.evaluate(()=>{
  document.getElementById('pd-place').value='Каб. E2E';
  document.getElementById('pd-number').value='П-E2E-001';
 });
 await page.evaluate(()=>document.getElementById('pd-req-save').click());
 await sleep(1200);
 ok('реквизиты сохранены через интерфейс', (await page.$eval('#pd-number',e=>e.value))==='П-E2E-001');
 ok('место проведения сохранено', (await page.$eval('#pd-place',e=>e.value))==='Каб. E2E');
 const sec28Key = await page.evaluate(()=>{const d=App._pd.data;const s=d.sections.find(x=>x.type==='discussion');return s?s.key:null;});
 ok('раздел обсуждения найден', !!sec28Key, sec28Key);
 await page.evaluate(k=>{document.getElementById('pd-b-'+k).value='Правка секретаря E2E.';}, sec28Key);
 await page.evaluate(()=>{document.getElementById('pd-conclusion').value='Заключение E2E.';});
 await page.evaluate(()=>document.getElementById('pd-save').click());
 await sleep(1200);
 ok('черновик сохранён', (await page.evaluate(k=>{const s=App._pd.data.sections.find(x=>x.key===k);return s?s.body:'';}, sec28Key)).includes('Правка секретаря E2E'));
 ok('статус протокола — черновик', (await page.evaluate(()=>App._pd.data.status))==='Черновик');
 await page.evaluate(()=>document.getElementById('pd-add-section').click());
 await sleep(350);
 await page.evaluate(()=>{document.getElementById('ps-title').value='9. Раздел E2E';document.getElementById('ps-body').value='Текст раздела E2E.';});
 await page.evaluate(()=>document.getElementById('ps-save').click());
 await sleep(1200);
 ok('раздел добавлен через интерфейс', (await page.evaluate(()=>App._pd.data.sections.some(s=>s.title==='9. Раздел E2E')))===true);
 await page.evaluate(()=>document.getElementById('pd-att-add').click());
 await sleep(350);
 await page.evaluate(()=>{
  document.getElementById('pat-kind').value='link'; App.pdAttKind();
  document.getElementById('pat-name').value='Приложение E2E';
  document.getElementById('pat-url').value='file://share/e2e.pdf';
 });
 await page.evaluate(()=>document.getElementById('pat-save').click());
 await sleep(1200);
 ok('приложение добавлено', (await page.evaluate(()=>App._pd.data.attachments.length))===1);
 await page.evaluate(()=>document.getElementById('pd-preview').click());
 await sleep(1200);
 const pv28Txt = await page.$eval('#pv-plain',e=>e.textContent);
 ok('предпросмотр содержит реквизиты', pv28Txt.includes('П-E2E-001'));
 ok('предпросмотр содержит обсуждение и замечания', pv28Txt.includes('Обсуждение E2E') && pv28Txt.includes('оформить акт'));
 ok('предпросмотр содержит правку и заключение', pv28Txt.includes('Правка секретаря E2E') && pv28Txt.includes('Заключение E2E'));
 await page.evaluate(()=>App.pdPreviewTab('doc'));
 await sleep(400);
 ok('предпросмотр документа (HTML)', await page.$eval('#pv-html',e=>e.style.display!=='none'));
 await page.screenshot({path:'/tmp/e2e/shot_protocol.png'});
 await page.evaluate(()=>App.closeModal(true));
 const dlg28b = d=>{d.accept().catch(()=>{});};
 page.on('dialog', dlg28b);
 await page.evaluate(()=>document.querySelector('#pd-sections .pd-del').click());
 await sleep(1200);
 page.off('dialog', dlg28b);
 ok('пользовательский раздел удалён', (await page.evaluate(()=>App._pd.data.sections.some(s=>s.title==='9. Раздел E2E')))===false);
 await page.evaluate(async id=>{ await rpc('meeting.conduct',{id:id}); await rpc('meeting.protocol',{id:id,formats:['docx','pdf']}); }, p28mid);
 await sleep(1500);
 await page.evaluate(id=>openMeeting(id), p28mid);
 await sleep(1200);
 ok('протокол выпущен в M09', (await page.evaluate(id=>DB.docs.some(d=>d.title.includes((DB.meetings.find(m=>m.id===id)||{}).code)), p28mid))===true);
 await page.evaluate(()=>App.closeDrawer());

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

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

 console.log('== Права: наблюдатель ==');
 await page.evaluate(()=>App.logout());
 await sleep(300);
 await page.evaluate(()=>{ App.pickUser('zaytseva'); });
 await page.click('.login-row .btn.pri');
 await page.waitForSelector('.shell',{timeout:10000});
 await sleep(300);
 ok('наблюдатель: нет M14 в навигации', !(await page.evaluate(()=>[...document.querySelectorAll('.nav a')].some(a=>a.textContent.includes('Администрирование')))));
 const denied = await page.evaluate(async()=>{ const r=await rpc('project.create',{name:'x',cat:'НИОКР',pm:'u3',start:'2026-12-01',end:'2026-12-10',budgetHours:10}); return r===false; });
 ok('наблюдатель: создание отклонено', denied===true);
 await sleep(200);
 ok('тост 403 показан', (await page.evaluate(()=>document.getElementById('toasts').textContent)).includes('403'));

 console.log('== Сессия и выход ==');
 await page.evaluate(()=>App.logout());
 await sleep(300);
 ok('после выхода экран входа', await page.$('.login-box')!==null);

 console.log('== Ошибки страницы ==');
 ok('нет pageerror/console.error', errors.length===0, errors.slice(0,4).join(' | '));

 await browser.close();
 console.log('\nИТОГ E2E: '+PASS+' ok, '+FAIL+' FAIL');
 process.exit(FAIL?1:0);
})().catch(e=>{console.error('E2E CRASH:',e);process.exit(2);});

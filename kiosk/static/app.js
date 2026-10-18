/* The kiosk keeps drafts locally; accepted records are persisted by the server. */
(() => {
  'use strict';
  const $ = (selector, parent = document) => parent.querySelector(selector);
  const main = $('#main');
  const clone = value => JSON.parse(JSON.stringify(value));
  const titleCase = value => String(value || '').replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
  const active = items => (items || []).filter(item => item.active !== false);
  const pages = {settings:'Settings', testing:'Testing'};
  const paths = {settings:'/settings', testing:'/testing'};
  const events = ['fault_opened', 'incident_escalated', 'incident_resolved', 'weekly_completed'];
  let state = null, page = document.body.dataset.page || 'settings', currentUrl = location.pathname + location.search;
  let dirty = false, secretDirty = false, form = null, formKey = '', treeEdit = null, treeDirty = false, savingSettings = false;
  let historyIndex = 0, restoringHistory = false;
  const scrollPositions = new Map();
  const pendingSecretNames = new Set();
  const pendingFileReads = new Set();
  const testResults = new Map();

  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (key === 'checked' || key === 'disabled' || key === 'required' || key === 'multiple') node[key] = value;
      else if (key === 'value') node.value = value;
      else node.setAttribute(key, value === true ? '' : String(value));
    }
    for (const child of children.flat(Infinity)) if (child !== undefined && child !== null && child !== false) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
    return node;
  }
  const button = (label, action, kind = '', attrs = {}) => el('button',{type:'button',class:`button ${kind}`,onClick:action,...attrs},label);
  const routeLink = (label,url,kind='') => el('a',{href:url,class:`button ${kind}`,'data-route':''},label);
  const badge = (label,tone='') => el('span',{class:`badge ${tone}`},label);
  function tone(value) {return ['available','completed','resolved','passed','sent'].includes(value)?'good':['unavailable','failed','closed_unresolved'].includes(value)?'bad':['open','in_progress','escalated','under_maintenance','pending','unavailable_test'].includes(value)?'warn':'';}
  const panel = (title, body, extra) => el('section',{class:'panel'},title && el('div',{class:'panel-heading'},el('h2',{},title),extra),body);
  const notice = (text,kind='') => el('div',{class:`notice ${kind}`},text);
  function date(value, withTime=false) {if(!value)return 'Not recorded';try{return new Intl.DateTimeFormat('en-GB',{day:'numeric',month:'short',...(withTime?{hour:'2-digit',minute:'2-digit'}:{}),timeZone:state?.settings.general.timezone||'Europe/London'}).format(new Date(value));}catch{return 'Unknown date';}}
  function notify(message,error=false) {const node=$('#toast');node.textContent=message;node.classList.toggle('error',error);node.hidden=false;clearTimeout(notify.timer);notify.timer=setTimeout(()=>node.hidden=true,error?8000:4500);}
  async function api(url,method='GET',data) {
    const response = await fetch(url,{method,credentials:'same-origin',headers:{'Accept':'application/json',...(method!=='GET'?{'Content-Type':'application/json','X-CSRF-Token':$('meta[name="csrf-token"]').content}:{})},...(data!==undefined?{body:JSON.stringify(data)}:{})});
    let result;try{result=await response.json();}catch{throw new Error('The kiosk returned an unreadable response. Please try again.');}
    if(!response.ok)throw new Error(result.error||`Request failed (${response.status}).`);
    return result;
  }
  async function busy(node,work) {const old=node.textContent;node.disabled=true;node.textContent='Working…';try{return await work();}catch(error){notify(error.message,true);}finally{node.disabled=false;node.textContent=old;}}
  async function refresh(renderPage=false) {state=await api('/api/state');updateChrome();if(renderPage)render();}
  function updateChrome() {
    document.body.dataset.page=page;
    $('#page-label').textContent=pages[page]||'Settings';
    $('#farm-name').textContent=state.settings.general.farm_name;
    document.title=`${pages[page]||'Settings'} · ${state.settings.general.farm_name}`;
    for(const link of document.querySelectorAll('.header-actions [data-route]')) {
      const selected=getPage(link.getAttribute('href'))===page;
      link.classList.toggle('active',selected);
      if(selected)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');
    }
  }
  function markDirty(){dirty=true;const status=$('.save-status');if(status)status.textContent='Unsaved changes';}
  function draftId(){return `iforge-draft:${formKey}`;}
  function setupForm(kind, initial, key) {
    formKey=`${kind}:${key||'new'}`;form=clone(initial);treeEdit=null;treeDirty=false;dirty=false;secretDirty=false;pendingSecretNames.clear();
    try{const saved=localStorage.getItem(draftId());if(saved){const draft=JSON.parse(saved);form=draft.form;if(draft.tree){treeEdit=draft.tree;treeDirty=true;}dirty=true;}}catch{notify('This browser could not load the saved draft.',true);}
  }
  function saveDraft(){try{localStorage.setItem(draftId(),JSON.stringify({form,tree:treeEdit&&JSON.stringify(treeEdit)!==JSON.stringify(state.diagnosis)?treeEdit:null,saved_at:new Date().toISOString()}));dirty=false;treeDirty=false;const status=$('.save-status');if(status)status.textContent='Draft saved on this device · not submitted';notify('Draft saved on this device.');return true;}catch{notify('This device could not save a draft. Keep this page open and save your record.',true);return false;}}
  function clearDraft(){try{localStorage.removeItem(draftId());}catch{}dirty=false;treeDirty=false;secretDirty=false;pendingSecretNames.clear();}
  async function mayLeave(){if(savingSettings){notify('Wait for settings to finish saving.',true);return false;}if(!dirty&&!secretDirty&&!treeDirty)return true;const dialog=$('#leave-dialog');$('button[value="draft"]',dialog).disabled=secretDirty;$('p',dialog).textContent=secretDirty?'A credential has not been stored yet. Stay and use Store credential, or discard the pending credential and other unsaved edits. Credentials cannot be saved in browser drafts.':'Save a draft on this device to pick up where you left off, discard these changes, or stay on this page.';dialog.returnValue='stay';dialog.showModal();const answer=await new Promise(resolve=>{
      const finish=value=>{dialog.removeEventListener('click',choose);dialog.removeEventListener('close',closed);resolve(value);};
      const choose=event=>{const chosen=event.target.closest('button[value]');if(!chosen||chosen.disabled)return;event.preventDefault();const value=chosen.value;dialog.close(value);finish(value);};
      const closed=()=>{if(!dialog.open)finish(dialog.returnValue||'stay');};
      dialog.addEventListener('click',choose);dialog.addEventListener('close',closed);
    });if(answer==='draft')return saveDraft();if(answer==='discard'){clearDraft();return true;}return false;}
  function getPage(url){const path=new URL(url,location.origin).pathname.replace(/\/$/,'');return Object.keys(paths).find(key=>paths[key]===path)||null;}
  function paneScroll(){return $('.settings-scroll')||$('.testing-scroll');}
  function applyLocation(url,preserveSettings=false){scrollPositions.set(currentUrl,paneScroll()?.scrollTop||0);currentUrl=url;page=getPage(url);if(!preserveSettings){form=null;formKey='';dirty=false;treeDirty=false;secretDirty=false;pendingSecretNames.clear();}render();const pane=paneScroll();if(pane)pane.scrollTop=scrollPositions.get(url)||0;main.focus({preventScroll:true});}
  async function navigate(url,replace=false){
    if(savingSettings){notify('Wait for settings to finish saving.',true);return;}
    if(url===currentUrl)return;
    const nextPage=getPage(url);
    const preserveSettings=page==='settings'&&nextPage==='settings'&&!secretDirty;
    if(!preserveSettings&&!await mayLeave())return;
    if(!nextPage){location.assign(url);return;}
    historyIndex+=replace?0:1;
    history[replace?'replaceState':'pushState']({...history.state,kiosk:true,index:historyIndex,url},'',url);
    applyLocation(url,preserveSettings);
  }
  history.replaceState({...history.state,kiosk:true,index:0,url:currentUrl},'',currentUrl);
  window.addEventListener('popstate',async event=>{if(restoringHistory){restoringHistory=false;return;}const target=event.state;if(!target?.kiosk)return;const previous=historyIndex;const targetUrl=location.pathname+location.search;const preserve=page==='settings'&&getPage(targetUrl)==='settings'&&!secretDirty;if(savingSettings||(!preserve&&(dirty||treeDirty||secretDirty))){restoringHistory=true;history.go(previous-target.index);if(!await mayLeave())return;restoringHistory=true;history.go(target.index-previous);}historyIndex=target.index;applyLocation(targetUrl,preserve);});
  window.addEventListener('beforeunload',event=>{if(dirty||treeDirty||secretDirty||savingSettings){event.preventDefault();event.returnValue='';}});
  document.addEventListener('click',event=>{const link=event.target.closest('a[data-route]');if(!link||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();navigate(link.getAttribute('href'));});
  $('#back-button').addEventListener('click',async()=>{if(await mayLeave()){if(window.KioskNavigation)window.KioskNavigation.back('/');else location.assign('/');}});
  $('#home-button').addEventListener('click',async event=>{if(event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;event.preventDefault();if(await mayLeave()){if(window.KioskNavigation)window.KioskNavigation.home();else location.assign('/');}});

  function field(label,value,onChange,options={}) {
    let input;
    if(options.options){input=el('select',{'aria-label':label,required:options.required},options.options.map(o=>el('option',{value:typeof o==='object'?o.value:o},typeof o==='object'?o.label:titleCase(o))));input.value=value??'';}
    else input=el(options.type==='textarea'?'textarea':'input',{'aria-label':label,type:options.type==='textarea'?undefined:options.type||'text',value:value??'',placeholder:options.placeholder,required:options.required,min:options.min,max:options.max,step:options.step,disabled:options.disabled,autocomplete:options.autocomplete||'off',rows:options.rows});
    input.addEventListener(options.options?'change':'input',()=>onChange(options.type==='number'?(input.value===''?null:Number(input.value)):input.value));
    return el('label',{class:`field ${options.wide?'wide':''}`},el('span',{class:'field-label'},label,options.required&&el('span',{class:'required'},'*')),input,options.help&&el('small',{},options.help));
  }
  function check(label,checked,onChange,help){return el('label',{class:'toggle'},el('input',{type:'checkbox',checked,onChange:event=>onChange(event.target.checked)}),el('span',{},label,help&&el('small',{class:'tiny muted'},` — ${help}`)));}
  const printerOptions=()=>active(state.settings.printers).map(p=>({value:p.id,label:p.name}));
  function saveFooter(actionButtons){return el('div',{class:'form-footer'},el('span',{class:'save-status'},dirty||treeDirty?'Unsaved changes':'Settings saved'),el('div',{class:'actions'},actionButtons));}
  function render(){if(!state)return;updateChrome();main.replaceChildren();if(page==='testing')renderTesting();else renderSettings();}

  const sections=[['general','General'],['printers','Printers'],['technicians','Technicians'],['forms','Maintenance forms'],['schedule','Schedule'],['diagnosis','Diagnosis & SOPs'],['google','Google storage'],['email','Email notifications'],['discord','Discord webhooks'],['hardware','Physical controls'],['backups','Backup & recovery'],['testing','Testing']];
  function configField(group,key,label,options={}){return field(label,form[group][key],value=>{form[group][key]=value;markDirty();},options);}
  function configCheck(group,key,label,help){return check(label,form[group][key],value=>{form[group][key]=value;markDirty();},help);}
  function hasPendingFileReads() {
    for(const read of pendingFileReads)if(!read.node.isConnected||read.form!==form)pendingFileReads.delete(read);
    return pendingFileReads.size>0;
  }
  function fileImport(kind,label,onLoad,{onReading=()=>{},help='Choose a file, then select Store credential.'}={}) {
    const currentForm=form;
    let sequence=0,activeRead=null;
    const status=el('small',{'aria-live':'polite'});
    const picker=el('input',{type:'file',accept:kind.startsWith('google_')?'.json,application/json':'.txt,.json,text/plain,application/json','aria-label':`Choose ${label} file`,'data-file-import':kind});
    const wrapper=el('label',{class:'file-import'},el('span',{class:'field-label'},`Choose ${label} file`),picker,el('small',{},`${help} Maximum file size: 64 KB.`),status);
    const current=token=>token===sequence&&wrapper.isConnected&&form===currentForm;
    picker.addEventListener('change',async()=>{
      if(savingSettings){picker.value='';notify('Wait for settings to finish saving.',true);return;}
      const file=picker.files[0];
      if(!file)return;
      const token=++sequence;
      if(activeRead)pendingFileReads.delete(activeRead);
      const read={node:wrapper,form:currentForm};
      activeRead=read;pendingFileReads.add(read);
      onReading(true);status.textContent='Reading file…';
      try {
        const value=await window.KioskFileImports.readFile(file,kind);
        if(!current(token))return;
        onLoad(value);
        notify(kind==='spreadsheet_id'?'Workbook ID loaded. Select Save settings to apply it.':'File loaded. Select Store credential to save it.');
      } catch(error) {
        if(current(token))notify(error?.name==='FileImportError'?error.message:'Cannot read this file. Check its format and use a file of 64 KB or less.',true);
      } finally {
        pendingFileReads.delete(read);
        if(activeRead===read)activeRead=null;
        if(current(token)){picker.value='';status.textContent='';onReading(false);}
      }
    });
    return {
      node:wrapper,
      cancel(){sequence++;if(activeRead)pendingFileReads.delete(activeRead);activeRead=null;picker.value='';status.textContent='';onReading(false);},
      setDisabled(disabled){picker.disabled=disabled;}
    };
  }
  function workbookField() {
    const wrapper=el('div',{class:'field wide'});
    let importer;
    const input=field('Workbook ID',form.google.spreadsheet_id,value=>{importer.cancel();form.google.spreadsheet_id=value;markDirty();},{help:'From the Google Sheets URL: /spreadsheets/d/WORKBOOK_ID/edit'});
    importer=fileImport('spreadsheet_id','Workbook ID',value=>{form.google.spreadsheet_id=value;$('input',input).value=value;markDirty();},{help:'Use a text or JSON file with one workbook ID or Google Sheets URL. Then select Save settings.'});
    wrapper.append(input,importer.node);
    return wrapper;
  }
  function renderSettings(){
    if(!form){setupForm('settings',state.settings,'configuration');if(!treeEdit)treeEdit=clone(state.diagnosis);}
    const section=new URLSearchParams(location.search).get('section')||'general';
    main.replaceChildren();
    const nav=el('nav',{class:'settings-nav','aria-label':'Settings sections'},sections.map(([id,label])=>el('a',{class:`settings-link ${section===id?'active':''}`,href:id==='testing'?'/testing':`/settings?section=${id}`,'data-route':'','aria-current':section===id?'page':null},label)));
    const content=el('div',{class:'settings-scroll',tabindex:'0','aria-label':'Settings form'});
    const builders={general:settingsGeneral,printers:settingsPrinters,technicians:settingsTechnicians,forms:settingsForms,schedule:settingsSchedule,diagnosis:settingsDiagnosis,google:settingsGoogle,email:settingsEmail,discord:settingsDiscord,hardware:settingsHardware,backups:settingsBackups};
    (builders[section]||settingsGeneral)(content);
    const actions=section==='diagnosis'
      ?[button('Save diagnosis & SOPs',event=>saveDiagnosis(event.currentTarget),'primary')]
      :[button('Discard changes',async()=>{if(!dirty&&!treeDirty&&!secretDirty)return;if(!confirm('Discard all unsaved settings, diagnosis edits, and pending credentials?'))return;clearDraft();form=null;renderSettings();},'ghost'),button('Save settings',event=>saveSettings(event.currentTarget),'primary')];
    main.append(el('div',{class:'settings-layout',inert:savingSettings},nav,el('div',{class:'settings-content'},content,saveFooter(actions))));
    nav.querySelector('.active')?.scrollIntoView({block:'nearest'});
  }
  async function saveSettings(node){
    if(savingSettings){notify('Wait for settings to finish saving.',true);return;}
    if(hasPendingFileReads()){notify('Wait for the file to finish loading before saving settings.',true);return;}
    if(secretDirty){notify('Store or clear the typed credentials before saving settings.',true);return;}
    savingSettings=true;
    const layout=$('.settings-layout');if(layout)layout.inert=true;
    try {
      await busy(node,async()=>{
        const result=await api('/api/settings','PUT',form);
        form=clone(result.settings||result);state.settings=clone(form);dirty=false;
        try{localStorage.removeItem(draftId());}catch{}
        await refresh();
        notify('Settings saved. The hardware worker will reload updated pin mappings.');
        renderSettings();
      });
    } finally {
      savingSettings=false;
      const currentLayout=$('.settings-layout');if(currentLayout)currentLayout.inert=false;
    }
  }
  function sectionPanel(content,title,description,body){content.append(panel(title,el('div',{},el('p',{class:'settings-description'},description),body)));}
  function settingsGeneral(content){sectionPanel(content,'General','These settings apply to this kiosk. Dates are stored in UTC and displayed in your selected timezone.',el('div',{class:'form-grid'},configField('general','farm_name','Farm name',{wide:true}),configField('general','timezone','Display timezone',{help:'IANA timezone, for example Europe/London.'}),configField('general','refresh_seconds','Dashboard refresh (seconds)',{type:'number',min:5,max:3600})));content.append(notice(`Configuration revision ${state.settings.revision}. Credentials are stored separately and are never included in ordinary exports.`,'info'));}
  function collection(content,key,title,description,fields,newItem){const holder=el('div');const renderRows=()=>{holder.replaceChildren(...form[key].map((item,index)=>el('div',{class:'array-item'},el('div',{class:'array-heading'},item.name||item.label||`${title} ${index+1}`,button(index>0?'↑ Move up':'First',()=>{[form[key][index-1],form[key][index]]=[form[key][index],form[key][index-1]];markDirty();renderRows();},'small ghost',{disabled:index===0})),el('div',{class:'form-grid three'},fields.map(([property,label,opts])=>property==='active'||property==='required'?check(label,item[property],value=>{item[property]=value;markDirty();}):field(label,item[property],value=>{item[property]=value;markDirty();},{...opts,disabled:property==='id'&&state.settings[key].some(x=>x.id===item.id)}))))));};renderRows();sectionPanel(content,title,description,el('div',{},holder,button(`+ Add ${key==='printers'?'printer':key==='technicians'?'technician':key==='checklist'?'checklist item':'usage reading'}`,()=>{form[key].push(newItem());markDirty();renderRows();},'ghost')));}
  const newId=prefix=>`${prefix}-${Date.now().toString(36)}`;
  function settingsPrinters(content){collection(content,'printers','Printers','Add, rename, reorder, or archive printers. Stable IDs keep historical records connected to the correct printer.',[['id','Stable printer ID'],['name','Display name'],['model','Model'],['active','Active in kiosk']],()=>({id:newId('printer'),name:'New printer',model:'Prusa Core One',active:true}));}
  function settingsTechnicians(content){collection(content,'technicians','Technicians','Select technicians from this list when recording maintenance. Archive a name to keep their maintenance history.',[['id','Stable technician ID'],['name','Name'],['active','Active in kiosk']],()=>({id:newId('technician'),name:'New technician',active:true}));}
  function settingsForms(content){collection(content,'checklist','Weekly checklist','Required active items must be completed before a weekly check can be marked complete.',[['id','Stable item ID'],['label','Checklist label'],['required','Required to complete'],['active','Show on form']],()=>({id:newId('check'),label:'New checklist item',required:true,active:true}));collection(content,'metrics','Usage readings','Current readings are recorded with their label and unit, preserving their meaning when forms change.',[['id','Stable metric ID'],['label','Reading label'],['unit','Unit'],['active','Show on form']],()=>({id:newId('metric'),label:'New usage reading',unit:'hours',active:true}));}
  function settingsSchedule(content){sectionPanel(content,'Academic schedule','The active year, semester, and week identify the weekly check for each printer.',el('div',{class:'form-grid'},configField('schedule','academic_year','Academic year',{placeholder:'2026/27'}),configField('schedule','semester','Semester',{type:'number',min:1,max:4}),configField('schedule','week','Current teaching week',{type:'number',min:1,max:53}),configField('schedule','teaching_weeks','Teaching weeks per semester',{type:'number',min:1,max:53})));content.append(notice('Automatic overdue reminders, digests, and quiet-hour scheduling are not implemented in this build. The dashboard shows weekly completion for the selected teaching week.'));}
  function secretControl(name,label,multiline=false) {
    const currentForm=form;
    const stored=!!state.secret_status?.[name];
    const needsDestination=name.startsWith('discord:')&&!state.settings.discord.destinations.some(destination=>`discord:${destination.id}`===name);
    let value='',reading=false,storing=false,importer=null;
    const wrapper=el('div',{class:'secret-control','data-secret-name':name});
    const updatePending=()=>{if(value||reading)pendingSecretNames.add(name);else pendingSecretNames.delete(name);secretDirty=pendingSecretNames.size>0;};
    const input=field(multiline?'Credential JSON':'Credential','',newValue=>{value=newValue;updatePending();},{type:multiline?'textarea':'password',placeholder:stored?'A credential is stored. Enter a new value to replace it.':'Enter a credential to store securely',help:'Stored on the kiosk server. Never included in browser drafts or configuration exports.',autocomplete:'new-password'});
    const inputNode=$('input,textarea',input);
    let statusNode=badge(stored?'Stored':'Not set',stored?'good':'');
    const updateControls=()=>{inputNode.disabled=needsDestination||reading||storing;store.disabled=needsDestination||reading||storing;remove.disabled=needsDestination||reading||storing;clear.disabled=needsDestination||storing;importer?.setDisabled(needsDestination||storing);};
    const setValue=newValue=>{value=newValue;inputNode.value=value;updatePending();};
    const save=async(node,removeStored=false)=>{
      if(needsDestination||reading||storing)return;
      if(removeStored&&!confirm(`Remove the stored ${label.toLowerCase()}? This disconnects the integration until a new credential is supplied.`))return;
      if(!removeStored&&!value.trim()){notify('Enter a credential or choose a file first.',true);return;}
      storing=true;updateControls();
      try {
        await busy(node,async()=>{
          const result=await api(`/api/secrets/${encodeURIComponent(name)}`,'PUT',{value:removeStored?'':value});
          state.secret_status=result.secret_status||state.secret_status;
          if(!wrapper.isConnected||form!==currentForm)return;
          setValue('');
          const replacement=badge(removeStored?'Not set':'Stored',removeStored?'':'good');
          statusNode.replaceWith(replacement);statusNode=replacement;
          notify(removeStored?'Credential removed.':'Credential stored on the kiosk.');
        });
      } finally {storing=false;if(wrapper.isConnected&&form===currentForm)updateControls();}
    };
    const store=button('Store credential',event=>save(event.currentTarget),'small dark');
    const remove=button('Remove',event=>save(event.currentTarget,true),'small ghost');
    const clear=button('Clear input',()=>{importer?.cancel();setValue('');},'small ghost');
    const kind=name.startsWith('discord:')?'discord_webhook':name.startsWith('google_')?name:null;
    if(kind)importer=fileImport(kind,label,setValue,{onReading:isReading=>{reading=isReading;updatePending();updateControls();},help:kind==='discord_webhook'?'Use a text or JSON file with one webhook URL. Then select Store credential.':'Choose the Google JSON file, then select Store credential.'});
    wrapper.append(el('div',{class:'secret-label'},el('h3',{},label),statusNode));
    if(needsDestination)wrapper.append(el('p',{class:'tiny muted'},'Save this destination before loading its webhook.'));
    wrapper.append(input);
    if(importer)wrapper.append(importer.node);
    wrapper.append(el('div',{class:'actions'},store,clear,remove));
    updateControls();
    return wrapper;
  }
  function settingsGoogle(content){sectionPanel(content,'Google Drive & Sheets','Maintenance records synchronize to a workbook in Google Drive. Staff use the kiosk to edit; use Sheets for viewing and reporting.',el('div',{},configCheck('google','enabled','Enable Google Sheets synchronization'),el('div',{class:'form-grid',style:'margin-top:17px'},configField('google','account_email','Intended Google account',{type:'email',wide:true,help:'The account must own or have access to the workbook; this address alone does not authorize access.'}),configField('google','auth_mode','Connection method',{options:[{value:'oauth',label:'Google account (OAuth)'},{value:'service_account',label:'Service account'}]}),configField('google','sync_interval','Sync interval (seconds)',{type:'number',min:5,max:3600}),workbookField(),configField('google','folder_id','Drive folder ID',{wide:true,help:'Optional: select the existing folder by its ID from the Drive URL.'})),notice('Create or select the workbook in threedprintingiforge@gmail.com’s Google Drive, then enter its ID here. Test the connection before enabling sync.'),secretControl('google_service_account','Service account JSON',true),secretControl('google_oauth_client','Google OAuth client JSON',true),el('div',{class:'actions',style:'margin-top:16px'},button('Connect Google account  →',async()=>{if(!await mayLeave())return;location.href='/auth/google/start';},'dark'),routeLink('Test Google connection','/testing','ghost')),el('details',{class:'disclosure'},el('summary',{},'Already have an authorized-user OAuth credential?'),secretControl('google_oauth_token','Authorized-user OAuth JSON',true))));}
  function eventChecks(config){return el('div',{class:'checks-grid'},events.map(event=>check(titleCase(event),(config.events||[]).includes(event),enabled=>{config.events||=[];config.events=enabled?[...new Set([...config.events,event])]:config.events.filter(e=>e!==event);markDirty();})));}
  function settingsEmail(content){sectionPanel(content,'Email notifications','Send maintenance alerts using an SMTP server. Test email is sent only when an operator presses the test button.',el('div',{},configCheck('email','enabled','Enable email notifications'),el('div',{class:'form-grid',style:'margin-top:16px'},configField('email','host','SMTP hostname',{placeholder:'smtp.gmail.com'}),configField('email','port','SMTP port',{type:'number',min:1,max:65535}),configField('email','security','Transport security',{options:[{value:'starttls',label:'STARTTLS'},{value:'ssl',label:'TLS / SSL'}]}),configField('email','username','SMTP username'),configField('email','sender','From address',{type:'email'}),configField('email','subject_prefix','Subject prefix'),field('Recipients',form.email.recipients.join(', '),value=>{form.email.recipients=value.split(/[\n,;]/).map(x=>x.trim()).filter(Boolean);markDirty();},{wide:true,help:'Separate addresses with commas.'})),el('h3',{class:'subheading'},'Send an alert when'),eventChecks(form.email),secretControl('smtp_password','SMTP password'),el('p',{class:'tiny muted',style:'margin-top:15px'},'For Gmail SMTP, use a supported app password. Email OAuth is not part of this build.')));}
  function settingsDiscord(content){const rows=el('div');const draw=()=>{rows.replaceChildren(...form.discord.destinations.map((destination,index)=>el('div',{class:'array-item'},el('div',{class:'array-heading'},destination.name||'Discord channel',badge(destination.enabled?'Enabled':'Disabled',destination.enabled?'good':'')),el('div',{class:'form-grid'},field('Destination ID',destination.id,value=>{destination.id=value;markDirty();},{disabled:true}),field('Channel / destination name',destination.name,value=>{destination.name=value;markDirty();})),check('Enable this destination',destination.enabled,value=>{destination.enabled=value;markDirty();}),el('h3',{class:'subheading'},'Notification events'),eventChecks(destination),el('h3',{class:'subheading'},'Route these printers'),el('p',{class:'tiny muted'},'No printers selected means all printers.'),el('div',{class:'checks-grid'},form.printers.map(printer=>check(printer.name,(destination.printer_ids||[]).includes(printer.id),enabled=>{destination.printer_ids||=[];destination.printer_ids=enabled?[...new Set([...destination.printer_ids,printer.id])]:destination.printer_ids.filter(id=>id!==printer.id);markDirty();}))),secretControl(`discord:${destination.id}`,'Discord webhook URL'))));};draw();sectionPanel(content,'Discord channels & webhooks','Keep fault alerts and routine maintenance in the right channels. Store each webhook on the kiosk and choose its event rules.',el('div',{},configCheck('discord','enabled','Enable Discord notifications'),el('div',{style:'margin-top:18px'},rows),button('+ Add destination',()=>{if(secretDirty){notify('Store or clear the pending credentials before adding a destination.',true);return;}form.discord.destinations.push({id:newId('channel'),name:'New maintenance channel',enabled:true,events:['fault_opened','incident_resolved'],printer_ids:[]});markDirty();draw();},'ghost'),el('p',{class:'tiny muted',style:'margin-top:16px'},'Save a new destination before storing its webhook or testing it. Ordinary saves and repeated physical triggers do not create new fault alerts.')));}
  function settingsHardware(content){const mappings=el('div');const draw=()=>mappings.replaceChildren(...form.hardware.mappings.map((mapping,index)=>el('div',{class:'array-item'},el('div',{class:'array-heading'},`Control mapping ${index+1}`,button('Remove mapping',()=>{form.hardware.mappings.splice(index,1);markDirty();draw();},'small ghost')),el('div',{class:'form-grid three'},field('Printer',mapping.printer_id,value=>{mapping.printer_id=value;markDirty();},{options:form.printers.map(p=>({value:p.id,label:p.name}))}),field('Button GPIO pin',mapping.button_pin,value=>{mapping.button_pin=value;markDirty();},{type:'number',min:0,max:27}),field('LED GPIO pin',mapping.led_pin,value=>{mapping.led_pin=value;markDirty();},{type:'number',min:0,max:27})),el('div',{class:'checks-grid'},check('Input pull-up',mapping.pull_up,value=>{mapping.pull_up=value;markDirty();}),check('LED active high',mapping.led_active_high,value=>{mapping.led_active_high=value;markDirty();})))));draw();sectionPanel(content,'Physical maintenance controls','Map each printer to its button and LED using BCM GPIO numbering. Real input/output tests require the Raspberry Pi.',el('div',{},el('div',{class:'form-grid three'},configField('hardware','backend','Hardware backend',{options:[{value:'mock',label:'Mock / cloud simulation'},{value:'gpiozero',label:'Raspberry Pi GPIO'}]}),configField('hardware','hold_seconds','Button hold (seconds)',{type:'number',min:0.1,max:30,step:0.1}),configField('hardware','debounce_seconds','Debounce (seconds)',{type:'number',min:0.01,max:5,step:0.01})),el('h3',{class:'subheading'},'Printer pin mappings'),mappings,button('+ Add mapping',()=>{form.hardware.mappings.push({printer_id:form.printers[0]?.id||'',button_pin:17,led_pin:27,pull_up:true,led_active_high:true});markDirty();draw();},'ghost'),el('p',{class:'tiny muted',style:'margin-top:16px'},'The running hardware worker reloads pin changes automatically. Pins must be unique across all active mappings.')));}
  function settingsBackups(content){sectionPanel(content,'Export & recovery','Export records and non-secret settings. Credentials are deliberately excluded.',el('div',{},el('div',{class:'form-grid'},el('p',{class:'tiny muted'},'Automatic backup scheduling is not implemented. Use the manual exports below.'),configField('backups','retention','Reserved backup retention',{type:'number',min:1,max:365,disabled:true,help:'Reserved configuration; no automatic backups run in this build.'})),el('div',{class:'actions',style:'margin:18px 0'},el('a',{href:'/api/export',download:'',class:'button dark'},'↓ Export all records'),el('a',{href:'/api/settings/export',download:'',class:'button ghost'},'↓ Export settings')),notice('Exports download to this device. Automatic Drive backup upload is not implemented in this build.'),el('h3',{class:'subheading'},'Import a settings export'),el('p',{class:'tiny muted',style:'margin-bottom:12px'},'Choose a settings JSON export. Review imported values before saving.'),el('div',{class:'file-upload'},el('input',{type:'file',accept:'.json,application/json','aria-label':'Import settings export',onChange:async event=>{try{const file=event.target.files[0];if(!file)return;if(file.size>2*1024*1024)throw new Error('Settings files must be smaller than 2 MB.');const loaded=JSON.parse(await file.text());const settings=loaded.settings||loaded;if(!Object.keys(state.settings).every(key=>key in settings)||!settings.general||!['printers','technicians','checklist','metrics'].every(key=>Array.isArray(settings[key])))throw new Error('This file is not a complete settings export.');settings.revision=state.settings.revision;form=clone(settings);markDirty();notify('Settings loaded for review. Save settings to apply them.');renderSettings();}catch(error){notify(error.message,true);event.target.value='';}}}))));const versions=el('div',{class:'tiny muted'},'Loading configuration history…');content.append(panel('Previous configurations',versions));api('/api/settings/history').then(data=>{versions.replaceChildren(...(data.versions||[]).slice().reverse().map(version=>el('div',{class:'history-version'},el('span',{},`Revision ${version.revision}`),el('span',{class:'muted'},version.general?.farm_name||''),button('Review this version',()=>{form=clone(version);form.revision=state.settings.revision;markDirty();notify('Version loaded for review. Save settings to activate it.');renderSettings();},'small ghost'))));if(!data.versions?.length)versions.textContent='No previous settings revisions yet.';}).catch(error=>versions.textContent=error.message);}

  function settingsDiagnosis(content){treeEdit||=clone(state.diagnosis);const tabs=el('div',{class:'pill-tabs'});const editor=el('div');let type='categories',selected='';let selectedNode='';
    const change=()=>{treeDirty=true;const status=$('.save-status');if(status)status.textContent='Unsaved diagnosis changes';};
    const draw=()=>{tabs.replaceChildren(...[['categories','Symptom categories'],['nodes','Questions & results'],['sops','SOP documents']].map(([key,label])=>el('button',{type:'button',class:`pill-tab ${type===key?'active':''}`,onClick:()=>{type=key;selected='';draw();}},label)));const collection=treeEdit[type]||{};const keys=Object.keys(collection);if(!keys.includes(selected))selected=keys[0]||'';const item=collection[selected];editor.replaceChildren(field('Select an item',selected,value=>{selected=value;draw();},{options:keys.map(id=>({value:id,label:collection[id].label||collection[id].text||collection[id].title||id}))}));
      const f=(key,label,options={})=>field(label,item[key],value=>{item[key]=value;change();},options);const lines=(key,label)=>field(label,(item[key]||[]).join('\n'),value=>{item[key]=value.split('\n').map(x=>x.trim()).filter(Boolean);change();},{type:'textarea',help:'One item per line.'});
      if(item){editor.append(el('p',{class:'mono muted',style:'margin:12px 0 18px'},`Stable ID: ${selected}`));
        if(type==='categories')editor.append(el('div',{class:'form-grid'},f('label','Category label'),f('start_node','First question',{options:Object.keys(treeEdit.nodes).map(id=>({value:id,label:`${id} · ${treeEdit.nodes[id].text||treeEdit.nodes[id].label}`}))}),f('display_order','Display order',{type:'number',min:0}),f('icon','Icon reference')));
        else if(type==='sops')editor.append(el('div',{class:'form-grid'},f('title','Procedure title',{wide:true}),f('description','Description',{type:'textarea',wide:true}),f('file','Document URL or /sops/ path',{wide:true}),lines('tags','Tags')));
        else if(item.type==='question'){const options=el('div');const drawOptions=()=>options.replaceChildren(...item.options.map((option,index)=>el('div',{class:'array-item'},el('div',{class:'form-grid'},field('Answer label',option.label,value=>{option.label=value;change();}),field('Next question or result',option.next,value=>{option.next=value;change();},{options:Object.keys(treeEdit.nodes).map(id=>({value:id,label:`${id} · ${treeEdit.nodes[id].text||treeEdit.nodes[id].label}`}))})),button('Remove answer',()=>{item.options.splice(index,1);change();drawOptions();},'small ghost'))));drawOptions();editor.append(f('text','Question',{type:'textarea'}),el('div',{style:'margin-top:16px'},f('help_text','Helper text',{type:'textarea'})),el('h3',{class:'subheading'},'Possible answers'),options,button('+ Add answer',()=>{item.options.push({label:'New answer',next:Object.keys(treeEdit.nodes)[0]||''});change();drawOptions();},'small ghost'));}
        else editor.append(el('div',{class:'form-grid'},f('label','Diagnosis label',{wide:true}),f('description','Description',{type:'textarea',wide:true}),lines('likely_causes','Likely causes'),lines('recommended_actions','Recommended actions'),f('component_involved','Component'),f('fix_summary','Suggested fix summary'),f('default_result_status','Default result status'),f('rank_hint','Likelihood rank',{type:'number',min:1}),lines('sop_ids','Referenced SOP IDs')));
      }
      const newButtons=el('div',{class:'actions',style:'margin-top:22px'});const add=(kind)=>{let id=prompt(`Stable ID for the new ${kind.replace('_',' ')} (letters, numbers, underscores, hyphens):`);if(!id)return;if(!/^[a-zA-Z0-9_-]+$/.test(id)||collection[id]){notify('Choose a unique ID using letters, numbers, underscores, or hyphens.',true);return;}collection[id]=kind==='category'?{label:'New symptom',icon:'printer',start_node:Object.keys(treeEdit.nodes)[0]||'',display_order:keys.length+1}:kind==='sop'?{title:'New procedure',description:'',file:'',tags:[]}:kind==='question'?{type:'question',text:'New question',help_text:'',options:[]}:{type:'diagnosis',label:'New diagnosis',description:'',likely_causes:[],recommended_actions:[],component_involved:'',fix_summary:'',sop_ids:[],rank_hint:1,default_result_status:'in_progress'};selected=id;change();draw();};if(type==='categories')newButtons.append(button('+ Add category',()=>add('category'),'ghost'));else if(type==='sops')newButtons.append(button('+ Add SOP',()=>add('sop'),'ghost'));else newButtons.append(button('+ Add question',()=>add('question'),'ghost'),button('+ Add diagnosis result',()=>add('diagnosis'),'ghost'));if(item)newButtons.append(button('Remove selected item',()=>{if(!confirm('Remove this item? Any references to it must be updated before the graph can be saved.'))return;delete collection[selected];change();selected='';draw();},'ghost danger-text'));editor.append(newButtons);
    };draw();sectionPanel(content,'Diagnosis guide & procedures','Edit questions, answer paths, repair results, and SOP links. The server validates the complete graph before accepting it.',el('div',{},tabs,editor));}
  async function saveDiagnosis(node){
    if(secretDirty){notify('Store or clear the typed credentials before saving diagnosis edits.',true);return;}
    await busy(node,async()=>{
      const result=await api('/api/diagnosis','PUT',treeEdit);
      state.diagnosis=result.diagnosis||result;treeEdit=clone(state.diagnosis);treeDirty=false;
      try{localStorage.removeItem(draftId());}catch{}
      notify('Diagnosis guide saved and validated.');renderSettings();
    });
  }


  function resultElement(result){const status=result.status||'failed';return el('div',{class:`test-result ${status}`},el('strong',{},`${titleCase(status)} · ${result.duration_ms??'—'} ms`),el('div',{},result.message||'Check finished.'),el('div',{class:'tiny muted'},`Run ${date(result.local_started_at||new Date().toISOString(),true)}`),result.details&&el('details',{},el('summary',{},'View diagnostic details'),el('pre',{},typeof result.details==='string'?result.details:JSON.stringify(result.details,null,2))));}
  function renderTesting(){
    const toolbar=el('div',{class:'testing-toolbar'},el('p',{class:'muted'},'Tests use saved settings. Test records are kept separate.'),button('Run local checks',event=>busy(event.currentTarget,async()=>{for(const name of ['config','diagnosis']){const result=await api(`/api/tests/${name}`,'POST',{});result.local_started_at=new Date().toISOString();testResults.set(name,result);}renderTesting();notify('Configuration and diagnosis checks finished.');}),'primary'));
    const content=el('div',{class:'testing-scroll',tabindex:'0','aria-label':'Integration and hardware tests'});
    main.replaceChildren(toolbar,content);
    const tests=[
      {name:'config',title:'Configuration',description:'Validate current settings and printer mappings without contacting external services.',label:'Check configuration',category:'LOCAL'},
      {name:'diagnosis',title:'Diagnosis & SOPs',description:'Check the diagnosis graph, references, and available procedure documents.',label:'Validate diagnosis',category:'LOCAL'},
      {name:'google',title:'Google connection',description:'Check authorized identity and access to the configured workbook and folder.',label:'Check Google connection',category:'READ ONLY'},
      {name:'google_write',title:'Sheets write / read',description:'Write a marked test row, read it back, and clean up that test row.',label:'Run Sheets write test',category:'REMOTE WRITE'},
      {name:'email',title:'Email delivery',description:'Send a labelled test email to your selected recipient. SMTP acceptance does not confirm inbox receipt.',label:'Send test email',category:'SENDS EMAIL'},
      {name:'discord',title:'Discord webhook',description:'Send a labelled test message to a selected channel. Mentions are suppressed.',label:'Send test Discord message',category:'SENDS MESSAGE'},
      {name:'trigger',title:'Simulated maintenance button',description:'Exercise the maintenance handler using isolated test records. Does not test wiring or electrical input.',label:'Simulate trigger',category:'SIMULATION'},
      {name:'physical',title:'Physical button',description:'Monitor actual Raspberry Pi input for a short test session without creating a real incident.',label:'Monitor physical input',category:'REAL HARDWARE'},
      {name:'led',title:'Printer LED',description:'Temporarily exercise the configured LED and restore its previous state.',label:'Test LED output',category:'REAL HARDWARE'},
      {name:'offline',title:'Offline record queue',description:'Verify isolated maintenance records and pending deliveries while a test adapter is unavailable.',label:'Test offline queue',category:'ISOLATED DATA'},
      {name:'recovery',title:'Restart recovery',description:'Reload isolated test records to check that pending work survives a service restart.',label:'Test recovery',category:'ISOLATED DATA'}
    ];
    const grid=el('div',{class:'testing-grid'});
    for(const test of tests){const options={};const resultBox=el('div');if(testResults.has(test.name))resultBox.append(resultElement(testResults.get(test.name)));const card=el('section',{class:'test-card'},el('span',{class:'eyebrow'},test.category),el('div',{class:'test-heading'},el('h3',{},test.title)),el('p',{},test.description));
      if(test.name==='email'){options.recipient=state.settings.email.recipients[0]||'';card.append(field('Test recipient',options.recipient,value=>options.recipient=value,{type:'email',placeholder:'name@example.com'}));}
      if(test.name==='discord'){options.destination_id=state.settings.discord.destinations[0]?.id||'';card.append(field('Destination channel',options.destination_id,value=>options.destination_id=value,{options:state.settings.discord.destinations.map(d=>({value:d.id,label:d.name}))}));}
      if(['trigger','physical','led'].includes(test.name)){options.printer_id=active(state.settings.printers)[0]?.id||'';card.append(field('Printer',options.printer_id,value=>options.printer_id=value,{options:printerOptions()}));}
      if(test.name==='trigger'){options.hold_seconds=state.settings.hardware.hold_seconds;card.append(field('Simulated hold (seconds)',options.hold_seconds,value=>options.hold_seconds=value,{type:'number',min:0,max:30,step:0.1}));}
      if(test.name==='physical'){options.duration=5;card.append(field('Session length (seconds)',options.duration,value=>options.duration=value,{type:'number',min:1,max:15}));}
      card.append(button(test.label,event=>busy(event.currentTarget,async()=>{const started=new Date().toISOString();resultBox.replaceChildren(el('div',{class:'test-result'},el('span',{class:'spinner'}),' Check running…'));let result;try{result=await api(`/api/tests/${test.name}`,'POST',options);}catch(error){result={status:'failed',message:error.message,duration_ms:0};}result.local_started_at=started;testResults.set(test.name,result);resultBox.replaceChildren(resultElement(result));}),test.category.startsWith('SENDS')?'dark':'ghost'),resultBox);grid.append(card);
    }
    content.append(grid);const queue=el('div',{class:'job-list'},(state.jobs||[]).filter(j=>j.status!=='sent').slice(0,20).map(job=>el('div',{class:'job-row'},badge(titleCase(job.status),tone(job.status)),el('div',{class:'job-copy'},el('strong',{},`${titleCase(job.kind)} · ${job.attempts||0} attempts`),el('div',{class:'muted'},job.last_error||'Waiting for delivery')))));
    content.append(el('div',{style:'margin-top:8px'},panel('Delivery queue',el('div',{},queue.children.length?queue:el('p',{class:'tiny muted'},'No outstanding delivery jobs.'),el('p',{class:'tiny muted',style:'margin-top:13px'},'Process pending jobs sends real queued records and notifications to their configured destinations.'),el('div',{class:'actions',style:'margin-top:15px'},button('Process pending jobs',event=>busy(event.currentTarget,async()=>{const result=await api('/api/sync','POST',{});await refresh();notify(result.message||'Delivery cycle finished. Review queue status.');renderTesting();}),'dark'))))));
  }

  refresh(true).then(()=>{const query=new URLSearchParams(location.search);if(query.get('google_connected')==='1')notify('Google account connected. Test workbook access before enabling synchronization.');if(query.has('google_error')){const messages={client_missing:'Store your Google OAuth client JSON in Settings → Google storage before connecting.',cancelled:'Google authorization was cancelled. Start Connect Google account again when ready.',wrong_account:`Sign in with ${state.settings.google.account_email}. The selected Google account did not match.`,connection_failed:'Google connection did not complete. Check the OAuth client and callback configuration, then try again.'};notify(messages[query.get('google_error')]||'Google connection did not complete. Review the connection settings and try again.',true);}}).catch(error=>{main.replaceChildren(notice(error.message,'error'),button('Try again',()=>location.reload(),'primary'));});
})();

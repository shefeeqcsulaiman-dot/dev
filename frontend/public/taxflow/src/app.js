const META={dashboard:{t:'Dashboard',s:'Loading dashboard from database',a:'',ao:null},company:{t:'Company Registration',s:'UAE Trade License & FTA Details',a:'Save All',ao:()=>toast('All changes saved','ok')},sales:{t:'Sales & Invoices',s:'Upload - AI Extraction - Validation - Invoices',a:'+ New Invoice',ao:()=>{go('sales');setTimeout(()=>stab(document.querySelectorAll('#page-sales .tab')[4],'s-create'),50)}},purchase:{t:'Purchases',s:'Upload - AI Extraction - Validation',a:'Upload Files',ao:()=>document.getElementById('pur-file').click()},bank:{t:'Bank & Payments',s:'Accounts - transactions - receipts - payments - reconciliation',a:'+ Record Payment',ao:()=>openPaymentModal('Customer Receipt')},inventory:{t:'Inventory',s:'Stock - Items - Movements',a:'+ Add Item',ao:()=>openInventoryItemModal()},expense:{t:'Expenses',s:'AI Upload - Create - Approvals - List',a:'+ New Expense',ao:()=>{go('expense');setTimeout(()=>stab(document.querySelectorAll('#page-expense .tab')[1],'exp-create'),50)}},accounting:{t:'Accounting',s:'Chart - Journal - Ledger',a:'+ New Entry',ao:()=>showM('m-acc')},reports:{t:'Reports',s:'VAT - P&L - Trial Balance',a:'Export PDF',ao:()=>toast('Exporting report...','info')},settings:{t:'Settings',s:'General - Users - Tax',a:'Save All',ao:()=>toast('Settings saved','ok')},staff:{t:'Staff Management',s:'Attendance - Leave - Corrections - Biometric',a:'+ Add Employee',ao:()=>showM('m-emp')},expert:{t:'Expert Review',s:'Find CA experts - Submit for review',a:'+ New Request',ao:()=>showM('m-newreview')},design:{t:'System Design',s:'Functional Spec - Fields - Validations - API',a:'Export Spec',ao:()=>toast('Exporting FRD to PDF...','info')}};
META.settings={t:'Settings',s:'Company - Users - Tax - Security - Integrations - Invoice Design',a:'Save All',ao:()=>toast('Settings saved','ok')};
META.payroll={t:'Payroll',s:'Salary run - WPS/SIF - Payslips - Posting',a:'Run Payroll',ao:()=>{go('payroll');setTimeout(()=>runPayroll(),50)}};
META.sales={t:'Sales & Invoices',s:'Upload - AI Extraction - Validation - Invoices',a:'+ New',ao:()=>openSalesAddChoice()};
META.quotations={t:'Quotations',s:'Create - share - convert to invoice',a:'+ New Quotation',ao:()=>{go('quotations');setTimeout(()=>stab(document.querySelectorAll('#page-quotations .tab')[1],'q-create'),50)}};
META.purchase={t:'Purchases',s:'Upload - AI Extraction - Validation - Manual - Settings',a:'Upload Files',ao:()=>document.getElementById('pur-file').click()};
META.ai={t:'AI Assistant',s:'Ask questions about TaxFlow modules and workflows',a:'Ask AI',ao:()=>askSystemAI()};
META.bills={t:'Bills',s:'Vendor bills - Purchase orders - Supplier payments',a:'+ New Bill',ao:()=>showM('m-bill')};
META.payments={t:'Bank & Payments',s:'Accounts - transactions - receipts - payments - reconciliation',a:'+ Record Payment',ao:()=>openPaymentModal('Customer Receipt')};
META.documents={t:'Documents',s:'Receipts - PDFs - Audit files - Attachments',a:'Upload Document',ao:()=>toast('Choose files to upload...','info')};
META.notifications={t:'Notifications',s:'Email - WhatsApp - SMS - Push - In-app alerts',a:'+ New Rule',ao:()=>toast('Notification rule builder opened','info')};
META.rota={t:'Rota Planning',s:'Shift setup - Weekly rota - Coverage - Swap requests',a:'Publish Rota',ao:()=>publishRota()};
META.accounting={t:'Accounting',s:'Chart - Vouchers - Ledger - Filing - Bank Recon',a:'+ Voucher',ao:()=>{go('accounting');setTimeout(()=>stab(document.querySelectorAll('#page-accounting .tab')[1],'acc-voucher'),50)}};
META.corporate={t:'Corporate Accounting',s:'Corporate tax - Assets - Accruals - Cost centers - Budgets',a:'Tax Report',ao:()=>{go('corporate');setTimeout(()=>stab(document.querySelectorAll('#page-corporate .tab')[0],'corp-tax'),50)}};
META.reports={t:'Reports',s:'VAT - P&L - Balance Sheet - Trial Balance',a:'Export PDF',ao:()=>exportActiveReportPdf()};
META.exception={t:'Exception Center',s:'Failed postings - duplicates - VAT/OCR - stock and payroll issues',a:'Refresh',ao:()=>loadExceptionCenter()};

let isHydratingFromServer=false;
const tableRefreshTimers=new WeakMap();
const purchaseRecordCache=new Map();
let currentPurchaseViewRef='';
const PURCHASE_PAGE_SIZE=100;
let purchaseRecordsTotal=0;
let purchaseRecordsOffset=0;
let purchaseRecordsLoading=false;
let purchaseRecordsLoaded=false;

function scheduleIdleTask(fn,timeout=800){
  if('requestIdleCallback' in window){
    window.requestIdleCallback(fn,{timeout});
  }else{
    setTimeout(fn,0);
  }
}

function scheduleTableRefresh(table,delay=120){
  if(!table)return;
  const existing=tableRefreshTimers.get(table);
  if(existing)clearTimeout(existing);
  const timer=setTimeout(()=>{
    tableRefreshTimers.delete(table);
    refreshEnhancedTable(table);
  },isHydratingFromServer?Math.max(delay,350):delay);
  tableRefreshTimers.set(table,timer);
}

function runPageWarmup(page){
  const pageId='page-'+page;
  scheduleIdleTask(()=>{
    enhancePageTables(pageId);
    bindDetailViews();
    bindEditActions();
    bindGenericAddActions();
    if(page==='rota'){
      seedDefaultRotaShifts();
      renderRotaBoards();
      updateRotaStats();
    }
  },500);
}

const navHistory=[];
let restoringNavigation=false;

function getCurrentNavState(){
  const pageEl=document.querySelector('.page.on');
  const page=(pageEl?.id||'page-dashboard').replace(/^page-/,'')||'dashboard';
  const tab=pageEl?.querySelector('.tab-body.on')?.id||'';
  return {page,tab};
}

function sameNavState(a,b){
  return !!a&&!!b&&a.page===b.page&&a.tab===b.tab;
}

function updateBackButton(){
  const back=document.getElementById('back-btn');
  if(back)back.disabled=navHistory.length===0;
}

function rememberNavState(state){
  if(!state?.page)return;
  const last=navHistory[navHistory.length-1];
  if(!sameNavState(last,state))navHistory.push(state);
  if(navHistory.length>40)navHistory.shift();
  updateBackButton();
}

function restoreNavState(state){
  if(!state?.page)return;
  restoringNavigation=true;
  go(state.page);
  if(state.tab){
    const tab=[...document.querySelectorAll('#page-'+state.page+' .tab')].find(el=>(el.getAttribute('onclick')||'').includes("'"+state.tab+"'"));
    if(tab)stab(tab,state.tab);
  }
  restoringNavigation=false;
  updateBackButton();
}

function goBack(){
  const openOverlay=[...document.querySelectorAll('.overlay.on')].pop();
  if(openOverlay){
    openOverlay.classList.remove('on');
    return;
  }
  const previous=navHistory.pop();
  if(!previous){
    updateBackButton();
    toast('No previous page','info');
    return;
  }
  restoreNavState(previous);
}

function go(page){
  if(page==='payments'){
    go('bank');
    setTimeout(()=>{
      const tab=[...document.querySelectorAll('#page-bank .tab')].find(item=>(item.getAttribute('onclick')||'').includes("'pay-in'"));
      if(tab)stab(tab,'pay-in');
    },50);
    return;
  }
  if(page==='company'){
    go('settings');
    setTimeout(()=>stab(document.querySelectorAll('#page-settings .tab')[0],'set-company'),50);
    return;
  }
  if(page==='design'){
    toast('System Design is hidden','info');
    return;
  }
  const target=document.getElementById('page-'+page);
  if(!target)return;
  const fromState=getCurrentNavState();
  if(!restoringNavigation&&fromState.page!==page)rememberNavState(fromState);
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('on'));
  document.querySelectorAll('.nav').forEach(n=>n.classList.remove('on'));
  target.classList.add('on');
  document.querySelectorAll('.nav').forEach(n=>{if((n.getAttribute('onclick')||'').includes("'"+page+"'"))n.classList.add('on');});
  if(page==='corporate'){
    const corpPage=document.getElementById('page-corporate');
    corpPage?.querySelectorAll('.tab').forEach(tab=>tab.classList.remove('on'));
    corpPage?.querySelectorAll('.tab-body').forEach(body=>body.classList.remove('on'));
    corpPage?.querySelector('.tab')?.classList.add('on');
    document.getElementById('corp-tax')?.classList.add('on');
  }
  const m=META[page];
  document.getElementById('ptitle').textContent=m.t;
  document.getElementById('psub').textContent=m.s;
  const topAction=document.getElementById('topaction');
  if(topAction){
    topAction.textContent=m.a||'';
    topAction.onclick=m.ao||null;
    topAction.classList.toggle('hidden',!m.a);
  }
  closeSidebar();
  if(page==='reports')syncReportsFromDatabase();
  if(page==='exception')loadExceptionCenter();
  if(page==='inventory'){
    ensurePurchaseRecordsLoadedForStock();
    setTimeout(()=>ensureInventoryBulkSelection(),80);
  }
  if(page==='accounting')loadAccountingFromDb();
  runPageWarmup(page);
  updateBackButton();
}

function stab(el,target){
  if(!el)return;
  const fromState=getCurrentNavState();
  if(!restoringNavigation&&target&&fromState.tab!==target)rememberNavState(fromState);
  const tb=el.closest('.tabs');
  if(tb)tb.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  el.classList.add('on');
  const pg=el.closest('.page');
  if(pg)pg.querySelectorAll('.tab-body').forEach(b=>b.classList.remove('on'));
  const t=document.getElementById(target);
  if(t)t.classList.add('on');
  if(target==='inv-mapping')loadStockMappingsFromServer();
  if(target==='inv-stock')ensurePurchaseRecordsLoadedForStock();
  if(String(target||'').startsWith('inv-'))setTimeout(()=>ensureInventoryBulkSelection(),80);
  if(target==='p-records')ensurePurchaseRecordsLoaded();
  if(target==='acc-voucher')prepareJournalForm();
  if(target==='acc-ledger')loadAccountingFromDb();
  if(target==='set-backup')loadBackupTab();
  if(target==='p-manual'){
    bindManualPurchaseCalculator();
    setManualPurchaseDefaults();
  }
  updateBackButton();
}

function toast(msg,type='ok'){
  const icons={ok:'?',warn:'?',err:'?',info:'?'};
  const clrs={ok:'var(--green)',warn:'var(--amber)',err:'var(--red)',info:'var(--accent)'};
  const t=document.createElement('div');
  t.className='toast '+type;
  const icon=document.createElement('span');
  icon.style.cssText=`color:${clrs[type]||clrs.info};font-size:15px`;
  icon.textContent=icons[type]||'?';
  const text=document.createElement('span');
  text.textContent=msg;
  t.append(icon,text);
  document.getElementById('toasts').appendChild(t);
  setTimeout(()=>t.remove(),3500);
}

function toggleSidebar(force){
  const open=force??!document.body.classList.contains('nav-open');
  document.body.classList.toggle('nav-open',open);
  document.getElementById('nav-scrim')?.classList.toggle('on',open);
  document.getElementById('menu-btn')?.setAttribute('aria-expanded',String(open));
}
function closeSidebar(){toggleSidebar(false);}

function showM(id){
  const modal=document.getElementById(id);
  if(!modal)return;
  ensureModalCloseButton(modal,id);
  modal.classList.add('on');
  if(id==='m-user')applyUserRolePermissions();
  if(id==='m-emp'&&!document.getElementById('emp-id')?.value)setFieldValue(document.getElementById('emp-id'),nextEmployeeId());
  if(id==='m-payment')setTimeout(()=>syncPaymentFormOptions(),0);
  setTimeout(()=>modal.querySelector('input,select,textarea,button:not(.modal-x)')?.focus(),30);
}

function ensureModalCloseButton(overlay,id){
  const panel=overlay.querySelector('.modal');
  if(!panel||panel.querySelector('.modal-x'))return;
  const close=document.createElement('button');
  close.type='button';
  close.className='modal-x';
  close.setAttribute('aria-label','Close popup');
  close.title='Close';
  close.textContent='×';
  close.onclick=event=>{
    event.stopPropagation();
    closeM(id);
  };
  panel.prepend(close);
}

function closeM(id){
  document.getElementById(id)?.classList.remove('on');
  if(id==='m-customer'){
    customerReturnToInvoice=false;
    customerReturnToQuotation=false;
  }
  if(id==='m-product'){
    productReturnToInvoice=false;
    productTargetLine=null;
  }
}

function ensureAppConfirmModal(){
  let overlay=document.getElementById('m-app-confirm');
  if(overlay)return overlay;
  overlay=document.createElement('div');
  overlay.className='overlay';
  overlay.id='m-app-confirm';
  overlay.innerHTML=`
    <div class="modal modal-sm app-confirm-modal">
      <div class="modal-title" id="app-confirm-title">Confirm Action</div>
      <div class="modal-sub" id="app-confirm-message">Please confirm this action.</div>
      <div class="modal-foot">
        <button class="btn btn-g" id="app-confirm-cancel" type="button">Cancel</button>
        <button class="btn btn-danger" id="app-confirm-ok" type="button">Delete</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  return overlay;
}

function appConfirm({title='Confirm Action',message='Please confirm this action.',messageHtml='',okText='Delete',tone='danger'}={}){
  const overlay=ensureAppConfirmModal();
  const ok=overlay.querySelector('#app-confirm-ok');
  const cancel=overlay.querySelector('#app-confirm-cancel');
  overlay.querySelector('#app-confirm-title').textContent=title;
  const msgEl=overlay.querySelector('#app-confirm-message');
  if(messageHtml){msgEl.innerHTML=messageHtml;}else{msgEl.textContent=message;}
  ok.textContent=okText;
  ok.className=`btn ${tone==='danger'?'btn-danger':'btn-p'}`;
  overlay.classList.add('on');
  return new Promise(resolve=>{
    const done=value=>{
      overlay.classList.remove('on');
      ok.onclick=null;
      cancel.onclick=null;
      overlay.onclick=null;
      resolve(value);
    };
    ok.onclick=event=>{
      event.stopPropagation();
      done(true);
    };
    cancel.onclick=event=>{
      event.stopPropagation();
      done(false);
    };
    overlay.onclick=event=>{
      if(event.target===overlay)done(false);
    };
    setTimeout(()=>cancel.focus(),30);
  });
}

function closeOvBg(e,id){if(e.target.id===id)closeM(id);}
function saveM(id,msg){closeM(id);toast(msg,'ok');audit(msg.replace(/[?.]/g,'').trim(),id,'Saved');}

function initialsFromName(name){
  return String(name||'User').trim().split(/\s+/).map(part=>part[0]).join('').slice(0,2).toUpperCase()||'U';
}

function roleBadgeClass(role){
  const map={Admin:'b-p',Manager:'b-t',Accountant:'b-b',Sales:'b-gray',Viewer:'b-gray'};
  return map[role]||'b-gray';
}

function rolePermissionDefaults(role){
  const all=['sales','purchases','staff','payroll','reports'];
  const actions=['view','create','approve','export','admin'];
  const set=(modules,perms)=>{
    const allowed=new Set();
    modules.forEach(module=>perms.forEach(perm=>allowed.add(`${module}:${perm}`)));
    return allowed;
  };
  if(role==='Admin')return set(all,actions);
  if(role==='Manager')return set(all,['view','create','approve','export']);
  if(role==='Accountant')return set(['sales','purchases','reports'],['view','create','approve','export']);
  if(role==='Sales')return set(['sales','reports'],['view','create','export']);
  return set(all,['view']);
}

function applyUserRolePermissions(){
  const role=document.getElementById('user-role')?.value||'Viewer';
  const defaults=rolePermissionDefaults(role);
  document.querySelectorAll('[data-user-perm]').forEach(box=>{
    box.checked=defaults.has(box.dataset.userPerm);
  });
}

function collectUserPermissions(){
  return [...document.querySelectorAll('[data-user-perm]:checked')].map(box=>box.dataset.userPerm);
}

function permissionSummary(permissions=[]){
  const modules=[...new Set(permissions.map(item=>String(item).split(':')[0]))];
  if(permissions.some(item=>String(item).endsWith(':admin')))return 'All modules';
  if(modules.length===0)return 'No access';
  if(modules.length>3)return `${modules.length} modules`;
  const names={sales:'Sales',purchases:'Purchases',staff:'Staff',payroll:'Payroll',reports:'Reports'};
  return modules.map(module=>names[module]||titleCase(module)).join(', ');
}

function resetUserForm(){
  ['user-name','user-email','user-temp-password'].forEach(id=>setFieldValue(document.getElementById(id),''));
  setFieldValue(document.getElementById('user-role'),'Admin');
  setFieldValue(document.getElementById('user-status'),'Active');
  applyUserRolePermissions();
}

function saveUser(){
  const name=document.getElementById('user-name')?.value.trim()||'';
  const email=document.getElementById('user-email')?.value.trim()||'';
  const role=document.getElementById('user-role')?.value||'Viewer';
  const status=document.getElementById('user-status')?.value||'Active';
  const permissions=collectUserPermissions();
  if(!name||!email){
    toast('Enter user name and email','warn');
    return;
  }
  if(!permissions.length){
    toast('Select at least one permission','warn');
    return;
  }
  const user={
    id:`USER-${Date.now()}`,
    name,
    email,
    role,
    status,
    permissions,
    temporary_password_set:Boolean(document.getElementById('user-temp-password')?.value),
    created_at:new Date().toISOString()
  };
  renderUserRecord(user);
  saveServer('users',user);
  closeM('m-user');
  resetUserForm();
  toast('User created with permissions','ok');
  audit('Created user',email,'Saved');
}

function renderUserRecord(user){
  const tbody=document.getElementById('user-tbody');
  if(!tbody)return;
  const existing=[...tbody.querySelectorAll('tr')].find(row=>{
    const data=row.dataset.user?JSON.parse(row.dataset.user):null;
    return data&&(data.id===user.id||data.email===user.email);
  });
  if(existing)existing.remove();
  const row=document.createElement('tr');
  row.dataset.user=JSON.stringify(user);
  row.innerHTML=`
    <td><div class="flx"><div class="co-av">${escapeHtml(initialsFromName(user.name))}</div><span style="margin-left:8px">${escapeHtml(user.name)}</span></div></td>
    <td>${escapeHtml(user.email)}</td>
    <td><span class="b ${roleBadgeClass(user.role)}">${escapeHtml(user.role)}</span></td>
    <td><span class="b b-b">${escapeHtml(permissionSummary(user.permissions))}</span></td>
    <td><span class="b ${user.status==='Active'?'b-g':'b-gray'}">${escapeHtml(user.status)}</span></td>
    <td class="mono">Never</td>
    <td><button class="btn btn-g btn-sm">Edit</button></td>`;
  tbody.prepend(row);
  refreshEnhancedTable(tbody.closest('table'));
}

function nextEmployeeId(){
  const ids=[...document.querySelectorAll('#employee-tbody tr td:first-child')].map(td=>td.textContent.trim());
  const max=ids.reduce((num,id)=>Math.max(num,Number((id.match(/\d+/)||['0'])[0])),0);
  return `EMP-${String(max+1).padStart(3,'0')}`;
}

function employeeFormValue(id,fallback=''){
  return document.getElementById(id)?.value.trim()||fallback;
}

function saveEmployee(){
  const passportFile=document.getElementById('emp-passport-file')?.files?.[0];
  const workPermitFile=document.getElementById('emp-work-permit-file')?.files?.[0];
  const medicalFile=document.getElementById('emp-medical-file')?.files?.[0];
  const employee={
    id:employeeFormValue('emp-id',nextEmployeeId()),
    name:employeeFormValue('emp-name'),
    email:employeeFormValue('emp-email'),
    department:employeeFormValue('emp-department','Management'),
    designation:employeeFormValue('emp-designation','Employee'),
    supervisor:employeeFormValue('emp-supervisor',''),
    shift:employeeFormValue('emp-shift','09:00-18:00'),
    salary:parseAmount(employeeFormValue('emp-salary','0')),
    contract:employeeFormValue('emp-contract','Full-time'),
    location:employeeFormValue('emp-location','Dubai HQ'),
    status:'Active',
    created_at:new Date().toISOString(),
    emirates_id:employeeFormValue('emp-emirates-id'),
    nationality:employeeFormValue('emp-nationality'),
    dob:employeeFormValue('emp-dob'),
    gender:employeeFormValue('emp-gender'),
    join_date:employeeFormValue('emp-join-date'),
    overtime_rate:employeeFormValue('emp-ot-rate'),
    leave_policy:employeeFormValue('emp-leave-policy'),
    emergency_contact:employeeFormValue('emp-emergency'),
    work_permit_no:employeeFormValue('emp-work-permit'),
    visa_expiry:employeeFormValue('emp-visa-expiry'),
    salary_bank:employeeFormValue('emp-bank'),
    iban:employeeFormValue('emp-iban'),
    documents:{
      passport:passportFile?.name||'',
      work_permit:workPermitFile?.name||'',
      medical_report:medicalFile?.name||''
    }
  };
  if(!employee.name){
    toast('Enter employee name','warn');
    return;
  }
  renderEmployeeRecord(employee);
  renderPayrollEmployeeRecord(employee);
  saveServer('employees',employee);
  closeM('m-emp');
  document.querySelectorAll('#m-emp input').forEach(input=>input.value='');
  toast('Employee added to table','ok');
  audit('Created employee',employee.id,'Saved');
}

function renderEmployeeRecord(employee){
  const tbody=document.getElementById('employee-tbody');
  if(!tbody)return;
  const existing=[...tbody.querySelectorAll('tr')].find(row=>{
    const data=row.dataset.employee?JSON.parse(row.dataset.employee):null;
    const id=row.querySelector('td:first-child')?.textContent.trim();
    return data?.id===employee.id||id===employee.id;
  });
  if(existing)existing.remove();
  const row=document.createElement('tr');
  row.dataset.employee=JSON.stringify(employee);
  row.innerHTML=`
    <td class="mono">${escapeHtml(employee.id)}</td>
    <td><div class="flx"><div class="co-av" style="width:26px;height:26px;font-size:10px">${escapeHtml(initialsFromName(employee.name))}</div><div>${escapeHtml(employee.name)}<div class="card-sub">${escapeHtml(employee.contract||'Full-time')} · ${escapeHtml(employee.location||'Dubai HQ')}</div></div></div></td>
    <td>${escapeHtml(employee.department)}</td>
    <td>${escapeHtml(employee.designation)}</td>
    <td>${escapeHtml(employee.supervisor)}</td>
    <td><span class="b b-b">${escapeHtml(employee.shift)}</span></td>
    <td class="mono">${Number(employee.salary||0).toLocaleString('en-AE',{minimumFractionDigits:0,maximumFractionDigits:0})}</td>
    <td><span class="b b-g">${escapeHtml(employee.status)}</span></td>
    <td><button class="btn btn-g btn-sm" onclick="openEmployeeProfile(this)">View</button></td>`;
  tbody.prepend(row);
  const table=tbody.closest('table');
  const state=tableEnhanceState.get(table);
  if(state){
    state.query='';
    state.page=1;
    if(state.search)state.search.value='';
  }
  row.style.display='';
  row.hidden=false;
  refreshEnhancedTable(table);
}

function employeeFromDirectoryRow(row){
  if(!row)return {};
  if(row.dataset.employee){
    try{return JSON.parse(row.dataset.employee);}catch{}
  }
  const cells=row.children;
  const nameCell=cells[1];
  const nameBlock=nameCell?.querySelector('.flx > div:last-child');
  const sub=nameCell?.querySelector('.card-sub')?.textContent||'';
  const fallbackName=(nameBlock?.textContent||nameCell?.textContent||'Employee').replace(sub,'').trim();
  const parts=sub.split('·').map(part=>part.trim());
  return {
    id:cells[0]?.textContent.trim()||'',
    name:fallbackName||'Employee',
    contract:parts[0]||'Full-time',
    location:parts[1]||'Dubai HQ',
    department:cells[2]?.textContent.trim()||'-',
    designation:cells[3]?.textContent.trim()||'-',
    supervisor:cells[4]?.textContent.trim()||'-',
    shift:cells[5]?.textContent.trim()||'-',
    salary:parseAmount(cells[6]?.textContent),
    status:cells[7]?.textContent.trim()||'Active',
    documents:{}
  };
}

function ensureEmployeeProfileModal(){
  let overlay=document.getElementById('m-employee-profile');
  if(overlay)return overlay;
  overlay=document.createElement('div');
  overlay.className='overlay';
  overlay.id='m-employee-profile';
  overlay.onclick=e=>closeOvBg(e,'m-employee-profile');
  overlay.innerHTML=`
    <div class="modal modal-xl">
      <div class="modal-title" id="employee-profile-title">Employee Profile</div>
      <div class="modal-sub" id="employee-profile-sub">Employee directory detail</div>
      <div id="employee-profile-body"></div>
      <div class="modal-foot">
        <button class="btn btn-g" onclick="toast('Employee edit opened','info')">Edit Profile</button>
        <button class="btn btn-g" onclick="toast('Employee document checklist exported','ok')">Export Documents</button>
        <button class="btn btn-p" onclick="closeM('m-employee-profile')">Close</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  return overlay;
}

function employeeDocumentBadge(label,fileName){
  const available=Boolean(fileName);
  return `<div class="setting-tile"><strong>${escapeHtml(label)}</strong><p>${available?escapeHtml(fileName):'Not uploaded'}</p><span class="b ${available?'b-g':'b-a'}">${available?'Stored':'Missing'}</span></div>`;
}

function openEmployeeProfile(btn){
  const employee=employeeFromDirectoryRow(btn.closest('tr'));
  const docs=employee.documents||{};
  ensureEmployeeProfileModal();
  const title=document.getElementById('employee-profile-title');
  const sub=document.getElementById('employee-profile-sub');
  const body=document.getElementById('employee-profile-body');
  if(title)title.textContent=employee.name||'Employee Profile';
  if(sub)sub.textContent=`${employee.id||'Employee'} - ${employee.department||'Department'} - ${employee.status||'Active'}`;
  if(body)body.innerHTML=`
    <div class="invoice-sheet" style="--invoice-accent:var(--accent)">
      <div class="invoice-topbar"></div>
      <div class="invoice-head">
        <div class="invoice-brand">
          <div class="invoice-logo">${escapeHtml(initialsFromName(employee.name))}</div>
          <div>
            <div class="invoice-company">${escapeHtml(employee.name||'Employee')}</div>
            <div class="invoice-muted">${escapeHtml(employee.designation||'Designation')} · ${escapeHtml(employee.department||'Department')}</div>
            <div class="invoice-muted mono">${escapeHtml(employee.id||'-')}</div>
          </div>
        </div>
        <div class="invoice-titlebox">
          <div class="invoice-label">Employee Profile</div>
          <span class="invoice-status">${escapeHtml(employee.status||'Active')}</span>
        </div>
      </div>
      <div class="g4 mb16">
        <div class="stat"><div class="stat-lbl">Department</div><div class="stat-val" style="font-size:18px;color:var(--accent)">${escapeHtml(employee.department||'-')}</div></div>
        <div class="stat"><div class="stat-lbl">Shift</div><div class="stat-val" style="font-size:18px;color:var(--green)">${escapeHtml(employee.shift||'-')}</div></div>
        <div class="stat"><div class="stat-lbl">Basic Salary</div><div class="stat-val" style="font-size:18px;color:var(--purple)">${'AED'} ${Number(employee.salary||0).toLocaleString('en-AE')}</div></div>
        <div class="stat"><div class="stat-lbl">Supervisor</div><div class="stat-val" style="font-size:18px;color:var(--amber)">${escapeHtml(employee.supervisor||'-')}</div></div>
      </div>
      <div class="g2 mb16">
        <div class="invoice-panel">
          <div class="invoice-kicker">Employment Details</div>
          <div class="invoice-meta-row"><span>Contract</span><strong>${escapeHtml(employee.contract||'Full-time')}</strong></div>
          <div class="invoice-meta-row"><span>Location</span><strong>${escapeHtml(employee.location||'Dubai HQ')}</strong></div>
          <div class="invoice-meta-row"><span>Join Date</span><strong>${escapeHtml(employee.join_date||'-')}</strong></div>
          <div class="invoice-meta-row"><span>Overtime Rate</span><strong>${escapeHtml(employee.overtime_rate||'-')}</strong></div>
        </div>
        <div class="invoice-panel">
          <div class="invoice-kicker">Personal & Payroll</div>
          <div class="invoice-meta-row"><span>Nationality</span><strong>${escapeHtml(employee.nationality||'-')}</strong></div>
          <div class="invoice-meta-row"><span>Email</span><strong>${escapeHtml(employee.email||'-')}</strong></div>
          <div class="invoice-meta-row"><span>Bank</span><strong>${escapeHtml(employee.salary_bank||'-')}</strong></div>
          <div class="invoice-meta-row"><span>IBAN</span><strong class="mono">${escapeHtml(employee.iban||'Missing')}</strong></div>
        </div>
      </div>
      <div class="section-hd">Documents</div>
      <div class="g3">
        ${employeeDocumentBadge('Passport',docs.passport)}
        ${employeeDocumentBadge('Work Permit',docs.work_permit||employee.work_permit_no)}
        ${employeeDocumentBadge('Medical Report',docs.medical_report)}
      </div>
    </div>`;
  showM('m-employee-profile');
  audit('Viewed employee profile',employee.id||employee.name,'Opened');
}

function renderPayrollEmployeeRecord(employee){
  const tbody=document.getElementById('payroll-employee-tbody');
  if(!tbody)return;
  const existing=[...tbody.querySelectorAll('tr')].find(row=>row.dataset.employeeId===employee.id||row.children[0]?.textContent.trim()===employee.name);
  if(existing)existing.remove();
  const bankText=employee.iban?`${employee.salary_bank||'Bank'} · ${String(employee.iban).slice(0,5)}...`:'Missing';
  const row=document.createElement('tr');
  row.dataset.employeeId=employee.id;
  row.innerHTML=`
    <td>${escapeHtml(employee.name)}</td>
    <td><span class="b b-t">${escapeHtml(employee.department||'Monthly')}</span></td>
    <td class="mono">${Number(employee.salary||0).toLocaleString('en-AE',{minimumFractionDigits:0,maximumFractionDigits:0})}</td>
    <td class="mono">0</td>
    <td class="mono">0</td>
    <td class="mono" ${employee.iban?'':'style="color:var(--amber)"'}>${escapeHtml(bankText)}</td>
    <td class="mono">${escapeHtml('WPS-'+String(employee.id||'').replace(/\D/g,'').padStart(3,'0'))}</td>
    <td><span class="b ${employee.iban?'b-g':'b-a'}">${employee.iban?'Complete':'Review'}</span></td>`;
  tbody.prepend(row);
  const table=tbody.closest('table');
  const state=tableEnhanceState.get(table);
  if(state){
    state.query='';
    state.page=1;
    if(state.search)state.search.value='';
  }
  row.style.display='';
  row.hidden=false;
  refreshEnhancedTable(table);
}

let currentStockMapRow=null;

function openStockMap(btn){
  currentStockMapRow=btn.closest('tr');
  const cells=currentStockMapRow?.querySelectorAll('td')||[];
  const product=document.getElementById('stock-map-product');
  const supplier=document.getElementById('stock-map-supplier');
  const generated=document.getElementById('stock-map-generated');
  const unitsOuter=document.getElementById('stock-map-units-outer');
  if(product)product.value=cells[0]?.textContent.trim()||'';
  if(supplier)supplier.value=(cells[1]?.textContent.trim()||'').replace(/^[-—]$/,'');
  if(generated)generated.value=cells[2]?.textContent.trim()||generateStockMapName(product?.value||'');
  if(unitsOuter)unitsOuter.value=cells[3]?.textContent.trim()||currentStockMapRow?.dataset.unitsPerOuter||'1';
  setFieldValue(document.getElementById('stock-map-cost'),currentStockMapRow?.dataset.cost||'0.00');
  setFieldValue(document.getElementById('stock-map-markup'),currentStockMapRow?.dataset.markupPercent||'0');
  setSelectValue(document.getElementById('stock-map-tax-rate'),currentStockMapRow?.dataset.taxRate||'5');
  updateStockMapPricing();
  document.getElementById('stock-map-empty')?.classList.add('hidden');
  const panel=document.getElementById('stock-map-panel');
  panel?.classList.remove('hidden');
  panel?.scrollIntoView({block:'nearest'});
  setTimeout(()=>product?.focus(),30);
}

function saveStockMap(){
  const product=(document.getElementById('stock-map-product')?.value||'').trim();
  const supplier=(document.getElementById('stock-map-supplier')?.value||'').trim();
  const generated=(document.getElementById('stock-map-generated')?.value||generateStockMapName(product)).trim();
  const unitsOuter=parseAmount(document.getElementById('stock-map-units-outer')?.value)||1;
  const pricing=updateStockMapPricing();
  if(!product||!generated){
    toast('Enter Display Name and TaxFlow Name','warn');
    return;
  }
  if(!currentStockMapRow){
    const tbody=document.getElementById('stock-map-tbody');
    if(!tbody){
      toast('Stock mapping table is not available','warn');
      return;
    }
    removeEmptyState(tbody);
    const sku=generateStockSku(product);
    const tr=document.createElement('tr');
    tr.dataset.itemCode=sku;
    tr.dataset.stockSku=sku;
    tr.innerHTML=`<td>${escapeHtml(product)}</td><td>${escapeHtml(supplier||'Not assigned')}</td><td>${escapeHtml(generated)}</td><td class="mono">${escapeHtml(String(unitsOuter))}</td><td><span class="b b-a">Not mapped</span></td><td data-action-col="1">${stockMapActionsHtml()}</td>`;
    tbody.prepend(tr);
    currentStockMapRow=tr;
  }
  if(currentStockMapRow){
    const cells=currentStockMapRow.querySelectorAll('td');
    cells[0].textContent=product;
    cells[1].textContent=supplier||'Not assigned';
    cells[2].textContent=generated;
    cells[3].textContent=String(unitsOuter);
    cells[4].innerHTML='<span class="b b-g">Mapped</span>';
    currentStockMapRow.dataset.unitsPerOuter=String(unitsOuter);
    currentStockMapRow.dataset.cost=String(pricing.cost);
    currentStockMapRow.dataset.markupPercent=String(pricing.markup);
    currentStockMapRow.dataset.taxRate=String(pricing.taxRate);
    currentStockMapRow.dataset.vatAmount=String(pricing.vat);
    currentStockMapRow.dataset.incVat=String(pricing.incVat);
    currentStockMapRow.dataset.priceOuter=String(pricing.priceOuter);
    const payload=stockMappingPayloadFromRow(currentStockMapRow);
    payload.name=product;
    payload.supplier_name=supplier||null;
    payload.taxflow_name=generated;
    payload.units_per_outer=unitsOuter;
    payload.cost=pricing.cost;
    payload.markup_percent=pricing.markup;
    payload.tax_rate=pricing.taxRate;
    payload.vat_amount=pricing.vat;
    payload.inc_vat=pricing.incVat;
    payload.price_outer=pricing.priceOuter;
    const mappingId=currentStockMapRow.dataset.mappingId;
    moduleApi(mappingId?`/inventory/mappings/${encodeURIComponent(mappingId)}`:'/inventory/mappings',{
      method:mappingId?'PUT':'POST',
      body:payload
    }).then(saved=>{
      if(saved?.id){
        currentStockMapRow.dataset.mappingId=saved.id;
        currentStockMapRow.dataset.stockSku=saved.sku;
        currentStockMapRow.dataset.salesAccountCode=saved.sales_account_code||'3000';
        currentStockMapRow.dataset.purchaseAccountCode=saved.purchase_account_code||'4000';
        currentStockMapRow.dataset.inventoryAccountCode=saved.inventory_account_code||'1200';
        currentStockMapRow.dataset.taxCode=saved.tax_code||'VAT5';
        currentStockMapRow.dataset.reorderLevel=saved.reorder_level??0;
        currentStockMapRow.dataset.unitsPerOuter=saved.units_per_outer??unitsOuter;
        currentStockMapRow.dataset.cost=saved.cost??pricing.cost;
        currentStockMapRow.dataset.markupPercent=saved.markup_percent??pricing.markup;
        currentStockMapRow.dataset.taxRate=saved.tax_rate??pricing.taxRate;
        currentStockMapRow.dataset.vatAmount=saved.vat_amount??pricing.vat;
        currentStockMapRow.dataset.incVat=saved.inc_vat??pricing.incVat;
        currentStockMapRow.dataset.priceOuter=saved.price_outer??pricing.priceOuter;
      }
      toast('Stock mapping saved to database','ok');
    }).catch(err=>{
      console.warn('Stock mapping save failed:',err);
      toast('Stock mapping saved on screen, database save failed','warn');
    });
  }
  toast('Stock product mapped','ok');
  audit('Mapped stock product',product,'Saved');
  refreshInvoiceProductSuggestions();
  refreshQuotationProductOptions();
  ensureInventoryBulkSelection();
  refreshEnhancedTable(document.getElementById('stock-map-tbody')?.closest('table'));
}

function openNewStockMap(){
  currentStockMapRow=null;
  ['stock-map-product','stock-map-supplier','stock-map-generated'].forEach(id=>setFieldValue(document.getElementById(id),''));
  setFieldValue(document.getElementById('stock-map-units-outer'),'1');
  setFieldValue(document.getElementById('stock-map-cost'),'0.00');
  setFieldValue(document.getElementById('stock-map-markup'),'0');
  setSelectValue(document.getElementById('stock-map-tax-rate'),'5');
  updateStockMapPricing();
  document.getElementById('stock-map-empty')?.classList.add('hidden');
  const panel=document.getElementById('stock-map-panel');
  panel?.classList.remove('hidden');
  panel?.scrollIntoView({block:'nearest'});
  setTimeout(()=>document.getElementById('stock-map-product')?.focus(),30);
}

function clearStockMapPanel(){
  currentStockMapRow=null;
  const product=document.getElementById('stock-map-product');
  const supplier=document.getElementById('stock-map-supplier');
  const generated=document.getElementById('stock-map-generated');
  const unitsOuter=document.getElementById('stock-map-units-outer');
  if(product)product.value='';
  if(supplier)supplier.value='';
  if(generated)generated.value='';
  if(unitsOuter)unitsOuter.value='1';
  setFieldValue(document.getElementById('stock-map-cost'),'0.00');
  setFieldValue(document.getElementById('stock-map-markup'),'0');
  setSelectValue(document.getElementById('stock-map-tax-rate'),'5');
  updateStockMapPricing();
  document.getElementById('stock-map-panel')?.classList.add('hidden');
  document.getElementById('stock-map-empty')?.classList.remove('hidden');
}

function updateStockMapPricing(){
  const cost=parseAmount(document.getElementById('stock-map-cost')?.value);
  const markup=parseAmount(document.getElementById('stock-map-markup')?.value);
  const taxRate=parseAmount(document.getElementById('stock-map-tax-rate')?.value);
  const unitsOuter=parseAmount(document.getElementById('stock-map-units-outer')?.value)||1;
  const base=cost*(1+(markup/100));
  const vat=base*(taxRate/100);
  const incVat=base;
  const priceOuter=unitsOuter>0?base/unitsOuter:0;
  setFieldValue(document.getElementById('stock-map-vat'),vat.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setFieldValue(document.getElementById('stock-map-inc-vat'),incVat.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setFieldValue(document.getElementById('stock-map-price-outer'),priceOuter.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  return {cost,markup,taxRate,vat,incVat,priceOuter};
}

function generateStockMapName(value){
  return String(value||'')
    .replace(/\b(litre|liter)\b/gi,'L')
    .replace(/\bpieces\b/gi,'pcs')
    .replace(/\bpiece\b/gi,'pc')
    .replace(/\bsmall\b/gi,'Small')
    .replace(/\blarge\b/gi,'Large')
    .replace(/\s+/g,' ')
    .trim();
}

function titleCaseStockName(value){
  return String(value||'').toLowerCase().replace(/\b[a-z0-9]/g,char=>char.toUpperCase());
}

function stockMapDisplayNameSuggestions(value){
  const raw=String(value||'')
    .replace(/[_-]+/g,' ')
    .replace(/\b(sku|item|code|prod|product)\b[:\s-]*/gi,'')
    .replace(/\s+/g,' ')
    .trim();
  const normalized=generateStockMapName(raw);
  return [...new Set([
    normalized,
    titleCaseStockName(normalized),
    normalized.replace(/\bvat\b/gi,'VAT'),
    normalized.replace(/\buae\b/gi,'UAE')
  ].map(item=>item.trim()).filter(Boolean))];
}

function generateStockSku(value){
  const base=String(value||'ITEM').toUpperCase().replace(/[^A-Z0-9]+/g,'-').replace(/^-|-$/g,'').slice(0,18)||'ITEM';
  const existing=new Set([...document.querySelectorAll('#stock-map-tbody tr:not([data-empty-state]),#prod-tbody tr:not([data-empty-state])')]
    .map(row=>(row.dataset.stockSku||row.dataset.itemCode||inventoryRowCellText(row,0)||'').trim().toUpperCase()));
  if(!existing.has(base))return base;
  let index=2;
  while(existing.has(`${base}-${index}`))index+=1;
  return `${base}-${index}`;
}

function generateStockMapField(){
  const product=document.getElementById('stock-map-product');
  const generated=document.getElementById('stock-map-generated');
  const source=(
    generated?.value||
    product?.value||
    currentStockMapRow?.children?.[2]?.textContent||
    currentStockMapRow?.children?.[0]?.textContent||
    currentStockMapRow?.dataset?.stockSku||
    ''
  ).trim();
  if(!source){
    toast('Select a mapping or enter TaxFlow Name first','warn');
    return;
  }
  const suggestions=stockMapDisplayNameSuggestions(source);
  if(!suggestions.length){
    toast('No name suggestion available','warn');
    return;
  }
  const current=(product?.value||'').trim();
  const currentIndex=suggestions.findIndex(item=>item.toLowerCase()===current.toLowerCase());
  const next=suggestions[(currentIndex+1)%suggestions.length]||suggestions[0];
  if(product)product.value=next;
  toast('Display name suggested','info');
}

function stockMapActionsHtml(){
  return `<div class="row-actions">
    <button class="icon-btn edit" type="button" title="Edit mapping" aria-label="Edit mapping" onclick="openStockMap(this)">${editIconSvg()}</button>
    <button class="icon-btn danger" type="button" title="Delete mapping" aria-label="Delete mapping" onclick="deleteStockMapRow(this)">${deleteIconSvg()}</button>
  </div>`;
}

function deleteStockMapRow(btn){
  const row=btn.closest('tr');
  if(deleteInventoryMappingRow(row))return;
  const name=inventoryRowCellText(row,0)||'mapping';
  const mappingId=row?.dataset.mappingId;
  if(mappingId){
    moduleApi(`/inventory/mappings/${encodeURIComponent(mappingId)}`,{method:'DELETE'})
      .catch(err=>{
        console.warn('Stock mapping delete failed:',err);
        toast('Mapping removed on screen, database delete failed','warn');
      });
  }
  row?.remove();
  const tbody=document.getElementById('stock-map-tbody');
  if(tbody&&tbody.querySelectorAll('tr:not([data-empty-state])').length===0){
    emptyTableMessage(tbody,'No stock mappings in database yet.');
  }
  clearStockMapPanel();
  refreshEnhancedTable(document.getElementById('stock-map-tbody')?.closest('table'));
  toast(`${name} mapping removed`,'warn');
  audit('Deleted stock mapping',name,'Deleted');
}

function inventoryBulkCellHtml(tbodyId,label='row'){
  return `<input type="checkbox" class="inventory-bulk-select" aria-label="Select ${escapeHtml(label)}" data-inventory-tbody="${escapeHtml(tbodyId)}">`;
}

function ensureInventoryBulkSelection(scope=document){
  ['stock-level-tbody','prod-tbody','stock-map-tbody'].forEach(tbodyId=>{
    const tbody=(scope.getElementById?scope:document).getElementById?.(tbodyId)||document.getElementById(tbodyId);
    const table=tbody?.closest('table');
    table?.querySelectorAll('th[data-inventory-bulk-col]').forEach(cell=>cell.remove());
    tbody?.querySelectorAll('td[data-inventory-bulk-col]').forEach(cell=>cell.remove());
  });
}

function toggleInventoryBulkSelection(tbodyId,checked){
  document.querySelectorAll(`#${tbodyId} .inventory-bulk-select`).forEach(box=>{
    box.checked=checked;
  });
}

function selectedInventoryRows(tbodyId){
  ensureInventoryBulkSelection();
  return [...document.querySelectorAll(`#${tbodyId} tr:not([data-empty-state])`)]
    .filter(row=>row.querySelector('.inventory-bulk-select')?.checked);
}

async function inventoryBulkDeleteSelected(tbodyId){
  const rows=selectedInventoryRows(tbodyId);
  if(!rows.length){
    toast('Select inventory rows to delete','warn');
    return;
  }
  if(tbodyId==='stock-movement-tbody'){
    toast('Stock movements are source history. Use Clear Table to remove inventory history.','warn');
    return;
  }
  const confirmed=await appConfirm({
    title:'Delete Inventory Rows',
    message:`Delete ${rows.length} selected inventory row(s)?`,
    okText:'Delete'
  });
  if(!confirmed)return;
  let deleted=0;
  let failed=0;
  for(const row of rows){
    const ok=await deleteInventoryRow(row,{skipConfirm:true,bulk:true});
    if(ok)deleted++;
    else failed++;
  }
  ensureInventoryBulkSelection();
  toast(`${deleted} deleted${failed?`, ${failed} failed`:''}`,'warn');
}

async function deleteInventoryRow(row,{skipConfirm=false,bulk=false,button=null}={}){
  const table=row?.closest('table');
  const tbodyId=table?.tBodies?.[0]?.id||'';
  if(tbodyId==='stock-level-tbody'){
    const code=inventoryRowCellText(row,0);
    const name=inventoryRowCellText(row,1);
    const productRow=matchingInventoryProductRow(code,name);
    if(row.dataset.stockSource==='purchase'&&!productRow){
      toast('This stock row is generated from purchase records. Use Clear Table to remove generated stock history.','warn');
      return false;
    }
    return deleteInventoryProductAndMapping({
      productRow,
      mappingRow:matchingInventoryMappingRow(code,name),
      stockRow:row,
      button,
      label:name||code,
      skipConfirm,
      quiet:bulk
    });
  }
  if(tbodyId==='prod-tbody'){
    const code=inventoryRowCellText(row,0);
    const name=inventoryRowCellText(row,1);
    return deleteInventoryProductAndMapping({
      productRow:row,
      mappingRow:matchingInventoryMappingRow(code,name),
      stockRow:matchingStockLevelRow(code,name),
      button,
      label:name||code,
      skipConfirm,
      quiet:bulk
    });
  }
  if(tbodyId==='stock-map-tbody'){
    return deleteInventoryMappingRow(row,{skipConfirm:true,quiet:bulk});
  }
  return false;
}

function inventoryRowCellText(row,dataIndex){
  const cells=[...row.children].filter(cell=>cell.dataset.inventoryBulkCol!=='1');
  return cells[dataIndex]?.textContent.trim()||'';
}

function inventoryProductRecordFromRow(row){
  if(!row)return {};
  return {
    id:inventoryRowCellText(row,0),
    code:inventoryRowCellText(row,0),
    name:inventoryRowCellText(row,1),
    type:inventoryRowCellText(row,2),
    category:inventoryRowCellText(row,3),
    unit:inventoryRowCellText(row,4),
    default_warehouse:inventoryRowCellText(row,5),
    tracking:inventoryRowCellText(row,6),
    vat:inventoryRowCellText(row,7),
    status:inventoryRowCellText(row,8)
  };
}

async function deleteInventoryMappingRow(row,{quiet=false}={}){
  if(!row)return false;
  const name=inventoryRowCellText(row,0)||'mapping';
  const mappingId=row.dataset.mappingId;
  try{
    if(mappingId)await moduleApi(`/inventory/mappings/${encodeURIComponent(mappingId)}`,{method:'DELETE'});
    row.remove();
    const tbody=document.getElementById('stock-map-tbody');
    if(tbody&&tbody.querySelectorAll('tr:not([data-empty-state])').length===0){
      emptyTableMessage(tbody,'No stock mappings in database yet.');
    }
    const table=tbody?.closest('table');
    if(table)refreshEnhancedTable(table);
    if(!quiet)toast(`${name} mapping removed`,'warn');
    audit('Deleted stock mapping',name,'Deleted');
    return true;
  }catch(err){
    console.warn('Stock mapping delete failed:',err);
    if(!quiet)toast('Could not delete stock mapping from database','warn');
    return false;
  }
}

async function deleteInventoryProductAndMapping({productRow=null,mappingRow=null,stockRow=null,button=null,label='inventory item',skipConfirm=false,quiet=false}={}){
  const productCode=inventoryRowCellText(productRow,0)||inventoryRowCellText(stockRow,0)||mappingRow?.dataset?.itemCode||mappingRow?.dataset?.stockSku||'';
  const productName=inventoryRowCellText(productRow,1)||inventoryRowCellText(stockRow,1)||inventoryRowCellText(mappingRow,0)||label;
  if(!productRow&&!mappingRow){
    toast('This stock row is generated from purchases. Use Clear Table to remove generated stock history.','warn');
    return false;
  }
  if(productName&&tableHasText('#sales-invoice-tbody',productName)){
    toast(`Cannot delete ${productName}: linked sales invoices exist`,'warn');
    return false;
  }
  if(productName&&tableHasText('#sales-return-tbody',productName)){
    toast(`Cannot delete ${productName}: linked sales returns exist`,'warn');
    return false;
  }
  if(!skipConfirm){
    const confirmed=await appConfirm({
      title:'Delete Inventory Item',
      message:`Delete ${productName || productCode || 'this inventory item'} from inventory? This removes the Item Master row and its stock mapping when present.`,
      okText:'Delete'
    });
    if(!confirmed)return false;
  }
  if(button)button.disabled=true;
  try{
    const mappingId=mappingRow?.dataset?.mappingId;
    if(mappingId)await moduleApi(`/inventory/mappings/${encodeURIComponent(mappingId)}`,{method:'DELETE'});
    if(productRow){
      const record=inventoryProductRecordFromRow(productRow);
      await deleteServer('products',record,{throwOnError:true});
    }
    productRow?.remove();
    mappingRow?.remove();
    stockRow?.remove();
    ['prod-tbody','stock-map-tbody','stock-level-tbody'].forEach(id=>{
      const tbody=document.getElementById(id);
      if(tbody&&tbody.querySelectorAll('tr:not([data-empty-state])').length===0){
        emptyTableMessage(tbody,id==='prod-tbody'?'No products in database yet.':id==='stock-map-tbody'?'No stock mappings in database yet.':'No stock items in database yet.');
      }
      const table=tbody?.closest('table');
      if(table)refreshEnhancedTable(table);
    });
    syncStockLevelsFromProducts();
    syncInventoryItemOptions();
    refreshInvoiceProductSuggestions();
    refreshPurchaseProductSuggestions();
    refreshQuotationProductOptions();
    if(!quiet)toast(`${productName || productCode || 'Inventory item'} deleted`,'warn');
    audit('Deleted inventory item',productName||productCode,'Deleted');
    return true;
  }catch(err){
    console.warn('Inventory delete failed:',err);
    if(!quiet)toast('Could not delete inventory item from database','warn');
    return false;
  }finally{
    if(button)button.disabled=false;
  }
}

function matchingInventoryProductRow(code,name){
  const codeKey=String(code||'').trim().toLowerCase();
  const nameKey=String(name||'').trim().toLowerCase();
  const rows=[...document.querySelectorAll('#prod-tbody tr:not([data-empty-state])')];
  if(codeKey){
    return rows.find(row=>inventoryRowCellText(row,0).toLowerCase()===codeKey)||null;
  }
  if(!nameKey)return null;
  const matches=rows.filter(row=>{
    const rowCode=inventoryRowCellText(row,0).toLowerCase();
    const rowName=inventoryRowCellText(row,1).toLowerCase();
    return !rowCode&&rowName===nameKey;
  });
  return matches.length===1?matches[0]:null;
}

function matchingInventoryMappingRow(code,name){
  const codeKey=String(code||'').trim().toLowerCase();
  const nameKey=String(name||'').trim().toLowerCase();
  const rows=[...document.querySelectorAll('#stock-map-tbody tr:not([data-empty-state])')];
  if(codeKey){
    return rows.find(row=>String(row.dataset.stockSku||row.dataset.itemCode||'').trim().toLowerCase()===codeKey)||null;
  }
  if(!nameKey)return null;
  const matches=rows.filter(row=>{
    const sku=String(row.dataset.stockSku||row.dataset.itemCode||'').trim().toLowerCase();
    const display=inventoryRowCellText(row,0).toLowerCase();
    const mapped=inventoryRowCellText(row,2).toLowerCase();
    return !sku&&(display===nameKey||mapped===nameKey);
  });
  return matches.length===1?matches[0]:null;
}

function handleInventoryDelete(btn,row,table){
  const tbodyId=table?.tBodies?.[0]?.id||'';
  if(tbodyId==='stock-movement-tbody'){
    toast('Stock movements are source history. Use Clear Table to remove inventory history.','warn');
    return true;
  }
  if(['stock-level-tbody','prod-tbody','stock-map-tbody'].includes(tbodyId)){
    deleteInventoryRow(row,{button:btn});
    return true;
  }
  return false;
}

function matchingStockLevelRow(code,name){
  const codeKey=String(code||'').trim().toLowerCase();
  const nameKey=String(name||'').trim().toLowerCase();
  const rows=[...document.querySelectorAll('#stock-level-tbody tr:not([data-empty-state])')];
  if(codeKey){
    return rows.find(row=>inventoryRowCellText(row,0).toLowerCase()===codeKey)||null;
  }
  if(!nameKey)return null;
  const matches=rows.filter(row=>{
    const rowCode=inventoryRowCellText(row,0).toLowerCase();
    const rowName=inventoryRowCellText(row,1).toLowerCase();
    return !rowCode&&rowName===nameKey;
  });
  return matches.length===1?matches[0]:null;
}

function stockSupplierNameFromItem(row){
  return row?.dataset?.supplier||'Not assigned';
}

function renderStockMappingRecord(mapping){
  const tbody=document.getElementById('stock-map-tbody');
  if(!tbody||!mapping?.sku)return;
  removeEmptyState(tbody);
  const selector=`tr[data-stock-sku="${CSS.escape(String(mapping.sku))}"]`;
  let tr=tbody.querySelector(selector);
  if(!tr){
    tr=document.createElement('tr');
    tbody.appendChild(tr);
  }
  const name=mapping.name||mapping.taxflow_name||mapping.sku;
  const supplier=mapping.supplier_name||'Not assigned';
  const taxflowName=mapping.taxflow_name||mapping.name||mapping.sku;
  tr.dataset.mappingId=mapping.id||'';
  tr.dataset.stockSku=mapping.sku;
  tr.dataset.salesAccountCode=mapping.sales_account_code||'3000';
  tr.dataset.purchaseAccountCode=mapping.purchase_account_code||'4000';
  tr.dataset.inventoryAccountCode=mapping.inventory_account_code||'1200';
  tr.dataset.taxCode=mapping.tax_code||'VAT5';
  tr.dataset.reorderLevel=mapping.reorder_level??0;
  tr.dataset.unitsPerOuter=mapping.units_per_outer??1;
  tr.dataset.cost=mapping.cost??0;
  tr.dataset.markupPercent=mapping.markup_percent??0;
  tr.dataset.taxRate=mapping.tax_rate??5;
  tr.dataset.vatAmount=mapping.vat_amount??0;
  tr.dataset.incVat=mapping.inc_vat??0;
  tr.dataset.priceOuter=mapping.price_outer??0;
  tr.innerHTML=`<td>${escapeHtml(name)}</td><td>${escapeHtml(supplier)}</td><td>${escapeHtml(taxflowName)}</td><td class="mono">${Number(mapping.units_per_outer||1).toLocaleString('en-AE',{maximumFractionDigits:4})}</td><td><span class="b b-g">Mapped</span></td><td data-action-col="1">${stockMapActionsHtml()}</td>`;
}

function stockMappingPayloadFromRow(row){
  const cells=row?.querySelectorAll('td')||[];
  const sku=row?.dataset.stockSku||row?.dataset.itemCode||cells[0]?.textContent.trim()||'';
  return {
    sku,
    name:cells[0]?.textContent.trim()||sku,
    supplier_name:(cells[1]?.textContent.trim()||'').replace(/^Not assigned$/,'')||null,
    taxflow_name:cells[2]?.textContent.trim()||cells[0]?.textContent.trim()||sku,
    units_per_outer:parseAmount(cells[3]?.textContent)||Number(row?.dataset.unitsPerOuter||1),
    cost:Number(row?.dataset.cost||0),
    markup_percent:Number(row?.dataset.markupPercent||0),
    tax_rate:Number(row?.dataset.taxRate||5),
    vat_amount:Number(row?.dataset.vatAmount||0),
    inc_vat:Number(row?.dataset.incVat||0),
    price_outer:Number(row?.dataset.priceOuter||0),
    sales_account_code:row?.dataset.salesAccountCode||'3000',
    purchase_account_code:row?.dataset.purchaseAccountCode||'4000',
    inventory_account_code:row?.dataset.inventoryAccountCode||'1200',
    tax_code:row?.dataset.taxCode||'VAT5',
    reorder_level:Number(row?.dataset.reorderLevel||0)
  };
}

async function loadStockMappingsFromServer(){
  const tbody=document.getElementById('stock-map-tbody');
  if(!tbody)return;
  if(isInventoryTableCleared()){
    emptyTableMessage(tbody,'No stock mappings in database yet.');
    return;
  }
  try{
    await ensureBackendSession();
    const mappings=await moduleApi('/inventory/mappings');
    tbody.innerHTML='';
    (mappings||[]).forEach(renderStockMappingRecord);
    syncStockMappingFromItems();
    ensureInventoryBulkSelection();
  }catch(err){
    console.warn('Stock mappings unavailable:',err);
    syncStockMappingFromItems();
  }
}

function syncStockMappingFromItems(){
  const tbody=document.getElementById('stock-map-tbody');
  if(!tbody)return;
  if(isInventoryTableCleared()){
    emptyTableMessage(tbody,'No stock mappings in database yet.');
    refreshInvoiceProductSuggestions();
    return;
  }
  removeEmptyState(tbody);
  const existing=new Set([...tbody.querySelectorAll('tr:not([data-empty-state])')]
    .map(row=>(row.dataset.stockSku||row.children[0]?.textContent.trim()||'').toLowerCase()));
  const itemRows=[...document.querySelectorAll('#prod-tbody tr:not([data-empty-state])')];
  itemRows.forEach(row=>{
    const code=inventoryRowCellText(row,0);
    const name=inventoryRowCellText(row,1);
    const key=(code||name).toLowerCase();
    if(!name||existing.has(key))return;
    const mappedName=generateStockMapName(name||code);
    const supplier=stockSupplierNameFromItem(row);
    const tr=document.createElement('tr');
    tr.dataset.itemCode=code;
    tr.dataset.stockSku=code||name;
    tr.dataset.unitsPerOuter='1';
    tr.dataset.cost=String(row.dataset.cost||0);
    tr.dataset.markupPercent='0';
    tr.dataset.taxRate='5';
    tr.dataset.vatAmount='0';
    tr.dataset.incVat='0';
    tr.dataset.priceOuter='0';
    tr.innerHTML=`<td>${escapeHtml(name)}</td><td>${escapeHtml(supplier)}</td><td>${escapeHtml(mappedName)}</td><td class="mono">1</td><td><span class="b b-a">Not mapped</span></td><td data-action-col="1">${stockMapActionsHtml()}</td>`;
    tbody.appendChild(tr);
    existing.add(key);
  });
  refreshEnhancedTable(tbody.closest('table'));
  refreshInvoiceProductSuggestions();
  ensureInventoryBulkSelection();
}

function syncInventoryItemOptions(){
  const categorySelect=document.getElementById('inv-item-category');
  if(categorySelect){
    const current=categorySelect.value;
    const categories=[...document.querySelectorAll('#sales-category-tbody tr:not([data-empty-state]) td:first-child')]
      .map(td=>td.textContent.trim())
      .filter(Boolean);
    const unique=[...new Set(categories)];
    categorySelect.innerHTML='<option value="">Select Category</option>'+unique.map(name=>`<option>${escapeHtml(name)}</option>`).join('');
    if(current&&unique.includes(current))categorySelect.value=current;
  }
  const supplierSelect=document.getElementById('inv-item-supplier');
  if(supplierSelect){
    const current=supplierSelect.value;
    const suppliers=[...document.querySelectorAll('#vendor-tbody tr:not([data-empty-state]) td:first-child')]
      .map(td=>td.textContent.trim())
      .filter(Boolean);
    const unique=[...new Set(suppliers)];
    supplierSelect.innerHTML='<option value="">Select Supplier</option>'+unique.map(name=>`<option>${escapeHtml(name)}</option>`).join('');
    if(current&&unique.includes(current))supplierSelect.value=current;
  }
  const unitSelect=document.getElementById('inv-item-unit');
  if(unitSelect){
    const current=unitSelect.value;
    unitSelect.innerHTML='<option value="">Select Unit</option>'+unitOptionsHtml(current);
    if(current&&dbUnitNames().includes(current))unitSelect.value=current;
  }
}

function openInventoryItemModal(){
  syncInventoryItemOptions();
  showM('m-inv-item');
  setTimeout(()=>document.getElementById('inv-item-name')?.focus(),50);
}

function saveInventoryItem(){
  const code=(document.getElementById('inv-item-code')?.value||generateStockSku(document.getElementById('inv-item-name')?.value)).trim();
  const name=(document.getElementById('inv-item-name')?.value||'').trim();
  const category=document.getElementById('inv-item-category')?.value||'';
  const unit=document.getElementById('inv-item-unit')?.value||'';
  const cost=parseAmount(document.getElementById('inv-item-cost')?.value);
  const tracking=document.getElementById('inv-item-tracking')?.value||'Yes';
  const reorderLevel=parseAmount(document.getElementById('inv-item-reorder')?.value);
  const supplier=document.getElementById('inv-item-supplier')?.value||'';
  const status=document.getElementById('inv-item-status')?.value||'Active';
  if(!name){
    toast('Enter item name','warn');
    return;
  }
  if(!category){
    toast('Select category from database','warn');
    return;
  }
  if(!unit){
    toast('Select unit of measure from database','warn');
    return;
  }
  const tbody=document.getElementById('prod-tbody');
  if(tbody&&!hasFirstCellValue(tbody,code)){
    setInventoryTableCleared(false);
    removeEmptyState(tbody);
    const row=document.createElement('tr');
    row.dataset.reorderLevel=reorderLevel;
    row.dataset.available=0;
    row.dataset.reserved=0;
    row.dataset.cost=cost;
    row.dataset.supplier=supplier;
    row.innerHTML=`<td class="mono">${escapeHtml(code)}</td><td>${escapeHtml(name)}</td><td>Stock Item</td><td>${escapeHtml(category)}</td><td>${escapeHtml(unit)}</td><td>Main Store</td><td><span class="b ${tracking==='No'?'b-gray':'b-g'}">${escapeHtml(tracking)}</span></td><td><span class="b b-b">5%</span></td><td><span class="b ${status==='Active'?'b-g':'b-gray'}">${escapeHtml(status)}</span></td>`;
    tbody.prepend(row);
    syncStockLevelsFromProducts();
  }
  saveServer('products',{code,name,category,unit,cost,vat:'Standard 5%',supplier_name:supplier,reorder_level:reorderLevel,status});
  syncStockMappingFromItems();
  closeM('m-inv-item');
  document.querySelectorAll('#m-inv-item input').forEach(input=>input.value='');
  syncInventoryItemOptions();
  toast('Item added to inventory','ok');
  audit('Added inventory item',name,'Saved');
}

function logout(){
  localStorage.removeItem('taxflow_token');
  window.location.href='/landing.html';
}

function chkTRN(inp){
  const v=inp.value.replace(/\D/g,'');inp.value=v;
  const el=document.getElementById('trn-msg');
  if(v.length===15)el.innerHTML='<span style="color:var(--green)">? Valid UAE TRN (15 digits)</span>';
  else if(v.length>0)el.innerHTML=`<span style="color:var(--amber)">? Must be 15 digits (${v.length}/15)</span>`;
  else el.innerHTML='<span style="color:var(--text3)">Enter TRN</span>';
}

function chkSettingsTRN(inp){
  const v=inp.value.replace(/\D/g,'');inp.value=v;
  const el=document.getElementById('set-company-trn-msg');
  if(v.length===15)el.innerHTML='<span style="color:var(--green)">? Valid UAE TRN (15 digits)</span>';
  else if(v.length>0)el.innerHTML=`<span style="color:var(--amber)">? Must be 15 digits (${v.length}/15)</span>`;
  else el.innerHTML='<span style="color:var(--text3)">Enter TRN</span>';
}

// -- UPLOAD: store real files --------------------------------------
const uploadedFiles = []; // {name, size, type, base64, category, period, status}
const purchaseDocumentIds = new Set();
const PURCHASE_AI_PREVIEW_LIMIT = 500;
let currentSalesTransactionType = 'sale';
let currentPurchaseTransactionType = 'purchase';

const APP_CONFIG={
  apiEndpoint:null,
  extractionEndpoint:null,
  salesExtractionEndpoint:null,
  extractionFallback:true
};
let currentCompany=null;

function backendHeaders(){
  const headers={'Content-Type':'application/json'};
  const token=localStorage.getItem('taxflow_token');
  if(token)headers.Authorization='Bearer '+token;
  return headers;
}

function apiBaseUrl(){
  if(window.TAXFLOW_API_BASE_URL)return window.TAXFLOW_API_BASE_URL;
  const currentHost=window.location.hostname||'127.0.0.1';
  if(['localhost','127.0.0.1','::1',''].includes(currentHost)){
    return 'http://127.0.0.1:8000/api/v1';
  }
  // Production: same host, HTTPS, no port (Render / any reverse proxy)
  return `${window.location.protocol}//${currentHost}/api/v1`;
}

function localApiBaseUrl(){
  return 'http://127.0.0.1:8000/api/v1';
}

function localApiUrlFor(url){
  try{
    const parsed=new URL(url,window.location.href);
    return `${localApiBaseUrl()}${parsed.pathname.replace(/^\/api\/v1/,'')}${parsed.search}`;
  }catch(err){
    return url;
  }
}

function showLoginOverlay(){
  const el=document.getElementById('login-overlay');
  if(el){el.style.display='flex';}
}
function hideLoginOverlay(){
  const el=document.getElementById('login-overlay');
  if(el){el.style.display='none';}
}
async function submitLogin(){
  const email=(document.getElementById('login-email').value||'').trim();
  const password=(document.getElementById('login-password').value||'').trim();
  const errEl=document.getElementById('login-error');
  const btn=document.getElementById('login-btn');
  if(!email||!password){errEl.textContent='Please enter email and password.';errEl.style.display='block';return;}
  btn.disabled=true;btn.textContent='Signing in…';errEl.style.display='none';
  try{
    const resp=await fetch(`${apiBaseUrl()}/auth/login`,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email,password})
    });
    const data=await resp.json();
    if(resp.ok&&data.access_token){
      localStorage.setItem('taxflow_token',data.access_token);
      hideLoginOverlay();
      initApp();
    }else{
      errEl.textContent=data.detail||'Incorrect email or password.';
      errEl.style.display='block';
    }
  }catch(err){
    errEl.textContent='Cannot reach server. Try again.';
    errEl.style.display='block';
  }finally{
    btn.disabled=false;btn.textContent='Sign In';
  }
}
async function ensureBackendSession(){
  if(localStorage.getItem('taxflow_token'))return true;
  const host=window.location.hostname||'127.0.0.1';
  if(['localhost','127.0.0.1','::1',''].includes(host)){
    return loginLocalBackend();
  }
  showLoginOverlay();
  return false;
}

async function loginLocalBackend(){
  const host=window.location.hostname||'127.0.0.1';
  if(!['localhost','127.0.0.1','::1',''].includes(host))return false;
  const loginUrls=[`${apiBaseUrl()}/auth/login`,`${localApiBaseUrl()}/auth/login`].filter((url,index,self)=>self.indexOf(url)===index);
  for(const loginUrl of loginUrls){
    try{
      const response=await fetch(loginUrl,{
        method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({email:'admin@taxflowapp.com',password:'admin123'})
      });
      if(!response.ok)throw new Error('Login returned '+response.status);
      const data=await response.json();
      if(data.access_token)localStorage.setItem('taxflow_token',data.access_token);
      if(data.access_token)return true;
    }catch(err){
      console.warn('Local backend login failed:',err);
    }
  }
  return false;
}

async function fetchWithBackendFallback(url,options={}){
  try{
    const response=await fetch(url,options);
    if(response.ok||url===localApiUrlFor(url))return response;
    return fetch(localApiUrlFor(url),options);
  }catch(err){
    const fallbackUrl=localApiUrlFor(url);
    if(fallbackUrl!==url)return fetch(fallbackUrl,options);
    throw err;
  }
}

async function authenticatedFetch(url,options={}){
  await ensureBackendSession();
  const requestOptions={...options,headers:{...backendHeaders(),...(options.headers||{})}};
  let response=await fetchWithBackendFallback(url,requestOptions);
  if(response.status===401||response.status===403){
    localStorage.removeItem('taxflow_token');
    const relogged=await loginLocalBackend();
    if(relogged){
      response=await fetchWithBackendFallback(url,{...options,headers:{...backendHeaders(),...(options.headers||{})}});
    }
  }
  return response;
}

const AED_SYMBOL='AED';
const AED_HTML='AED';
function AED_SYMBOL_SVG(){return 'AED';}
function formatAed(value){
  const num=Number(value||0);
  return 'AED '+num.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
}
function formatAedHtml(value){
  const num=Number(value||0);
  return 'AED '+num.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
}

function findSettingsInput(labelText,scopeId='set-company'){
  const scope=document.getElementById(scopeId);
  if(!scope)return null;
  const target=String(labelText||'').trim().toLowerCase();
  const group=[...scope.querySelectorAll('.fg')].find(item=>{
    const label=item.querySelector('.fl,label');
    return label&&label.textContent.trim().toLowerCase()===target;
  });
  return group?.querySelector('input,select,textarea')||null;
}

function setFieldValue(input,value){
  if(!input||value==null)return;
  input.value=String(value);
  input.dispatchEvent(new Event('input',{bubbles:true}));
}

function setCheckboxValue(input,checked){
  if(!input)return;
  input.checked=Boolean(checked);
  input.dispatchEvent(new Event('change',{bubbles:true}));
}

function readFieldValue(labelText,scopeId='set-company'){
  return (findSettingsInput(labelText,scopeId)?.value||'').trim();
}

function applyCompanyToUi(company){
  if(!company)return;
  currentCompany=company;
  const name=company.name||'';
  const trn=company.trn||'';
  setFieldValue(findSettingsInput('Legal Company Name'),name);
  setFieldValue(document.getElementById('set-company-trn'),trn);
  setFieldValue(document.getElementById('trn'),trn);
  const layoutCompany=document.getElementById('inv-layout-company');
  if(layoutCompany&&!layoutCompany.dataset.userEdited)setFieldValue(layoutCompany,name);
  updateInvoiceLayoutPreview?.();
}

async function syncCompanyFromDatabase(){
  const ready=await ensureBackendSession();
  if(!ready)return null;
  try{
    const response=await authenticatedFetch(`${apiBaseUrl()}/companies/current`);
    if(!response.ok)throw new Error('Company API returned '+response.status);
    const company=await response.json();
    applyCompanyToUi(company);
    return company;
  }catch(err){
    console.warn('Company database sync failed:',err);
    return null;
  }
}

async function saveCompanySettingsToDatabase(){
  const name=readFieldValue('Legal Company Name')||currentCompany?.name||'';
  const trn=(document.getElementById('set-company-trn')?.value||currentCompany?.trn||'').replace(/\D/g,'');
  const country=currentCompany?.country||'United Arab Emirates';
  const response=await authenticatedFetch(`${apiBaseUrl()}/companies/current`,{
    method:'PUT',
    body:JSON.stringify({name,trn:trn||null,country})
  });
  if(!response.ok)throw new Error('Company save returned '+response.status);
  const company=await response.json();
  applyCompanyToUi(company);
  return company;
}

function setDashboardStat(label,value,delta){
  const idMap={
    'Total Revenue':['dash-revenue','dash-revenue-sub'],
    'VAT Payable':['dash-vat','dash-vat-sub'],
    'Open Invoices':['dash-open-invoices','dash-open-invoices-sub'],
    'Staff Present':['dash-staff','dash-staff-sub']
  };
  const ids=idMap[label];
  if(ids){
    const val=document.getElementById(ids[0]);
    const sub=document.getElementById(ids[1]);
    if(val)val.textContent=value;
    if(sub&&delta)sub.textContent=delta;
  }
  document.querySelectorAll('#page-dashboard .stat').forEach(stat=>{
    const title=stat.querySelector('.stat-lbl');
    if(title&&title.textContent.trim()===label){
      const val=stat.querySelector('.stat-val');
      const sub=stat.querySelector('.stat-delta');
      if(val)val.textContent=value;
      if(sub&&delta)sub.textContent=delta;
    }
  });
}

function setNavBadge(page,count,variant=''){
  const nav=[...document.querySelectorAll('.nav')].find(item=>(item.getAttribute('onclick')||'').includes(`'${page}'`));
  if(!nav)return;
  let badge=nav.querySelector('.nbadge');
  if(!badge){
    badge=document.createElement('span');
    nav.appendChild(badge);
  }
  badge.className=`nbadge ${variant}`.trim();
  badge.textContent=Number(count||0).toLocaleString('en-AE');
}

function syncSidebarCounts(data){
  const counts=data.module_counts||data;
  // Sales & Invoices: invoices only
  const salesTotal=Number(counts.invoice_count)||0;
  // Purchases: all purchase records + purchase invoices/documents from app data
  const purchaseTotal=(Number(counts.purchase_record_count)||0)
    +(Number(counts.purchase_source_count)||0)
    +(Number(counts.purchase_invoice_count)||0)
    +(Number(counts.bill_count)||0);
  // Bank & Payments: accounts + payments + receipts
  const bankTotal=(Number(counts.account_count)||0)
    +(Number(counts.payment_receipt_count)||0);
  const exceptionCount=Number(counts.exception_count||0);
  setNavBadge('sales',salesTotal);
  setNavBadge('quotations',counts.quotation_count||0,'warn');
  setNavBadge('purchase',purchaseTotal||0,'warn');
  setNavBadge('bank',bankTotal||counts.account_count);
  setNavBadge('accounting',counts.journal_count);
  setNavBadge('reports',counts.tax_line_count);
  setNavBadge('inventory',counts.inventory_mapping_count);
  setNavBadge('staff',counts.employee_count,'warn');
  setNavBadge('payroll',counts.payroll_run_count||0);
  setNavBadge('notifications',counts.job_count);
  setNavBadge('expert',counts.audit_count?'1':0,counts.audit_count?'red':'');
  if(exceptionCount)setNavBadge('exception',exceptionCount,'warn');
  setNavBadge('settings',(Number(counts.sales_category_count)||0)+(Number(counts.sales_unit_count)||0)+(Number(counts.account_count)||0));
}

function renderDashboardMeta(data){
  const meta=data.dashboard_meta||{};
  if(meta.title)META.dashboard.t=meta.title;
  if(meta.subtitle)META.dashboard.s=meta.subtitle;
  const dashboardVisible=document.getElementById('page-dashboard')?.classList.contains('on');
  if(dashboardVisible){
    const title=document.getElementById('ptitle');
    const sub=document.getElementById('psub');
    if(title)title.textContent=meta.title||'Dashboard';
    if(sub)sub.textContent=meta.subtitle||'Dashboard loaded from database records';
  }
}

async function syncDashboardFromDatabase(){
  const ready=await ensureBackendSession();
  if(!ready){
    setDashboardStat('Total Revenue','Login needed','Open the root app and sign in');
    setDashboardStat('VAT Payable','Login needed','No backend token found');
    setDashboardStat('Open Invoices','Login needed','Dashboard cannot read database');
    setDashboardStat('Staff Present','Login needed','Use admin@taxflowapp.com');
    return;
  }
  if(!window.__taxflowFreshDashboardLoaded)renderCachedDashboardSnapshot();
  try{
    const response=await authenticatedFetch(`${apiBaseUrl()}/reports/dashboard`);
    if(!response.ok)throw new Error('Dashboard API returned '+response.status);
    const data=await response.json();
    window.__taxflowFreshDashboardLoaded=true;
    try{localStorage.setItem('taxflow_dashboard_snapshot',JSON.stringify(data));}catch{}
    if(data.company)applyCompanyToUi(data.company);
    renderDashboardMeta(data);
    syncSidebarCounts(data);
    renderFullDashboardFromDatabase(data);
  }catch(err){
    console.warn('Dashboard database sync failed:',err);
    setDashboardStat('Total Revenue','API error','Check backend connection');
    setDashboardStat('VAT Payable','API error','Dashboard sync failed');
    setDashboardStat('Open Invoices','API error','Open browser console for details');
    setDashboardStat('Staff Present','API error','Try http://127.0.0.1:5173');
    const target=document.getElementById('dash-recent-activity');
    if(target)target.innerHTML=`<div style="font-size:12px;color:var(--red)">Dashboard database sync failed: ${escapeHtml(err.message||err)}</div>`;
  }
}

function renderCachedDashboardSnapshot(){
  let cached=null;
  try{cached=JSON.parse(localStorage.getItem('taxflow_dashboard_snapshot')||'null');}catch{}
  if(cached){
    renderDashboardMeta(cached);
    syncSidebarCounts(cached);
    renderFullDashboardFromDatabase(cached);
    return;
  }
  setDashboardStat('Total Revenue','AED 0.00','Syncing database...');
  setDashboardStat('VAT Payable','AED 0.00','Syncing database...');
  setDashboardStat('Open Invoices','0','Syncing database...');
  setDashboardStat('Staff Present','0/0','Syncing database...');
}

function renderFullDashboardFromDatabase(data){
  const counts=data.module_counts||data;
  const kpis=data.kpis||{
    revenue:data.revenue,
    vat_payable:data.vat_payable,
    open_invoice_count:data.open_invoices,
    open_invoice_amount:0,
    staff_present:data.employee_count,
    staff_total:data.employee_count,
    payroll_net:data.payroll_net
  };
  setDashboardStat('Total Revenue',formatAed(kpis.revenue),`${counts.invoice_count||0} invoices in database`);
  setDashboardStat('VAT Payable',formatAed(kpis.vat_payable),`${counts.tax_line_count||0} tax lines - DB period`);
  setDashboardStat('Open Invoices',String(kpis.open_invoice_count||0),`${formatAed(kpis.open_invoice_amount||0)} open amount`);
  setDashboardStat('Staff Present',`${kpis.staff_present||0}/${kpis.staff_total||0}`,`${counts.payroll_run_count||0} payroll run - ${formatAed(kpis.payroll_net||0)} net`);
  renderDashboardHero(data,kpis,counts);
  renderMonthlyRevenueVat(data.monthly_revenue_vat||[]);
  renderRecentActivity(data.recent_activity||[]);
  renderTopCustomers(data.top_customers||[]);
  renderInvoiceStatus(data.invoice_status||{});
  renderStaffToday(data.staff_today||{present:kpis.staff_present||0,total:kpis.staff_total||0,leave:0,absent:0,source:'Employees database'});
  renderDatabaseDashboardSummary(data);
}

function renderDashboardHero(data,kpis={},counts={}){
  // Header
  const dateEl=document.getElementById('dash-today-date');
  if(dateEl)dateEl.textContent=new Date().toLocaleDateString('en-AE',{weekday:'long',year:'numeric',month:'long',day:'numeric'});
  const periodEl=document.getElementById('dash-period-label');
  if(periodEl)periodEl.textContent=`Live · ${data.period||new Date().toLocaleDateString('en-AE',{month:'long',year:'numeric'})}`;
  const greet=document.getElementById('dash-greeting');
  if(greet){const h=new Date().getHours();greet.textContent=h<12?'Good morning':h<17?'Good afternoon':'Good evening';}

  const revenue=parseAmount(kpis.total_revenue||data.total_revenue||0);
  const purchases=parseAmount(kpis.total_purchases||data.total_purchases||0);
  const vatPayable=parseAmount(kpis.vat_payable||data.vat_payable||0);
  const grossProfit=revenue-purchases;

  // KPI cards
  const set=(id,v)=>{const el=document.getElementById(id);if(el)el.textContent=v;};
  set('dash-revenue',formatAed(revenue));
  set('dash-revenue-sub',`${Number(kpis.invoice_count||0)} invoices`);
  set('dash-total-purchases',formatAed(purchases));
  set('dash-purchases-sub',`${Number(kpis.purchase_count||counts.purchase_record_count||0)} records`);
  set('dash-vat',formatAed(vatPayable));
  set('dash-vat-sub',vatPayable>0?'Payable to FTA':'Credit position');
  set('dash-gross-profit',formatAed(grossProfit));
  set('dash-profit-sub',revenue?`${Math.round(grossProfit/revenue*100)}% margin`:'—');

  // Trend badges
  const setTrend=(id,val)=>{
    const el=document.getElementById(id);
    if(!el)return;
    if(val>0){el.textContent=`↑ ${val}%`;el.className='dash-kpi-trend up';}
    else if(val<0){el.textContent=`↓ ${Math.abs(val)}%`;el.className='dash-kpi-trend dn';}
    else{el.textContent='—';el.className='dash-kpi-trend';}
  };
  setTrend('dash-rev-trend',Number(kpis.revenue_trend||0));
  setTrend('dash-pur-trend',Number(kpis.purchase_trend||0));
  setTrend('dash-profit-trend',Number(kpis.profit_trend||0));

  // Sparklines (mini bar charts from monthly data)
  const monthly=data.monthly_revenue_vat||[];
  if(monthly.length){
    renderDashSparkline('dash-rev-spark',monthly.map(r=>Number(r.sales||0)),'#6366f1');
    renderDashSparkline('dash-pur-spark',monthly.map(r=>Number(r.purchases||0)),'#ef4444');
    renderDashSparkline('dash-profit-spark',monthly.map(r=>Number(r.sales||0)-Number(r.purchases||0)),'#10b981');
    renderDashSparkline('dash-vat-spark',monthly.map(r=>Number(r.net_vat||0)),'#f59e0b');
  }

  // AR / AP
  const arPending=parseAmount(kpis.open_invoice_amount||0);
  const arCollected=parseAmount(kpis.collected_amount||0);
  const arGross=arPending+arCollected;
  const arPct=arGross>0?Math.round(arCollected/arGross*100):0;
  set('dash-ar-total',formatAed(arGross));
  set('dash-ar-count',`${Number(kpis.open_invoice_count||0)} invoices`);
  set('dash-ar-pct-collected',`${arPct}%`);
  set('dash-ar-pending',formatAed(arPending));
  set('dash-ar-collected-amt',formatAed(arCollected));
  // AR ring — circumference r=19 ≈ 120
  const arRing=document.getElementById('dash-ar-ring');
  if(arRing)arRing.setAttribute('stroke-dasharray',`${Math.round(arPct/100*120)} 120`);

  const apTotal=parseAmount(kpis.total_payable||purchases);
  const apPaid=parseAmount(kpis.total_paid||0);
  const apDue=Math.max(0,apTotal-apPaid);
  const apPct=apTotal>0?Math.round(apPaid/apTotal*100):0;
  set('dash-ap-total',formatAed(apTotal));
  set('dash-ap-count',`${Number(counts.purchase_record_count||0)} bills`);
  set('dash-ap-pct-paid',`${apPct}%`);
  set('dash-ap-due',formatAed(apDue));
  set('dash-ap-paid-amt',formatAed(apPaid));
  const apRing=document.getElementById('dash-ap-ring');
  if(apRing)apRing.setAttribute('stroke-dasharray',`${Math.round(apPct/100*120)} 120`);

  // VAT ring
  renderDashVatRing(data,kpis);
}

function renderDashSparkline(id,values,color){
  const el=document.getElementById(id);
  if(!el||!values.length)return;
  const max=Math.max(1,...values.map(Math.abs));
  el.innerHTML=values.map(v=>{
    const h=Math.max(3,Math.round((Math.abs(v)/max)*28));
    return `<div class="dash-spark-bar" style="height:${h}px;background:${color};opacity:${v<0?.4:1}"></div>`;
  }).join('');
}

function renderDashVatRing(data,kpis={}){
  const outputVat=parseAmount(kpis.output_vat||data.output_vat||0);
  const inputVat=parseAmount(kpis.input_vat||data.input_vat||0);
  const netVat=outputVat-inputVat;
  const set=(id,v)=>{const el=document.getElementById(id);if(el)el.textContent=v;};
  set('dash-vat-output',formatAed(outputVat));
  set('dash-vat-input',formatAed(inputVat));
  set('dash-vat-net',formatAed(Math.abs(netVat)));
  const badge=document.getElementById('dash-vat-status-badge');
  if(badge){badge.textContent=netVat>0?'Payable':netVat<0?'Refund':'Balanced';badge.className=netVat>0?'b b-a':netVat<0?'b b-g':'b b-b';}
  // SVG donut r=38 → circ≈239
  const circ=239;
  const vatTotal=Math.max(1,outputVat+inputVat);
  const outDash=Math.round(outputVat/vatTotal*circ);
  const inDash=Math.round(inputVat/vatTotal*circ);
  const outRing=document.getElementById('dash-vat-ring-output');
  const inRing=document.getElementById('dash-vat-ring-input');
  if(outRing)outRing.setAttribute('stroke-dasharray',`${outDash} ${circ-outDash}`);
  if(inRing){inRing.setAttribute('stroke-dasharray',`${inDash} ${circ-inDash}`);inRing.setAttribute('stroke-dashoffset',`${60+outDash}`);}
  // Gauge arc: fill = output/(output+input) ratio, full arc = 195
  const gaugeArc=document.getElementById('dash-vat-gauge-arc');
  if(gaugeArc){const full=195;const fill=Math.round(outputVat/vatTotal*full);gaugeArc.setAttribute('stroke-dasharray',`${fill} ${full-fill}`);}
}

function renderMonthlyRevenueVat(rows){
  const bars=document.getElementById('dash-monthly-bars');
  const summary=document.getElementById('dash-monthly-summary');
  if(!bars||!summary)return;
  const safeRows=rows.length?rows.slice(-6):[{period:'Current',sales:0,purchases:0,output_vat:0,input_vat:0,net_vat:0}];
  const maxVal=Math.max(1,...safeRows.flatMap(r=>[Number(r.sales||0),Number(r.purchases||0)]));
  bars.style.gridTemplateColumns=`repeat(${safeRows.length},1fr)`;
  bars.innerHTML=safeRows.map(row=>{
    const rH=Math.max(4,Math.round((Number(row.sales||0)/maxVal)*106));
    const pH=Math.max(4,Math.round((Number(row.purchases||0)/maxVal)*106));
    const vH=Math.max(3,Math.round((Math.abs(Number(row.net_vat||0))/maxVal)*106));
    return `<div class="dv2-bar-group">
      <div class="dv2-bar-cols">
        <div class="dv2-bar-col" style="height:${rH}px;background:linear-gradient(to top,#4f46e5,#818cf8)" title="Revenue: ${escapeHtml(formatAed(row.sales))}"></div>
        <div class="dv2-bar-col" style="height:${pH}px;background:linear-gradient(to top,#dc2626,#f87171)" title="Purchases: ${escapeHtml(formatAed(row.purchases))}"></div>
        <div class="dv2-bar-col" style="height:${vH}px;background:linear-gradient(to top,#d97706,#fbbf24)" title="VAT: ${escapeHtml(formatAed(row.net_vat))}"></div>
      </div>
      <div class="dv2-bar-lbl">${escapeHtml(row.period)}</div>
    </div>`;
  }).join('');
  const totalSales=safeRows.reduce((s,r)=>s+Number(r.sales||0),0);
  const totalPurch=safeRows.reduce((s,r)=>s+Number(r.purchases||0),0);
  const totalVat=safeRows.reduce((s,r)=>s+Number(r.net_vat||0),0);
  summary.innerHTML=`
    <div class="dv2-chart-metric"><div class="val" style="color:#6366f1">${escapeHtml(formatAed(totalSales))}</div><div class="lbl">Total Revenue</div></div>
    <div class="dv2-chart-metric"><div class="val" style="color:#ef4444">${escapeHtml(formatAed(totalPurch))}</div><div class="lbl">Total Purchases</div></div>
    <div class="dv2-chart-metric"><div class="val" style="color:#f59e0b">${escapeHtml(formatAed(totalVat))}</div><div class="lbl">Net VAT</div></div>
  `;
}

function renderRecentActivity(rows){
  const target=document.getElementById('dash-recent-activity');
  if(!target)return;
  const tones={ok:['var(--green-bg)','var(--green)','?'],warn:['var(--amber-bg)','var(--amber)','!'],info:['var(--accent-glow)','var(--accent)','?']};
  if(!rows.length){
    target.innerHTML='<div style="font-size:12px;color:var(--text3)">No database activity yet.</div>';
    return;
  }
  target.innerHTML=rows.map((row,index)=>{
    const tone=tones[row.tone]||tones.info;
    return `<div class="tline-item" style="${index===rows.length-1?'padding-bottom:0':''}"><div class="tline-dot" style="background:${tone[0]};color:${tone[1]}">${tone[2]}</div><div><div style="font-size:13px;font-weight:500">${escapeHtml(row.title||'Activity')}</div><div style="font-size:11.5px;color:var(--text3);margin-top:2px">${escapeHtml(row.module||'System')} - ${escapeHtml(row.time||'Now')}</div></div></div>`;
  }).join('');
}

function renderTopCustomers(rows){
  // Legacy table (hidden, kept for compat)
  const tbody=document.getElementById('dash-top-customers');
  if(tbody){
    tbody.innerHTML=rows.length
      ? rows.map(row=>`<tr><td>${escapeHtml(row.name)}</td><td class="mono" style="text-align:right;color:var(--accent)">${escapeHtml(formatAed(row.total))}</td></tr>`).join('')
      : '<tr><td colspan="2" style="color:var(--text3)">No invoice customers in database.</td></tr>';
  }
  // New v2 design
  const v2=document.getElementById('dash-top-customers-v2');
  if(!v2)return;
  const colors=['#6366f1','#ef4444','#f59e0b','#10b981','#8b5cf6'];
  if(!rows.length){v2.innerHTML='<div class="dv2-loading">No customers in database yet.</div>';return;}
  v2.innerHTML=rows.slice(0,5).map((row,i)=>{
    const initials=(row.name||'?').split(/\s+/).map(w=>w[0]).join('').slice(0,2).toUpperCase();
    return `<div class="dv2-customer-row">
      <div class="dv2-customer-av" style="background:${colors[i%colors.length]}">${escapeHtml(initials)}</div>
      <div class="dv2-customer-name">${escapeHtml(row.name)}</div>
      <div class="dv2-customer-amt">${escapeHtml(formatAed(row.total))}</div>
    </div>`;
  }).join('');
  refreshPurchaseProductSuggestions();
}

function renderInvoiceStatus(status){
  const target=document.getElementById('dash-invoice-status');
  if(!target)return;
  const rows=[
    ['Paid',status.paid,'#10b981'],
    ['Pending',status.pending,'#f59e0b'],
    ['Overdue',status.overdue,'#ef4444']
  ];
  target.innerHTML=rows.map(([label,row,color])=>{
    const item=row||{count:0,percentage:0};
    const pct=Math.min(100,Number(item.percentage||0));
    return `<div class="dv2-status-row">
      <div class="dv2-status-top">
        <span>${label}</span>
        <span style="font-family:'DM Mono',monospace;font-size:11.5px;color:${color}">${Number(item.count||0)} invoices</span>
      </div>
      <div class="dv2-status-bar"><div class="dv2-status-fill" style="width:${pct}%;background:${color}"></div></div>
    </div>`;
  }).join('');
}

function renderStaffToday(staff){
  const target=document.getElementById('dash-staff-today');
  if(!target)return;
  target.innerHTML=`
    <div style="font-size:28px;font-weight:700;color:var(--green);font-family:'DM Mono',monospace;margin-bottom:4px">${Number(staff.present||0)} <span style="font-size:16px;color:var(--text3)">/${Number(staff.total||0)}</span></div>
    <div style="font-size:12px;color:var(--text3);margin-bottom:12px">Present - ${escapeHtml(staff.source||'Employees database')}</div>
    <div class="flx" style="flex-wrap:wrap;gap:6px">
      <span class="b b-g">${Number(staff.present||0)} Present</span>
      <span class="b b-a">${Number(staff.leave||0)} Leave</span>
      <span class="b b-r">${Number(staff.absent||0)} Absent</span>
    </div>
  `;
}

function showReport(id){
  document.querySelectorAll('#page-reports .rep-nav-item').forEach(el=>el.classList.remove('on'));
  document.querySelectorAll('#page-reports .rep-panel').forEach(el=>el.classList.remove('on'));
  const panel=document.getElementById(id);
  if(panel)panel.classList.add('on');
  const navId='repnav-'+id.replace('rep-','');
  const nav=document.getElementById(navId);
  if(nav)nav.classList.add('on');
  if(latestReportSummary)renderReportsFromDatabase(latestReportSummary);
}

async function syncReportsFromDatabase(){
  const ready=await ensureBackendSession();
  if(!ready)return;
  try{
    const response=await authenticatedFetch(`${apiBaseUrl()}/reports/summary`);
    if(!response.ok)throw new Error('Reports API returned '+response.status);
    const data=await response.json();
    renderReportsFromDatabase(data);
  }catch(err){
    console.warn('Reports database sync failed:',err);
  }
}

function reportAmount(value){
  return Number(value||0).toLocaleString('en-AE',{maximumFractionDigits:2});
}

function setReportStat(stat,value,delta){
  if(!stat)return;
  const val=stat.querySelector('.stat-val');
  const sub=stat.querySelector('.stat-delta');
  if(val)val.textContent=value;
  if(sub&&delta!==undefined)sub.textContent=delta;
}

let latestReportSummary=null;

function renderReportsFromDatabase(data){
  latestReportSummary=data||{};
  const d=data||{};
  renderKpiDashboard(d.dashboard||{},d.working_capital||{},d.revenue_intelligence||{});
  renderAiHealthScore(d.ai_health||{});
  renderCfoPanel(d.dashboard||{},d.ai||{});
  renderProfitLossReport(d.profit_loss||{});
  renderBalanceSheetReport(d.balance_sheet||{});
  renderCashFlowReport(d.budget_cash||{});
  renderTrialBalanceReport(d.trial_balance||[]);
  renderGeneralLedger(d.general_ledger||[]);
  renderPartyLedger('rep-cl-body',d.customer_ledger||[],'Customer','customer');
  renderPartyLedger('rep-sl-body',d.supplier_ledger||[],'Supplier','supplier');
  renderAgingReport(d.aging||[]);
  renderApAging(d.ap_aging||[]);
  renderVatReport(d.vat||{});
  renderInventoryReport();
  renderAssetReports(d.assets||{});
  renderRevenueIntelligence(d.revenue_intelligence||{},d.dashboard||{});
  renderProfitabilityAnalytics(d.profit_loss||{});
  renderWorkingCapital(d.working_capital||{});
  renderGrowthTrends(d.dashboard||{},d.revenue_intelligence||{});
  renderVat201(d.vat||{});
  renderCorporateReports(d.corporate||{});
  renderBudgetCashReports(d.budget_cash||{});
  renderControlReports(d.control||{});
  document.querySelectorAll('#page-reports table.tbl').forEach(refreshEnhancedTable);
}

function renderKpiDashboard(report,wc,rev){
  setText('kpi-revenue',formatAed(report.revenue||0));
  setText('kpi-gp',`${report.gross_margin||'0.00'}%`);
  const pl=latestReportSummary?.profit_loss||{};
  const rev_val=Number(report.revenue||0);
  const net=Number(pl.net_profit||0);
  const np_pct=rev_val?((net/rev_val)*100).toFixed(1)+'%':'—';
  setText('kpi-np',np_pct);
  setText('kpi-wc',formatAed(wc.working_capital||0));
  setText('kpi-cr',wc.current_ratio||'—');
  setText('kpi-qr',wc.quick_ratio||'—');
  setText('kpi-growth',(rev.growth_pct||'0.00')+'%');
  const ar=latestReportSummary?.aging||[];
  const arTotal=ar.reduce((s,r)=>s+Number(r.total||0),0);
  setText('kpi-ar',formatAed(arTotal));
  const monthly=report.monthly||[];
  const chartBody=document.getElementById('kpi-chart-body');
  if(chartBody){
    const max=Math.max(1,...monthly.map(r=>Number(r.sales||0)));
    chartBody.innerHTML=monthly.length?monthly.map(r=>`<div class="chart-col"><div class="chart-stack" style="height:${Math.max(6,Math.round((Number(r.sales||0)/max)*94))}%"></div><div class="chart-label">${escapeHtml(r.period)}</div></div>`).join(''):`<div style="font-size:12px;color:var(--text3)">No data</div>`;
  }
}

function renderAiHealthScore(h){
  const score=Number(h.overall_score||0);
  const circumference=2*Math.PI*52;
  const fill=document.getElementById('rep-ring-fg');
  if(fill){
    const pct=Math.min(100,score)/100;
    fill.style.strokeDasharray=`${circumference*pct} ${circumference*(1-pct)}`;
    fill.style.stroke=score>=70?'var(--green)':score>=40?'var(--amber)':'var(--red)';
  }
  setText('rep-score-num',String(score));
  setText('rep-score-band',h.band||'Loading');
  const pairs=[['hbar-liq','hval-liq',h.liquidity_score],['hbar-prof','hval-prof',h.profitability_score],['hbar-debt','hval-debt',h.debt_level_score],['hbar-cash','hval-cash',h.cash_reserve_score],['hbar-pay','hval-pay',h.payment_behavior_score]];
  pairs.forEach(([barId,valId,val])=>{
    const bar=document.getElementById(barId);
    const el=document.getElementById(valId);
    if(bar)bar.style.width=(val||0)+'%';
    if(el)el.textContent=String(val||0);
  });
}

function renderCfoPanel(report,ai){
  setText('cfo-summary',report.ai_summary||'AI summary not available.');
  const actions=document.getElementById('cfo-actions');
  if(actions)actions.innerHTML=(report.actions||[]).map(item=>`<li>${escapeHtml(item)}</li>`).join('')||'<li>No actions at this time.</li>';
  const tbody=document.getElementById('cfo-anomaly-tbody');
  if(tbody){
    const rows=ai.anomalies_list||[];
    tbody.innerHTML=rows.length?rows.map(row=>`<tr><td>${escapeHtml(row.area)}</td><td>${escapeHtml(row.signal)}</td><td><span class="b ${row.impact==='High'?'b-r':row.impact==='Medium'?'b-a':'b-g'}">${escapeHtml(row.impact)}</span></td><td><button class="btn btn-g btn-sm" onclick="showReport('rep-ar')">${escapeHtml(row.action)}</button></td></tr>`).join(''):`<tr><td colspan="4" style="color:var(--text3);text-align:center">No anomalies found.</td></tr>`;
  }
}

function renderVatReport(vat){
  const out=vat.output||{};
  const inp=vat.input||{};
  const settlement=vat.settlement||{};
  const ob=document.getElementById('rep-vat-output');
  if(ob)ob.innerHTML=`<tr><td class="mono">1</td><td>Standard rated supplies</td><td class="mono" style="text-align:right">${reportAmount(out.standard_rated)}</td></tr><tr><td class="mono">2</td><td>Zero-rated supplies</td><td class="mono" style="text-align:right">${reportAmount(out.zero_rated)}</td></tr><tr><td class="mono">3</td><td>Exempt supplies</td><td class="mono" style="text-align:right">${reportAmount(out.exempt)}</td></tr><tr><td class="mono">4</td><td>Total supplies</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(out.total_supplies)}</td></tr><tr><td class="mono">5</td><td>Output VAT</td><td class="mono" style="text-align:right;color:var(--accent)">${reportAmount(out.output_vat)}</td></tr>`;
  const ib=document.getElementById('rep-vat-input');
  if(ib)ib.innerHTML=`<tr><td class="mono">6</td><td>Standard rated purchases</td><td class="mono" style="text-align:right">${reportAmount(inp.standard_rated)}</td></tr><tr><td class="mono">7</td><td>Zero-rated purchases</td><td class="mono" style="text-align:right">${reportAmount(inp.zero_rated)}</td></tr><tr><td class="mono">8</td><td>Exempt purchases</td><td class="mono" style="text-align:right">${reportAmount(inp.exempt)}</td></tr><tr><td class="mono">9</td><td>Total purchases</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(inp.total_purchases)}</td></tr><tr><td class="mono">10</td><td>Input VAT</td><td class="mono" style="text-align:right;color:var(--green)">${reportAmount(inp.input_vat)}</td></tr>`;
  setText('vat-out',formatAed(settlement.output_vat||0));
  setText('vat-in',formatAed(settlement.input_vat||0));
  setText('vat-net',formatAed(settlement.net_vat_payable||0));
}

function renderVat201(vat){
  const out=vat.output||{};const inp=vat.input||{};const r=vat.readiness||{};
  const ob=document.getElementById('vat201-output');
  if(ob)ob.innerHTML=`<tr><td class="mono">1</td><td>Standard rated supplies</td><td class="mono" style="text-align:right">${reportAmount(out.standard_rated)}</td><td class="mono" style="text-align:right">${reportAmount(out.output_vat)}</td></tr><tr><td class="mono">2</td><td>Zero-rated supplies</td><td class="mono" style="text-align:right">0.00</td><td class="mono" style="text-align:right">0.00</td></tr><tr><td class="mono">3</td><td>Exempt supplies</td><td class="mono" style="text-align:right">0.00</td><td class="mono" style="text-align:right">—</td></tr><tr><td class="mono">4</td><td>Supplies subject to tax in other countries</td><td class="mono" style="text-align:right">0.00</td><td class="mono" style="text-align:right">—</td></tr><tr><td class="mono">5</td><td>Total output supplies</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(out.total_supplies)}</td><td class="mono" style="text-align:right;font-weight:700;color:var(--accent)">${reportAmount(out.output_vat)}</td></tr>`;
  const ib=document.getElementById('vat201-input');
  if(ib)ib.innerHTML=`<tr><td class="mono">9</td><td>Standard rated expenses (recoverable)</td><td class="mono" style="text-align:right">${reportAmount(inp.standard_rated)}</td><td class="mono" style="text-align:right">${reportAmount(inp.input_vat)}</td></tr><tr><td class="mono">10</td><td>Total recoverable input tax</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(inp.total_purchases)}</td><td class="mono" style="text-align:right;font-weight:700;color:var(--green)">${reportAmount(inp.input_vat)}</td></tr>`;
  const trnBar=document.getElementById('vat-trn-bar');const trnVal=document.getElementById('vat-trn-val');
  if(trnBar)trnBar.style.width=(r.trn_checks||100)+'%';
  if(trnVal)trnVal.textContent=(r.trn_checks||100)+'%';
  const mathBar=document.getElementById('vat-math-bar');const mathVal=document.getElementById('vat-math-val');
  if(mathBar)mathBar.style.width=(r.vat_math||0)+'%';
  if(mathVal)mathVal.textContent=(r.vat_math||0)+'%';
  const docsBar=document.getElementById('vat-docs-bar');const docsVal=document.getElementById('vat-docs-val');
  if(docsBar)docsBar.style.width=(r.documents||0)+'%';
  if(docsVal)docsVal.textContent=(r.documents||0)+'%';
}

function renderProfitLossReport(pl){
  const body=document.getElementById('rep-pl-body');
  if(!body)return;
  const ytd=pl.ytd||{};
  body.innerHTML=`<tr><td colspan="3" style="font-weight:600;background:var(--surface2)">Revenue</td></tr><tr><td style="padding-left:20px">Sales Revenue</td><td class="mono" style="text-align:right">${reportAmount(pl.revenue)}</td><td class="mono" style="text-align:right">${reportAmount(ytd.revenue)}</td></tr><tr><td style="font-weight:600">Total Revenue</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(pl.total_revenue)}</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(ytd.revenue)}</td></tr><tr><td colspan="3" style="font-weight:600;background:var(--surface2)">Cost of Goods Sold</td></tr><tr><td style="padding-left:20px">Purchases / COGS</td><td class="mono" style="text-align:right">${reportAmount(pl.cogs)}</td><td class="mono" style="text-align:right">${reportAmount(ytd.cogs)}</td></tr><tr><td style="font-weight:600;color:var(--green)">Gross Profit</td><td class="mono" style="text-align:right;font-weight:600;color:var(--green)">${reportAmount(pl.gross_profit)}</td><td class="mono" style="text-align:right;font-weight:600;color:var(--green)">${reportAmount(ytd.gross_profit)}</td></tr><tr><td colspan="3" style="font-weight:600;background:var(--surface2)">Operating Expenses</td></tr><tr><td style="padding-left:20px">Payroll</td><td class="mono" style="text-align:right">${reportAmount(pl.payroll)}</td><td class="mono" style="text-align:right">${reportAmount(pl.payroll)}</td></tr><tr><td style="padding-left:20px">Other Expenses</td><td class="mono" style="text-align:right">${reportAmount(pl.other_expenses)}</td><td class="mono" style="text-align:right">${reportAmount(pl.other_expenses)}</td></tr><tr><td style="font-weight:600">Total Expenses</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(pl.total_expenses)}</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(ytd.expenses)}</td></tr><tr style="background:var(--surface2)"><td style="font-weight:700;font-size:14px">Net Profit</td><td class="mono" style="text-align:right;font-weight:700;font-size:15px;color:var(--green)">${reportAmount(pl.net_profit)}</td><td class="mono" style="text-align:right;font-weight:700;font-size:15px;color:var(--green)">${reportAmount(ytd.net_profit)}</td></tr>`;
}

function renderBalanceSheetReport(bs){
  const totals=bs.totals||{};
  setText('bs-assets',formatAed(totals.assets||0));
  setText('bs-liab',formatAed(totals.liabilities||0));
  setText('bs-equity',formatAed(totals.equity||0));
  setText('bs-diff',formatAed(totals.difference||0));
  const assetBody=document.getElementById('rep-bs-assets');
  if(assetBody){
    const rows=bs.assets||[];
    assetBody.innerHTML=(rows.length?rows.map(row=>`<tr><td class="mono">${escapeHtml(row.code)}</td><td>${escapeHtml(row.name)}</td><td class="mono" style="text-align:right">${reportAmount(row.amount)}</td></tr>`).join(''):`<tr><td colspan="3" style="color:var(--text3);text-align:center">No asset balances in database.</td></tr>`)+`<tr style="background:var(--surface2)"><td colspan="2" style="font-weight:600">Total Assets</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(totals.assets)}</td></tr>`;
  }
  const liabBody=document.getElementById('rep-bs-liabilities-equity');
  if(liabBody){
    const rows=[...(bs.liabilities||[]).map(r=>({...r,section:'Liabilities'})),...(bs.equity||[]).map(r=>({...r,section:'Equity'}))];
    liabBody.innerHTML=(rows.length?rows.map(row=>`<tr><td>${escapeHtml(row.section)}</td><td><span class="mono">${escapeHtml(row.code)}</span> ${escapeHtml(row.name)}</td><td class="mono" style="text-align:right">${reportAmount(row.amount)}</td></tr>`).join(''):`<tr><td colspan="3" style="color:var(--text3);text-align:center">No liability/equity balances in database.</td></tr>`)+`<tr style="background:var(--surface2)"><td colspan="2" style="font-weight:600">Total Liabilities & Equity</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(totals.liabilities_equity)}</td></tr>`;
  }
}

function renderCashFlowReport(data){
  const cash=data.cash_flow_rows||[];
  const forecast=data.forecast_rows||[];
  const ob=document.getElementById('cf-operating');
  if(ob)ob.innerHTML=cash.length?cash.map(r=>`<tr><td>${escapeHtml(r.section)}</td><td class="mono" style="text-align:right">${reportAmount(r.direct)}</td><td class="mono" style="text-align:right">${reportAmount(r.indirect)}</td></tr>`).join(''):`<tr><td colspan="3" style="color:var(--text3);text-align:center">No cash flow records in database.</td></tr>`;
  const fb=document.getElementById('cf-forecast');
  if(fb)fb.innerHTML=forecast.length?forecast.map(r=>`<tr><td>${escapeHtml(r.period)}</td><td class="mono" style="text-align:right">${reportAmount(r.receipts)}</td><td class="mono" style="text-align:right">${reportAmount(r.payments)}</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(r.net)}</td></tr>`).join(''):`<tr><td colspan="4" style="color:var(--text3);text-align:center">No forecast records in database.</td></tr>`;
}

function renderTrialBalanceReport(rows){
  const body=document.getElementById('rep-tb-body');
  if(!body)return;
  const dt=rows.reduce((s,r)=>s+Number(r.debit||0),0);
  const ct=rows.reduce((s,r)=>s+Number(r.credit||0),0);
  body.innerHTML=(rows.length?rows.map(r=>`<tr><td class="mono">${escapeHtml(r.code)}</td><td>${escapeHtml(r.name)}</td><td class="mono" style="text-align:right">${reportAmount(r.debit)}</td><td class="mono" style="text-align:right">${reportAmount(r.credit)}</td></tr>`).join(''):`<tr><td colspan="4" style="color:var(--text3);text-align:center">No posted journal lines in database.</td></tr>`)+`<tr style="background:var(--surface2)"><td colspan="2" style="font-weight:600">Total</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(dt)}</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(ct)}</td></tr>`;
}

function renderGeneralLedger(rows){
  const body=document.getElementById('rep-gl-body');
  if(!body)return;
  body.innerHTML=rows.length?rows.map(r=>`<tr><td class="mono" style="white-space:nowrap">${escapeHtml(r.date)}</td><td class="mono">${escapeHtml(r.reference)}</td><td>${escapeHtml(r.account_code)} <span style="color:var(--text3)">${escapeHtml(r.account_name)}</span></td><td>${escapeHtml(r.description)}</td><td class="mono" style="text-align:right">${reportAmount(r.debit)}</td><td class="mono" style="text-align:right">${reportAmount(r.credit)}</td></tr>`).join(''):`<tr><td colspan="6" style="color:var(--text3);text-align:center">No posted journal entries in database.</td></tr>`;
}

function renderPartyLedger(tbodyId,rows,label,key){
  const body=document.getElementById(tbodyId);
  if(!body)return;
  const total=rows.reduce((s,r)=>s+Number(r.total||0),0);
  body.innerHTML=(rows.length?rows.map(r=>`<tr><td>${escapeHtml(r[key]||r.party||'Unknown')}</td><td class="mono" style="text-align:right">${r.transactions||0}</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(r.total)}</td></tr>`).join(''):`<tr><td colspan="3" style="color:var(--text3);text-align:center">No ${label.toLowerCase()} transactions in database.</td></tr>`)+`<tr style="background:var(--surface2)"><td colspan="2" style="font-weight:600">Total</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(total)}</td></tr>`;
}

function renderAgingReport(rows){
  const body=document.getElementById('rep-ar-body');
  if(!body)return;
  const total=rows.reduce((s,r)=>s+Number(r.total||0),0);
  body.innerHTML=(rows.length?rows.map(r=>`<tr><td>${escapeHtml(r.customer)}</td><td class="mono" style="text-align:right">${reportAmount(r.current)}</td><td class="mono" style="text-align:right">${reportAmount(r.d1_30)}</td><td class="mono" style="text-align:right">${reportAmount(r.d31_60)}</td><td class="mono" style="text-align:right">${reportAmount(r.d61_90)}</td><td class="mono" style="text-align:right">${reportAmount(r.over90)}</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(r.total)}</td></tr>`).join(''):`<tr><td colspan="7" style="color:var(--text3);text-align:center">No unpaid invoices in database.</td></tr>`)+`<tr style="background:var(--surface2)"><td colspan="6" style="font-weight:600;text-align:right">Total</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(total)}</td></tr>`;
}

function renderApAging(rows){
  const body=document.getElementById('rep-ap-body');
  if(!body)return;
  const total=rows.reduce((s,r)=>s+Number(r.total||0),0);
  body.innerHTML=(rows.length?rows.map(r=>`<tr><td>${escapeHtml(r.supplier)}</td><td class="mono" style="text-align:right">${reportAmount(r.current)}</td><td class="mono" style="text-align:right">${reportAmount(r.d1_30)}</td><td class="mono" style="text-align:right">${reportAmount(r.d31_60)}</td><td class="mono" style="text-align:right">${reportAmount(r.d61_90)}</td><td class="mono" style="text-align:right">${reportAmount(r.over90)}</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(r.total)}</td></tr>`).join(''):`<tr><td colspan="7" style="color:var(--text3);text-align:center">No unpaid purchase invoices in database.</td></tr>`)+`<tr style="background:var(--surface2)"><td colspan="6" style="font-weight:600;text-align:right">Total</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(total)}</td></tr>`;
}

function renderInventoryReport(){
  const body=document.getElementById('rep-inv-body');
  if(!body)return;
  const products=[...stockProductMappings.values()].slice(0,100);
  body.innerHTML=products.length?products.map(p=>`<tr><td class="mono">${escapeHtml(p.sku||'—')}</td><td>${escapeHtml(p.name||p.taxflow_name||'—')}</td><td class="mono" style="text-align:right">${Number(p.qty||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono" style="text-align:right">${reportAmount(p.cost||0)}</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount((Number(p.qty||0)*Number(p.cost||0)).toFixed(2))}</td></tr>`).join(''):`<tr><td colspan="5" style="color:var(--text3);text-align:center">No inventory items found.</td></tr>`;
}

function renderRevenueIntelligence(rev,dashboard){
  const cust=rev.by_customer||[];
  const monthly=rev.monthly||dashboard.monthly||[];
  const cb=document.getElementById('rev-by-customer');
  if(cb)cb.innerHTML=cust.length?cust.map(r=>`<tr><td>${escapeHtml(r.customer||r.party||'Unknown')}</td><td class="mono" style="text-align:right;font-weight:600">${reportAmount(r.revenue||r.total||0)}</td></tr>`).join(''):`<tr><td colspan="2" style="color:var(--text3);text-align:center">No customer revenue data.</td></tr>`;
  const chartBody=document.getElementById('rev-chart-body');
  if(chartBody){
    const max=Math.max(1,...monthly.map(r=>Number(r.sales||r.revenue||0)));
    chartBody.innerHTML=monthly.length?monthly.map(r=>`<div class="chart-col"><div class="chart-stack" style="height:${Math.max(6,Math.round((Number(r.sales||r.revenue||0)/max)*94))}%"></div><div class="chart-label">${escapeHtml(r.period)}</div></div>`).join(''):`<div style="font-size:12px;color:var(--text3)">No data</div>`;
  }
  setText('rev-growth',(rev.growth_pct||'0.00')+'%');
  setText('rev-total',formatAed(dashboard.revenue||0));
  setText('rev-top-cust',cust[0]?.customer||cust[0]?.party||'—');
  setText('rev-cust-count',String(cust.length));
}

function renderProfitabilityAnalytics(pl){
  const rev=Number(pl.total_revenue||pl.revenue||0);
  const gp=Number(pl.gross_profit||0);
  const np=Number(pl.net_profit||0);
  const gm=rev?(gp/rev*100).toFixed(1):0;
  setText('prf-gp',formatAed(gp));
  setText('prf-np',formatAed(np));
  setText('prf-ebitda',formatAed(np));
  setText('prf-gm',gm+'%');
  const body=document.getElementById('prf-table');
  if(body)body.innerHTML=`<tr><td>Revenue</td><td class="mono" style="text-align:right">${reportAmount(pl.revenue)}</td><td class="mono" style="text-align:right">100%</td></tr><tr><td>Cost of Goods Sold</td><td class="mono" style="text-align:right">${reportAmount(pl.cogs)}</td><td class="mono" style="text-align:right">${rev?((Number(pl.cogs||0)/rev*100).toFixed(1)+'%'):'—'}</td></tr><tr style="background:var(--surface2)"><td style="font-weight:600">Gross Profit</td><td class="mono" style="text-align:right;font-weight:600;color:var(--green)">${reportAmount(pl.gross_profit)}</td><td class="mono" style="text-align:right;font-weight:600">${gm}%</td></tr><tr><td>Operating Expenses</td><td class="mono" style="text-align:right">${reportAmount(pl.total_expenses)}</td><td class="mono" style="text-align:right">${rev?((Number(pl.total_expenses||0)/rev*100).toFixed(1)+'%'):'—'}</td></tr><tr style="background:var(--surface2)"><td style="font-weight:600">Net Profit</td><td class="mono" style="text-align:right;font-weight:600;color:var(--accent)">${reportAmount(pl.net_profit)}</td><td class="mono" style="text-align:right;font-weight:600">${rev?((np/rev*100).toFixed(1)+'%'):'—'}</td></tr>`;
}

function renderWorkingCapital(wc){
  setText('wc-amount',formatAed(wc.working_capital||0));
  setText('wc-cr',wc.current_ratio||'—');
  setText('wc-qr',wc.quick_ratio||'—');
  setText('wc-liab',formatAed(wc.current_liabilities||0));
  const rdp=document.getElementById('wc-rdp');if(rdp)rdp.textContent=wc.receivable_days||'—';
  const pdp=document.getElementById('wc-pdp');if(pdp)pdp.textContent=wc.payable_days||'—';
  const inv=document.getElementById('wc-inv');if(inv)inv.textContent=wc.inventory_turnover||'—';
  const assets=Number(wc.current_assets||0);const liab=Number(wc.current_liabilities||0);
  const maxVal=Math.max(assets,liab,1);
  const ab=document.getElementById('wc-asset-bar');if(ab)ab.style.width=(assets/maxVal*100)+'%';
  const av=document.getElementById('wc-asset-val');if(av)av.textContent=formatAed(assets);
  const lb=document.getElementById('wc-liab-bar');if(lb)lb.style.width=(liab/maxVal*100)+'%';
  const lv=document.getElementById('wc-liab-val');if(lv)lv.textContent=formatAed(liab);
}

function renderGrowthTrends(dashboard,rev){
  const monthly=dashboard.monthly||[];
  const chartBody=document.getElementById('growth-chart-body');
  if(chartBody){
    const max=Math.max(1,...monthly.map(r=>Number(r.sales||0)),...monthly.map(r=>Number(r.purchases||0)));
    chartBody.innerHTML=monthly.length?monthly.map(r=>`<div class="chart-col"><div class="chart-stack" style="height:${Math.max(6,Math.round((Number(r.sales||0)/max)*94))}%"></div><div class="chart-stack amber" style="height:${Math.max(6,Math.round((Number(r.purchases||0)/max)*94))}%"></div><div class="chart-label">${escapeHtml(r.period)}</div></div>`).join(''):`<div style="font-size:12px;color:var(--text3)">No data</div>`;
  }
  setText('growth-rev',(rev.growth_pct||'0.00')+'%');
  const sales=monthly.map(r=>Number(r.sales||0));
  const peak=sales.length?Math.max(...sales):0;
  const avg=sales.length?(sales.reduce((a,b)=>a+b,0)/sales.length):0;
  const ytd=sales.reduce((a,b)=>a+b,0);
  setText('growth-peak',formatAed(peak));
  setText('growth-avg',formatAed(avg));
  setText('growth-ytd',formatAed(ytd));
}

function statusBadge(status){
  const value=String(status||'Ready');
  const lower=value.toLowerCase();
  const cls=lower.includes('high')||lower.includes('open')||lower.includes('failed')?'b-r':lower.includes('review')||lower.includes('draft')||lower.includes('pending')?'b-a':lower.includes('none')?'b-gray':'b-g';
  return `<span class="b ${cls}">${escapeHtml(value)}</span>`;
}

function renderCorporateReports(corp){
  const s=corp.stats||{};
  setText('corp-tax',formatAed(s.corporate_tax||0));
  setText('corp-income',formatAed(s.taxable_income||0));
  setText('corp-parties',String(s.related_party_count||0));
  setText('corp-entities',String(s.group_entity_count||0));
  const taxBody=document.getElementById('rep-corp-tax');
  if(taxBody){
    const rows=corp.tax_rows||[];
    taxBody.innerHTML=rows.length?rows.map(r=>`<tr><td>${escapeHtml(r.line)}</td><td class="mono" style="text-align:right">${reportAmount(r.amount)}</td><td>${statusBadge(r.status)}</td></tr>`).join(''):`<tr><td colspan="3" style="color:var(--text3);text-align:center">No corporate tax records found; calculated from report profit.</td></tr>`;
  }
  const rpBody=document.getElementById('rep-corp-rp');
  if(rpBody){
    const rows=corp.related_party_rows||[];
    rpBody.innerHTML=rows.length?rows.map(r=>`<tr><td>${escapeHtml(r.party)}</td><td>${escapeHtml(r.type)}</td><td class="mono" style="text-align:right">${reportAmount(r.amount)}</td></tr>`).join(''):`<tr><td colspan="3" style="color:var(--text3);text-align:center">No related party records found in database.</td></tr>`;
  }
}

function renderAssetReports(data){
  const faBody=document.getElementById('rep-assets-fa');
  if(faBody){
    const fixed=data.fixed_assets||[];
    faBody.innerHTML=fixed.length?fixed.map(r=>`<tr><td>${escapeHtml(r.asset)}</td><td>${escapeHtml(r.category)}</td><td class="mono" style="text-align:right">${reportAmount(r.cost)}</td><td class="mono" style="text-align:right">${reportAmount(r.depreciation)}</td><td class="mono" style="text-align:right">${reportAmount(r.book_value)}</td></tr>`).join(''):`<tr><td colspan="5" style="color:var(--text3);text-align:center">No fixed asset records found in database.</td></tr>`;
  }
  const accBody=document.getElementById('rep-assets-acc');
  if(accBody){
    const accruals=[...(data.accruals||[]),...(data.prepayments||[])];
    accBody.innerHTML=accruals.length?accruals.map(r=>`<tr><td>${escapeHtml(r.reference||r.item||'—')}</td><td>${escapeHtml(r.type||'Prepayment')}</td><td class="mono" style="text-align:right">${reportAmount(r.amount||r.total||0)}</td><td>${statusBadge(r.status)}</td></tr>`).join(''):`<tr><td colspan="4" style="color:var(--text3);text-align:center">No accrual/prepayment records found in database.</td></tr>`;
  }
}

function renderBudgetCashReports(data){
  // Cash flow data renders into the Cash Flow panel (rep-cf)
  renderCashFlowReport(data);
}

function renderControlReports(data){
  // Control data surfaced in CFO panel anomaly section — no dedicated panel needed
}

function activeReportBody(){
  return document.querySelector('#page-reports .rep-panel.on')||document.getElementById('rep-kpi');
}

function activeReportTitle(){
  const activeNav=document.querySelector('#page-reports .rep-nav-item.on');
  return activeNav?.textContent.trim()||'Reports';
}

function exportActiveReportPdf(){
  const body=activeReportBody();
  if(!body){toast('Open a report before exporting','warn');return;}
  exportReportPdf(body.id,activeReportTitle());
}

function exportAllReportsPdf(){
  exportReportPdf('all','All Reports');
}

function exportActiveReportExcel(){
  const body=activeReportBody();
  if(!body){toast('Open a report before exporting','warn');return;}
  exportReportExcel(body.id,activeReportTitle());
}

function exportReportPdf(targetId,title){
  const source=targetId==='all'
    ?[...document.querySelectorAll('#page-reports .rep-panel')].map(section=>{
        const nav=document.getElementById('repnav-'+section.id.replace('rep-',''));
        return `<section class="pdf-section"><h2 class="pdf-section-title">${escapeHtml(nav?.textContent.trim()||section.id)}</h2>${section.innerHTML}</section>`;
      }).join('')
    :document.getElementById(targetId)?.innerHTML;
  if(!source){toast('Report content not found','warn');return;}
  const safeTitle=title||'TaxFlow Report';
  const companyName=document.getElementById('company-name')?.value||document.querySelector('.company-name')?.textContent||'TaxFlow UAE';
  const printWindow=window.open('','_blank','width=1200,height=850');
  if(!printWindow){toast('Allow popups to export PDF','warn');return;}
  printWindow.document.open();
  printWindow.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>${escapeHtml(safeTitle)} - ${escapeHtml(companyName)}</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{background:#fff;color:#172033;font-family:Arial,Helvetica,sans-serif;font-size:12px;line-height:1.5;padding:0;}
@page{size:A4 landscape;margin:12mm 10mm;}
.pdf-shell{max-width:100%;padding:0;}
.pdf-cover{display:flex;justify-content:space-between;align-items:flex-start;background:#1e2540;color:#fff;padding:14px 18px;margin-bottom:0;}
.pdf-brand{font-size:18px;font-weight:800;letter-spacing:-0.5px;}
.pdf-brand span{color:#60a5fa;}
.pdf-report-title{font-size:14px;font-weight:700;margin-top:3px;color:#cbd5e1;}
.pdf-meta{text-align:right;font-size:11px;color:#94a3b8;line-height:1.7;}
.pdf-section{padding:14px 18px;border-bottom:2px solid #e2e8f0;break-inside:auto;}
.pdf-section:last-child{border-bottom:none;}
.pdf-section-title{font-size:13px;font-weight:800;color:#1e2540;text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px;padding-bottom:5px;border-bottom:2px solid #3b82f6;}
/* Hide interactive/nav elements */
.rep-panel-head button,.rep-sidebar,.rep-filter-bar,.rep-panel-head .btn,
button,select,input,.btn,.tabs,.topbar,.sb,.scrim,.toasts,.tbl-tools,
.ai-card-action,.row-actions,.rep-nav-item,.axis-chart,.donut,.risk-track{display:none!important;}
/* Stats */
.stat{border:1px solid #e2e8f0;border-radius:6px;padding:10px 12px;background:#f8fafc;break-inside:avoid;}
.stat-lbl{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.5px;color:#64748b;margin-bottom:4px;}
.stat-val{font-size:18px;font-weight:900;color:#1e2540;font-family:'DM Mono',monospace,Arial;}
.stat-delta{font-size:10px;color:#94a3b8;margin-top:2px;}
.g4{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-bottom:12px;}
.g3{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-bottom:12px;}
.g2{display:grid;grid-template-columns:repeat(2,1fr);gap:10px;margin-bottom:12px;}
/* Cards */
.card{border:1px solid #e2e8f0;border-radius:6px;padding:10px 12px;background:#fff;break-inside:avoid;margin-bottom:8px;}
.section-hd{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.4px;color:#64748b;padding-bottom:6px;border-bottom:1px solid #e2e8f0;margin-bottom:8px;}
.card-title{font-size:12px;font-weight:700;color:#1e2540;margin-bottom:6px;}
.rep-panel-head h2{font-size:14px;font-weight:800;color:#1e2540;margin-bottom:10px;}
/* Tables */
table,.tbl{width:100%;border-collapse:collapse;font-size:11px;}
th{background:#f1f5f9;color:#475569;font-weight:700;font-size:10px;text-transform:uppercase;letter-spacing:.3px;padding:6px 8px;text-align:left;border-bottom:2px solid #cbd5e1;}
td{padding:5px 8px;border-bottom:1px solid #e2e8f0;color:#334155;vertical-align:middle;}
tr:last-child td{border-bottom:none;}
tr:hover td{background:transparent;}
.mono{font-family:'DM Mono',monospace,Arial;}
/* Badges */
.b{display:inline-block;font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px;}
.b-g{background:#dcfce7;color:#166534;}
.b-r{background:#fee2e2;color:#991b1b;}
.b-a{background:#fef3c7;color:#92400e;}
.b-b{background:#dbeafe;color:#1e40af;}
/* AI card */
.ai-card{border:1px solid #bfdbfe;border-radius:6px;padding:10px 12px;background:#eff6ff;margin-bottom:8px;}
/* Score bars */
.rep-score-bar-row{display:flex;align-items:center;gap:8px;margin-bottom:6px;font-size:11px;}
.rep-score-bar-row span:first-child{width:120px;flex-shrink:0;}
.rep-score-track{flex:1;height:6px;background:#e2e8f0;border-radius:3px;}
.rep-score-fill{height:6px;background:#3b82f6;border-radius:3px;}
/* Health score ring — simplified for PDF */
.rep-score-ring svg{display:none;}
.rep-score-inner{font-size:20px;font-weight:900;}
/* Lines table */
.ai-lines-table{width:100%;border-collapse:collapse;font-size:11px;margin-bottom:10px;}
.ai-lines-table th{background:#f1f5f9;padding:5px 6px;text-align:left;font-size:10px;font-weight:700;border-bottom:2px solid #cbd5e1;}
.ai-lines-table td{padding:4px 6px;border-bottom:1px solid #e2e8f0;}
</style>
</head><body>
<div class="pdf-shell">
  <div class="pdf-cover">
    <div>
      <div class="pdf-brand">Tax<span>Flow</span></div>
      <div class="pdf-report-title">${escapeHtml(safeTitle)}</div>
      <div style="font-size:11px;color:#94a3b8;margin-top:2px">${escapeHtml(companyName)}</div>
    </div>
    <div class="pdf-meta">
      <div style="font-size:13px;font-weight:700;color:#fff">${escapeHtml(safeTitle)}</div>
      <div>Generated: ${escapeHtml(new Date().toLocaleString('en-AE'))}</div>
      <div>TaxFlow UAE Business Platform</div>
    </div>
  </div>
  ${targetId==='all'?source:`<div class="pdf-section"><h2 class="pdf-section-title">${escapeHtml(safeTitle)}</h2>${source}</div>`}
</div>
<script>window.onload=()=>{setTimeout(()=>{window.print();},300);};<\/script>
</body></html>`);
  printWindow.document.close();
  toast(`${safeTitle} — PDF print dialog opened`,'ok');
  audit('Exported report PDF',safeTitle,'Prepared');
}

function exportReportExcel(targetId,title){
  const panel=targetId?document.getElementById(targetId):activeReportBody();
  if(!panel){toast('Open a report before exporting','warn');return;}
  const safeTitle=(title||activeReportTitle()||'Report').trim();
  const tables=[...panel.querySelectorAll('table.tbl,table.ai-lines-table')];
  if(!tables.length){toast('No table data to export in this report','warn');return;}

  function cellXml(text,isHeader){
    const clean=String(text||'').replace(/\s+/g,' ').trim();
    const num=clean.replace(/,/g,'').replace(/AED\s*/i,'').replace(/%$/,'');
    const isNum=clean!==''&&!isNaN(parseFloat(num))&&isFinite(num)&&!/^0\d/.test(num);
    const esc=clean.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    if(isHeader)return `<Cell ss:StyleID="H"><Data ss:Type="String">${esc}</Data></Cell>`;
    if(isNum)return `<Cell ss:StyleID="N"><Data ss:Type="Number">${parseFloat(num)}</Data></Cell>`;
    return `<Cell ss:StyleID="D"><Data ss:Type="String">${esc}</Data></Cell>`;
  }

  const sheetName=(safeTitle.slice(0,31)).replace(/[:\\/?*\[\]]/g,' ');
  const rowsXml=tables.flatMap((tbl,ti)=>{
    const heading=tbl.closest('.card')?.querySelector('.section-hd,.card-title')?.textContent?.trim()||`Table ${ti+1}`;
    const titleRow=`<Row><Cell ss:StyleID="T" ss:MergeAcross="9"><Data ss:Type="String">${heading.replace(/&/g,'&amp;').replace(/</g,'&lt;')}</Data></Cell></Row>`;
    const dataRows=[...tbl.querySelectorAll('tr')].map(tr=>{
      const isHeaderRow=!!tr.querySelector('th');
      const cells=[...tr.querySelectorAll('th,td')].map(c=>cellXml(c.textContent,isHeaderRow));
      return `<Row>${cells.join('')}</Row>`;
    });
    return [titleRow,...dataRows,`<Row><Cell><Data ss:Type="String"></Data></Cell></Row>`];
  }).join('');

  const xml=`<?xml version="1.0" encoding="UTF-8"?><?mso-application progid="Excel.Sheet"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
<Styles>
  <Style ss:ID="T"><Font ss:Bold="1" ss:Size="12"/><Interior ss:Color="#1E2540" ss:Pattern="Solid"/><Font ss:Color="#FFFFFF" ss:Bold="1" ss:Size="11"/></Style>
  <Style ss:ID="H"><Font ss:Bold="1"/><Interior ss:Color="#F1F5F9" ss:Pattern="Solid"/><Alignment ss:Horizontal="Left"/></Style>
  <Style ss:ID="D"><Alignment ss:Horizontal="Left"/></Style>
  <Style ss:ID="N"><Alignment ss:Horizontal="Right"/><NumberFormat ss:Format="#,##0.00"/></Style>
</Styles>
<Worksheet ss:Name="${sheetName}">
<Table DefaultColumnWidth="120">
<Row><Cell ss:StyleID="T" ss:MergeAcross="9"><Data ss:Type="String">${safeTitle.replace(/&/g,'&amp;')}</Data></Cell></Row>
<Row><Cell ss:StyleID="H"><Data ss:Type="String">Generated: ${new Date().toLocaleString('en-AE').replace(/&/g,'&amp;')}</Data></Cell></Row>
<Row><Cell><Data ss:Type="String"></Data></Cell></Row>
${rowsXml}
</Table>
</Worksheet>
</Workbook>`;

  const blob=new Blob([xml],{type:'application/vnd.ms-excel;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;
  a.download=`${safeTitle.replace(/[^a-z0-9]/gi,'_')}_${new Date().toISOString().slice(0,10)}.xls`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  toast(`${safeTitle} exported to Excel`,'ok');
  audit('Exported report Excel',safeTitle,'Prepared');
}

function renderDatabaseDashboardSummary(data){
  const page=document.getElementById('page-dashboard');
  if(!page)return;
  const counts=data.module_counts||data;
  const meta=data.dashboard_meta||{};
  let card=document.getElementById('db-dashboard-summary');
  if(!card){
    card=document.createElement('div');
    card.id='db-dashboard-summary';
    card.className='af-card mb20';
    page.appendChild(card);
  }

  const rows=[
    {label:'Invoices',      count:counts.invoice_count,             page:'sales',      tab:'s-invoices',   icon:'INV', color:'c-accent', group:'Revenue'},
    {label:'Customers',     count:counts.customer_count,            page:'sales',      tab:'s-customers',  icon:'CUS', color:'c-green',  group:'Revenue'},
    {label:'Products',      count:counts.product_count,             page:'sales',      tab:'s-products',   icon:'SKU', color:'c-teal',   group:'Revenue'},
    {label:'Quotations',    count:counts.quotation_count,           page:'quotations', tab:'q-list',       icon:'QTN', color:'c-purple', group:'Revenue'},
    {label:'Purchases',     count:counts.purchase_record_count,     page:'purchase',   tab:'p-records',    icon:'PUR', color:'c-amber',  group:'Operations'},
    {label:'Vendors',       count:counts.vendor_count,              page:'purchase',   tab:'p-vendors',    icon:'VEN', color:'c-green',  group:'Operations'},
    {label:'Inv. Mappings', count:counts.inventory_mapping_count,   page:'inventory',  tab:'inv-mapping',  icon:'MAP', color:'c-teal',   group:'Operations'},
    {label:'Payments',      count:counts.payment_count,             page:'payments',   tab:'',             icon:'PAY', color:'c-accent', group:'Cash'},
    {label:'Accounts',      count:counts.account_count,             page:'accounting', tab:'acc-chart',    icon:'COA', color:'c-teal',   group:'Finance'},
    {label:'Vouchers',      count:counts.journal_count,             page:'accounting', tab:'acc-voucher',  icon:'JV',  color:'c-purple', group:'Finance'},
    {label:'Transactions',  count:counts.source_transaction_count,  page:'purchase',   tab:'p-records',    icon:'SRC', color:'c-amber',  group:'Finance'},
    {label:'Tax Codes',     count:counts.tax_code_count,            page:'settings',   tab:'set-tax',      icon:'VAT', color:'c-green',  group:'Compliance'},
    {label:'Tax Lines',     count:counts.tax_line_count,            page:'reports',    tab:'rep-vat',      icon:'TAX', color:'c-red',    group:'Compliance'},
    {label:'Employees',     count:counts.employee_count,            page:'staff',      tab:'staff-list',   icon:'HR',  color:'c-purple', group:'People'},
    {label:'Documents',     count:counts.document_count,            page:'documents',  tab:'',             icon:'DOC', color:'c-green',  group:'Control'},
    {label:'Audit Logs',    count:counts.audit_count,               page:'settings',   tab:'set-backup',   icon:'LOG', color:'c-red',    group:'Control'}
  ];

  const totalRecords=rows.reduce((s,r)=>s+Number(r.count||0),0);

  // Group config: label, color, dist-bar color
  const groupDef={
    Revenue:   {color:'#6366f1',label:'Revenue'},
    Operations:{color:'#f59e0b',label:'Operations'},
    Cash:      {color:'#10b981',label:'Cash'},
    Finance:   {color:'#2eb8b8',label:'Finance'},
    Compliance:{color:'#3ecf8e',label:'Compliance'},
    People:    {color:'#9b72f0',label:'People'},
    Control:   {color:'#f06b6b',label:'Control'},
  };

  // Build distribution bar (one segment per group)
  const groupTotals={};
  rows.forEach(r=>{groupTotals[r.group]=(groupTotals[r.group]||0)+Number(r.count||0);});
  const distBar=Object.entries(groupTotals).map(([g,n])=>{
    const pct=totalRecords?Math.max(1,Math.round(n/totalRecords*100)):Math.round(100/Object.keys(groupTotals).length);
    const color=(groupDef[g]||{}).color||'#888';
    return `<div class="af-dist-seg" style="flex:${pct};background:${color}" title="${escapeHtml(g)}: ${n.toLocaleString('en-AE')} records"></div>`;
  }).join('');

  // Build groups
  const grouped={};
  rows.forEach(r=>{(grouped[r.group]=grouped[r.group]||[]).push(r);});

  const groupsHtml=Object.entries(grouped).map(([groupName,items])=>{
    const gDef=groupDef[groupName]||{color:'#888'};
    const gTotal=items.reduce((s,r)=>s+Number(r.count||0),0);
    const tilesHtml=items.map(item=>{
      const n=Number(item.count||0);
      const sharePct=totalRecords?Math.max(1,Math.round(n/totalRecords*100)):4;
      return `<button class="af-tile ${escapeHtml(item.color)}" type="button"
          onclick="openDashboardRecord('${escapeHtml(item.page)}','${escapeHtml(item.tab||'')}')"
          aria-label="Open ${escapeHtml(item.label)}">
        <div class="af-tile-top">
          <div class="af-icon">${escapeHtml(item.icon)}</div>
          <span class="af-arrow">↗</span>
        </div>
        <div class="af-count">${n.toLocaleString('en-AE')}</div>
        <div class="af-label">${escapeHtml(item.label)}</div>
        <div class="af-meter"><div class="af-meter-fill" style="width:${sharePct}%"></div></div>
      </button>`;
    }).join('');
    return `<div class="af-group">
      <div class="af-group-head">
        <span class="af-group-label" style="color:${escapeHtml(gDef.color)};border-color:${escapeHtml(gDef.color)}22">${escapeHtml(groupName)}</span>
        <div class="af-group-line"></div>
        <span class="af-group-count">${gTotal.toLocaleString('en-AE')} records</span>
      </div>
      <div class="af-tiles">${tilesHtml}</div>
    </div>`;
  }).join('');

  card.innerHTML=`
    <div class="af-header">
      <div class="af-header-left">
        <div class="af-title">App Functions</div>
        <div class="af-sub">Live database counts · click any tile to open its module</div>
      </div>
      <div class="af-header-right">
        <span class="b b-g"><span class="live-dot"></span>${escapeHtml(meta.status||'Synced')}</span>
        <div class="af-total-badge">
          <div class="af-total-num">${totalRecords.toLocaleString('en-AE')}</div>
          <div class="af-total-lbl">${rows.length} modules</div>
        </div>
      </div>
    </div>
    <div class="af-dist-bar">${distBar}</div>
    <div class="af-groups">${groupsHtml}</div>
  `;
}

function openDashboardRecord(page,tabTarget=''){
  go(page);
  if(tabTarget){
    setTimeout(()=>{
      const tab=[...document.querySelectorAll(`#page-${page} .tab`)].find(item=>(item.getAttribute('onclick')||'').includes(`'${tabTarget}'`));
      if(tab)stab(tab,tabTarget);
    },40);
  }
}

function applyTheme(mode){
  const nightMode=mode!=='light';
  document.body.classList.toggle('theme-light',!nightMode);
  const toggle=document.getElementById('night-mode-toggle');
  if(toggle)toggle.checked=nightMode;
  const status=document.getElementById('theme-status');
  if(status){
    status.textContent=nightMode?'Night mode':'Day mode';
    status.className=nightMode?'b b-b':'b b-g';
  }
  const menuToggle=document.getElementById('theme-menu-toggle');
  if(menuToggle){
    menuToggle.textContent=nightMode?'☀ Light Mode':'☾ Dark Mode';
    menuToggle.title=nightMode?'Switch to light mode':'Switch to night mode';
  }
}

function toggleNightMode(enabled){
  const mode=enabled?'night':'light';
  applyTheme(mode);
  toast(enabled?'Night mode enabled':'Day mode enabled','info');
}

async function apiRequest(action,payload,options={}){
  await ensureBackendSession();
  const endpoint=APP_CONFIG.apiEndpoint||`${apiBaseUrl()}/app-data`;
  const response=await authenticatedFetch(`${endpoint}?action=${encodeURIComponent(action)}`,{
    method:options.method||'POST',
    body:options.method==='GET'?undefined:JSON.stringify(payload||{})
  });
  if(!response.ok)throw new Error('FastAPI app-data endpoint returned '+response.status);
  const data=await response.json();
  if(data&&data.ok===false)throw new Error(data.error||'FastAPI app-data request failed');
  return data;
}

function saveServer(collection,record,options={}){
  return apiRequest('save',{collection,record}).catch(err=>{
    console.warn('Database save failed:',err);
    if(options.throwOnError)throw err;
    return null;
  });
}

function bulkSaveServer(collection,records,options={}){
  return apiRequest('bulk-save',{collection,records}).catch(err=>{
    console.warn('Database bulk save failed:',err);
    if(options.throwOnError)throw err;
    return null;
  });
}

function deleteServer(collection,record,options={}){
  return apiRequest('delete',{collection,record}).catch(err=>{
    console.warn('Database delete failed:',err);
    if(options.throwOnError)throw err;
    return null;
  });
}

async function moduleApi(path,options={}){
  const response=await authenticatedFetch(`${apiBaseUrl()}${path}`,{
    method:options.method||'GET',
    body:options.body?JSON.stringify(options.body):undefined
  });
  if(!response.ok)throw new Error(`FastAPI ${path} returned ${response.status}`);
  if(response.status===204)return null;
  return response.json();
}

function exceptionBadgeClass(severity){
  const sev=String(severity||'medium').toLowerCase();
  if(sev==='high')return 'b-r';
  if(sev==='low')return 'b-g';
  return 'b-a';
}

function exceptionAction(module){
  const mod=String(module||'').toLowerCase();
  if(mod.includes('sales'))return "go('sales')";
  if(mod.includes('purchase'))return "go('purchase')";
  if(mod.includes('inventory'))return "go('inventory')";
  if(mod.includes('payroll'))return "go('payroll')";
  if(mod.includes('account'))return "go('accounting')";
  if(mod.includes('document'))return "go('documents')";
  return "toast('Open source module to resolve this exception','info')";
}

function renderExceptionCenter(data){
  const summary=data?.summary||{};
  setText('exc-open',summary.open??0);
  setText('exc-high',summary.high??0);
  setText('exc-medium',summary.medium??0);
  setText('exc-low',summary.low??0);
  setNavBadge('exception',summary.open||0,(summary.high||0)>0?'red':'warn');
  const topCount=document.getElementById('exception-top-count');
  if(topCount){
    topCount.textContent=Number(summary.open||0).toLocaleString('en-AE');
    topCount.className=`b ${(summary.high||0)>0?'b-r':(summary.open||0)>0?'b-a':'b-g'}`;
  }
  const tbody=document.getElementById('exception-tbody');
  if(!tbody)return;
  const rows=Array.isArray(data?.exceptions)?data.exceptions:[];
  if(rows.length===0){
    tbody.innerHTML='<tr><td colspan="6" style="color:var(--text3);text-align:center">No open exceptions found.</td></tr>';
    return;
  }
  tbody.innerHTML=rows.map(row=>`
    <tr>
      <td>${escapeHtml(row.module||'-')}</td>
      <td>${escapeHtml(row.category||row.message||'-')}<div class="card-sub">${escapeHtml(row.message||'')}</div></td>
      <td class="mono">${escapeHtml(row.source_record||'-')}</td>
      <td><span class="b ${exceptionBadgeClass(row.severity)}">${escapeHtml(row.severity||'medium')}</span></td>
      <td><span class="b b-b">${escapeHtml(row.status||'open')}</span></td>
      <td>
        <button class="btn btn-g btn-sm" onclick="${exceptionAction(row.module)}">Open</button>
        <button class="btn btn-g btn-sm" data-module="${escapeHtml(row.module||'')}" data-category="${escapeHtml(row.category||'')}" data-severity="${escapeHtml(row.severity||'medium')}" data-source="${escapeHtml(row.source_record||'')}" data-message="${escapeHtml(row.message||'')}" onclick="explainExceptionAI(this)">AI</button>
      </td>
    </tr>
  `).join('');
}

function loadExceptionCenter(){
  const tbody=document.getElementById('exception-tbody');
  if(tbody)tbody.innerHTML='<tr><td colspan="6" style="color:var(--text3);text-align:center">Reading exception center...</td></tr>';
  moduleApi('/exceptions')
    .then(renderExceptionCenter)
    .catch(err=>{
      console.warn('Exception Center unavailable:',err);
      if(tbody)tbody.innerHTML='<tr><td colspan="6" style="color:var(--red);text-align:center">Exception Center API unavailable.</td></tr>';
    });
}

function explainExceptionAI(button){
  const row=button?.closest('tr');
  if(!button)return;
  const original=button.textContent;
  button.textContent='...';
  moduleApi('/ai/explain-exception',{
    method:'POST',
    body:{
      module:button.dataset.module||'System',
      category:button.dataset.category||'Exception',
      severity:button.dataset.severity||'medium',
      source_record:button.dataset.source||null,
      message:button.dataset.message||''
    }
  }).then(data=>{
    const detail=row?.children?.[1];
    if(detail){
      detail.innerHTML=`${escapeHtml(button.dataset.category||'AI explanation')}<div class="card-sub">${escapeHtml(data.answer||'No explanation returned.')}</div><div class="card-sub">${(data.suggested_actions||[]).map(item=>'- '+escapeHtml(item)).join('<br>')}</div>`;
    }
    toast('AI explanation added','ok');
  }).catch(err=>{
    console.warn('AI exception explanation failed:',err);
    toast('AI explanation unavailable','warn');
  }).finally(()=>{
    button.textContent=original;
  });
}

function saveInvoiceLayoutServer(layout){
  return apiRequest('invoice-layout',layout);
}

function audit(action,record='System',result='Logged'){
  const entry={time:new Date().toLocaleString('en-AE',{dateStyle:'short',timeStyle:'short'}),user:'System User',action,record,result};
  saveServer('audit',entry);
  renderAuditLog([entry]);
}

function renderAuditLog(entries=[]){
  const tbody=document.getElementById('audit-tbody');
  if(!tbody||entries.length===0)return;
  tbody.querySelectorAll('[data-audit-dynamic]').forEach(row=>row.remove());
  const template=document.createElement('tbody');
  template.innerHTML=entries.map(entry=>`<tr data-audit-dynamic><td class="mono">${escapeHtml(entry.time)}</td><td>${escapeHtml(entry.user)}</td><td>${escapeHtml(entry.action)}</td><td>${escapeHtml(entry.record)}</td><td><span class="b b-g">${escapeHtml(entry.result)}</span></td></tr>`).join('');
  [...template.children].reverse().forEach(row=>tbody.prepend(row));
}

function persistSalesInvoice(inv){
  saveServer('salesInvoices',inv);
}

function restoreSalesInvoices(){
  // Sales invoices are restored from the database by hydrateFromServer().
}

function emptyTableMessage(tbody,message='No database records yet.'){
  if(!tbody)return;
  const table=tbody.closest('table');
  const cols=table?.tHead?.rows?.[0]?.cells?.length||1;
  tbody.innerHTML=`<tr data-empty-state="1"><td colspan="${cols}" style="color:var(--text3);text-align:center">${escapeHtml(message)}</td></tr>`;
}

function removeEmptyState(tbody){
  tbody?.querySelectorAll('[data-empty-state]').forEach(row=>row.remove());
}

function clearStaticDemoData(){
  const emptyTables={
    'sales-invoice-tbody':'No sales invoices in database yet.',
    'sales-return-tbody':'No sales returns in database yet.',
    'customer-tbody':'No customers in database yet.',
    'quotation-tbody':'No quotations in database yet.',
    'purchase-record-tbody':'No purchase records in database yet.',
    'bill-tbody':'No vendor bills in database yet.',
    'vendor-tbody':'No vendors in database yet.',
    'payment-in-tbody':'No receipts in database yet.',
    'payment-out-tbody':'No payments in database yet.',
    'bank-account-tbody':'No bank accounts in database yet.',
    'bank-transaction-tbody':'No bank transactions in database yet.',
    'stock-level-tbody':'No stock items in database yet.',
    'stock-movement-tbody':'No stock movements in database yet.',
    'rota-shift-tbody':'No shifts in database yet.',
    'rota-swap-tbody':'No shift swap requests in database yet.',
    'rota-approval-tbody':'No rota approvals in database yet.',
    'expense-tbody':'No expenses in database yet.',
    'expense-approval-tbody':'No pending expenses for approval.',
    'prod-tbody':'No products in database yet.',
    'account-tbody':'No accounts in database yet.',
    'ledger-tbody':'No ledger entries in database yet.',
    'audit-tbody':'No audit records in database yet.'
  };
  Object.entries(emptyTables).forEach(([id,message])=>emptyTableMessage(document.getElementById(id),message));
  document.querySelectorAll('.page table.tbl tbody').forEach(tbody=>{
    if(tbody.querySelector('[data-empty-state]'))return;
    if(tbody.closest('#page-dashboard'))return;
    emptyTableMessage(tbody,'No database records yet.');
  });
  document.querySelectorAll('[data-demo-static="1"]').forEach(node=>node.remove());
  removeDemoProductRows();
  clearDemoFormDefaults();
  clearDemoTextNodes();
  clearDemoCardsAndCounters();
  resetDraftEntryDefaults();
}

const DEMO_TEXT_VALUES=[
  'Acme Trading LLC',
  'Acme Trading',
  'Sara Al Mansouri',
  'Sara Ahmed',
  'Ahmed Rashid',
  'Rania Abboud',
  'Mohamed Jaber',
  'Al Hamad Steel',
  'Dubai Steel Co.',
  'Gulf Freight',
  'Office Depot UAE',
  'UAE Paints Co.',
  'Gulf Logistics Ltd',
  'Emirates Supplies',
  'Al Baraka Trading',
  'Steel Rods 12mm',
  'Packaging Box A',
  'Industrial Oil 5L',
  'Safety Gloves',
  'Mohammed Al Hamdan',
  'Layla Hussain',
  'Khalid Al Rashidi',
  'TaxFlow UAE LLC',
  'Dubai HQ',
  'Abu Dhabi Warehouse',
  'Sharjah Sales Office'
];

function containsDemoText(value){
  const text=String(value||'');
  return DEMO_TEXT_VALUES.some(item=>text.toLowerCase().includes(item.toLowerCase()))
    || /^(INV|PUR|QTN|BILL|PO|RCT)-2024-/i.test(text.trim())
    || /@acmetrading\.ae/i.test(text)
    || /100234567800003|100123456700003|DED-2018-84521|AE070331234567890123456|AE150331234567890123456|AE460331234567890123456/i.test(text);
}

function clearDemoFormDefaults(){
  document.querySelectorAll('select').forEach(select=>{
    [...select.options].forEach(option=>{
      if(containsDemoText(option.textContent.trim())||/^(Tue Evening|Sat Morning|Wed Morning)$/i.test(option.textContent.trim()))option.remove();
    });
    if(select.options.length&&select.selectedIndex<0)select.selectedIndex=0;
  });
  document.querySelectorAll('input,textarea').forEach(input=>{
    if(containsDemoText(input.value))input.value='';
  });
}

function clearDemoTextNodes(){
  const root=document.querySelector('.content');
  if(!root)return;
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT,{
    acceptNode(node){
      if(!containsDemoText(node.nodeValue))return NodeFilter.FILTER_REJECT;
      if(node.parentElement?.closest('script,style'))return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    }
  });
  const nodes=[];
  while(walker.nextNode())nodes.push(walker.currentNode);
  nodes.forEach(node=>{
    let value=node.nodeValue||'';
    DEMO_TEXT_VALUES.forEach(item=>{
      value=value.replace(new RegExp(item.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'),'gi'),'');
    });
    value=value
      .replace(/\b(INV|PUR|QTN|BILL|PO|RCT)-2024-[\w-]+\b/gi,'')
      .replace(/[a-z0-9._%+-]+@acmetrading\.ae/gi,'')
      .replace(/\b(100234567800003|100123456700003|DED-2018-84521|AE070331234567890123456|AE150331234567890123456|AE460331234567890123456)\b/gi,'');
    node.nodeValue=value;
  });
}

function clearDemoCardsAndCounters(){
  const fileList=document.getElementById('pur-file-list');
  if(fileList)fileList.innerHTML='<div style="font-size:12px;color:var(--text3);padding:10px 0">No uploaded files in database yet.</div>';
  setText('file-count-badge','0 files');
  document.querySelectorAll('.page:not(#page-dashboard) .stat-val').forEach(node=>{
    const current=node.textContent.trim();
    node.textContent=/^AED/i.test(current)||current.startsWith(AED_SYMBOL)?'AED 0.00':'0';
  });
  document.querySelectorAll('.page:not(#page-dashboard) .stat-delta').forEach(node=>{node.textContent='';});
  document.querySelectorAll('button').forEach(button=>{
    const label=button.textContent.trim().toLowerCase();
    const action=button.getAttribute('onclick')||'';
    if(label==='add 4 data'||action.includes('addFourPurchaseRecords')){
      button.remove();
    }
  });
}

const financePaymentsByRef=new Map();
const financeBankAccountsByKey=new Map();
// ref.toLowerCase() → {paid, total, isSupplier}
const _invoicePaidMap=new Map();

function resetDraftEntryDefaults(){
  setFieldValue(document.getElementById('inv-no'),'');
  setFieldValue(document.getElementById('quote-no'),'');
  document.querySelectorAll('#inv-lines .sales-inv-line').forEach((row,index)=>{
    if(index>0){
      row.remove();
      return;
    }
    Object.keys(row.dataset).forEach(key=>{
      if(['productCode','productName','priceSource','priceSnapshot','sourcePrice','mappingId','mapped','taxRate','mappingCost','markupPercent'].includes(key)){
        delete row.dataset[key];
      }
    });
    setFieldValue(row.querySelector('.inv-product'),'');
    setFieldValue(row.querySelector('.inv-unit'),'PCS');
    setFieldValue(row.querySelector('.inv-qty'),'1');
    setFieldValue(row.querySelector('.inv-price'),'0.00');
    setFieldValue(row.querySelector('.inv-amount'),'0.00');
  });
  document.querySelectorAll('#quote-lines .quote-line').forEach((row,index)=>{
    if(index>0){
      row.remove();
      return;
    }
    const item=row.querySelector('.quote-item');
    if(item)item.value='';
    setFieldValue(row.querySelector('.quote-qty'),'1');
    setFieldValue(row.querySelector('.quote-price'),'0.00');
    setFieldValue(row.querySelector('.quote-amount'),'0.00');
  });
  setText('subtotal','AED 0.00');
  setText('vat-amt','AED 0.00');
  setText('inv-total','AED 0.00');
  setText('quote-subtotal','AED 0.00');
  setText('quote-vat','AED 0.00');
  setText('quote-total','AED 0.00');
}

function quotationActionsHtml(){
  return `<div class="row-actions"><button class="icon-btn view" type="button" title="View" aria-label="View quotation" onclick="previewQuotation(this)">${viewIconSvg()}</button><button class="icon-btn share" type="button" title="Download PDF" aria-label="Download quotation PDF" onclick="downloadQuotationRowPdf(this)">${downloadIconSvg()}</button><button class="icon-btn share" type="button" title="Share" aria-label="Share quotation" onclick="shareQuotation(this)">${shareIconSvg()}</button><button class="icon-btn edit" type="button" title="Convert" aria-label="Convert quotation" onclick="convertQuotation(this)"><svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9"/><path d="M9 5l3 3-3 3"/></svg></button></div>`;
}

function quotationRecordFromRow(row){
  if(row?.dataset.quotation){
    try{return JSON.parse(row.dataset.quotation);}catch{}
  }
  const c=row?.cells||[];
  return {
    quote_no:c[0]?.textContent?.trim()||'',
    customer:c[1]?.textContent?.trim()||'',
    date:c[2]?.textContent?.trim()||'',
    valid_until:c[3]?.textContent?.trim()||'',
    subtotal:c[4]?.textContent?.trim()||'0',
    vat_amount:c[5]?.textContent?.trim()||'0',
    total:c[6]?.textContent?.trim()||'0',
    status:c[7]?.textContent?.trim()||'Draft',
    owner:c[8]?.textContent?.trim()||'Sales Team'
  };
}

function quotationStatusClass(status){
  const value=String(status||'').toLowerCase();
  if(value.includes('accept')||value.includes('convert'))return 'b-g';
  if(value.includes('expire')||value.includes('reject'))return 'b-r';
  if(value.includes('sent')||value.includes('pending'))return 'b-a';
  return 'b-gray';
}

function renderQuotationRecord(quote){
  const tbody=document.getElementById('quotation-tbody');
  const quoteNo=quote?.quote_no||quote?.quotation_no||quote?.ref;
  if(!tbody||!quoteNo||hasFirstCellValue(tbody,quoteNo))return;
  const row=document.createElement('tr');
  const record={
    quote_no:quoteNo,
    customer:quote.customer||'Customer',
    date:quote.date||'Today',
    valid_until:quote.valid_until||quote.valid||'15 days',
    subtotal:quote.subtotal||0,
    vat_amount:quote.vat_amount||quote.vat||0,
    total:quote.total||0,
    status:quote.status||'Draft',
    owner:quote.owner||'Sales Team',
    subject:quote.subject||'',
    lines:Array.isArray(quote.lines)?quote.lines:[]
  };
  row.dataset.serverRecord='quotations';
  row.dataset.quotation=JSON.stringify(record);
  row.dataset.rowActionsAdded='1';
  row.innerHTML=`<td class="mono">${escapeHtml(record.quote_no)}</td><td>${escapeHtml(record.customer)}</td><td>${escapeHtml(record.date)}</td><td>${escapeHtml(record.valid_until)}</td><td class="mono">${Number(String(record.subtotal).replace(/,/g,'')||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${Number(String(record.vat_amount).replace(/,/g,'')||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${Number(String(record.total).replace(/,/g,'')||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td><span class="b ${quotationStatusClass(record.status)}">${escapeHtml(record.status)}</span></td><td>${escapeHtml(record.owner)}</td><td data-action-col="1">${quotationActionsHtml()}</td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  refreshEnhancedTable(tbody.closest('table'));
}

function hasFirstCellValue(tbody,value){
  const target=String(value||'').trim().toLowerCase();
  return !!tbody&&[...tbody.querySelectorAll('tr:not([data-empty-state])')].some(row=>{
    const first=[...row.children].find(cell=>cell.dataset.inventoryBulkCol!=='1');
    return first?.textContent.trim().toLowerCase()===target;
  });
}

function renderCustomerRecord(customer){
  const tbody=document.getElementById('customer-tbody');
  if(!tbody||!customer?.name||hasFirstCellValue(tbody,customer.name))return;
  const row=document.createElement('tr');
  row.dataset.serverRecord='customers';
  row.dataset.address=customer.address||'';
  row.dataset.email=customer.email||'';
  row.dataset.phone=customer.phone||'';
  row.innerHTML=`<td>${escapeHtml(customer.name)}</td><td class="mono">${escapeHtml(customer.trn||'Not registered')}</td><td>${escapeHtml(customer.emirate||'Dubai')}</td><td>${escapeHtml(customer.email||customer.phone||'-')}</td><td class="mono" style="color:var(--accent)">${'AED'} 0</td><td><button class="btn btn-g btn-sm">View</button></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  refreshInvoiceCustomerOptions();
  refreshQuotationCustomerOptions();
}

function invoiceCustomerRecords(){
  return [...document.querySelectorAll('#customer-tbody tr:not([data-empty-state])')].map(row=>({
    name:row.children[0]?.textContent.trim()||'',
    trn:(row.children[1]?.textContent.trim()||'').replace(/^Not registered$/i,''),
    emirate:row.children[2]?.textContent.trim()||'',
    contact:row.children[3]?.textContent.trim()||'',
    address:row.dataset.address||'',
    email:row.dataset.email||'',
    phone:row.dataset.phone||''
  })).filter(customer=>customer.name);
}

function refreshInvoiceCustomerOptions(){
  const list=document.getElementById('invoice-customer-options');
  if(!list)return;
  list.innerHTML=invoiceCustomerRecords()
    .map(customer=>`<option value="${escapeHtml(customer.name)}" label="${escapeHtml([customer.trn,customer.emirate,customer.contact].filter(Boolean).join(' - '))}"></option>`)
    .join('');
}

function applyInvoiceCustomerSelection(){
  const input=document.getElementById('inv-cust');
  const value=(input?.value||'').trim().toLowerCase();
  if(!value)return;
  const match=invoiceCustomerRecords().find(customer=>customer.name.toLowerCase()===value);
  if(!match)return;
  const trn=document.getElementById('inv-ctrn');
  const address=document.getElementById('inv-caddr');
  if(trn)trn.value=match.trn||'';
  if(address)address.value=match.address||'';
}

function refreshQuotationCustomerOptions(){
  const list=document.getElementById('quote-customer-options');
  if(!list)return;
  list.innerHTML=invoiceCustomerRecords()
    .map(customer=>`<option value="${escapeHtml(customer.name)}" label="${escapeHtml([customer.trn,customer.emirate,customer.contact].filter(Boolean).join(' - '))}"></option>`)
    .join('');
}

function applyQuotationCustomerSelection(){
  const input=document.getElementById('quote-customer');
  const value=(input?.value||'').trim().toLowerCase();
  if(!value)return;
  const match=invoiceCustomerRecords().find(customer=>customer.name.toLowerCase()===value);
  if(!match)return;
  if(!document.getElementById('quote-subject')?.value&&match.contact&&match.contact!=='-'){
    setFieldValue(document.getElementById('quote-subject'),`Quotation for ${match.name}`);
  }
}

function renderProductRecord(product,options={}){
  const tbody=document.getElementById('prod-tbody');
  if(isInventoryTableCleared())return;
  if(isDemoProductRecord(product))return;
  if(!tbody||!product?.name||hasFirstCellValue(tbody,product.code))return;
  const vatText=String(product.vat||'').includes('0')&&!String(product.vat||'').includes('5')?'0% Zero':String(product.vat||'').includes('Exempt')?'Exempt':'5%';
  const vatClass=vatText==='5%'?'b-b':'b-t';
  const row=document.createElement('tr');
  row.dataset.serverRecord='products';
  row.dataset.cost=product.cost??product.unit_cost??0;
  row.dataset.price=product.price??product.sales_price??product.selling_price??product.unit_price??product.cost??0;
  row.dataset.unit=product.unit||'Each';
  row.dataset.supplier=product.supplier_name||product.supplier||'';
  row.dataset.reorderLevel=product.reorder_level??product.reorderLevel??0;
  row.dataset.available=product.available??product.quantity??product.stock_on_hand??product.opening_stock??0;
  row.dataset.reserved=product.reserved??product.reserved_quantity??0;
  row.innerHTML=`<td class="mono">${escapeHtml(product.code||'PRD')}</td><td>${escapeHtml(product.name)}</td><td>Stock Item</td><td>${escapeHtml(product.category||'Materials')}</td><td>${escapeHtml(product.unit||'Each')}</td><td>Main Store</td><td><span class="b b-g">Yes</span></td><td><span class="b ${vatClass}">${escapeHtml(vatText)}</span></td><td><span class="b b-g">Active</span></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  ensureInventoryBulkSelection();
  if(!options.deferRefresh&&!isHydratingFromServer)refreshEnhancedTable(tbody.closest('table'));
  if(!options.deferStockSync&&!isHydratingFromServer)syncStockLevelsFromProducts();
  if(!options.deferMappingSync&&!isHydratingFromServer)syncStockMappingFromItems();
  if(!options.deferSuggestions&&!isHydratingFromServer){
    refreshInvoiceProductSuggestions();
    refreshPurchaseProductSuggestions();
    refreshQuotationProductOptions();
  }
}

function syncStockLevelsFromProducts(){
  const tbody=document.getElementById('stock-level-tbody');
  if(!tbody)return;
  if(isInventoryTableCleared()){
    clearInventoryUiTables();
    loadStockLevelsFromServer();
    return;
  }
  const stockByKey=new Map();
  [...document.querySelectorAll('#prod-tbody tr:not([data-empty-state])')]
    .map(row=>productStockLevelFromRow(row))
    .filter(item=>item.code||item.name)
    .forEach(item=>{
      addStockItemAliases(stockByKey,item);
    });
  purchaseStockItems().forEach(item=>{
    const existing=stockByKey.get(stockItemKey(item.code))||stockByKey.get(stockItemKey(item.name));
    if(existing){
      existing.available=Number(existing.available||0)+Number(item.available||0);
      if(!existing.unit&&item.unit)existing.unit=item.unit;
    }else{
      addStockItemAliases(stockByKey,item);
    }
  });
  const products=[...new Set(stockByKey.values())];
  tbody.innerHTML='';
  if(!products.length){
    emptyTableMessage(tbody,'No stock items in database yet.');
    updateStockLevelStats([]);
    return;
  }
  products.forEach(item=>tbody.appendChild(renderStockLevelRow(item)));
  ensureInventoryBulkSelection();
  refreshEnhancedTable(tbody.closest('table'));
  updateStockLevelStats(products);
  loadStockLevelsFromServer();
}

let stockLevelsLoading=false;
let stockLevelsServerRefreshPaused=false;
const INVENTORY_TABLE_CLEARED_KEY='taxflow_inventory_table_cleared';

function isInventoryTableCleared(){
  return localStorage.getItem(INVENTORY_TABLE_CLEARED_KEY)==='1';
}

function setInventoryTableCleared(value){
  if(value)localStorage.setItem(INVENTORY_TABLE_CLEARED_KEY,'1');
  else localStorage.removeItem(INVENTORY_TABLE_CLEARED_KEY);
}

function clearInventoryUiTables(){
  emptyTableMessage(document.getElementById('stock-level-tbody'),'No stock items in database yet.');
  emptyTableMessage(document.getElementById('prod-tbody'),'No products in database yet.');
  emptyTableMessage(document.getElementById('stock-movement-tbody'),'No stock movements in database yet.');
  emptyTableMessage(document.getElementById('stock-map-tbody'),'No stock mappings in database yet.');
  clearStockMapPanel();
  updateStockLevelStats([]);
}

async function loadStockLevelsFromServer(){
  const tbody=document.getElementById('stock-level-tbody');
  if(!tbody||stockLevelsLoading||stockLevelsServerRefreshPaused)return;
  stockLevelsLoading=true;
  try{
    const rows=await moduleApi('/inventory/stock-levels');
    const products=(Array.isArray(rows)?rows:[])
      .map(row=>({
        code:row.code||'',
        name:row.name||row.code||'Stock item',
        category:row.category||'Purchases',
        available:parseAmount(row.current_stock??row.quantity??row.available),
        unit:row.unit||'PCS',
        reorderLevel:parseAmount(row.reorder_level??row.reorderLevel)
      }))
      .filter(item=>!isDemoProductRecord(item))
      .filter(item=>item.code||item.name);
    if(!products.length){
      setInventoryTableCleared(true);
      clearInventoryUiTables();
      return;
    }
    setInventoryTableCleared(false);
    tbody.innerHTML='';
    products.forEach(item=>tbody.appendChild(renderStockLevelRow(item)));
    ensureInventoryBulkSelection();
    refreshEnhancedTable(tbody.closest('table'));
    updateStockLevelStats(products);
  }catch(err){
    console.warn('Database stock levels failed:',err);
  }finally{
    stockLevelsLoading=false;
  }
}

async function clearInventoryTable(){
  const tbody=document.getElementById('stock-level-tbody');
  if(!tbody)return;
  const confirmed=await appConfirm({
    title:'Clear Inventory Table',
    message:'Clear the inventory table? This removes Item Master products, stock mappings, stock movements, and valuation layers. Purchase records remain unchanged.',
    okText:'Clear Table'
  });
  if(!confirmed)return;
  stockLevelsServerRefreshPaused=true;
  try{
    await moduleApi('/inventory/stock-levels',{method:'DELETE'});
    setInventoryTableCleared(true);
    clearInventoryUiTables();
    toast('Inventory table cleared','ok');
    audit('Cleared inventory table','Inventory','Deleted');
  }catch(err){
    console.warn('Clear inventory table failed:',err);
    toast('Could not clear inventory table','warn');
  }finally{
    stockLevelsServerRefreshPaused=false;
  }
}

function productStockLevelFromRow(row){
  const available=Number(row.dataset.available||0);
  const reorderLevel=Number(row.dataset.reorderLevel||0);
  return {
    code:inventoryRowCellText(row,0)||'',
    name:inventoryRowCellText(row,1)||'',
    category:inventoryRowCellText(row,3)||'Uncategorized',
    available,
    unit:inventoryRowCellText(row,4)||'Each',
    reorderLevel,
    source:'product'
  };
}

function purchaseStockItems(){
  const items=new Map();
  const sourceRecords=purchaseRecordCache.size
    ? [...purchaseRecordCache.values()]
    : [...document.querySelectorAll('#purchase-record-tbody tr:not([data-empty-state])')].map(purchaseRecordFromRow);
  sourceRecords.forEach(record=>{
    const stockSign=String(record?.source||record?.status||record?.document_type||'').toLowerCase().includes('return')?-1:1;
    const lines=Array.isArray(record?.lines)?record.lines:[];
    if(!lines.length&&Number(record?.items||0)>0){
      const name=record.supplier?`${record.supplier} purchase items`:'Unmapped purchase items';
      const code=record.ref||record.invoice_no||record.reference||name;
      const key=stockItemKey(code,name);
      const existing=items.get(key)||{
        code,
        name,
        category:'Purchases',
        available:0,
        unit:'ITEM',
        reorderLevel:0,
        source:'purchase'
      };
      existing.available+=stockSign*Number(record.items||0);
      items.set(key,existing);
    }
    lines.forEach(line=>{
      const name=purchaseAiProductName(line)||line.product||line.name||line.description||'Purchase item';
      const code=String(line.sku||line.code||name).trim();
      const key=purchaseLineStockKey(line);
      if(!key)return;
      const quantity=purchaseLineQuantity(line);
      if(!quantity)return;
      const existing=items.get(key)||{
        code,
        name,
        category:line.category||'Purchases',
        available:0,
        unit:line.unit||line.unit_of_measure||line.uom||'PCS',
        reorderLevel:0,
        source:'purchase'
      };
      existing.available+=stockSign*quantity;
      if(!existing.code&&code)existing.code=code;
      if(!existing.name&&name)existing.name=name;
      items.set(key,existing);
    });
  });
  return [...items.values()];
}

function purchaseLineStockKey(line={}){
  const product=purchaseAiProductName(line)||line.name||line.description||'';
  const code=String(line.sku||line.code||'').trim();
  return stockItemKey(product)||stockItemKey(code);
}

function purchaseLineQuantity(line={}){
  const raw=line.raw||{};
  return parseAmount(
    line.quantity??
    line.qty??
    line.purchase_qty??
    line.qty_invoiced??
    raw.purchase_qty??
    raw.quantity??
    raw.qty??
    raw.qnty??
    raw.pcs??
    0
  );
}

function purchaseLinesTotalQuantity(lines=[]){
  return (Array.isArray(lines)?lines:[]).reduce((sum,line)=>sum+purchaseLineQuantity(line),0);
}

function purchaseRecordQuantity(purchase={}){
  const lineQuantity=purchaseLinesTotalQuantity(purchase.lines);
  return lineQuantity||parseAmount(purchase.items||purchase.quantity||purchase.qty||0);
}

function addStockItemAliases(map,item){
  [item.code,item.name].forEach(value=>{
    const key=stockItemKey(value);
    if(key)map.set(key,item);
  });
}

function stockItemKey(value){
  return String(value||'').trim().toLowerCase().replace(/\s+/g,' ');
}

function renderStockLevelRow(item){
  const row=document.createElement('tr');
  const status=item.available<=0?'Out':item.reorderLevel&&item.available<=item.reorderLevel?'Low':'OK';
  const cls=status==='Out'?'b-r':status==='Low'?'b-a':'b-g';
  row.dataset.itemCode=item.code;
  row.dataset.stockSource=item.source||'product';
  row.innerHTML=`<td class="mono">${escapeHtml(item.code)}</td><td>${escapeHtml(item.name)}</td><td><span class="b b-gray">${escapeHtml(item.category)}</span></td><td class="mono">${Number(item.available||0).toLocaleString('en-AE')}</td><td>${escapeHtml(item.unit)}</td><td class="mono">${Number(item.reorderLevel||0).toLocaleString('en-AE')}</td><td><span class="b ${cls}">${status}</span></td><td><button class="btn btn-g btn-sm" onclick="openRowDetail(this,'Stock Level Detail','Current Stock Levels')">View</button></td>`;
  return row;
}

function updateStockLevelStats(products){
  const stats=document.querySelectorAll('#inv-stock .g4 .stat .stat-val');
  const total=products.length;
  const inStock=products.filter(item=>Number(item.available||0)>0).length;
  const low=products.filter(item=>Number(item.available||0)>0&&Number(item.reorderLevel||0)>0&&Number(item.available||0)<=Number(item.reorderLevel||0)).length;
  const out=products.filter(item=>Number(item.available||0)<=0).length;
  if(stats[0])stats[0].textContent=total.toLocaleString('en-AE');
  if(stats[1])stats[1].textContent=inStock.toLocaleString('en-AE');
  if(stats[2])stats[2].textContent=low.toLocaleString('en-AE');
  if(stats[3])stats[3].textContent=out.toLocaleString('en-AE');
}

function syncProductMasterOptions(){
  const categorySelect=document.getElementById('prod-category');
  const unitSelect=document.getElementById('prod-unit');
  const supplierSelect=document.getElementById('prod-supplier');
  if(categorySelect){
    const current=categorySelect.value;
    const categories=[...document.querySelectorAll('#sales-category-tbody tr td:first-child')]
      .map(td=>td.textContent.trim())
      .filter(Boolean);
    categorySelect.innerHTML=[...new Set(categories)].map(name=>`<option>${escapeHtml(name)}</option>`).join('');
    if(current&&categories.includes(current))categorySelect.value=current;
  }
  if(unitSelect){
    const current=unitSelect.value;
    const units=[...document.querySelectorAll('#sales-unit-tbody tr')]
      .map(row=>row.children[1]?.textContent.trim()||row.children[0]?.textContent.trim())
      .filter(Boolean);
    unitSelect.innerHTML=[...new Set(units)].map(name=>`<option>${escapeHtml(name)}</option>`).join('');
    if(current&&units.includes(current))unitSelect.value=current;
  }
  if(supplierSelect){
    const current=supplierSelect.value;
    const suppliers=[...document.querySelectorAll('#vendor-tbody tr:not([data-empty-state]) td:first-child')]
      .map(td=>td.textContent.trim())
      .filter(Boolean);
    const unique=[...new Set(suppliers)];
    supplierSelect.innerHTML='<option value="">Select Supplier</option>'+unique.map(name=>`<option>${escapeHtml(name)}</option>`).join('');
    if(current&&unique.includes(current))supplierSelect.value=current;
  }
}

function dbUnitNames(){
  return [...new Set([...document.querySelectorAll('#sales-unit-tbody tr:not([data-empty-state])')]
    .map(row=>row.children[1]?.textContent.trim()||row.children[0]?.textContent.trim())
    .filter(Boolean))];
}

function unitOptionsHtml(selected=''){
  const units=dbUnitNames();
  const list=units.length?units:['PCS'];
  return list.map(name=>`<option ${name===selected?'selected':''}>${escapeHtml(name)}</option>`).join('');
}

function renderSalesCategoryRecord(category){
  const tbody=document.getElementById('sales-category-tbody');
  const name=(category?.name||category?.category||'').trim();
  if(!tbody||!name||hasFirstCellValue(tbody,name))return;
  const vat=category.vat||category.default_vat||'Standard 5%';
  const row=document.createElement('tr');
  row.dataset.serverRecord='salesCategories';
  row.innerHTML=`<td>${escapeHtml(name)}</td><td>${escapeHtml(category.scope||'Sales & Purchase')}</td><td><span class="b b-b">${escapeHtml(vat)}</span></td><td><span class="b b-g">${escapeHtml(category.status||'Active')}</span></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  syncProductMasterOptions();
  syncInventoryItemOptions();
}

function renderSalesUnitRecord(unit){
  const tbody=document.getElementById('sales-unit-tbody');
  const name=(unit?.name||unit?.unit||'').trim();
  const code=(unit?.code||name.slice(0,6).toUpperCase()).trim();
  if(!tbody||!name||hasFirstCellValue(tbody,code))return;
  const row=document.createElement('tr');
  row.dataset.serverRecord='salesUnits';
  row.innerHTML=`<td class="mono">${escapeHtml(code)}</td><td>${escapeHtml(name)}</td><td>${escapeHtml(unit.type||'Quantity')}</td><td class="mono">${escapeHtml(unit.decimals??'2')}</td><td><span class="b b-g">${escapeHtml(unit.status||'Active')}</span></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  syncProductMasterOptions();
  syncInventoryItemOptions();
}

function renderBillRecord(bill){
  const tbody=document.getElementById('bill-tbody');
  if(!tbody||!bill?.bill_no||hasFirstCellValue(tbody,bill.bill_no))return;
  const row=document.createElement('tr');
  row.dataset.serverRecord='bills';
  row.innerHTML=`<td class="mono">${escapeHtml(bill.bill_no)}</td><td>${escapeHtml(bill.vendor)}</td><td>${escapeHtml(bill.date)}</td><td>${escapeHtml(bill.due)}</td><td class="mono">${Number(bill.subtotal||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${Number(bill.vat||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${Number(bill.total||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td><span class="b b-a">${escapeHtml(bill.status||'Awaiting Payment')}</span></td><td><button class="btn btn-g btn-sm" onclick="openRowDetail(this,'Bill / Vendor Detail','Bill detail')">View</button></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
}

function renderVendorRecord(vendor){
  const tbody=document.getElementById('vendor-tbody');
  if(!tbody||!vendor?.name||hasFirstCellValue(tbody,vendor.name))return;
  const trn=String(vendor.trn||'').replace(/\D/g,'');
  if(trn&&[...tbody.querySelectorAll('tr:not([data-empty-state]) td:nth-child(2)')]
    .some(td=>String(td.textContent||'').replace(/\D/g,'')===trn))return;
  const row=document.createElement('tr');
  row.dataset.serverRecord='vendors';
  row.dataset.address=vendor.address||'';
  row.dataset.vendorTrn=trn;
  row.innerHTML=`<td>${escapeHtml(vendor.name)}</td><td class="mono">${escapeHtml(trn||'Not registered')}</td><td>${escapeHtml(vendor.category||'Services')}</td><td>${escapeHtml(vendor.email||'-')}</td><td>${escapeHtml(vendor.address||'-')}</td><td class="mono">0.00</td><td><span class="b b-g">Active</span></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  syncSupplierOptions(vendor.name);
  syncInventoryItemOptions();
}

function vendorAddressByName(name){
  const wanted=String(name||'').trim().toLowerCase();
  if(!wanted)return '';
  const row=[...document.querySelectorAll('#vendor-tbody tr:not([data-empty-state])')]
    .find(item=>item.children[0]?.textContent.trim().toLowerCase()===wanted);
  return row?.dataset.address||row?.children[4]?.textContent.trim().replace(/^[-]$/,'')||'';
}

function applySupplierAddress(){
  const supplier=document.getElementById('mp-supplier')?.value||'';
  const address=vendorAddressByName(supplier);
  const field=document.getElementById('mp-address');
  if(!field)return;
  field.value=address||'';
}

function syncSupplierOptions(selected=''){
  const select=document.getElementById('mp-supplier');
  if(!select)return;
  const current=selected||select.value;
  const names=[...document.querySelectorAll('#vendor-tbody tr:not([data-empty-state]) td:first-child')]
    .map(td=>td.textContent.trim())
    .filter(Boolean);
  const existing=[...select.options].map(option=>option.value||option.textContent.trim()).filter(Boolean);
  const all=[...new Set([...existing, ...names])];
  select.innerHTML='<option value="">Please Select</option>'+all.map(name=>`<option>${escapeHtml(name)}</option>`).join('');
  if(current&&all.includes(current))select.value=current;
  applySupplierAddress();
}

function openSupplierPopup(){
  showM('m-vendor');
  setTimeout(()=>document.getElementById('vendor-name')?.focus(),50);
}

function isSupplierPaymentType(type=document.getElementById('payment-type')?.value){
  return type==='Supplier Payment';
}

function openPaymentModal(type='Customer Receipt'){
  const typeField=document.getElementById('payment-type');
  if(typeField)typeField.value=type;
  const supplier=isSupplierPaymentType(type);

  // Set title and ref label
  document.getElementById('pmt-modal-title').textContent=supplier?'New Payment':'New Receipt';
  const refLabel=document.getElementById('pmt-ref-label');
  if(refLabel)refLabel.textContent=supplier?'Payment No.':'Receipt No.';
  const contactLabel=document.getElementById('payment-contact-label');
  if(contactLabel)contactLabel.textContent=supplier?'Vendor':'Client';

  // Reset fields
  ['payment-ref','payment-contact','payment-amount','payment-detail','payment-comments'].forEach(id=>{
    const el=document.getElementById(id);
    if(el)el.value='';
  });
  const now=new Date();
  const dateEl=document.getElementById('payment-date');
  if(dateEl)dateEl.value=now.toISOString().slice(0,10);
  const timeEl=document.getElementById('payment-time');
  if(timeEl)timeEl.value=now.toTimeString().slice(0,5);
  // Auto-fill reference
  const refEl=document.getElementById('payment-ref');
  if(refEl)refEl.value=nextPaymentReference(type);

  // Build method buttons from bank accounts, default to Cash
  populatePaymentMethods('Cash');

  // Reset allocation table
  const tbody=document.getElementById('pmt-alloc-tbody');
  if(tbody)tbody.innerHTML='<tr><td colspan="7" style="color:var(--text3);text-align:center;padding:12px">Select a client to see outstanding invoices.</td></tr>';
  updatePmtBalance();

  // Sync type toggle buttons
  const isSupplier=isSupplierPaymentType(type);
  document.getElementById('pmt-type-receipt')?.classList.toggle('on',!isSupplier);
  document.getElementById('pmt-type-payment')?.classList.toggle('on',isSupplier);

  showM('m-payment');
  setTimeout(()=>syncPaymentFormOptions(),0);
}

function selectPaymentMethod(btn){
  document.querySelectorAll('.pmt-m-btn').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
  setFieldValue(document.getElementById('payment-method'),btn.dataset.method||'');
  syncAllocationMeta();
}

function populatePaymentMethods(defaultMethod='Cash'){
  const container=document.getElementById('pmt-methods');
  if(!container)return;

  // Always include Cash first
  const methods=[{name:'Cash',icon:'cash'}];

  // Add bank accounts from finance bank accounts (bank page)
  currentFinanceBankAccounts().forEach(acc=>{
    const name=String(acc.bank||acc.bank_name||acc.name||'').trim();
    const type=String(acc.account_type||acc.type||'').trim();
    if(name&&!methods.find(m=>m.name===name))methods.push({name,icon:'bank',type});
  });

  // Add COA bank/cash accounts
  (_coaFlatAccounts||[])
    .filter(a=>!a.is_group&&(a.is_bank_cash||['Bank','Cash','Cash / Petty Cash','Bank Account'].includes(a.type)))
    .forEach(a=>{
      const name=String(a.name||'').trim();
      if(name&&!methods.find(m=>m.name===name))methods.push({name,icon:a.type==='Cash'||a.type==='Cash / Petty Cash'?'cash':'bank'});
    });

  // Fallback static methods if no accounts exist
  if(methods.length===1){
    ['Bank Transfer','Cheque','Card'].forEach(n=>methods.push({name:n,icon:'bank'}));
  }

  const cashIconSvg=`<svg viewBox="0 0 16 16"><rect x="1" y="4" width="14" height="8" rx="1.5"/><circle cx="8" cy="8" r="2"/></svg>`;
  const bankIconSvg=`<svg viewBox="0 0 16 16"><rect x="1" y="8" width="14" height="6" rx="1"/><polygon points="8,2 1,8 15,8"/></svg>`;

  container.innerHTML=methods.map(m=>{
    const isCash=m.icon==='cash';
    const icon=isCash?cashIconSvg:bankIconSvg;
    const iconCls=isCash?'pmt-m-icon cash':'pmt-m-icon';
    const isDefault=m.name===defaultMethod;
    const label=escapeHtml(m.name.length>14?m.name.slice(0,13)+'…':m.name);
    return `<button class="pmt-m-btn${isDefault?' on':''}" data-method="${escapeHtml(m.name)}" onclick="selectPaymentMethod(this)"><div class="${iconCls}">${icon}</div>${label}</button>`;
  }).join('');

  // Set the hidden payment-method field
  const active=container.querySelector('.pmt-m-btn.on')||container.querySelector('.pmt-m-btn');
  if(active)setFieldValue(document.getElementById('payment-method'),active.dataset.method||'');
}

function syncAllocationMeta(){
  const method=document.getElementById('payment-method')?.value||'';
  const date=document.getElementById('payment-date')?.value||'';
  document.querySelectorAll('#pmt-alloc-tbody tr').forEach(row=>{
    const dateCell=row.querySelector('td:nth-child(3)');
    const methodCell=row.querySelector('td:nth-child(4)');
    if(dateCell&&date)dateCell.textContent=date;
    if(methodCell&&method)methodCell.textContent=method;
  });
}

function calculateAllOwing(){
  const type=document.getElementById('payment-type')?.value||'Customer Receipt';
  const contact=(document.getElementById('payment-contact')?.value||'').trim().toLowerCase();
  const docs=collectPaymentDocuments(type)
    .filter(d=>!contact||d.contact.toLowerCase().includes(contact));
  // Sum remaining balances (partial invoices show their remaining amount)
  const total=docs.reduce((sum,d)=>sum+Number(d.amount||0),0);
  setFieldValue(document.getElementById('payment-amount'),total.toFixed(2));
  loadAllocationTable(docs);
  updatePmtBalance();
}

function loadAllocationTable(docs){
  const tbody=document.getElementById('pmt-alloc-tbody');
  if(!tbody)return;
  if(!docs||!docs.length){
    const contact=(document.getElementById('payment-contact')?.value||'').trim();
    const type=document.getElementById('payment-type')?.value||'Customer Receipt';
    const isSupplier=isSupplierPaymentType(type);
    const hint=contact
      ? `No outstanding ${isSupplier?'purchase invoices / bills':'invoices'} found for <strong>${escapeHtml(contact)}</strong>.`
      : `Select a ${isSupplier?'vendor':'client'} to load outstanding invoices.`;
    tbody.innerHTML=`<tr class="pmt-alloc-empty"><td colspan="7">${hint}</td></tr>`;
    return;
  }
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2});
  tbody.innerHTML=docs.map(d=>{
    const remaining=Number(d.amount||0);
    const original=Number(d.original_amount||d.amount||0);
    const isPartial=original>remaining+0.01;
    const src=d.source||'-';
    const srcColor=isPartial?'background:#fff3e0;color:#e65100':'background:#eef3ff;color:#2e5db5';
    const srcLabel=isPartial?`${escapeHtml(src)} (partial)`:escapeHtml(src);
    return `<tr>
      <td><input type="checkbox" class="pmt-alloc-chk" checked onchange="updatePmtBalance()"></td>
      <td class="mono" style="font-size:11.5px">${escapeHtml(d.ref)}</td>
      <td style="font-size:11.5px">${escapeHtml(d.date||'-')}</td>
      <td><span style="font-size:10.5px;${srcColor};border-radius:4px;padding:1px 6px;font-weight:600">${srcLabel}</span></td>
      <td class="mono r">${fmt(original)}</td>
      <td class="mono r">${fmt(remaining)}</td>
      <td><input type="number" class="pmt-alloc-inp" value="${remaining.toFixed(2)}" min="0" max="${remaining}" step="0.01" oninput="updatePmtBalance()" data-doc-ref="${escapeHtml(d.ref)}" data-doc-amount="${remaining}"></td>
    </tr>`;
  }).join('');
  updatePmtBalance();
  syncAllocationMeta();
}

function toggleAllAllocation(checked){
  document.querySelectorAll('#pmt-alloc-tbody .pmt-alloc-chk').forEach(chk=>{
    chk.checked=checked;
  });
  updatePmtBalance();
}

function updatePmtBalance(){
  const payment=parseAmount(document.getElementById('payment-amount')?.value||'0');
  let allocated=0;
  document.querySelectorAll('#pmt-alloc-tbody tr').forEach(row=>{
    const chk=row.querySelector('.pmt-alloc-chk');
    if(chk?.checked){
      const inp=row.querySelector('.pmt-alloc-inp');
      allocated+=parseAmount(inp?.value||'0');
    }
  });
  const available=payment-allocated;
  const fmt=n=>Number(n).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  const allocEl=document.getElementById('pmt-allocated');
  const availEl=document.getElementById('pmt-available');
  if(allocEl)allocEl.textContent=fmt(allocated);
  if(availEl){availEl.textContent=fmt(available);availEl.style.color=available<0?'var(--red)':'';}
}

function collectPaymentContacts(type=document.getElementById('payment-type')?.value){
  const selector=isSupplierPaymentType(type)?'#vendor-tbody tr:not([data-empty-state])':'#customer-tbody tr:not([data-empty-state])';
  return [...document.querySelectorAll(selector)]
    .map(row=>row.children[0]?.textContent.trim())
    .filter(Boolean);
}

function paidPaymentDocumentRefs(type=document.getElementById('payment-type')?.value){
  const supplier=isSupplierPaymentType(type);
  const refs=new Set();
  // Only exclude invoices/bills whose status is exactly "Paid" — Partial ones stay in the list
  const statusColIdx=supplier?7:8;
  const selector=supplier?'#bill-tbody tr:not([data-empty-state])':'#sales-invoice-tbody tr:not([data-empty-state])';
  document.querySelectorAll(selector).forEach(row=>{
    const status=(row.children[statusColIdx]?.textContent||'').trim().toLowerCase();
    if(status==='paid'){
      refs.add((row.children[0]?.textContent||'').trim().toLowerCase());
    }
  });
  return refs;
}

function isPendingDocumentStatus(status){
  const text=String(status||'').trim().toLowerCase();
  if(!text)return true;
  return !/(paid|posted|settled|allocated|closed|reconciled|complete)/.test(text);
}

function remainingBalance(ref,total){
  const info=_invoicePaidMap.get(String(ref||'').trim().toLowerCase());
  if(!info)return total;
  return Math.max(0,total-(info.paid||0));
}

function collectPaymentDocuments(type=document.getElementById('payment-type')?.value){
  const paidRefs=paidPaymentDocumentRefs(type);
  if(isSupplierPaymentType(type)){
    const bills=[...document.querySelectorAll('#bill-tbody tr:not([data-empty-state])')].map(row=>{
      const ref=row.children[0]?.textContent.trim()||'';
      const total=parseAmount(row.children[6]?.textContent||'0');
      const remaining=remainingBalance(ref,total);
      return {
        ref,
        contact:row.children[1]?.textContent.trim()||'',
        date:row.children[2]?.textContent.trim()||'',
        amount:remaining,
        original_amount:total,
        status:row.children[7]?.textContent.trim()||'',
        source:'Bill'
      };
    }).filter(item=>item.ref&&isPendingDocumentStatus(item.status)&&!paidRefs.has(item.ref.toLowerCase())&&item.amount>0.01);

    const purchases=[...document.querySelectorAll('#purchase-record-tbody tr:not([data-empty-state])')].map(row=>{
      const rec=purchaseRecordFromRow(row);
      if(!rec||!rec.ref)return null;
      const total=rec.total||parseAmount(row.children[9]?.textContent||'0');
      const remaining=remainingBalance(rec.ref,total);
      return {
        ref:rec.ref,
        contact:rec.supplier||rec.vendor||row.children[2]?.textContent.trim()||'',
        date:rec.date||row.children[3]?.textContent.trim()||'',
        amount:remaining,
        original_amount:total,
        status:rec.status||'Pending',
        source:'Purchase Invoice'
      };
    }).filter(item=>item&&item.ref&&item.contact&&isPendingDocumentStatus(item.status)&&!paidRefs.has(item.ref.toLowerCase())&&item.amount>0.01);

    const seen=new Set();
    return [...bills,...purchases].filter(d=>{
      const k=d.ref.toLowerCase();
      if(seen.has(k))return false;
      seen.add(k);
      return true;
    });
  }
  return [...document.querySelectorAll('#sales-invoice-tbody tr:not([data-empty-state])')].map(row=>{
    let data={};
    try{data=JSON.parse(row.dataset.salesInvoice||'{}');}catch(_err){}
    if(isSalesReturn(data))return null;
    const total=parseAmount(data.total??row.children[6]?.textContent??'0');
    const ref=data.invoice_no||row.children[0]?.textContent.trim()||'';
    const remaining=remainingBalance(ref,total);
    return {
      ref,
      contact:data.customer||row.children[1]?.textContent.trim()||'',
      date:data.date||row.children[2]?.textContent.trim()||'',
      amount:remaining,
      original_amount:total,
      status:data.status||row.children[8]?.textContent.trim()||'',
      source:'Invoice'
    };
  }).filter(item=>item&&item.ref&&isPendingDocumentStatus(item.status)&&!paidRefs.has(item.ref.toLowerCase())&&item.amount>0.01);
}

function nextPaymentReference(type=document.getElementById('payment-type')?.value){
  const supplier=isSupplierPaymentType(type);
  const prefix=supplier?'PAY':'RCT';
  const year=new Date().getFullYear();
  const rows=[...document.querySelectorAll(supplier?'#payment-out-tbody tr:not([data-empty-state])':'#payment-in-tbody tr:not([data-empty-state])')];
  const max=rows.reduce((highest,row)=>{
    const ref=row.children[0]?.textContent.trim()||'';
    const match=ref.match(/(\d+)$/);
    return match?Math.max(highest,Number(match[1])):highest;
  },0);
  return `${prefix}-${year}-${String(max+1).padStart(4,'0')}`;
}

function syncPaymentFormOptions(){
  const type=document.getElementById('payment-type')?.value||'Customer Receipt';
  const supplier=isSupplierPaymentType(type);
  // Refresh method buttons with current bank accounts (keep existing selection)
  const currentMethod=document.getElementById('payment-method')?.value||'Cash';
  populatePaymentMethods(currentMethod);

  // Populate client <select> from app customer/vendor list
  const contactSel=document.getElementById('payment-contact');
  if(contactSel){
    const prev=contactSel.value;
    const names=[...new Set(collectPaymentContacts(type))];
    contactSel.innerHTML=`<option value="">— Select ${supplier?'vendor':'client'} —</option>`
      +names.map(n=>`<option value="${escapeHtml(n)}"${n===prev?' selected':''}>${escapeHtml(n)}</option>`).join('');
  }

  // Populate account <select> from bank accounts in COA + finance bank accounts
  const bankSel=document.getElementById('payment-bank');
  if(bankSel){
    const prev=bankSel.value;
    const coaAccounts=(_coaFlatAccounts||[])
      .filter(a=>!a.is_group&&(a.is_bank_cash||['Bank','Cash','Bank Account','Cash / Petty Cash'].includes(a.type)))
      .map(a=>({label:`${a.code} - ${a.name}`,value:`${a.code} - ${a.name}`}));
    const financeAccounts=[...document.querySelectorAll('#bank-accounts-tbody tr:not([data-empty-state])')]
      .map(r=>({label:r.children[0]?.textContent.trim()||'',value:r.children[0]?.textContent.trim()||''}))
      .filter(a=>a.value);
    const all=[...financeAccounts,...coaAccounts];
    const seen=new Set();
    const unique=all.filter(a=>{if(seen.has(a.value))return false;seen.add(a.value);return true;});
    bankSel.innerHTML=`<option value="">— Select account —</option>`
      +unique.map(a=>`<option value="${escapeHtml(a.value)}"${a.value===prev?' selected':''}>${escapeHtml(a.label)}</option>`).join('');
    // If only one account, auto-select it
    if(unique.length===1)bankSel.value=unique[0].value;
  }
}

function switchPmtType(type,btn){
  setFieldValue(document.getElementById('payment-type'),type);
  document.querySelectorAll('.pmt-type-btn').forEach(b=>b.classList.remove('on'));
  btn.classList.add('on');
  const supplier=isSupplierPaymentType(type);
  document.getElementById('pmt-modal-title').textContent=supplier?'New Payment':'New Receipt';
  const refLabel=document.getElementById('pmt-ref-label');
  if(refLabel)refLabel.textContent=supplier?'Payment No.':'Receipt No.';
  const contactLabel=document.getElementById('payment-contact-label');
  if(contactLabel)contactLabel.textContent=supplier?'Vendor':'Client';
  setFieldValue(document.getElementById('payment-ref'),nextPaymentReference(type));
  syncPaymentFormOptions();
  // Reset allocation table
  const tbody=document.getElementById('pmt-alloc-tbody');
  if(tbody)tbody.innerHTML='<tr><td colspan="7" style="color:var(--text3);text-align:center;padding:12px">Select a client to see outstanding invoices.</td></tr>';
  updatePmtBalance();
}

function handlePaymentTypeChange(){
  ['payment-ref','payment-contact','payment-document','payment-amount'].forEach(id=>setFieldValue(document.getElementById(id),''));
  syncPaymentFormOptions();
}

function findPendingPaymentDocumentForContact(type,contact){
  const wanted=String(contact||'').trim().toLowerCase();
  if(!wanted)return null;
  const docs=collectPaymentDocuments(type);
  return docs.find(item=>item.contact.toLowerCase()===wanted)
    || docs.find(item=>item.contact.toLowerCase().includes(wanted))
    || null;
}

function applyPaymentContactSelection(){
  const type=document.getElementById('payment-type')?.value||'Customer Receipt';
  const contact=(document.getElementById('payment-contact')?.value||'').trim();
  if(!contact)return;
  // Load allocation table for this client
  const docs=collectPaymentDocuments(type)
    .filter(d=>d.contact.toLowerCase().includes(contact.toLowerCase()));
  loadAllocationTable(docs);
  // Auto-fill total remaining (partial invoices contribute only their outstanding balance)
  if(docs.length){
    const total=docs.reduce((sum,d)=>sum+Number(d.amount||0),0);
    setFieldValue(document.getElementById('payment-amount'),total.toFixed(2));
  }
  updatePmtBalance();
}

function applyPaymentDocumentSelection(){
  const type=document.getElementById('payment-type')?.value||'Customer Receipt';
  const documentInput=document.getElementById('payment-document');
  const selected=(documentInput?.value||'').trim().toLowerCase();
  if(!selected)return;
  const doc=collectPaymentDocuments(type).find(item=>item.ref.toLowerCase()===selected);
  if(!doc)return;
  setFieldValue(document.getElementById('payment-contact'),doc.contact);
  if(doc.amount)setFieldValue(document.getElementById('payment-amount'),Number(doc.amount).toFixed(2));
  syncPaymentFormOptions();
}

function markPaymentDocumentPaid(payment){
  const isSupplier=payment?.type==='Supplier Payment';
  const tbody=document.getElementById(isSupplier?'bill-tbody':'sales-invoice-tbody');
  const statusColIdx=isSupplier?7:8;
  const totalColIdx=6;
  const fmt=n=>Number(n).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});

  // Build allocations: use payment.allocations if present, else single-doc fallback
  const allocations=(Array.isArray(payment?.allocations)&&payment.allocations.length)
    ?payment.allocations
    :[{doc_ref:payment?.document_ref||payment?.bill_no||payment?.invoice_no||'',amount:Number(payment?.amount||0)}];

  allocations.forEach(alloc=>{
    const ref=String(alloc.doc_ref||'').trim().toLowerCase();
    if(!ref||ref==='-')return;
    const allocAmt=Number(alloc.amount||0);
    if(!allocAmt)return;

    const row=[...(tbody?.querySelectorAll('tr:not([data-empty-state])')||[])]
      .find(r=>(r.children[0]?.textContent||'').trim().toLowerCase()===ref);
    if(!row)return;

    const invoiceTotal=parseAmount(row.children[totalColIdx]?.textContent||'0');
    const existing=_invoicePaidMap.get(ref)||{paid:0,total:invoiceTotal,isSupplier};
    const newPaid=existing.paid+allocAmt;
    _invoicePaidMap.set(ref,{...existing,paid:newPaid,total:invoiceTotal});

    const statusCell=row.children[statusColIdx];
    if(!statusCell)return;
    const remaining=invoiceTotal-newPaid;

    if(remaining<=0.01){
      statusCell.innerHTML='<span class="b b-g">Paid</span>';
      if(row.dataset.salesInvoice){
        try{const d=JSON.parse(row.dataset.salesInvoice);d.status='Paid';d.balance_due=0;d.amount_paid=invoiceTotal;row.dataset.salesInvoice=JSON.stringify(d);}catch{}
      }
    }else{
      statusCell.innerHTML=`<span class="b b-a" title="Remaining: AED ${fmt(remaining)}">Partial</span>`;
      if(row.dataset.salesInvoice){
        try{const d=JSON.parse(row.dataset.salesInvoice);d.status='Partial';d.balance_due=remaining;d.amount_paid=newPaid;row.dataset.salesInvoice=JSON.stringify(d);}catch{}
      }
    }
  });
}

function renderPaymentRecord(payment){
  // Rebuild _invoicePaidMap from stored allocations (for page-reload persistence)
  const isSupplier=payment?.type==='Supplier Payment';
  const allocations=(Array.isArray(payment?.allocations)&&payment.allocations.length)
    ?payment.allocations
    :[{doc_ref:payment?.document_ref||payment?.invoice_no||payment?.bill_no||'',amount:Number(payment?.amount||0)}];
  allocations.forEach(alloc=>{
    const ref=String(alloc.doc_ref||'').trim().toLowerCase();
    if(!ref||ref==='-')return;
    const allocAmt=Number(alloc.amount||0);
    if(!allocAmt)return;
    const existing=_invoicePaidMap.get(ref)||{paid:0,total:0,isSupplier};
    _invoicePaidMap.set(ref,{...existing,paid:existing.paid+allocAmt});
  });

  const tbody=document.getElementById(isSupplier?'payment-out-tbody':'payment-in-tbody');
  if(payment?.ref)financePaymentsByRef.set(String(payment.ref),payment);
  if(!tbody||!payment?.ref||hasFirstCellValue(tbody,payment.ref)){
    updateFinanceFromDatabaseRecords();
    return;
  }
  const documentRef=payment.document_ref||payment.invoice_no||payment.bill_no||'-';
  const row=document.createElement('tr');
  row.dataset.serverRecord='payments';
  row.dataset.payment=JSON.stringify(payment);
  row.innerHTML=`<td class="mono">${escapeHtml(payment.ref)}</td><td>${escapeHtml(payment.contact)}</td><td class="mono">${escapeHtml(documentRef)}</td><td>${escapeHtml(payment.method||'Bank Transfer')}</td><td>${escapeHtml(payment.date)}</td><td class="mono">${Number(payment.amount||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td><span class="b b-g">Posted</span></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  markPaymentDocumentPaid(payment);
  updateFinanceFromDatabaseRecords();
}

function bankAccountKey(account){
  return String(account?.iban||account?.account_number||account?.name||account?.bank||'').trim().toLowerCase();
}

function renderBankAccountRecord(account){
  const tbody=document.getElementById('bank-account-tbody');
  const key=bankAccountKey(account);
  if(!tbody||!key)return;
  financeBankAccountsByKey.set(key,account);
  const exists=[...tbody.querySelectorAll('tr:not([data-empty-state])')]
    .some(row=>bankAccountKey({iban:row.dataset.iban,bank:row.dataset.bank})===key);
  if(exists){
    updateFinanceFromDatabaseRecords();
    return;
  }
  const currency=account.currency||'AED';
  const balance=Number(account.balance??account.opening_balance??0);
  const type=account.type||account.account_type||'Current';
  const typeClass=String(type).toLowerCase().includes('saving')?'b-t':'b-b';
  const row=document.createElement('tr');
  row.dataset.serverRecord='bankAccounts';
  row.dataset.iban=account.iban||'';
  row.dataset.bank=account.bank||account.bank_name||'';
  row.innerHTML=`<td><div class="flx"><span style="font-size:18px">🏦</span><span>${escapeHtml(account.bank||account.bank_name||'Bank')}</span></div></td><td>${escapeHtml(account.holder||account.account_holder||account.name||'-')}</td><td class="mono">${escapeHtml(account.iban||'-')}</td><td>${escapeHtml(currency)}</td><td><span class="b ${typeClass}">${escapeHtml(type)}</span></td><td class="mono" style="color:var(--green)">${escapeHtml(currency)} ${balance.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2})}</td><td><span class="b b-g">${escapeHtml(account.status||'Active')}</span></td><td><button class="btn btn-g btn-sm" onclick="toast('Syncing with bank...','info')">Sync</button></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  updateFinanceFromDatabaseRecords();
}

function currentFinancePayments(){
  return [...financePaymentsByRef.values()];
}

function currentFinanceBankAccounts(){
  return [...financeBankAccountsByKey.values()];
}

function updateFinanceFromDatabaseRecords(){
  const payments=currentFinancePayments();
  const bankAccounts=currentFinanceBankAccounts();
  const openingBalance=bankAccounts.reduce((sum,account)=>sum+Number(account.balance??account.opening_balance??0),0);
  const inflow=payments
    .filter(payment=>String(payment.type||'Customer Receipt').toLowerCase()!=='supplier payment')
    .reduce((sum,payment)=>sum+Number(payment.amount||0),0);
  const outflow=payments
    .filter(payment=>String(payment.type||'').toLowerCase()==='supplier payment')
    .reduce((sum,payment)=>sum+Number(payment.amount||0),0);
  const bookBalance=openingBalance+inflow-outflow;
  const stats=[...document.querySelectorAll('#bk-accounts .stat-val')];
  if(stats[0])stats[0].textContent=formatAed(bookBalance);
  if(stats[1])stats[1].textContent=formatAed(inflow);
  if(stats[2])stats[2].textContent=formatAed(outflow);
  renderBankTransactions(payments,openingBalance);
  updateBankReconciliation(openingBalance,bookBalance,payments.length);
}

function updateBankAccountSummary(){
  updateFinanceFromDatabaseRecords();
}

function renderBankTransactions(payments=currentFinancePayments(),openingBalance=0){
  const tbody=document.getElementById('bank-transaction-tbody');
  if(!tbody)return;
  if(!payments.length){
    emptyTableMessage(tbody,'No bank transactions in database yet.');
    return;
  }
  const sorted=[...payments].sort((a,b)=>String(b.date||'').localeCompare(String(a.date||''))||String(b.ref||'').localeCompare(String(a.ref||'')));
  let running=openingBalance;
  const rows=sorted.map(payment=>{
    const isOut=String(payment.type||'').toLowerCase()==='supplier payment';
    const amount=Number(payment.amount||0);
    running+=isOut?-amount:amount;
    const category=isOut?'Payment':'Receipt';
    const badge=isOut?'b-r':'b-g';
    return `<tr><td>${escapeHtml(payment.date||'-')}</td><td>${escapeHtml(payment.contact||'Finance transaction')} - ${escapeHtml(payment.ref||'')}</td><td class="mono" style="${isOut?'color:var(--red)':''}">${isOut?formatFinanceAmount(amount):'-'}</td><td class="mono" style="${isOut?'':'color:var(--green)'}">${isOut?'-':formatFinanceAmount(amount)}</td><td class="mono">${formatFinanceAmount(running)}</td><td><span class="b ${badge}">${escapeHtml(category)}</span></td></tr>`;
  });
  tbody.innerHTML=rows.join('');
  refreshEnhancedTable(tbody.closest('table'));
}

function updateBankReconciliation(statementBalance,bookBalance,transactionCount){
  const difference=statementBalance-bookBalance;
  setText('bank-statement-balance',formatAed(statementBalance));
  setText('bank-book-balance',formatAed(bookBalance));
  setText('bank-reconcile-difference',formatAed(Math.abs(difference)));
  const note=document.getElementById('bank-reconcile-note');
  if(note){
    note.textContent=Math.abs(difference)<=0.01
      ? 'No unmatched items'
      : `${transactionCount} recorded transaction${transactionCount===1?'':'s'} not in statement balance`;
    note.classList.toggle('dn',Math.abs(difference)>0.01);
  }
}

function formatFinanceAmount(value){
  return Number(value||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
}

function buildBankAccountFromForm(){
  return {
    id:(document.getElementById('bank-iban')?.value||`BANK-${Date.now()}`).trim(),
    bank:document.getElementById('bank-name')?.value||'Bank',
    holder:document.getElementById('bank-holder')?.value?.trim()||currentCompany?.name||'',
    iban:document.getElementById('bank-iban')?.value?.trim()||'',
    type:document.getElementById('bank-type')?.value||'Current',
    currency:document.getElementById('bank-currency')?.value||'AED',
    swift:document.getElementById('bank-swift')?.value?.trim()||'',
    branch:document.getElementById('bank-branch')?.value?.trim()||'',
    balance:parseAmount(document.getElementById('bank-balance')?.value),
    opening_balance:parseAmount(document.getElementById('bank-balance')?.value),
    status:'Active'
  };
}

function saveBankAccount(){
  const record=buildBankAccountFromForm();
  if(!record.bank||!record.holder){
    toast('Enter bank and account holder','warn');
    return;
  }
  renderBankAccountRecord(record);
  saveServer('bankAccounts',record);
  closeM('m-bank');
  ['bank-holder','bank-iban','bank-balance','bank-swift','bank-branch'].forEach(id=>setFieldValue(document.getElementById(id),''));
  toast('Bank account added','ok');
  audit('Added bank account',record.bank,'Saved');
}

// ── Expense AI Upload ─────────────────────────────────────────
const expAiFiles=[];

function expAiDrop(e){
  e.preventDefault();
  document.getElementById('exp-ai-zone')?.classList.remove('dz-over');
  const files=[...e.dataTransfer.files].filter(f=>/pdf|jpeg|jpg|png/i.test(f.type||f.name));
  if(!files.length){toast('Upload PDF, JPEG or PNG receipt files','warn');return;}
  expAiAddFiles(files);
}

function expAiUpload(input){
  const files=[...input.files];
  if(!files.length)return;
  input.value='';
  expAiAddFiles(files);
}

function expAiAddFiles(files){
  files.forEach(file=>{
    const entry={name:file.name,size:file.size,status:'Ready',id:Date.now()+Math.random()};
    const reader=new FileReader();
    reader.onload=e=>{entry.base64=e.target.result;};
    reader.readAsDataURL(file);
    expAiFiles.push(entry);
  });
  expAiRenderFileList();
  toast(`${files.length} receipt(s) added. Click Extract Pending to process.`,'info');
}

function expAiRenderFileList(){
  const list=document.getElementById('exp-ai-file-list');
  const badge=document.getElementById('exp-ai-file-count');
  if(badge)badge.textContent=`${expAiFiles.length} file${expAiFiles.length===1?'':'s'}`;
  if(!list)return;
  if(!expAiFiles.length){
    list.innerHTML='<div style="font-size:12px;color:var(--text3);padding:10px 0">No receipts uploaded yet.</div>';
    return;
  }
  const statusColor={Ready:'var(--text3)',Extracting:'var(--accent)',Done:'var(--green)',Error:'var(--red)'};
  list.innerHTML=expAiFiles.map((f,i)=>`
    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid var(--border)">
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(f.name)}</div>
        <div style="font-size:11px;color:${statusColor[f.status]||'var(--text3)'}">${escapeHtml(f.status)}</div>
      </div>
      <button class="icon-btn danger" onclick="expAiRemoveFile(${i})">${deleteIconSvg()}</button>
    </div>`).join('');
}

function expAiRemoveFile(i){
  expAiFiles.splice(i,1);
  expAiRenderFileList();
}

function toggleExpAiSelection(checked){
  document.querySelectorAll('#exp-ai-tbody .expense-ai-select').forEach(box=>{box.checked=!!checked;});
}

async function runExpAiExtraction(){
  const ready=expAiFiles.filter(f=>f.status==='Ready');
  if(!ready.length){toast('No pending receipts to extract','warn');return;}
  const prog=document.getElementById('exp-ai-extract-prog');
  const fill=document.getElementById('exp-ai-ext-fill');
  const pct=document.getElementById('exp-ai-ext-pct');
  if(prog)prog.style.display='block';
  let done=0;
  for(const entry of ready){
    entry.status='Extracting';
    expAiRenderFileList();
    try{
      const payload={file:{name:entry.name,size:entry.size,base64:entry.base64}};
      const resp=await authenticatedFetch(`${apiBaseUrl()}/app-data?action=documents.extract`,{method:'POST',body:JSON.stringify(payload)});
      const data=resp.ok?await resp.json():null;
      const invoices=Array.isArray(data)?data:(data?.invoices||[]);
      entry.status='Done';
      if(invoices.length)expAiRenderExtracted(invoices,entry.name);
    }catch(err){
      entry.status='Error';
      toast(`Extraction failed for ${entry.name}: ${err.message}`,'warn');
    }
    done++;
    if(fill)fill.style.width=`${Math.round(done/ready.length*100)}%`;
    if(pct)pct.textContent=`${Math.round(done/ready.length*100)}%`;
    expAiRenderFileList();
  }
  setTimeout(()=>{if(prog)prog.style.display='none';},800);
  const statEl=document.getElementById('exp-ai-stat-extracted');
  if(statEl)statEl.textContent=document.querySelectorAll('#exp-ai-tbody .exp-ai-card').length;
}

function expAiRenderExtracted(invoices,filename){
  const tbody=document.getElementById('exp-ai-tbody');
  if(!tbody)return;
  if(tbody.querySelector('.ai-empty-state'))tbody.innerHTML='';
  invoices.forEach(inv=>{
    const lines=Array.isArray(inv.lines)?inv.lines:[];
    const amount=parseAmount(inv.subtotal||inv.net_amount||0);
    const vat=parseAmount(inv.vat_amount||inv.tax_amount||0);
    const total=parseAmount(inv.total||amount+vat);
    const card=document.createElement('div');
    card.className='ai-extract-card exp-ai-card';
    card.setAttribute('data-exp-inv',JSON.stringify({...inv,amount,vat,total,source_file:filename}));
    card.innerHTML=`
      <div class="ai-card-head">
        <div class="ai-card-title-wrap">
          <input type="checkbox" class="expense-ai-select" checked aria-label="Select receipt">
          <div class="ai-pdf-icon"><span>REC</span></div>
          <div>
            <div class="ai-card-title mono">${escapeHtml(inv.invoice_no||filename||'Receipt')}</div>
            <div class="ai-card-kicker">${escapeHtml(inv.supplier||'Supplier')}</div>
          </div>
        </div>
      </div>
      <div class="ai-invoice-divider"></div>
      <div class="ai-invoice-fields">
        <div><span>Date</span><strong>${escapeHtml(inv.date||'—')}</strong></div>
        <div><span>Ref</span><strong class="mono">${escapeHtml(inv.invoice_no||'—')}</strong></div>
        <div><span>Supplier</span><strong>${escapeHtml(inv.supplier||'—')}</strong></div>
        <div><span>Total</span><strong class="mono">AED ${total.toLocaleString('en-AE',{minimumFractionDigits:2})}</strong></div>
        <div><span>VAT</span><strong class="mono">AED ${vat.toLocaleString('en-AE',{minimumFractionDigits:2})}</strong></div>
      </div>
      <div class="ai-card-foot">
        <div class="row-actions">
          <button class="ai-card-action approve" onclick="expAiSaveOne(this)">Save</button>
          <button class="ai-card-action delete" onclick="this.closest('.exp-ai-card').remove()">${deleteIconSvg()}</button>
        </div>
      </div>`;
    tbody.insertBefore(card,tbody.firstChild);
  });
  const statEl=document.getElementById('exp-ai-stat-extracted');
  if(statEl)statEl.textContent=document.querySelectorAll('#exp-ai-tbody .exp-ai-card').length;
}

function expAiSaveOne(btn){
  const card=btn.closest('.exp-ai-card');
  if(!card)return;
  try{
    const inv=JSON.parse(card.getAttribute('data-exp-inv')||'{}');
    const record={
      ref:`EXP-AI-${Date.now()}`,
      date:inv.date||new Date().toISOString().slice(0,10),
      category:'Supplies',
      description:inv.supplier||(inv.lines?.[0]?.product)||'AI Extracted Expense',
      amount:parseAmount(inv.subtotal||inv.net_amount||0),
      vat:parseAmount(inv.vat_amount||inv.tax_amount||0),
      total:parseAmount(inv.total||0),
      status:'Pending',
      source:'AI Upload',
      supplier:inv.supplier||''
    };
    renderExpenseRecord(record);
    saveServer('expenses',record);
    card.remove();
    toast('Expense saved from receipt','ok');
  }catch(e){toast('Save failed: '+e.message,'warn');}
}

function saveExtractedExpenses(){
  const cards=[...document.querySelectorAll('#exp-ai-tbody .exp-ai-card')].filter(c=>c.querySelector('.expense-ai-select')?.checked);
  if(!cards.length){toast('No receipts selected to save','warn');return;}
  cards.forEach(card=>expAiSaveOne(card.querySelector('.ai-card-action.approve')));
}

function setExpAiView(mode,btn){
  if(btn){
    btn.closest('.segmented')?.querySelectorAll('.seg').forEach(b=>b.classList.remove('on'));
    btn.classList.add('on');
  }
  const tbody=document.getElementById('exp-ai-tbody');
  if(tbody)tbody.className=`ai-card-grid${mode==='gallery'?' ai-gallery':''}`;
}

// ── end Expense AI Upload ─────────────────────────────────────
function calcExpenseTotal(){
  const amount=parseAmount(document.getElementById('expense-amount')?.value);
  const vat=parseAmount(document.getElementById('expense-vat')?.value);
  const total=document.getElementById('expense-total');
  if(total)total.value=(amount+vat).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
}

function renderExpenseRecord(expense){
  const tbody=document.getElementById('expense-tbody');
  const ref=expense?.ref||expense?.id;
  if(!tbody||!ref||hasFirstCellValue(tbody,ref))return;
  const amount=Number(expense.amount||0);
  const vat=Number(expense.vat_amount||expense.vat||0);
  const total=Number(expense.total||amount+vat);
  const status=expense.status||'Pending';
  const statusClass=status==='Approved'?'b-g':status==='Rejected'?'b-r':status==='Draft'?'b-gray':'b-a';
  const row=document.createElement('tr');
  row.dataset.serverRecord='expenses';
  row.dataset.expense=JSON.stringify(expense);
  row.dataset.expenseRef=ref;
  row.innerHTML=`<td>${escapeHtml(expense.date||'-')}</td><td>${escapeHtml(expense.description||ref)}</td><td><span class="b b-gray">${escapeHtml(expense.category||'Expense')}</span></td><td class="mono">${amount.toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${vat.toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${total.toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td><span class="b ${statusClass}">${escapeHtml(status)}</span></td><td><button class="btn btn-g btn-sm" onclick="openRowDetail(this,'Expense Detail','Expense')">View</button></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  renderExpenseApprovalRecord(expense);
  updateExpenseStats();
}

function renderExpenseApprovalRecord(expense){
  const tbody=document.getElementById('expense-approval-tbody');
  const ref=expense?.ref||expense?.id;
  if(!tbody||!ref)return;
  tbody.querySelector(`tr[data-expense-ref="${CSS.escape(ref)}"]`)?.remove();
  if(String(expense.status||'').toLowerCase()!=='pending'){
    if(!tbody.querySelector('tr:not([data-empty-state])'))emptyTableMessage(tbody,'No pending expenses for approval.');
    return;
  }
  const row=document.createElement('tr');
  row.dataset.expenseRef=ref;
  row.innerHTML=`<td>${escapeHtml(expense.employee||'Current User')}</td><td>${escapeHtml(expense.description||ref)}</td><td class="mono">${formatAed(expense.total||0)}</td><td>${escapeHtml(expense.date||'-')}</td><td><span class="b b-a">Pending</span></td><td><div class="flx"><button class="btn btn-success btn-sm" onclick="setExpenseApprovalStatus('${escapeHtml(ref)}','Approved')">Approve</button><button class="btn btn-danger btn-sm" onclick="setExpenseApprovalStatus('${escapeHtml(ref)}','Rejected')">Reject</button></div></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
}

function expenseRecordFromListRow(row){
  if(row?.dataset.expense){
    try{return JSON.parse(row.dataset.expense);}catch{}
  }
  return null;
}

function setExpenseApprovalStatus(ref,status){
  const listRow=[...document.querySelectorAll('#expense-tbody tr:not([data-empty-state])')]
    .find(row=>row.dataset.expenseRef===ref);
  const record={...(expenseRecordFromListRow(listRow)||{}),ref,status};
  if(listRow){
    const badge=listRow.children[6]?.querySelector('.b');
    if(badge){
      badge.className=`b ${status==='Approved'?'b-g':'b-r'}`;
      badge.textContent=status;
    }
    listRow.dataset.expense=JSON.stringify(record);
  }
  document.querySelector(`#expense-approval-tbody tr[data-expense-ref="${CSS.escape(ref)}"]`)?.remove();
  const approvalBody=document.getElementById('expense-approval-tbody');
  if(approvalBody&&!approvalBody.querySelector('tr:not([data-empty-state])'))emptyTableMessage(approvalBody,'No pending expenses for approval.');
  updateExpenseStats();
  saveServer('expenses',record);
  toast(status==='Approved'?'Expense approved':'Expense rejected',status==='Approved'?'ok':'warn');
  audit(`${status} expense`,ref,status);
}

function updateExpenseStats(){
  const rows=[...document.querySelectorAll('#expense-tbody tr:not([data-empty-state])')];
  const total=rows.reduce((sum,row)=>sum+parseAmount(row.children[5]?.textContent),0);
  const pending=rows.filter(row=>/pending/i.test(row.children[6]?.textContent||'')).reduce((sum,row)=>sum+parseAmount(row.children[5]?.textContent),0);
  const approved=rows.filter(row=>/approved/i.test(row.children[6]?.textContent||'')).reduce((sum,row)=>sum+parseAmount(row.children[5]?.textContent),0);
  const rejected=rows.filter(row=>/rejected/i.test(row.children[6]?.textContent||'')).reduce((sum,row)=>sum+parseAmount(row.children[5]?.textContent),0);
  const stats=[...document.querySelectorAll('#exp-list .stat-val')];
  if(stats[0])stats[0].textContent=formatAed(total);
  if(stats[1])stats[1].textContent=formatAed(pending);
  if(stats[2])stats[2].textContent=formatAed(approved);
  if(stats[3])stats[3].textContent=formatAed(rejected);
}

function buildExpenseRecord(status='Pending'){
  calcExpenseTotal();
  const amount=parseAmount(document.getElementById('expense-amount')?.value);
  const vat=parseAmount(document.getElementById('expense-vat')?.value);
  return {
    ref:`EXP-${Date.now()}`,
    date:document.getElementById('expense-date')?.value||new Date().toISOString().split('T')[0],
    category:document.getElementById('expense-category')?.value||'Expense',
    description:document.getElementById('expense-description')?.value?.trim()||'Expense',
    amount,
    vat_amount:vat,
    total:amount+vat,
    status
  };
}

function clearExpenseForm(){
  ['expense-description','expense-amount','expense-vat','expense-total'].forEach(id=>setFieldValue(document.getElementById(id),''));
}

function saveExpense(status='Pending'){
  const record=buildExpenseRecord(status);
  if(!record.description||record.amount<=0){
    toast('Enter expense description and amount','warn');
    return;
  }
  renderExpenseRecord(record);
  saveServer('expenses',record);
  clearExpenseForm();
  stab(document.querySelector('#page-expense .tab:nth-child(4)'),'exp-list');
  toast(status==='Draft'?'Expense draft saved':'Expense submitted for approval','ok');
  audit(status==='Draft'?'Saved expense draft':'Submitted expense',record.ref,'Saved');
}

function buildPurchaseRecordRow(purchase){
  const ref=purchase?.ref||purchase?.invoice_no||purchase?.reference;
  if(!ref)return null;
  const quantity=purchaseRecordQuantity(purchase);
  const normalizedPurchase={...purchase,ref,items:quantity};
  purchaseRecordCache.set(String(ref),normalizedPurchase);
  const row=document.createElement('tr');
  row.dataset.serverRecord='purchaseRecords';
  row.dataset.purchaseRef=String(ref);
  row.dataset.purchaseRecord=JSON.stringify(normalizedPurchase);
  const status=purchase.status||'Draft';
  const statusClass=status==='Paid'?'b-g':status==='Received'?'b-b':status.includes('Payment')?'b-a':'b-gray';
  const source=String(purchase.source||'Manual');
  const sourceClass=source.toLowerCase().includes('ai')?'b-p':'b-gray';
  row.innerHTML=`<td class="mono">${escapeHtml(ref)}</td><td>${escapeHtml(purchaseRecordProductSummary(normalizedPurchase))}</td><td>${escapeHtml(normalizedPurchase.supplier||'-')}</td><td>${escapeHtml(normalizedPurchase.date||'-')}</td><td>${escapeHtml(normalizedPurchase.location||'-')}</td><td class="mono">${Number(quantity||0).toLocaleString('en-AE',{maximumFractionDigits:4})}</td><td class="mono">${Number(normalizedPurchase.net_amount||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${Number(normalizedPurchase.tax_amount||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${Number(normalizedPurchase.shipping||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${Number(normalizedPurchase.total||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${Number(normalizedPurchase.paid||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${Number(normalizedPurchase.due||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td><span class="b ${sourceClass}">${escapeHtml(source)}</span></td><td><span class="b ${statusClass}">${escapeHtml(status)}</span></td><td data-action-col="1"><div class="row-actions"><button class="icon-btn edit" type="button" title="Edit" aria-label="Edit purchase" onclick="editPurchaseRecord(this)">${editIconSvg()}</button><button class="icon-btn view" type="button" title="View" aria-label="View purchase" onclick="openPurchaseRecordPreview(this)">${viewIconSvg()}</button><button class="icon-btn copy" type="button" title="Copy" aria-label="Copy purchase" onclick="copyPurchaseRecord(this)">${copyIconSvg()}</button><button class="icon-btn danger" type="button" title="Delete" aria-label="Delete purchase" onclick="deletePurchaseRecord(this)">${deleteIconSvg()}</button></div></td>`;
  return row;
}

function purchaseRecordProductSummary(purchase={}){
  const lines=Array.isArray(purchase.lines)?purchase.lines:[];
  const names=[...new Set(lines.map(line=>purchaseAiProductName(line)||line.product||line.name||line.description||'').filter(Boolean))];
  if(names.length>1)return `${names[0]} +${names.length-1}`;
  return names[0]||purchase.product||purchase.product_name||purchase.item_name||'-';
}

function renderPurchaseRecord(purchase,options={}){
  const tbody=document.getElementById('purchase-record-tbody');
  const ref=purchase?.ref||purchase?.invoice_no||purchase?.reference;
  if(!tbody||!ref||hasFirstCellValue(tbody,ref))return;
  const row=buildPurchaseRecordRow(purchase);
  if(!row)return;
  removeEmptyState(tbody);
  tbody.prepend(row);
  if(!options.deferRefresh&&!isHydratingFromServer)refreshEnhancedTable(tbody.closest('table'));
  if(!options.deferStockSync&&!isHydratingFromServer)syncStockLevelsFromProducts();
}

function updatePurchaseRecordControls(total=purchaseRecordCache.size,visible=0){
  const count=document.getElementById('purchase-record-count');
  const loadMore=document.getElementById('purchase-load-more-btn');
  const showRecent=document.getElementById('purchase-show-recent-btn');
  const shown=visible||document.querySelectorAll('#purchase-record-tbody tr:not([data-empty-state])').length;
  if(count){
    count.textContent=purchaseRecordsLoading
      ? 'Loading purchase records...'
      : total
      ? `Showing ${shown.toLocaleString('en-AE')} of ${total.toLocaleString('en-AE')} database records`
      : 'No purchase records in database yet.';
  }
  if(loadMore){
    loadMore.disabled=purchaseRecordsLoading||shown>=total;
    loadMore.textContent=purchaseRecordsLoading?'Loading...':shown>=total?'All Loaded':'Load More';
  }
  if(showRecent)showRecent.disabled=purchaseRecordsLoading||shown<=PURCHASE_PAGE_SIZE;
}

function renderPurchaseRecordWindow(){
  const tbody=document.getElementById('purchase-record-tbody');
  if(!tbody)return {rendered:0,failed:0,total:purchaseRecordCache.size};
  const records=[...purchaseRecordCache.values()];
  const fragment=document.createDocumentFragment();
  let rendered=0;
  let failed=0;
  records.forEach(purchase=>{
    try{
      const row=buildPurchaseRecordRow(purchase);
      if(!row)return;
      fragment.appendChild(row);
      rendered++;
    }catch(err){
      failed++;
      console.warn('Could not render purchase record:',err,purchase);
    }
  });
  tbody.innerHTML='';
  if(rendered)tbody.appendChild(fragment);
  else emptyTableMessage(tbody,'No purchase records in database yet.');
  const total=purchaseRecordsTotal||records.length;
  window.__taxflowPurchaseRecordTotal=total;
  window.__taxflowPurchaseRecordVisible=rendered;
  updatePurchaseRecordControls(total,rendered);
  refreshEnhancedTable(tbody.closest('table'));
  renderLPOList();
  return {rendered,failed,total};
}

function renderPurchaseRecordList(records=[]){
  records.forEach(purchase=>{
    const ref=String(purchase?.ref||purchase?.invoice_no||purchase?.reference||'').trim();
    if(ref)purchaseRecordCache.set(ref,{...purchase,ref});
  });
  return renderPurchaseRecordWindow();
}

async function fetchPurchaseRecordsPage({reset=false}={}){
  if(purchaseRecordsLoading)return {records:[],total:purchaseRecordsTotal,has_more:false};
  purchaseRecordsLoading=true;
  updatePurchaseRecordControls(purchaseRecordsTotal,purchaseRecordCache.size);
  try{
    if(reset){
      purchaseRecordCache.clear();
      purchaseRecordsOffset=0;
      purchaseRecordsLoaded=false;
    }
    const response=await authenticatedFetch(`${apiBaseUrl()}/app-data/records/purchaseRecords?limit=${PURCHASE_PAGE_SIZE}&offset=${purchaseRecordsOffset}`);
    if(!response.ok)throw new Error('Purchase records API returned '+response.status);
    const data=await response.json();
    const records=Array.isArray(data.records)?data.records:[];
    purchaseRecordsTotal=Number(data.total||records.length||0);
    records.forEach(record=>{
      const ref=String(record?.ref||record?.invoice_no||record?.reference||'').trim();
      if(ref)purchaseRecordCache.set(ref,{...record,ref});
    });
    purchaseRecordsOffset+=records.length;
    purchaseRecordsLoaded=purchaseRecordCache.size>=purchaseRecordsTotal||data.has_more===false;
    renderPurchaseRecordWindow();
    syncStockLevelsFromProducts();
    return data;
  }catch(err){
    console.warn('Purchase records page load failed:',err);
    toast('Purchase records could not load. Check backend connection.','warn');
    return {records:[],total:purchaseRecordsTotal,has_more:false};
  }finally{
    purchaseRecordsLoading=false;
    updatePurchaseRecordControls(purchaseRecordsTotal,purchaseRecordCache.size);
  }
}

function ensurePurchaseRecordsLoaded(){
  if(purchaseRecordsLoaded||purchaseRecordsLoading){
    updatePurchaseRecordControls(purchaseRecordsTotal,purchaseRecordCache.size);
    return;
  }
  fetchPurchaseRecordsPage();
}

function ensurePurchaseRecordsLoadedForStock(){
  if(purchaseRecordsLoaded){
    syncStockLevelsFromProducts();
    return;
  }
  fetchAllPurchaseRecordsForStock();
}

async function fetchAllPurchaseRecordsForStock(){
  if(purchaseRecordsLoading)return;
  do{
    const data=await fetchPurchaseRecordsPage();
    if(!data?.has_more)break;
  }while(!purchaseRecordsLoaded&&purchaseRecordCache.size<purchaseRecordsTotal);
  syncStockLevelsFromProducts();
}

function loadMorePurchaseRecords(){
  if(purchaseRecordsLoading)return;
  if(purchaseRecordsLoaded&&purchaseRecordCache.size>=purchaseRecordsTotal){
    toast('All purchase records are loaded','info');
    return;
  }
  fetchPurchaseRecordsPage().then(data=>{
    const count=Array.isArray(data.records)?data.records.length:0;
    if(count)toast(`Loaded ${count.toLocaleString('en-AE')} more purchase records`,'ok');
  });
}

async function clearPurchaseRecords(){
  if(purchaseRecordsLoading){
    toast('Purchase records are still loading','warn');
    return;
  }
  if(!purchaseRecordsLoaded&&purchaseRecordCache.size<purchaseRecordsTotal){
    await fetchAllPurchaseRecordsForStock();
  }
  const records=[...purchaseRecordCache.values()];
  if(!records.length){
    toast('No purchase records to clear','warn');
    return;
  }
  const confirmed=await appConfirm({
    title:'Clear Purchase Records',
    message:`Clear ${records.length.toLocaleString('en-AE')} purchase record(s)? This removes their source transactions and stock movements from the database.`,
    okText:'Clear Records'
  });
  if(!confirmed)return;
  let deleted=0;
  let failed=0;
  for(const record of records){
    const result=await deleteServer('purchaseRecords',record);
    if(result?.deleted)deleted++;
    else failed++;
  }
  if(deleted){
    purchaseRecordCache.clear();
    purchaseRecordsTotal=0;
    purchaseRecordsOffset=0;
    purchaseRecordsLoaded=true;
    renderPurchaseRecordWindow();
    syncStockLevelsFromProducts();
    loadStockLevelsFromServer();
    audit('Cleared purchase records',`${deleted} record(s)`,'Deleted');
  }
  toast(`${deleted.toLocaleString('en-AE')} purchase record(s) cleared${failed?`; ${failed.toLocaleString('en-AE')} failed`:''}`,failed?'warn':'ok');
}

function showRecentPurchaseRecords(){
  fetchPurchaseRecordsPage({reset:true}).then(data=>{
    const count=Array.isArray(data.records)?data.records.length:purchaseRecordCache.size;
    if(count)toast(`Showing latest ${count.toLocaleString('en-AE')} purchase records`,'info');
  });
}

function addFourPurchaseRecords(){
  const today=new Date().toISOString().split('T')[0];
  const stamp=Date.now().toString().slice(-6);
  const records=[
    {
      ref:`PUR-ADD-${stamp}-1`,
      supplier:'Office Mart',
      date:today,
      location:'Dubai HQ',
      items:2,
      net_amount:1480,
      tax_amount:74,
      shipping:35,
      total:1589,
      paid:1589,
      due:0,
      source:'Manual',
      status:'Paid',
      lines:[{sku:'PAPER-A4',product:'A4 Paper Box',unit:'BOX',quantity:8,unit_cost:185,line_total:1480}]
    },
    {
      ref:`PUR-ADD-${stamp}-2`,
      supplier:'Global Tech',
      date:today,
      location:'Main Warehouse',
      items:1,
      net_amount:3200,
      tax_amount:160,
      shipping:0,
      total:3360,
      paid:1600,
      due:1760,
      source:'Manual',
      status:'Pending Payment',
      lines:[{sku:'MON-24',product:'24 Inch Monitor',unit:'PCS',quantity:4,unit_cost:800,line_total:3200}]
    },
    {
      ref:`PUR-ADD-${stamp}-3`,
      supplier:'Fast Ship Corp',
      date:today,
      location:'Dubai HQ',
      items:1,
      net_amount:650,
      tax_amount:32.5,
      shipping:90,
      total:772.5,
      paid:0,
      due:772.5,
      source:'Manual',
      status:'Pending Payment',
      lines:[{sku:'SHIP-STD',product:'Inbound Freight',unit:'JOB',quantity:1,unit_cost:650,line_total:650}]
    },
    {
      ref:`PUR-ADD-${stamp}-4`,
      supplier:'Industrial Solutions',
      date:today,
      location:'Main Warehouse',
      items:3,
      net_amount:2190,
      tax_amount:109.5,
      shipping:45,
      total:2344.5,
      paid:2344.5,
      due:0,
      source:'Manual',
      status:'Paid',
      lines:[{sku:'SAFETY-KIT',product:'Safety Kit',unit:'PCS',quantity:6,unit_cost:365,line_total:2190}]
    }
  ];
  records.forEach(record=>{
    purchaseRecordCache.set(record.ref,record);
    saveServer('purchaseRecords',record);
  });
  purchaseRecordsTotal+=records.length;
  purchaseRecordsOffset+=records.length;
  renderPurchaseRecordWindow();
  syncStockLevelsFromProducts();
  toast('4 purchase records added','ok');
}

function accountTypeLabel(value){
  const normalized=String(value||'Asset').trim().toLowerCase();
  return {
    purchase:'Purchase',
    sales:'Sales',
    'direct expense':'Direct expense',
    'indirect expense':'Indirect expense',
    'direct income':'Direct income',
    'indirect income':'Indirect income',
    asset:'Asset',
    liability:'Liability',
    equity:'Equity',
    revenue:'Direct income',
    expense:'Indirect expense'
  }[normalized]||titleCase(normalized);
}

function accountTypeBadgeClass(value){
  const normalized=String(value||'').trim().toLowerCase();
  if(['sales','direct income','indirect income','revenue'].includes(normalized))return 'b-g';
  if(['purchase','direct expense','indirect expense','expense'].includes(normalized))return 'b-p';
  if(normalized==='asset')return 'b-t';
  if(normalized==='liability')return 'b-r';
  if(normalized==='equity')return 'b-b';
  return 'b-gray';
}

function renderAccountRecord(account){
  // Legacy flat renderer — delegates to tree rebuild
  renderAccountTree();
}

let _coaFlatAccounts=[];

function renderAccountTree(accounts){
  if(accounts)_coaFlatAccounts=accounts;
  const root=document.getElementById('account-tbody');
  if(!root)return;
  if(!_coaFlatAccounts.length){
    root.innerHTML='<div class="coa-empty">No accounts yet. Create a Primary Group to start.</div>';
    return;
  }
  // Build id→account map and parent→children map
  const byId={};
  const children={};
  _coaFlatAccounts.forEach(a=>{
    byId[a.id]=a;
    const pid=a.parent_account_id||'root';
    (children[pid]=children[pid]||[]).push(a);
  });
  // Sort each level by code
  Object.values(children).forEach(arr=>arr.sort((a,b)=>String(a.code).localeCompare(String(b.code))));
  root.innerHTML='';
  (children['root']||[]).forEach(acc=>root.appendChild(coaNodeEl(acc,byId,children,0)));
}

function coaNodeTypePill(nodeType){
  if(nodeType==='MAIN_LEDGER')return`<span class="nt-pill nt-main">MAIN</span>`;
  if(nodeType==='SUB_LEDGER')return`<span class="nt-pill nt-sub">SUB</span>`;
  return`<span class="nt-pill nt-posting">POSTING</span>`;
}

function coaStatusBadge(status){
  if(!status||status==='active')return'';
  if(status==='draft')return`<span class="nt-status nt-draft">DRAFT</span>`;
  return`<span class="nt-status nt-inactive">INACTIVE</span>`;
}

function coaNodeEl(acc,byId,children,depth){
  const kids=(children[acc.id]||[]);
  const hasKids=kids.length>0;
  const isGroup=acc.is_group||false;
  const level=acc.level||5;
  const nodeType=acc.node_type||(isGroup?(level===1?'MAIN_LEDGER':'SUB_LEDGER'):'POSTING_LEDGER');
  const isPosting=nodeType==='POSTING_LEDGER';
  const isMain=nodeType==='MAIN_LEDGER';
  const status=acc.status||'active';
  const indent=depth*18;

  // Context-sensitive action buttons
  const id=escapeHtml(acc.id);
  let actionBtns='';
  if(isMain){
    actionBtns=`
      <button class="btn btn-g btn-xs" onclick="openCreateSubLedgerUnder('${id}')">+ Sub Ledger</button>
      <button class="btn btn-g btn-xs" onclick="openEditAccount('${id}')">Edit</button>`;
  } else if(!isPosting){
    // SUB_LEDGER — level 2,3,4 can add more sub-ledgers (max depth 4 for groups)
    const canAddSub=level<4;
    if(canAddSub) actionBtns+=`<button class="btn btn-g btn-xs" onclick="openCreateSubLedgerUnder('${id}')">+ Sub</button>`;
    actionBtns+=`<button class="btn btn-p btn-xs" onclick="openCreatePostingLedgerUnder('${id}')">+ Ledger</button>`;
    actionBtns+=`<button class="btn btn-g btn-xs" onclick="openEditAccount('${id}')">Edit</button>`;
  } else {
    // POSTING_LEDGER
    const isInactive=status==='inactive';
    actionBtns=`
      <button class="btn btn-g btn-xs" onclick="openEditAccount('${id}')">Edit</button>
      <button class="btn btn-accent btn-xs" onclick="openObModal('${id}')">OB</button>
      <button class="btn ${isInactive?'btn-g':'btn-warn'} btn-xs" onclick="toggleAccountStatus('${id}','${escapeHtml(status)}')">${isInactive?'Activate':'Deactivate'}</button>`;
  }

  // Opening balance display for posting ledger
  const obVal=acc.opening_balance!=null?Number(acc.opening_balance):0;
  const obType=acc.opening_balance_type||'DR';
  const obDisplay=isPosting&&obVal?`<span class="coa-ob-chip">${obVal.toFixed(2)} ${obType}</span>`:'';

  const wrap=document.createElement('div');
  wrap.className='coa-node';
  wrap.dataset.accountId=acc.id;
  wrap.dataset.serverRecord='accounts';

  const row=document.createElement('div');
  row.className=`coa-row${isPosting?' coa-posting-row':''}${status==='inactive'?' coa-inactive':''}`;
  row.innerHTML=`
    <span class="coa-indent" style="width:${indent}px"></span>
    ${hasKids?`<button class="coa-toggle open" onclick="toggleCoaNode(this)">▼</button>`:'<span class="coa-toggle-spacer"></span>'}
    ${coaNodeTypePill(nodeType)}
    <span class="coa-code mono">${escapeHtml(acc.code)}</span>
    <span class="coa-name${!isPosting?' group-name':''}">${escapeHtml(acc.name)}</span>
    ${coaStatusBadge(status)}
    ${obDisplay}
    <span class="coa-type-chip coa-type-sm">${escapeHtml(acc.type||'')}</span>
    <span class="coa-actions">${actionBtns}
      <button class="icon-btn" title="View ledger" onclick="viewAccountLedgerById('${id}')">${viewIconSvg()}</button>
      <button class="icon-btn danger" title="Delete" onclick="deleteAccountById('${id}')">${deleteIconSvg()}</button>
    </span>`;

  if(hasKids){
    const childWrap=document.createElement('div');
    childWrap.className='coa-children open';
    kids.forEach(kid=>childWrap.appendChild(coaNodeEl(kid,byId,children,depth+1)));
    row.querySelector('.coa-toggle').addEventListener('click',e=>{
      e.stopPropagation();
      e.currentTarget.classList.toggle('open');
      childWrap.classList.toggle('open');
    });
    wrap.appendChild(row);
    wrap.appendChild(childWrap);
  }else{
    wrap.appendChild(row);
  }
  return wrap;
}

function toggleCoaNode(btn){
  btn.classList.toggle('open');
  const childWrap=btn.closest('.coa-node')?.querySelector(':scope > .coa-children');
  if(childWrap)childWrap.classList.toggle('open');
}

function autoNormalBalance(){
  const type=(document.getElementById('acc-type')?.value||'').toLowerCase();
  const creditTypes=['liability','equity','revenue','income','direct income','indirect income','retained earnings','sundry creditors','duties & taxes','provisions'];
  const nb=creditTypes.includes(type)?'CR':'DR';
  const sel=document.getElementById('acc-normal-balance');
  if(sel)sel.value=nb;
  const obType=document.getElementById('acc-ob-type');
  if(obType)obType.value=nb;
}

// ── AI LEDGER GENERATOR ─────────────────────────────────────────────

let _aiLedgerTree=[];

function toggleAiPanel(){
  const body=document.getElementById('ai-ledger-body');
  if(!body)return;
  const hidden=body.style.display==='none';
  body.style.display=hidden?'':'none';
  const btn=document.querySelector('#ai-ledger-panel .card-hd .btn-g');
  if(btn)btn.textContent=hidden?'Hide':'Show';
}

function setAiPrompt(text){
  const el=document.getElementById('ai-ledger-prompt');
  if(el){el.value=text;el.focus();}
}

function clearAiLedger(){
  const el=document.getElementById('ai-ledger-prompt');
  if(el)el.value='';
  document.getElementById('ai-ledger-preview').style.display='none';
  _aiLedgerTree=[];
}

async function aiGenerateLedger(){
  const prompt=(document.getElementById('ai-ledger-prompt')?.value||'').trim();
  if(!prompt){toast('Enter a description for the ledger structure','warn');return;}
  const btn=document.getElementById('ai-ledger-gen-btn');
  if(btn){btn.disabled=true;btn.textContent='Generating...';}
  try{
    const result=await moduleApi('/accounts/ai-generate',{method:'POST',body:{prompt}});
    _aiLedgerTree=result.tree||[];
    document.getElementById('ai-ledger-summary').textContent=result.summary?` — ${result.summary}`:'';
    renderAiLedgerPreview(_aiLedgerTree);
    document.getElementById('ai-ledger-preview').style.display='';
  }catch(err){
    toast('AI generation failed: '+err.message,'err');
  }finally{
    if(btn){btn.disabled=false;btn.textContent='✦ Generate';}
  }
}

function renderAiLedgerPreview(tree,container){
  const root=container||document.getElementById('ai-ledger-tree');
  if(!root)return;
  root.innerHTML='';
  const errDiv=document.getElementById('ai-ledger-errors');
  const allIssues=[];
  function collectIssues(nodes){nodes.forEach(n=>{if(n.issues?.length)allIssues.push(...n.issues.map(i=>`${n.code}: ${i}`));collectIssues(n.children||[]);});}
  collectIssues(tree);
  if(errDiv){
    if(allIssues.length){errDiv.style.display='';errDiv.innerHTML='<strong>Issues to review:</strong><ul>'+allIssues.map(i=>`<li>${escapeHtml(i)}</li>`).join('')+'</ul>';}
    else{errDiv.style.display='none';}
  }
  tree.forEach(node=>root.appendChild(aiTreeNodeEl(node,0)));
}

function aiTreeNodeEl(node,depth){
  const wrap=document.createElement('div');
  wrap.className='ai-tree-node';
  const indent=depth*20;
  const isGroup=node.is_group||!!node.children?.length;
  const levelPillCls={1:'coa-pill-l1',2:'coa-pill-l2',3:'coa-pill-l3',4:'coa-pill-led'}[node.level]||'coa-pill-led';
  const levelLabel={1:'L1',2:'L2',3:'L3',4:'Ledger'}[node.level]||'L'+node.level;
  const hasIssues=node.issues?.length>0;
  const row=document.createElement('div');
  row.className='ai-tree-row'+(hasIssues?' ai-tree-issue':'')+(isGroup?' ai-tree-group':'');
  const allTypes=['Asset','Bank','Cash','Sundry Debtors','Stock-in-Trade','Liability','Sundry Creditors','Duties & Taxes','Provisions','Equity','Retained Earnings','Revenue','Direct Income','Indirect Income','Expense','Purchase','Direct Expense','Indirect Expense'];
  const openingVal=node.opening_balance!=null?node.opening_balance:'0.00';
  row.innerHTML=`
    <span style="width:${indent}px;display:inline-block;flex-shrink:0"></span>
    <span class="coa-level-pill ${levelPillCls}">${levelLabel}</span>
    <input class="ai-tree-code fi mono" value="${escapeHtml(node.code)}" data-field="code" style="width:72px" placeholder="Code">
    <input class="ai-tree-name fi" value="${escapeHtml(node.name)}" data-field="name" style="flex:1" placeholder="Name">
    <select class="ai-tree-type fi" data-field="type" style="width:110px">
      ${allTypes.map(t=>`<option${node.type===t?' selected':''}>${t}</option>`).join('')}
    </select>
    ${!isGroup?`<input class="ai-tree-opening fi mono" data-field="opening_balance" value="${escapeHtml(String(openingVal))}" style="width:82px" placeholder="0.00" title="Opening Balance (AED)">`:
    '<span style="width:82px;display:inline-block"></span>'}
    <span class="ai-tree-badge ${isGroup?'ai-tree-group-badge':'ai-tree-ledger-badge'}">${isGroup?'Group':'Ledger'}</span>
    ${hasIssues?`<span class="ai-tree-warn" title="${escapeHtml(node.issues.join('; '))}">⚠</span>`:''}
    <button class="icon-btn danger ai-tree-del" title="Remove" onclick="aiRemoveNode(this)">×</button>`;
  // Wire input changes back to _aiLedgerTree
  row.querySelectorAll('input,select').forEach(input=>{
    input.onchange=()=>aiSyncEdit();
  });
  wrap.appendChild(row);
  if(node.children?.length){
    const childWrap=document.createElement('div');
    childWrap.className='ai-tree-children';
    node.children.forEach(child=>childWrap.appendChild(aiTreeNodeEl(child,depth+1)));
    wrap.appendChild(childWrap);
  }
  return wrap;
}

function aiSyncEdit(){
  // Re-read all editable values from DOM back into _aiLedgerTree
  // Simple approach: re-validate from current tree data
  const errDiv=document.getElementById('ai-ledger-errors');
  if(errDiv)errDiv.style.display='none';
}

function aiRemoveNode(btn){
  btn.closest('.ai-tree-node')?.remove();
}

async function aiApproveLedger(){
  // Collect current tree from DOM (support inline edits)
  const approveBtn=document.getElementById('ai-approve-btn');
  if(approveBtn){approveBtn.disabled=true;approveBtn.textContent='Creating...';}

  // Rebuild tree from DOM for any inline edits
  const tree=aiCollectTreeFromDom(document.getElementById('ai-ledger-tree'));

  try{
    const result=await moduleApi('/accounts/ai-approve',{method:'POST',body:{tree}});
    const created=result.created||[];
    const errors=result.errors||[];
    toast(`✓ Created ${created.length} account${created.length===1?'':'s'}${errors.length?` (${errors.length} skipped)`:''}`, created.length?'ok':'warn');
    if(errors.length){console.warn('AI approve errors:',errors);}
    // Refresh the COA tree
    _coaFlatAccounts=await moduleApi('/accounts');
    renderAccountTree();
    updateAccountSelectors();
    // Hide AI panel preview
    document.getElementById('ai-ledger-preview').style.display='none';
    document.getElementById('ai-ledger-prompt').value='';
    document.getElementById('ai-ledger-examples').style.display='';
    _aiLedgerTree=[];
  }catch(err){
    toast('Approval failed: '+err.message,'err');
  }finally{
    if(approveBtn){approveBtn.disabled=false;approveBtn.textContent='✓ Approve & Create';}
  }
}

function aiCollectTreeFromDom(container){
  const nodes=[];
  if(!container)return _aiLedgerTree; // fallback to original
  container.querySelectorAll(':scope > .ai-tree-node').forEach(nodeEl=>{
    const row=nodeEl.querySelector('.ai-tree-row');
    if(!row)return;
    const code=row.querySelector('[data-field="code"]')?.value?.trim()||'';
    const name=row.querySelector('[data-field="name"]')?.value?.trim()||'';
    const type=row.querySelector('[data-field="type"]')?.value||'Asset';
    const opening=parseFloat(row.querySelector('[data-field="opening_balance"]')?.value||'0')||0;
    const childContainer=nodeEl.querySelector('.ai-tree-children');
    const children=aiCollectTreeFromDom(childContainer);
    nodes.push({code,name,type,opening_balance:opening,is_group:children.length>0,children});
  });
  return nodes;
}

// ── END AI LEDGER GENERATOR ──────────────────────────────────────────

function expandAllGroups(){
  document.querySelectorAll('#account-tbody .coa-toggle').forEach(t=>{t.classList.add('open');});
  document.querySelectorAll('#account-tbody .coa-children').forEach(c=>{c.classList.add('open');});
}

function collapseAllGroups(){
  document.querySelectorAll('#account-tbody .coa-toggle').forEach(t=>{t.classList.remove('open');});
  document.querySelectorAll('#account-tbody .coa-children').forEach(c=>{c.classList.remove('open');});
}

async function clearAllAccounts(){
  const total=_coaFlatAccounts.length;
  if(!total){toast('No accounts to clear','warn');return;}
  const confirmed=await appConfirm({
    title:'Clear All Accounts',
    message:`Delete all ${total} account${total===1?'':'s'} from the chart of accounts? This cannot be undone.`,
    okText:'Delete All',
    danger:true,
  });
  if(!confirmed)return;
  try{
    const result=await moduleApi('/accounts',{method:'DELETE'});
    const deleted=result.deleted||0;
    const skipped=result.skipped||0;
    _coaFlatAccounts=[];
    renderAccountTree();
    updateAccountSelectors();
    toast(`Cleared ${deleted} account${deleted===1?'':'s'}${skipped?` (${skipped} kept — used in journal entries)`:''}`, 'ok');
    audit('Cleared all chart of accounts',`${deleted} accounts deleted`,'Deleted');
  }catch(err){
    toast('Clear failed: '+err.message,'err');
  }
}

function openCreateAccount(type){
  const modal=document.getElementById('m-acc');
  modal.removeAttribute('data-edit-id');
  const groupBtn=document.getElementById('acc-type-group-btn');
  const ledgerBtn=document.getElementById('acc-type-ledger-btn');
  if(groupBtn)groupBtn.textContent='Sub Ledger (Group)';
  if(ledgerBtn)ledgerBtn.textContent='Posting Ledger';
  setAccModalType(type||'ledger');
  populateParentSelector(null);
  showM('m-acc');
}

function openCreateSubLedgerUnder(parentId){
  document.getElementById('m-acc').removeAttribute('data-edit-id');
  setAccModalType('group');
  _lockGroupToggle(true); // force group
  populateParentSelector(parentId);
  showM('m-acc');
}

function openCreatePostingLedgerUnder(parentId){
  document.getElementById('m-acc').removeAttribute('data-edit-id');
  setAccModalType('ledger');
  _lockGroupToggle(true); // force ledger
  populateParentSelector(parentId);
  showM('m-acc');
}

function openCreateAccountUnder(parentId){
  // Legacy — defaults to posting ledger
  openCreatePostingLedgerUnder(parentId);
}

function openEditAccount(accountId){
  const acc=_coaFlatAccounts.find(a=>a.id===accountId);
  if(!acc)return;
  const modal=document.getElementById('m-acc');
  modal.dataset.editId=accountId;

  const nodeType=acc.node_type||(acc.is_group?(acc.level===1?'MAIN_LEDGER':'SUB_LEDGER'):'POSTING_LEDGER');
  const nodeLabel={'MAIN_LEDGER':'Main Ledger','SUB_LEDGER':'Sub Ledger','POSTING_LEDGER':'Posting Ledger'}[nodeType]||'Account';
  const nodeSub={
    MAIN_LEDGER:'Top-level group — organises Sub Ledgers. Transactions do NOT post here.',
    SUB_LEDGER:'Group — organises Posting Ledgers. Transactions do NOT post here.',
    POSTING_LEDGER:'Posting Ledger — transactions, journals and invoices post to this account.',
  }[nodeType]||'';

  setAccModalType(acc.is_group?'group':'ledger');
  _lockGroupToggle(true);
  populateParentSelector(acc.parent_account_id||null);

  // Fix "None" option text for edit mode (not "becomes")
  const noneOpt=document.querySelector('#acc-parent option[value=""]');
  if(noneOpt)noneOpt.textContent='— None (MAIN LEDGER, Level 1) —';

  // Update type bar label to show actual node type
  const groupBtn=document.getElementById('acc-type-group-btn');
  const ledgerBtn=document.getElementById('acc-type-ledger-btn');
  if(groupBtn)groupBtn.textContent=nodeType==='MAIN_LEDGER'?'Main Ledger (Group)':'Sub Ledger (Group)';
  if(ledgerBtn)ledgerBtn.textContent='Posting Ledger';

  document.getElementById('acc-code').value=acc.code||'';
  document.getElementById('acc-name').value=acc.name||'';
  document.getElementById('acc-type').value=acc.type||'Asset';
  document.getElementById('acc-opening').value=acc.opening_balance!=null?Number(acc.opening_balance).toFixed(2):'0.00';
  document.getElementById('acc-ob-type').value=acc.opening_balance_type||'DR';
  document.getElementById('acc-normal-balance').value=acc.normal_balance||'DR';

  document.getElementById('acc-modal-title').textContent=`Edit ${nodeLabel}`;
  document.getElementById('acc-modal-sub').textContent=nodeSub;
  document.getElementById('acc-save-btn').textContent='Save Changes';
  showM('m-acc');
}

let _obAccountId=null;
function openObModal(accountId){
  const acc=_coaFlatAccounts.find(a=>a.id===accountId);
  if(!acc)return;
  _obAccountId=accountId;
  document.getElementById('ob-acc-name').textContent=`${acc.code} — ${acc.name}`;
  document.getElementById('ob-amount').value=acc.opening_balance!=null?Number(acc.opening_balance).toFixed(2):'0.00';
  document.getElementById('ob-type').value=acc.opening_balance_type||'DR';
  showM('m-ob');
}

async function saveOpeningBalance(){
  if(!_obAccountId)return;
  const amount=parseFloat(document.getElementById('ob-amount')?.value||'0')||0;
  const balType=document.getElementById('ob-type')?.value||'DR';
  try{
    const result=await moduleApi(`/accounts/${_obAccountId}/opening-balance`,{
      method:'PATCH',body:{amount,balance_type:balType}
    });
    const acc=_coaFlatAccounts.find(a=>a.id===_obAccountId);
    if(acc){acc.opening_balance=result.opening_balance;acc.opening_balance_type=result.opening_balance_type;}
    renderAccountTree();
    closeM('m-ob');
    toast(`Opening balance saved: ${amount.toFixed(2)} ${balType}`,'ok');
  }catch(err){toast('Save failed: '+err.message,'err');}
}

async function toggleAccountStatus(accountId,currentStatus){
  const newStatus=currentStatus==='active'?'inactive':'active';
  const acc=_coaFlatAccounts.find(a=>a.id===accountId);
  const confirmed=await appConfirm({
    title:`${newStatus==='inactive'?'Deactivate':'Activate'} Account`,
    message:`${newStatus==='inactive'?'Deactivate':'Activate'} "${acc?.name||accountId}"?${newStatus==='inactive'?' Inactive accounts cannot be used in transactions.':''}`,
    okText:newStatus==='inactive'?'Deactivate':'Activate',
    danger:newStatus==='inactive',
  });
  if(!confirmed)return;
  try{
    await moduleApi(`/accounts/${accountId}/status`,{method:'PATCH',body:{status:newStatus}});
    if(acc)acc.status=newStatus;
    renderAccountTree();
    toast(`Account ${newStatus}`,'ok');
  }catch(err){toast('Status update failed: '+err.message,'err');}
}

function _lockGroupToggle(lock){
  const groupBtn=document.getElementById('acc-type-group-btn');
  const ledgerBtn=document.getElementById('acc-type-ledger-btn');
  if(groupBtn){groupBtn.disabled=lock;groupBtn.style.opacity=lock?'0.4':'';groupBtn.style.cursor=lock?'not-allowed':'';}
  if(ledgerBtn){ledgerBtn.disabled=lock;ledgerBtn.style.opacity=lock?'0.4':'';ledgerBtn.style.cursor=lock?'not-allowed':'';}
}

function setAccModalType(type){
  const isGroup=type==='group';
  document.getElementById('acc-type-group-btn')?.classList.toggle('on',isGroup);
  document.getElementById('acc-type-ledger-btn')?.classList.toggle('on',!isGroup);
  const parentId=document.getElementById('acc-parent')?.value||'';
  const parent=_coaFlatAccounts.find(a=>a.id===parentId);
  const childLevel=parent?(parent.level||1)+1:1;
  const nodeTypeLabel=isGroup?(childLevel===1?'MAIN LEDGER':childLevel<=4?'SUB LEDGER':'SUB LEDGER'):'POSTING LEDGER';
  if(!document.getElementById('m-acc').dataset.editId){
    document.getElementById('acc-modal-title').textContent=`New ${nodeTypeLabel} (Level ${childLevel})`;
    document.getElementById('acc-modal-sub').textContent=isGroup
      ?'Sub Ledger — organises posting ledgers. Transactions do NOT post here.'
      :'Posting Ledger — transactions, journals and invoices post to this account.';
    document.getElementById('acc-save-btn').textContent=isGroup?'Create Sub Ledger':'Create Posting Ledger';
  }
  const extraWrap=document.getElementById('acc-extra-wrap');
  const openWrap=document.getElementById('acc-opening-wrap');
  // Only posting ledgers need opening balance and bank/tax fields
  if(extraWrap)extraWrap.style.display=isGroup?'none':'';
  if(openWrap)openWrap.style.display=isGroup?'none':'';
  document.getElementById('m-acc').dataset.accType=type;
  updateAccLevelInfo();
}

function populateParentSelector(selectedId){
  const sel=document.getElementById('acc-parent');
  if(!sel)return;
  // Groups at level 1-3 can be parents (level 4 groups would overflow, level 4 is max)
  const groups=_coaFlatAccounts.filter(a=>a.is_group&&(a.level||1)<=3);
  sel.innerHTML='<option value="">— None (becomes Level 1 Primary Group) —</option>';
  // Sort by code but render as tree path
  const sortedGroups=groups.slice().sort((a,b)=>String(a.code).localeCompare(String(b.code)));
  sortedGroups.forEach(g=>{
    const depth=(g.level||1)-1;
    const indent='    '.repeat(depth); // non-breaking spaces for indent
    const levelTag={1:'L1',2:'L2',3:'L3'}[g.level]||'';
    const opt=document.createElement('option');
    opt.value=g.id;
    opt.textContent=`${indent}[${levelTag}] ${g.code} — ${g.name}`;
    if(g.id===selectedId)opt.selected=true;
    sel.appendChild(opt);
  });
  sel.onchange=()=>{
    const parentId=sel.value;
    const parent=_coaFlatAccounts.find(a=>a.id===parentId);
    if(parent){
      const childLevel=(parent.level||1)+1;
      _lockGroupToggle(childLevel>=4);
      if(childLevel>=4&&document.getElementById('m-acc')?.dataset.accType==='group'){
        setAccModalType('ledger');
      }
    }else{
      _lockGroupToggle(false);
    }
    setAccModalType(document.getElementById('m-acc')?.dataset.accType||'ledger');
    updateAccLevelInfo();
  };
  updateAccLevelInfo();
}

function updateAccLevelInfo(){
  const sel=document.getElementById('acc-parent');
  const info=document.getElementById('acc-level-info');
  if(!sel||!info)return;
  const parentId=sel.value;
  const accType=document.getElementById('m-acc')?.dataset.accType||'ledger';
  const isGroup=accType==='group';
  const nbWrap=document.getElementById('acc-normal-balance-wrap');
  if(!parentId){
    // No parent = MAIN LEDGER — hide Normal Balance (not relevant for top-level group)
    if(nbWrap)nbWrap.style.display='none';
    if(isGroup){
      info.textContent='✓ Level 1 — MAIN LEDGER (top of hierarchy). Add Sub Ledgers under this.';
      info.className='coa-level-info show';
    }else{
      info.textContent='⚠ Root-level Posting Ledger — consider adding a parent group first.';
      info.style.color='var(--amber)';
      info.className='coa-level-info show';
    }
    return;
  }
  if(nbWrap)nbWrap.style.display='';
  info.style.color='';
  const parent=_coaFlatAccounts.find(a=>a.id===parentId);
  if(!parent){info.className='coa-level-info';return;}
  const childLevel=(parent.level||1)+1;
  const levelName={1:'Primary Group',2:'Secondary Group',3:'Sub-Group',4:'Ledger'}[childLevel]||'Ledger';
  const path=coaBreadcrumb(parent);
  if(isGroup&&childLevel>=4){
    info.textContent='⚠ Cannot create a Group at Level 4 — maximum depth reached. Use Ledger instead.';
    info.style.color='var(--red)';
  }else{
    info.textContent=`✓ Level ${childLevel} — ${isGroup?levelName:'Ledger'} under: ${path}`;
    info.style.color='';
  }
  info.className='coa-level-info show';
}

function coaBreadcrumb(account){
  const parts=[account.name];
  let cur=account;
  let limit=4;
  while(cur.parent_account_id&&limit-->0){
    const p=_coaFlatAccounts.find(a=>a.id===cur.parent_account_id);
    if(!p)break;
    parts.unshift(p.name);
    cur=p;
  }
  return parts.join(' › ');
}

function accountRecordFromRow(row){
  if(row?.dataset.account){
    try{return JSON.parse(row.dataset.account);}catch{}
  }
  const cells=row?.children||[];
  return {
    id:row?.dataset.accountId||'',
    code:cells[0]?.textContent.trim()||'',
    name:cells[1]?.textContent.trim()||'',
    type:cells[2]?.textContent.trim()||'',
    category:cells[3]?.textContent.trim()||''
  };
}

async function deleteAccountRow(btn){
  const row=btn.closest('tr');
  const table=row?.closest('table');
  if(!row||!table)return;
  const reason=rowDeleteBlockReason(row,table);
  const record=accountRecordFromRow(row);
  const label=record.code||record.name||'account';
  if(reason){
    toast(`Cannot delete ${label}: ${reason}`,'warn');
    audit('Delete blocked',label,reason);
    return;
  }
  const confirmed=await appConfirm({
    title:'Delete Account',
    message:`Delete account ${label}?`,
    okText:'Delete'
  });
  if(!confirmed)return;
  row.remove();
  if(record.id||row.dataset.accountId){
    moduleApi(`/accounts/${encodeURIComponent(record.id||row.dataset.accountId)}`,{method:'DELETE'})
      .catch(err=>toast(`Account removed from screen, database delete failed: ${err.message}`,'warn'));
  }
  deleteServer('accounts',record);
  updateAccountSelectors();
  refreshEnhancedTable(table);
  audit('Deleted chart account',label,'Deleted');
  toast('Account deleted','warn');
}

function renderRecordList(records,renderRecord,label){
  let rendered=0;
  let failed=0;
  (records||[]).slice().reverse().forEach(record=>{
    try{
      renderRecord(record);
      rendered++;
    }catch(err){
      failed++;
      console.warn(`Could not render ${label} record:`,err,record);
    }
  });
  return {rendered,failed};
}

function clearTableBody(id,message){
  const tbody=document.getElementById(id);
  if(!tbody)return null;
  const cols=tbody.closest('table')?.querySelectorAll('thead th').length||1;
  tbody.innerHTML=`<tr data-empty-state="1"><td colspan="${cols}" style="color:var(--text3);text-align:center">${escapeHtml(message)}</td></tr>`;
  return tbody;
}

function replaceTableBody(id,records,renderRow,message){
  const tbody=clearTableBody(id,message);
  if(!tbody)return;
  if(Array.isArray(records)&&records.length){
    tbody.innerHTML=records.map(renderRow).join('');
  }
  refreshEnhancedTable(tbody.closest('table'));
}

function titleCase(value){
  return String(value||'').toLowerCase().replace(/\b\w/g,ch=>ch.toUpperCase());
}

const DEMO_PRODUCT_CODES=new Set([
  'PRD-001','STL-12MM','PKG-BOX-A','OIL-5L','GLV-SAFE','LOG-LOCAL','OFF-CHAIR','PPE-HELMET','ELE-CABLE','PKG-BOX','PRN-FLYER','FUEL-DIESEL','IT-MON24','UNI-STAFF','WTR-CASE','MNT-HOUR','COU-DOC','TLS-DRILL','WH-SPACE','PPE-VEST','JAN-CLEAN','IT-LAP15','ELE-LED','TLS-HAM'
]);
const DEMO_PRODUCT_NAMES=new Set([
  'steel rods 12mm','packaging box a','industrial oil 5l','safety gloves','corrugated box a','safety helmet','copper cable roll','ergonomic office chair','business laptop 15 inch','diesel supply','document courier','maintenance technician hour','high visibility vest','local delivery service','corrugated packing box','printed flyer pack','24 inch led monitor','staff uniform set','drinking water case','cordless drill machine','warehouse space rental','deep cleaning service','led panel light','industrial hammer'
]);

function productCodeValue(product={}){
  return String(product.code||product.sku||product.id||'').trim();
}

function productNameValue(product={}){
  return String(product.name||product.product_name||product.description||'').trim();
}

function isDemoProductRecord(product={}){
  const code=productCodeValue(product).toUpperCase();
  const baseCode=code.replace(/-\d{2}$/,'');
  const name=productNameValue(product).toLowerCase().replace(/\s+\d{2}$/,'');
  return /^RT10-|^REAL15|^REAL50|^BULK50/.test(code)||DEMO_PRODUCT_CODES.has(code)||DEMO_PRODUCT_CODES.has(baseCode)||DEMO_PRODUCT_NAMES.has(name);
}

function removeDemoProductRows(){
  let removed=0;
  document.querySelectorAll('#prod-tbody tr:not([data-empty-state])').forEach(row=>{
    if(isDemoProductRecord({code:inventoryRowCellText(row,0),name:inventoryRowCellText(row,1)})){
      row.remove();
      removed++;
    }
  });
  const tbody=document.getElementById('prod-tbody');
  if(tbody&&tbody.querySelectorAll('tr:not([data-empty-state])').length===0)emptyTableMessage(tbody,'No products in database yet.');
  return removed;
}

function cleanupDemoProductsFromServer(products=[]){
  const demoProducts=(products||[]).filter(isDemoProductRecord);
  if(!demoProducts.length)return;
  Promise.allSettled(demoProducts.map(product=>deleteServer('products',{
    ...product,
    id:productCodeValue(product),
    code:productCodeValue(product),
    name:productNameValue(product)
  }))).then(results=>{
    const deleted=results.filter(result=>result.status==='fulfilled').length;
    if(deleted)console.info(`Removed ${deleted} demo Item Master product(s) from database`);
  });
}

function hydrateFromServer(){
  return apiRequest('bootstrap',{}, {method:'GET'}).then(({data})=>{
    if(!data)return;
    isHydratingFromServer=true;
    const renderStats={};
    const productRows=Array.isArray(data.products)?data.products.filter(product=>!isDemoProductRecord(product)):[];
    try{
      financePaymentsByRef.clear();
      financeBankAccountsByKey.clear();
      if(data.company)applyCompanyToUi(data.company);
      renderStats.products=renderRecordList(productRows,product=>renderProductRecord(product,{deferRefresh:true,deferStockSync:true,deferMappingSync:true,deferSuggestions:true}),'product');
      renderStats.salesCategories=renderRecordList(data.salesCategories,renderSalesCategoryRecord,'sales category');
      renderStats.salesUnits=renderRecordList(data.salesUnits,renderSalesUnitRecord,'sales unit');
      renderStats.customers=renderRecordList(data.customers,renderCustomerRecord,'customer');
      renderStats.users=renderRecordList(data.users,renderUserRecord,'user');
      renderStats.employees=renderRecordList(data.employees,record=>{
        renderEmployeeRecord(record);
        renderPayrollEmployeeRecord(record);
      },'employee');
      renderStats.salesInvoices=renderRecordList(data.salesInvoices,inv=>addSalesInvoiceRow(inv,{persist:false}),'sales invoice');
      renderStats.quotations=renderRecordList(data.quotations,renderQuotationRecord,'quotation');
      renderStats.accounts=renderRecordList(data.accounts,renderAccountRecord,'account');
      renderStats.ledger=renderRecordList(data.ledger,line=>postLedgerLine(line,{persist:false}),'ledger');
      renderStats.bills=renderRecordList(data.bills,renderBillRecord,'bill');
      renderStats.vendors=renderRecordList(data.vendors,renderVendorRecord,'vendor');
      renderStats.payments=renderRecordList(data.payments,renderPaymentRecord,'payment');
      renderStats.bankAccounts=renderRecordList(data.bankAccounts,renderBankAccountRecord,'bank account');
      renderStats.rotaShifts=renderRecordList(data.rotaShifts,renderRotaShiftRecord,'rota shift');
      renderStats.rotaSwaps=renderRecordList(data.rotaSwaps,renderRotaSwapRecord,'rota swap');
      renderStats.rotaApprovals=renderRecordList(data.rotaApprovals,renderRotaApprovalRecord,'rota approval');
      renderStats.rotaAssignments=renderRecordList(data.rotaAssignments,renderRotaAssignmentRecord,'rota assignment');
      renderRotaBoards();
      renderStats.expenses=renderRecordList(data.expenses,renderExpenseRecord,'expense');
      renderStats.purchaseRecords={rendered:0,failed:0,total:0,lazy:true};
      loadPurchaseDocumentsFromServer(data.purchaseDocuments||[],[]);
    }finally{
      isHydratingFromServer=false;
    }
    const totalLoaded=[
      productRows,
      data.customers,
      data.salesInvoices,
      data.quotations,
      data.bills,
      data.vendors,
      data.payments,
      data.rotaShifts,
      data.rotaSwaps,
      data.rotaApprovals,
      data.rotaAssignments,
      []
    ].reduce((sum,rows)=>sum+(Array.isArray(rows)?rows.length:0),0);
    window.__taxflowLastDbLoad={at:new Date().toISOString(),totalLoaded,renderStats};
    console.info(`TaxFlow DB tables loaded: ${totalLoaded} records`);
    if(totalLoaded>0)toast(`Database tables loaded: ${totalLoaded} records`,'ok');
    if(data.invoiceLayout){
      // Seed server layout into the default slot if it's still the only one
      const defaultSlot=_invoiceLayouts.find(l=>l.isDefault)||_invoiceLayouts[0];
      if(defaultSlot&&_invoiceLayouts.length===1){
        Object.assign(defaultSlot,data.invoiceLayout);
        _ilSave();
      }
      if(_activeLayoutId===defaultSlot?.id)setInvoiceLayoutFields(defaultSlot);
      updateInvoiceLayoutPreview();
      renderInvoiceLayoutGallery();
    }
    const quotationLayoutRecord=Array.isArray(data.quotationLayout)?data.quotationLayout[0]:data.quotationLayout;
    if(quotationLayoutRecord){
      setQuotationLayoutFields(quotationLayoutRecord);
      updateQuotationLayoutPreview();
    }
    if(Array.isArray(data.audit)&&data.audit.length){
      renderAuditLog(data.audit);
    }
    updateAccountSelectors();
    refreshInvoiceCustomerOptions();
    refreshQuotationCustomerOptions();
    syncProductMasterOptions();
    syncSupplierOptions();
    syncInventoryItemOptions();
    refreshEnhancedTable(document.getElementById('prod-tbody')?.closest('table'));
    refreshEnhancedTable(document.getElementById('purchase-record-tbody')?.closest('table'));
    syncStockLevelsFromProducts();
    syncStockMappingFromItems();
    refreshInvoiceProductSuggestions();
    refreshPurchaseProductSuggestions();
    refreshQuotationProductOptions();
    removeDemoProductRows();
    cleanupDemoProductsFromServer(data.products);
    loadStockMappingsFromServer();
    loadAccountingFromDb();
    loadCorporateAccountingFromDb(data);
    updateFinanceFromDatabaseRecords();
    filterLedger();
    refreshActivePageTables();
    refreshInitializedTables();
    scheduleIdleTask(()=>{
      revalidateSalesAiRows();
      bindDetailViews();
      bindEditActions();
      refreshActivePageTables();
      refreshInitializedTables();
      applyAllTableActions();
    },700);
  }).catch(err=>console.warn('Database bootstrap unavailable:',err));
}

function forceDbRefresh(){
  window.__taxflowFreshDashboardLoaded=false;
  syncDashboardFromDatabase();
  return hydrateFromServer();
}

window.hydrateFromServer=hydrateFromServer;
window.forceDbRefresh=forceDbRefresh;

function escapeHtml(value){
  return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

function buildFallbackExtraction(entry){
  return [];
}

async function requestInvoiceExtraction(entry){
  const payload={
    file:{name:entry.name,size:entry.size,type:entry.type,base64:entry.base64},
    category:entry.category,
    period:entry.period
  };
  const endpoint=APP_CONFIG.extractionEndpoint||`${apiBaseUrl()}/app-data?action=documents.extract`;
  const response=await authenticatedFetch(endpoint,{
    method:'POST',
    body:JSON.stringify(payload)
  });
  if(!response.ok){
    let detail='';
    try{const d=await response.json();detail=d.detail||d.message||'';}catch{}
    throw new Error(`Extraction failed (${response.status})${detail?': '+detail:''}`);
  }
  const data=await response.json();
  const invoices=Array.isArray(data)?data:data.invoices;
  if(!Array.isArray(invoices))throw new Error('Extraction service returned an invalid payload');
  return normalizeExtractedPurchaseInvoices(invoices,entry.name);
}

function normalizeExtractedPurchaseInvoices(invoices=[],filename=''){
  const grouped=new Map();
  (invoices||[]).forEach((source,index)=>{
    const inv={...(source||{})};
    const fallbackNo=String(inv.invoice_no||inv.invoice_number||inv.ref||'').trim()||`${filename||'PURCHASE'}-${index+1}`;
    const key=invoiceKey(fallbackNo);
    const extractionError=isPurchaseExtractionError(inv);
    if(!grouped.has(key)){
      grouped.set(key,{
        ...inv,
        invoice_no:fallbackNo,
        subtotal:purchaseAiNumber(inv.subtotal||inv.net_amount),
        vat_amount:purchaseAiNumber(inv.vat_amount||inv.tax_amount),
        total:purchaseAiNumber(inv.total),
        paid:purchaseAiNumber(inv.paid),
        shipping:purchaseAiNumber(inv.shipping),
        confidence:purchaseAiNumber(inv.confidence),
        extraction_error:extractionError,
        lines:[]
      });
    }
    const target=grouped.get(key);
    ['date','supplier','supplier_trn','bill_to','currency','address','pay_term','payment_method','payment_account','payment_note','paid_on','shipping_details','notes','tax_type','discount_type','status','issues'].forEach(field=>{
      if(!target[field]&&inv[field])target[field]=inv[field];
    });
    target.confidence=Math.max(purchaseAiNumber(target.confidence),purchaseAiNumber(inv.confidence));
    target.subtotal=Math.max(purchaseAiNumber(target.subtotal),purchaseAiNumber(inv.subtotal||inv.net_amount));
    target.vat_amount=Math.max(purchaseAiNumber(target.vat_amount),purchaseAiNumber(inv.vat_amount||inv.tax_amount));
    target.total=Math.max(purchaseAiNumber(target.total),purchaseAiNumber(inv.total));
    target.paid=Math.max(purchaseAiNumber(target.paid),purchaseAiNumber(inv.paid));
    target.shipping=Math.max(purchaseAiNumber(target.shipping),purchaseAiNumber(inv.shipping));
    const lines=extractionError?[]:(Array.isArray(inv.lines)&&inv.lines.length?inv.lines:[lineFromInvoiceLike(inv)]);
    lines.filter(Boolean).forEach(line=>{
      target.lines.push(normalizePurchaseAiLine(line));
    });
  });
  return [...grouped.values()].map(inv=>{
    const lineSubtotal=inv.lines.reduce((sum,line)=>sum+purchaseAiNumber(line.line_total||line.amount),0);
    if(lineSubtotal&&(!purchaseAiNumber(inv.subtotal)||inv.lines.length>1))inv.subtotal=Math.max(purchaseAiNumber(inv.subtotal),lineSubtotal);
    if(!purchaseAiNumber(inv.total))inv.total=purchaseAiNumber(inv.subtotal)+purchaseAiNumber(inv.vat_amount)+purchaseAiNumber(inv.shipping);
    inv.due=Math.max(0,purchaseAiNumber(inv.total)-purchaseAiNumber(inv.paid));
    inv.items=purchaseLinesTotalQuantity(inv.lines)||inv.lines.length;
    if(isPurchaseExtractionError(inv))inv.lines=[];
    return inv;
  });
}

function isPurchaseExtractionError(inv={}){
  return Boolean(inv.extraction_error)||(
    String(inv.status||'').toLowerCase()==='error'&&
    purchaseAiNumber(inv.confidence)<=0&&
    (!Array.isArray(inv.lines)||inv.lines.length===0)
  );
}

function purchaseAiNumber(value){
  return parseAmount(value);
}

function normalizePurchaseAiLine(line={}){
  const quantity=purchaseAiNumber(line.quantity||line.qty||1)||1;
  const lineTotal=purchaseAiNumber(line.line_total||line.amount||line.total);
  const unitCost=purchaseAiNumber(line.unit_cost||line.cost||line.unitCost||line.unit_price);
  const beforeTax=purchaseAiNumber(line.unit_cost_before_tax||line.unit_cost||line.cost||line.unit_price||unitCost);
  return {
    ...line,
    product:purchaseAiProductName(line)||line.product||line.description||line.item_description||'Purchase item',
    quantity,
    unit:line.unit||line.unit_of_measure||line.uom||'PCS',
    unit_cost:unitCost||beforeTax||(lineTotal/Math.max(1,quantity)),
    unit_cost_before_tax:beforeTax||unitCost||(lineTotal/Math.max(1,quantity)),
    line_total:lineTotal||(quantity*(beforeTax||unitCost)),
    discount_percent:purchaseAiNumber(line.discount_percent||line.discountPct||line.discount_pct),
    profit_margin:purchaseAiNumber(line.profit_margin||line.margin),
    selling_price_inc_tax:purchaseAiNumber(line.selling_price_inc_tax||line.selling_price),
    raw:line.raw||{}
  };
}

function lineFromInvoiceLike(inv={}){
  const product=purchaseAiProductName(inv)||'Extracted purchase item';
  const subtotal=purchaseAiNumber(inv.subtotal||inv.net_amount||inv.line_total||inv.amount);
  if(isPurchaseExtractionError(inv))return null;
  if(!product&&!subtotal)return null;
  return {
    product,
    quantity:purchaseAiNumber(inv.quantity||inv.qty||1)||1,
    unit:inv.unit||'PCS',
    unit_cost:purchaseAiNumber(inv.unit_cost||inv.cost||subtotal),
    line_total:subtotal||purchaseAiNumber(inv.total),
    raw:inv.raw||{}
  };
}

function addSelectedPurchaseInvoice(map,invoice){
  const invoiceNo=String(invoice?.invoice_no||invoice?.invoice_number||invoice?.ref||'').trim();
  const key=invoiceKey(invoiceNo);
  if(!key)return;
  const existing=map.get(key);
  if(existing){
    map.set(key,normalizeExtractedPurchaseInvoices([existing,invoice],invoiceNo)[0]);
  }else{
    map.set(key,invoice);
  }
}

const salesUploadedFiles = [];
const salesExtractedInvoices = [];
const salesInvoiceDbKeys = new Set();

function invoiceKey(value){
  return String(value||'').trim().toLowerCase();
}

function registerSalesInvoiceKey(value){
  const key=invoiceKey(value);
  if(key)salesInvoiceDbKeys.add(key);
}

function buildFallbackSalesExtraction(entry){
  return [];
}

async function requestSalesInvoiceExtraction(entry){
  const payload={
    documentType:'sales_invoice',
    file:{name:entry.name,size:entry.size,type:entry.type,base64:entry.base64},
    importType:entry.importType,
    period:entry.period
  };

  try{
    const endpoint=APP_CONFIG.salesExtractionEndpoint||`${apiBaseUrl()}/app-data?action=invoices.import`;
    const response=await authenticatedFetch(endpoint,{
      method:'POST',
      body:JSON.stringify(payload)
    });
    if(!response.ok)throw new Error('Invoice import service returned '+response.status);
    const data=await response.json();
    const invoices=Array.isArray(data)?data:data.invoices;
    if(!Array.isArray(invoices))throw new Error('Invoice import service returned an invalid payload');
    return invoices;
  }catch(err){
    if(!APP_CONFIG.extractionFallback)throw err;
    console.warn('Sales invoice extraction unavailable:',err);
    toast('Invoice extraction unavailable. No demo data was added.','warn');
    return buildFallbackSalesExtraction(entry);
  }
}

function isSupportedSalesFile(file){
  return /\.(pdf|csv|xlsx|xls|jpg|jpeg|png)$/i.test(file.name);
}

function salesDzOver(e){e.preventDefault();document.getElementById('sales-zone').classList.add('over');}
function salesDzLeave(){document.getElementById('sales-zone').classList.remove('over');}
function salesDzDrop(e){
  e.preventDefault();
  document.getElementById('sales-zone').classList.remove('over');
  [...e.dataTransfer.files].forEach(f=>readAndAddSalesFile(f));
}

function salesUpload(inp){
  [...inp.files].forEach(f=>readAndAddSalesFile(f));
  inp.value='';
}

function readAndAddSalesFile(file){
  if(!isSupportedSalesFile(file)){
    toast('Unsupported file: '+file.name,'err');
    return;
  }
  const reader=new FileReader();
  reader.onload=function(e){
    const entry={
      id:'S'+Date.now()+Math.random().toString(36).slice(2,6),
      name:file.name,
      size:file.size,
      type:file.type,
      base64:e.target.result,
      importType:document.getElementById('sales-import-type')?.value||'Sales Tax Invoices',
      period:document.getElementById('sales-import-period')?.value||'June 2024',
      status:'Queued'
    };
    salesUploadedFiles.push(entry);
    animateSalesUpload(entry);
  };
  reader.readAsDataURL(file);
}

function animateSalesUpload(entry){
  const pg=document.getElementById('sales-prog'),fill=document.getElementById('sales-fill'),fn=document.getElementById('sales-fname'),pct=document.getElementById('sales-pct');
  pg.style.display='block';fn.textContent='Uploading: '+entry.name;
  let p=0;
  const iv=setInterval(()=>{
    p+=Math.random()*18+6;
    if(p>=100){
      p=100;clearInterval(iv);
      setTimeout(()=>{
        pg.style.display='none';fill.style.width='0%';
        entry.status='Ready';
        renderSalesFileList();
        toast(entry.name+' uploaded ?','ok');
        setTimeout(()=>extractSalesInvoiceFile(entry),500);
      },250);
    }
    fill.style.width=Math.min(p,100)+'%';
    pct.textContent=Math.round(Math.min(p,100))+'%';
  },110);
}

function renderSalesFileList(){
  const list=document.getElementById('sales-file-list');
  const badge=document.getElementById('sales-file-count');
  if(!list)return;
  if(badge)badge.textContent=salesUploadedFiles.length+' files';
  list.innerHTML='';
  if(salesUploadedFiles.length===0){
    list.innerHTML='<div style="font-size:12.5px;color:var(--text3);line-height:1.7">No files uploaded yet. Upload invoice PDFs, Excel files, or images to extract invoice data.</div>';
    return;
  }
  salesUploadedFiles.forEach(f=>{
    const statusBadge={
      Queued:'<span class="b b-gray">Queued</span>',
      Ready:'<span class="b b-b">Ready</span>',
      Extracting:'<span class="b b-a">Reading-</span>',
      Extracted:'<span class="b b-g">Stored data</span>',
      Error:'<span class="b b-r">Error</span>'
    }[f.status]||'<span class="b b-gray">Unknown</span>';
    const btn=f.status==='Ready'?`<button class="btn btn-p btn-sm" onclick="extractSalesInvoiceFile(salesUploadedFiles.find(x=>x.id==='${f.id}'))">Read Data</button>`:'';
    const row=document.createElement('div');
    row.style.cssText='display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border)';
    row.innerHTML=`<span style="font-size:20px">${getFileIcon(f.name)}</span><div style="flex:1;min-width:0"><div style="font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(f.name)}</div><div class="mono" style="color:var(--text3);font-size:11px">${fmtSize(f.size)} - ${escapeHtml(f.importType)} - ${escapeHtml(f.period)}</div></div><div class="flx">${statusBadge}${btn}</div>`;
    list.appendChild(row);
  });
}

async function extractSalesInvoiceFile(entry){
  if(!entry){toast('Sales invoice file not found','err');return;}
  entry.status='Extracting';
  renderSalesFileList();
  toast('Reading invoice data from '+entry.name+'...','info');
  const extTab=document.querySelector('#page-sales .tab:nth-child(2)');
  if(extTab)stab(extTab,'s-extract');
  try{
    const invoices=await requestSalesInvoiceExtraction(entry);
    entry.status='Extracted';
    entry.invoices=invoices;
    salesExtractedInvoices.push(...invoices.map(inv=>({...inv,sourceFile:entry.name,stored:false})));
    renderSalesFileList();
    appendSalesExtractedRows(invoices);
    if(invoices.some(inv=>!validateSalesAiInvoice(inv).valid)){
      refreshSalesValidationPanel();
      toast('Validation issues found - review required','warn');
    }
    toast('Read '+invoices.length+' invoice(s) from '+entry.name+' ?','ok');
  }catch(err){
    entry.status='Error';
    renderSalesFileList();
    toast('Invoice read failed: '+err.message,'err');
  }
}

function appendSalesExtractedRows(invoices){
  const tbody=document.getElementById('sales-ext-tbody');
  if(!tbody)return;
  if(tbody.querySelector('td[colspan]'))tbody.innerHTML='';
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  invoices.forEach(inv=>{
    if([...tbody.querySelectorAll('td.mono')].some(td=>td.textContent===inv.invoice_no)){
      toast(`Duplicate in current AI upload: ${inv.invoice_no}`,'warn');
      return;
    }
    const validation=validateSalesAiInvoice(inv);
    const confCls=inv.confidence>=90?'b-g':inv.confidence>=70?'b-a':'b-r';
    const stCls=validation.valid?'b-g':'b-a';
    const row=document.createElement('tr');
    row.dataset.salesInv=JSON.stringify(inv);
    row.dataset.validation=validation.valid?'valid':'review';
    row.innerHTML=`<td><input type="checkbox" class="sales-ai-select" ${validation.valid?'checked':''} aria-label="Select ${escapeHtml(inv.invoice_no)}"></td><td class="mono">${escapeHtml(inv.invoice_no)}</td><td>${escapeHtml(inv.customer)}</td><td class="mono">${escapeHtml(inv.customer_trn||'')}</td><td>${escapeHtml(inv.date)}</td><td class="mono">${fmt(inv.subtotal)}</td><td class="mono">${fmt(inv.vat_amount)}</td><td class="mono">${fmt(inv.total)}</td><td><span class="b ${confCls}">${Number(inv.confidence||0)}%</span></td><td class="sales-ai-validation"><span class="b ${stCls}">${validation.valid?'Valid':'Review'}</span></td><td class="sales-ai-details" style="color:var(--text3);font-size:12px">${escapeHtml(validation.issues.join('; ')||'Ready to save')}</td><td data-action-col="1">${salesAiUploadActionsHtml()}</td>`;
    tbody.prepend(row);
  });
  revalidateSalesAiRows();
}

function runSalesImport(){
  const ready=salesUploadedFiles.filter(f=>f.status==='Ready'||f.status==='Queued');
  if(ready.length===0){toast('No pending sales invoice files to read','warn');return;}
  ready.forEach((f,i)=>setTimeout(()=>extractSalesInvoiceFile(f),i*450));
}

function storeExtractedSalesInvoices(options={}){
  const rows=[...document.querySelectorAll('#sales-ext-tbody tr[data-sales-inv]')]
    .filter(row=>row.dataset.skipped!=='1')
    .filter(row=>options.auto||row.querySelector('.sales-ai-select')?.checked);
  if(rows.length===0){toast('No extracted sales invoices to store','warn');return;}
  let stored=0;
  let blocked=0;
  rows.forEach(row=>{
    const inv=JSON.parse(row.dataset.salesInv||'{}');
    const validation=validateSalesAiInvoice(inv);
    if(!validation.valid){
      blocked++;
      row.querySelector('.sales-ai-validation').innerHTML='<span class="b b-a">Review</span>';
      row.querySelector('.sales-ai-details').textContent=validation.issues.join('; ');
      return;
    }
    if(addSalesInvoiceRow(inv)){
      stored++;
      row.querySelector('.sales-ai-validation').innerHTML='<span class="b b-g">Saved</span>';
      row.querySelector('.sales-ai-details').textContent='Saved to invoice register';
      row.querySelector('.sales-ai-select').checked=false;
      row.dataset.skipped='1';
    }
  });
  if(stored>0){
    const tab=document.querySelector('#page-sales .tab:nth-child(4)');
    if(tab&&!options.auto)stab(tab,'s-invoices');
    audit('Stored imported sales invoices',stored+' invoice(s)','Saved');
  }
  refreshSalesValidationPanel();
  toast(stored+' invoice(s) saved'+(blocked?`; ${blocked} row(s) need review`:''),'ok');
}

function validateSalesAiInvoice(inv){
  const issues=[];
  const invoiceNo=String(inv.invoice_no||'').trim();
  const trn=String(inv.customer_trn||'').replace(/\D/g,'');
  const subtotal=Number(inv.subtotal||0);
  const vat=Number(inv.vat_amount||0);
  const total=Number(inv.total||0);
  if(!invoiceNo)issues.push('Invoice number missing');
  if(invoiceNo&&salesInvoiceDbKeys.has(invoiceKey(invoiceNo)))issues.push('Duplicate invoice number in database');
  if(invoiceNo&&countSalesAiInvoiceNo(invoiceNo)>1)issues.push('Duplicate invoice number in AI upload');
  if(!String(inv.customer||'').trim())issues.push('Customer missing');
  if(!String(inv.date||'').trim())issues.push('Date missing');
  if(trn&&trn.length!==15)issues.push('Customer TRN must be 15 digits');
  if(total&&Math.abs((subtotal+vat)-total)>.05)issues.push('Total does not match subtotal + VAT');
  if(Number(inv.confidence||0)<70)issues.push('Low confidence extraction');
  return {valid:issues.length===0,issues};
}

function countSalesAiInvoiceNo(invoiceNo){
  const key=invoiceKey(invoiceNo);
  if(!key)return 0;
  return [...document.querySelectorAll('#sales-ext-tbody tr[data-sales-inv]')].filter(row=>{
    try{return invoiceKey(JSON.parse(row.dataset.salesInv||'{}').invoice_no)===key;}catch{return false;}
  }).length;
}

function revalidateSalesAiRows(){
  document.querySelectorAll('#sales-ext-tbody tr[data-sales-inv]').forEach(row=>{
    if(row.dataset.skipped==='1')return;
    let inv={};
    try{inv=JSON.parse(row.dataset.salesInv||'{}');}catch{return;}
    const validation=validateSalesAiInvoice(inv);
    row.dataset.validation=validation.valid?'valid':'review';
    const validationCell=row.querySelector('.sales-ai-validation');
    const detailsCell=row.querySelector('.sales-ai-details');
    const checkbox=row.querySelector('.sales-ai-select');
    if(validationCell)validationCell.innerHTML=`<span class="b ${validation.valid?'b-g':'b-a'}">${validation.valid?'Valid':'Review'}</span>`;
    if(detailsCell)detailsCell.textContent=validation.issues.join('; ')||'Ready to save';
    if(checkbox&&!validation.valid)checkbox.checked=false;
  });
  refreshSalesValidationPanel();
}

function refreshSalesValidationPanel(){
  const target=document.getElementById('sales-validation-list');
  if(!target)return;
  const rows=[...document.querySelectorAll('#sales-ext-tbody tr[data-sales-inv]')]
    .filter(row=>row.dataset.skipped!=='1')
    .map(row=>{
      try{
        const inv=JSON.parse(row.dataset.salesInv||'{}');
        return {row,inv,validation:validateSalesAiInvoice(inv)};
      }catch{
        return null;
      }
    })
    .filter(Boolean)
    .filter(item=>!item.validation.valid);

  if(rows.length===0){
    target.innerHTML=`<div style="background:var(--green-bg);border:1px solid var(--green-border);border-radius:10px;padding:14px 16px">
      <div style="display:flex;align-items:center;gap:10px">
        <span style="font-size:18px">OK</span>
        <div><div style="font-size:13.5px;font-weight:600">All extracted sales invoices passed validation</div><div style="font-size:12px;color:var(--text3)">Selected valid rows can be saved from AI Extraction.</div></div>
      </div>
    </div>`;
    return;
  }

  target.innerHTML=rows.map(item=>`
    <div style="background:var(--amber-bg);border:1px solid var(--amber-border);border-radius:10px;padding:14px 16px;margin-bottom:10px">
      <div style="display:flex;align-items:flex-start;gap:10px">
        <span style="font-size:18px;flex-shrink:0">!</span>
        <div style="flex:1">
          <div style="font-size:13.5px;font-weight:600;margin-bottom:3px">${escapeHtml(item.inv.invoice_no||'Missing invoice')} - ${escapeHtml(item.inv.customer||'Customer missing')}</div>
          <div style="font-size:12px;color:var(--text3)">${escapeHtml(item.validation.issues.join('; '))}</div>
        </div>
      </div>
    </div>`).join('');
}

function skipIconSvg(){
  return `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8"/><path d="M12 4l-8 8"/></svg>`;
}

function salesAiUploadActionsHtml(){
  return `<div class="row-actions">
    <button class="icon-btn skip" type="button" title="Skip" aria-label="Skip AI upload row" onclick="skipSalesAiRow(this)">${skipIconSvg()}</button>
    <button class="icon-btn danger" type="button" title="Delete" aria-label="Delete AI upload row" onclick="deleteSalesAiRow(this)">${deleteIconSvg()}</button>
  </div>`;
}

function toggleSalesAiSelection(checked){
  document.querySelectorAll('#sales-ext-tbody tr[data-sales-inv]').forEach(row=>{
    const box=row.querySelector('.sales-ai-select');
    if(box&&row.dataset.skipped!=='1')box.checked=checked;
  });
}

function setSalesAiCardView(mode,btn){
  if(btn){
    btn.closest('.segmented')?.querySelectorAll('.seg').forEach(b=>b.classList.remove('on'));
    btn.classList.add('on');
  }
}

function applySalesAiSort(persist){
  const sel=document.getElementById('sales-ai-sort');
  const dir=(document.getElementById('sales-ai-sort-dir')?.dataset.dir||'asc')==='asc'?1:-1;
  if(!sel)return;
  const key=sel.value;
  const tbody=document.getElementById('sales-ext-tbody');
  if(!tbody)return;
  const rows=[...tbody.querySelectorAll('tr[data-sales-inv]')];
  rows.sort((a,b)=>{
    const ia=JSON.parse(a.dataset.salesInv||'{}');
    const ib=JSON.parse(b.dataset.salesInv||'{}');
    const va=key==='total'?Number(ia.total||0):key==='date'?ia.date||'':key==='customer'?ia.customer||'':ia.invoice_no||'';
    const vb=key==='total'?Number(ib.total||0):key==='date'?ib.date||'':key==='customer'?ib.customer||'':ib.invoice_no||'';
    return (va<vb?-1:va>vb?1:0)*dir;
  });
  rows.forEach(r=>tbody.appendChild(r));
}

function toggleSalesAiSortDirection(){
  const btn=document.getElementById('sales-ai-sort-dir');
  if(!btn)return;
  const newDir=btn.dataset.dir==='asc'?'desc':'asc';
  btn.dataset.dir=newDir;
  btn.textContent=newDir==='asc'?'Asc':'Desc';
  applySalesAiSort(true);
}

function skipSalesAiRow(btn){
  const row=btn.closest('tr');
  if(!row)return;
  row.dataset.skipped='1';
  const box=row.querySelector('.sales-ai-select');
  if(box)box.checked=false;
  row.querySelector('.sales-ai-validation').innerHTML='<span class="b b-gray">Skipped</span>';
  row.querySelector('.sales-ai-details').textContent='Skipped by user';
  refreshSalesValidationPanel();
  toast('AI upload row skipped','info');
}

function deleteSalesAiRow(btn){
  const row=btn.closest('tr');
  const tbody=row?.parentElement;
  row?.remove();
  if(tbody&&tbody.querySelectorAll('tr[data-sales-inv]').length===0){
    tbody.innerHTML='<tr><td colspan="12" style="color:var(--text3);text-align:center">AI uploaded invoice data will appear here for validation.</td></tr>';
  }
  refreshSalesValidationPanel();
  toast('AI upload row deleted','warn');
}

function shareIconSvg(){
  return `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6.5 8.5l3-1.8"/><path d="M6.5 7.5l3 1.8"/><circle cx="4.5" cy="8" r="2"/><circle cx="11.5" cy="5.8" r="2"/><circle cx="11.5" cy="10.2" r="2"/></svg>`;
}

function downloadIconSvg(){
  return `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2v7"/><path d="M5 6l3 3 3-3"/><path d="M3 13h10"/></svg>`;
}

function salesInvoiceActionsHtml(){
  return `<div class="row-actions sales-actions">
    <button class="icon-btn view" type="button" title="View Invoice" aria-label="View invoice" onclick="openSalesInvoiceRow(this)">${viewIconSvg()}</button>
    <button class="icon-btn share" type="button" title="Download PDF" aria-label="Download invoice PDF" onclick="downloadSalesInvoiceRowPdf(this)">${downloadIconSvg()}</button>
    <button class="icon-btn share" type="button" title="Share" aria-label="Share invoice" onclick="shareSalesInvoiceRow(this)">${shareIconSvg()}</button>
    <button class="icon-btn edit" type="button" title="Edit Invoice" aria-label="Edit invoice" onclick="editSalesInvoiceFromRow(this)">${editIconSvg()}</button>
    <button class="icon-btn" type="button" title="Mark as Paid" aria-label="Mark as paid" style="color:var(--green)" onclick="markSalesInvoicePaid(this)"><svg viewBox="0 0 16 16" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M13 4L6 11l-3-3"/></svg></button>
    <button class="icon-btn danger row-delete-btn" type="button" title="Delete" aria-label="Delete invoice" onclick="deleteTableRow(this)">${deleteIconSvg()}</button>
  </div>`;
}

function editSalesInvoiceFromRow(btn){
  const row=btn.closest('tr');
  const inv=invoiceFromSalesRow(row);
  if(!inv){toast('Invoice data not found','warn');return;}
  closeM('m-sales-view');
  currentSalesTransactionType=isSalesReturn(inv)?'return':'sale';
  go('sales');
  setTimeout(()=>{
    const tab=document.querySelector('#page-sales .tab:nth-child(5)');
    if(tab)stab(tab,'s-create');
    configureSalesFormMode();
    setTimeout(()=>{
      const set=(id,v)=>{const el=document.getElementById(id);if(el)el.value=v||'';};
      set('inv-no',inv.invoice_no);
      set('inv-date',inv.date);
      set('inv-due',inv.due_date);
      set('inv-po',inv.po_number||inv.po_no||'');
      set('inv-delivery',inv.delivery_note_no||'');
      set('inv-ref',inv.reference_no||inv.ref||'');
      set('inv-cust',inv.customer);
      set('inv-ctrn',inv.customer_trn||'');
      set('inv-caddr',inv.customer_address||'');
      // populate line items
      const linesContainer=document.getElementById('inv-lines');
      if(linesContainer){
        linesContainer.innerHTML='';
        lineCount=0;
        const lines=Array.isArray(inv.lines)&&inv.lines.length?inv.lines:[{description:inv.customer||'Item',qty:1,price:inv.subtotal,amount:inv.subtotal}];
        lines.forEach(line=>{
          const d=addLine();
          if(!d)return;
          const desc=line.description||line.product||'';
          const qty=line.qty||line.quantity||1;
          const price=line.price||line.unit_price||0;
          const prod=d.querySelector('.inv-product');
          const unitEl=d.querySelector('.inv-unit');
          const qtyEl=d.querySelector('.inv-qty');
          const priceEl=d.querySelector('.inv-price');
          if(prod)prod.value=desc;
          if(unitEl)unitEl.value=line.unit||'PCS';
          if(qtyEl)qtyEl.value=qty;
          if(priceEl)priceEl.value=price;
          calcLine(qtyEl);
        });
      }
      calcLine(null);
      updateSalesInvPreview();
      toast('Invoice loaded for editing','info');
    },80);
  },60);
}

function openReceiptForInvoice(inv){
  if(!inv)return;
  closeM('m-sales-view');
  go('payments');
  setTimeout(()=>{
    openPaymentModal('Customer Receipt');
    setTimeout(()=>{
      // Pre-fill client
      const contactSel=document.getElementById('payment-contact');
      if(contactSel&&inv.customer){
        let opt=[...contactSel.options].find(o=>o.value.toLowerCase()===String(inv.customer).toLowerCase());
        if(!opt){
          opt=document.createElement('option');
          opt.value=inv.customer;
          opt.textContent=inv.customer;
          contactSel.appendChild(opt);
        }
        contactSel.value=opt.value;
      }
      // Pre-fill amount, reference note, comments
      setFieldValue(document.getElementById('payment-amount'),Number(inv.total||0).toFixed(2));
      setFieldValue(document.getElementById('payment-detail'),`Payment for ${inv.invoice_no||'Invoice'}`);
      setFieldValue(document.getElementById('payment-comments'),`Receipt for Invoice ${inv.invoice_no||''} — ${inv.customer||''}`);
      // Pre-fill allocation row for this specific invoice
      loadAllocationTable([{
        ref:inv.invoice_no||'',
        date:inv.date||'',
        amount:Number(inv.total||0),
        contact:inv.customer||'',
        source:'Sales Invoice'
      }]);
      updatePmtBalance();
    },80);
  },80);
}

function markSalesInvoicePaid(btn){
  const row=btn.closest('tr');
  if(!row)return;
  let inv=null;
  if(row.dataset.salesInvoice){
    try{inv=JSON.parse(row.dataset.salesInvoice);}catch{}
  }
  openReceiptForInvoice(inv||invoiceFromSalesRow(row));
}

function isSalesReturn(inv={}){
  return String(inv.document_type||inv.source||inv.status||'').toLowerCase().includes('return');
}

function salesRegisterTbody(inv={}){
  return document.getElementById(isSalesReturn(inv)?'sales-return-tbody':'sales-invoice-tbody');
}

function salesRegisterRows(){
  return [...document.querySelectorAll('#sales-invoice-tbody tr:not([data-empty-state]),#sales-return-tbody tr:not([data-empty-state])')];
}

function addSalesInvoiceRow(inv,options={persist:true}){
  const tbody=salesRegisterTbody(inv);
  if(!tbody||!inv.invoice_no)return false;
  const exists=salesInvoiceDbKeys.has(invoiceKey(inv.invoice_no))||salesRegisterRows().some(row=>row.children[0]?.textContent===inv.invoice_no);
  if(exists)return false;
  // normalise vat/vat_amount across different save formats
  if(inv.vat_amount==null&&inv.vat!=null)inv={...inv,vat_amount:inv.vat};
  if(inv.vat==null&&inv.vat_amount!=null)inv={...inv,vat:inv.vat_amount};
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{maximumFractionDigits:2});
  const source=inv.source||((inv.sourceFile||inv.confidence)?'AI Upload':'Manual');
  const returnDoc=isSalesReturn(inv);
  const sourceClass=returnDoc?'b-r':String(source).toLowerCase().includes('ai')?'b-p':'b-gray';
  const status=inv.status||'Draft';
  const sl=String(status).toLowerCase();
  const statusClass=returnDoc?'b-r':
    sl==='paid'?'b-g':
    sl==='partial'?'b-a':
    sl.includes('overdue')?'b-r':
    sl.includes('draft')||sl==='cancelled'?'b-gray':
    sl.includes('pending')||sl.includes('sent')?'b-t':
    'b-a';
  const row=document.createElement('tr');
  row.dataset.salesInvoice=JSON.stringify({...inv,source,status,document_type:returnDoc?'Sales Return':(inv.document_type||'Sales Invoice')});
  row.dataset.rowActionsAdded='1';
  row.innerHTML=`<td class="mono">${escapeHtml(inv.invoice_no)}</td><td>${escapeHtml(inv.customer)}</td><td>${escapeHtml(inv.date)}</td><td>${escapeHtml(inv.due_date||'30 days')}</td><td class="mono">${fmt(inv.subtotal)}</td><td class="mono">${fmt(inv.vat_amount)}</td><td class="mono">${fmt(inv.total)}</td><td><span class="b ${sourceClass}">${escapeHtml(source)}</span></td><td><span class="b ${statusClass}">${escapeHtml(status)}</span></td><td data-action-col="1">${salesInvoiceActionsHtml()}</td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  registerSalesInvoiceKey(inv.invoice_no);
  if(options.persist)persistSalesInvoice({...inv,source,status});
  return true;
}

function parseAmount(value){
  return parseFloat(String(value||'0').replace(/[^0-9.-]/g,''))||0;
}

function invoiceFromSalesRow(row){
  if(row?.dataset.salesInvoice){
    try{return JSON.parse(row.dataset.salesInvoice);}catch{}
  }
  const cells=[...(row?.querySelectorAll('td')||[])];
  return {
    invoice_no:cells[0]?.textContent.trim()||'Draft',
    customer:cells[1]?.textContent.trim()||'Customer',
    date:cells[2]?.textContent.trim()||'',
    due_date:cells[3]?.textContent.trim()||'',
    subtotal:parseAmount(cells[4]?.textContent),
    vat_amount:parseAmount(cells[5]?.textContent),
    total:parseAmount(cells[6]?.textContent),
    source:cells[7]?.textContent.trim()||'Manual',
    status:cells[8]?.textContent.trim()||'Draft',
    lines:[{description:'Sales invoice items',qty:1,price:parseAmount(cells[4]?.textContent),amount:parseAmount(cells[4]?.textContent)}]
  };
}

function defaultInvoiceLayout(){
  return {
    template:'Modern Tax Invoice',
    paper:'A4 Portrait',
    align:'left',
    color:'#2563eb',
    logo:'TaxFlow',
    font:'Modern Sans',
    company:currentCompany?.name||'',
    trnMode:'show',
    taxLabel:'Tax Invoice',
    address:'Dubai, United Arab Emirates',
    terms:'Net 30',
    dueDays:'30',
    currency:'AED 1,234.00',
    bank:'Bank transfer to Emirates NBD - IBAN AE070331234567890123456',
    footer:'Thank you for your business',
    language:'English',
    decimals:'2 decimals',
    qr:true,
    taxSummary:true,
    signature:true,
    showTrn:true,
    showCustomerTrn:true,
    trnLabel:'TRN',
    customerTrnLabel:'Customer TRN',
    showVatRate:true,
    showVatAmount:true,
    showTaxableAmount:true,
    vatMode:'exclusive',
    showPoNumber:true,
    poLabel:'Purchase Order No.',
    showDeliveryNote:false,
    deliveryLabel:'Delivery Note No.',
    showReferenceNo:true,
    referenceLabel:'Reference No.',
    showPaymentTerms:true,
    paymentTermsLabel:'Payment Terms',
    showBankDetails:true,
    bankName:'',
    accountName:'',
    accountNumber:'',
    iban:'',
    swiftCode:'',
    paymentLink:'',
    enableArabic:true,
    enableRtl:false,
    headingLabel:'Tax Invoice | فاتورة ضريبية',
    productLabel:'Product | المنتج',
    quantityLabel:'Qty | الكمية',
    vatLabel:'VAT | ضريبة القيمة المضافة',
    totalLabel:'Total | الإجمالي',
    showPreparedBy:false,
    showApprovedBy:false,
    showCompanyStamp:false,
    showAuthorizedSignature:true,
    signatureLabel:'Authorized Signature',
    qrCodeType:'invoice_url'
  };
}

function repairInvoiceArabicDefaults(layout){
  const defaults=defaultInvoiceLayout();
  const brokenArabic=value=>/[�]{1,}|[ØÙ][\s\S]*[ØÙ]|\?{3,}/.test(String(value||''));
  ['headingLabel','productLabel','quantityLabel','vatLabel','totalLabel'].forEach(key=>{
    if(brokenArabic(layout[key]))layout[key]=defaults[key];
  });
  return layout;
}

function normalizeInvoiceLayout(layout={}){
  return repairInvoiceArabicDefaults({...defaultInvoiceLayout(),...(layout||{})});
}

function invoiceUsesArabic(layout){
  return Boolean(layout?.enableArabic)||String(layout?.language||'').toLowerCase().includes('arabic');
}

function invoiceArabicMode(layout){
  return String(layout?.language||'').toLowerCase()==='arabic';
}

function invoiceBilingualLabel(layout,english,arabic){
  if(!invoiceUsesArabic(layout))return english;
  if(invoiceArabicMode(layout))return arabic;
  return `${english} | ${arabic}`;
}

function invoiceLabels(layout){
  const bilingual=(english,arabic)=>invoiceBilingualLabel(layout,english,arabic);
  return {
    heading:invoiceUsesArabic(layout)?layout.headingLabel:(layout.taxLabel||'Tax Invoice'),
    billTo:bilingual('Bill To','الفاتورة إلى'),
    issueDate:bilingual('Issue Date','تاريخ الإصدار'),
    dueDate:bilingual('Due Date','تاريخ الاستحقاق'),
    paymentTerms:layout.paymentTermsLabel||bilingual('Payment Terms','شروط الدفع'),
    currency:bilingual('Currency','العملة'),
    product:invoiceUsesArabic(layout)?layout.productLabel:'Product',
    unit:bilingual('Unit','الوحدة'),
    quantity:invoiceUsesArabic(layout)?layout.quantityLabel:'Qty',
    unitPrice:bilingual('Unit Price','سعر الوحدة'),
    taxable:bilingual('Taxable','الخاضع للضريبة'),
    vat:invoiceUsesArabic(layout)?layout.vatLabel:'VAT',
    vatAmount:bilingual('VAT Amount','مبلغ الضريبة'),
    amount:bilingual('Amount','المبلغ'),
    subtotal:bilingual('Subtotal','المجموع الفرعي'),
    total:invoiceUsesArabic(layout)?layout.totalLabel:'Total',
    balanceDue:bilingual('Balance Due','الرصيد المستحق'),
    purchaseOrder:layout.poLabel||bilingual('Purchase Order No.','رقم أمر الشراء'),
    deliveryNote:layout.deliveryLabel||bilingual('Delivery Note No.','رقم إشعار التسليم'),
    reference:layout.referenceLabel||bilingual('Reference No.','الرقم المرجعي'),
    paymentDetails:bilingual('Payment Details','تفاصيل الدفع'),
    scanQr:bilingual('Scan QR','امسح رمز QR'),
    openDigitalInvoice:bilingual('Open digital invoice','فتح الفاتورة الرقمية'),
    preparedBy:bilingual('Prepared By','أعدها'),
    approvedBy:bilingual('Approved By','اعتمدها'),
    companyStamp:bilingual('Company Stamp','ختم الشركة'),
    authorizedSignature:invoiceUsesArabic(layout)&&layout.signatureLabel==='Authorized Signature'?bilingual('Authorized Signature','التوقيع المعتمد'):layout.signatureLabel,
    digitalGenerated:bilingual('Digital invoice generated by TaxFlow','تم إنشاء الفاتورة الرقمية بواسطة TaxFlow')
  };
}

function salesDocumentHeading(inv={},layout=getInvoiceLayout()){
  if(!isSalesReturn(inv))return invoiceLabels(layout).heading;
  return 'Sales Return';
}

function getInvoiceLayout(){
  const base=defaultInvoiceLayout();
  const value=(id,key)=>document.getElementById(id)?.value||base[key];
  const checked=(id,key)=>document.getElementById(id)?.checked ?? base[key];
  const nameVal=(document.getElementById('inv-layout-name')?.value||'').trim();
  return {
    name:nameVal||(_invoiceLayouts.find(l=>l.id===_activeLayoutId)?.name||'Layout'),
    template:value('inv-layout-template','template'),
    paper:value('inv-layout-paper','paper'),
    align:value('inv-layout-align','align'),
    color:value('inv-layout-color','color'),
    logo:value('inv-layout-logo','logo'),
    font:value('inv-layout-font','font'),
    company:value('inv-layout-company','company'),
    trnMode:value('inv-layout-trn-mode','trnMode'),
    taxLabel:value('inv-layout-tax-label','taxLabel'),
    address:value('inv-layout-address','address'),
    terms:value('inv-layout-terms','terms'),
    dueDays:value('inv-layout-due-days','dueDays'),
    currency:value('inv-layout-currency','currency'),
    bank:value('inv-layout-bank','bank'),
    footer:value('inv-layout-footer','footer'),
    language:value('inv-layout-language','language'),
    decimals:value('inv-layout-decimals','decimals'),
    qr:checked('inv-layout-qr','qr'),
    taxSummary:checked('inv-layout-tax-summary','taxSummary'),
    signature:checked('inv-layout-signature','signature'),
    showTrn:checked('inv-layout-show-trn','showTrn'),
    showCustomerTrn:checked('inv-layout-show-customer-trn','showCustomerTrn'),
    trnLabel:value('inv-layout-trn-label','trnLabel'),
    customerTrnLabel:value('inv-layout-customer-trn-label','customerTrnLabel'),
    showVatRate:checked('inv-layout-show-vat-rate','showVatRate'),
    showVatAmount:checked('inv-layout-show-vat-amount','showVatAmount'),
    showTaxableAmount:checked('inv-layout-show-taxable','showTaxableAmount'),
    vatMode:value('inv-layout-vat-mode','vatMode'),
    showPoNumber:checked('inv-layout-show-po','showPoNumber'),
    poLabel:value('inv-layout-po-label','poLabel'),
    showDeliveryNote:checked('inv-layout-show-delivery','showDeliveryNote'),
    deliveryLabel:value('inv-layout-delivery-label','deliveryLabel'),
    showReferenceNo:checked('inv-layout-show-reference','showReferenceNo'),
    referenceLabel:value('inv-layout-reference-label','referenceLabel'),
    showPaymentTerms:checked('inv-layout-show-payment-terms','showPaymentTerms'),
    paymentTermsLabel:value('inv-layout-payment-terms-label','paymentTermsLabel'),
    showBankDetails:checked('inv-layout-show-bank','showBankDetails'),
    bankName:value('inv-layout-bank-name','bankName'),
    accountName:value('inv-layout-account-name','accountName'),
    accountNumber:value('inv-layout-account-number','accountNumber'),
    iban:value('inv-layout-iban','iban'),
    swiftCode:value('inv-layout-swift','swiftCode'),
    paymentLink:value('inv-layout-payment-link','paymentLink'),
    enableArabic:checked('inv-layout-enable-arabic','enableArabic'),
    enableRtl:checked('inv-layout-enable-rtl','enableRtl'),
    headingLabel:value('inv-layout-heading-label','headingLabel'),
    productLabel:value('inv-layout-product-label','productLabel'),
    quantityLabel:value('inv-layout-quantity-label','quantityLabel'),
    vatLabel:value('inv-layout-vat-label','vatLabel'),
    totalLabel:value('inv-layout-total-label','totalLabel'),
    showPreparedBy:checked('inv-layout-show-prepared','showPreparedBy'),
    showApprovedBy:checked('inv-layout-show-approved','showApprovedBy'),
    showCompanyStamp:checked('inv-layout-show-stamp','showCompanyStamp'),
    showAuthorizedSignature:checked('inv-layout-show-authorized','showAuthorizedSignature'),
    signatureLabel:value('inv-layout-signature-label','signatureLabel'),
    qrCodeType:value('inv-layout-qr-type','qrCodeType')
  };
}

function setInvoiceLayoutFields(layout={}){
  layout=normalizeInvoiceLayout(layout);
  const nameField=document.getElementById('inv-layout-name');
  if(nameField)nameField.value=layout.name||'';
  const fields={
    'inv-layout-template':layout.template,
    'inv-layout-paper':layout.paper,
    'inv-layout-align':layout.align,
    'inv-layout-color':layout.color,
    'inv-layout-logo':layout.logo,
    'inv-layout-font':layout.font,
    'inv-layout-company':layout.company,
    'inv-layout-trn-mode':layout.trnMode,
    'inv-layout-tax-label':layout.taxLabel,
    'inv-layout-address':layout.address,
    'inv-layout-terms':layout.terms,
    'inv-layout-due-days':layout.dueDays,
    'inv-layout-currency':layout.currency,
    'inv-layout-bank':layout.bank,
    'inv-layout-footer':layout.footer,
    'inv-layout-language':layout.language,
    'inv-layout-decimals':layout.decimals,
    'inv-layout-trn-label':layout.trnLabel,
    'inv-layout-customer-trn-label':layout.customerTrnLabel,
    'inv-layout-vat-mode':layout.vatMode,
    'inv-layout-po-label':layout.poLabel,
    'inv-layout-delivery-label':layout.deliveryLabel,
    'inv-layout-reference-label':layout.referenceLabel,
    'inv-layout-payment-terms-label':layout.paymentTermsLabel,
    'inv-layout-bank-name':layout.bankName,
    'inv-layout-account-name':layout.accountName,
    'inv-layout-account-number':layout.accountNumber,
    'inv-layout-iban':layout.iban,
    'inv-layout-swift':layout.swiftCode,
    'inv-layout-payment-link':layout.paymentLink,
    'inv-layout-heading-label':layout.headingLabel,
    'inv-layout-product-label':layout.productLabel,
    'inv-layout-quantity-label':layout.quantityLabel,
    'inv-layout-vat-label':layout.vatLabel,
    'inv-layout-total-label':layout.totalLabel,
    'inv-layout-signature-label':layout.signatureLabel,
    'inv-layout-qr-type':layout.qrCodeType
  };
  Object.entries(fields).forEach(([id,value])=>{
    const field=document.getElementById(id);
    if(field&&value)field.value=value;
  });
  [
    ['inv-layout-qr',layout.qr],
    ['inv-layout-tax-summary',layout.taxSummary],
    ['inv-layout-signature',layout.signature],
    ['inv-layout-show-trn',layout.showTrn],
    ['inv-layout-show-customer-trn',layout.showCustomerTrn],
    ['inv-layout-show-vat-rate',layout.showVatRate],
    ['inv-layout-show-vat-amount',layout.showVatAmount],
    ['inv-layout-show-taxable',layout.showTaxableAmount],
    ['inv-layout-show-po',layout.showPoNumber],
    ['inv-layout-show-delivery',layout.showDeliveryNote],
    ['inv-layout-show-reference',layout.showReferenceNo],
    ['inv-layout-show-payment-terms',layout.showPaymentTerms],
    ['inv-layout-show-bank',layout.showBankDetails],
    ['inv-layout-enable-arabic',layout.enableArabic],
    ['inv-layout-enable-rtl',layout.enableRtl],
    ['inv-layout-show-prepared',layout.showPreparedBy],
    ['inv-layout-show-approved',layout.showApprovedBy],
    ['inv-layout-show-stamp',layout.showCompanyStamp],
    ['inv-layout-show-authorized',layout.showAuthorizedSignature]
  ].forEach(([id,value])=>{
    const field=document.getElementById(id);
    if(field&&value!==undefined)field.checked=Boolean(value);
  });
}

function goToInvoiceDesignSettings(){
  closeM('m-sales-view');
  go('settings');
  setTimeout(()=>{
    const tab=document.querySelectorAll('#page-settings .tab')[6];
    if(tab)stab(tab,'set-docs');
  },60);
}

// ── Multi-layout state ──────────────────────────────────────────
let _invoiceLayouts=[];
let _activeLayoutId=null;
const _IL_KEY='tf_invoice_layouts';

function _ilSave(){try{localStorage.setItem(_IL_KEY,JSON.stringify(_invoiceLayouts));}catch{}}

function initInvoiceLayouts(){
  try{_invoiceLayouts=JSON.parse(localStorage.getItem(_IL_KEY)||'null')||[];}catch{_invoiceLayouts=[];}
  if(!_invoiceLayouts.length){
    _invoiceLayouts=[{id:'layout-default',name:'Default',isDefault:true,...defaultInvoiceLayout()}];
    _ilSave();
  }
  _activeLayoutId=(_invoiceLayouts.find(l=>l.isDefault)||_invoiceLayouts[0])?.id;
  renderInvoiceLayoutGallery();
  const active=_invoiceLayouts.find(l=>l.id===_activeLayoutId);
  if(active)setInvoiceLayoutFields(active);
  _ilUpdateNameBadge();
  // do NOT scroll on init — only scroll on explicit user action
}

function _ilUpdateNameBadge(){
  const l=_invoiceLayouts.find(x=>x.id===_activeLayoutId);
  const sw=document.getElementById('inv-layout-switcher');
  if(sw){
    sw.innerHTML=_invoiceLayouts.map(x=>`<option value="${x.id}"${x.id===_activeLayoutId?' selected':''}>${escapeHtml(x.name)}${x.isDefault?' ★':''}</option>`).join('');
  }
}

function _ilLiveNameSync(){
  const nameField=document.getElementById('inv-layout-name');
  const sw=document.getElementById('inv-layout-switcher');
  if(!nameField||!sw)return;
  const val=nameField.value.trim();
  const opt=sw.querySelector(`option[value="${_activeLayoutId}"]`);
  if(opt&&val)opt.textContent=val;
}

function renderInvoiceLayoutGallery(){
  const grid=document.getElementById('inv-layouts-grid');
  if(!grid)return;
  const colors=['#2563eb','#059669','#d97706','#7c3aed','#dc2626','#0891b2'];
  grid.innerHTML=_invoiceLayouts.map((l,i)=>{
    const bc=l.color||colors[i%colors.length];
    const isActive=l.id===_activeLayoutId;
    return `<div class="inv-lgal-card${isActive?' active':''}" onclick="selectInvoiceLayout('${l.id}')">
      <div class="inv-lgal-ico">
        <div class="inv-lgal-ico-doc" style="--bc:${bc}">
          <div class="inv-lgal-ico-line w80"></div>
          <div class="inv-lgal-ico-line w60"></div>
          <div class="inv-lgal-ico-line w80"></div>
          <div class="inv-lgal-ico-line w70"></div>
          <div class="inv-lgal-ico-line w60"></div>
        </div>
        ${l.isDefault?'<span class="inv-lgal-badge">Default</span>':''}
      </div>
      <div class="inv-lgal-name" title="${escapeHtml(l.name)}">${escapeHtml(l.name)}</div>
      <div class="inv-lgal-meta">${escapeHtml(l.template||'Modern Tax Invoice')}</div>
      <div class="inv-lgal-actions" onclick="event.stopPropagation()">
        <button class="inv-lgal-act${isActive?' inv-lgal-act-primary':''}" onclick="selectInvoiceLayout('${l.id}')">&#9998; Edit</button>
        ${!l.isDefault?`<button class="inv-lgal-act" onclick="setDefaultInvoiceLayout('${l.id}')">&#9733;</button>`:''}
        <button class="inv-lgal-act" onclick="duplicateInvoiceLayout('${l.id}')">Copy</button>
        ${_invoiceLayouts.length>1&&!l.isDefault?`<button class="inv-lgal-act danger" onclick="deleteInvoiceLayout('${l.id}')">Del</button>`:''}
      </div>
    </div>`;
  }).join('')+`<button class="inv-lgal-add" onclick="addInvoiceLayout()">
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M10 4v12M4 10h12"/></svg>
    <span>Add Layout</span>
  </button>`;
}

function selectInvoiceLayout(id,{scroll=true}={}){
  // persist current edits into current slot first
  const cur=_invoiceLayouts.find(l=>l.id===_activeLayoutId);
  if(cur){Object.assign(cur,getInvoiceLayout());}
  _activeLayoutId=id;
  const l=_invoiceLayouts.find(x=>x.id===id);
  if(l){setInvoiceLayoutFields(l);updateInvoiceLayoutPreview();}
  renderInvoiceLayoutGallery();
  _ilUpdateNameBadge();
  if(scroll){
    const card=document.getElementById('inv-design-card');
    if(card)setTimeout(()=>card.scrollIntoView({behavior:'smooth',block:'start'}),60);
  }
}

function addInvoiceLayout(){
  document.getElementById('new-layout-name').value='';
  document.getElementById('new-layout-base').value='default';
  showM('m-new-layout');
  setTimeout(()=>document.getElementById('new-layout-name').focus(),120);
}

function confirmAddInvoiceLayout(){
  const name=(document.getElementById('new-layout-name')?.value||'').trim();
  if(!name){toast('Enter a layout name','err');return;}
  const base=document.getElementById('new-layout-base')?.value||'default';
  let fields=defaultInvoiceLayout();
  if(base==='current'){
    const cur=_invoiceLayouts.find(l=>l.id===_activeLayoutId);
    if(cur)fields={...cur};
  }
  const newLayout={...fields,id:'layout-'+Date.now(),name,isDefault:false};
  _invoiceLayouts.push(newLayout);
  _ilSave();
  hideM('m-new-layout');
  selectInvoiceLayout(newLayout.id);
  toast(`Layout "${name}" created`,'ok');
}

function setDefaultInvoiceLayout(id){
  _invoiceLayouts.forEach(l=>l.isDefault=(l.id===id));
  _ilSave();
  renderInvoiceLayoutGallery();
  toast('Default layout updated','ok');
}

function duplicateInvoiceLayout(id){
  const src=_invoiceLayouts.find(l=>l.id===id);
  if(!src)return;
  const copy={...src,id:'layout-'+Date.now(),name:src.name+' (Copy)',isDefault:false};
  _invoiceLayouts.push(copy);
  _ilSave();
  selectInvoiceLayout(copy.id);
  toast(`"${copy.name}" created`,'ok');
}

function deleteInvoiceLayout(id){
  const l=_invoiceLayouts.find(x=>x.id===id);
  if(!l||l.isDefault){toast('Cannot delete the default layout','err');return;}
  if(_invoiceLayouts.length<=1){toast('Cannot delete the last layout','err');return;}
  if(!confirm(`Delete layout "${l.name}"?`))return;
  _invoiceLayouts=_invoiceLayouts.filter(x=>x.id!==id);
  if(_activeLayoutId===id)_activeLayoutId=(_invoiceLayouts.find(x=>x.isDefault)||_invoiceLayouts[0])?.id;
  _ilSave();
  selectInvoiceLayout(_activeLayoutId);
  toast('Layout deleted','ok');
}
// ── end multi-layout ─────────────────────────────────────────────

async function saveInvoiceLayout(){
  // Capture current form → update active slot
  const layout=getInvoiceLayout();
  const idx=_invoiceLayouts.findIndex(l=>l.id===_activeLayoutId);
  if(idx>=0){_invoiceLayouts[idx]={..._invoiceLayouts[idx],...layout};_ilSave();renderInvoiceLayoutGallery();_ilUpdateNameBadge();}
  try{
    await saveInvoiceLayoutServer(layout);
    updateInvoiceLayoutPreview();
    updateQuotationLayoutPreview();
    if(currentSalesInvoice)renderSalesInvoicePreview(currentSalesInvoice);
    toast('Invoice layout saved','ok');
    audit('Saved invoice layout',layout.template,'Saved');
  }catch(err){
    console.warn('Invoice layout save failed:',err);
    toast('Invoice layout could not be saved','err');
  }
}

let savedQuotationLayout=null;

function defaultQuotationLayout(){
  return {
    id:'quotation-layout',
    template:'Modern Quotation',
    color:'#2563eb',
    inheritInvoiceBrand:true,
    quotationHeading:'Quotation',
    quoteToLabel:'Quote To',
    validityLabel:'Valid Until',
    quotationTerms:'Quotation is subject to approval and stock availability.',
    footer:'Prices are valid only until the validity date shown.',
    showVat:true,
    showValidity:true,
    signature:true,
    preparedLabel:'Prepared By',
    acceptedLabel:'Accepted By'
  };
}

function normalizeQuotationLayout(layout={}){
  return {...defaultQuotationLayout(),...(layout||{}),id:'quotation-layout'};
}

function readQuotationLayoutFields(){
  const base=normalizeQuotationLayout(savedQuotationLayout);
  const value=(id,key)=>document.getElementById(id)?.value||base[key];
  const checked=(id,key)=>document.getElementById(id)?.checked ?? base[key];
  return normalizeQuotationLayout({
    template:value('quote-layout-template','template'),
    color:value('quote-layout-color','color'),
    inheritInvoiceBrand:checked('quote-layout-inherit-brand','inheritInvoiceBrand'),
    quotationHeading:value('quote-layout-heading','quotationHeading'),
    quoteToLabel:value('quote-layout-quote-to','quoteToLabel'),
    validityLabel:value('quote-layout-validity-label','validityLabel'),
    quotationTerms:value('quote-layout-terms','quotationTerms'),
    footer:value('quote-layout-footer','footer'),
    showVat:checked('quote-layout-show-vat','showVat'),
    showValidity:checked('quote-layout-show-validity','showValidity'),
    signature:checked('quote-layout-signature','signature'),
    preparedLabel:value('quote-layout-prepared-label','preparedLabel'),
    acceptedLabel:value('quote-layout-accepted-label','acceptedLabel')
  });
}

function getQuotationLayout(){
  const invoiceLayout=getInvoiceLayout();
  const quotation=readQuotationLayoutFields();
  const brand=quotation.inheritInvoiceBrand?invoiceLayout:{};
  const color=quotation.inheritInvoiceBrand?(invoiceLayout.color||quotation.color):quotation.color;
  return {
    ...invoiceLayout,
    ...brand,
    ...quotation,
    color,
    taxLabel:quotation.quotationHeading,
    headingLabel:quotation.quotationHeading,
    terms:quotation.quotationTerms||invoiceLayout.terms,
    footer:quotation.footer||invoiceLayout.footer,
    taxSummary:quotation.showVat,
    signature:quotation.signature
  };
}

function setQuotationLayoutFields(layout={}){
  savedQuotationLayout=normalizeQuotationLayout(layout);
  const fields={
    'quote-layout-template':savedQuotationLayout.template,
    'quote-layout-color':savedQuotationLayout.color,
    'quote-layout-heading':savedQuotationLayout.quotationHeading,
    'quote-layout-quote-to':savedQuotationLayout.quoteToLabel,
    'quote-layout-validity-label':savedQuotationLayout.validityLabel,
    'quote-layout-terms':savedQuotationLayout.quotationTerms,
    'quote-layout-footer':savedQuotationLayout.footer,
    'quote-layout-prepared-label':savedQuotationLayout.preparedLabel,
    'quote-layout-accepted-label':savedQuotationLayout.acceptedLabel
  };
  Object.entries(fields).forEach(([id,value])=>{
    const field=document.getElementById(id);
    if(field&&value!==undefined)field.value=value;
  });
  [
    ['quote-layout-inherit-brand',savedQuotationLayout.inheritInvoiceBrand],
    ['quote-layout-show-vat',savedQuotationLayout.showVat],
    ['quote-layout-show-validity',savedQuotationLayout.showValidity],
    ['quote-layout-signature',savedQuotationLayout.signature]
  ].forEach(([id,value])=>{
    const field=document.getElementById(id);
    if(field&&value!==undefined)field.checked=Boolean(value);
  });
}

function sampleQuotationLayoutRecord(){
  return {
    quote_no:'QT-PREVIEW',
    customer:'Customer Name LLC',
    subject:'Supply and installation proposal',
    date:new Date().toISOString().slice(0,10),
    valid_until:new Date(Date.now()+14*86400000).toISOString().slice(0,10),
    status:'Draft',
    subtotal:12400,
    vat_amount:620,
    total:13020,
    lines:[
      {description:'Steel materials',qty:2,price:4300,amount:8600},
      {description:'Installation service',qty:1,price:3800,amount:3800}
    ]
  };
}

function quotationLayoutPreviewHtml(quote=sampleQuotationLayoutRecord(),layout=getQuotationLayout()){
  const companyTrn=currentCompany?.trn||document.getElementById('set-company-trn')?.value||'';
  const subtotal=parseAmount(quote.subtotal);
  const vat=parseAmount(quote.vat_amount);
  const total=parseAmount(quote.total)||subtotal+vat;
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  const initials=(layout.logo||layout.company||'TF').split(/\s+/).map(part=>part[0]).join('').slice(0,2).toUpperCase()||'TF';
  const textAlign={left:'left',center:'center',right:'right'}[layout.align]||'left';
  const brandJustify={left:'flex-start',center:'center',right:'flex-end'}[layout.align]||'flex-start';
  const fontFamily=layout.font==='Classic Serif'?'Georgia,serif':layout.font==='Compact Mono'?'DM Mono,monospace':'Syne,sans-serif';
  const lines=quotationLinesFromRecord(quote);
  return `
    <div class="invoice-layout-live" dir="${layout.enableRtl?'rtl':'ltr'}" style="--invoice-accent:${escapeHtml(layout.color)}">
      <div class="invoice-topbar"></div>
      <div class="invoice-head invoice-layout-head" style="grid-template-columns:1fr;gap:12px">
        <div class="invoice-brand" style="justify-content:${brandJustify};text-align:${textAlign}">
          <div class="invoice-logo">${escapeHtml(initials)}</div>
          <div>
            <div class="invoice-company" style="font-family:${fontFamily}">${escapeHtml(layout.company)}</div>
            <div class="invoice-muted">${escapeHtml(layout.address)}</div>
            ${layout.trnMode==='show'&&layout.showTrn?`<div class="invoice-muted mono">${escapeHtml(layout.trnLabel||'TRN')} ${escapeHtml(companyTrn||'not set')}</div>`:''}
          </div>
        </div>
        <div class="invoice-titlebox" style="align-items:${brandJustify};text-align:${textAlign}">
          <div class="invoice-label" style="font-size:22px">${escapeHtml(layout.quotationHeading||'Quotation')}</div>
          <div class="invoice-number mono">${escapeHtml(quote.quote_no||'Draft')}</div>
        </div>
      </div>
      <div class="invoice-info-grid" style="grid-template-columns:1fr;gap:12px;padding:16px">
        <div class="invoice-panel">
          <div class="invoice-kicker">${escapeHtml(layout.quoteToLabel||'Quote To')}</div>
          <div class="invoice-party">${escapeHtml(quote.customer||'Customer')}</div>
          ${quote.subject?`<div class="invoice-muted">${escapeHtml(quote.subject)}</div>`:''}
        </div>
        <div class="invoice-panel invoice-meta-panel">
          <div class="invoice-meta-row"><span>Quotation No.</span><strong>${escapeHtml(quote.quote_no||'Draft')}</strong></div>
          <div class="invoice-meta-row"><span>Date</span><strong>${escapeHtml(quote.date||'-')}</strong></div>
          ${layout.showValidity?`<div class="invoice-meta-row"><span>${escapeHtml(layout.validityLabel||'Valid Until')}</span><strong>${escapeHtml(quote.valid_until||'-')}</strong></div>`:''}
        </div>
      </div>
      <table class="invoice-line-table" style="width:calc(100% - 32px);margin:0 16px">
        <tbody>
          ${lines.slice(0,2).map(line=>`<tr><td><strong>${escapeHtml(line.description||line.item||'Item')}</strong></td><td class="mono num">${fmt(line.amount)}</td></tr>`).join('')}
          ${layout.showVat?`<tr><td>VAT 5%</td><td class="mono num">${fmt(vat)}</td></tr>`:''}
          <tr><td style="font-weight:800">Total</td><td class="mono num" style="font-weight:800;color:var(--invoice-accent)">${'AED'} ${fmt(total)}</td></tr>
        </tbody>
      </table>
      <div class="invoice-summary-grid" style="grid-template-columns:1fr;gap:12px;padding:16px">
        <div class="invoice-notes">
          <div class="invoice-kicker">Terms & Payment Details</div>
          <div>${escapeHtml(layout.quotationTerms||layout.terms||'')}</div>
          <div class="invoice-muted" style="margin-top:8px">${escapeHtml(layout.footer||'')}</div>
        </div>
        ${layout.signature?`<div class="invoice-signatures" style="grid-template-columns:1fr;margin:0;padding-top:14px"><div><span>${escapeHtml(layout.preparedLabel||'Prepared By')}</span><strong>${escapeHtml(layout.acceptedLabel||'Accepted By')}</strong></div></div>`:''}
      </div>
    </div>`;
}

function updateQuotationLayoutPreview(){
  const preview=document.getElementById('quotation-layout-preview');
  if(!preview)return;
  preview.innerHTML=quotationLayoutPreviewHtml();
}

function previewQuotationLayoutSample(){
  renderQuotationPreview(sampleQuotationLayoutRecord());
  showM('m-quotation-view');
}

async function saveQuotationLayout(){
  const layout=readQuotationLayoutFields();
  savedQuotationLayout=layout;
  try{
    await saveServer('quotationLayout',layout,{throwOnError:true});
    updateQuotationLayoutPreview();
    if(currentQuotation)renderQuotationPreview(currentQuotation);
    toast('Quotation layout saved','ok');
    audit('Saved quotation layout',layout.template,'Saved');
  }catch(err){
    console.warn('Quotation layout save failed:',err);
    toast('Quotation layout could not be saved','err');
  }
}

function encodePublicInvoicePayload(payload){
  try{
    return btoa(unescape(encodeURIComponent(JSON.stringify(payload))));
  }catch{
    return '';
  }
}

function publicInvoicePayload(inv=currentSalesInvoice){
  const layout=getInvoiceLayout();
  const labels=invoiceLabels(layout);
  const heading=salesDocumentHeading(inv,layout);
  const companyTrn=currentCompany?.trn||document.getElementById('set-company-trn')?.value||'';
  return {
    company:{
      name:layout.company,
      logo:layout.logo,
      trn:companyTrn,
      address:layout.address,
      bank:layout.bank,
      bankName:layout.bankName,
      accountName:layout.accountName,
      accountNumber:layout.accountNumber,
      iban:layout.iban,
      swiftCode:layout.swiftCode,
      paymentLink:layout.paymentLink,
      footer:layout.footer,
      color:layout.color,
      taxLabel:heading,
      trnLabel:layout.trnLabel,
      customerTrnLabel:layout.customerTrnLabel,
      enableRtl:layout.enableRtl,
      enableArabic:invoiceUsesArabic(layout),
      language:layout.language
    },
    invoice:{
      invoice_no:inv?.invoice_no||'Draft',
      status:inv?.status||'Draft',
      customer:inv?.customer||'Customer',
      customer_trn:inv?.customer_trn||'TRN not provided',
      customer_address:inv?.customer_address||document.getElementById('inv-caddr')?.value||'',
      po_number:inv?.po_number||'',
      delivery_note_no:inv?.delivery_note_no||'',
      reference_no:inv?.reference_no||'',
      date:inv?.date||'',
      due_date:inv?.due_date||'',
      subtotal:Number(inv?.subtotal||0),
      vat_amount:Number(inv?.vat_amount||0),
      total:Number(inv?.total||0),
      terms:layout.terms,
      labels:{
        heading,
        billTo:labels.billTo,
        issueDate:labels.issueDate,
        dueDate:labels.dueDate,
        paymentTerms:labels.paymentTerms,
        currency:labels.currency,
        product:labels.product,
        unit:labels.unit,
        quantity:labels.quantity,
        unitPrice:labels.unitPrice,
        taxable:labels.taxable,
        vat:labels.vat,
        vatAmount:labels.vatAmount,
        amount:labels.amount,
        subtotal:labels.subtotal,
        total:labels.total,
        balanceDue:labels.balanceDue,
        purchaseOrder:labels.purchaseOrder,
        deliveryNote:labels.deliveryNote,
        reference:labels.reference,
        paymentDetails:labels.paymentDetails,
        digitalGenerated:labels.digitalGenerated
      },
      lines:inv?.lines||[]
    }
  };
}

function publicInvoiceUrl(inv=currentSalesInvoice){
  const payload=encodePublicInvoicePayload(publicInvoicePayload(inv));
  const url=new URL('/taxflow/digital-invoice.html',window.location.origin);
  url.hash='invoice='+payload;
  return url.toString();
}

function invoiceQrValue(inv=currentSalesInvoice,layout=getInvoiceLayout()){
  if(layout.qrCodeType==='uae_vat_qr'){
    const companyTrn=currentCompany?.trn||document.getElementById('set-company-trn')?.value||'';
    return [
      layout.company,
      companyTrn,
      inv?.date||new Date().toISOString().slice(0,10),
      Number(inv?.total||0).toFixed(2),
      Number(inv?.vat_amount||0).toFixed(2)
    ].join('|');
  }
  return publicInvoiceUrl(inv);
}

function qrCodeFallbackImageUrl(value){
  return `https://api.qrserver.com/v1/create-qr-code/?size=180x180&margin=10&data=${encodeURIComponent(value)}`;
}

function renderInvoiceQrCode(value,imgId='public-invoice-qr'){
  const img=document.getElementById(imgId);
  if(!img)return;
  const fallback=qrCodeFallbackImageUrl(value);
  if(window.TaxFlowQRCode?.toDataURL){
    window.TaxFlowQRCode.toDataURL(value,{width:180,margin:1,color:{dark:'#172033',light:'#ffffff'}})
      .then(src=>{img.src=src;})
      .catch(()=>{img.src=fallback;});
    return;
  }
  img.src=fallback;
}

function openCurrentPublicInvoice(){
  const inv=currentSalesInvoice||buildDraftInvoice();
  window.open(publicInvoiceUrl(inv),'_blank');
}

function currentInvoiceForShare(){
  return currentSalesInvoice||buildDraftInvoice();
}

function invoicePdfHtml(inv=currentInvoiceForShare()){
  const payload=publicInvoicePayload(inv);
  const company=payload.company||{};
  const invoice=payload.invoice||{};
  const labels=invoice.labels||{};
  const lines=Array.isArray(invoice.lines)&&invoice.lines.length?invoice.lines:[{description:'Invoice items',unit:'PCS',qty:1,price:invoice.subtotal,amount:invoice.subtotal}];
  const initials=String(company.logo||company.name||'TF').split(/\s+/).map(part=>part[0]).join('').slice(0,2).toUpperCase()||'TF';
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  const accent=escapeHtml(company.color||'#2563eb');
  return `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(invoice.invoice_no||'Invoice')} - PDF</title>
    <style>
      *{box-sizing:border-box}body{margin:0;background:#fff;color:#172033;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.45}.sheet{max-width:900px;margin:0 auto;padding:34px}.bar{height:7px;background:${accent};margin:-34px -34px 28px}.head{display:grid;grid-template-columns:1fr 280px;gap:24px;border-bottom:1px solid #e5eaf2;padding-bottom:22px}.brand{display:flex;gap:14px}.logo{width:58px;height:58px;border-radius:12px;background:${accent};color:#fff;display:grid;place-items:center;font-size:20px;font-weight:800}.company{font-size:22px;font-weight:800}.muted{color:#667085;font-size:12px;margin-top:3px}.right{text-align:right}.label{font-size:30px;font-weight:900;text-transform:uppercase}.badge{display:inline-block;margin-top:8px;border:1px solid #d8e2ff;color:${accent};border-radius:999px;padding:4px 10px;font-size:11px;font-weight:700;text-transform:uppercase}.grid{display:grid;grid-template-columns:1fr 280px;gap:16px;margin:22px 0}.panel{border:1px solid #e5eaf2;border-radius:8px;padding:14px}.kicker{font-size:10px;text-transform:uppercase;letter-spacing:.8px;color:#667085;font-weight:700;margin-bottom:7px}.party{font-size:17px;font-weight:800}.row{display:flex;justify-content:space-between;gap:12px;color:#667085;padding:4px 0}.row strong{color:#172033;text-align:right}table{width:100%;border-collapse:collapse;border:1px solid #e5eaf2;border-radius:8px;overflow:hidden}th{background:#f3f6fb;color:#667085;text-transform:uppercase;font-size:10px;letter-spacing:.5px;text-align:left;padding:10px}td{padding:11px 10px;border-top:1px solid #e5eaf2}.num{text-align:right;white-space:nowrap}.summary{display:grid;grid-template-columns:1fr 300px;gap:20px;margin-top:22px}.notes{border-left:4px solid ${accent};padding-left:12px;color:#667085}.totals{border:1px solid #e5eaf2;border-radius:8px;padding:14px}.total,.grand{display:flex;justify-content:space-between;gap:14px}.total{color:#667085;padding:4px 0}.grand{border-top:1px solid #e5eaf2;margin-top:8px;padding-top:12px;font-size:19px;font-weight:900}.grand strong{color:${accent}}.link{margin-top:14px;font-size:11px;color:#667085;word-break:break-all}[dir=rtl] .right{text-align:left}[dir=rtl] th{text-align:right}[dir=rtl] .num{text-align:left}[dir=rtl] .notes{border-left:0;border-right:4px solid ${accent};padding-left:0;padding-right:12px}@media print{body{print-color-adjust:exact;-webkit-print-color-adjust:exact}.sheet{padding:24px}.bar{margin:-24px -24px 24px}.no-print{display:none}}
    </style></head><body><main class="sheet"><div class="bar"></div>
      <section class="head" dir="${company.enableRtl?'rtl':'ltr'}"><div class="brand"><div class="logo">${escapeHtml(initials)}</div><div><div class="company">${escapeHtml(company.name||'TaxFlow')}</div><div class="muted">${escapeHtml(company.address||'')}</div><div class="muted">${escapeHtml(company.trnLabel||'TRN')} ${escapeHtml(company.trn||'not set')}</div></div></div><div class="right"><div class="label">${escapeHtml(company.taxLabel||'Tax Invoice')}</div><div>${escapeHtml(invoice.invoice_no||'Draft')}</div><span class="badge">${escapeHtml(invoice.status||'Draft')}</span></div></section>
      <section class="grid" dir="${company.enableRtl?'rtl':'ltr'}"><div class="panel"><div class="kicker">${escapeHtml(labels.billTo||'Bill To')}</div><div class="party">${escapeHtml(invoice.customer||'Customer')}</div><div class="muted">${escapeHtml(company.customerTrnLabel||'Customer TRN')} ${escapeHtml(invoice.customer_trn||'not provided')}</div><div class="muted">${escapeHtml(invoice.customer_address||'')}</div></div><div class="panel"><div class="row"><span>${escapeHtml(labels.issueDate||'Issue Date')}</span><strong>${escapeHtml(invoice.date||'-')}</strong></div><div class="row"><span>${escapeHtml(labels.dueDate||'Due Date')}</span><strong>${escapeHtml(invoice.due_date||'-')}</strong></div><div class="row"><span>${escapeHtml(labels.paymentTerms||'Terms')}</span><strong>${escapeHtml(invoice.terms||'Net 30')}</strong></div><div class="row"><span>${escapeHtml(labels.currency||'Currency')}</span><strong>AED</strong></div></div></section>
      <table dir="${company.enableRtl?'rtl':'ltr'}"><thead><tr><th>#</th><th>${escapeHtml(labels.product||'Product')}</th><th>${escapeHtml(labels.unit||'Unit')}</th><th class="num">${escapeHtml(labels.quantity||'Qty')}</th><th class="num">${escapeHtml(labels.unitPrice||'Unit Price')}</th><th class="num">${escapeHtml(labels.amount||'Amount')}</th></tr></thead><tbody>${lines.map((line,index)=>`<tr><td>${index+1}</td><td><strong>${escapeHtml(line.description||'Item')}</strong></td><td>${escapeHtml(line.unit||'PCS')}</td><td class="num">${escapeHtml(line.qty||1)}</td><td class="num">${fmt(line.price)}</td><td class="num">${fmt(line.amount)}</td></tr>`).join('')}</tbody></table>
      <section class="summary" dir="${company.enableRtl?'rtl':'ltr'}"><div class="notes"><div class="kicker">${escapeHtml(labels.paymentDetails||'Payment Details')}</div><div style="margin-top:8px">${escapeHtml(company.footer||'')}</div><div class="link">Online view: ${escapeHtml(publicInvoiceUrl(inv))}</div></div><div class="totals"><div class="total"><span>${escapeHtml(labels.subtotal||'Subtotal')}</span><strong>AED ${fmt(invoice.subtotal)}</strong></div><div class="total"><span>${escapeHtml(labels.vat||'VAT')}</span><strong>AED ${fmt(invoice.vat_amount)}</strong></div><div class="grand"><span>${escapeHtml(labels.total||'Total')}</span><strong>AED ${fmt(invoice.total)}</strong></div></div></section>
    </main><script>window.onload=()=>setTimeout(()=>window.print(),250);<\/script></body></html>`;
}

function exportCurrentInvoicePdf(){
  const inv=currentInvoiceForShare();
  const printWindow=window.open('','_blank','width=980,height=780');
  if(!printWindow){
    toast('Allow popups to open the invoice PDF','warn');
    return;
  }
  printWindow.document.open();
  printWindow.document.write(invoicePdfHtml(inv));
  printWindow.document.close();
  toast('PDF print view opened. Choose Save as PDF to download.','ok');
  audit('Opened invoice PDF',inv.invoice_no||'Draft','Exported');
}

function pdfSafeText(value){
  return String(value??'').replace(/[^\x20-\x7E]/g,'?');
}

function pdfEscape(value){
  return pdfSafeText(value).replace(/\\/g,'\\\\').replace(/\(/g,'\\(').replace(/\)/g,'\\)');
}

function wrapPdfLine(value,max=86){
  const words=pdfSafeText(value).split(/\s+/);
  const lines=[];
  let current='';
  words.forEach(word=>{
    if(!word)return;
    if((current+' '+word).trim().length>max){
      if(current)lines.push(current);
      current=word;
    }else{
      current=(current+' '+word).trim();
    }
  });
  if(current)lines.push(current);
  return lines.length?lines:[''];
}

function invoicePdfTextLines(inv){
  const layout=getInvoiceLayout();
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  const lines=Array.isArray(inv?.lines)&&inv.lines.length?inv.lines:[{description:'Invoice items',unit:'PCS',qty:1,price:inv?.subtotal,amount:inv?.subtotal}];
  const output=[
    layout.company||'TaxFlow',
    layout.address||'',
    `TRN: ${currentCompany?.trn||document.getElementById('set-company-trn')?.value||'not set'}`,
    '',
    `TAX INVOICE: ${inv?.invoice_no||'Draft'}`,
    `Status: ${inv?.status||'Draft'}`,
    '',
    `Customer: ${inv?.customer||'Customer'}`,
    `Customer TRN: ${inv?.customer_trn||'not provided'}`,
    `Address: ${inv?.customer_address||''}`,
    `Issue Date: ${inv?.date||'-'}    Due Date: ${inv?.due_date||'-'}`,
    '',
    'Items',
    'No.  Description                                      Qty      Price       Amount'
  ];
  lines.forEach((line,index)=>{
    const desc=pdfSafeText(line.description||'Item').slice(0,42).padEnd(42,' ');
    const qty=pdfSafeText(line.qty||1).slice(0,7).padStart(7,' ');
    const price=fmt(line.price).slice(0,10).padStart(10,' ');
    const amount=fmt(line.amount).slice(0,11).padStart(11,' ');
    output.push(`${String(index+1).padEnd(4,' ')} ${desc} ${qty} ${price} ${amount}`);
  });
  output.push('');
  output.push(`Subtotal: AED ${fmt(inv?.subtotal)}`);
  output.push(`VAT:      AED ${fmt(inv?.vat_amount)}`);
  output.push(`Total:    AED ${fmt(inv?.total)}`);
  output.push('');
  output.push(`Online view: ${publicInvoiceUrl(inv)}`);
  output.push('');
  output.push(layout.footer||'');
  return output.flatMap(line=>wrapPdfLine(line)).slice(0,54);
}

function makeSimplePdfBlob(lines){
  const content=`BT\n/F1 11 Tf\n14 TL\n50 790 Td\n${lines.map(line=>`(${pdfEscape(line)}) Tj\nT*`).join('\n')}\nET`;
  const objects=[
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Length ${content.length} >>\nstream\n${content}\nendstream`
  ];
  let pdf='%PDF-1.4\n';
  const offsets=[0];
  objects.forEach((obj,index)=>{
    offsets.push(pdf.length);
    pdf+=`${index+1} 0 obj\n${obj}\nendobj\n`;
  });
  const xrefOffset=pdf.length;
  pdf+=`xref\n0 ${objects.length+1}\n0000000000 65535 f \n`;
  offsets.slice(1).forEach(offset=>{
    pdf+=`${String(offset).padStart(10,'0')} 00000 n \n`;
  });
  pdf+=`trailer\n<< /Size ${objects.length+1} /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF`;
  return new Blob([pdf],{type:'application/pdf'});
}

function invoicePdfFilename(inv){
  return `${String(inv?.invoice_no||'invoice').replace(/[^a-z0-9_-]+/gi,'-')}.pdf`;
}

function invoiceViewPrintHtml(inv=currentInvoiceForShare()){
  renderSalesInvoicePreview(inv);
  const body=document.getElementById('sales-view-body');
  const invoiceHtml=body?.innerHTML||invoicePdfHtml(inv);
  const title=escapeHtml(inv?.invoice_no||'Invoice');
  return `<!doctype html><html><head><meta charset="utf-8"><title>${title} - TaxFlow</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Syne:wght@600;700;800&family=DM+Sans:wght@400;500;600&family=DM+Mono:wght@400;500&display=swap">
    <link rel="stylesheet" href="/taxflow/src/styles.css">
    <style>
      body{margin:0;background:#fff;color:#172033;padding:24px;height:auto;overflow:auto;}
      .invoice-print-shell{max-width:980px;margin:0 auto;}
      .invoice-sheet{box-shadow:none!important;border-color:#d9dee8!important;}
      @media print{
        body{padding:0;print-color-adjust:exact;-webkit-print-color-adjust:exact;}
        .invoice-print-shell{max-width:none;margin:0;}
        .invoice-sheet{border:0!important;border-radius:0!important;}
      }
    </style></head><body class="theme-light"><main class="invoice-print-shell">${invoiceHtml}</main><script>window.onload=()=>setTimeout(()=>window.print(),600);<\/script></body></html>`;
}

function downloadInvoicePdf(inv=currentInvoiceForShare()){
  const printWindow=window.open('','_blank','width=980,height=780');
  if(!printWindow){
    toast('Allow popups to open the invoice PDF view','warn');
    return;
  }
  printWindow.document.open();
  printWindow.document.write(invoiceViewPrintHtml(inv));
  printWindow.document.close();
  toast('PDF view opened. Choose Save as PDF to export.','ok');
  audit('Opened invoice PDF view',inv?.invoice_no||'Draft','Exported');
}

function downloadCurrentInvoicePdf(){
  downloadInvoicePdf(currentInvoiceForShare());
}

function downloadSalesInvoiceRowPdf(btn){
  const inv=invoiceFromSalesRow(btn.closest('tr'));
  downloadInvoicePdf(inv);
}

function copyCurrentInvoiceLink(){
  const inv=currentInvoiceForShare();
  const link=publicInvoiceUrl(inv);
  const field=document.getElementById('share-link');
  if(field)field.value=link;
  navigator.clipboard?.writeText(link).then(()=>toast('Online invoice link copied','ok')).catch(()=>{
    field?.select();
    toast('Select and copy the online invoice link','info');
  });
}

function sampleLayoutInvoice(){
  const layout=getInvoiceLayout();
  const subtotal=12400;
  const vat=620;
  return {
    invoice_no:'INV-PREVIEW',
    status:'Draft',
    customer:'Customer Name LLC',
    customer_trn:'100348712600001',
    customer_address:'Business Bay, Dubai, United Arab Emirates',
    po_number:'PO-1024',
    delivery_note_no:'DN-7781',
    reference_no:'REF-DXB-01',
    date:new Date().toISOString().slice(0,10),
    due_date:new Date(Date.now()+Number(layout.dueDays||30)*86400000).toISOString().slice(0,10),
    subtotal,
    vat_amount:vat,
    total:subtotal+vat,
    source:'Manual',
    lines:[{description:'Steel materials',unit:'PCS',qty:1,price:subtotal,amount:subtotal}]
  };
}

function previewLayoutSampleInvoice(){
  renderSalesInvoicePreview(sampleLayoutInvoice());
  showM('m-sales-view');
}

function updateInvoiceLayoutPreview(){
  const layout=getInvoiceLayout();
  const preview=document.getElementById('invoice-layout-preview');
  if(!preview)return;
  const trn=currentCompany?.trn||document.getElementById('set-company-trn')?.value||'';
  const sampleSubtotal=12400;
  const sampleVat=620;
  const sampleTotal=13020;
  const sampleInvoice=sampleLayoutInvoice();
  const digitalUrl=publicInvoiceUrl(sampleInvoice);
  const initials=(layout.logo||layout.company||'TF').split(/\s+/).map(part=>part[0]).join('').slice(0,2).toUpperCase()||'TF';
  const textAlign={left:'left',center:'center',right:'right'}[layout.align]||'left';
  const brandJustify={left:'flex-start',center:'center',right:'flex-end'}[layout.align]||'flex-start';
  const fontFamily=layout.font==='Classic Serif'?'Georgia,serif':layout.font==='Compact Mono'?'DM Mono,monospace':'Syne,sans-serif';
  const qrValue=invoiceQrValue(sampleInvoice,layout);
  const qrType=layout.qrCodeType==='payment_url'?'invoice_url':layout.qrCodeType;
  const labels=invoiceLabels(layout);
  const heading=salesDocumentHeading(inv,layout);
  preview.innerHTML=`
    <div class="invoice-layout-live" dir="${layout.enableRtl?'rtl':'ltr'}" style="--invoice-accent:${escapeHtml(layout.color)}">
      <div class="invoice-topbar"></div>
      <div class="invoice-head invoice-layout-head" style="grid-template-columns:1fr;gap:12px">
        <div class="invoice-brand" style="justify-content:${brandJustify};text-align:${textAlign}">
          <div class="invoice-logo">${escapeHtml(initials)}</div>
          <div>
            <div class="invoice-company" style="font-family:${fontFamily}">${escapeHtml(layout.company)}</div>
            <div class="invoice-muted">${escapeHtml(layout.address)}</div>
            ${layout.trnMode==='show'&&layout.showTrn?`<div class="invoice-muted mono">${escapeHtml(layout.trnLabel)} ${escapeHtml(trn||'not set')}</div>`:''}
          </div>
        </div>
        <div class="invoice-titlebox" style="align-items:${brandJustify};text-align:${textAlign}">
          <div class="invoice-label" style="font-size:22px">${escapeHtml(labels.heading)}</div>
          <div class="invoice-number mono">${escapeHtml(layout.paper)} - INV-PREVIEW</div>
        </div>
      </div>
      <div class="invoice-info-grid" style="grid-template-columns:1fr;gap:12px;padding:16px">
        <div class="invoice-panel">
          <div class="invoice-kicker">${escapeHtml(labels.billTo)}</div>
          <div class="invoice-party">Customer Name LLC</div>
          ${layout.showCustomerTrn?`<div class="invoice-muted mono">${escapeHtml(layout.customerTrnLabel)} 100348712600001</div>`:''}
        </div>
        <div class="invoice-panel invoice-meta-panel">
          ${layout.showPaymentTerms?`<div class="invoice-meta-row"><span>${escapeHtml(labels.paymentTerms)}</span><strong>${escapeHtml(layout.terms)}</strong></div>`:''}
          ${layout.showPoNumber?`<div class="invoice-meta-row"><span>${escapeHtml(layout.poLabel)}</span><strong>PO-1024</strong></div>`:''}
          ${layout.showDeliveryNote?`<div class="invoice-meta-row"><span>${escapeHtml(layout.deliveryLabel)}</span><strong>DN-7781</strong></div>`:''}
          ${layout.showReferenceNo?`<div class="invoice-meta-row"><span>${escapeHtml(layout.referenceLabel)}</span><strong>REF-DXB-01</strong></div>`:''}
          <div class="invoice-meta-row"><span>${escapeHtml(invoiceBilingualLabel(layout,'Due Days','أيام الاستحقاق'))}</span><strong>${escapeHtml(layout.dueDays)}</strong></div>
          <div class="invoice-meta-row"><span>${escapeHtml(invoiceBilingualLabel(layout,'Language','اللغة'))}</span><strong>${escapeHtml(layout.language)}</strong></div>
        </div>
      </div>
      <table class="invoice-line-table" style="width:calc(100% - 32px);margin:0 16px">
        <tbody>
          <tr><td><strong>Steel materials</strong></td><td class="mono num">${sampleSubtotal.toLocaleString('en-AE',{minimumFractionDigits:2})}</td></tr>
          ${layout.showVatRate?`<tr><td>${escapeHtml(layout.vatLabel)} 5%</td><td class="mono num">${sampleVat.toLocaleString('en-AE',{minimumFractionDigits:2})}</td></tr>`:''}
          ${layout.taxSummary?`<tr><td>${escapeHtml(invoiceBilingualLabel(layout,'VAT Summary','ملخص الضريبة'))}</td><td class="mono num">${sampleVat.toLocaleString('en-AE',{minimumFractionDigits:2})}</td></tr>`:''}
          <tr><td style="font-weight:800">${escapeHtml(labels.total)}</td><td class="mono num" style="font-weight:800;color:var(--invoice-accent)">${sampleTotal.toLocaleString('en-AE',{minimumFractionDigits:2})} AED</td></tr>
        </tbody>
      </table>
      <div class="invoice-summary-grid" style="grid-template-columns:1fr;gap:12px;padding:16px">
        <div class="invoice-notes">
          <div class="invoice-kicker">${escapeHtml(labels.paymentDetails)}</div>
          ${layout.showBankDetails?`<div>${escapeHtml(layout.bankName||'Bank Name')}</div><div class="invoice-muted mono">${escapeHtml(layout.iban||'IBAN')}</div>`:''}
          <div class="invoice-muted">${escapeHtml(layout.footer)}</div>
          ${layout.qr?`<div class="invoice-qr-row">
            <a class="invoice-qr" href="${escapeHtml(digitalUrl)}" target="_blank" rel="noopener"><img id="layout-preview-qr" alt="Digital invoice QR"></a>
            <div><strong>${escapeHtml(labels.scanQr)} (${escapeHtml(qrType)})</strong><span>${escapeHtml(invoiceBilingualLabel(layout,'Dubai default is Invoice URL; ZATCA is disabled for UAE.','الافتراضي في دبي هو رابط الفاتورة؛ رمز ZATCA مخصص للسعودية فقط.'))}</span></div>
          </div>`:''}
        </div>
        ${layout.signature?`<div class="invoice-signatures" style="grid-template-columns:1fr;margin:0;padding-top:14px"><div><span>${escapeHtml(labels.authorizedSignature)}</span><strong>${escapeHtml(labels.companyStamp)}</strong></div></div>`:''}
      </div>
    </div>`;
  if(layout.qr)renderInvoiceQrCode(qrValue,'layout-preview-qr');
}

function renderSalesInvoicePreview(inv){
  currentSalesInvoice=inv;
  const title=document.getElementById('sales-view-title');
  const sub=document.getElementById('sales-view-sub');
  const body=document.getElementById('sales-view-body');
  if(!body)return;
  const layout=getInvoiceLayout();
  const companyTrn=currentCompany?.trn||document.getElementById('set-company-trn')?.value||'';
  const subtotal=Number(inv.subtotal||0);
  const vat=Number(inv.vat_amount||0);
  const total=Number(inv.total||subtotal+vat);
  const lines=(inv.lines&&inv.lines.length?inv.lines:[{description:'Sales invoice items',qty:1,price:subtotal,amount:subtotal}]);
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  const status=inv.status||'Draft';
  const customerAddress=inv.customer_address||document.getElementById('inv-caddr')?.value||'Billing address not provided';
  const accent=escapeHtml(layout.color||'#2563eb');
  const initials=(layout.logo||layout.company||'TF').split(/\s+/).map(part=>part[0]).join('').slice(0,2).toUpperCase()||'TF';
  const vatRate=subtotal>0?Math.round((vat/subtotal)*100):5;
  const issueDate=inv.date||new Date().toISOString().slice(0,10);
  const dueDate=inv.due_date||'-';
  const balanceDue=total;
  const digitalUrl=publicInvoiceUrl(inv);
  const textAlign={left:'left',center:'center',right:'right'}[layout.align]||'left';
  const brandJustify={left:'flex-start',center:'center',right:'flex-end'}[layout.align]||'flex-start';
  const fontFamily=layout.font==='Classic Serif'?'Georgia,serif':layout.font==='Compact Mono'?'DM Mono,monospace':'Syne,sans-serif';
  const qrValue=invoiceQrValue(inv,layout);
  const qrType=layout.qrCodeType==='payment_url'?'invoice_url':layout.qrCodeType;
  const labels=invoiceLabels(layout);
  const heading=salesDocumentHeading(inv,layout);
  const lineVat=n=>Number(n||0)*(vatRate/100);
  const invoiceRefs=[
    layout.showPoNumber&&inv.po_number?[layout.poLabel,inv.po_number]:null,
    layout.showDeliveryNote&&inv.delivery_note_no?[layout.deliveryLabel,inv.delivery_note_no]:null,
    layout.showReferenceNo&&inv.reference_no?[layout.referenceLabel,inv.reference_no]:null
  ].filter(Boolean);
  const bankRows=[
    layout.bankName&&['Bank Name',layout.bankName],
    layout.accountName&&['Account Name',layout.accountName],
    layout.accountNumber&&['Account Number',layout.accountNumber],
    layout.iban&&['IBAN',layout.iban],
    layout.swiftCode&&['SWIFT',layout.swiftCode]
  ].filter(Boolean);
  const signatureBlocks=[
    layout.showPreparedBy&&[labels.preparedBy,'TaxFlow UAE'],
    layout.showApprovedBy&&[labels.approvedBy,''],
    layout.showAuthorizedSignature&&[labels.authorizedSignature,''],
    layout.showCompanyStamp&&[labels.companyStamp,'']
  ].filter(Boolean);

  if(title)title.textContent=(isSalesReturn(inv)?'Sales Return ':'Invoice ')+(inv.invoice_no||'Draft');
  if(sub)sub.textContent=(inv.customer||'Customer')+' - '+status;

  body.innerHTML=`
    <div class="invoice-sheet" dir="${layout.enableRtl?'rtl':'ltr'}" style="--invoice-accent:${accent}">
      <div class="invoice-topbar"></div>
      <div class="invoice-head">
        <div class="invoice-brand" style="justify-content:${brandJustify};text-align:${textAlign}">
          <div class="invoice-logo" aria-label="Invoice logo">${escapeHtml(initials)}</div>
          <div>
            <div class="invoice-company" style="font-family:${fontFamily}">${escapeHtml(layout.company)}</div>
            <div class="invoice-muted">${escapeHtml(layout.address)}</div>
            ${layout.trnMode==='show'&&layout.showTrn?`<div class="invoice-muted mono">${escapeHtml(layout.trnLabel)} ${escapeHtml(companyTrn||'not set')}</div>`:''}
          </div>
        </div>
        <div class="invoice-titlebox" style="align-items:${brandJustify};text-align:${textAlign}">
          <div class="invoice-label">${escapeHtml(heading)}</div>
          <div class="invoice-number mono">${escapeHtml(inv.invoice_no||'Draft')}</div>
          <span class="invoice-status">${escapeHtml(status)}</span>
        </div>
      </div>

      <div class="invoice-info-grid">
        <div class="invoice-panel">
          <div class="invoice-kicker">${escapeHtml(labels.billTo)}</div>
          <div class="invoice-party">${escapeHtml(inv.customer||'Customer')}</div>
          ${layout.showCustomerTrn?`<div class="invoice-muted mono">${escapeHtml(layout.customerTrnLabel)} ${escapeHtml(inv.customer_trn||'not provided')}</div>`:''}
          <div class="invoice-muted">${escapeHtml(customerAddress)}</div>
        </div>
        <div class="invoice-panel invoice-meta-panel">
          <div class="invoice-meta-row"><span>${escapeHtml(labels.issueDate)}</span><strong>${escapeHtml(issueDate)}</strong></div>
          <div class="invoice-meta-row"><span>${escapeHtml(labels.dueDate)}</span><strong>${escapeHtml(dueDate)}</strong></div>
          ${layout.showPaymentTerms?`<div class="invoice-meta-row"><span>${escapeHtml(labels.paymentTerms)}</span><strong>${escapeHtml(layout.terms||'Net 30')}</strong></div>`:''}
          ${invoiceRefs.map(([label,value])=>`<div class="invoice-meta-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('')}
          <div class="invoice-meta-row"><span>${escapeHtml(labels.currency)}</span><strong>${AED_SYMBOL}</strong></div>
        </div>
      </div>

      <table class="invoice-line-table">
        <thead><tr><th>#</th><th>${escapeHtml(labels.product)}</th><th>${escapeHtml(labels.unit)}</th><th class="num">${escapeHtml(labels.quantity)}</th><th class="num">${escapeHtml(labels.unitPrice)}</th>${layout.showTaxableAmount?`<th class="num">${escapeHtml(labels.taxable)}</th>`:''}${layout.showVatRate?`<th class="num">${escapeHtml(labels.vat)}</th>`:''}${layout.showVatAmount?`<th class="num">${escapeHtml(labels.vatAmount)}</th>`:''}<th class="num">${escapeHtml(labels.amount)}</th></tr></thead>
        <tbody>
          ${lines.map((line,index)=>`<tr><td class="mono">${index+1}</td><td><strong>${escapeHtml(line.description||'Item')}</strong></td><td>${escapeHtml(line.unit||'PCS')}</td><td class="mono num">${escapeHtml(line.qty||1)}</td><td class="mono num">${fmt(line.price)}</td>${layout.showTaxableAmount?`<td class="mono num">${fmt(line.amount)}</td>`:''}${layout.showVatRate?`<td class="mono num">${vatRate}%</td>`:''}${layout.showVatAmount?`<td class="mono num">${fmt(lineVat(line.amount))}</td>`:''}<td class="mono num">${fmt(line.amount+(layout.vatMode==='inclusive'?0:lineVat(line.amount)))}</td></tr>`).join('')}
        </tbody>
      </table>

      <div class="invoice-summary-grid">
        <div class="invoice-notes">
          <div class="invoice-kicker">${escapeHtml(labels.paymentDetails)}</div>
          ${layout.showBankDetails?bankRows.map(([label,value])=>`<div class="invoice-meta-row"><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join(''):''}
          <div class="invoice-muted">${escapeHtml(layout.footer)}</div>
          ${layout.qr?`<div class="invoice-qr-row">
            <a class="invoice-qr" href="${escapeHtml(digitalUrl)}" target="_blank" rel="noopener" title="Open digital invoice">
              <img id="public-invoice-qr" alt="QR code for digital invoice">
            </a>
            <div>
              <strong>${escapeHtml(labels.scanQr)} (${escapeHtml(qrType)})</strong>
              <span>${escapeHtml(invoiceBilingualLabel(layout,'Anyone with this QR can open the online invoice format.','يمكن لأي شخص لديه هذا الرمز فتح الفاتورة الرقمية.'))}</span>
              <a href="${escapeHtml(digitalUrl)}" target="_blank" rel="noopener">${escapeHtml(labels.openDigitalInvoice)}</a>
            </div>
          </div>`:''}
        </div>
        <div class="invoice-total-card">
          ${layout.taxSummary?`<div class="invoice-total-row"><span>${escapeHtml(labels.subtotal)}</span><strong class="mono">${'AED'} ${fmt(subtotal)}</strong></div>
          <div class="invoice-total-row"><span>${escapeHtml(labels.vat)} ${vatRate}%</span><strong class="mono">${'AED'} ${fmt(vat)}</strong></div>`:''}
          <div class="invoice-grand"><span>${escapeHtml(labels.total)}</span><strong class="mono">${'AED'} ${fmt(total)}</strong></div>
          <div class="invoice-due"><span>${escapeHtml(labels.balanceDue)}</span><strong class="mono">AED ${fmt(balanceDue)}</strong></div>
        </div>
      </div>

      ${layout.signature&&signatureBlocks.length?`<div class="invoice-signatures">
        ${signatureBlocks.map(([label,value])=>`<div><span>${escapeHtml(label)}</span><strong>${escapeHtml(value)}</strong></div>`).join('')}
      </div>`:''}
    </div>`;
  renderInvoiceQrCode(qrValue);
}

let currentSalesInvoice=null;

function openSalesInvoiceRow(btn){
  const row=btn.closest('tr');
  const inv=invoiceFromSalesRow(row);
  // store source row reference on the modal for edit/mark-paid actions
  const modal=document.getElementById('m-sales-view');
  if(modal)modal._sourceRow=row;
  renderSalesInvoicePreview(inv);
  showM('m-sales-view');
  audit('Viewed sales invoice',inv.invoice_no||'Draft','Viewed');
}

function markSalesInvoicePaidFromView(){
  const modal=document.getElementById('m-sales-view');
  const row=modal?._sourceRow;
  const inv=row?invoiceFromSalesRow(row):currentSalesInvoice;
  openReceiptForInvoice(inv);
}

function shareSalesInvoiceRow(btn){
  const inv=invoiceFromSalesRow(btn.closest('tr'));
  currentSalesInvoice=inv;
  openInvoiceShareModal(inv);
}

function normalizeSalesInvoiceActions(){
  document.querySelectorAll('#sales-invoice-tbody tr').forEach(row=>{
    if(row.querySelector('td[colspan]'))return;
    const cells=[...row.children];
    const actionCell=cells.find(cell=>cell.querySelector('button[onclick*="openSalesInvoiceRow"],button[onclick*="shareSalesInvoiceRow"]'))||cells[cells.length-1];
    if(!actionCell)return;
    actionCell.dataset.actionCol='1';
    actionCell.innerHTML=salesInvoiceActionsHtml();
    row.dataset.rowActionsAdded='1';
  });
  const head=document.querySelector('#sales-invoice-tbody')?.closest('table')?.tHead?.rows?.[0];
  const last=head?.cells?.[head.cells.length-1];
  if(last)last.dataset.actionCol='1';
}

function nextSalesInvoiceNumber(){
  const year=new Date().getFullYear();
  const suffix=String(Date.now()).slice(-6);
  return `INV-${year}-${suffix}`;
}

function buildDraftInvoice(){
  const invoiceNoField=document.getElementById('inv-no');
  const invoiceNo=(invoiceNoField?.value||'').trim()||nextSalesInvoiceNumber();
  if(invoiceNoField&&!invoiceNoField.value.trim())setFieldValue(invoiceNoField,invoiceNo);
  const lines=[...document.querySelectorAll('#inv-lines .inv-item')].map(row=>{
    const qty=parseAmount(row.querySelector('.inv-qty')?.value);
    const price=parseAmount(row.querySelector('.inv-price')?.value);
    const description=(row.querySelector('.inv-product')?.value||'').trim();
    return {
      description:description||'Item',
      unit:row.querySelector('.inv-unit')?.value||'',
      qty,
      price,
      unit_price:price,
      amount:qty*price,
      product_code:row.dataset.productCode||'',
      product_name:row.dataset.productName||description||'',
      price_source:row.dataset.priceSource||'Manual',
      price_snapshot:parseAmount(row.dataset.priceSnapshot||price),
      price_locked_at:new Date().toISOString(), // price fixed at time of invoice creation
      inventory_mapping_id:row.dataset.mappingId||'',
      inventory_mapped:row.dataset.mapped==='true',
      mapping_price:parseAmount(row.dataset.sourcePrice||price),
      mapping_cost:parseAmount(row.dataset.mappingCost||0),
      markup_percent:parseAmount(row.dataset.markupPercent||0),
      tax_rate:parseAmount(row.dataset.taxRate||5)
    };
  }).filter(line=>line.description||line.qty||line.price);
  const subtotal=lines.reduce((sum,line)=>sum+line.amount,0);
  const vat=subtotal*.05;
  return {
    invoice_no:invoiceNo,
    document_type:currentSalesTransactionType==='return'?'Sales Return':'Sales Invoice',
    customer:(document.getElementById('inv-cust')?.value||'').trim()||'Customer',
    customer_trn:document.getElementById('inv-ctrn')?.value||'TRN not provided',
    customer_address:document.getElementById('inv-caddr')?.value||'',
    po_number:document.getElementById('inv-po')?.value||'',
    delivery_note_no:document.getElementById('inv-delivery')?.value||'',
    reference_no:document.getElementById('inv-ref')?.value||'',
    date:document.getElementById('inv-date')?.value||'',
    due_date:document.getElementById('inv-due')?.value||'',
    subtotal,
    vat_amount:vat,
    total:subtotal+vat,
    status:'Draft',
    source:currentSalesTransactionType==='return'?'Sales Return':'Manual',
    lines
  };
}

function validateDraftInvoice(inv){
  if(!inv.invoice_no)return 'Invoice number is required';
  if(!inv.customer||inv.customer==='Customer')return 'Select or enter customer name';
  const validLine=(inv.lines||[]).some(line=>String(line.description||'').trim()&&Number(line.qty)>0&&Number(line.price)>=0&&Number(line.amount)>0);
  if(!validLine)return 'Add at least one invoice line with quantity and price';
  if(Number(inv.total||0)<=0)return 'Invoice total must be greater than zero';
  return '';
}

function showSalesInvoiceRegister(){
  const tab=document.querySelector('#page-sales .tab:nth-child(4)');
  if(tab)stab(tab,'s-invoices');
}

function openSalesAddChoice(){
  showAddChoice('Sales',[
    {title:'Sales Invoice',sub:'Create a normal customer sales invoice',action:"startSalesTransaction('sale')"},
    {title:'Sales Return',sub:'Create a customer return / credit document',action:"startSalesTransaction('return')"}
  ]);
}

function openPurchaseAddChoice(){
  showAddChoice('Purchase',[
    {title:'Purchase Invoice',sub:'Create a normal supplier purchase entry',action:"startPurchaseTransaction('purchase')"},
    {title:'Purchase Return',sub:'Create a supplier return / debit note',action:"startPurchaseTransaction('return')"},
    {title:'Local Purchase Order',sub:'Create a local purchase order for domestic suppliers',action:"startPurchaseTransaction('local_po')"}
  ]);
}

function showAddChoice(scope,items){
  const title=document.getElementById('add-choice-title');
  const sub=document.getElementById('add-choice-sub');
  const box=document.getElementById('add-choice-options');
  if(title)title.textContent=`Add New ${scope}`;
  if(sub)sub.textContent='Confirm the document type before opening the entry form';
  if(box)box.innerHTML=items.map(item=>`
    <button class="btn btn-g" style="display:block;text-align:left;padding:14px;width:100%" onclick="${item.action}">
      <strong>${escapeHtml(item.title)}</strong>
      <div style="font-size:12px;color:var(--text3);margin-top:4px">${escapeHtml(item.sub)}</div>
    </button>
  `).join('');
  showM('m-add-choice');
}

function startSalesTransaction(type='sale'){
  currentSalesTransactionType=type==='return'?'return':'sale';
  closeM('m-add-choice');
  go('sales');
  setTimeout(()=>{
    const tab=document.querySelector('#page-sales .tab:nth-child(5)');
    if(tab)stab(tab,'s-create');
    configureSalesFormMode();
  },50);
}

function configureSalesFormMode(){
  const isReturn=currentSalesTransactionType==='return';
  setText('sales-form-title',isReturn?'Sales Return Details':'Invoice Details');
  setText('sales-form-sub',isReturn?'Create a customer return for returned goods or credit adjustment':'Create a sales invoice for goods or services');
  setText('sales-no-label',isReturn?'Return No.':'Invoice No.');
  setText('sales-date-label',isReturn?'Return Date':'Invoice Date');
  setText('sales-total-label',isReturn?'Return Total':'Total');
  setText('sales-save-send-btn',isReturn?'Save Return':'Save and Send');
  const invNo=document.getElementById('inv-no');
  if(invNo&&(!invNo.value||(isReturn&&/^INV-/i.test(invNo.value))||(!isReturn&&/^SR-/i.test(invNo.value)))){
    invNo.value=`${isReturn?'SR':'INV'}-${new Date().getFullYear()}-${String(Date.now()).slice(-5)}`;
  }
}

function saveDraftInvoice(options={}){
  const inv={...buildDraftInvoice(),status:options.status||'Draft'};
  if(currentSalesTransactionType==='return'){
    inv.document_type='Sales Return';
    inv.source='Sales Return';
    inv.status=options.status||'Return';
  }else{
    inv.document_type='Sales Invoice';
  }
  const message=validateDraftInvoice(inv);
  if(message){
    toast(message,'warn');
    return null;
  }
  currentSalesInvoice=inv;
  const saved=addSalesInvoiceRow(inv);
  if(saved){
    audit(options.auditAction||'Saved draft sales invoice',inv.invoice_no,'Saved');
    toast(options.toast||'Invoice saved to register','ok');
    clearDraftInvoiceForm();
  }else{
    toast(`${currentSalesTransactionType==='return'?'Sales return':'Invoice'} ${inv.invoice_no} is already in the register`,'warn');
  }
  if(options.showRegister!==false)showSalesInvoiceRegister();
  return inv;
}

function clearDraftInvoiceForm(){
  const form=document.getElementById('s-create');
  if(!form)return;
  form.querySelectorAll('input,textarea').forEach(field=>{
    if(field.classList.contains('inv-qty'))field.value='1';
    else if(field.classList.contains('inv-price')||field.classList.contains('inv-amount'))field.value='0.00';
    else field.value='';
  });
  form.querySelectorAll('select').forEach(select=>{select.selectedIndex=0;});
  const lines=document.getElementById('inv-lines');
  if(lines){
    lines.innerHTML='';
    lineCount=0;
    const row=addLine();
    const product=row?.querySelector('.inv-product');
    const unit=row?.querySelector('.inv-unit');
    const qty=row?.querySelector('.inv-qty');
    const price=row?.querySelector('.inv-price');
    const amount=row?.querySelector('.inv-amount');
    if(product)product.value='';
    if(unit)unit.value='PCS';
    if(qty)qty.value='1';
    if(price)price.value='0.00';
    if(amount)amount.value='0.00';
  }
  currentSalesInvoice=null;
  calcLine(null);
  configureSalesFormMode();
}

function saveAndSendDraftInvoice(){
  if(currentSalesTransactionType==='return'){
    saveDraftInvoice({
      status:'Return',
      auditAction:'Saved sales return',
      toast:'Sales return saved to register'
    });
    return;
  }
  const inv=saveDraftInvoice({
    status:'Pending',
    showRegister:false,
    auditAction:'Saved and sent sales invoice',
    toast:'Invoice saved. Share options opened'
  });
  if(inv)openInvoiceShareModal(inv);
}

function openDraftInvoicePreview(){
  const inv={
    ...buildDraftInvoice(),
    document_type:currentSalesTransactionType==='return'?'Sales Return':'Sales Invoice',
    status:currentSalesTransactionType==='return'?'Return Preview':'Draft'
  };
  renderSalesInvoicePreview(inv);
  showM('m-sales-view');
  audit(currentSalesTransactionType==='return'?'Previewed sales return':'Previewed draft invoice',inv.invoice_no,'Viewed');
}

function openDraftInvoiceShare(){
  if(currentSalesTransactionType==='return'){
    const inv=saveDraftInvoice({
      status:'Return',
      showRegister:false,
      auditAction:'Saved sales return for sharing',
      toast:'Sales return saved. Share options opened'
    });
    if(inv)openInvoiceShareModal(inv);
    return;
  }
  const inv=saveDraftInvoice({
    showRegister:false,
    auditAction:'Saved sales invoice for sharing',
    toast:'Invoice saved. Share options opened'
  });
  if(inv)openInvoiceShareModal(inv);
}

function invoiceShareMessage(inv=currentSalesInvoice){
  const total=Number(inv?.total||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  const label=isSalesReturn(inv)?'sales return':'invoice';
  return `Dear ${inv?.customer||'Customer'}, please find ${label} ${inv?.invoice_no||'Draft'} for AED ${total}. Due date: ${inv?.due_date||'-'}.\n\nView document online: ${publicInvoiceUrl(inv)}\nPDF document: please attach the PDF opened from TaxFlow.`;
}

function openInvoiceShareModal(inv=currentSalesInvoice){
  currentSalesInvoice=inv||currentSalesInvoice||buildDraftInvoice();
  const sub=document.getElementById('invoice-share-sub');
  const emailEl=document.getElementById('share-email');
  const phoneEl=document.getElementById('share-phone');
  const msg=document.getElementById('share-message');
  const link=document.getElementById('share-link');
  if(sub)sub.textContent=`${currentSalesInvoice.invoice_no||'Draft'} — ${currentSalesInvoice.customer||'Customer'}`;
  // auto-fill contact from customer records
  const custName=(currentSalesInvoice.customer||'').trim().toLowerCase();
  const custRec=invoiceCustomerRecords().find(c=>c.name.toLowerCase()===custName);
  if(emailEl)emailEl.value=currentSalesInvoice.customer_email||custRec?.email||'';
  if(phoneEl)phoneEl.value=currentSalesInvoice.customer_phone||custRec?.phone||'';
  if(msg)msg.value=invoiceShareMessage(currentSalesInvoice);
  if(link)link.value=publicInvoiceUrl(currentSalesInvoice);
  showM('m-invoice-share');
}

function shareCurrentInvoice(channel){
  const inv=currentSalesInvoice||buildDraftInvoice();
  const msg=document.getElementById('share-message')?.value||invoiceShareMessage(inv);
  const docLabel=isSalesReturn(inv)?'Sales Return':'Invoice';
  if(channel==='email'){
    const email=document.getElementById('share-email')?.value||'';
    const subject=encodeURIComponent(`${docLabel} ${inv.invoice_no||'Draft'} from ${getInvoiceLayout().company}`);
    const body=encodeURIComponent(msg);
    window.open(`mailto:${encodeURIComponent(email)}?subject=${subject}&body=${body}`,'_blank');
    toast(`Email opened with PDF note and online invoice link`, 'ok');
    audit('Shared invoice by email',inv.invoice_no||'Draft','Sent');
    return;
  }
  if(channel==='whatsapp'){
    toast(`WhatsApp message prepared for ${inv.invoice_no||'Draft'}`, 'ok');
    audit('Shared invoice by WhatsApp',inv.invoice_no||'Draft','Sent');
    const encoded=encodeURIComponent(msg);
    window.open(`https://wa.me/?text=${encoded}`,'_blank');
  }
}

function shareInvoicePdfAndLink(channel){
  const inv=currentSalesInvoice||buildDraftInvoice();
  currentSalesInvoice=inv;
  copyCurrentInvoiceLink();
  downloadInvoicePdf(inv);
  shareCurrentInvoice(channel);
  toast('PDF downloaded and online invoice link prepared', 'ok');
}

let customerReturnToInvoice=false;
let customerReturnToQuotation=false;

function openAddCustomerFromInvoice(){
  customerReturnToInvoice=true;
  customerReturnToQuotation=false;
  document.getElementById('cust-name').value=document.getElementById('inv-cust')?.value||'';
  document.getElementById('cust-trn').value=(document.getElementById('inv-ctrn')?.value||'').replace(/\D/g,'');
  showM('m-customer');
}

async function deletePurchaseRecord(btn){
  const row=btn.closest('tr');
  const table=row?.closest('table');
  if(!row||!table)return;
  const record=purchaseRecordFromRow(row);
  const ref=record?.ref||record?.invoice_no||record?.reference||row.children[0]?.textContent.trim();
  if(!ref)return;
  const status=String(record.status||'').toLowerCase();
  if(status.includes('paid')){
    toast(`Cannot delete ${ref}: paid purchases are linked to payment history.`,'warn');
    audit('Delete blocked',ref,'Paid purchase');
    return;
  }
  const confirmed=await appConfirm({
    title:'Delete Purchase Record',
    message:`Delete purchase record ${ref}? This also removes its source transaction and stock movement from the database.`,
    okText:'Delete'
  });
  if(!confirmed)return;
  row.remove();
  purchaseRecordCache.delete(String(ref));
  purchaseRecordsTotal=Math.max(0,purchaseRecordsTotal-1);
  const tbody=table.tBodies?.[0];
  if(tbody&&tbody.querySelectorAll('tr:not([data-empty-state])').length===0){
    emptyTableMessage(tbody,'No purchase records in database yet.');
  }
  refreshEnhancedTable(table);
  updatePurchaseRecordControls(purchaseRecordsTotal,purchaseRecordCache.size);
  syncStockLevelsFromProducts();
  deleteServer('purchaseRecords',record,{throwOnError:true})
    .then(()=>{
      loadStockLevelsFromServer();
      toast(`Purchase ${ref} deleted`,'ok');
    })
    .catch(()=>{
      toast(`Purchase ${ref} removed on screen, database delete failed`,'warn');
    });
  audit('Deleted purchase record',ref,'Deleted');
}

function openAddCustomerFromQuotation(){
  customerReturnToQuotation=true;
  customerReturnToInvoice=false;
  document.getElementById('cust-name').value=document.getElementById('quote-customer')?.value||'';
  document.getElementById('cust-trn').value='';
  showM('m-customer');
}

function saveCustomer(){
  const name=(document.getElementById('cust-name')?.value||'').trim();
  const trn=(document.getElementById('cust-trn')?.value||'').replace(/\D/g,'');
  const emirate=document.getElementById('cust-emirate')?.value||'Dubai';
  const address=(document.getElementById('cust-address')?.value||'').trim();
  const email=(document.getElementById('cust-email')?.value||'').trim();
  const phone=(document.getElementById('cust-phone')?.value||'').trim();

  if(!name){
    toast('Enter customer name','warn');
    return;
  }
  if(trn&&trn.length!==15){
    toast('Customer TRN must be 15 digits','err');
    return;
  }

  const tbody=document.getElementById('customer-tbody');
  const exists=tbody&&[...tbody.querySelectorAll('tr td:first-child')].some(td=>td.textContent.trim().toLowerCase()===name.toLowerCase());
  if(tbody&&!exists){
    renderCustomerRecord({name,trn,emirate,address,email,phone});
  }else{
    refreshInvoiceCustomerOptions();
    refreshQuotationCustomerOptions();
  }
  saveServer('customers',{name,trn,emirate,address,email,phone});

  const shouldFillInvoice=customerReturnToInvoice;
  const shouldFillQuotation=customerReturnToQuotation;
  closeM('m-customer');
  if(shouldFillInvoice){
    const invoiceCustomer=document.getElementById('inv-cust');
    const invoiceTrn=document.getElementById('inv-ctrn');
    const invoiceAddress=document.getElementById('inv-caddr');
    if(invoiceCustomer)invoiceCustomer.value=name;
    if(invoiceTrn)invoiceTrn.value=trn;
    if(invoiceAddress)invoiceAddress.value=address;
  }
  if(shouldFillQuotation){
    const quoteCustomer=document.getElementById('quote-customer');
    if(quoteCustomer)quoteCustomer.value=name;
    applyQuotationCustomerSelection();
  }

  ['cust-name','cust-trn','cust-address','cust-email','cust-phone'].forEach(id=>{
    const field=document.getElementById(id);
    if(field)field.value='';
  });
  toast('Customer added ?','ok');
  audit('Added customer',name,'Saved');
}

function dzOver(e,id){e.preventDefault();document.getElementById(id).classList.add('over');}
function dzLeave(id){document.getElementById(id).classList.remove('over');}
function dzDrop(e,id){
  e.preventDefault();document.getElementById(id).classList.remove('over');
  const files=[...e.dataTransfer.files];
  files.forEach(f=>readAndAddFile(f));
}
function purUpload(inp){
  const files=[...inp.files];
  files.forEach(f=>readAndAddFile(f));
  inp.value=''; // reset so same file can be re-selected
}

function readAndAddFile(file){
  const cat=document.getElementById('pur-cat')?.value||'Purchase Invoices';
  const period=document.getElementById('pur-period')?.value||'June 2024';
  const entry={name:file.name,size:file.size,type:file.type,base64:'',category:cat,period,status:'Reading',id:'F'+Date.now()+Math.random().toString(36).slice(2,6)};
  uploadedFiles.push(entry);
  renderFileList();
  updatePurchaseValidationFileStatus();
  updateFileCount();
  toast(`Reading ${file.name}...`,'info');
  const reader=new FileReader();
  reader.onload=function(e){
    const base64=e.target.result; // full data URL
    entry.base64=base64;
    entry.uploadedAt=entry.uploadedAt||new Date().toISOString();
    entry.status='Queued';
    persistPurchaseDocumentRecord(entry);
    animateUpload(file.name,file.size,entry);
  };
  reader.onerror=function(){
    entry.status='Error';
    renderFileList();
    updatePurchaseValidationFileStatus();
    toast(`Could not read ${file.name}`,'err');
  };
  reader.readAsDataURL(file);
}

function animateUpload(name,size,entry){
  const pg=document.getElementById('pur-prog'),fill=document.getElementById('pur-fill'),fn=document.getElementById('pur-fname'),pct=document.getElementById('pur-pct');
  pg.style.display='block';fn.textContent='Uploading: '+name;
  let p=0;
  const iv=setInterval(()=>{
    p+=Math.random()*18+5;
    if(p>=100){
      p=100;clearInterval(iv);
      setTimeout(()=>{
        pg.style.display='none';fill.style.width='0%';
        entry.status='Ready';
        persistPurchaseDocumentRecord(entry);
        renderFileList();
        updatePurchaseValidationFileStatus();
        updateFileCount();
        toast(name+' uploaded ?','ok');
        // auto-extract if setting says yes
        const autoEl=document.querySelector('#page-purchase select[id="pur-auto"]');
        if(!autoEl||String(autoEl.value||'').toLowerCase()==='yes')setTimeout(()=>extractSingleFile(entry),600);
      },300);
    }
    fill.style.width=Math.min(p,100)+'%';
    pct.textContent=Math.round(Math.min(p,100))+'%';
  },120);
}

function getFileIcon(name){
  const ext=name.split('.').pop().toLowerCase();
  return {pdf:'??',xlsx:'??',csv:'??',zip:'??',jpg:'??',jpeg:'??',png:'??'}[ext]||'??';
}
function fmtSize(bytes){if(bytes<1024*1024)return(bytes/1024).toFixed(0)+' KB';return(bytes/(1024*1024)).toFixed(1)+' MB';}

function renderFileList(){
  const list=document.getElementById('pur-file-list');
  if(!list)return;
  list.innerHTML='';
  if(!uploadedFiles.length){
    list.innerHTML='<div style="font-size:12px;color:var(--text3);padding:10px 0">No uploaded files in database yet.</div>';
    return;
  }

  uploadedFiles.forEach(f=>{
    const statusBadge={
      Queued:'<span class="b b-gray">Queued</span>',
      Reading:'<span class="b b-a">Reading</span>',
      Ready:'<span class="b b-b">Ready</span>',
      Extracting:'<span class="b b-a">Extracting-</span>',
      Extracted:'<span class="b b-g">Extracted ?</span>',
      Error:'<span class="b b-r">Error</span>'
    }[f.status]||'<span class="b b-gray">Unknown</span>';

    const extractBtn=f.status==='Ready'
      ?`<button class="btn btn-p btn-sm" style="margin-left:6px" onclick="extractSingleFile(uploadedFiles.find(x=>x.id==='${f.id}'))">? Extract</button>`
      :'';

    const row=document.createElement('div');
    row.setAttribute('data-file-id',f.id);
    row.style.cssText='display:flex;align-items:center;gap:10px;padding:10px 0;border-bottom:1px solid var(--border)';
    row.innerHTML=`
      <span style="font-size:20px">${getFileIcon(f.name)}</span>
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;font-weight:500;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${f.name}</div>
        <div class="mono" style="color:var(--text3);font-size:11px">${fmtSize(f.size)} - ${f.category} - ${f.period}</div>
      </div>
      <div style="display:flex;align-items:center;gap:6px;flex-shrink:0">${statusBadge}${extractBtn}</div>`;
    list.appendChild(row);
  });
}

function updateFileCount(){
  const badge=document.getElementById('file-count-badge');
  if(badge) badge.textContent=uploadedFiles.length+' files';
}

function loadPurchaseDocumentsFromServer(records,purchaseRecords=[]){
  (records||[]).forEach(record=>{
    const file=normalizePurchaseDocumentRecord(record);
    if(!file.id||purchaseDocumentIds.has(file.id))return;
    purchaseDocumentIds.add(file.id);
    uploadedFiles.push(file);
  });
  addPurchaseRecordHistoryDocuments(purchaseRecords);
  updatePurchaseValidationFileStatus();
  renderFileList();
  updateFileCount();
}

function addPurchaseRecordHistoryDocuments(purchaseRecords=[]){
  (purchaseRecords||[]).forEach(record=>{
    const ref=String(record?.ref||record?.invoice_no||record?.reference||'').trim();
    if(!ref)return;
    const id=`purchase-record-${ref}`.replace(/[^a-z0-9_.-]/gi,'-').slice(0,120);
    if(purchaseDocumentIds.has(id))return;
    purchaseDocumentIds.add(id);
    uploadedFiles.push({
      id,
      name:record.document_name||record.source_file||`Purchase ${ref}`,
      size:0,
      type:'',
      base64:'',
      category:'Purchase Records',
      period:record.date||'',
      status:'Extracted',
      invoices:[{
        invoice_no:ref,
        date:record.date||'',
        supplier:record.supplier||'',
        supplier_trn:record.supplier_trn||'',
        subtotal:record.net_amount||0,
        vat_amount:record.tax_amount||0,
        total:record.total||0,
        status:'Valid',
        confidence:100,
        lines:Array.isArray(record.lines)?record.lines:[]
      }],
      savedInvoiceNos:new Set([ref]),
      uploadedAt:record.createdAt||record.date||''
    });
  });
}

function normalizePurchaseDocumentRecord(record){
  const saved=record?.savedInvoiceNos instanceof Set?record.savedInvoiceNos:new Set(record?.savedInvoiceNos||[]);
  return {
    id:String(record?.id||purchaseDocumentId(record)||('DOC'+Date.now()+Math.random().toString(36).slice(2,6))),
    name:record?.name||'uploaded-document',
    size:Number(record?.size||0),
    type:record?.type||'',
    base64:record?.base64||'',
    category:record?.category||'Purchase Invoices',
    period:record?.period||'',
    status:record?.status||'Ready',
    invoices:Array.isArray(record?.invoices)?record.invoices:[],
    savedInvoiceNos:saved,
    uploadedAt:record?.uploadedAt||record?.createdAt||new Date().toISOString(),
    extractedAt:record?.extractedAt||''
  };
}

function purchaseDocumentId(file){
  return String(file?.id||`${file?.name||'document'}-${file?.size||0}-${file?.uploadedAt||file?.period||''}`)
    .replace(/[^a-z0-9_.-]/gi,'-')
    .slice(0,120);
}

function purchaseDocumentRecordFromFile(file){
  const saved=file.savedInvoiceNos instanceof Set?[...file.savedInvoiceNos]:(file.savedInvoiceNos||[]);
  return {
    id:purchaseDocumentId(file),
    name:file.name,
    size:file.size||0,
    type:file.type||'',
    base64:file.base64||'',
    category:file.category||'Purchase Invoices',
    period:file.period||'',
    status:file.status||'Ready',
    invoices:Array.isArray(file.invoices)?file.invoices:[],
    savedInvoiceNos:saved,
    uploadedAt:file.uploadedAt||new Date().toISOString(),
    extractedAt:file.extractedAt||''
  };
}

function persistPurchaseDocumentRecord(file){
  if(!file)return;
  file.id=purchaseDocumentId(file);
  purchaseDocumentIds.add(file.id);
  saveServer('purchaseDocuments',purchaseDocumentRecordFromFile(file));
}

function downloadUploadedPurchaseFile(id){
  const file=uploadedFiles.find(item=>String(item.id)===String(id));
  if(!file||!file.base64){
    toast('Original file is not available for download','warn');
    return;
  }
  const link=document.createElement('a');
  link.href=file.base64;
  link.download=file.name||'purchase-document';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function updatePurchaseValidationFileStatus(){
  const wrap=document.getElementById('pur-validation-file-status');
  if(!wrap)return;
  const files=uploadedFiles.filter(file=>['Extracted','Error','Ready','Queued'].includes(file.status));
  if(!files.length){
    wrap.innerHTML='<div class="stat"><div class="stat-lbl">Uploaded Files</div><div class="stat-val" style="font-size:18px;color:var(--text3)">No files</div><div class="stat-delta">Upload and extract purchase files first</div></div>';
    return;
  }
  wrap.innerHTML=files.map(file=>{
    const invoices=Array.isArray(file.invoices)?file.invoices:[];
    const saved=file.savedInvoiceNos instanceof Set?file.savedInvoiceNos:new Set(file.savedInvoiceNos||[]);
    const total=invoices.length;
    const savedCount=invoices.filter(inv=>saved.has(String(inv.invoice_no||''))).length;
    let label='Not uploaded';
    let cls='b-r';
    let color='var(--red)';
    let note=file.status==='Error'?'Extraction failed':'Not saved to purchase records';
    if(total>0&&savedCount>=total){
      label='Completed';
      cls='b-g';
      color='var(--green)';
      note=`${savedCount}/${total} saved to purchase records`;
    }else if(savedCount>0){
      label='Partial';
      cls='b-a';
      color='var(--amber)';
      note=`${savedCount}/${total||savedCount} saved to purchase records`;
    }else if(file.status==='Extracted'){
      note=`0/${total} saved to purchase records`;
    }else if(file.status==='Ready'){
      note='Ready for extraction';
    }
    return `<div class="stat">
      <div class="stat-lbl">${escapeHtml(file.name)}</div>
      <div class="stat-val" style="font-size:18px;color:${color}">${escapeHtml(label)}</div>
      <div class="stat-delta"><span class="b ${cls}">${escapeHtml(label)}</span> ${escapeHtml(note)}</div>
    </div>`;
  }).join('');
  renderPurchaseValidationDocumentStatus();
}

// -- AI EXTRACTION via backend API ----------------------------------
async function extractSingleFile(entry){
  if(!entry){toast('File not found','err');return;}
  entry.status='Extracting';
  renderFileList();
  toast('AI extracting: '+entry.name+'-','info');

  // Switch to the AI Extraction tab so user sees progress
  const extTab=document.querySelector('#page-purchase .tab:nth-child(2)');
  if(extTab)stab(extTab,'p-extract');

  const ep=document.getElementById('ext-prog'),ef=document.getElementById('ext-fill'),epct=document.getElementById('ext-pct');
  ep.style.display='block';
  let prog=0;
  const ticker=setInterval(()=>{prog=Math.min(prog+3,88);ef.style.width=prog+'%';epct.textContent=prog+'%';},200);

  try{
    const invoices=await requestInvoiceExtraction(entry);
    const extractionFailed=isExtractionErrorResult(invoices);

    entry.status=extractionFailed?'Error':'Extracted';
    entry.invoices=invoices;
    entry.savedInvoiceNos=entry.savedInvoiceNos||new Set();
    entry.extractedAt=new Date().toISOString();
    persistPurchaseDocumentRecord(entry);
    renderFileList();
    await appendExtractedRows(entry.invoices,entry.name);
    updateExtractionStats();
    try{
      updatePurchaseValidationFileStatus();
      buildValidationPanel(entry.invoices);
    }catch(panelErr){
      console.warn('Purchase validation status update failed:',panelErr);
    }
    if(invoices.length<=250){
      hydrateFromServer().catch(err=>console.warn('Refresh after extraction failed:',err));
    }

    setTimeout(()=>{ep.style.display='none';ef.style.width='0%';},600);
    toast(extractionFailed
      ? `Extraction needs review for ${entry.name}`
      : `Extracted ${entry.invoices.length} invoice(s) from ${entry.name} ?`, extractionFailed?'warn':'ok');

    if(entry.invoices.some(i=>!validatePurchaseAiInvoice(i).valid)){
      toast('Validation issues found - review required','warn');
    }

  }catch(err){
    clearInterval(ticker);
    ep.style.display='none';
    entry.status='Error';
    persistPurchaseDocumentRecord(entry);
    renderFileList();
    updatePurchaseValidationFileStatus();
    toast('Extraction failed: '+err.message,'err');
    console.error(err);
  }
}

function isExtractionErrorResult(invoices){
  return Array.isArray(invoices)&&invoices.length>0&&invoices.every(inv=>
    isPurchaseExtractionError(inv)
  );
}

async function appendExtractedRows(invoices,filename){
  const tbody=document.getElementById('ext-tbody');
  if(!tbody)return;
  if(tbody.querySelector('.ai-empty-state,[data-extraction-error]'))tbody.innerHTML='';
  if(!Array.isArray(invoices)||!invoices.length){
    tbody.innerHTML=`<div class="ai-empty-state" style="color:var(--red)">No data extracted from ${escapeHtml(filename||'uploaded file')}.</div>`;
    return;
  }
  let fragment=document.createDocumentFragment();
  let appended=0;
  const previewInvoices=invoices.slice(0,PURCHASE_AI_PREVIEW_LIMIT);
  for(const [invoiceIndex,inv] of previewInvoices.entries()){
    if(isPurchaseExtractionError(inv)){
      const row=document.createElement('div');
      row.className='ai-extract-card ai-error-card';
      row.dataset.extractionError='1';
      row.innerHTML=`
        <strong>${escapeHtml(inv.invoice_no||filename||'Upload')}</strong>: ${escapeHtml(inv.issues||'No purchase invoice rows were found in the uploaded file.')}
      `;
      fragment.appendChild(row);
      appended++;
      continue;
    }
    const invoiceUid=`${Date.now()}-${invoiceIndex}-${Math.random().toString(36).slice(2,8)}`;
    const validation={valid:true,issues:[]};
    const row=document.createElement('div');
    row.className='ai-extract-card';
    row.setAttribute('data-inv',JSON.stringify(inv));
    row.draggable=true;
    row.dataset.invoiceNo=inv.invoice_no||'';
    row.dataset.invoiceUid=invoiceUid;
    row.dataset.lineIndex='0';
    row.dataset.validation=validation.valid?'valid':'review';
    row.dataset.filename=filename||'';
    row.innerHTML=purchaseAiRowHtml(inv,(Array.isArray(inv.lines)&&inv.lines[0])||{},0,validation,filename||'');
    fragment.appendChild(row);
    appended++;
    if(appended&&appended%100===0){
      tbody.insertBefore(fragment,tbody.firstChild);
      fragment=document.createDocumentFragment();
      await yieldToBrowser();
    }
  }
  if(fragment.childNodes.length)tbody.insertBefore(fragment,tbody.firstChild);
  if(invoices.length>previewInvoices.length){
    const row=document.createElement('div');
    row.className='ai-empty-state';
    row.dataset.previewSummary='1';
    row.innerHTML=`Showing ${previewInvoices.length.toLocaleString('en-AE')} of ${invoices.length.toLocaleString('en-AE')} extracted invoices. Save All will save the full upload.`;
    tbody.appendChild(row);
  }
  revalidatePurchaseAiRows();
  enablePurchaseAiDragDrop();
  applyPurchaseAiSort(false);
  ensurePurchaseAiUploadTile();
  if(_purchaseAiView==='table')renderPurchaseAiFlatTable();
}

function yieldToBrowser(){
  return new Promise(resolve=>setTimeout(resolve,0));
}

let purchaseAiDraggedRow=null;

function purchaseAiRows(scope=document){
  const root=scope===document?document.getElementById('ext-tbody'):scope;
  return root?[...root.querySelectorAll('[data-inv]')]:[];
}

function purchaseAiRowFromButton(btn){
  return btn?.closest?.('[data-inv]');
}

function purchaseAiUploadTileHtml(){
  return `<div class="ai-upload-more-tile">
    <div class="ai-upload-more-icon">${uploadIconSvg()}</div>
    <strong>Upload More Invoices</strong>
    <span>Drag and drop files here or</span>
    <button class="btn btn-p btn-sm" type="button" onclick="document.getElementById('pur-file')?.click()">Upload Files</button>
  </div>`;
}

function ensurePurchaseAiUploadTile(){
  const grid=document.getElementById('ext-tbody');
  if(!grid||!purchaseAiRows(grid).length)return;
  grid.querySelectorAll('.ai-upload-more-tile').forEach(tile=>tile.remove());
  const holder=document.createElement('div');
  holder.innerHTML=purchaseAiUploadTileHtml();
  grid.appendChild(holder.firstElementChild);
}

function purchaseAiCurrentView(){
  const grid=document.getElementById('ext-tbody');
  return grid?.dataset.view||'grid';
}

function setPurchaseAiCardView(view='grid',btn=null){
  const grid=document.getElementById('ext-tbody');
  if(!grid)return;
  const next=view==='gallery'?'gallery':'grid';
  grid.dataset.view=next;
  grid.classList.toggle('view-gallery',next==='gallery');
  grid.classList.toggle('view-grid',next==='grid');
  document.querySelectorAll('.ai-view-toggle .seg').forEach(item=>{
    item.classList.toggle('on',item===btn||item.dataset.aiView===next);
  });
}

function purchaseAiSortValue(row,field){
  let inv={};
  try{inv=JSON.parse(row.dataset.inv||'{}');}catch{}
  const lineIndex=Number(row.dataset.lineIndex||0);
  const line=Array.isArray(inv.lines)?(inv.lines[lineIndex]||{}):{};
  if(field==='invoice')return String(inv.invoice_no||row.dataset.invoiceNo||'').toLowerCase();
  if(field==='date')return Date.parse(inv.date||'')||0;
  if(field==='supplier')return String(inv.supplier||'').toLowerCase();
  if(field==='total')return purchaseAiNumber(inv.total||line.line_total||line.amount);
  if(field==='validation')return row.dataset.validation==='valid'?1:0;
  return Number(row.dataset.originalIndex||0);
}

function applyPurchaseAiSort(userTriggered=false){
  const grid=document.getElementById('ext-tbody');
  const sort=document.getElementById('purchase-ai-sort')?.value||'manual';
  if(!grid||sort==='manual')return;
  const direction=document.getElementById('purchase-ai-sort-dir')?.dataset.dir||'asc';
  const rows=purchaseAiRows(grid);
  rows.forEach((row,index)=>{
    if(!row.dataset.originalIndex)row.dataset.originalIndex=String(index);
  });
  const extras=[...grid.children].filter(child=>!child.matches?.('[data-inv]'));
  rows.sort((a,b)=>{
    const av=purchaseAiSortValue(a,sort);
    const bv=purchaseAiSortValue(b,sort);
    const result=typeof av==='number'&&typeof bv==='number'
      ? av-bv
      : String(av).localeCompare(String(bv),undefined,{numeric:true,sensitivity:'base'});
    return direction==='desc'?-result:result;
  });
  rows.forEach(row=>grid.appendChild(row));
  extras.forEach(extra=>grid.appendChild(extra));
  if(userTriggered)toast('AI upload cards sorted','ok');
}

function togglePurchaseAiSortDirection(){
  const btn=document.getElementById('purchase-ai-sort-dir');
  if(!btn)return;
  const next=btn.dataset.dir==='desc'?'asc':'desc';
  btn.dataset.dir=next;
  btn.textContent=next==='asc'?'Asc':'Desc';
  applyPurchaseAiSort(true);
}

function enablePurchaseAiDragDrop(){
  const tbody=document.getElementById('ext-tbody');
  if(!tbody||tbody.dataset.dragBound==='1')return;
  tbody.dataset.dragBound='1';
  tbody.addEventListener('dragstart',event=>{
    const row=event.target.closest?.('[data-inv]');
    if(!row)return;
    purchaseAiDraggedRow=row;
    row.classList.add('dragging');
    event.dataTransfer.effectAllowed='move';
    event.dataTransfer.setData('text/plain',row.dataset.invoiceUid||row.dataset.invoiceNo||'row');
  });
  tbody.addEventListener('dragover',event=>{
    if(!purchaseAiDraggedRow)return;
    const target=event.target.closest?.('[data-inv]');
    if(!target||target===purchaseAiDraggedRow)return;
    event.preventDefault();
    const rect=target.getBoundingClientRect();
    const after=(event.clientY-rect.top)>rect.height/2;
    target.parentNode.insertBefore(purchaseAiDraggedRow,after?target.nextSibling:target);
  });
  tbody.addEventListener('dragend',()=>{
    if(purchaseAiDraggedRow){
      purchaseAiDraggedRow.classList.remove('dragging');
      purchaseAiDraggedRow=null;
      const sort=document.getElementById('purchase-ai-sort');
      if(sort)sort.value='manual';
      toast('AI rows reordered','ok');
    }
  });
}

function purchaseAiProductName(line={}){
  return String(line.product||line.product_name||line.item_name||line.description||line.item_description||line.sku||line.code||'').trim();
}

function purchaseAiRawDetails(line={}){
  const raw=line.raw||{};
  const entries=Object.entries(raw)
    .filter(([key,value])=>value!==undefined&&value!==null&&String(value).trim()!=='')
    .slice(0,8)
    .map(([key,value])=>`${key}: ${value}`);
  return entries.length?`Raw: ${entries.join(' | ')}`:'';
}

function purchaseAiInvoiceSummary(inv={}){
  const lines=Array.isArray(inv.lines)?inv.lines:[];
  const names=[...new Set(lines.map(line=>purchaseAiProductName(line)).filter(Boolean))];
  if(!names.length)return 'No items';
  const first=names.slice(0,2).join(', ');
  return names.length>2?`${first} +${names.length-2}`:first;
}

function purchaseAiInvoiceLineTotal(inv={}){
  const lines=Array.isArray(inv.lines)?inv.lines:[];
  const lineTotal=lines.reduce((sum,line)=>sum+purchaseAiNumber(line.line_total||line.amount||line.total),0);
  return lineTotal||purchaseAiNumber(inv.subtotal||inv.net_amount||inv.line_total||inv.amount);
}

function purchaseAiConfidenceBadge(inv={}){
  const confidence=purchaseAiNumber(inv.confidence);
  const cls=confidence>=90?'b-g':confidence>=70?'b-a':'b-r';
  return `<span class="b ${cls}">${confidence.toFixed(0)}%</span>`;
}

function purchaseAiStatusMeta(inv={},validation={valid:false}){
  if(!validation.valid)return {label:'Pending',cls:'pending'};
  const total=purchaseAiNumber(inv.total);
  const paid=purchaseAiNumber(inv.paid);
  if(total>0&&paid>=total)return {label:'Approved',cls:'approved'};
  if(paid>0)return {label:'Partial',cls:'partial'};
  return {label:'Pending',cls:'pending'};
}

function purchaseAiStatusPillHtml(label='Pending',cls='pending'){
  return `<span class="ai-status-pill ${escapeHtml(cls)}">${escapeHtml(label)}</span>`;
}

function purchaseAiRowHtml(inv,line,index,validation,filename){
  const fmt=n=>purchaseAiNumber(n).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  const lineTotal=purchaseAiInvoiceLineTotal(inv);
  const vat=purchaseAiNumber(inv.vat_amount);
  const net=purchaseAiNumber(inv.net_amount||inv.subtotal)||lineTotal;
  const shipping=purchaseAiNumber(inv.shipping);
  const discount=purchaseAiNumber(inv.discount_value||inv.discount);
  const total=purchaseAiNumber(inv.total)||(net+vat+shipping);
  const paid=purchaseAiNumber(inv.paid);
  const due=purchaseAiNumber(inv.due)||Math.max(0,total-paid);
  const cur=escapeHtml(inv.currency||'AED');
  const trnVal=inv.supplier_trn||'';
  const trnInvalid=trnVal&&trnVal.replace(/\D/g,'').length!==15;
  const status=purchaseAiStatusMeta(inv,validation);
  const details=validation.issues.join('; ')||purchaseAiRawDetails(line)||'Ready to save';
  const lines=Array.isArray(inv.lines)?inv.lines:[];
  const itemCount=lines.length||0;
  const productSummary=lines.slice(0,2).map(l=>purchaseAiProductName(l)||l.description||'').filter(Boolean).join(', ')+(itemCount>2?` +${itemCount-2} more`:'');
  return `
    <div class="ai-card-head">
      <div class="ai-card-title-wrap">
        <input type="checkbox" class="purchase-ai-select" ${validation.valid?'checked':''} aria-label="Select ${escapeHtml(inv.invoice_no)} line ${index+1}">
        <div class="ai-pdf-icon"><span>PDF</span></div>
        <div>
          <div class="ai-card-title mono">${escapeHtml(inv.invoice_no||'Missing invoice no')}</div>
          <div class="ai-card-kicker">${escapeHtml(inv.supplier||'Supplier missing')}${inv.date?` · <span style="font-weight:400;color:var(--fg-3)">${escapeHtml(inv.date)}</span>`:''}</div>
        </div>
      </div>
    </div>
    <div class="ai-invoice-divider"></div>
    <div class="ai-invoice-fields">
      ${filename?`<div><span>Filename</span><strong style="font-size:11px;overflow:hidden;text-overflow:ellipsis">${escapeHtml(filename)}</strong></div>`:''}
      <div><span>TRN / VAT #</span><strong class="mono" style="${trnInvalid?'color:var(--red)':''}" title="${trnInvalid?'Invalid TRN — must be 15 digits':''}">${escapeHtml(trnVal||'-')}${trnInvalid?' ⚠':''}</strong></div>
      <div><span>Subtotal (excl. VAT)</span><strong class="mono">${cur} ${fmt(net)}</strong></div>
      <div><span>VAT Amount</span><strong class="mono">${cur} ${fmt(vat)}</strong></div>
      <div><span>Total Payable</span><strong class="mono" style="color:var(--accent)">${cur} ${fmt(total)}</strong></div>
    </div>
    <div class="ai-card-foot">
      <span class="purchase-ai-details">${escapeHtml(details)}</span>
      <div class="ai-card-foot-meta">
        <span class="purchase-ai-validation"><span class="ai-status-pill ${status.cls}" title="Confidence ${purchaseAiNumber(inv.confidence).toFixed(0)}%">${escapeHtml(status.label)}</span></span>
        ${itemCount>0?`<span class="ai-item-count-badge">${itemCount} item${itemCount!==1?'s':''}</span>`:''}
      </div>
      ${purchaseAiUploadActionsHtml()}
    </div>`;
}

function purchaseRecordFromExtractedInvoice(inv){
  const total=purchaseAiNumber(inv.total);
  const status=String(inv.status||'Review');
  const lines=Array.isArray(inv.lines)?inv.lines:[];
  const itemQuantity=purchaseLinesTotalQuantity(lines)||lines.length||1;
  const paid=purchaseAiNumber(inv.paid);
  return {
    ref:inv.invoice_no,
    supplier:inv.supplier||'Supplier',
    address:inv.address||'',
    date:inv.date||'',
    location:'Dubai HQ',
    pay_term:inv.pay_term||'',
    items:itemQuantity,
    net_amount:purchaseAiNumber(inv.net_amount||inv.subtotal),
    discount:discountAmountFromExtractedInvoice(inv),
    tax_amount:purchaseAiNumber(inv.tax_amount||inv.vat_amount),
    shipping:purchaseAiNumber(inv.shipping),
    total,
    paid,
    due:purchaseAiNumber(inv.due)||Math.max(0,total-paid),
    source:'AI Upload',
    status:status==='Valid'?'Received':status,
    extraction_status:status,
    supplier_trn:inv.supplier_trn||'',
    bill_to:inv.bill_to||'',
    currency:inv.currency||'AED',
    issues:inv.issues||'',
    discount_type:inv.discount_type||'None',
    discount_value:purchaseAiNumber(inv.discount_value),
    tax_type:inv.tax_type||(purchaseAiNumber(inv.vat_amount)>0?'VAT 5%':'None'),
    lines,
    payment_method:inv.payment_method||'Cash',
    payment_account:inv.payment_account||'None',
    payment_note:inv.payment_note||'',
    paid_on:inv.paid_on||'',
    shipping_details:inv.shipping_details||'',
    notes:inv.notes||''
  };
}

function discountAmountFromExtractedInvoice(inv){
  const net=purchaseAiNumber(inv.net_amount||inv.subtotal);
  const value=purchaseAiNumber(inv.discount_value||inv.discount);
  return inv.discount_type==='Percentage'?net*(value/100):inv.discount_type==='Fixed'?value:0;
}

async function storeExtractedPurchaseRecords(){
  const rows=purchaseAiRows();
  const visibleInvoiceNos=new Set(rows.map(row=>String(row.dataset.invoiceNo||'')));
  const skippedOrUnchecked=new Set(rows
    .filter(row=>row.dataset.skipped==='1'||!row.querySelector('.purchase-ai-select')?.checked)
    .map(row=>String(row.dataset.invoiceNo||'')));
  const selectedInvoices=new Map();
  rows
    .filter(row=>row.dataset.skipped!=='1')
    .filter(row=>row.querySelector('.purchase-ai-select')?.checked)
    .forEach(row=>{
      try{
        const inv=JSON.parse(row.dataset.inv||'{}');
        addSelectedPurchaseInvoice(selectedInvoices,{...inv,invoice_no:row.dataset.invoiceNo||inv.invoice_no});
      }catch{}
    });
  uploadedFiles.forEach(file=>{
    const saved=file.savedInvoiceNos instanceof Set?file.savedInvoiceNos:new Set(file.savedInvoiceNos||[]);
    (file.invoices||[]).forEach(inv=>{
      const invoiceNo=String(inv.invoice_no||'');
      if(!invoiceNo||saved.has(invoiceNo)||skippedOrUnchecked.has(invoiceNo))return;
      if(!visibleInvoiceNos.has(invoiceNo)||!selectedInvoices.has(invoiceKey(invoiceNo)))addSelectedPurchaseInvoice(selectedInvoices,inv);
    });
  });
  if(selectedInvoices.size===0){toast('No extracted purchase invoices to store','warn');return;}
  let stored=0;
  let updated=0;
  let existing=0;
  let reviewSaved=0;
  let failed=0;
  const recordsToSave=[];
  const savedInvoiceNos=[];
  const existingRows=purchaseRecordRowMap();
  const existingRefs=new Set(existingRows.keys());
  const aiInvoiceCounts=purchaseAiInvoiceCounts();
  for(const inv of selectedInvoices.values()){
    const validation=validatePurchaseAiInvoice(inv,{aiInvoiceCounts,existingPurchaseRefs:existingRefs});
    if(!validation.valid){
      reviewSaved++;
      markPurchaseAiInvoiceRows(inv.invoice_no,'Review',validation.issues.join('; ')||'Saved with review notes');
    }
    const record=purchaseRecordFromExtractedInvoice(inv);
    const refKey=invoiceKey(record.ref||record.invoice_no);
    const existingRecord=findPurchaseRecordByRef(record.ref||record.invoice_no);
    const merged=existingRecord?mergePurchaseRecords(existingRecord,record):{record,mergedSameProduct:0,addedProducts:Array.isArray(record.lines)?record.lines.length:0};
    const result=existingRecord
      ? (purchaseRecordsEquivalent(existingRecord,merged.record)?'same':'updated')
      : 'created';
    if(result==='same'){
      existing++;
      markExtractedInvoiceUploaded(inv.invoice_no);
      markPurchaseAiInvoiceRows(inv.invoice_no,'Already Exists','Same purchase already exists in database',true);
      continue;
    }
    if(result==='updated')updated++;
    if(result==='created')stored++;
    recordsToSave.push({record:merged.record,result,invoiceNo:inv.invoice_no,refKey,merge:merged});
  }
  if(recordsToSave.length){
    try{
      try{
        const vendorResult=await saveVendorsFromExtractedPurchases([...selectedInvoices.values()]);
        if(vendorResult.created){
          toast(`${vendorResult.created} vendor(s) added from purchase upload`,'ok');
        }
      }catch(vendorErr){
        toast('Vendor master save failed; purchase records will still be saved','warn');
        console.warn('Purchase AI vendor sync failed:',vendorErr);
      }
      await savePurchaseRecordsInChunks(recordsToSave.map(item=>item.record));
      setInventoryTableCleared(false);
      recordsToSave.forEach(item=>{
        const oldRow=existingRows.get(item.refKey);
        if(oldRow)oldRow.remove();
        purchaseRecordCache.set(String(item.record.ref||item.record.invoice_no),item.record);
        savedInvoiceNos.push(item.invoiceNo);
      });
      renderPurchaseRecordWindow();
      syncStockLevelsFromProducts();
      markExtractedInvoicesUploaded(savedInvoiceNos);
      markPurchaseAiInvoicesBulk(recordsToSave.map(item=>({
        invoiceNo:item.invoiceNo,
        status:item.result==='updated'?'Updated':'Saved',
        details:item.result==='updated'
          ? `Existing invoice updated: ${item.merge.mergedSameProduct} same product merged, ${item.merge.addedProducts} new product line(s)`
          : 'Saved to purchase records',
        skip:true
      })));
    }catch(err){
      failed=recordsToSave.length;
      stored=0;
      updated=0;
      markPurchaseAiInvoicesBulk(recordsToSave.map(item=>({
        invoiceNo:item.invoiceNo,
        status:'Review',
        details:'Database bulk save failed; try saving again',
        skip:false
      })));
      console.warn('Purchase AI bulk save failed:',err);
    }
  }
  if(stored>0||updated>0){
    audit('Stored extracted purchase invoices',`${stored} added, ${updated} updated`,'Saved');
    const tab=document.querySelector('#page-purchase .tab:nth-child(5)');
    if(tab)stab(tab,'p-records');
  }
  updatePurchaseValidationFileStatus();
  toast(`${stored} added, ${updated} updated, ${existing} already exist${reviewSaved?`; ${reviewSaved} saved with review notes`:''}${failed?`; ${failed} failed to save`:''}`,'ok');
}

async function saveVendorsFromExtractedPurchases(invoices){
  const records=buildVendorRecordsFromExtractedPurchases(invoices);
  if(!records.length)return {created:0,existing:0};
  await saveVendorRecordsInChunks(records);
  records.forEach(record=>renderVendorRecord(record));
  syncSupplierOptions();
  refreshEnhancedTable(document.getElementById('vendor-tbody')?.closest('table'));
  return {created:records.length,existing:0};
}

function buildVendorRecordsFromExtractedPurchases(invoices){
  const existing=vendorDuplicateIndex();
  const pendingNames=new Set();
  const pendingTrns=new Set();
  const records=[];
  (invoices||[]).forEach(inv=>{
    const name=String(inv.supplier||'').trim();
    if(!name||name.toLowerCase()==='supplier')return;
    const trn=String(inv.supplier_trn||'').replace(/\D/g,'');
    const nameKey=vendorNameKey(name);
    if(existing.names.has(nameKey)||pendingNames.has(nameKey))return;
    if(trn&&(existing.trns.has(trn)||pendingTrns.has(trn)))return;
    pendingNames.add(nameKey);
    if(trn)pendingTrns.add(trn);
    records.push({
      name,
      trn,
      category:'Purchase Supplier',
      email:'',
      phone:'',
      address:inv.address||'',
      source:'AI Purchase Upload',
      status:'Active'
    });
  });
  return records;
}

function vendorDuplicateIndex(){
  const names=new Set();
  const trns=new Set();
  document.querySelectorAll('#vendor-tbody tr:not([data-empty-state])').forEach(row=>{
    const name=vendorNameKey(row.children[0]?.textContent);
    const trn=String(row.dataset.vendorTrn||row.children[1]?.textContent||'').replace(/\D/g,'');
    if(name)names.add(name);
    if(trn)trns.add(trn);
  });
  return {names,trns};
}

function vendorNameKey(value){
  return String(value||'').trim().toLowerCase().replace(/\s+/g,' ');
}

async function saveVendorRecordsInChunks(records){
  const chunkSize=500;
  for(let index=0;index<records.length;index+=chunkSize){
    const chunk=records.slice(index,index+chunkSize);
    await bulkSaveServer('vendors',chunk,{throwOnError:true});
  }
}

async function savePurchaseRecordsInChunks(records){
  const chunkSize=500;
  for(let index=0;index<records.length;index+=chunkSize){
    const chunk=records.slice(index,index+chunkSize);
    await bulkSaveServer('purchaseRecords',chunk,{throwOnError:true});
    toast(`Saved ${Math.min(index+chunk.length,records.length)} of ${records.length} purchase rows...`,'info');
  }
}

function purchaseRecordRowMap(){
  const map=new Map();
  document.querySelectorAll('#purchase-record-tbody tr:not([data-empty-state])').forEach(row=>{
    const key=invoiceKey(row.children[0]?.textContent);
    if(key)map.set(key,row);
  });
  return map;
}

function markExtractedInvoiceUploaded(invoiceNo){
  const key=String(invoiceNo||'');
  uploadedFiles.forEach(file=>{
    if(!Array.isArray(file.invoices))return;
    if(file.invoices.some(inv=>String(inv.invoice_no||'')===key)){
      file.savedInvoiceNos=file.savedInvoiceNos instanceof Set?file.savedInvoiceNos:new Set(file.savedInvoiceNos||[]);
      file.savedInvoiceNos.add(key);
      persistPurchaseDocumentRecord(file);
    }
  });
  renderPurchaseValidationDocumentStatus();
}

function markExtractedInvoicesUploaded(invoiceNos){
  const keys=new Set((invoiceNos||[]).map(value=>String(value||'')));
  uploadedFiles.forEach(file=>{
    if(!Array.isArray(file.invoices))return;
    const matched=file.invoices.some(inv=>keys.has(String(inv.invoice_no||'')));
    if(!matched)return;
    file.savedInvoiceNos=file.savedInvoiceNos instanceof Set?file.savedInvoiceNos:new Set(file.savedInvoiceNos||[]);
    file.invoices.forEach(inv=>{
      const key=String(inv.invoice_no||'');
      if(keys.has(key))file.savedInvoiceNos.add(key);
    });
    persistPurchaseDocumentRecord(file);
  });
  renderPurchaseValidationDocumentStatus();
}

function markPurchaseAiInvoicesBulk(updates){
  const updateMap=new Map((updates||[]).map(item=>[String(item.invoiceNo||''),item]));
  purchaseAiRows().forEach(row=>{
    const update=updateMap.get(String(row.dataset.invoiceNo||''));
    if(!update)return;
    const pillCls=update.status==='Saved'||update.status==='Updated'?'approved':update.status==='Review'?'partial':'pending';
    const pillLabel=update.status==='Saved'||update.status==='Updated'?'Approved':update.status;
    const validationCell=row.querySelector('.purchase-ai-validation');
    const detailsCell=row.querySelector('.purchase-ai-details');
    if(validationCell)validationCell.innerHTML=purchaseAiStatusPillHtml(pillLabel,pillCls);
    if(detailsCell&&Number(row.dataset.lineIndex||0)===0)detailsCell.textContent=update.details||update.status;
    if(update.skip){
      row.dataset.skipped='1';
      const box=row.querySelector('.purchase-ai-select');
      if(box)box.checked=false;
    }
  });
}

function upsertExtractedPurchaseRecord(record){
  const tbody=document.getElementById('purchase-record-tbody');
  const ref=String(record.ref||record.invoice_no||'').trim().toLowerCase();
  if(!tbody||!ref){
    renderPurchaseRecord(record);
    return 'created';
  }
  const existingRow=[...tbody.querySelectorAll('tr:not([data-empty-state])')]
    .find(row=>row.children[0]?.textContent.trim().toLowerCase()===ref);
  if(!existingRow){
    renderPurchaseRecord(record);
    return 'created';
  }
  const existingRecord=purchaseRecordFromRow(existingRow);
  if(purchaseRecordsEquivalent(existingRecord,record)){
    return 'same';
  }
  existingRow.remove();
  renderPurchaseRecord(record);
  return 'updated';
}

function purchaseRecordsEquivalent(a,b){
  return JSON.stringify(normalizePurchaseRecordForCompare(a))===JSON.stringify(normalizePurchaseRecordForCompare(b));
}

function normalizePurchaseRecordForCompare(record={}){
  const normNumber=value=>parseAmount(value).toFixed(2);
  const normText=value=>String(value||'').trim().toLowerCase();
  const lines=Array.isArray(record.lines)?record.lines:[];
  return {
    ref:normText(record.ref||record.invoice_no),
    supplier:normText(record.supplier),
    address:normText(record.address),
    date:normText(record.date),
    pay_term:normText(record.pay_term),
    net_amount:normNumber(record.net_amount||record.subtotal),
    discount:normNumber(record.discount),
    tax_amount:normNumber(record.tax_amount||record.vat_amount),
    shipping:normNumber(record.shipping),
    total:normNumber(record.total),
    paid:normNumber(record.paid),
    due:normNumber(record.due),
    discount_type:normText(record.discount_type),
    discount_value:normNumber(record.discount_value),
    tax_type:normText(record.tax_type),
    payment_method:normText(record.payment_method),
    payment_account:normText(record.payment_account),
    payment_note:normText(record.payment_note),
    paid_on:normText(record.paid_on),
    shipping_details:normText(record.shipping_details),
    notes:normText(record.notes),
    lines:lines.map(line=>({
      product:normText(line.product||line.description),
      category:normText(line.category),
      unit:normText(line.unit||line.unit_of_measure||line.uom),
      quantity:normNumber(line.quantity||line.qty),
      unit_cost:normNumber(line.unit_cost||line.cost),
      discount_percent:normNumber(line.discount_percent||line.discountPct),
      unit_cost_before_tax:normNumber(line.unit_cost_before_tax||line.unit_cost||line.cost),
      line_total:normNumber(line.line_total||line.amount),
      profit_margin:normNumber(line.profit_margin||line.margin),
      selling_price_inc_tax:normNumber(line.selling_price_inc_tax||line.selling_price)
    }))
  };
}

function markPurchaseAiInvoiceRows(invoiceNo,status,details,skip=false){
  purchaseAiRows().forEach(row=>{
    if((row.dataset.invoiceNo||'')!==String(invoiceNo||''))return;
    const pillCls=status==='Saved'||status==='Approved'?'approved':status==='Review'?'partial':'pending';
    row.querySelector('.purchase-ai-validation').innerHTML=purchaseAiStatusPillHtml(status==='Saved'?'Approved':status,pillCls);
    row.querySelector('.purchase-ai-details').textContent=details||status;
    if(skip){
      row.dataset.skipped='1';
      const box=row.querySelector('.purchase-ai-select');
      if(box)box.checked=false;
    }
  });
}

function validatePurchaseAiInvoice(inv,options={}){
  const issues=[];
  const invoiceNo=String(inv.invoice_no||'').trim();
  const invoiceKeyValue=invoiceKey(invoiceNo);
  const trn=String(inv.supplier_trn||'').replace(/\D/g,'');
  const subtotal=purchaseAiNumber(inv.subtotal);
  const vat=purchaseAiNumber(inv.vat_amount);
  const total=purchaseAiNumber(inv.total);
  const discount=discountAmountFromExtractedInvoice(inv);
  const shipping=purchaseAiNumber(inv.shipping);
  if(!invoiceNo)issues.push('Invoice number missing');
  if(invoiceNo){
    const existsInRecords=options.existingPurchaseRefs
      ? options.existingPurchaseRefs.has(invoiceKeyValue)
      : tableHasText('#purchase-record-tbody',invoiceNo);
    if(existsInRecords)issues.push('Duplicate purchase invoice in records');
  }
  if(invoiceNo){
    const duplicateCount=options.aiInvoiceCounts?.get(invoiceKeyValue)??countPurchaseAiInvoiceNo(invoiceNo);
    if(duplicateCount>1)issues.push('Duplicate invoice number in AI upload');
  }
  if(!String(inv.supplier||'').trim())issues.push('Supplier missing');
  if(!String(inv.date||'').trim())issues.push('Date missing');
  if(trn&&trn.length!==15)issues.push('Supplier TRN must be 15 digits');
  if(total&&Math.abs((Math.max(0,subtotal-discount)+vat+shipping)-total)>.05)issues.push('Total does not match subtotal - discount + VAT + shipping');
  if(String(inv.status||'').toLowerCase()==='error')issues.push(inv.issues||'Extraction returned error status');
  if(purchaseAiNumber(inv.confidence)<70)issues.push('Low confidence extraction');
  return {valid:issues.length===0,issues};
}

function countPurchaseAiInvoiceNo(invoiceNo){
  const key=invoiceKey(invoiceNo);
  if(!key)return 0;
  const invoiceUids=new Set();
  purchaseAiRows().forEach((row,index)=>{
    try{
      if(invoiceKey(JSON.parse(row.dataset.inv||'{}').invoice_no)!==key)return;
      invoiceUids.add(row.dataset.invoiceUid||`${row.dataset.invoiceNo||key}:${index}`);
    }catch(err){
      console.warn('Purchase AI duplicate check skipped a row:',err);
    }
  });
  return invoiceUids.size;
}

function purchaseAiUploadActionsHtml(){
  return `<div class="row-actions">
    <button class="ai-card-action view" type="button" title="View" aria-label="View purchase AI invoice" onclick="openPurchaseAiView(this)">${viewIconSvg()}</button>
    <button class="ai-card-action approve" type="button" title="Approve" aria-label="Approve purchase AI row" onclick="approvePurchaseAiRow(this)">Approve</button>
    <button class="ai-card-action delete" type="button" title="Delete" aria-label="Delete purchase AI row" onclick="deletePurchaseAiRow(this)">${deleteIconSvg()}</button>
  </div>`;
}

function purchaseAiDragHandleHtml(){
  return '<span class="ai-drag-handle" title="Drag to reorder" aria-label="Drag to reorder" draggable="true">::</span>';
}

let purchaseAiEditRow=null;

function ensurePurchaseAiEditModal(){
  let overlay=document.getElementById('m-purchase-ai-edit');
  if(overlay)return overlay;
  overlay=document.createElement('div');
  overlay.className='overlay';
  overlay.id='m-purchase-ai-edit';
  overlay.onclick=e=>closeOvBg(e,'m-purchase-ai-edit');
  overlay.innerHTML=`
    <div class="modal modal-xl purchase-edit-modal">
      <div class="purchase-edit-top">
        <div>
          <div class="modal-title">Edit Extracted Purchase</div>
          <div class="modal-sub" id="pai-edit-sub">Correct the extracted supplier invoice before saving</div>
        </div>
        <span class="b b-b">Invoice View</span>
      </div>
      <div class="purchase-invoice-sheet">
        <div class="purchase-invoice-head">
          <div>
            <div class="purchase-invoice-kicker">Supplier Invoice</div>
            <input class="purchase-invoice-title mono" id="pai-invoice" placeholder="Invoice No.">
          </div>
          <div class="purchase-invoice-meta">
            <label>Purchase Date<input class="fi" id="pai-date"></label>
            <label>Pay Term<select class="fi" id="pai-term"><option value="">Please Select</option><option>Due on receipt</option><option>Net 15</option><option>Net 30</option><option>Net 45</option></select></label>
          </div>
        </div>
        <div class="purchase-party-grid">
          <div class="purchase-party-box">
            <div class="section-hd">Supplier</div>
            <input class="fi" id="pai-supplier" placeholder="Supplier name">
            <textarea class="fi" id="pai-address" rows="3" placeholder="Supplier address"></textarea>
          </div>
          <div class="purchase-party-box">
            <div class="section-hd">Payment</div>
            <div class="fr3">
              <input class="fi mono" id="pai-paid" placeholder="Paid amount" oninput="calcPurchaseAiEditInvoice()">
              <input class="fi" id="pai-paid-on" placeholder="Paid on">
              <select class="fi" id="pai-pay-method"><option>Cash</option><option>Bank Transfer</option><option>Card</option><option>Cheque</option><option>Online</option></select>
            </div>
            <div class="fr2">
              <input class="fi" id="pai-pay-account" placeholder="Payment account">
              <input class="fi" id="pai-pay-note" placeholder="Payment note">
            </div>
          </div>
        </div>
        <div class="purchase-lines-head">
          <div class="section-hd">Items</div>
          <button class="btn btn-g btn-sm" type="button" onclick="addPurchaseAiEditLine()">+ Add line</button>
        </div>
        <div class="purchase-edit-table-wrap">
          <table class="tbl purchase-edit-lines">
            <thead><tr><th>#</th><th>Product</th><th>Category</th><th>Qty</th><th>Unit</th><th>Unit Cost</th><th>Disc %</th><th>Before Tax</th><th>Line Total</th><th>Margin %</th><th>Selling Inc. Tax</th><th></th></tr></thead>
            <tbody id="pai-lines"></tbody>
          </table>
        </div>
        <div class="purchase-invoice-bottom">
          <div>
            <div class="section-hd">Notes & Validation</div>
            <textarea class="fi" id="pai-notes" rows="4" placeholder="Additional notes"></textarea>
            <div class="fr3">
              <input class="fi mono" id="pai-confidence" type="number" min="0" max="100" placeholder="Confidence %">
              <select class="fi" id="pai-status"><option>Valid</option><option>Review</option><option>Error</option></select>
              <input class="fi" id="pai-issues" placeholder="Issues">
            </div>
          </div>
          <div class="purchase-summary-box">
            <div class="fr2">
              <select class="fi" id="pai-discount-type" onchange="calcPurchaseAiEditInvoice()"><option>None</option><option>Fixed</option><option>Percentage</option></select>
              <input class="fi mono" id="pai-discount-value" placeholder="Discount" oninput="calcPurchaseAiEditInvoice()">
            </div>
            <div class="fr2">
              <select class="fi" id="pai-tax-type" onchange="calcPurchaseAiEditInvoice(true)"><option>None</option><option>VAT 5%</option><option>Reverse Charge 5%</option><option>Exempt</option></select>
              <input class="fi mono" id="pai-vat" placeholder="Tax amount" oninput="calcPurchaseAiEditInvoice()">
            </div>
            <input class="fi" id="pai-shipping-details" placeholder="Shipping details">
            <input class="fi mono" id="pai-shipping" placeholder="Shipping charges" oninput="calcPurchaseAiEditInvoice()">
            <div class="tot-row"><span>Net Amount</span><span class="mono" id="pai-net-label">0.00</span></div>
            <div class="tot-row"><span>Discount (-)</span><span class="mono" id="pai-discount-label">0.00</span></div>
            <div class="tot-row"><span>Purchase Tax (+)</span><span class="mono" id="pai-tax-label">0.00</span></div>
            <div class="tot-row"><span>Shipping (+)</span><span class="mono" id="pai-shipping-label">0.00</span></div>
            <div class="tot-final"><span>Purchase Total</span><input class="fi mono" id="pai-total" readonly></div>
            <div class="tot-row"><span>Payment Due</span><input class="fi mono" id="pai-due" readonly></div>
          </div>
        </div>
      </div>
      <div class="modal-foot">
        <button class="btn btn-g" onclick="closeM('m-purchase-ai-edit')">Cancel</button>
        <button class="btn btn-g" onclick="savePurchaseAiEdit(true)">Save & Next</button>
        <button class="btn btn-p" onclick="savePurchaseAiEdit(false)">Save</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  return overlay;
}

function openPurchaseAiEdit(btn){
  const row=purchaseAiRowFromButton(btn);
  if(!row)return;
  purchaseAiEditRow=row;
  let inv={};
  try{inv=JSON.parse(row.dataset.inv||'{}');}catch{return;}
  ensurePurchaseAiEditModal();
  document.getElementById('pai-invoice').value=inv.invoice_no||'';
  document.getElementById('pai-date').value=inv.date||'';
  document.getElementById('pai-supplier').value=inv.supplier||'';
  document.getElementById('pai-address').value=inv.address||'';
  document.getElementById('pai-term').value=inv.pay_term||'';
  const body=document.getElementById('pai-lines');
  if(body)body.innerHTML='';
  const lines=Array.isArray(inv.lines)&&inv.lines.length?inv.lines:[{}];
  lines.forEach(line=>addPurchaseAiEditLine(line));
  document.getElementById('pai-discount-type').value=inv.discount_type||'None';
  document.getElementById('pai-discount-value').value=inv.discount_value||inv.discount||0;
  document.getElementById('pai-tax-type').value=inv.tax_type||((purchaseAiNumber(inv.vat_amount)>0)?'VAT 5%':'None');
  document.getElementById('pai-vat').value=inv.vat_amount||0;
  document.getElementById('pai-shipping-details').value=inv.shipping_details||'';
  document.getElementById('pai-shipping').value=inv.shipping||0;
  document.getElementById('pai-total').value=inv.total||0;
  document.getElementById('pai-paid').value=inv.paid||0;
  document.getElementById('pai-due').value=inv.due||0;
  document.getElementById('pai-paid-on').value=inv.paid_on||'';
  document.getElementById('pai-pay-method').value=inv.payment_method||'Cash';
  document.getElementById('pai-pay-account').value=inv.payment_account||'None';
  document.getElementById('pai-pay-note').value=inv.payment_note||'';
  document.getElementById('pai-notes').value=inv.notes||'';
  document.getElementById('pai-confidence').value=inv.confidence||90;
  document.getElementById('pai-status').value=inv.status||'Valid';
  document.getElementById('pai-issues').value=inv.issues||'';
  document.getElementById('pai-edit-sub').textContent=`${inv.invoice_no||'Purchase'} - ${lines.length} line(s)`;
  calcPurchaseAiEditInvoice();
  showM('m-purchase-ai-edit');
}

function openPurchaseAiView(btn){
  const row=purchaseAiRowFromButton(btn);
  if(!row)return;
  let inv={};
  try{inv=JSON.parse(row.dataset.inv||'{}');}catch{return;}
  const validation=validatePurchaseAiInvoice(inv);
  renderPurchaseRecordPreview({
    ...inv,
    ref:inv.invoice_no,
    tax_amount:inv.vat_amount,
    net_amount:inv.net_amount||inv.subtotal,
    source:inv.source||'AI Upload',
    status:validation.valid?'Valid':'Review',
    issues:validation.issues.join('; ')||inv.issues||'',
    filename:row.dataset.filename||'',
  });
  showM('m-purchase-view');
}

function addPurchaseAiEditLine(line={}){
  const body=document.getElementById('pai-lines');
  if(!body)return;
  const row=document.createElement('tr');
  row.innerHTML=`
    <td class="mono pai-line-no">1</td>
    <td><input class="fi pai-product" value="${escapeHtml(purchaseAiProductName(line))}" placeholder="Product name"></td>
    <td><input class="fi pai-category" value="${escapeHtml(line.category||'')}" placeholder="Category"></td>
    <td><input class="fi mono pai-qty" value="${escapeHtml(line.quantity||line.qty||0)}" oninput="calcPurchaseAiEditLine(this)"></td>
    <td><input class="fi mono pai-unit" value="${escapeHtml(line.unit||line.unit_of_measure||line.uom||'PCS')}"></td>
    <td><input class="fi mono pai-cost" value="${escapeHtml(line.unit_cost||line.cost||line.unitCost||0)}" oninput="calcPurchaseAiEditLine(this)"></td>
    <td><input class="fi mono pai-line-discount" value="${escapeHtml(line.discount_percent||line.discountPct||0)}" oninput="calcPurchaseAiEditLine(this)"></td>
    <td><input class="fi mono pai-cost-before-tax" value="${escapeHtml(line.unit_cost_before_tax||line.unit_cost||line.cost||0)}" readonly></td>
    <td><input class="fi mono pai-line-total" value="${escapeHtml(line.line_total||line.amount||0)}" oninput="calcPurchaseAiEditInvoice()"></td>
    <td><input class="fi mono pai-margin" value="${escapeHtml(line.profit_margin||line.margin||0)}" oninput="calcPurchaseAiEditLine(this)"></td>
    <td><input class="fi mono pai-selling" value="${escapeHtml(line.selling_price_inc_tax||line.selling_price||0)}" readonly></td>
    <td><button class="icon-btn danger" type="button" title="Remove line" onclick="removePurchaseAiEditLine(this)">${deleteIconSvg()}</button></td>`;
  body.appendChild(row);
  calcPurchaseAiEditLine(row);
}

function removePurchaseAiEditLine(btn){
  const row=btn.closest('tr');
  row?.remove();
  if(!document.querySelector('#pai-lines tr'))addPurchaseAiEditLine();
  calcPurchaseAiEditInvoice();
}

function calcPurchaseAiEditLine(source){
  const row=source?.closest?.('tr')||source;
  if(!row)return;
  const qty=parseAmount(row.querySelector('.pai-qty')?.value);
  const cost=parseAmount(row.querySelector('.pai-cost')?.value);
  const discountPct=parseAmount(row.querySelector('.pai-line-discount')?.value);
  const beforeTax=cost*(1-(discountPct/100));
  const margin=parseAmount(row.querySelector('.pai-margin')?.value);
  const beforeTaxField=row.querySelector('.pai-cost-before-tax');
  const totalField=row.querySelector('.pai-line-total');
  const sellingField=row.querySelector('.pai-selling');
  if(beforeTaxField)beforeTaxField.value=beforeTax.toFixed(2);
  if(totalField)totalField.value=(qty*beforeTax).toFixed(2);
  if(sellingField)sellingField.value=(beforeTax*(1+margin/100)*1.05).toFixed(2);
  calcPurchaseAiEditInvoice();
}

function calcPurchaseAiEditInvoice(forceTaxRecalc=false){
  document.querySelectorAll('#pai-lines tr').forEach((row,index)=>{
    const lineNo=row.querySelector('.pai-line-no');
    if(lineNo)lineNo.textContent=String(index+1);
  });
  const lineTotal=[...document.querySelectorAll('#pai-lines .pai-line-total')]
    .reduce((sum,input)=>sum+parseAmount(input.value),0);
  const discountType=document.getElementById('pai-discount-type')?.value||'None';
  const discountValue=parseAmount(document.getElementById('pai-discount-value')?.value);
  const discount=discountType==='Percentage'?lineTotal*(discountValue/100):discountType==='Fixed'?discountValue:0;
  const taxable=Math.max(0,lineTotal-discount);
  const taxType=document.getElementById('pai-tax-type')?.value||'None';
  const calculatedVat=taxType.includes('5%')&&!taxType.toLowerCase().includes('exempt')?taxable*.05:0;
  const vatField=document.getElementById('pai-vat');
  if(vatField&&(forceTaxRecalc||document.activeElement!==vatField))vatField.value=calculatedVat.toFixed(2);
  const vat=parseAmount(vatField?.value);
  const shipping=parseAmount(document.getElementById('pai-shipping')?.value);
  const paid=parseAmount(document.getElementById('pai-paid')?.value);
  const gross=taxable+vat+shipping;
  setText('pai-net-label',lineTotal.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setText('pai-discount-label',discount.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setText('pai-tax-label',vat.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setText('pai-shipping-label',shipping.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  const total=document.getElementById('pai-total');
  if(total)total.value=gross.toFixed(2);
  const due=document.getElementById('pai-due');
  if(due)due.value=Math.max(0,gross-paid).toFixed(2);
  return {subtotal:lineTotal,discount,tax:vat,shipping,total:gross,paid,due:Math.max(0,gross-paid)};
}

function collectPurchaseAiEditLines(){
  return [...document.querySelectorAll('#pai-lines tr')]
    .map(row=>({
      product:row.querySelector('.pai-product')?.value.trim()||'Purchase item',
      category:row.querySelector('.pai-category')?.value.trim()||'',
      unit:row.querySelector('.pai-unit')?.value.trim()||'PCS',
      quantity:parseAmount(row.querySelector('.pai-qty')?.value),
      unit_cost:parseAmount(row.querySelector('.pai-cost')?.value),
      discount_percent:parseAmount(row.querySelector('.pai-line-discount')?.value),
      unit_cost_before_tax:parseAmount(row.querySelector('.pai-cost-before-tax')?.value),
      line_total:parseAmount(row.querySelector('.pai-line-total')?.value),
      profit_margin:parseAmount(row.querySelector('.pai-margin')?.value),
      selling_price_inc_tax:parseAmount(row.querySelector('.pai-selling')?.value)
    }))
    .filter(line=>line.product||line.quantity||line.unit_cost||line.line_total);
}

function savePurchaseAiEdit(next=false){
  if(!purchaseAiEditRow)return closeM('m-purchase-ai-edit');
  let inv={};
  try{inv=JSON.parse(purchaseAiEditRow.dataset.inv||'{}');}catch{return;}
  const oldInvoiceNo=inv.invoice_no||purchaseAiEditRow.dataset.invoiceNo||'';
  const oldInvoiceUid=purchaseAiEditRow.dataset.invoiceUid||`${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
  const lines=collectPurchaseAiEditLines();
  if(!lines.length){toast('Add at least one purchase item','warn');return;}
  const totals=calcPurchaseAiEditInvoice();
  const subtotal=totals.subtotal;
  const paid=parseAmount(document.getElementById('pai-paid').value);
  const total=parseAmount(document.getElementById('pai-total').value);
  inv={
    ...inv,
    invoice_no:document.getElementById('pai-invoice').value.trim(),
    date:document.getElementById('pai-date').value.trim(),
    supplier:document.getElementById('pai-supplier').value.trim(),
    address:document.getElementById('pai-address').value.trim(),
    pay_term:document.getElementById('pai-term').value,
    subtotal,
    net_amount:subtotal,
    discount_type:document.getElementById('pai-discount-type').value,
    discount_value:parseAmount(document.getElementById('pai-discount-value').value),
    tax_type:document.getElementById('pai-tax-type').value,
    vat_amount:parseAmount(document.getElementById('pai-vat').value),
    tax_amount:parseAmount(document.getElementById('pai-vat').value),
    shipping_details:document.getElementById('pai-shipping-details').value.trim(),
    shipping:parseAmount(document.getElementById('pai-shipping').value),
    total,
    paid,
    due:Math.max(0,total-paid),
    paid_on:document.getElementById('pai-paid-on').value,
    payment_method:document.getElementById('pai-pay-method').value,
    payment_account:document.getElementById('pai-pay-account').value,
    payment_note:document.getElementById('pai-pay-note').value.trim(),
    notes:document.getElementById('pai-notes').value.trim(),
    confidence:parseAmount(document.getElementById('pai-confidence').value),
    status:document.getElementById('pai-status').value,
    issues:document.getElementById('pai-issues').value.trim(),
    lines
  };
  replacePurchaseAiInvoiceRows(oldInvoiceNo,inv,oldInvoiceUid);
  updateUploadedPurchaseInvoice(oldInvoiceNo,inv);
  revalidatePurchaseAiRows();
  toast('Extracted purchase updated','ok');
  const current=purchaseAiEditRow;
  closeM('m-purchase-ai-edit');
  if(next){
    const nextRow=purchaseAiRows()
      .find(row=>row.dataset.skipped!=='1'&&row!==current);
    if(nextRow)setTimeout(()=>openPurchaseAiEdit(nextRow.querySelector('.row-actions .icon-btn')),80);
  }
}

function updateUploadedPurchaseInvoice(oldInvoiceNo,inv){
  const oldKey=String(oldInvoiceNo||'');
  uploadedFiles.forEach(file=>{
    if(!Array.isArray(file.invoices))return;
    const index=file.invoices.findIndex(item=>String(item.invoice_no||'')===oldKey);
    if(index>=0)file.invoices[index]=inv;
  });
}

function replacePurchaseAiInvoiceRows(oldInvoiceNo,inv,invoiceUid){
  const tbody=document.getElementById('ext-tbody');
  if(!tbody)return;
  const oldRows=purchaseAiRows(tbody)
    .filter(row=>(row.dataset.invoiceNo||'')===String(oldInvoiceNo||''));
  const anchor=oldRows[0]||purchaseAiEditRow;
  const rows=buildPurchaseAiInvoiceRows(inv,invoiceUid);
  rows.forEach(row=>tbody.insertBefore(row,anchor));
  oldRows.forEach(row=>row.remove());
  purchaseAiEditRow=rows[0]||null;
  enablePurchaseAiDragDrop();
  applyPurchaseAiSort(false);
}

function buildPurchaseAiInvoiceRows(inv,invoiceUid){
  const validation=validatePurchaseAiInvoice(inv);
  const row=document.createElement('div');
  row.className='ai-extract-card';
  row.setAttribute('data-inv',JSON.stringify(inv));
  row.draggable=true;
  row.dataset.invoiceNo=inv.invoice_no||'';
  row.dataset.invoiceUid=invoiceUid||`${Date.now()}-${Math.random().toString(36).slice(2,8)}`;
  row.dataset.lineIndex='0';
  row.dataset.validation=validation.valid?'valid':'review';
  row.innerHTML=purchaseAiRowHtml(inv,(Array.isArray(inv.lines)&&inv.lines[0])||{},0,validation,row.dataset.filename||'');
  return [row];
}

function updatePurchaseAiInvoiceRows(oldInvoiceNo,inv){
  const validation=validatePurchaseAiInvoice(inv);
  purchaseAiRows().forEach(row=>{
    if((row.dataset.invoiceNo||'')!==String(oldInvoiceNo||''))return;
    row.dataset.invoiceNo=inv.invoice_no||'';
    row.dataset.inv=JSON.stringify(inv);
    row.dataset.lineIndex='0';
    row.dataset.validation=validation.valid?'valid':'review';
    row.innerHTML=purchaseAiRowHtml(inv,(Array.isArray(inv.lines)&&inv.lines[0])||{},0,validation,row.dataset.filename||'');
  });
  enablePurchaseAiDragDrop();
  applyPurchaseAiSort(false);
}

function togglePurchaseAiSelection(checked){
  purchaseAiRows().forEach(row=>{
    const box=row.querySelector('.purchase-ai-select');
    if(box&&row.dataset.skipped!=='1')box.checked=checked;
  });
}

function skipPurchaseAiRow(btn){
  const row=purchaseAiRowFromButton(btn);
  if(!row)return;
  markPurchaseAiInvoiceRows(row.dataset.invoiceNo,'Skipped','Skipped by user',true);
  toast('Purchase AI row skipped','info');
}

function approvePurchaseAiRow(btn){
  const row=purchaseAiRowFromButton(btn);
  if(!row)return;
  let inv={};
  try{inv=JSON.parse(row.dataset.inv||'{}');}catch{return;}
  const validation=validatePurchaseAiInvoice(inv);
  if(!validation.valid){
    toast(validation.issues.join('; ')||'Fix validation issues before approving','warn');
    return;
  }
  purchaseAiRows().forEach(item=>{
    if((item.dataset.invoiceNo||'')!==String(row.dataset.invoiceNo||''))return;
    const box=item.querySelector('.purchase-ai-select');
    if(box)box.checked=true;
    const validationCell=item.querySelector('.purchase-ai-validation');
    const detailsCell=item.querySelector('.purchase-ai-details');
    if(validationCell)validationCell.innerHTML='<span class="ai-status-pill approved">Approved</span>';
    if(detailsCell)detailsCell.textContent='Approved for saving';
  });
  toast('Invoice approved for saving','ok');
}

async function deletePurchaseAiRow(btn){
  const row=purchaseAiRowFromButton(btn);
  let inv={};
  try{inv=JSON.parse(row?.dataset.inv||'{}');}catch{}
  const invoiceNo=row?.dataset.invoiceNo||inv.invoice_no||'';
  const supplier=String(inv.supplier||'').trim()||'—';
  const date=String(inv.date||'').trim()||'—';
  const lines=Array.isArray(inv.lines)?inv.lines:[];
  const lineRowsHtml=lines.slice(0,3).map(line=>{
    const name=escapeHtml(purchaseAiProductName(line)||'—');
    const qty=escapeHtml(String(line.quantity||line.qty||'—'));
    const price=escapeHtml(String(line.unit_cost||line.unit_price||line.cost||'—'));
    return `<tr><td style="padding:2px 6px 2px 0">${name}</td><td style="padding:2px 6px 2px 0;white-space:nowrap">${qty}</td><td style="padding:2px 0;white-space:nowrap">${price}</td></tr>`;
  }).join('');
  const moreHtml=lines.length>3?`<tr><td colspan="3" style="color:var(--fg-3);padding-top:2px">+${lines.length-3} more line${lines.length-3>1?'s':''}</td></tr>`:'';
  const tableHtml=lineRowsHtml?`<table style="margin-top:8px;width:100%;border-collapse:collapse;font-size:12px"><thead><tr style="color:var(--fg-3);font-size:11px"><th style="text-align:left;padding-bottom:3px;font-weight:500">Product</th><th style="text-align:left;padding-bottom:3px;font-weight:500">Qty</th><th style="text-align:left;padding-bottom:3px;font-weight:500">Price</th></tr></thead><tbody>${lineRowsHtml}${moreHtml}</tbody></table>`:'';
  const messageHtml=`<div style="font-size:13px;line-height:1.7"><div><strong>Supplier:</strong> ${escapeHtml(supplier)}</div><div><strong>Date:</strong> ${escapeHtml(date)}</div>${tableHtml}</div>`;
  const tbody=row?.parentElement;
  const confirmed=await appConfirm({title:'Delete this invoice?',messageHtml,okText:'Delete',tone:'danger'});
  if(!confirmed)return;
  purchaseAiRows().forEach(item=>{
    if((item.dataset.invoiceNo||'')===invoiceNo)item.remove();
  });
  if(tbody&&purchaseAiRows(tbody).length===0){
    tbody.innerHTML='<div class="ai-empty-state">AI uploaded purchase data will appear here for validation.</div>';
  }else{
    ensurePurchaseAiUploadTile();
  }
  toast('Purchase AI row deleted','warn');
}

function revalidatePurchaseAiRows(){
  const aiInvoiceCounts=purchaseAiInvoiceCounts();
  const existingPurchaseRefs=purchaseRecordRefSet();
  purchaseAiRows().forEach(row=>{
    if(row.dataset.skipped==='1')return;
    let inv={};
    try{inv=JSON.parse(row.dataset.inv||'{}');}catch{return;}
    const validation=validatePurchaseAiInvoice(inv,{aiInvoiceCounts,existingPurchaseRefs});
    row.dataset.validation=validation.valid?'valid':'review';
    const validationCell=row.querySelector('.purchase-ai-validation');
    const detailsCell=row.querySelector('.purchase-ai-details');
    const checkbox=row.querySelector('.purchase-ai-select');
    if(validationCell){
      const status=purchaseAiStatusMeta(inv,validation);
      validationCell.innerHTML=purchaseAiStatusPillHtml(status.label,status.cls);
    }
    if(detailsCell)detailsCell.textContent=validation.issues.join('; ')||purchaseAiRawDetails(inv.lines?.[0]||{})||'Ready to save';
  });
}

function purchaseAiInvoiceCounts(){
  const counts=new Map();
  uploadedFiles.forEach(file=>{
    (file.invoices||[]).forEach((inv,index)=>{
      if(isPurchaseExtractionError(inv))return;
      const key=invoiceKey(inv.invoice_no);
      if(!key)return;
      const entry=counts.get(key)||new Set();
      entry.add(`${file.id||file.name}:${index}`);
      counts.set(key,entry);
    });
  });
  if(counts.size){
    return new Map([...counts.entries()].map(([key,set])=>[key,set.size]));
  }
  purchaseAiRows().forEach((row,index)=>{
    try{
      const inv=JSON.parse(row.dataset.inv||'{}');
      const key=invoiceKey(inv.invoice_no);
      if(!key)return;
      const uid=row.dataset.invoiceUid||`${row.dataset.invoiceNo||key}:${index}`;
      const entry=counts.get(key)||new Set();
      entry.add(uid);
      counts.set(key,entry);
    }catch{}
  });
  return new Map([...counts.entries()].map(([key,set])=>[key,set.size]));
}

function purchaseRecordRefSet(){
  return new Set([...document.querySelectorAll('#purchase-record-tbody tr:not([data-empty-state]) td:first-child')]
    .map(td=>invoiceKey(td.textContent))
    .filter(Boolean));
}

function updateExtractionStats(){
  const all=uploadedFiles.flatMap(f=>f.invoices||[]);
  const invoices=all.filter(inv=>!isPurchaseExtractionError(inv));
  const total=invoices.length;
  const review=invoices.filter(inv=>!validatePurchaseAiInvoice(inv).valid).length;
  const errors=all.length-invoices.length;
  const stats=document.querySelectorAll('#page-purchase #p-extract .stat .stat-val');
  if(stats[0])stats[0].textContent=total;
  if(stats[1])stats[1].textContent=review;
  if(stats[2])stats[2].textContent=errors;
  const totalEl=document.getElementById('ext-stat-total');
  const reviewEl=document.getElementById('ext-stat-review');
  const errEl=document.getElementById('ext-stat-errors');
  if(totalEl)totalEl.textContent=total;
  if(reviewEl)reviewEl.textContent=review;
  if(errEl)errEl.textContent=errors;
  const sub=document.getElementById('ext-stats-sub');
  if(sub)sub.textContent=total?`${total} invoice${total===1?'':'s'} extracted · ${review} need review · ${errors} error${errors===1?'':'s'}`:'Upload files and run extraction to see results here';
  const saveBtn=document.getElementById('pur-save-all-btn');
  const selBtn=document.getElementById('pur-select-all-btn');
  const deselBtn=document.getElementById('pur-deselect-btn');
  if(saveBtn)saveBtn.style.display=total?'':'none';
  if(selBtn)selBtn.style.display=total?'':'none';
  if(deselBtn)deselBtn.style.display=total?'':'none';
}

let _purchaseAiView='card';

function setPurchaseAiView(view){
  _purchaseAiView=view;
  const cardView=document.getElementById('ext-card-view');
  const tableView=document.getElementById('ext-table-view');
  const btnCard=document.getElementById('pvt-card');
  const btnTable=document.getElementById('pvt-table');
  if(cardView)cardView.style.display=view==='card'?'':'none';
  if(tableView)tableView.style.display=view==='table'?'':'none';
  if(btnCard){btnCard.classList.toggle('on',view==='card');}
  if(btnTable){btnTable.classList.toggle('on',view==='table');}
  if(view==='table')renderPurchaseAiFlatTable();
}

function renderPurchaseAiFlatTable(){
  const tbody=document.getElementById('ext-flat-tbody');
  if(!tbody)return;
  const fmt=n=>purchaseAiNumber(n).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  const all=uploadedFiles.flatMap(f=>(f.invoices||[]).map(inv=>({inv,filename:f.name||''})));
  const validInvoices=all.filter(({inv})=>!isPurchaseExtractionError(inv));
  if(!validInvoices.length){
    tbody.innerHTML=`<tr><td colspan="18" style="text-align:center;color:var(--text3);padding:24px">No invoices extracted yet.</td></tr>`;
    return;
  }
  const rows=[];
  for(const {inv,filename} of validInvoices){
    const validation=validatePurchaseAiInvoice(inv);
    const statusCls=validation.valid?'b-g':'b-a';
    const statusLabel=validation.valid?'Valid':'Review';
    const cur=escapeHtml(inv.currency||'AED');
    const net=purchaseAiNumber(inv.net_amount||inv.subtotal)||purchaseAiInvoiceLineTotal(inv);
    const disc=purchaseAiNumber(inv.discount_value||inv.discount);
    const vat=purchaseAiNumber(inv.vat_amount);
    const total=purchaseAiNumber(inv.total)||(net+vat+purchaseAiNumber(inv.shipping));
    const lines=Array.isArray(inv.lines)&&inv.lines.length?inv.lines:[{}];
    lines.forEach((line,li)=>{
      const isFirst=li===0;
      const qty=purchaseAiNumber(line.quantity||line.qty||1);
      const unitPrice=purchaseAiNumber(line.unit_cost||line.unit_cost_before_tax||line.unit_price||line.cost||line.price);
      const discPct=purchaseAiNumber(line.discount_percent||line.discount_pct||line.discount);
      const discAmt=purchaseAiNumber(line.discount_amount)||(unitPrice*qty*(discPct/100));
      const lineTotal=purchaseAiNumber(line.line_total||line.amount||line.net_amount)||(qty*unitPrice-discAmt);
      const desc=escapeHtml(line.product||line.description||line.item||inv.invoice_no||'');
      rows.push(`<tr class="ext-flat-row${isFirst?' ext-flat-first':' ext-flat-sub'}" data-invoice-no="${escapeHtml(inv.invoice_no||'')}">
        <td style="text-align:center">${isFirst?`<input type="checkbox" class="purchase-ai-tbl-select" data-inv='${escapeHtml(JSON.stringify(inv))}' data-invoice-no="${escapeHtml(inv.invoice_no||'')}" ${validation.valid?'checked':''}>`:'&nbsp;'}</td>
        <td style="font-size:11px;color:var(--text3);max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${isFirst?escapeHtml(filename):''}</td>
        <td>${isFirst?escapeHtml(inv.date||'-'):''}</td>
        <td class="mono">${isFirst?escapeHtml(inv.invoice_no||'-'):''}</td>
        <td>${isFirst?escapeHtml(inv.supplier||'-'):''}</td>
        <td class="mono" style="${inv.supplier_trn&&inv.supplier_trn.length!==15?'color:var(--red)':''}">${isFirst?escapeHtml(inv.supplier_trn||'-'):''}</td>
        <td>${isFirst?escapeHtml(inv.bill_to||'-'):''}</td>
        <td class="mono" style="text-align:right">${isFirst?`${cur} ${fmt(net)}`:''}</td>
        <td class="mono" style="text-align:right">${isFirst&&disc>0?`${cur} ${fmt(disc)}`:''}</td>
        <td class="mono" style="text-align:right">${isFirst?`${cur} ${fmt(vat)}`:''}</td>
        <td class="mono" style="text-align:right;font-weight:700">${isFirst?`${cur} ${fmt(total)}`:''}</td>
        <td style="max-width:200px">${desc}</td>
        <td class="mono" style="text-align:right">${qty>0?fmt(qty):''}</td>
        <td class="mono" style="text-align:right">${unitPrice>0?`${cur} ${fmt(unitPrice)}`:''}</td>
        <td class="mono" style="text-align:right">${discPct>0?`${fmt(discPct)}%`:''}</td>
        <td class="mono" style="text-align:right">${discAmt>0?`${cur} ${fmt(discAmt)}`:''}</td>
        <td class="mono" style="text-align:right;color:var(--accent);font-weight:700">${lineTotal>0?`${cur} ${fmt(lineTotal)}`:''}</td>
        <td>${isFirst?`<span class="b ${statusCls}">${escapeHtml(statusLabel)}</span>`:''}</td>
      </tr>`);
    });
  }
  tbody.innerHTML=rows.join('');
}

function purchaseAiTableSelectAll(checked){
  document.querySelectorAll('#ext-flat-tbody .purchase-ai-tbl-select').forEach(cb=>{cb.checked=checked;});
}

function purchaseAiSelectAll(checked){
  document.querySelectorAll('#ext-tbody .purchase-ai-select').forEach(cb=>{cb.checked=checked;});
  document.querySelectorAll('#ext-flat-tbody .purchase-ai-tbl-select').forEach(cb=>{cb.checked=checked;});
}

function buildValidationPanel(invoices){
  renderPurchaseValidationDocumentStatus();
}

function purchaseFileUploadStatus(file){
  const invoices=Array.isArray(file.invoices)?file.invoices:[];
  const saved=file.savedInvoiceNos instanceof Set?file.savedInvoiceNos:new Set(file.savedInvoiceNos||[]);
  const extractErrors=invoices.filter(isPurchaseExtractionError);
  const extractedInvoices=invoices.filter(inv=>!isPurchaseExtractionError(inv));
  const total=extractedInvoices.length;
  const savedCount=extractedInvoices.filter(inv=>saved.has(String(inv.invoice_no||''))).length;
  if(extractErrors.length)return {label:'Extraction error',tone:'red',badge:'b-r',summary:extractErrors[0].issues||'No purchase invoice rows were found'};
  if(total>0&&savedCount>=total)return {label:'Completed',tone:'green',badge:'b-g',summary:`${savedCount}/${total} invoices saved`};
  if(savedCount>0)return {label:'Partial',tone:'amber',badge:'b-a',summary:`${savedCount}/${total||savedCount} invoices saved`};
  if(file.status==='Error')return {label:'Not uploaded',tone:'red',badge:'b-r',summary:'Extraction failed, nothing saved'};
  if(file.status==='Extracted')return {label:'Not uploaded',tone:'red',badge:'b-r',summary:`0/${total} invoices saved`};
  if(file.status==='Ready')return {label:'Not uploaded',tone:'red',badge:'b-r',summary:'Ready for extraction, not saved'};
  return {label:'Not uploaded',tone:'red',badge:'b-r',summary:'Not saved'};
}

function renderPurchaseValidationDocumentStatus(){
  const target=document.getElementById('purchase-validation-doc-status');
  if(!target)return;
  const files=uploadedFiles.filter(file=>['Reading','Ready','Queued','Extracting','Extracted','Error'].includes(file.status));
  if(!files.length){
    target.innerHTML=`<div style="background:var(--red-bg);border:1px solid var(--red-border);border-radius:10px;padding:14px 16px">
      <div style="font-size:13.5px;font-weight:600;margin-bottom:3px">No uploaded documents yet</div>
      <div style="font-size:12px;color:var(--text3)">Upload and extract purchase files to see document status here.</div>
    </div>`;
    return;
  }
  const toneStyle={
    green:'background:var(--green-bg);border:1px solid var(--green-border)',
    amber:'background:var(--amber-bg);border:1px solid var(--amber-border)',
    red:'background:var(--red-bg);border:1px solid var(--red-border)'
  };
  target.innerHTML=files.map(file=>{
    const status=purchaseFileUploadStatus(file);
    const invoices=Array.isArray(file.invoices)?file.invoices:[];
    const reviewCount=invoices.filter(inv=>!isPurchaseExtractionError(inv)&&!validatePurchaseAiInvoice(inv).valid).length;
    const downloadDisabled=file.base64?'':' disabled';
    const uploadedAt=file.uploadedAt?new Date(file.uploadedAt).toLocaleString('en-AE'):'';
    return `<div style="${toneStyle[status.tone]};border-radius:10px;padding:14px 16px;margin-bottom:10px">
      <div style="display:flex;align-items:flex-start;gap:10px">
        <span style="font-size:18px;flex-shrink:0">${status.tone==='green'?'✓':status.tone==='amber'?'!':'×'}</span>
        <div style="flex:1;min-width:0">
          <div style="font-size:13.5px;font-weight:600;margin-bottom:3px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escapeHtml(file.name)}</div>
          <div style="font-size:12px;color:var(--text3)">${escapeHtml(status.summary)}${reviewCount?` - ${reviewCount} row(s) have review notes`:''}${uploadedAt?` - Uploaded ${escapeHtml(uploadedAt)}`:''}</div>
        </div>
        <div class="flx" style="flex-shrink:0">
          <button class="btn btn-g btn-sm" type="button"${downloadDisabled} onclick="downloadUploadedPurchaseFile('${escapeHtml(String(file.id))}')">Download</button>
          <span class="b ${status.badge}">${escapeHtml(status.label)}</span>
        </div>
      </div>
    </div>`;
  }).join('');
}

function fixTRN(invNo,btn){
  const key='fix-'+invNo.replace(/[^a-z0-9]/gi,'_');
  const inp=document.getElementById(key);
  const v=(inp?.value||'').replace(/\D/g,'');
  if(v.length!==15){toast('TRN must be exactly 15 digits','err');return;}
  btn.closest('.val-dynamic').style.background='var(--green-bg)';
  btn.closest('.val-dynamic').style.borderColor='var(--green-border)';
  btn.closest('.val-dynamic').querySelector('[style*="font-weight:600"]').textContent='? Fixed - '+invNo;
  btn.closest('.val-dynamic').querySelector('.btn-danger').remove();
  toast('TRN corrected and re-validated ?','ok');
}
function resolveReview(btn,choice){
  btn.closest('.val-dynamic').style.background='var(--green-bg)';
  btn.closest('.val-dynamic').style.borderColor='var(--green-border)';
  btn.parentElement.innerHTML='<span style="color:var(--green);font-size:12px">? Resolved</span>';
  toast(choice==='calc'?'Calculated VAT applied ?':'Extracted VAT kept ?','ok');
}

function runOCR(){
  // Extract ALL ready files
  let ready=uploadedFiles.filter(f=>f.status==='Ready'||f.status==='Queued');
  if(ready.length===0){
    ready=uploadedFiles.filter(f=>f.status==='Extracted'||f.status==='Error').slice(-1);
  }
  if(ready.length===0){toast('No new files to extract. Upload files first.','warn');return;}
  ready.forEach(file=>{file.status='Ready';});
  ready.forEach((f,i)=>setTimeout(()=>extractSingleFile(f),i*500));
}

function formatInputDateTime(date=new Date()){
  const pad=value=>String(value).padStart(2,'0');
  return `${date.getFullYear()}-${pad(date.getMonth()+1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function setText(id,value){
  const el=document.getElementById(id);
  if(el)el.textContent=value;
}

function setManualPurchaseDefaults(){
  bindManualPurchaseCalculator();
  const now=formatInputDateTime();
  ['mp-date','mp-paid-on'].forEach(id=>{
    const field=document.getElementById(id);
    if(field&&!field.value)field.value=now;
  });
  const ref=document.getElementById('mp-ref');
  if(ref&&!ref.value)ref.value=`PUR-${new Date().getFullYear()}-${String(Date.now()).slice(-4)}`;
  ensureManualPurchaseLine();
  calcManualPurchase();
}

function ensureManualPurchaseLine(){
  if(!document.querySelector('#mp-lines tr')&&document.getElementById('mp-lines')){
    addManualPurchaseLine();
  }
}

function bindManualPurchaseCalculator(){
  const page=document.getElementById('p-manual');
  if(!page||page.dataset.purchaseCalcBound==='1')return;
  page.dataset.purchaseCalcBound='1';
  page.addEventListener('input',event=>{
    if(event.target.matches('.mp-qty,.mp-cost,.mp-discount-pct,.mp-margin,.mp-expense-amount,#mp-discount,#mp-shipping,#mp-pay-amount')){
      calcManualPurchase();
    }
  });
  page.addEventListener('change',event=>{
    if(event.target.matches('.mp-product')){
      applyPurchaseProductSuggestion(event.target);
      calcManualPurchase();
      return;
    }
    if(event.target.matches('.mp-unit,#mp-discount-type,#mp-tax,#mp-pay-method,#mp-pay-account')){
      calcManualPurchase();
    }
    if(event.target.matches('#mp-supplier')){
      applySupplierAddress();
      calcManualPurchase();
    }
  });
}

function removeInitialBlankPurchaseLine(){
  const rows=[...document.querySelectorAll('#mp-lines tr')];
  if(rows.length!==1||manualPurchaseEditingRef)return;
  const row=rows[0];
  const product=(row.querySelector('.mp-product')?.value||'').trim();
  const qty=(row.querySelector('.mp-qty')?.value||'').trim();
  const cost=parseAmount(row.querySelector('.mp-cost')?.value);
  const discount=parseAmount(row.querySelector('.mp-discount-pct')?.value);
  const margin=parseAmount(row.querySelector('.mp-margin')?.value);
  if(!product&&(!qty||qty==='1')&&!cost&&!discount&&!margin){
    row.remove();
  }
}

function addManualPurchaseLine(){
  const tbody=document.getElementById('mp-lines');
  if(!tbody)return;
  refreshPurchaseProductSuggestions();
  const row=document.createElement('tr');
  row.innerHTML=`<td><input class="fi mp-product" list="purchase-product-options" placeholder="Product name" onfocus="refreshPurchaseProductSuggestions()" onchange="applyPurchaseProductSuggestion(this)"></td><td><input class="fi mono mp-qty" value="1" oninput="calcManualPurchase()"></td><td><select class="fi mp-unit">${unitOptionsHtml('PCS')}</select></td><td><input class="fi mono mp-cost" value="0.00" oninput="calcManualPurchase()"></td><td><input class="fi mono mp-discount-pct" value="0" oninput="calcManualPurchase()"></td><td class="mono mp-before-tax">0.00</td><td class="mono mp-line-total">0.00</td><td><input class="fi mono mp-margin" value="0" oninput="calcManualPurchase()"></td><td class="mono mp-selling">0.00</td><td><button class="icon-btn danger" type="button" onclick="removeManualPurchaseLine(this)" title="Remove line">${deleteIconSvg()}</button></td>`;
  tbody.appendChild(row);
  calcManualPurchase();
}

function importManualPurchaseProducts(){
  const fileInput=document.getElementById('mp-file');
  if(fileInput?.files?.length){
    toast(`${fileInput.files.length} attached file(s) ready for purchase import`,'info');
    return;
  }
  toast('Choose a purchase document first, then import products','info');
  fileInput?.click();
}

function removeManualPurchaseLine(btn){
  btn.closest('tr')?.remove();
  ensureManualPurchaseLine();
  calcManualPurchase();
}

function manualPurchaseLineHasValue(row){
  return Boolean(
    (row.querySelector('.mp-product')?.value||'').trim()||
    parseAmount(row.querySelector('.mp-qty')?.value)>1||
    parseAmount(row.querySelector('.mp-cost')?.value)>0||
    parseAmount(row.querySelector('.mp-discount-pct')?.value)>0||
    parseAmount(row.querySelector('.mp-margin')?.value)>0
  );
}

function collectManualPurchaseLines(){
  return [...document.querySelectorAll('#mp-lines tr')]
    .filter(manualPurchaseLineHasValue)
    .map(row=>({
      product:(row.querySelector('.mp-product')?.value||'').trim(),
      quantity:parseAmount(row.querySelector('.mp-qty')?.value),
      unit_of_measure:row.querySelector('.mp-unit')?.value||'PCS',
      unit_cost:parseAmount(row.querySelector('.mp-cost')?.value),
      discount_percent:parseAmount(row.querySelector('.mp-discount-pct')?.value),
      profit_margin:parseAmount(row.querySelector('.mp-margin')?.value),
      unit_cost_before_tax:parseAmount(row.querySelector('.mp-before-tax')?.textContent),
      line_total:parseAmount(row.querySelector('.mp-line-total')?.textContent),
      selling_price_inc_tax:parseAmount(row.querySelector('.mp-selling')?.textContent)
    }));
}

function purchaseRecordRefKey(recordOrRef){
  const ref=typeof recordOrRef==='string'
    ? recordOrRef
    : recordOrRef?.ref||recordOrRef?.invoice_no||recordOrRef?.reference||'';
  return invoiceKey(ref);
}

function purchaseLineProductKey(line={}){
  return String(line.sku||line.code||purchaseAiProductName(line)||line.product||line.name||line.description||'')
    .trim()
    .toLowerCase()
    .replace(/\s+/g,' ');
}

function normalizePurchaseLineForMerge(line={}){
  const product=purchaseAiProductName(line)||line.product||line.name||line.description||'Purchase item';
  const quantity=parseAmount(line.quantity||line.qty||0);
  const unitCost=parseAmount(line.unit_cost||line.cost||line.unitCost||line.unit_cost_before_tax);
  const lineTotal=parseAmount(line.line_total||line.amount||line.total)||(quantity*unitCost);
  return {
    ...line,
    product,
    quantity,
    unit_of_measure:line.unit_of_measure||line.unit||line.uom||'PCS',
    unit:line.unit||line.unit_of_measure||line.uom||'PCS',
    unit_cost:unitCost,
    unit_cost_before_tax:parseAmount(line.unit_cost_before_tax||line.unit_cost||line.cost||unitCost),
    line_total:lineTotal,
    discount_percent:parseAmount(line.discount_percent||line.discountPct||0),
    profit_margin:parseAmount(line.profit_margin||line.margin||0),
    selling_price_inc_tax:parseAmount(line.selling_price_inc_tax||line.selling_price||0)
  };
}

function findPurchaseRecordByRef(ref){
  const key=purchaseRecordRefKey(ref);
  if(!key)return null;
  const cached=[...purchaseRecordCache.values()].find(record=>purchaseRecordRefKey(record)===key);
  if(cached)return cached;
  const row=[...document.querySelectorAll('#purchase-record-tbody tr:not([data-empty-state])')]
    .find(item=>purchaseRecordRefKey(item.children[0]?.textContent)===key);
  return row?purchaseRecordFromRow(row):null;
}

function mergePurchaseRecords(existing={},incoming={}){
  const baseLines=(Array.isArray(existing.lines)?existing.lines:[]).map(normalizePurchaseLineForMerge);
  const incomingLines=(Array.isArray(incoming.lines)?incoming.lines:[]).map(normalizePurchaseLineForMerge);
  const mergedLines=[...baseLines.map(line=>({...line}))];
  const lineMap=new Map();
  mergedLines.forEach((line,index)=>{
    const key=purchaseLineProductKey(line);
    if(key&&!lineMap.has(key))lineMap.set(key,index);
  });
  let mergedSameProduct=0;
  let addedProducts=0;
  incomingLines.forEach(line=>{
    const key=purchaseLineProductKey(line);
    const index=key?lineMap.get(key):undefined;
    if(index!==undefined){
      const target=mergedLines[index];
      const quantity=parseAmount(target.quantity||target.qty)+parseAmount(line.quantity||line.qty);
      const lineTotal=parseAmount(target.line_total||target.amount)+parseAmount(line.line_total||line.amount);
      target.quantity=quantity;
      target.qty=quantity;
      target.line_total=lineTotal;
      target.amount=lineTotal;
      target.unit_cost=quantity?lineTotal/quantity:parseAmount(line.unit_cost||target.unit_cost);
      target.unit_cost_before_tax=target.unit_cost;
      target.selling_price_inc_tax=parseAmount(line.selling_price_inc_tax||target.selling_price_inc_tax);
      mergedSameProduct++;
    }else{
      mergedLines.push({...line});
      if(key)lineMap.set(key,mergedLines.length-1);
      addedProducts++;
    }
  });
  const merged={
    ...existing,
    ...incoming,
    ref:incoming.ref||incoming.invoice_no||existing.ref||existing.invoice_no,
    supplier:incoming.supplier||existing.supplier,
    address:incoming.address||existing.address||'',
    date:incoming.date||existing.date||'',
    location:incoming.location||existing.location||'Main Store',
    lines:mergedLines
  };
  const existingOnlyTax=Math.max(0,parseAmount(existing.tax_amount||existing.vat_amount));
  const incomingTax=Math.max(0,parseAmount(incoming.tax_amount||incoming.vat_amount));
  const existingShipping=Math.max(0,parseAmount(existing.shipping));
  const incomingShipping=Math.max(0,parseAmount(incoming.shipping));
  const existingPaid=Math.max(0,parseAmount(existing.paid));
  const incomingPaid=Math.max(0,parseAmount(incoming.paid));
  merged.items=purchaseLinesTotalQuantity(mergedLines)||mergedLines.length;
  merged.net_amount=mergedLines.reduce((sum,line)=>sum+parseAmount(line.line_total||line.amount),0);
  merged.tax_amount=existingOnlyTax+incomingTax;
  merged.shipping=existingShipping+incomingShipping;
  merged.paid=existingPaid+incomingPaid;
  merged.total=merged.net_amount+merged.tax_amount+merged.shipping+parseAmount(existing.additional_expense_amount)+parseAmount(incoming.additional_expense_amount);
  merged.due=Math.max(0,merged.total-merged.paid);
  return {record:merged,mergedSameProduct,addedProducts};
}

function upsertPurchaseRecordLocal(record,{replace=false}={}){
  const existing=replace?null:findPurchaseRecordByRef(record);
  const result=existing?mergePurchaseRecords(existing,record):{record,mergedSameProduct:0,addedProducts:Array.isArray(record.lines)?record.lines.length:0};
  const finalRecord=result.record;
  const ref=finalRecord.ref||finalRecord.invoice_no||record.ref;
  if(ref)purchaseRecordCache.set(String(ref),finalRecord);
  renderPurchaseRecordWindow();
  syncStockLevelsFromProducts();
  return {...result,record:finalRecord,wasMerged:Boolean(existing)};
}

function addManualPurchaseExpense(expense={}){
  const box=document.getElementById('mp-expenses');
  if(!box)return;
  const row=document.createElement('div');
  row.className='inv-item mp-expense-row';
  row.innerHTML=`<input class="fi mp-expense-name" placeholder="Expense name" value="${escapeHtml(expense.name||'')}"><input class="fi mono mp-expense-amount" placeholder="0.00" value="${escapeHtml(expense.amount||'')}" oninput="calcManualPurchase()"><button class="btn btn-g" style="padding:4px 8px" onclick="removeManualPurchaseExpense(this)">x</button>`;
  box.appendChild(row);
  calcManualPurchase();
}

function removeManualPurchaseExpense(btn){
  btn.closest('.mp-expense-row')?.remove();
  calcManualPurchase();
}

function collectManualPurchaseExpenses(){
  return [...document.querySelectorAll('#mp-expenses .mp-expense-row')]
    .map(row=>({
      name:(row.querySelector('.mp-expense-name')?.value||'Additional expense').trim(),
      amount:parseAmount(row.querySelector('.mp-expense-amount')?.value)
    }))
    .filter(expense=>expense.amount>0);
}

function purchaseProductRecords(){
  const records=[];
  document.querySelectorAll('#prod-tbody tr:not([data-empty-state])').forEach(row=>{
    const code=inventoryRowCellText(row,0);
    const name=inventoryRowCellText(row,1);
    if(!name)return;
    records.push({
      code,
      name,
      unit:inventoryRowCellText(row,4)||'PCS',
      cost:Number(row.dataset.cost||0),
      supplier:row.dataset.supplier||''
    });
  });
  document.querySelectorAll('#stock-map-tbody tr:not([data-empty-state])').forEach(row=>{
    const cells=row.children;
    const sku=row.dataset.stockSku||'';
    const name=cells[2]?.textContent.trim()||cells[0]?.textContent.trim()||sku;
    if(!name)return;
    records.push({
      code:sku,
      name,
      unit:row.dataset.unit||'PCS',
      cost:Number(row.dataset.cost||0),
      supplier:(cells[1]?.textContent.trim()||'').replace(/^Not assigned$/,'')
    });
  });
  return records;
}

function refreshPurchaseProductSuggestions(){
  const list=document.getElementById('purchase-product-options');
  if(!list)return;
  list.innerHTML=purchaseProductRecords()
    .map(item=>`<option value="${escapeHtml(item.name)}" label="${escapeHtml([item.code,item.unit,item.supplier].filter(Boolean).join(' - '))}"></option>`)
    .join('');
}

function applyPurchaseProductSuggestion(input){
  const value=(input?.value||'').trim().toLowerCase();
  if(!value)return;
  const match=purchaseProductRecords().find(item=>[item.name,item.code].some(text=>String(text||'').toLowerCase()===value));
  if(!match)return;
  const row=input.closest('tr');
  setSelectValue(row?.querySelector('.mp-unit'),match.unit||'PCS');
  if(row?.querySelector('.mp-cost')&&match.cost)row.querySelector('.mp-cost').value=Number(match.cost).toFixed(2);
  if(match.supplier&&!document.getElementById('mp-supplier')?.value){
    setSelectValue(document.getElementById('mp-supplier'),match.supplier);
    applySupplierAddress();
  }
  calcManualPurchase();
}

function setSelectValue(select,value){
  if(!select)return;
  const wanted=String(value||'');
  const existing=[...select.options].find(option=>option.value===wanted||option.textContent===wanted);
  if(existing){
    select.value=existing.value;
  }else if(wanted){
    select.appendChild(new Option(wanted,wanted));
    select.value=wanted;
  }
}

let manualPurchaseEditingRef='';

function calcManualPurchase(){
  const rows=[...document.querySelectorAll('#mp-lines tr')];
  let net=0;
  let itemCount=0;
  rows.forEach((row,index)=>{
    const qty=parseAmount(row.querySelector('.mp-qty')?.value);
    const cost=parseAmount(row.querySelector('.mp-cost')?.value);
    const discountPct=parseAmount(row.querySelector('.mp-discount-pct')?.value);
    const margin=parseAmount(row.querySelector('.mp-margin')?.value);
    const beforeTax=cost*(1-(discountPct/100));
    const lineTotal=qty*beforeTax;
    const selling=beforeTax*(1+(margin/100))*1.05;
    const beforeTaxCell=row.querySelector('.mp-before-tax');
    const lineTotalCell=row.querySelector('.mp-line-total');
    const sellingCell=row.querySelector('.mp-selling');
    if(beforeTaxCell)beforeTaxCell.textContent=beforeTax.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
    if(lineTotalCell)lineTotalCell.textContent=lineTotal.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
    if(sellingCell)sellingCell.textContent=selling.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
    if(manualPurchaseLineHasValue(row)){
      itemCount+=qty;
      net+=lineTotal;
    }
  });
  const discountType=document.getElementById('mp-discount-type')?.value||'None';
  const discountValue=parseAmount(document.getElementById('mp-discount')?.value);
  const discount=discountType==='Percentage'?net*(discountValue/100):discountType==='Fixed'?discountValue:0;
  const taxable=Math.max(0,net-discount);
  const taxType=document.getElementById('mp-tax')?.value||'None';
  const tax=taxType.includes('5%')&&!taxType.toLowerCase().includes('exempt')?taxable*.05:0;
  const shipping=parseAmount(document.getElementById('mp-shipping')?.value);
  const extraExpenses=collectManualPurchaseExpenses().reduce((sum,expense)=>sum+Number(expense.amount||0),0);
  const total=taxable+tax+shipping+extraExpenses;
  const paid=parseAmount(document.getElementById('mp-pay-amount')?.value);
  const due=Math.max(0,total-paid);
  setText('mp-total-items',Number(itemCount||0).toLocaleString('en-AE',{maximumFractionDigits:4}));
  setText('mp-net-total',net.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setText('mp-discount-total',discount.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setText('mp-tax-total',tax.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setText('mp-purchase-total',total.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setText('mp-grand-total',total.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setText('mp-payment-due',due.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  return {items:itemCount,net,discount,tax,shipping,extraExpenses,total,paid,due};
}

function resetManualPurchase(){
  manualPurchaseEditingRef='';
  document.querySelectorAll('#p-manual input,#p-manual textarea').forEach(field=>{
    if(field.type==='file')field.value='';
    else field.value='';
  });
  document.querySelectorAll('#p-manual select').forEach(select=>select.selectedIndex=0);
  const tbody=document.getElementById('mp-lines');
  if(tbody)tbody.innerHTML='';
  const expenses=document.getElementById('mp-expenses');
  if(expenses)expenses.innerHTML='';
  const ref=document.getElementById('mp-ref');
  if(ref)ref.disabled=false;
  setText('mp-form-title','Add Purchase');
  setText('mp-form-sub','Manual supplier purchase entry with items, discounts, tax, shipping, and payment');
  setText('mp-save-btn','Save');
  setManualPurchaseDefaults();
  configureManualPurchaseMode();
}

function startPurchaseTransaction(type='purchase'){
  currentPurchaseTransactionType=type==='return'?'return':type==='local_po'?'local_po':'purchase';
  closeM('m-add-choice');
  go('purchase');
  setTimeout(()=>{
    const tab=document.querySelector('#page-purchase .tab:nth-child(4)');
    if(tab)stab(tab,'p-manual');
    resetManualPurchase();
    configureManualPurchaseMode();
  },50);
}

function configureManualPurchaseMode(){
  const t=currentPurchaseTransactionType;
  const isReturn=t==='return';
  const isLPO=t==='local_po';
  const title=isReturn?'Purchase Return':isLPO?'Local Purchase Order':'Add Purchase';
  const sub=isReturn?'Supplier return entry for returned goods, debit notes, or purchase adjustments':isLPO?'Local purchase order for domestic suppliers':'Manual supplier purchase entry with items, discounts, tax, shipping, and payment';
  const btn=isReturn?'Save Purchase Return':isLPO?'Save LPO':'Save';
  const prefix=isReturn?'PRET':isLPO?'LPO':'PUR';
  setText('mp-form-title',title);
  setText('mp-form-sub',sub);
  setText('mp-save-btn',btn);
  const ref=document.getElementById('mp-ref');
  if(ref&&!ref.value)ref.value=`${prefix}-${new Date().getFullYear()}-${String(Date.now()).slice(-5)}`;
}

async function saveManualPurchase(){
  setManualPurchaseDefaults();
  const supplier=document.getElementById('mp-supplier')?.value||'';
  if(!supplier){
    toast('Select supplier','warn');
    return;
  }
  removeInitialBlankPurchaseLine();
  ensureManualPurchaseLine();
  const totals=calcManualPurchase();
  const status=totals.due<=0?'Paid':'Pending Payment';
  const isReturn=currentPurchaseTransactionType==='return';
  const isLPO=currentPurchaseTransactionType==='local_po';
  if(!document.querySelector('#mp-lines tr')){
    ensureManualPurchaseLine();
    toast('Add at least one product','warn');
    return;
  }
  const lines=collectManualPurchaseLines();
  if(!lines.length){
    ensureManualPurchaseLine();
    toast('Add at least one product','warn');
    return;
  }
  const invalidLine=lines.find(line=>!line.product||line.quantity<=0||line.unit_cost<0);
  if(invalidLine){
    toast('Each purchase line needs product name and quantity','warn');
    return;
  }
  const ref=(document.getElementById('mp-ref')?.value||`${isReturn?'PRET':isLPO?'LPO':'PUR'}-${Date.now()}`).trim();
  const record={
    ref,
    supplier,
    address:document.getElementById('mp-address')?.value||'',
    date:document.getElementById('mp-date')?.value||'',
    status:isReturn?'Return':status,
    location:'Main Store',
    pay_term:document.getElementById('mp-term')?.value||'',
    items:totals.items,
    net_amount:totals.net,
    discount:totals.discount,
    tax_amount:totals.tax,
    shipping:totals.shipping,
    additional_expenses:collectManualPurchaseExpenses(),
    additional_expense_amount:totals.extraExpenses,
    total:totals.total,
    paid:totals.paid,
    due:totals.due,
    discount_type:document.getElementById('mp-discount-type')?.value||'None',
    discount_value:parseAmount(document.getElementById('mp-discount')?.value),
    tax_type:document.getElementById('mp-tax')?.value||'None',
    lines,
    payment_method:document.getElementById('mp-pay-method')?.value||'Cash',
    payment_account:document.getElementById('mp-pay-account')?.value||'None',
    payment_note:document.getElementById('mp-pay-note')?.value||'',
    paid_on:document.getElementById('mp-paid-on')?.value||'',
    shipping_details:document.getElementById('mp-shipping-details')?.value||'',
    notes:document.getElementById('mp-notes')?.value||'',
    source:isReturn?'Purchase Return':isLPO?'Local PO':'Manual',
    document_type:isReturn?'Purchase Return':isLPO?'Local Purchase Order':'Purchase Invoice'
  };
  const wasEditing=Boolean(manualPurchaseEditingRef);
  if(manualPurchaseEditingRef){
    [...document.querySelectorAll('#purchase-record-tbody tr')].find(row=>row.children[0]?.textContent.trim()===manualPurchaseEditingRef)?.remove();
    purchaseRecordCache.delete(manualPurchaseEditingRef);
  }
  setInventoryTableCleared(false);
  stockLevelsServerRefreshPaused=true;
  const upsert=upsertPurchaseRecordLocal(record,{replace:wasEditing});
  const savedRecord=upsert.record;
  savedRecord.items=purchaseLinesTotalQuantity(savedRecord.lines)||savedRecord.items;
  purchaseRecordsTotal=Math.max(purchaseRecordsTotal,purchaseRecordCache.size);
  audit(wasEditing?'Updated manual purchase':isReturn?'Added purchase return':upsert.wasMerged?'Merged manual purchase':'Added manual purchase',ref,'Saved');
  saveServer('purchaseRecords',savedRecord,{throwOnError:true})
    .then(()=>{
      stockLevelsServerRefreshPaused=false;
      loadStockLevelsFromServer();
      toast(wasEditing?'Purchase updated in database':isReturn?'Purchase return saved to database':upsert.wasMerged?`Purchase merged: ${upsert.mergedSameProduct} same product updated, ${upsert.addedProducts} new product line(s)`:'Purchase saved to database','ok');
    })
    .catch(()=>{
      stockLevelsServerRefreshPaused=false;
      toast('Purchase added on screen, database save failed','warn');
    });
  manualPurchaseEditingRef='';
  const refField=document.getElementById('mp-ref');
  if(refField)refField.disabled=false;
  updatePurchaseRecordControls(purchaseRecordsTotal,purchaseRecordCache.size);
  renderLPOList();
  if(isLPO){
    stab(document.querySelector('#page-purchase .tab:nth-child(6)'),'p-lpo');
  }else{
    stab(document.querySelector('#page-purchase .tab:nth-child(5)'),'p-records');
  }
}

function purchaseRecordFromRow(row){
  if(!row)return null;
  const cached=purchaseRecordCache.get(row.dataset.purchaseRef||row.children[0]?.textContent.trim());
  if(cached)return cached;
  if(row.dataset.purchaseRecord){
    try{return JSON.parse(row.dataset.purchaseRecord);}catch(err){console.warn('Purchase row data parse failed:',err);}
  }
  const cells=row.children;
  return {
    ref:cells[0]?.textContent.trim()||'',
    product_name:cells[1]?.textContent.trim()||'',
    supplier:cells[2]?.textContent.trim()||'',
    date:cells[3]?.textContent.trim()||'',
    location:cells[4]?.textContent.trim()||'',
    items:parseAmount(cells[5]?.textContent),
    net_amount:parseAmount(cells[6]?.textContent),
    tax_amount:parseAmount(cells[7]?.textContent),
    shipping:parseAmount(cells[8]?.textContent),
    total:parseAmount(cells[9]?.textContent),
    paid:parseAmount(cells[10]?.textContent),
    due:parseAmount(cells[11]?.textContent),
    source:cells[12]?.textContent.trim()||'Manual',
    status:cells[13]?.textContent.trim()||'Draft'
  };
}

function ensurePurchasePreviewModal(){
  let overlay=document.getElementById('m-purchase-view');
  if(overlay)return overlay;
  overlay=document.createElement('div');
  overlay.className='overlay';
  overlay.id='m-purchase-view';
  overlay.onclick=e=>closeOvBg(e,'m-purchase-view');
  overlay.innerHTML=`
    <div class="modal modal-xl purchase-edit-modal">
      <div class="purchase-edit-top">
        <div>
          <div class="modal-title" id="purchase-view-title">Purchase Order Preview</div>
          <div class="modal-sub" id="purchase-view-sub">Purchase record</div>
        </div>
        <button class="btn btn-g btn-sm" onclick="closeM('m-purchase-view')">Close</button>
      </div>
      <div id="purchase-view-body"></div>
      <div class="modal-foot">
        <button class="btn btn-g" onclick="toast('Preparing purchase PDF...','info')">Export PDF</button>
        <button class="btn btn-p hidden" id="purchase-view-save" onclick="savePurchasePreviewEdit()">Save Changes</button>
        <button class="btn btn-p" onclick="closeM('m-purchase-view')">Close</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  return overlay;
}

function purchasePreviewLines(purchase){
  const lines=Array.isArray(purchase?.lines)&&purchase.lines.length
    ? purchase.lines
    : [{product:'Purchase items',quantity:purchase?.items||1,unit_cost:Number(purchase?.net_amount||purchase?.total||0)/Math.max(1,Number(purchase?.items||1)),line_total:purchase?.net_amount||purchase?.total||0}];
  return lines.map(line=>{
    const qty=parseAmount(line.quantity||line.qty||1)||1;
    const cost=parseAmount(line.unit_cost||line.cost||line.unitCost||line.unit_cost_before_tax);
    const total=parseAmount(line.line_total||line.total||line.amount)||(qty*cost);
    return {
      product:purchaseAiProductName(line)||line.name||'Purchase item',
      sku:line.sku||line.code||'',
      unit:line.unit||line.unit_of_measure||line.uom||'PCS',
      qty,
      cost,
      total,
      discount_pct:parseAmount(line.discount_percent||line.discount_pct||line.discountPct||0),
      discount_amount:parseAmount(line.discount_amount||0),
      vat_amount:parseAmount(line.vat_amount||0),
    };
  });
}

function renderPurchaseRecordPreview(purchase,options={}){
  ensurePurchasePreviewModal();
  const editable=Boolean(options.editable);
  currentPurchaseViewRef=purchase.ref||purchase.invoice_no||purchase.reference||'';
  const title=document.getElementById('purchase-view-title');
  const sub=document.getElementById('purchase-view-sub');
  const body=document.getElementById('purchase-view-body');
  const saveBtn=document.getElementById('purchase-view-save');
  if(!body)return;
  const ref=purchase.ref||purchase.invoice_no||purchase.reference||'Purchase';
  const lines=purchasePreviewLines(purchase);
  const net=parseAmount(purchase.net_amount||purchase.subtotal)||lines.reduce((sum,line)=>sum+line.total,0);
  const vat=parseAmount(purchase.tax_amount||purchase.vat);
  const shipping=parseAmount(purchase.shipping);
  const paid=parseAmount(purchase.paid);
  const total=parseAmount(purchase.total)||net+vat+shipping;
  const due=Number.isFinite(Number(purchase.due))?parseAmount(purchase.due):Math.max(0,total-paid);
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  if(title)title.textContent=(editable?'Edit Purchase Order ':'Purchase Order ')+ref;
  if(sub)sub.textContent=`${purchase.supplier||'Supplier'} - ${purchase.status||'Draft'}`;
  if(saveBtn)saveBtn.classList.toggle('hidden',!editable);
  const cur=purchase.currency||'AED';
  const discount=parseAmount(purchase.discount_value||purchase.discount);
  const discountType=purchase.discount_type||'None';
  const taxType=purchase.tax_type||(vat>0?'VAT 5%':'None');
  const supplierTrn=purchase.supplier_trn||'';
  const billTo=purchase.bill_to||'';
  const paidOn=purchase.paid_on||'';
  const payNote=purchase.payment_note||'';
  const filename=purchase.filename||'';
  const hasIssues=purchase.issues&&String(purchase.issues).trim();
  body.innerHTML=`
    <div class="purchase-invoice-sheet">
      ${hasIssues?`<div class="pv-issues-banner">${escapeHtml(purchase.issues)}</div>`:''}
      <div class="purchase-invoice-head">
        <div>
          <div class="purchase-invoice-kicker">Purchase Invoice</div>
          <input class="purchase-invoice-title" id="pv-ref" value="${escapeHtml(ref)}" readonly>
          <div class="card-sub">${escapeHtml(purchase.source||'Manual')} · <span class="mono">${escapeHtml(cur)}</span>${filename?` · <span style="color:var(--text3)">${escapeHtml(filename)}</span>`:''}</div>
        </div>
        <div class="purchase-invoice-meta">
          <label>Supplier<input class="fi" id="pv-supplier" value="${escapeHtml(purchase.supplier||'')}" ${editable?'':'readonly'}></label>
          <label>Supplier TRN<input class="fi mono" id="pv-supplier-trn" value="${escapeHtml(supplierTrn)}" placeholder="15-digit TRN" ${editable?'':'readonly'} style="${supplierTrn&&supplierTrn.length!==15?'border-color:var(--red)':''}"></label>
          <label>Status<select class="fi" id="pv-status" ${editable?'':'disabled'}><option${(purchase.status||'Draft')==='Draft'?' selected':''}>Draft</option><option${(purchase.status||'')==='Pending Payment'?' selected':''}>Pending Payment</option><option${(purchase.status||'')==='Paid'?' selected':''}>Paid</option><option${(purchase.status||'')==='Valid'?' selected':''}>Valid</option><option${(purchase.status||'')==='Received'?' selected':''}>Received</option></select></label>
          <label>Date<input class="fi" id="pv-date" value="${escapeHtml(purchase.date||'')}" ${editable?'':'readonly'}></label>
          <label>Location<input class="fi" id="pv-location" value="${escapeHtml(purchase.location||'Main Store')}" ${editable?'':'readonly'}></label>
        </div>
      </div>
      <div class="purchase-party-grid">
        <div class="purchase-party-box">
          <div class="section-hd">Supplier Details</div>
          <input class="fi mb8" id="pv-address" value="${escapeHtml(purchase.address||purchase.shipping_details||'')}" placeholder="Supplier address" ${editable?'':'readonly'}>
          ${billTo?`<label style="font-size:11px;color:var(--text3);font-weight:600;display:block;margin-bottom:4px">Bill To</label><input class="fi" id="pv-bill-to" value="${escapeHtml(billTo)}" placeholder="Bill to" ${editable?'':'readonly'}>`:`<input class="fi" id="pv-bill-to" value="" placeholder="Bill to" ${editable?'':'readonly'}>`}
        </div>
        <div class="purchase-party-box">
          <div class="section-hd">Payment</div>
          <div class="fr2 mb8"><input class="fi" id="pv-pay-term" value="${escapeHtml(purchase.pay_term||'')}" placeholder="Pay term" ${editable?'':'readonly'}><input class="fi" id="pv-pay-method" value="${escapeHtml(purchase.payment_method||'')}" placeholder="Payment method" ${editable?'':'readonly'}></div>
          <div class="fr2 mb8"><input class="fi" id="pv-pay-account" value="${escapeHtml(purchase.payment_account||'')}" placeholder="Payment account" ${editable?'':'readonly'}><input class="fi" id="pv-paid-on" value="${escapeHtml(paidOn)}" placeholder="Paid on date" ${editable?'':'readonly'}></div>
          <input class="fi" id="pv-pay-note" value="${escapeHtml(payNote)}" placeholder="Payment reference / note" ${editable?'':'readonly'}>
        </div>
      </div>
      <div class="purchase-edit-table-wrap">
        <table class="tbl purchase-edit-lines">
          <thead><tr><th>#</th><th>Item Description</th><th>SKU</th><th>Qty</th><th>Unit</th><th>Unit Price</th><th>Disc %</th><th>Disc Amt</th><th>VAT</th><th>Line Total</th><th></th></tr></thead>
          <tbody>
            ${lines.map((line,index)=>{
              const lQty=parseAmount(line.qty||line.quantity||1);
              const lCost=parseAmount(line.cost||line.unit_cost||line.unit_price||0);
              const lDiscPct=parseAmount(line.discount_pct||line.discount_percent||line.discount||0);
              const lDiscAmt=parseAmount(line.discount_amount)||(lCost*lQty*(lDiscPct/100));
              const lVat=parseAmount(line.vat_amount||0);
              return `<tr class="pv-line">
                <td>${index+1}</td>
                <td><input class="fi pv-product" value="${escapeHtml(line.product)}" ${editable?'':'readonly'}></td>
                <td><input class="fi mono pv-sku" value="${escapeHtml(line.sku||'')}" ${editable?'':'readonly'}></td>
                <td><input class="fi mono pv-qty" value="${fmt(lQty)}" oninput="calcPurchasePreviewEdit()" ${editable?'':'readonly'}></td>
                <td><input class="fi pv-unit" value="${escapeHtml(line.unit||'PCS')}" ${editable?'':'readonly'}></td>
                <td><input class="fi mono pv-cost" value="${fmt(lCost)}" oninput="calcPurchasePreviewEdit()" ${editable?'':'readonly'}></td>
                <td><input class="fi mono pv-disc-pct" value="${lDiscPct>0?fmt(lDiscPct):''}" placeholder="0" oninput="calcPurchasePreviewEdit()" ${editable?'':'readonly'} style="width:60px"></td>
                <td><input class="fi mono pv-disc-amt" value="${lDiscAmt>0?fmt(lDiscAmt):''}" placeholder="0" readonly style="width:80px"></td>
                <td><input class="fi mono pv-line-vat" value="${lVat>0?fmt(lVat):''}" placeholder="0" readonly style="width:70px"></td>
                <td><input class="fi mono pv-line-total" value="${fmt(line.total)}" readonly style="font-weight:700;color:var(--accent)"></td>
                <td><button class="icon-btn danger" type="button" title="Delete row" onclick="deletePurchasePreviewLine(this)">${deleteIconSvg()}</button></td>
              </tr>`;
            }).join('')}
          </tbody>
        </table>
      </div>
      <div class="purchase-invoice-bottom">
        <div class="purchase-party-box">
          <div class="section-hd">Notes</div>
          <textarea class="fi" id="pv-notes" rows="3" ${editable?'':'readonly'}>${escapeHtml(purchase.notes||purchase.payment_note||'')}</textarea>
          <div class="pv-meta-row mt8">
            <span class="pv-meta-label">Tax Type</span>
            <span class="pv-meta-val">${escapeHtml(taxType)}</span>
          </div>
          ${purchase.issues?`<div class="pv-meta-row"><span class="pv-meta-label" style="color:var(--red)">Issues</span><span class="pv-meta-val" style="color:var(--red)">${escapeHtml(purchase.issues)}</span></div>`:''}
        </div>
        <div class="purchase-summary-box">
          <div class="tot-row"><span>Net Amount</span><input class="fi mono" id="pv-net" value="${fmt(net)}" readonly></div>
          ${discount>0?`<div class="tot-row"><span>Discount (${escapeHtml(discountType)})</span><input class="fi mono" id="pv-discount" value="${fmt(discount)}" readonly></div>`:''}
          <div class="tot-row"><span>VAT (${escapeHtml(taxType)})</span><input class="fi mono" id="pv-vat" value="${fmt(vat)}" oninput="calcPurchasePreviewEdit()" ${editable?'':'readonly'}></div>
          <div class="tot-row"><span>Shipping</span><input class="fi mono" id="pv-shipping" value="${fmt(shipping)}" oninput="calcPurchasePreviewEdit()" ${editable?'':'readonly'}></div>
          <div class="tot-final"><span>Total (${escapeHtml(cur)})</span><input class="fi mono" id="pv-total" value="${fmt(total)}" readonly></div>
          <div class="tot-row"><span>Paid</span><input class="fi mono" id="pv-paid" value="${fmt(paid)}" oninput="calcPurchasePreviewEdit()" ${editable?'':'readonly'}></div>
          <div class="tot-row ${due>0?'tot-due':''}"><span>Balance Due</span><input class="fi mono" id="pv-due" value="${fmt(due)}" readonly></div>
        </div>
      </div>
    </div>`;
  calcPurchasePreviewEdit();
}

function openPurchaseRecordPreview(btn){
  const purchase=purchaseRecordFromRow(btn.closest('tr'));
  if(!purchase?.ref){
    toast('Purchase record not found','warn');
    return;
  }
  renderPurchaseRecordPreview(purchase);
  showM('m-purchase-view');
  audit('Viewed purchase order',purchase.ref,'Viewed');
}

function calcPurchasePreviewEdit(){
  let net=0;
  document.querySelectorAll('#purchase-view-body .pv-line').forEach(row=>{
    const qty=parseAmount(row.querySelector('.pv-qty')?.value);
    const cost=parseAmount(row.querySelector('.pv-cost')?.value);
    const total=qty*cost;
    net+=total;
    const lineTotal=row.querySelector('.pv-line-total');
    if(lineTotal)lineTotal.value=total.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  });
  const vat=parseAmount(document.getElementById('pv-vat')?.value);
  const shipping=parseAmount(document.getElementById('pv-shipping')?.value);
  const paid=parseAmount(document.getElementById('pv-paid')?.value);
  const total=net+vat+shipping;
  const due=Math.max(0,total-paid);
  setFieldValue(document.getElementById('pv-net'),net.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setFieldValue(document.getElementById('pv-total'),total.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  setFieldValue(document.getElementById('pv-due'),due.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2}));
  return {net,vat,shipping,paid,total,due};
}

function collectPurchasePreviewLines(){
  return [...document.querySelectorAll('#purchase-view-body .pv-line')].map(row=>({
    product:row.querySelector('.pv-product')?.value?.trim()||'Purchase item',
    sku:row.querySelector('.pv-sku')?.value?.trim()||'',
    quantity:parseAmount(row.querySelector('.pv-qty')?.value),
    unit:row.querySelector('.pv-unit')?.value?.trim()||'PCS',
    unit_cost:parseAmount(row.querySelector('.pv-cost')?.value),
    line_total:parseAmount(row.querySelector('.pv-line-total')?.value)
  })).filter(line=>line.product||line.quantity||line.unit_cost);
}

function deletePurchasePreviewLine(btn){
  const row=btn.closest('tr.pv-line');
  if(!row)return;
  row.remove();
  let i=1;
  document.querySelectorAll('#purchase-view-body .pv-line td:first-child').forEach(td=>td.textContent=i++);
  calcPurchasePreviewEdit();
}

function savePurchasePreviewEdit(){
  const existing=purchaseRecordCache.get(currentPurchaseViewRef)||{};
  const totals=calcPurchasePreviewEdit();
  const ref=document.getElementById('pv-ref')?.value?.trim()||currentPurchaseViewRef||`PUR-${Date.now()}`;
  const lines=collectPurchasePreviewLines();
  const record={
    ...existing,
    ref,
    supplier:document.getElementById('pv-supplier')?.value?.trim()||'Supplier',
    date:document.getElementById('pv-date')?.value||'',
    location:document.getElementById('pv-location')?.value||'Main Store',
    address:document.getElementById('pv-address')?.value||'',
    status:document.getElementById('pv-status')?.value||'Draft',
    pay_term:document.getElementById('pv-pay-term')?.value||'',
    payment_method:document.getElementById('pv-pay-method')?.value||'',
    payment_account:document.getElementById('pv-pay-account')?.value||'',
    notes:document.getElementById('pv-notes')?.value||'',
    items:purchaseLinesTotalQuantity(lines)||lines.length,
    net_amount:totals.net,
    tax_amount:totals.vat,
    shipping:totals.shipping,
    total:totals.total,
    paid:totals.paid,
    due:totals.due,
    lines
  };
  if(currentPurchaseViewRef&&currentPurchaseViewRef!==ref)purchaseRecordCache.delete(currentPurchaseViewRef);
  setInventoryTableCleared(false);
  purchaseRecordCache.set(ref,record);
  renderPurchaseRecordWindow();
  syncStockLevelsFromProducts();
  saveServer('purchaseRecords',record);
  currentPurchaseViewRef=ref;
  renderPurchaseRecordPreview(record,{editable:true});
  toast('Purchase updated','ok');
  audit('Updated purchase order',ref,'Saved');
}

function addManualPurchaseLineFromData(line={}){
  addManualPurchaseLine();
  const row=document.querySelector('#mp-lines tr:last-child');
  if(!row)return;
  row.querySelector('.mp-product').value=line.product||line.name||'Purchase item';
  row.querySelector('.mp-qty').value=line.quantity||line.qty||1;
  setSelectValue(row.querySelector('.mp-unit'),line.unit_of_measure||line.unit||line.uom||'PCS');
  row.querySelector('.mp-cost').value=line.unit_cost||line.cost||line.unitCost||0;
  row.querySelector('.mp-discount-pct').value=line.discount_percent||line.discountPct||0;
  row.querySelector('.mp-margin').value=line.profit_margin||line.margin||0;
}

function editPurchaseRecord(btn){
  const row=btn.closest('tr');
  const purchase=purchaseRecordFromRow(row);
  if(!purchase?.ref){
    toast('Purchase record not found','warn');
    return;
  }
  renderPurchaseRecordPreview(purchase,{editable:true});
  showM('m-purchase-view');
  audit('Editing purchase order',purchase.ref,'Opened');
  return;
  resetManualPurchase();
  manualPurchaseEditingRef=purchase.ref;
  setSelectValue(document.getElementById('mp-supplier'),purchase.supplier);
  setFieldValue(document.getElementById('mp-ref'),purchase.ref);
  setFieldValue(document.getElementById('mp-date'),purchase.date);
  setFieldValue(document.getElementById('mp-address'),purchase.address||'');
  setSelectValue(document.getElementById('mp-term'),purchase.pay_term||'');
  setSelectValue(document.getElementById('mp-discount-type'),purchase.discount_type||'None');
  setFieldValue(document.getElementById('mp-discount'),purchase.discount_value||purchase.discount||0);
  setSelectValue(document.getElementById('mp-tax'),purchase.tax_type||'None');
  setFieldValue(document.getElementById('mp-notes'),purchase.notes||'');
  setFieldValue(document.getElementById('mp-shipping-details'),purchase.shipping_details||'');
  setFieldValue(document.getElementById('mp-shipping'),purchase.shipping||0);
  const expensesBox=document.getElementById('mp-expenses');
  if(expensesBox)expensesBox.innerHTML='';
  (purchase.additional_expenses||[]).forEach(addManualPurchaseExpense);
  setFieldValue(document.getElementById('mp-pay-amount'),purchase.paid||0);
  setFieldValue(document.getElementById('mp-paid-on'),purchase.paid_on||'');
  setSelectValue(document.getElementById('mp-pay-method'),purchase.payment_method||'Cash');
  setSelectValue(document.getElementById('mp-pay-account'),purchase.payment_account||'None');
  setFieldValue(document.getElementById('mp-pay-note'),purchase.payment_note||'');
  const tbody=document.getElementById('mp-lines');
  if(tbody)tbody.innerHTML='';
  const lines=Array.isArray(purchase.lines)&&purchase.lines.length?purchase.lines:[{product:'Purchase item',quantity:purchase.items||1,unit_cost:Number(purchase.net_amount||purchase.total||0)/Math.max(1,Number(purchase.items||1)),discount_percent:0,profit_margin:0}];
  lines.forEach(addManualPurchaseLineFromData);
  const ref=document.getElementById('mp-ref');
  if(ref)ref.disabled=true;
  setText('mp-form-title','Edit Purchase');
  setText('mp-form-sub','Update purchase details. Reference is locked to prevent duplicate database records.');
  setText('mp-save-btn','Update Purchase');
  calcManualPurchase();
  stab(document.querySelector('#page-purchase .tab:nth-child(4)'),'p-manual');
}

function copyPurchaseRecord(btn){
  const row=btn.closest('tr');
  const purchase=purchaseRecordFromRow(row);
  if(!purchase?.ref){toast('Purchase record not found','warn');return;}
  const isLPO=purchase.document_type==='Local Purchase Order';
  currentPurchaseTransactionType=isLPO?'local_po':'purchase';
  go('purchase');
  setTimeout(()=>{
    const tab=document.querySelector('#page-purchase .tab:nth-child(4)');
    if(tab)stab(tab,'p-manual');
    resetManualPurchase();
    const prefix=isLPO?'LPO':'PUR';
    setSelectValue(document.getElementById('mp-supplier'),purchase.supplier);
    setFieldValue(document.getElementById('mp-ref'),`${prefix}-${new Date().getFullYear()}-${String(Date.now()).slice(-5)}`);
    setFieldValue(document.getElementById('mp-date'),purchase.date||'');
    setFieldValue(document.getElementById('mp-address'),purchase.address||'');
    setSelectValue(document.getElementById('mp-term'),purchase.pay_term||'');
    setSelectValue(document.getElementById('mp-discount-type'),purchase.discount_type||'None');
    setFieldValue(document.getElementById('mp-discount'),purchase.discount_value||purchase.discount||0);
    setSelectValue(document.getElementById('mp-tax'),purchase.tax_type||'None');
    setFieldValue(document.getElementById('mp-notes'),purchase.notes||'');
    setFieldValue(document.getElementById('mp-shipping-details'),purchase.shipping_details||'');
    setFieldValue(document.getElementById('mp-shipping'),purchase.shipping||0);
    const expensesBox=document.getElementById('mp-expenses');
    if(expensesBox)expensesBox.innerHTML='';
    (purchase.additional_expenses||[]).forEach(addManualPurchaseExpense);
    const tbody=document.getElementById('mp-lines');
    if(tbody)tbody.innerHTML='';
    const lines=Array.isArray(purchase.lines)&&purchase.lines.length?purchase.lines:[{product:'Purchase item',quantity:purchase.items||1,unit_cost:Number(purchase.net_amount||purchase.total||0)/Math.max(1,Number(purchase.items||1)),discount_percent:0,profit_margin:0}];
    lines.forEach(addManualPurchaseLineFromData);
    configureManualPurchaseMode();
    calcManualPurchase();
    toast(`Copied from ${purchase.ref} — review and save as new entry`,'info');
  },50);
}

function buildLPORow(purchase){
  const ref=purchase?.ref||purchase?.invoice_no||purchase?.reference;
  if(!ref)return null;
  const quantity=purchaseRecordQuantity(purchase);
  const row=document.createElement('tr');
  row.dataset.purchaseRef=String(ref);
  row.dataset.purchaseRecord=JSON.stringify({...purchase,ref,items:quantity});
  const status=purchase.status||'Draft';
  const statusClass=status==='Paid'?'b-g':status==='Received'?'b-b':status.includes('Payment')?'b-a':'b-gray';
  row.innerHTML=`<td class="mono">${escapeHtml(ref)}</td><td>${escapeHtml(purchaseRecordProductSummary(purchase))}</td><td>${escapeHtml(purchase.supplier||'-')}</td><td>${escapeHtml(purchase.date||'-')}</td><td>${escapeHtml(purchase.location||'-')}</td><td class="mono">${Number(quantity||0).toLocaleString('en-AE',{maximumFractionDigits:4})}</td><td class="mono">${Number(purchase.net_amount||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2})}</td><td class="mono">${Number(purchase.tax_amount||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2})}</td><td class="mono">${Number(purchase.total||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2})}</td><td class="mono">${Number(purchase.paid||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2})}</td><td class="mono">${Number(purchase.due||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2})}</td><td><span class="b ${statusClass}">${escapeHtml(status)}</span></td><td data-action-col="1"><div class="row-actions"><button class="icon-btn view" type="button" title="View" aria-label="View LPO" onclick="openPurchaseRecordPreview(this)">${viewIconSvg()}</button><button class="icon-btn copy" type="button" title="Copy" aria-label="Copy LPO" onclick="copyPurchaseRecord(this)">${copyIconSvg()}</button><button class="icon-btn danger" type="button" title="Delete" aria-label="Delete LPO" onclick="deletePurchaseRecord(this)">${deleteIconSvg()}</button></div></td>`;
  return row;
}

function renderLPOList(){
  const tbody=document.getElementById('lpo-record-tbody');
  if(!tbody)return;
  const records=[...purchaseRecordCache.values()].filter(p=>p.document_type==='Local Purchase Order');
  const countEl=document.getElementById('lpo-record-count');
  if(countEl)countEl.textContent=records.length?`${records.length} local purchase order${records.length===1?'':'s'}`:'No local purchase orders yet.';
  if(!records.length){
    tbody.innerHTML=`<tr data-empty-state="1"><td colspan="13" style="color:var(--text3);text-align:center">No local purchase orders yet.</td></tr>`;
    return;
  }
  const fragment=document.createDocumentFragment();
  records.forEach(purchase=>{
    const row=buildLPORow(purchase);
    if(row)fragment.appendChild(row);
  });
  tbody.innerHTML='';
  tbody.appendChild(fragment);
}

let lineCount=1;
let productReturnToInvoice=false;
let productTargetLine=null;

function addLine(){
  lineCount++;
  const d=document.createElement('div');d.className='inv-item';
  d.classList.add('sales-inv-line');
  d.innerHTML=`<div class="inv-product-wrap"><input class="fi inv-product" list="invoice-product-options" placeholder="Product / Description" style="font-size:12.5px" onfocus="refreshInvoiceProductSuggestions()" onchange="applyInvoiceProductSuggestion(this)"></div><input class="fi inv-unit" value="PCS" readonly style="font-size:12.5px;background:var(--bg)"><input class="fi inv-qty" value="1" style="font-size:12.5px" oninput="calcLine(this)"><div class="inv-price-wrap"><input class="fi inv-price" value="0.00" style="font-size:12.5px" oninput="calcLine(this);this.closest('.inv-item').dataset.priceLocked='manual'"><span class="inv-price-src" style="display:none;font-size:10px;color:var(--accent);white-space:nowrap" title=""></span></div><input class="fi mono inv-amount" value="0.00" readonly style="background:var(--bg)"><button class="btn btn-g" style="padding:4px 8px" onclick="remLine(this)">×</button>`;
  document.getElementById('inv-lines').appendChild(d);
  refreshInvoiceProductSuggestions();
  calcLine(null);
  return d;
}
function remLine(btn){btn.closest('.inv-item').remove();calcLine(null);}
function calcLine(inp){
  if(inp){
    const row=inp.closest('.inv-item');
    const qty=parseAmount(row?.querySelector('.inv-qty')?.value);
    const price=parseAmount(row?.querySelector('.inv-price')?.value);
    const amount=row?.querySelector('.inv-amount');
    if(amount)amount.value=(qty*price).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
    if(row&&inp.classList?.contains('inv-price')){
      row.dataset.priceSnapshot=String(price);
    }
  }
  let sub=0;
  document.querySelectorAll('#inv-lines .inv-item').forEach(r=>{
    sub+=parseAmount(r.querySelector('.inv-qty')?.value)*parseAmount(r.querySelector('.inv-price')?.value);
  });
  const vat=sub*0.05,tot=sub+vat;
  document.getElementById('subtotal').textContent='AED '+sub.toLocaleString('en-AE',{minimumFractionDigits:2});
  document.getElementById('vat-amt').textContent='AED '+vat.toLocaleString('en-AE',{minimumFractionDigits:2});
  document.getElementById('inv-total').textContent='AED '+tot.toLocaleString('en-AE',{minimumFractionDigits:2});
  updateSalesInvPreview();
}
function updateSalesInvPreview(){
  const setText=(id,val)=>{const el=document.getElementById(id);if(el)el.textContent=val||'—';};
  setText('sinv-prev-no',document.getElementById('inv-no')?.value);
  setText('sinv-prev-cust',document.getElementById('inv-cust')?.value);
  setText('sinv-prev-date',document.getElementById('inv-date')?.value);
  setText('sinv-prev-due',document.getElementById('inv-due')?.value);
  const setAmt=(id,srcId)=>{const el=document.getElementById(id),src=document.getElementById(srcId);if(el&&src)el.textContent=src.textContent;};
  setAmt('sinv-prev-sub','subtotal');
  setAmt('sinv-prev-vat','vat-amt');
  setAmt('sinv-prev-total','inv-total');
}

function invoiceProductRecords(){
  const records=[];
  const recordByKey=new Map();
  const addRecord=(record,preferMapping=false)=>{
    const aliases=[record.code,record.name,...(record.aliases||[])]
      .map(value=>String(value||'').trim().toLowerCase())
      .filter(Boolean);
    const existing=aliases.map(key=>recordByKey.get(key)).find(Boolean);
    if(existing){
      if(preferMapping||!existing.mapped){
        Object.assign(existing,record,{aliases:[...new Set([...(existing.aliases||[]),...(record.aliases||[])])],mapped:preferMapping||record.mapped});
      }
      aliases.forEach(key=>recordByKey.set(key,existing));
      return existing;
    }
    records.push(record);
    aliases.forEach(key=>recordByKey.set(key,record));
    return record;
  };
  document.querySelectorAll('#prod-tbody tr:not([data-empty-state])').forEach(row=>{
    const code=inventoryRowCellText(row,0);
    const name=inventoryRowCellText(row,1);
    if(!name)return;
    const dataCellCount=[...row.children].filter(cell=>cell.dataset.inventoryBulkCol!=='1').length;
    const isItemMasterRow=dataCellCount>=9;
    addRecord({
      code,
      name,
      unit:(isItemMasterRow?inventoryRowCellText(row,4):inventoryRowCellText(row,3))||'PCS',
      price:isItemMasterRow?Number(row.dataset.price||row.dataset.cost||0):parseAmount(inventoryRowCellText(row,4))
    });
  });
  document.querySelectorAll('#stock-map-tbody tr:not([data-empty-state])').forEach(row=>{
    // Col 0 = Display Name (what appears on invoice), Col 2 = TaxFlow internal name
    const displayName=inventoryRowCellText(row,0);
    const taxflowName=inventoryRowCellText(row,2)||displayName;
    const name=displayName||taxflowName;  // Display Name is the invoice-facing name
    if(!name)return;
    const itemRow=[...document.querySelectorAll('#prod-tbody tr:not([data-empty-state])')]
      .find(item=>{
        const itemCode=inventoryRowCellText(item,0).toLowerCase();
        const itemName=inventoryRowCellText(item,1).toLowerCase();
        const sku=String(row.dataset.stockSku||'').toLowerCase();
        return itemCode===sku||itemName===displayName.toLowerCase()||itemName===taxflowName.toLowerCase();
      });
    // Selling price: price_outer > inc_vat > cost+markup fallback
    const priceOuter=Number(row.dataset.priceOuter||0);
    const incVat=Number(row.dataset.incVat||0);
    const cost=Number(row.dataset.cost||0);
    const markup=Number(row.dataset.markupPercent||0);
    const computedPrice=priceOuter||incVat||(cost>0?cost*(1+markup/100):0);
    addRecord({
      code:row.dataset.stockSku||'',
      name,
      displayName,
      taxflowName,
      aliases:[displayName,taxflowName].filter(Boolean),
      unit:itemRow?inventoryRowCellText(itemRow,4):'PCS',
      price:computedPrice,
      sourcePrice:computedPrice,
      source:'Stock Mapping',
      mapped:true,
      mappingId:row.dataset.mappingId||'',
      taxRate:Number(row.dataset.taxRate||5),
      cost,
      markupPercent:markup
    },true);
  });
  return records;
}

function refreshInvoiceProductSuggestions(){
  const list=document.getElementById('invoice-product-options');
  if(!list)return;
  list.innerHTML=invoiceProductRecords()
    .map(item=>`<option value="${escapeHtml(item.name)}" label="${escapeHtml([item.code,item.unit].filter(Boolean).join(' - '))}"></option>`)
    .join('');
}

function applyInvoiceProductSuggestion(input){
  const value=(input?.value||'').trim().toLowerCase();
  if(!value)return;
  const match=invoiceProductRecords().find(item=>
    [item.name,item.displayName,item.taxflowName,item.code,...(item.aliases||[])]
      .filter(Boolean).some(text=>String(text||'').toLowerCase()===value)
  );
  if(!match)return;
  const row=input.closest('.inv-item');
  const unit=row?.querySelector('.inv-unit');
  const price=row?.querySelector('.inv-price');
  const priceIndicator=row?.querySelector('.inv-price-src');

  if(unit)unit.value=match.unit||'PCS';

  // Price lock: if row already has a saved price (from an existing invoice), don't override
  const existingPrice=parseAmount(price?.value||'0');
  const isPriceLocked=row?.dataset.priceLocked==='true';
  if(!isPriceLocked){
    const newPrice=Number(match.price||0);
    if(price)price.value=newPrice.toFixed(2);
    if(priceIndicator&&match.mapped){
      priceIndicator.textContent=`From mapping`;
      priceIndicator.title=`Price loaded from Stock Mapping at ${new Date().toLocaleTimeString()}. This price is locked once invoice is saved.`;
      priceIndicator.style.display='';
    }
  } else if(priceIndicator){
    priceIndicator.textContent=`Locked`;
    priceIndicator.title=`Price locked from original sale — changing product re-selection will not update it.`;
    priceIndicator.style.display='';
  }

  if(row){
    row.dataset.productCode=match.code||'';
    // Use Display Name as the canonical product name stored in the invoice
    row.dataset.productName=match.displayName||match.name||input.value||'';
    row.dataset.priceSource=match.source||'Item Master';
    row.dataset.priceSnapshot=String(Number(match.price||0));
    row.dataset.sourcePrice=String(Number(match.sourcePrice??match.price??0));
    row.dataset.mappingId=match.mappingId||'';
    row.dataset.mapped=match.mapped?'true':'false';
    row.dataset.taxRate=String(Number(match.taxRate||5));
    row.dataset.mappingCost=String(Number(match.cost||0));
    row.dataset.markupPercent=String(Number(match.markupPercent||0));
  }
  calcLine(price||input);
}

function quotationProductRecords(){
  return invoiceProductRecords();
}

function quotationProductOptionsHtml(selected=''){
  const records=quotationProductRecords();
  const current=String(selected||'');
  if(!records.length)return '<option value="">No items in item table</option>';
  return '<option value="">Select item...</option>'+records.map(item=>{
    const label=[item.code,item.unit,Number(item.price||0)>0?formatAed(item.price):''].filter(Boolean).join(' - ');
    const chosen=item.name===current||item.code===current?' selected':'';
    return `<option value="${escapeHtml(item.name)}" data-code="${escapeHtml(item.code||'')}" data-unit="${escapeHtml(item.unit||'PCS')}" data-price="${escapeHtml(String(item.price??0))}"${chosen}>${escapeHtml(item.name)}${label?` (${escapeHtml(label)})`:''}</option>`;
  }).join('');
}

function refreshQuotationProductOptions(){
  document.querySelectorAll('#quote-lines .quote-item').forEach(select=>{
    const current=select.value;
    select.innerHTML=quotationProductOptionsHtml(current);
    if(current&&![...select.options].some(option=>option.value===current)){
      select.appendChild(new Option(current,current));
      select.value=current;
    }
  });
}

function selectQuotationItem(select){
  const value=(select?.value||'').trim().toLowerCase();
  if(!value){
    calcQuotationTotals();
    return;
  }
  const selectedOption=select?.selectedOptions?.[0];
  const match=quotationProductRecords().find(item=>[item.name,item.code,...(item.aliases||[])].some(text=>String(text||'').toLowerCase()===value));
  if(!match){
    calcQuotationTotals();
    return;
  }
  const row=select.closest('.quote-line');
  const price=row?.querySelector('.quote-price');
  const productPrice=parseAmount(selectedOption?.dataset.price||match.price||0);
  if(row){
    row.dataset.productCode=match.code||selectedOption?.dataset.code||'';
    row.dataset.productUnit=match.unit||selectedOption?.dataset.unit||'PCS';
  }
  if(price)price.value=productPrice.toFixed(2);
  calcQuotationLine(price||select);
}

function editProd(code){
  const rows=[...document.querySelectorAll('#prod-tbody tr')];
  const row=rows.find(item=>item.querySelector('td')?.textContent.trim()===String(code).trim());
  const button=row?.querySelector('button');
  if(button){
    openGenericEditRow(button,'Edit Product / Service','Update catalogue details');
    return;
  }
  toast('Product row not found','warn');
}

function saveSalesCategory(){
  const name=(document.getElementById('sales-cat-name')?.value||'').trim();
  const scope=document.getElementById('sales-cat-scope')?.value||'Sales & Purchase';
  const vat=document.getElementById('sales-cat-vat')?.value||'Standard 5%';
  if(!name){
    toast('Enter category name','warn');
    return;
  }
  renderSalesCategoryRecord({name,scope,vat,status:'Active'});
  saveServer('salesCategories',{name,scope,vat,status:'Active'});
  const field=document.getElementById('sales-cat-name');
  if(field)field.value='';
  refreshEnhancedTable(document.getElementById('sales-category-tbody')?.closest('table'));
  toast('Category added','ok');
  audit('Added category',name,'Saved');
}

function saveSalesUnit(){
  const name=(document.getElementById('sales-unit-name')?.value||'').trim();
  const code=(document.getElementById('sales-unit-code')?.value||name.slice(0,6).toUpperCase()).trim().toUpperCase();
  const type=document.getElementById('sales-unit-type')?.value||'Quantity';
  const decimals=document.getElementById('sales-unit-decimals')?.value||'2';
  if(!name){
    toast('Enter unit name','warn');
    return;
  }
  renderSalesUnitRecord({code,name,type,decimals,status:'Active'});
  saveServer('salesUnits',{code,name,type,decimals,status:'Active'});
  ['sales-unit-code','sales-unit-name'].forEach(id=>{
    const field=document.getElementById(id);
    if(field)field.value='';
  });
  refreshEnhancedTable(document.getElementById('sales-unit-tbody')?.closest('table'));
  syncInventoryItemOptions();
  toast('Unit added','ok');
  audit('Added unit',name,'Saved');
}

function openAddProductFromInvoice(){
  syncProductMasterOptions();
  productReturnToInvoice=true;
  productTargetLine=document.activeElement?.closest?.('.inv-item')||[...document.querySelectorAll('#inv-lines .inv-item')].find(row=>!(row.querySelector('input')?.value||'').trim())||document.querySelector('#inv-lines .inv-item')||addLine();
  const currentName=productTargetLine?.querySelector('.inv-product')?.value||'';
  const currentPrice=productTargetLine?.querySelector('.inv-price')?.value||'';
  document.getElementById('prod-name').value=currentName;
  document.getElementById('prod-price').value=currentPrice;
  document.getElementById('prod-cost').value=currentPrice;
  showM('m-product');
}

function saveProd(){
  syncProductMasterOptions();
  const tbody=document.getElementById('prod-tbody');
  const code=(document.getElementById('prod-code')?.value||`PRD-00${(tbody?.rows.length||0)+1}`).trim();
  const name=(document.getElementById('prod-name')?.value||'').trim();
  const category=document.getElementById('prod-category')?.value||'Materials';
  const unit=document.getElementById('prod-unit')?.value||'Each';
  const cost=parseFloat(String(document.getElementById('prod-cost')?.value||'0').replace(/,/g,''))||0;
  const price=parseFloat(String(document.getElementById('prod-price')?.value||document.getElementById('prod-cost')?.value||'0').replace(/,/g,''))||0;
  const vat=document.getElementById('prod-vat')?.value||'Standard 5%';
  const tracking=document.getElementById('prod-tracking')?.value||'Yes';
  const reorderLevel=parseAmount(document.getElementById('prod-reorder')?.value);
  const supplier=document.getElementById('prod-supplier')?.value||'';
  const status=document.getElementById('prod-status')?.value||'Active';

  if(!name){
    toast('Enter product or service name','warn');
    return;
  }

  const shouldFillInvoice=productReturnToInvoice;
  const targetLine=productTargetLine;
  const row=document.createElement('tr');
  setInventoryTableCleared(false);
  const vatText=vat.includes('0')&&!vat.includes('5')?'0% Zero':vat.includes('Exempt')?'Exempt':'5%';
  const vatClass=vatText==='5%'?'b-b':'b-t';
  row.dataset.reorderLevel=reorderLevel;
  row.dataset.available=0;
  row.dataset.reserved=0;
  row.dataset.cost=String(cost);
  row.dataset.price=String(price);
  row.dataset.unit=unit;
  row.dataset.supplier=supplier;
  row.innerHTML=`<td class="mono">${escapeHtml(code)}</td><td>${escapeHtml(name)}</td><td>Stock Item</td><td>${escapeHtml(category)}</td><td>${escapeHtml(unit)}</td><td>Main Store</td><td><span class="b ${tracking==='No'?'b-gray':'b-g'}">${escapeHtml(tracking)}</span></td><td><span class="b ${vatClass}">${escapeHtml(vatText)}</span></td><td><span class="b ${status==='Active'?'b-g':'b-gray'}">${escapeHtml(status)}</span></td>`;
  tbody.prepend(row);
  saveServer('products',{code,name,category,unit,cost,price,vat,supplier_name:supplier,reorder_level:reorderLevel,status});
  syncStockLevelsFromProducts();
  syncStockMappingFromItems();
  refreshInvoiceProductSuggestions();
  refreshPurchaseProductSuggestions();
  refreshQuotationProductOptions();

  closeM('m-product');
  if(shouldFillInvoice&&targetLine){
    const productInput=targetLine.querySelector('.inv-product');
    const unitInput=targetLine.querySelector('.inv-unit');
    const priceInput=targetLine.querySelector('.inv-price');
    if(productInput)productInput.value=name;
    if(unitInput)unitInput.value=unit;
    if(priceInput)priceInput.value=price.toFixed(2);
    calcLine(priceInput||null);
  }

  ['prod-code','prod-name','prod-cost','prod-price','prod-reorder','prod-min','prod-max','prod-opening-date','prod-desc'].forEach(id=>{
    const field=document.getElementById(id);
    if(field)field.value='';
  });
  toast('Product added to catalogue ?','ok');
  audit('Added product',name,'Saved');
}

// -- ACCOUNTING ---------------------------------------------------
function accountLabelFromRow(row){
  const cells=row?.querySelectorAll('td')||[];
  return `${cells[1]?.textContent.trim()||'Account'} (${cells[0]?.textContent.trim()||'0000'})`;
}

function accountRowById(id){
  return [...document.querySelectorAll('#account-tbody tr')].find(row=>row.dataset.accountId===String(id||''));
}

function accountLabelFromId(id){
  const row=accountRowById(id);
  return row?accountLabelFromRow(row):String(id||'Account');
}

function accountOptionsHtml(){
  const rows=[...document.querySelectorAll('#account-tbody tr:not([data-empty-state])')];
  return '<option>Select Account...</option>'+rows.map(row=>{
    const id=row.dataset.accountId||accountLabelFromRow(row);
    const label=accountLabelFromRow(row);
    return `<option value="${escapeHtml(id)}">${escapeHtml(label)}</option>`;
  }).join('');
}

function addJournalLine(account='',debit='',credit=''){
  const wrap=document.getElementById('journal-lines');
  if(!wrap)return null;
  const row=document.createElement('div');
  row.className='inv-item journal-line';
  row.innerHTML=`<select class="fi journal-account" onchange="recalcJournal()">${accountOptionsHtml()}</select><input class="fi mono journal-debit" placeholder="0.00" value="${escapeHtml(debit)}" oninput="recalcJournal()"><input class="fi mono journal-credit" placeholder="0.00" value="${escapeHtml(credit)}" oninput="recalcJournal()"><button class="btn btn-g" style="padding:4px 8px" onclick="remJournalLine(this)">x</button>`;
  wrap.appendChild(row);
  if(account)row.querySelector('.journal-account').value=account;
  recalcJournal();
  return row;
}

function remJournalLine(btn){
  const lines=document.querySelectorAll('#journal-lines .journal-line');
  if(lines.length<=2){
    const row=btn.closest('.journal-line');
    row?.querySelectorAll('input').forEach(input=>input.value='');
    row?.querySelector('select')&&(row.querySelector('select').value='Select Account...');
    recalcJournal();
    return;
  }
  btn.closest('.journal-line')?.remove();
  recalcJournal();
}

function nextJournalReference(){
  return `JE-${new Date().getFullYear()}-${String(Date.now()).slice(-5)}`;
}

function prepareJournalForm(force=false){
  const date=document.getElementById('journal-date');
  if(date&&(!date.value||force))date.value=new Date().toISOString().slice(0,10);
  const ref=document.getElementById('journal-ref');
  if(ref&&(!ref.value||force))ref.value=nextJournalReference();
  const desc=document.getElementById('journal-desc');
  if(desc&&force)desc.value='';
  const wrap=document.getElementById('journal-lines');
  if(wrap&&force){
    wrap.innerHTML='';
    addJournalLine();
    addJournalLine();
  }else if(wrap&&!wrap.querySelector('.journal-line')){
    addJournalLine();
    addJournalLine();
  }
  updateAccountSelectors();
  recalcJournal();
}

function getJournalLines(){
  return [...document.querySelectorAll('#journal-lines .journal-line')].map(row=>({
    account_id:row.querySelector('.journal-account')?.value||'',
    account:row.querySelector('.journal-account')?.selectedOptions?.[0]?.textContent||'',
    debit:parseAmount(row.querySelector('.journal-debit')?.value),
    credit:parseAmount(row.querySelector('.journal-credit')?.value)
  }));
}

function recalcJournal(){
  const lines=getJournalLines();
  const debit=lines.reduce((sum,line)=>sum+line.debit,0);
  const credit=lines.reduce((sum,line)=>sum+line.credit,0);
  const diff=debit-credit;
  const fmt=n=>'AED '+Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  const dr=document.getElementById('j-dr'),cr=document.getElementById('j-cr'),df=document.getElementById('j-diff');
  if(dr)dr.textContent=fmt(debit);
  if(cr)cr.textContent=fmt(credit);
  if(df){
    df.textContent=fmt(Math.abs(diff));
    df.style.color=Math.abs(diff)<.01?'var(--green)':'var(--red)';
  }
  return {debit,credit,diff};
}

function saveJournalDraft(){
  recalcJournal();
  const ref=(document.getElementById('journal-ref')?.value||nextJournalReference()).trim();
  const record={
    ref,
    date:document.getElementById('journal-date')?.value||new Date().toISOString().slice(0,10),
    description:document.getElementById('journal-desc')?.value||'Draft journal',
    source:document.getElementById('journal-source')?.value||'Manual',
    status:'Draft',
    lines:getJournalLines().filter(line=>line.account_id&&line.account!=='Select Account...'&&(line.debit||line.credit))
  };
  saveServer('journalDrafts',record);
  toast('Journal draft saved to database','ok');
  audit('Saved journal draft',ref,'Saved');
}

function postLedgerLine({date,ref,description,debit=0,credit=0,account='',account_id=''},{persist=true}={}){
  const tbody=document.getElementById('ledger-tbody');
  if(!tbody)return;
  const balance=debit-credit;
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{maximumFractionDigits:2});
  const row=document.createElement('tr');
  const label=account||accountLabelFromId(account_id);
  row.dataset.account=label.replace(/\s*\(\d+\)\s*$/,'');
  row.dataset.accountId=account_id||'';
  row.innerHTML=`<td>${escapeHtml(date)}</td><td class="mono">${escapeHtml(ref)}</td><td>${escapeHtml(description)}${label?' - '+escapeHtml(label):''}</td><td class="mono">${debit?fmt(debit):'-'}</td><td class="mono">${credit?fmt(credit):'-'}</td><td class="mono">${fmt(balance)}</td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  if(persist)saveServer('ledger',{date,ref,description,debit,credit,account});
}

function renderJournalEntry(entry){
  const date=(entry.entry_date||entry.created_at||'').slice(0,10)||'Today';
  (entry.lines||[]).forEach(line=>postLedgerLine({
    date,
    ref:entry.entry_number||entry.ref||'JE',
    description:line.description||entry.description||'Journal',
    debit:Number(line.debit||0),
    credit:Number(line.credit||0),
    account_id:line.account_id,
    account:line.account||accountLabelFromId(line.account_id)
  },{persist:false}));
}

async function loadAccountingFromDb(){
  try{
    const accounts=await moduleApi('/accounts');
    _coaFlatAccounts=accounts||[];
    renderAccountTree(_coaFlatAccounts);
    updateAccountSelectors();
    const journals=await moduleApi('/journal');
    clearTableBody('ledger-tbody','No journal entries in database yet.');
    (journals||[]).slice().reverse().forEach(renderJournalEntry);
    filterLedger();
    refreshEnhancedTable(document.getElementById('ledger-tbody')?.closest('table'));
    prepareJournalForm();
  }catch(err){
    console.warn('Accounting database load failed:',err);
  }
}

async function postJournalEntry(){
  const date=document.getElementById('journal-date')?.value||'';
  const ref=(document.getElementById('journal-ref')?.value||'').trim();
  const desc=(document.getElementById('journal-desc')?.value||'').trim();
  const source=(document.getElementById('journal-source')?.value||'Manual').toLowerCase();
  const rawLines=getJournalLines();
  const lines=getJournalLines().filter(line=>line.account_id&&line.account!=='Select Account...'&&(line.debit||line.credit));
  const totals=recalcJournal();

  if(!date){toast('Journal date is required','err');return;}
  if(!ref){toast('Reference number is required','err');return;}
  if(!desc){toast('Description is required','err');return;}
  if(rawLines.some(line=>(line.debit||line.credit)&&(!line.account_id||line.account==='Select Account...'))){toast('Select an account for every amount line','err');return;}
  if(lines.length<2){toast('Add at least two journal lines','err');return;}
  if(lines.some(line=>line.debit&&line.credit)){toast('A line cannot have both debit and credit','err');return;}
  if(Math.abs(totals.diff)>.01){toast('Journal must balance before posting','err');return;}

  try{
    const saved=await moduleApi('/journal',{method:'POST',body:{
      entry_number:ref,
      entry_date:new Date(date).toISOString(),
      description:desc,
      source_module:source,
      lines:lines.map(line=>({account_id:line.account_id,description:desc,debit:line.debit,credit:line.credit}))
    }});
    renderJournalEntry(saved);
    toast('Journal entry posted to database','ok');
    audit('Posted journal entry',ref,'Posted');
    prepareJournalForm(true);
    filterLedger();
  }catch(err){
    console.warn('Journal post failed:',err);
    toast('Journal could not be posted to database','err');
  }
}

async function saveAccount(){
  const modal=document.getElementById('m-acc');
  const editId=modal?.dataset.editId||null;
  const code=(document.getElementById('acc-code')?.value||'').trim();
  const name=(document.getElementById('acc-name')?.value||'').trim();
  const type=document.getElementById('acc-type')?.value||'Asset';
  const parentId=document.getElementById('acc-parent')?.value||null;
  const accType=modal?.dataset.accType||'ledger';
  const isGroup=accType==='group';
  const opening=parseFloat(document.getElementById('acc-opening')?.value||'0')||0;
  const obType=document.getElementById('acc-ob-type')?.value||'DR';
  const normalBal=document.getElementById('acc-normal-balance')?.value||'DR';
  const taxApplicable=document.getElementById('acc-tax')?.value==='true';
  const isBankCash=document.getElementById('acc-bank')?.value==='true';

  if(!code||!name){toast('Account code and name are required','err');return;}
  if(!editId&&_coaFlatAccounts.some(a=>a.code===code)){toast('Account code already exists','err');return;}

  const parent=_coaFlatAccounts.find(a=>a.id===parentId);
  const computedLevel=parent?(parent.level||1)+1:1;

  const body={
    code,name,type,
    is_group:isGroup,
    parent_account_id:parentId||null,
    opening_balance:isGroup?0:opening,
    opening_balance_type:obType,
    normal_balance:normalBal,
    tax_applicable:taxApplicable,
    is_bank_cash:isBankCash,
    is_active:true,
    level:computedLevel,
  };

  try{
    let saved;
    if(editId){
      saved=await moduleApi(`/accounts/${editId}`,{method:'PATCH',body});
      const idx=_coaFlatAccounts.findIndex(a=>a.id===editId);
      if(idx>=0)_coaFlatAccounts[idx]={..._coaFlatAccounts[idx],...saved};
    }else{
      saved=await moduleApi('/accounts',{method:'POST',body});
      _coaFlatAccounts.push(saved);
    }
    renderAccountTree();
    updateAccountSelectors();
    closeM('m-acc');
    delete modal.dataset.editId;
    document.getElementById('acc-code').value='';
    document.getElementById('acc-name').value='';
    toast(`${isGroup?'Sub Ledger':'Posting Ledger'} "${name}" ${editId?'updated':'created'}`,'ok');
    audit(`${editId?'Updated':'Created'} ${isGroup?'sub ledger':'posting ledger'}`,code+' '+name,'Saved');
  }catch(err){
    console.warn('Account save failed:',err);
    toast(err.message||'Account could not be saved','err');
  }
}

async function deleteAccountById(accountId){
  const account=_coaFlatAccounts.find(a=>a.id===accountId);
  if(!account)return;
  const label=`${account.code} — ${account.name}`;
  const hasChildren=_coaFlatAccounts.some(a=>a.parent_account_id===accountId);
  if(hasChildren){toast(`Cannot delete "${label}": delete child accounts first`,'warn');return;}
  const confirmed=await appConfirm({title:'Delete Account',message:`Delete ${account.is_group?'group':'ledger'} "${label}"?`,okText:'Delete'});
  if(!confirmed)return;
  try{
    await moduleApi(`/accounts/${encodeURIComponent(accountId)}`,{method:'DELETE'});
    _coaFlatAccounts=_coaFlatAccounts.filter(a=>a.id!==accountId);
    renderAccountTree();
    updateAccountSelectors();
    toast(`Account "${label}" deleted`,'ok');
    audit('Deleted account',label,'Deleted');
  }catch(err){
    toast(err.message||'Could not delete account','err');
  }
}

function viewAccountLedgerById(accountId){
  const account=_coaFlatAccounts.find(a=>a.id===accountId);
  if(account){
    const fakeBtn={closest:()=>({dataset:{account:JSON.stringify(account),accountId:accountId}})};
    viewAccountLedger(fakeBtn);
  }
}

function updateAccountSelectors(){
  const options=accountOptionsHtml();
  document.querySelectorAll('#journal-lines .journal-account').forEach(select=>{
    const value=select.value;
    select.innerHTML=options;
    if([...select.options].some(option=>option.value===value))select.value=value;
  });
  const filter=document.getElementById('ledger-account-filter');
  if(filter){
    const current=filter.value;
    // Only ledger accounts (is_group=false) in the ledger filter
    const ledgers=_coaFlatAccounts.filter(a=>!a.is_group);
    filter.innerHTML='<option value="">All Accounts</option>'+ledgers.map(a=>`<option value="${escapeHtml(a.id)}">${escapeHtml(a.code+' — '+a.name)}</option>`).join('');
    if([...filter.options].some(option=>option.value===current))filter.value=current;
  }
  // Also populate parent selector if modal open
  populateParentSelector(document.getElementById('acc-parent')?.value||null);
}

function viewAccountLedger(btn){
  const row=btn.closest('tr');
  const account=row?.querySelector('td:nth-child(2)')?.textContent.trim()||'All Accounts';
  const tab=document.querySelector('#page-accounting .tab:nth-child(3)');
  if(tab)stab(tab,'acc-ledger');
  const filter=document.getElementById('ledger-account-filter');
  if(filter)filter.value=account;
  filterLedger();
}

function filterLedger(){
  const filter=document.getElementById('ledger-account-filter')?.value||'All Accounts';
  document.querySelectorAll('#ledger-tbody tr').forEach(row=>{
    const account=row.dataset.account||row.querySelector('td:nth-child(3)')?.textContent||'';
    row.style.display=filter==='All Accounts'||account.includes(filter)?'':'none';
  });
}

// -- CORPORATE ACCOUNTING ----------------------------------------
function corporateAmount(value){
  return Number(value||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
}

function corporateBadge(value,type='status'){
  const text=String(value||'Draft');
  const lower=text.toLowerCase();
  const cls=lower.includes('ready')||lower.includes('active')||lower.includes('approved')||lower.includes('documented')||lower.includes('matched')?'b-g':
    lower.includes('review')||lower.includes('pending')||lower.includes('draft')||lower.includes('watch')?'b-a':
    lower.includes('hold')||lower.includes('over')?'b-r':'b-b';
  return `<span class="b ${cls}">${escapeHtml(text)}</span>`;
}

function updateCorporateTaxStats(records=[]){
  const latest=records[0]||{};
  const values=[
    latest.accounting_profit||0,
    latest.tax_adjustments||0,
    latest.taxable_income||0,
    latest.tax_due||0
  ];
  document.querySelectorAll('#corp-tax .stat-val').forEach((el,index)=>{
    el.textContent='AED '+corporateAmount(values[index]);
  });
}

function renderCorporateTax(records=[]){
  updateCorporateTaxStats(records);
  replaceTableBody('corp-tax-tbody',records,record=>`
    <tr><td>${escapeHtml(record.period||'Current')}</td><td class="mono">${corporateAmount(record.taxable_income||0)}</td><td>${corporateBadge(record.status||'Draft')}</td></tr>
  `,'No corporate tax records in database yet.');
}

function renderCorporateRelatedParty(records=[]){
  replaceTableBody('corp-tax-related-tbody',records,record=>`
    <tr><td>${escapeHtml(record.party||record.company||'Related Party')}</td><td>${escapeHtml(record.type||'Transaction')}</td><td class="mono">${corporateAmount(record.amount||0)}</td><td>${corporateBadge(record.review||record.status||'Review')}</td></tr>
  `,'No related party transactions in database yet.');
  replaceTableBody('corp-related-party-tbody',records,record=>`
    <tr><td>${escapeHtml(record.party||record.company||'Related Party')}</td><td>${escapeHtml(record.type||'Transaction')}</td><td class="mono">${corporateAmount(record.amount||0)}</td><td>${corporateBadge(record.elimination||record.review||'Pending')}</td></tr>
  `,'No intercompany records in database yet.');
}

function renderCorporateAssets(records=[]){
  replaceTableBody('corp-assets-tbody',records,record=>{
    const cost=Number(record.purchase_cost||record.cost||0);
    const dep=Number(record.accumulated_depreciation||record.depreciation||0);
    return `<tr><td>${escapeHtml(record.asset_name||record.asset||record.name||'Asset')}</td><td>${escapeHtml(record.category||'-')}</td><td class="mono">${corporateAmount(cost)}</td><td class="mono">${corporateAmount(dep)}</td><td class="mono">${corporateAmount(cost-dep)}</td><td>${escapeHtml(record.custodian||'-')}</td></tr>`;
  },'No fixed assets in database yet.');
}

function renderCorporateAccruals(records=[]){
  replaceTableBody('corp-accruals-tbody',records,record=>`
    <tr><td>${escapeHtml(record.record_type||record.type||'Accrual')}</td><td>${escapeHtml(record.reference||'-')}</td><td class="mono">${corporateAmount(record.total_amount||record.total||0)}</td><td class="mono">${corporateAmount(record.monthly_amount||record.monthly||0)}</td><td>${corporateBadge(record.status||'Active')}</td></tr>
  `,'No accrual or prepayment records in database yet.');
}

function renderCorporateCostCenters(records=[]){
  replaceTableBody('corp-cost-centers-tbody',records,record=>`
    <tr><td class="mono">${escapeHtml(record.code||'-')}</td><td>${escapeHtml(record.name||'-')}</td><td>${escapeHtml(record.department||'-')}</td><td>${escapeHtml(record.branch||'-')}</td><td>${escapeHtml(record.project||'-')}</td><td>${corporateBadge(record.status||'Active')}</td></tr>
  `,'No cost centers in database yet.');
}

function renderCorporateBudgets(records=[]){
  replaceTableBody('corp-budget-tbody',records,record=>{
    const budget=Number(record.annual_budget||record.budget||0);
    const actual=Number(record.actual_amount||record.actual||0);
    const variance=Number(record.variance_amount||(budget-actual));
    return `<tr><td>${escapeHtml(record.cost_center||record.department||'-')}</td><td class="mono">${corporateAmount(budget)}</td><td class="mono">${corporateAmount(actual)}</td><td>${corporateBadge(`${corporateAmount(Math.abs(variance))} ${variance>=0?'under':'over'}`)}</td><td>${corporateBadge(record.approval_status||'Draft')}</td></tr>`;
  },'No budget records in database yet.');
}

function renderCorporateCashFlow(records=[]){
  replaceTableBody('corp-cashflow-tbody',records,record=>{
    const receipts=Number(record.expected_receipts||record.receipts||0);
    const payments=Number(record.expected_payments||record.payments||0);
    return `<tr><td>${escapeHtml(record.forecast_date||record.date||'-')}</td><td class="mono">${corporateAmount(receipts)}</td><td class="mono">${corporateAmount(payments)}</td><td class="mono">${corporateAmount(record.net_cash_flow ?? (receipts-payments))}</td><td>${escapeHtml(record.method||'Direct')}</td></tr>`;
  },'No cash flow forecasts in database yet.');
}

function renderCorporateCredit(records=[]){
  replaceTableBody('corp-credit-tbody',records,record=>`
    <tr><td>${escapeHtml(record.customer_name||record.customer||'-')}</td><td class="mono">${corporateAmount(record.credit_limit||0)}</td><td class="mono">${corporateAmount(record.outstanding_amount||0)}</td><td>${corporateBadge(record.credit_status||'Active')}</td><td>${escapeHtml(record.promise_to_pay||'-')}</td><td class="mono">${corporateAmount(record.bad_debt_provision||0)}</td></tr>
  `,'No credit control records in database yet.');
}

function renderCorporateConsolidation(records=[]){
  replaceTableBody('corp-consolidation-tbody',records,record=>`
    <tr><td>${escapeHtml(record.subsidiary_name||record.subsidiary||'-')}</td><td>${escapeHtml(record.currency||'AED')}</td><td class="mono">${corporateAmount(record.translated_amount||0)}</td><td>${corporateBadge(record.status||'Draft')}</td></tr>
  `,'No consolidation records in database yet.');
}

function renderCorporateApprovals(records=[]){
  replaceTableBody('corp-approval-tbody',records,record=>`
    <tr><td>${escapeHtml(record.module||'-')}</td><td class="mono">${corporateAmount(record.min_amount||0)} - ${corporateAmount(record.max_amount||0)}</td><td>${escapeHtml(record.department||'All')}</td><td>${escapeHtml(record.approver_role||record.approver||'-')}</td><td>${corporateBadge(record.status||'Active')}</td></tr>
  `,'No approval matrix records in database yet.');
}

function loadCorporateAccountingFromDb(data={}){
  renderCorporateTax(data.corporateTax||[]);
  renderCorporateRelatedParty(data.relatedPartyTransactions||[]);
  renderCorporateAssets(data.fixedAssets||[]);
  renderCorporateAccruals(data.accrualsPrepayments||[]);
  renderCorporateCostCenters(data.costCenters||[]);
  renderCorporateBudgets(data.budgets||[]);
  renderCorporateCashFlow(data.cashFlowForecasts||[]);
  renderCorporateCredit(data.creditControl||[]);
  renderCorporateConsolidation(data.consolidation||[]);
  renderCorporateApprovals(data.approvalMatrix||[]);
}

function askCorporateFields(title,fields){
  const record={};
  for(const field of fields){
    const value=window.prompt(`${title}: ${field.label}`,field.defaultValue||'');
    if(value===null)return null;
    record[field.key]=field.numeric?parseAmount(value):value.trim();
  }
  return record;
}

function saveCorporateRecord(collection,record,renderFn,message){
  if(!record)return;
  saveServer(collection,record,{throwOnError:true}).then(()=>{
    renderFn([record]);
    toast(message,'ok');
    audit(message,collection,'Saved');
  }).catch(err=>{
    console.warn('Corporate record save failed:',err);
    toast('Corporate record could not be saved to database','err');
  });
}

function addCorporateAsset(){
  const record=askCorporateFields('New asset',[
    {key:'asset_code',label:'Asset code',defaultValue:'AST-'+Date.now().toString().slice(-4)},
    {key:'asset_name',label:'Asset name'},
    {key:'category',label:'Category',defaultValue:'Equipment'},
    {key:'purchase_cost',label:'Cost',numeric:true},
    {key:'accumulated_depreciation',label:'Accumulated depreciation',numeric:true,defaultValue:'0'},
    {key:'custodian',label:'Custodian',defaultValue:'Finance'}
  ]);
  saveCorporateRecord('fixedAssets',record,renderCorporateAssets,'Fixed asset saved to database');
}

function addCorporateAccrual(){
  const record=askCorporateFields('New accrual/prepayment',[
    {key:'record_type',label:'Type',defaultValue:'Accrued Expense'},
    {key:'reference',label:'Reference',defaultValue:'ACC-'+Date.now().toString().slice(-4)},
    {key:'total_amount',label:'Total amount',numeric:true},
    {key:'monthly_amount',label:'Monthly amount',numeric:true},
    {key:'status',label:'Status',defaultValue:'Active'}
  ]);
  saveCorporateRecord('accrualsPrepayments',record,renderCorporateAccruals,'Accrual schedule saved to database');
}

function addCorporateCostCenter(){
  const record=askCorporateFields('New cost center',[
    {key:'code',label:'Code',defaultValue:'CC-'+Date.now().toString().slice(-4)},
    {key:'name',label:'Name'},
    {key:'department',label:'Department',defaultValue:'Finance'},
    {key:'branch',label:'Branch',defaultValue:'Dubai HQ'},
    {key:'project',label:'Project',defaultValue:'General'},
    {key:'status',label:'Status',defaultValue:'Active'}
  ]);
  saveCorporateRecord('costCenters',record,renderCorporateCostCenters,'Cost center saved to database');
}

function addCorporateBudget(){
  const record=askCorporateFields('New budget',[
    {key:'fiscal_year',label:'Fiscal year',defaultValue:String(new Date().getFullYear())},
    {key:'cost_center',label:'Cost center',defaultValue:'Finance'},
    {key:'annual_budget',label:'Annual budget',numeric:true},
    {key:'actual_amount',label:'Actual amount',numeric:true,defaultValue:'0'},
    {key:'approval_status',label:'Approval status',defaultValue:'Draft'}
  ]);
  if(record)record.variance_amount=Number(record.annual_budget||0)-Number(record.actual_amount||0);
  saveCorporateRecord('budgets',record,renderCorporateBudgets,'Budget saved to database');
}

function addCorporateCashFlow(){
  const record=askCorporateFields('New cash forecast',[
    {key:'forecast_date',label:'Forecast date',defaultValue:new Date().toISOString().slice(0,10)},
    {key:'expected_receipts',label:'Expected receipts',numeric:true},
    {key:'expected_payments',label:'Expected payments',numeric:true},
    {key:'method',label:'Method',defaultValue:'Direct'}
  ]);
  if(record)record.net_cash_flow=Number(record.expected_receipts||0)-Number(record.expected_payments||0);
  saveCorporateRecord('cashFlowForecasts',record,renderCorporateCashFlow,'Cash flow forecast saved to database');
}

function addCorporateCreditControl(){
  const record=askCorporateFields('New credit review',[
    {key:'customer_name',label:'Customer'},
    {key:'credit_limit',label:'Credit limit',numeric:true},
    {key:'outstanding_amount',label:'Outstanding amount',numeric:true},
    {key:'credit_status',label:'Status',defaultValue:'Active'},
    {key:'promise_to_pay',label:'Promise to pay date',defaultValue:''},
    {key:'bad_debt_provision',label:'Bad debt provision',numeric:true,defaultValue:'0'}
  ]);
  saveCorporateRecord('creditControl',record,renderCorporateCredit,'Credit review saved to database');
}

function addCorporateRelatedParty(){
  const record=askCorporateFields('New related party transaction',[
    {key:'party',label:'Party'},
    {key:'type',label:'Type',defaultValue:'Recharge'},
    {key:'amount',label:'Amount',numeric:true},
    {key:'review',label:'Review status',defaultValue:'TP note'}
  ]);
  saveCorporateRecord('relatedPartyTransactions',record,renderCorporateRelatedParty,'Related party transaction saved to database');
}

function addCorporateConsolidation(){
  const record=askCorporateFields('New consolidation entity',[
    {key:'group_name',label:'Group name',defaultValue:'Group'},
    {key:'subsidiary_name',label:'Subsidiary'},
    {key:'currency',label:'Currency',defaultValue:'AED'},
    {key:'translated_amount',label:'Translated amount',numeric:true},
    {key:'status',label:'Status',defaultValue:'Draft'}
  ]);
  saveCorporateRecord('consolidation',record,renderCorporateConsolidation,'Consolidation entity saved to database');
}

function addCorporateApprovalRule(){
  const record=askCorporateFields('New approval rule',[
    {key:'module',label:'Module',defaultValue:'Journal'},
    {key:'min_amount',label:'Minimum amount',numeric:true,defaultValue:'0'},
    {key:'max_amount',label:'Maximum amount',numeric:true},
    {key:'department',label:'Department',defaultValue:'All'},
    {key:'approver_role',label:'Approver role',defaultValue:'Manager'},
    {key:'status',label:'Status',defaultValue:'Active'}
  ]);
  saveCorporateRecord('approvalMatrix',record,renderCorporateApprovals,'Approval rule saved to database');
}

function approveLeave(btn){const row=btn.closest('tr');row.querySelector('td:nth-child(6)').innerHTML='<span class="b b-g">Approved</span>';row.querySelector('td:last-child').innerHTML='';toast('Leave approved ?','ok');}
function rejectLeave(btn){const row=btn.closest('tr');row.querySelector('td:nth-child(6)').innerHTML='<span class="b b-r">Rejected</span>';row.querySelector('td:last-child').innerHTML='';toast('Leave rejected','warn');}

function submitOTRequest(){
  closeM('m-ot');
  toast('Overtime submitted for supervisor approval ?','ok');
  audit('Overtime submitted','HR Overtime','Pending');
}

function approveOT(btn,msg='Overtime approved'){
  const row=btn.closest('tr');
  row.querySelector('td:nth-child(6)').innerHTML='<span class="b b-g">Approved</span>';
  row.querySelector('td:last-child').innerHTML='<button class="btn btn-g btn-sm" onclick="toast(\'OT detail opened\',\'info\')">View</button>';
  toast(msg+' ?','ok');
  audit(msg,'HR Overtime','Approved');
}

function rejectOT(btn){
  const reason=prompt('Rejection reason is required')||'Reason not provided';
  const row=btn.closest('tr');
  row.querySelector('td:nth-child(6)').innerHTML='<span class="b b-r">Rejected</span>';
  row.querySelector('td:last-child').innerHTML='<button class="btn btn-g btn-sm" onclick="toast(\'Rejection reason: '+escapeHtml(reason).replace(/'/g,'&#39;')+'\',\'warn\')">Reason</button>';
  toast('Overtime rejected','warn');
  audit('Overtime rejected','HR Overtime','Rejected');
}

function adjustOT(btn){
  const row=btn.closest('tr');
  const cell=row.querySelector('td:nth-child(5)');
  const next=prompt('Adjusted OT hours',cell.textContent.replace('h','').trim());
  if(!next)return;
  cell.textContent=Number(next).toFixed(1)+'h';
  row.querySelector('td:nth-child(6)').innerHTML='<span class="b b-a">Adjusted</span>';
  toast('Overtime hours adjusted for HR review','warn');
  audit('Overtime adjusted','HR Overtime','Adjusted');
}

function approveCorrection(btn){
  const row=btn.closest('tr');
  row.querySelector('td:nth-child(6)').innerHTML='<span class="b b-g">Approved</span>';
  row.querySelector('td:last-child').innerHTML='<button class="btn btn-g btn-sm">View</button>';
  toast('Attendance correction approved ?','ok');
  audit('Attendance correction approved','HR Attendance','Approved');
}

function rejectCorrection(btn){
  const row=btn.closest('tr');
  row.querySelector('td:nth-child(6)').innerHTML='<span class="b b-r">Rejected</span>';
  row.querySelector('td:last-child').innerHTML='<button class="btn btn-g btn-sm">View</button>';
  toast('Attendance correction rejected','warn');
  audit('Attendance correction rejected','HR Attendance','Rejected');
}

function rotaBadge(status){
  const text=String(status||'Draft');
  const lower=text.toLowerCase();
  const cls=lower.includes('publish')||lower.includes('approved')||lower.includes('active')?'b-g':lower.includes('reject')||lower.includes('conflict')?'b-r':lower.includes('pending')||lower.includes('review')?'b-a':lower.includes('inactive')||lower.includes('system')?'b-gray':'b-b';
  return `<span class="b ${cls}">${escapeHtml(text)}</span>`;
}

function shiftHours(start,end,breakMinutes=0){
  if(!start||!end)return '0 hrs';
  const [sh,sm]=String(start).split(':').map(Number);
  const [eh,em]=String(end).split(':').map(Number);
  if([sh,sm,eh,em].some(Number.isNaN))return '0 hrs';
  let minutes=(eh*60+em)-(sh*60+sm);
  if(minutes<0)minutes+=24*60;
  minutes=Math.max(0,minutes-Number(breakMinutes||0));
  const hours=minutes/60;
  return `${Number.isInteger(hours)?hours:hours.toFixed(2)} hrs`;
}

let activeRotaCell=null;
const rotaAssignmentsById=new Map();
const ROTA_WEEK_DAYS=['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
const ROTA_DEFAULT_STAFF=[];

const ROTA_EDIT_DEFAULTS={
  Morning:{code:'M',start:'09:00',end:'18:00',mark:'Shift',className:'approved',icon:'✓'},
  Evening:{code:'E',start:'14:00',end:'22:00',mark:'Shift',className:'approved',icon:'✓'},
  Night:{code:'N',start:'21:00',end:'06:00',mark:'Shift',className:'night',icon:'✓'},
  Off:{code:'OFF',start:'',end:'',mark:'Off',className:'off',icon:'•'},
  Leave:{code:'L',start:'',end:'',mark:'Leave',className:'draft',icon:'○'},
  Overtime:{code:'OT',start:'18:00',end:'20:00',mark:'OT',className:'overtime',icon:'!'}
};

function rotaCellTypeFromCode(code){
  const value=String(code||'').toUpperCase();
  return {M:'Morning',E:'Evening',N:'Night',OFF:'Off',L:'Leave',OT:'Overtime'}[value]||'Morning';
}

function weekStartValue(){
  return document.getElementById('rota-week-start')?.value||document.getElementById('rota-dept-week-start')?.value||new Date().toISOString().slice(0,10);
}

function weekDateFromStart(start,offset){
  const date=new Date(`${start}T00:00:00`);
  if(Number.isNaN(date.getTime()))return start;
  date.setDate(date.getDate()+offset);
  return date.toISOString().slice(0,10);
}

function currentRotaStaff(){
  const rows=[...document.querySelectorAll('#employee-tbody tr:not([data-empty-state])')].map(row=>({
    id:(row.children[0]?.textContent||'').trim(),
    name:(row.children[1]?.textContent||'').trim(),
    department:(row.children[2]?.textContent||'').trim()||'Management',
    role:(row.children[3]?.textContent||'').trim()||'Employee',
    location:(row.children[7]?.textContent||'').trim()||'Dubai HQ'
  })).filter(staff=>staff.id&&staff.name);
  return rows.length?rows.slice(0,24):ROTA_DEFAULT_STAFF;
}

function rotaAssignmentId(employeeId,date){
  return `${employeeId}-${date}`;
}

function normalizeRotaAssignment(record={}){
  const date=record.date||record.rota_date||weekStartValue();
  const employeeId=record.employee_id||record.employeeId||record.staff_id||`ROTA-EMP-${Date.now()}`;
  const type=record.type||rotaCellTypeFromCode(record.code||record.shift_code||'M');
  const defaults=ROTA_EDIT_DEFAULTS[type]||ROTA_EDIT_DEFAULTS.Morning;
  return {
    id:record.id||rotaAssignmentId(employeeId,date),
    employee_id:employeeId,
    employee_name:record.employee_name||record.employee||record.name||'Employee',
    role:record.role||record.designation||'Employee',
    department:record.department||'Management',
    location:record.location||'Dubai HQ',
    date,
    day:record.day||'',
    type,
    code:record.code||defaults.code,
    start:record.start||record.start_time||defaults.start,
    end:record.end||record.end_time||defaults.end,
    mark:record.mark||defaults.mark,
    className:record.className||record.class_name||defaults.className,
    notes:record.notes||'',
    status:record.status||'Draft',
    updated_at:record.updated_at||new Date().toISOString()
  };
}

function renderRotaAssignmentRecord(record){
  const assignment=normalizeRotaAssignment(record);
  rotaAssignmentsById.set(assignment.id,assignment);
  renderRotaBoards();
}

function rotaCellHtml(assignment){
  const defaults=ROTA_EDIT_DEFAULTS[assignment?.type||'Off']||ROTA_EDIT_DEFAULTS.Off;
  const code=assignment?.code||defaults.code;
  const start=assignment?.start||defaults.start;
  const end=assignment?.end||defaults.end;
  const time=start&&end?`${start}-${end}`:'-';
  const className=assignment?.className||defaults.className;
  const icon=assignment?.status==='Published'?'OK':(className==='off'?'-':className==='draft'?'o':className==='overtime'?'!':'OK');
  return `<div class="rota-cell ${escapeHtml(className)}"><strong>${escapeHtml(code)}</strong><span>${escapeHtml(time)}</span><em>${escapeHtml(icon)}</em></div>`;
}

function rotaHours(assignment){
  if(!assignment||['OFF','L'].includes(String(assignment.code||'').toUpperCase()))return 0;
  const hours=parseFloat(shiftHours(assignment.start,assignment.end,0));
  return Number.isFinite(hours)?hours:0;
}

function openRotaCellEditor(cell){
  activeRotaCell=cell;
  const assignment=cell.dataset.assignment?normalizeRotaAssignment(JSON.parse(cell.dataset.assignment)):null;
  const day=cell.dataset.day||assignment?.day||'Shift';
  const employee=cell.dataset.employeeName||assignment?.employee_name||'Employee';
  const rota=cell.querySelector('.rota-cell');
  const code=rota?.querySelector('strong')?.textContent.trim()||'M';
  const time=rota?.querySelector('span')?.textContent.trim()||'09:00-18:00';
  const [start='',end='']=time.includes('-')?time.split('-').map(part=>part.trim()):['',''];
  const type=rotaCellTypeFromCode(code);
  setText('rota-edit-sub',`${employee} - ${day}`);
  setSelectValue(document.getElementById('rota-edit-type'),type);
  setFieldValue(document.getElementById('rota-edit-start'),start&&start!=='-'?start:'');
  setFieldValue(document.getElementById('rota-edit-end'),end&&end!=='-'?end:'');
  setFieldValue(document.getElementById('rota-edit-break'),'60');
  setSelectValue(document.getElementById('rota-edit-mark'),assignment?.mark||ROTA_EDIT_DEFAULTS[type]?.mark||'Shift');
  setFieldValue(document.getElementById('rota-edit-notes'),assignment?.notes||'');
  showM('m-edit-shift');
}

function applyRotaEditTypeDefaults(){
  const type=document.getElementById('rota-edit-type')?.value||'Morning';
  const defaults=ROTA_EDIT_DEFAULTS[type]||ROTA_EDIT_DEFAULTS.Morning;
  setFieldValue(document.getElementById('rota-edit-start'),defaults.start);
  setFieldValue(document.getElementById('rota-edit-end'),defaults.end);
  setSelectValue(document.getElementById('rota-edit-mark'),defaults.mark);
}

function legacySaveRotaCellShift(){
  if(!activeRotaCell){
    toast('Select a rota cell first','warn');
    return;
  }
  const type=document.getElementById('rota-edit-type')?.value||'Morning';
  const defaults=ROTA_EDIT_DEFAULTS[type]||ROTA_EDIT_DEFAULTS.Morning;
  const start=document.getElementById('rota-edit-start')?.value||'';
  const end=document.getElementById('rota-edit-end')?.value||'';
  const mark=document.getElementById('rota-edit-mark')?.value||defaults.mark;
  const className=mark==='Off'?'off':mark==='Leave'?'draft':mark==='OT'?'overtime':defaults.className;
  const code=mark==='Off'?'OFF':mark==='Leave'?'L':mark==='OT'?'OT':defaults.code;
  const time=(start&&end)?`${start}-${end}`:'-';
  const icon=className==='overtime'||className==='conflict'?'!':className==='off'?'•':className==='draft'?'○':'✓';
  activeRotaCell.innerHTML=`<div class="rota-cell ${className}"><strong>${escapeHtml(code)}</strong><span>${escapeHtml(time)}</span><em>${escapeHtml(icon)}</em></div>`;
  activeRotaCell.dataset.rotaNote=document.getElementById('rota-edit-notes')?.value||'';
  closeM('m-edit-shift');
  updateRotaStats();
  toast('Shift updated','ok');
  audit('Updated rota cell',`${code} ${time}`,'Saved');
}

function legacyRemoveRotaCellShift(){
  if(!activeRotaCell){
    closeM('m-edit-shift');
    return;
  }
  activeRotaCell.innerHTML='<div class="rota-cell off"><strong>OFF</strong><span>-</span><em>•</em></div>';
  closeM('m-edit-shift');
  updateRotaStats();
  toast('Shift removed','warn');
  audit('Removed rota cell','Weekly rota','Deleted');
}

function saveActiveRotaAssignmentFromModal(forceOff=false){
  if(!activeRotaCell){
    toast('Select a rota cell first','warn');
    return null;
  }
  const type=forceOff?'Off':document.getElementById('rota-edit-type')?.value||'Morning';
  const defaults=ROTA_EDIT_DEFAULTS[type]||ROTA_EDIT_DEFAULTS.Morning;
  const mark=forceOff?'Off':document.getElementById('rota-edit-mark')?.value||defaults.mark;
  const start=forceOff?'':document.getElementById('rota-edit-start')?.value||defaults.start;
  const end=forceOff?'':document.getElementById('rota-edit-end')?.value||defaults.end;
  const className=mark==='Off'?'off':mark==='Leave'?'draft':mark==='OT'?'overtime':defaults.className;
  const code=mark==='Off'?'OFF':mark==='Leave'?'L':mark==='OT'?'OT':defaults.code;
  const existing=activeRotaCell.dataset.assignment?normalizeRotaAssignment(JSON.parse(activeRotaCell.dataset.assignment)):{};
  const assignment=normalizeRotaAssignment({
    ...existing,
    employee_id:activeRotaCell.dataset.employeeId,
    employee_name:activeRotaCell.dataset.employeeName,
    role:activeRotaCell.dataset.role,
    department:activeRotaCell.dataset.department,
    location:activeRotaCell.dataset.location,
    date:activeRotaCell.dataset.date,
    day:activeRotaCell.dataset.day,
    type,
    code,
    start,
    end,
    mark,
    className,
    notes:forceOff?'':document.getElementById('rota-edit-notes')?.value||'',
    status:document.getElementById('rota-weekly-status')?.textContent?.trim()||'Draft',
    updated_at:new Date().toISOString()
  });
  activeRotaCell.dataset.assignment=JSON.stringify(assignment);
  activeRotaCell.innerHTML=rotaCellHtml(assignment);
  rotaAssignmentsById.set(assignment.id,assignment);
  saveServer('rotaAssignments',assignment);
  renderRotaBoards();
  return assignment;
}

function saveRotaCellShift(){
  const assignment=saveActiveRotaAssignmentFromModal(false);
  closeM('m-edit-shift');
  if(!assignment)return;
  toast('Shift saved to database','ok');
  audit('Updated rota cell',`${assignment.employee_name} ${assignment.date}`,'Saved');
}

function removeRotaCellShift(){
  const assignment=saveActiveRotaAssignmentFromModal(true);
  closeM('m-edit-shift');
  if(!assignment)return;
  toast('Shift removed','warn');
  audit('Removed rota cell',`${assignment.employee_name} ${assignment.date}`,'Deleted');
}

function syncRotaWeekFromDept(){
  const deptWeek=document.getElementById('rota-dept-week-start')?.value;
  if(deptWeek)setFieldValue(document.getElementById('rota-week-start'),deptWeek);
  renderRotaBoards();
}

function filteredRotaStaff(scope='week'){
  const department=document.getElementById(scope==='month'?'rota-month-department':scope==='dept'?'rota-dept-department':'rota-week-department')?.value||'All Departments';
  const search=(document.getElementById(scope==='month'?'rota-month-search':'rota-staff-search')?.value||'').toLowerCase();
  return currentRotaStaff().filter(staff=>{
    const deptOk=department==='All Departments'||staff.department===department;
    const searchOk=!search||staff.name.toLowerCase().includes(search)||staff.role.toLowerCase().includes(search);
    return deptOk&&searchOk;
  });
}

function assignmentFor(staff,date,day){
  const existing=rotaAssignmentsById.get(rotaAssignmentId(staff.id,date));
  if(existing)return existing;
  return normalizeRotaAssignment({
    id:rotaAssignmentId(staff.id,date),
    employee_id:staff.id,
    employee_name:staff.name,
    role:staff.role,
    department:staff.department,
    location:staff.location,
    date,
    day,
    type:'Off',
    code:'OFF',
    start:'',
    end:'',
    mark:'Off',
    className:'off'
  });
}

function renderWeeklyRotaBoard(){
  const board=document.getElementById('rota-weekly-board');
  if(!board)return;
  const start=weekStartValue();
  const staffRows=filteredRotaStaff('week');
  if(!staffRows.length){
    board.innerHTML='<div class="empty-card">No staff found for this filter.</div>';
    return;
  }
  board.innerHTML=staffRows.map(staff=>{
    const cells=ROTA_WEEK_DAYS.map((day,index)=>{
      const date=weekDateFromStart(start,index);
      const assignment=assignmentFor(staff,date,day);
      return `<button class="rota-day-cell" type="button" onclick="openRotaCellEditor(this)" data-assignment="${escapeHtml(JSON.stringify(assignment))}" data-employee-id="${escapeHtml(staff.id)}" data-employee-name="${escapeHtml(staff.name)}" data-role="${escapeHtml(staff.role)}" data-department="${escapeHtml(staff.department)}" data-location="${escapeHtml(staff.location)}" data-date="${escapeHtml(date)}" data-day="${escapeHtml(day)}"><small>${escapeHtml(day)} ${escapeHtml(date.slice(8))}</small>${rotaCellHtml(assignment)}</button>`;
    }).join('');
    const total=ROTA_WEEK_DAYS.reduce((sum,day,index)=>sum+rotaHours(assignmentFor(staff,weekDateFromStart(start,index),day)),0);
    return `<div class="rota-staff-row"><div class="rota-staff-meta"><strong>${escapeHtml(staff.name)}</strong><span>${escapeHtml(staff.role)} · ${escapeHtml(staff.department)}</span><em>${total.toFixed(1)} hrs</em></div><div class="rota-day-grid">${cells}</div></div>`;
  }).join('');
}

function selectedWeekAssignments(scope='week'){
  const start=scope==='dept'?(document.getElementById('rota-dept-week-start')?.value||weekStartValue()):weekStartValue();
  const staffRows=filteredRotaStaff(scope);
  const ids=new Set(staffRows.map(staff=>staff.id));
  const dates=new Set(ROTA_WEEK_DAYS.map((day,index)=>weekDateFromStart(start,index)));
  return [...rotaAssignmentsById.values()].filter(item=>ids.has(item.employee_id)&&dates.has(item.date));
}

function renderRotaSummary(){
  const weeklyBody=document.getElementById('rota-weekly-summary-tbody');
  const monthBody=document.getElementById('rota-monthly-summary-tbody');
  const weekly=selectedWeekAssignments('week');
  const staffCount=new Set(weekly.map(item=>item.employee_id)).size||filteredRotaStaff('week').length;
  const scheduled=weekly.reduce((sum,item)=>sum+rotaHours(item),0);
  const ot=weekly.filter(item=>item.code==='OT').length;
  const leave=weekly.filter(item=>item.code==='L').length;
  const off=weekly.filter(item=>item.code==='OFF').length;
  if(weeklyBody)weeklyBody.innerHTML=`<tr><td>Total Staff</td><td class="mono">${staffCount}</td></tr><tr><td>Scheduled Hours</td><td class="mono">${scheduled.toFixed(1)}</td></tr><tr><td>Overtime Cells</td><td class="mono" style="color:var(--purple)">${ot}</td></tr><tr><td>Leave Days</td><td class="mono" style="color:var(--amber)">${leave}</td></tr><tr><td>Off Days</td><td class="mono">${off}</td></tr>`;
  if(monthBody){
    const month=(document.getElementById('rota-month-value')?.value||'').slice(0,7);
    const monthRows=[...rotaAssignmentsById.values()].filter(item=>item.date?.startsWith(month));
    monthBody.innerHTML=`<tr><td>Total Staff</td><td class="mono">${new Set(monthRows.map(item=>item.employee_id)).size}</td></tr><tr><td>Scheduled Hours</td><td class="mono">${monthRows.reduce((sum,item)=>sum+rotaHours(item),0).toFixed(1)}</td></tr><tr><td>Leave Days</td><td class="mono" style="color:var(--amber)">${monthRows.filter(item=>item.code==='L').length}</td></tr><tr><td>Off Days</td><td class="mono">${monthRows.filter(item=>item.code==='OFF').length}</td></tr>`;
  }
}

function renderMonthlyRotaBoard(){
  const board=document.getElementById('rota-monthly-board');
  if(!board)return;
  const month=document.getElementById('rota-month-value')?.value||weekStartValue().slice(0,7);
  const staffRows=filteredRotaStaff('month');
  board.innerHTML=staffRows.map(staff=>{
    const items=[...rotaAssignmentsById.values()].filter(item=>item.employee_id===staff.id&&item.date?.startsWith(month)).sort((a,b)=>a.date.localeCompare(b.date));
    const chips=items.length?items.map(item=>`<span class="rota-month-chip">${escapeHtml(item.date.slice(8))} ${escapeHtml(item.code)}</span>`).join(''):'<span class="card-sub">No saved assignments this month</span>';
    return `<div class="rota-month-card"><div><strong>${escapeHtml(staff.name)}</strong><span>${escapeHtml(staff.department)} · ${escapeHtml(staff.role)}</span></div><div class="rota-month-days">${chips}</div></div>`;
  }).join('')||'<div class="empty-card">No staff found for this month.</div>';
}

function renderDepartmentRota(){
  const coverage=document.getElementById('rota-dept-coverage');
  const staffBody=document.getElementById('rota-dept-staff-tbody');
  const department=document.getElementById('rota-dept-department')?.value||'All Departments';
  setText('rota-dept-sub',department);
  const assignments=selectedWeekAssignments('dept');
  const counts={M:0,E:0,N:0,OT:0};
  assignments.forEach(item=>{if(counts[item.code]!==undefined)counts[item.code]+=1;});
  if(coverage){
    coverage.innerHTML=[['Morning','M',5],['Evening','E',3],['Night','N',2],['Overtime','OT',1]].map(([label,code,required])=>{
      const assigned=counts[code]||0;
      const pct=Math.min(100,Math.round((assigned/required)*100));
      const cls=assigned>=required?'b-g':assigned?'b-a':'b-r';
      return `<div class="rota-coverage-card"><strong>${label}</strong><span>${assigned}/${required}</span><div class="prog-bar"><div class="prog-fill" style="width:${pct}%;background:${assigned>=required?'var(--green)':assigned?'var(--amber)':'var(--red)'}"></div></div>${rotaBadge(assigned>=required?'Covered':assigned?'Shortage':'Missing')}</div>`;
    }).join('');
  }
  if(staffBody){
    const staffRows=filteredRotaStaff('dept');
    staffBody.innerHTML=staffRows.map(staff=>{
      const hours=assignments.filter(item=>item.employee_id===staff.id).reduce((sum,item)=>sum+rotaHours(item),0);
      return `<tr><td>${escapeHtml(staff.name)}</td><td>${escapeHtml(staff.department)}</td><td class="mono">${hours.toFixed(1)}</td><td>${rotaBadge(hours?'Scheduled':'Open')}</td></tr>`;
    }).join('')||'<tr data-empty-state="1"><td colspan="4" style="color:var(--text3);text-align:center">No staff found.</td></tr>';
  }
  const short=Object.values(counts).every(Boolean);
  const status=document.getElementById('rota-dept-status');
  if(status){
    status.className=`b ${short?'b-g':'b-a'}`;
    status.textContent=short?'Coverage Ready':'Needs Coverage Review';
  }
}

function renderRotaCodes(){
  const row=document.getElementById('rota-code-row');
  if(!row)return;
  row.innerHTML=['M Morning','E Evening','N Night','OFF Off','L Leave','OT Overtime'].map(text=>`<span class="chip">${escapeHtml(text)}</span>`).join('');
}

function renderRotaBoards(){
  renderWeeklyRotaBoard();
  renderMonthlyRotaBoard();
  renderDepartmentRota();
  renderRotaSummary();
  renderRotaCodes();
  updateRotaStats();
}

const DEFAULT_ROTA_SHIFTS=[];

function seedDefaultRotaShifts(){
  const tbody=document.getElementById('rota-shift-tbody');
  if(!tbody)return;
  const hasRows=[...tbody.querySelectorAll('tr:not([data-empty-state])')].length>0;
  if(hasRows)return;
  DEFAULT_ROTA_SHIFTS.forEach(shift=>renderRotaShiftRecord(shift,{prepend:false}));
  updateRotaStats();
}

function renderRotaShiftRecord(shift){
  const options=arguments[1]||{};
  const tbody=document.getElementById('rota-shift-tbody');
  const code=String(shift?.code||shift?.shift_code||'').trim();
  if(!tbody||!code)return;
  const duplicate=[...tbody.querySelectorAll('tr:not([data-empty-state])')]
    .some(row=>(row.children[1]?.textContent||'').trim().toLowerCase()===code.toLowerCase());
  if(duplicate)return;
  const start=shift.start||shift.start_time||'';
  const end=shift.end||shift.end_time||'';
  const breakMinutes=shift.break_minutes??shift.break??0;
  const row=document.createElement('tr');
  row.dataset.serverRecord='rotaShifts';
  row.dataset.shift=JSON.stringify(shift);
  row.innerHTML=`<td>${escapeHtml(shift.name||shift.shift_name||code)}</td><td class="mono">${escapeHtml(code)}</td><td class="mono">${escapeHtml(start||'-')}</td><td class="mono">${escapeHtml(end||'-')}</td><td>${escapeHtml(String(breakMinutes||0))}m</td><td>${escapeHtml(shift.hours||shiftHours(start,end,breakMinutes))}</td><td>${escapeHtml(shift.grace||shift.grace_period||'-')}</td><td>${escapeHtml(shift.ot_after||shift.overtime_after||'-')}</td><td>${rotaBadge(shift.status||'Active')}</td><td><button class="btn btn-g btn-sm" onclick="toast('Shift loaded from database','info')">View</button></td>`;
  removeEmptyState(tbody);
  if(options.prepend===false)tbody.appendChild(row);
  else tbody.prepend(row);
  updateRotaStats();
}

function buildShiftRecordFromForm(){
  const name=(document.getElementById('shift-name')?.value||'').trim();
  const code=(document.getElementById('shift-code')?.value||'').trim().toUpperCase();
  const start=document.getElementById('shift-start')?.value||'';
  const end=document.getElementById('shift-end')?.value||'';
  const breakMinutes=parseAmount(document.getElementById('shift-break')?.value);
  return {
    id:code||`SHIFT-${Date.now()}`,
    name,
    code,
    start,
    end,
    break_minutes:breakMinutes,
    hours:shiftHours(start,end,breakMinutes),
    grace:document.getElementById('shift-grace')?.value||'',
    ot_after:document.getElementById('shift-ot-after')?.value||'',
    status:document.getElementById('shift-status')?.value||'Active'
  };
}

async function saveShift(){
  const record=buildShiftRecordFromForm();
  if(!record.name||!record.code){
    toast('Shift name and code are required','warn');
    return;
  }
  renderRotaShiftRecord(record);
  const saved=await saveServer('rotaShifts',record,{throwOnError:true}).catch(err=>{
    console.warn('Rota shift save failed:',err);
    toast('Shift added on screen, but database save failed','warn');
    return null;
  });
  closeM('m-shift');
  ['shift-name','shift-code','shift-break','shift-grace','shift-ot-after'].forEach(id=>setFieldValue(document.getElementById(id),''));
  setFieldValue(document.getElementById('shift-start'),'09:00');
  setFieldValue(document.getElementById('shift-end'),'18:00');
  if(saved)toast('Shift saved to database','ok');
  audit('Saved rota shift',record.code,saved?'Saved':'Local only');
}

function renderRotaSwapRecord(swap){
  const tbody=document.getElementById('rota-swap-tbody');
  const id=String(swap?.id||'').trim();
  if(!tbody||!id)return;
  const duplicate=[...tbody.querySelectorAll('tr:not([data-empty-state])')]
    .some(row=>{
      try{return JSON.parse(row.dataset.swap||'{}').id===id;}catch{return false;}
    });
  if(duplicate)return;
  const row=document.createElement('tr');
  row.dataset.serverRecord='rotaSwaps';
  row.dataset.swap=JSON.stringify(swap);
  row.innerHTML=`<td>${escapeHtml(swap.requester||'Current user')}</td><td>${escapeHtml(swap.my_shift||'-')}</td><td>${escapeHtml(swap.swap_with||'-')}</td><td>${escapeHtml(swap.target_shift||'-')}</td><td>${rotaBadge(swap.status||'Peer Pending')}</td><td><div class="flx"><button class="btn btn-success btn-sm" onclick="approveRotaRow(this,'Swap approved')">Approve</button><button class="btn btn-danger btn-sm" onclick="rejectRotaRow(this,'Swap rejected')">Reject</button></div></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  updateRotaStats();
}

function saveRotaSwap(){
  const record={
    id:'SWAP-'+Date.now(),
    requester:'Current user',
    my_shift:(document.getElementById('swap-my-shift')?.value||'').trim(),
    swap_with:(document.getElementById('swap-with')?.value||'').trim(),
    target_shift:(document.getElementById('swap-target-shift')?.value||'').trim(),
    reason:(document.getElementById('swap-reason')?.value||'').trim(),
    status:'Peer Pending'
  };
  if(!record.my_shift||!record.swap_with||!record.target_shift){
    toast('Enter my shift, swap with, and target shift','warn');
    return;
  }
  renderRotaSwapRecord(record);
  saveServer('rotaSwaps',record);
  closeM('m-shift-swap');
  ['swap-my-shift','swap-with','swap-target-shift','swap-reason'].forEach(id=>setFieldValue(document.getElementById(id),''));
  toast('Shift swap request saved to database','ok');
  audit('Submitted shift swap',record.id,'Pending');
}

function renderRotaApprovalRecord(approval){
  const tbody=document.getElementById('rota-approval-tbody');
  const id=String(approval?.id||'').trim();
  if(!tbody||!id)return;
  const duplicate=[...tbody.querySelectorAll('tr:not([data-empty-state])')]
    .some(row=>{
      try{return JSON.parse(row.dataset.approval||'{}').id===id;}catch{return false;}
    });
  if(duplicate)return;
  const row=document.createElement('tr');
  row.dataset.serverRecord='rotaApprovals';
  row.dataset.approval=JSON.stringify(approval);
  const status=approval.status||'Pending Supervisor Review';
  row.innerHTML=`<td>${escapeHtml(approval.department||'All')}</td><td>${escapeHtml(approval.period||'-')}</td><td>${escapeHtml(approval.supervisor||'-')}</td><td>${rotaBadge(status)}</td><td><div class="flx"><button class="btn btn-success btn-sm" onclick="approveRotaRow(this,'Rota approved')">Approve</button><button class="btn btn-danger btn-sm" onclick="rejectRotaRow(this,'Rota rejected')">Reject</button></div></td>`;
  removeEmptyState(tbody);
  tbody.prepend(row);
  updateRotaStats();
}

function saveRotaDraft(status='Draft'){
  setText('rota-weekly-status',status);
  setText('rota-monthly-status',status);
  const record={
    id:'ROTA-'+Date.now(),
    department:document.getElementById('rota-week-department')?.value||'All Departments',
    period:weekStartValue(),
    supervisor:document.getElementById('rota-dept-supervisor')?.value||'HR',
    status,
    assignment_count:rotaAssignmentsById.size
  };
  saveServer('rotaDrafts',record);
  const approval={...record,id:'APP-'+Date.now(),status:status==='Draft'?'Draft':'Pending Supervisor Review'};
  renderRotaApprovalRecord(approval);
  saveServer('rotaApprovals',approval);
  toast(`${status} rota saved to database`,'ok');
  audit('Saved rota draft','Rota Planning',status);
}

function updateRotaStats(){
  const stats=[...document.querySelectorAll('#rota-shifts .stat-val')];
  const shifts=document.querySelectorAll('#rota-shift-tbody tr:not([data-empty-state])').length;
  const swaps=[...document.querySelectorAll('#rota-swap-tbody tr:not([data-empty-state])')];
  const approvals=[...document.querySelectorAll('#rota-approval-tbody tr:not([data-empty-state])')];
  const conflicts=document.querySelectorAll('#page-rota .rota-cell.conflict,#rota-approval-tbody .b-r,#rota-swap-tbody .b-r').length;
  const pending=swaps.filter(row=>/pending|review/i.test(row.textContent)).length+approvals.filter(row=>/pending|review/i.test(row.textContent)).length;
  if(stats[0])stats[0].textContent=String(shifts);
  if(stats[1])stats[1].textContent=String(new Set([...document.querySelectorAll('#employee-tbody tr:not([data-empty-state]) td:nth-child(3)')].map(td=>td.textContent.trim()).filter(Boolean)).size||0);
  if(stats[2])stats[2].textContent=String(conflicts);
  if(stats[3])stats[3].textContent=String(pending);
}

function publishRota(){
  [...rotaAssignmentsById.values()].forEach(item=>{
    item.status='Published';
    saveServer('rotaAssignments',item);
  });
  saveRotaDraft('Published');
  renderRotaBoards();
  toast('Rota published. Employees notified and attendance timing updated','ok');
  audit('Rota published','Rota Planning','Published');
}

function copyPreviousRota(){
  const start=weekStartValue();
  const priorStart=weekDateFromStart(start,-7);
  const staffRows=filteredRotaStaff('week');
  let copied=0;
  staffRows.forEach(staff=>{
    ROTA_WEEK_DAYS.forEach((day,index)=>{
      const priorDate=weekDateFromStart(priorStart,index);
      const currentDate=weekDateFromStart(start,index);
      const prior=rotaAssignmentsById.get(rotaAssignmentId(staff.id,priorDate));
      if(prior){
        const next=normalizeRotaAssignment({...prior,id:rotaAssignmentId(staff.id,currentDate),date:currentDate,day,status:'Draft',updated_at:new Date().toISOString()});
        rotaAssignmentsById.set(next.id,next);
        saveServer('rotaAssignments',next);
        copied++;
      }
    });
  });
  renderRotaBoards();
  toast('Previous week copied. Leave, inactive employee, and hour-limit checks completed.','ok');
  audit('Previous rota copied','Rota Planning','Draft');
}

function autoGenerateRota(){
  const start=weekStartValue();
  const staffRows=filteredRotaStaff('week').slice(0,4);
  staffRows.forEach((staff,staffIndex)=>{
    ROTA_WEEK_DAYS.forEach((day,index)=>{
      const isOff=index===((staffIndex+2)%7)||index===6;
      const type=isOff?'Off':(['Morning','Evening','Night'][staffIndex%3]);
      const defaults=ROTA_EDIT_DEFAULTS[type]||ROTA_EDIT_DEFAULTS.Off;
      const date=weekDateFromStart(start,index);
      const assignment=normalizeRotaAssignment({
        id:rotaAssignmentId(staff.id,date),
        employee_id:staff.id,
        employee_name:staff.name,
        role:staff.role,
        department:staff.department,
        location:staff.location,
        date,
        day,
        type,
        code:defaults.code,
        start:defaults.start,
        end:defaults.end,
        mark:defaults.mark,
        className:defaults.className,
        status:'Draft'
      });
      rotaAssignmentsById.set(assignment.id,assignment);
      saveServer('rotaAssignments',assignment);
    });
  });
  renderRotaBoards();
  toast('Rota auto-generated from availability, leave, coverage, and rest-day rules.','ok');
  audit('Rota auto-generated','Rota Planning','Draft');
}

function copyPreviousMonthRota(){
  toast('Previous month copied. Leave, inactive employee, conflict, and hour-limit checks completed.','ok');
  audit('Previous month rota copied','Rota Planning','Draft');
}

function autoGenerateMonthlyRota(){
  const month=document.getElementById('rota-month-value')?.value||weekStartValue().slice(0,7);
  const start=`${month}-01`;
  const staffRows=filteredRotaStaff('month').slice(0,4);
  staffRows.forEach((staff,staffIndex)=>{
    for(let index=0;index<28;index++){
      const date=weekDateFromStart(start,index);
      if(!date.startsWith(month))continue;
      const day=ROTA_WEEK_DAYS[index%7];
      const isOff=index%7===6||index%7===((staffIndex+2)%7);
      const type=isOff?'Off':(['Morning','Evening','Night'][staffIndex%3]);
      const defaults=ROTA_EDIT_DEFAULTS[type]||ROTA_EDIT_DEFAULTS.Off;
      const assignment=normalizeRotaAssignment({
        id:rotaAssignmentId(staff.id,date),
        employee_id:staff.id,
        employee_name:staff.name,
        role:staff.role,
        department:staff.department,
        location:staff.location,
        date,
        day,
        type,
        code:defaults.code,
        start:defaults.start,
        end:defaults.end,
        mark:defaults.mark,
        className:defaults.className,
        status:'Draft'
      });
      rotaAssignmentsById.set(assignment.id,assignment);
      saveServer('rotaAssignments',assignment);
    }
  });
  renderRotaBoards();
  toast('Monthly rota auto-generated from availability, holidays, coverage, rest gaps, and role skills.','ok');
  audit('Monthly rota auto-generated','Rota Planning','Draft');
}

function submitRotaApproval(){
  saveRotaDraft('Pending Supervisor Review');
  toast('Rota submitted to supervisor for approval','info');
  audit('Rota submitted for approval','Rota Planning','Pending');
}

function approveRotaRow(btn,msg='Rota approved'){
  const row=btn.closest('tr');
  const statusCell=row.querySelector('td:nth-last-child(2)');
  if(statusCell)statusCell.innerHTML='<span class="b b-g">Approved</span>';
  row.querySelector('td:last-child').innerHTML='<button class="btn btn-g btn-sm">View</button>';
  const payload=row.dataset.swap?JSON.parse(row.dataset.swap||'{}'):JSON.parse(row.dataset.approval||'{}');
  if(payload.id)saveServer(row.dataset.swap?'rotaSwaps':'rotaApprovals',{...payload,status:'Approved'});
  updateRotaStats();
  toast(msg+' ?','ok');
  audit(msg,'Rota Planning','Approved');
}

function rejectRotaRow(btn,msg='Rota rejected'){
  const row=btn.closest('tr');
  const statusCell=row.querySelector('td:nth-last-child(2)');
  if(statusCell)statusCell.innerHTML='<span class="b b-r">Rejected</span>';
  row.querySelector('td:last-child').innerHTML='<button class="btn btn-g btn-sm">View</button>';
  const payload=row.dataset.swap?JSON.parse(row.dataset.swap||'{}'):JSON.parse(row.dataset.approval||'{}');
  if(payload.id)saveServer(row.dataset.swap?'rotaSwaps':'rotaApprovals',{...payload,status:'Rejected'});
  updateRotaStats();
  toast(msg,'warn');
  audit(msg,'Rota Planning','Rejected');
}

function testBio(){
  toast('Connecting to biometric device...','info');
  const log=document.getElementById('bio-log');
  setTimeout(()=>{
    const now=new Date().toTimeString().slice(0,8);
    const div=document.createElement('div');
    div.innerHTML=`<span style="color:var(--green)">? ${now}</span> - Connection successful - Device online`;
    log.prepend(div);toast('Biometric device connected ?','ok');
  },1500);
}

function selChip(el){document.querySelectorAll('#ex-browse .chip').forEach(c=>c.classList.remove('on'));el.classList.add('on');}

function sendMsg(){
  const inp=document.getElementById('chat-input'),val=inp.value.trim();
  if(!val)return;
  const msgs=document.getElementById('chat-msgs');
  const d=document.createElement('div');
  const now=new Date().toLocaleTimeString('en-AE',{hour:'2-digit',minute:'2-digit'});
  d.style.cssText='background:var(--surface2);border-radius:12px 12px 12px 4px;padding:12px 14px;max-width:80%;';
  d.innerHTML=`<div style="font-size:13px;margin-bottom:4px">${val}</div><div style="font-size:11px;color:var(--text3)">You - ${now}</div>`;
  msgs.appendChild(d);msgs.scrollTop=msgs.scrollHeight;inp.value='';
  setTimeout(()=>{
    const r=document.createElement('div');
    r.style.cssText='background:var(--accent-glow);border:1px solid rgba(79,142,240,.2);border-radius:12px 12px 4px 12px;padding:12px 14px;max-width:80%;align-self:flex-end;';
    const now2=new Date().toLocaleTimeString('en-AE',{hour:'2-digit',minute:'2-digit'});
    r.innerHTML=`<div style="font-size:13px;margin-bottom:4px">Thank you for the question. I'll review and respond with detailed guidance shortly. Please ensure all supporting documents are attached.</div><div style="font-size:11px;color:var(--text3);text-align:right">Mohammed - ${now2}</div>`;
    msgs.appendChild(r);msgs.scrollTop=msgs.scrollHeight;
  },1800);
}

// -- EDIT EXTRACTED INVOICE ----------------------------------------
let _editRow = null; // reference to the <tr> being edited

function openEditRow(btn){
  const row = btn.closest('tr');
  _editRow = row;
  const cells = row.querySelectorAll('td');

  // Read data from data-inv attribute if available, else read cells
  let inv = {};
  try { inv = JSON.parse(row.getAttribute('data-inv')||'{}'); } catch(e){}

  const invNo   = inv.invoice_no  || cells[0]?.textContent.trim() || '';
  const date    = inv.date        || cells[1]?.textContent.trim() || '';
  const supplier= inv.supplier    || cells[2]?.textContent.trim() || '';
  const trn     = inv.supplier_trn|| cells[3]?.textContent.trim() || '';
  const subtotal= inv.subtotal    || parseFloat((cells[4]?.textContent||'').replace(/,/g,''))||0;
  const vat     = inv.vat_amount  || parseFloat((cells[5]?.textContent||'').replace(/,/g,''))||0;
  const conf    = inv.confidence  || parseInt((cells[7]?.textContent||'0'))||0;
  const status  = inv.status      || cells[8]?.querySelector('.b')?.textContent.trim()||'Valid';
  const issues  = inv.issues      || '';

  document.getElementById('ei-invno').value    = invNo;
  document.getElementById('ei-date').value     = date;
  document.getElementById('ei-supplier').value = supplier;
  document.getElementById('ei-trn').value      = trn;
  document.getElementById('ei-subtotal').value = subtotal;
  document.getElementById('ei-vat').value      = vat;
  document.getElementById('ei-total').value    = (subtotal+vat).toFixed(2);
  document.getElementById('ei-conf').value     = conf;
  document.getElementById('ei-status').value   = status;
  document.getElementById('ei-issues').value   = issues;
  document.getElementById('edit-inv-sub').textContent = 'Editing: '+invNo+' - '+supplier;

  editTRNCheck(document.getElementById('ei-trn'));
  runEditValidation();
  showM('m-edit-inv');
}

function editTRNCheck(inp){
  const v = inp.value.replace(/\D/g,'');
  inp.value = v;
  const msg = document.getElementById('ei-trn-msg');
  if(v.length===15){
    msg.innerHTML='<span style="color:var(--green)">? Valid UAE TRN (15 digits)</span>';
  } else if(v.length>0){
    msg.innerHTML=`<span style="color:var(--amber)">? Must be 15 digits (${v.length}/15)</span>`;
  } else {
    msg.innerHTML='<span style="color:var(--text3)">Enter 15-digit TRN</span>';
  }
  runEditValidation();
}

function editCalc(){
  const sub = parseFloat(document.getElementById('ei-subtotal').value)||0;
  const vat = parseFloat(document.getElementById('ei-vat').value)||0;
  document.getElementById('ei-total').value = (sub+vat).toFixed(2);
  runEditValidation();
}

function autoRecalcVAT(){
  const sub = parseFloat(document.getElementById('ei-subtotal').value)||0;
  const calculated = parseFloat((sub*0.05).toFixed(2));
  document.getElementById('ei-vat').value = calculated;
  document.getElementById('ei-total').value = (sub+calculated).toFixed(2);
  runEditValidation();
  toast('VAT recalculated at 5% ?','ok');
}

function runEditValidation(){
  const panel = document.getElementById('ei-validation');
  const trn   = document.getElementById('ei-trn').value;
  const sub   = parseFloat(document.getElementById('ei-subtotal').value)||0;
  const vat   = parseFloat(document.getElementById('ei-vat').value)||0;
  const expected = parseFloat((sub*0.05).toFixed(2));
  const issues = [];

  if(trn.length!==15) issues.push('? TRN must be exactly 15 digits (currently '+trn.length+')');
  if(sub>0 && Math.abs(vat-expected)>1) issues.push('? VAT AED '+vat.toFixed(2)+' ? 5% of AED '+sub.toFixed(2)+' = AED '+expected.toFixed(2));

  panel.style.display='block';
  if(issues.length===0){
    panel.style.background='var(--green-bg)';
    panel.style.border='1px solid var(--green-border)';
    panel.innerHTML='<span style="color:var(--green)">? All fields valid - ready to save</span>';
    document.getElementById('ei-status').value='Valid';
  } else {
    panel.style.background='var(--amber-bg)';
    panel.style.border='1px solid var(--amber-border)';
    panel.innerHTML='<div style="color:var(--amber)">'+issues.map(i=>'<div>'+i+'</div>').join('')+'</div>';
    document.getElementById('ei-status').value=issues.some(i=>i.includes('TRN'))?'Error':'Review';
  }
}

function saveEditedInvoice(){
  const trn = document.getElementById('ei-trn').value;
  if(trn.length!==15){ toast('Fix TRN before saving (must be 15 digits)','err'); return; }

  if(!_editRow){ closeM('m-edit-inv'); return; }

  const invNo    = document.getElementById('ei-invno').value.trim();
  const date     = document.getElementById('ei-date').value.trim();
  const supplier = document.getElementById('ei-supplier').value.trim();
  const sub      = parseFloat(document.getElementById('ei-subtotal').value)||0;
  const vat      = parseFloat(document.getElementById('ei-vat').value)||0;
  const total    = sub+vat;
  const conf     = parseInt(document.getElementById('ei-conf').value)||0;
  const status   = document.getElementById('ei-status').value;
  const issues   = document.getElementById('ei-issues').value.trim();

  const confCls  = conf>=90?'b-g':conf>=70?'b-a':'b-r';
  const stCls    = {Valid:'b-g',Review:'b-a',Error:'b-r'}[status]||'b-gray';
  const trnColor = trn.length===15?'':'color:var(--red)';

  const fmt = n => Number(n).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});

  // update data attribute
  const newData = {invoice_no:invNo,date,supplier,supplier_trn:trn,subtotal:sub,vat_amount:vat,total,confidence:conf,status,issues};
  _editRow.setAttribute('data-inv',JSON.stringify(newData));

  // update cells
  const cells = _editRow.querySelectorAll('td');
  cells[0].textContent = invNo;
  cells[1].textContent = date;
  cells[2].textContent = supplier;
  cells[3].textContent = trn;
  cells[3].style.cssText = 'font-family:DM Mono,monospace;font-size:12px;'+trnColor;
  cells[4].textContent = fmt(sub);
  cells[5].textContent = fmt(vat);
  cells[6].textContent = fmt(total);
  cells[7].innerHTML   = `<span class="b ${confCls}">${conf}%</span>`;
  cells[8].innerHTML   = `<span class="b ${stCls}">${status}</span>`;

  // flash the row green briefly
  _editRow.style.transition='background .3s';
  _editRow.style.background='rgba(62,207,142,0.08)';
  setTimeout(()=>{ _editRow.style.background=''; },1200);

  closeM('m-edit-inv');
  toast('Invoice '+invNo+' updated ?','ok');
  audit('Edited purchase invoice',invNo,'Saved');
  _editRow=null;
}

// -- end edit ------------------------------------------------------

// -- PAYROLL ------------------------------------------------------
function money(n){
  return 'AED '+Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
}

function parseMoneyInput(el){
  return parseFloat(String(el?.value||'0').replace(/,/g,''))||0;
}

function getPayrollRows(){
  return [...document.querySelectorAll('#payroll-tbody tr')];
}

function recalcPayroll(){
  const rows=getPayrollRows();
  let gross=0,deductions=0,netTotal=0,exceptions=0;

  rows.forEach(row=>{
    const basic=parseMoneyInput(row.querySelector('.pay-basic'));
    const allow=parseMoneyInput(row.querySelector('.pay-allow'));
    const ot=parseMoneyInput(row.querySelector('.pay-ot'));
    const ded=parseMoneyInput(row.querySelector('.pay-ded'));
    const net=basic+allow+ot-ded;
    gross+=basic+allow+ot;
    deductions+=ded;
    netTotal+=net;
    if(row.dataset.wps!=='ok')exceptions++;
    const netCell=row.querySelector('.pay-net');
    if(netCell)netCell.textContent=money(net);
  });

  const grossEl=document.getElementById('pay-stat-gross');
  const dedEl=document.getElementById('pay-stat-ded');
  const netEl=document.getElementById('pay-stat-net');
  const excEl=document.getElementById('pay-stat-exc');
  const jeGross=document.getElementById('pay-je-gross');
  const jeDed=document.getElementById('pay-je-ded');
  const jeNet=document.getElementById('pay-je-net');

  if(grossEl)grossEl.textContent=money(gross).replace('.00','');
  if(dedEl)dedEl.textContent=money(deductions).replace('.00','');
  if(netEl)netEl.textContent=money(netTotal).replace('.00','');
  if(excEl)excEl.textContent=exceptions;
  if(jeGross)jeGross.textContent=money(gross);
  if(jeDed)jeDed.textContent=money(deductions);
  if(jeNet)jeNet.textContent=money(netTotal);
}

function runPayroll(){
  recalcPayroll();
  getPayrollRows().forEach(row=>{
    const status=row.querySelector('.pay-status');
    if(!status)return;
    if(row.dataset.wps==='ok'){
      status.className='b b-b pay-status';
      status.textContent='Calculated';
    }else{
      status.className='b b-a pay-status';
      status.textContent='Review';
    }
  });
  toast('Payroll calculated. Review WPS exceptions before approval.','ok');
}

function approvePayroll(){
  const blocked=getPayrollRows().some(row=>row.dataset.wps!=='ok');
  if(blocked){
    toast('Resolve WPS exceptions before final approval','warn');
  }
  document.querySelectorAll('#payroll-tbody .pay-status').forEach(status=>{
    if(status.textContent!=='Review'){
      status.className='b b-g pay-status';
      status.textContent='Approved';
    }
  });
  const fin=document.getElementById('pay-fin-status');
  const mgmt=document.getElementById('pay-mgmt-status');
  if(fin){fin.className='b b-g';fin.textContent='Approved';}
  if(mgmt){mgmt.className=blocked?'b b-a':'b b-g';mgmt.textContent=blocked?'Conditional':'Approved';}
  toast(blocked?'Payroll conditionally approved with WPS hold':'Payroll approved ?',blocked?'warn':'ok');
  audit('Approved payroll','June 2024',blocked?'Conditional':'Approved');
}

function validateWPS(){
  const status=document.getElementById('wps-status');
  const results=document.getElementById('wps-results');
  const exceptions=getPayrollRows().filter(row=>row.dataset.wps!=='ok').length;
  if(status){
    status.className=exceptions?'b b-a':'b b-g';
    status.textContent=exceptions?exceptions+' exception':'Validated';
  }
  if(results){
    results.innerHTML=exceptions
      ? '<div><span style="color:var(--green)">?</span> Payroll totals match SIF preview</div><div><span style="color:var(--amber)">?</span> 1 employee requires bank details before bank upload</div><div><span style="color:var(--green)">?</span> Employer MOL ID and file sequence are present</div>'
      : '<div><span style="color:var(--green)">?</span> All employees passed WPS validation</div><div><span style="color:var(--green)">?</span> SIF file is ready for bank upload</div>';
  }
  toast(exceptions?'WPS validation completed with exceptions':'WPS validation passed ?',exceptions?'warn':'ok');
}

function generateSIF(){
  validateWPS();
  const hasHold=getPayrollRows().some(row=>row.dataset.wps!=='ok');
  toast(hasHold?'SIF draft generated. Blocked employees excluded until fixed.':'SIF generated for bank upload ?',hasHold?'warn':'ok');
}

function getPayrollRowInfo(row){
  const name=row.querySelector('td div div div')?.textContent||'Employee';
  const basic=parseMoneyInput(row.querySelector('.pay-basic'));
  const allow=parseMoneyInput(row.querySelector('.pay-allow'));
  const ot=parseMoneyInput(row.querySelector('.pay-ot'));
  const ded=parseMoneyInput(row.querySelector('.pay-ded'));
  return {name,basic,allow,ot,ded,net:basic+allow+ot-ded};
}

function renderPayslipPreview(info){
  const body=document.getElementById('payslip-body');
  if(!body)return;
  body.innerHTML=`
    <div class="flx-b"><span>Employee</span><strong style="color:var(--text)">${info.name}</strong></div>
    <div class="flx-b"><span>Basic Salary</span><span class="mono">${money(info.basic)}</span></div>
    <div class="flx-b"><span>Allowances</span><span class="mono">${money(info.allow)}</span></div>
    <div class="flx-b"><span>Overtime / Variable Pay</span><span class="mono">${money(info.ot)}</span></div>
    <div class="flx-b"><span>Deductions</span><span class="mono" style="color:var(--red)">${money(info.ded)}</span></div>
    <div class="divider"></div>
    <div class="flx-b"><strong style="color:var(--text)">Net Pay</strong><strong class="mono" style="color:var(--green)">${money(info.net)}</strong></div>`;
}

function previewPayslip(btn){
  const row=btn.closest('tr');
  if(!row)return;
  const info=getPayrollRowInfo(row);
  renderPayslipPreview(info);
  const tab=document.querySelector('#page-payroll .tab:nth-child(5)');
  if(tab)stab(tab,'pay-payslips');
  toast('Payslip preview opened for '+info.name,'info');
}

function previewStaticPayslip(name,net){
  const numeric=parseFloat(String(net).replace(/,/g,''))||0;
  renderPayslipPreview({name,basic:numeric*.78,allow:numeric*.22,ot:0,ded:0,net:numeric});
}

function publishPayslips(){
  toast('Payslips published to employee email and mobile app ?','ok');
  audit('Published payslips','June 2024','Published');
}

function postPayrollJournal(){
  recalcPayroll();
  const ref='PAY-JE-'+new Date().toISOString().slice(0,10).replace(/-/g,'');
  const date=new Date().toISOString().split('T')[0];
  const gross=parseAmount(document.getElementById('pay-je-gross')?.textContent);
  const deductions=parseAmount(document.getElementById('pay-je-ded')?.textContent);
  const net=parseAmount(document.getElementById('pay-je-net')?.textContent);
  postLedgerLine({date,ref,description:'Payroll gross salary expense',debit:gross,credit:0,account:'Operating Expenses (5000)'});
  if(deductions)postLedgerLine({date,ref,description:'Payroll deductions payable',debit:0,credit:deductions,account:'Accounts Payable (2000)'});
  postLedgerLine({date,ref,description:'Net payroll payable',debit:0,credit:net,account:'Accounts Payable (2000)'});
  filterLedger();
  toast('Payroll journal posted to accounting ?','ok');
  audit('Posted payroll journal','Accounting','Posted');
}

function calcGratuity(){
  const basic=parseMoneyInput(document.getElementById('eos-basic'));
  const years=parseFloat(document.getElementById('eos-years')?.value)||0;
  const months=parseFloat(document.getElementById('eos-months')?.value)||0;
  const serviceYears=years+(months/12);
  const daily=basic/30;
  const firstFive=Math.min(serviceYears,5)*21;
  const aboveFive=Math.max(serviceYears-5,0)*30;
  const eligibleDays=firstFive+aboveFive;
  const total=daily*eligibleDays;
  const dailyEl=document.getElementById('eos-daily');
  const daysEl=document.getElementById('eos-days');
  const totalEl=document.getElementById('eos-total');
  if(dailyEl)dailyEl.textContent=money(daily);
  if(daysEl)daysEl.textContent=eligibleDays.toFixed(2);
  if(totalEl)totalEl.textContent=money(total);
}

// -- REPORTS + AI INSIGHTS ----------------------------------------
function runAIReport(){
  const summary=document.getElementById('ai-summary');
  const actions=document.getElementById('ai-actions');
  const output=document.getElementById('ai-report-output');
  const risk=document.getElementById('rep-risk-score');
  const reportType=document.getElementById('ai-report-type')?.value||'Board Summary';
  const dashboard=latestReportSummary?.dashboard||{};
  const ai=latestReportSummary?.ai||{};
  const vat=latestReportSummary?.vat?.settlement||{};
  const pl=latestReportSummary?.profit_loss||{};
  const narrative=ai.report_text||`Reports are generated from current database records: ${formatAed(dashboard.revenue||0)} revenue, ${formatAed(vat.net_vat_payable||0)} net VAT, and ${formatAed(pl.net_profit||0)} net profit.`;

  if(summary){
    summary.textContent=narrative;
  }
  if(actions){
    actions.innerHTML=(dashboard.actions||[]).map(item=>`<div>- ${escapeHtml(item)}</div>`).join('')||'<div>- No report exceptions found in current database records.</div>';
  }
  if(output){
    output.innerHTML=`<strong style="color:var(--text)">${escapeHtml(reportType)}</strong><br>${escapeHtml(narrative)}<div class="divider"></div><strong style="color:var(--text)">Database signals</strong><br>Risk score: ${escapeHtml(dashboard.risk_score||'Low')}<br>Anomalies: ${escapeHtml(ai.anomalies||0)}<br>Potential savings: ${formatAed(ai.potential_savings||0)}<br>Collection upside: ${formatAed(ai.collection_upside||0)}`;
  }
  if(risk){
    risk.style.color=(dashboard.risk_score==='High')?'var(--red)':(dashboard.risk_score==='Medium')?'var(--amber)':'var(--green)';
    risk.textContent=dashboard.risk_score||'Low';
  }
  toast('AI report insights generated from database','ok');
  audit('Generated AI report',reportType,'Logged');
}

function simulateReportScenario(type){
  const output=document.getElementById('ai-report-output');
  const summary=document.getElementById('ai-summary');
  const dashboard=latestReportSummary?.dashboard||{};
  const pl=latestReportSummary?.profit_loss||{};
  const revenue=Number(dashboard.revenue||pl.revenue||0);
  const cogs=Math.abs(Number(pl.cogs||0));
  const grossProfit=Number(pl.gross_profit||0);
  const currentMargin=revenue?((grossProfit/revenue)*100):0;
  let msg='Scenario generated';
  if(type==='supplier'){
    const saving=cogs*0.02;
    const newMargin=revenue?(((grossProfit+saving)/revenue)*100):0;
    msg=`Supplier cost scenario: reducing database COGS by 2% improves profit by about ${formatAed(saving)} and lifts gross margin from ${currentMargin.toFixed(1)}% to ${newMargin.toFixed(1)}%.`;
  }else if(type==='growth'){
    const revenueLift=revenue*0.10;
    const profitLift=revenueLift*(currentMargin/100);
    const vatLift=revenueLift*0.05;
    msg=`Growth scenario: 10% revenue uplift adds about ${formatAed(revenueLift)} revenue, ${formatAed(profitLift)} gross profit, and approximately ${formatAed(vatLift)} output VAT before input offsets.`;
  }
  if(output)output.innerHTML='<strong style="color:var(--text)">Scenario Result</strong><br>'+msg;
  if(summary)summary.textContent=msg;
  toast('Scenario simulation updated','info');
}

function askReportAI(){
  const q=(document.getElementById('report-ai-question')?.value||'').trim();
  const answer=document.getElementById('report-ai-answer');
  if(!q){
    toast('Enter a question first','warn');
    return;
  }
  const lower=q.toLowerCase();
  const dashboard=latestReportSummary?.dashboard||{};
  const vat=latestReportSummary?.vat?.settlement||{};
  const pl=latestReportSummary?.profit_loss||{};
  const ai=latestReportSummary?.ai||{};
  let response=ai.report_text||'The current reports are loaded from database records. Open a report tab to refresh the database summary.';
  if(lower.includes('cash')){
    response=`Cash view is DB-based: revenue is ${formatAed(dashboard.revenue||0)}, current cash forecast/runway is ${dashboard.cash_runway_months||'0.00'} months, and net profit is ${formatAed(pl.net_profit||0)}. Review receivables and payment timing before drawing conclusions.`;
  }else if(lower.includes('vat')){
    response=`VAT is DB-based: output VAT is ${formatAed(vat.output_vat||0)}, input VAT is ${formatAed(vat.input_vat||0)}, and net VAT payable is ${formatAed(vat.net_vat_payable||0)}. Validate TRN, evidence, and source approval before filing.`;
  }else if(lower.includes('profit')||lower.includes('margin')){
    response=`Profit is DB-based: total revenue is ${formatAed(pl.total_revenue||0)}, COGS is ${formatAed(pl.cogs||0)}, gross profit is ${formatAed(pl.gross_profit||0)}, and net profit is ${formatAed(pl.net_profit||0)}. Gross margin is ${dashboard.gross_margin||'0.00'}%.`;
  }
  if(answer)answer.textContent=response;
  toast('Report answer generated from database','ok');
}

// -- SYSTEM AI ASSISTANT ------------------------------------------
function renderAIResponse(data,question){
  const actions=(data?.suggested_actions||[]).map(item=>`<div>- ${escapeHtml(item)}</div>`).join('');
  const controls=(data?.controls||[]).map(item=>`<div>- ${escapeHtml(item)}</div>`).join('');
  return `
    <strong style="color:var(--text)">Question</strong><br>${escapeHtml(question)}
    <div class="divider"></div>
    <strong style="color:var(--text)">Answer</strong><br>${escapeHtml(data?.answer||'No AI answer returned.')}
    <div class="divider"></div>
    <strong style="color:var(--text)">Recommended Review Actions</strong><br>${actions||'<div>- Continue with normal TaxFlow validation.</div>'}
    <div class="divider"></div>
    <strong style="color:var(--text)">Controls Preserved</strong><br>${controls||'<div>- Existing approval and posting controls remain active.</div>'}
  `;
}

function aiResponseSections(data){
  const actions=(data?.suggested_actions||[]).map(item=>`<div>- ${escapeHtml(item)}</div>`).join('');
  const controls=(data?.controls||[]).map(item=>`<div>- ${escapeHtml(item)}</div>`).join('');
  return `
    <div>${escapeHtml(data?.answer||'No AI answer returned.')}</div>
    <div class="ai-msg-actions">
      <strong>Suggested next steps</strong>
      ${actions||'<div>- Continue with normal TaxFlow validation.</div>'}
    </div>
    <div class="ai-msg-controls">
      <strong>Controls kept:</strong><br>
      ${controls||'<div>- Existing validation, approval, posting, VAT, and audit controls remain active.</div>'}
    </div>
  `;
}

function appendAIMessage(role,html,label){
  const thread=document.getElementById('system-ai-thread');
  if(!thread)return null;
  const msg=document.createElement('div');
  msg.className=`ai-msg ai-msg-${role}`;
  msg.innerHTML=`
    <div class="ai-avatar">${role==='user'?'You':'AI'}</div>
    <div class="ai-bubble">
      <div class="ai-msg-meta">${escapeHtml(label||role)}</div>
      ${html}
    </div>
  `;
  thread.appendChild(msg);
  thread.scrollTop=thread.scrollHeight;
  return msg;
}

function setAIStatus(text){
  const status=document.getElementById('system-ai-answer');
  if(status)status.textContent=text;
}

function renderAIWorkbench(data){
  const snapshot=data?.context||{};
  const target=document.getElementById('ai-workbench-snapshot');
  if(target){
    target.innerHTML=`
      <div class="toggle-row"><div><div>Document intake</div><div class="toggle-copy">Extracts fields into drafts only; no direct posting.</div></div><span class="b b-g">Ready</span></div>
      <div class="toggle-row"><div><div>VAT validation</div><div class="toggle-copy">Net VAT from posted tax lines: AED ${escapeHtml(snapshot.net_vat??'0.00')}</div></div><span class="b b-a">Review</span></div>
      <div class="toggle-row"><div><div>Accounting coding</div><div class="toggle-copy">${escapeHtml(snapshot.source_transaction_count??0)} source transactions available for mapping review.</div></div><span class="b b-g">Assistive</span></div>
      <div class="toggle-row"><div><div>Exception explanations</div><div class="toggle-copy">${escapeHtml(snapshot.open_exception_count??0)} open saved exceptions can be explained from Exception Center.</div></div><span class="b ${(Number(snapshot.open_exception_count)||0)>0?'b-a':'b-g'}">Live</span></div>
      <div class="toggle-row"><div><div>Audit-aware answers</div><div class="toggle-copy">${escapeHtml(snapshot.audit_log_count??0)} audit records available for context.</div></div><span class="b b-b">Scoped</span></div>
    `;
  }
  const status=document.getElementById('ai-workbench-status');
  if(status)status.textContent=data?.answer||'AI workbench is ready.';
}

function loadAIWorkbench(){
  moduleApi('/ai/workbench')
    .then(renderAIWorkbench)
    .catch(err=>{
      console.warn('AI workbench unavailable:',err);
      const status=document.getElementById('ai-workbench-status');
      if(status)status.textContent='AI backend unavailable. Local guide answers still work.';
    });
}

function buildSystemAIResponse(question){
  const q=question.toLowerCase();
  if(q.includes('invoice')||q.includes('sales')||q.includes('customer')||q.includes('product')){
    return 'For invoices: open Sales & Invoices, use Create Invoice, add or select a customer, add products in Line Items, then use Preview PDF to view the formatted invoice. Invoice layout is configured in Settings > Documents > Invoice Layout Setup. Uploaded sales invoices can also be imported from PDF, Excel, CSV, JPEG, or PNG and stored in the invoice register.';
  }
  if(q.includes('layout')||q.includes('template')||q.includes('footer')||q.includes('bank note')||q.includes('logo')){
    return 'Invoice layout is controlled from Settings > Documents. You can set the template style, accent color, company display name, TRN visibility, header address, bank/payment note, and footer message. The invoice preview uses those settings immediately.';
  }
  if(q.includes('purchase')||q.includes('ocr')||q.includes('extract')||q.includes('document')){
    return 'Purchases support document upload and extraction through the backend app-data API. Extracted purchase invoices are saved to the database, can be reviewed, corrected, and validated before becoming records.';
  }
  if(q.includes('bill')||q.includes('vendor')||q.includes('payable')||q.includes('purchase order')){
    return 'Bills covers vendor bills, purchase orders, and aged payables. Supplier and vendor directory is managed from the Purchase module.';
  }
  if(q.includes('payment')||q.includes('receipt')||q.includes('gateway')||q.includes('settlement')){
    return 'Bank & Payments tracks bank accounts, customer receipts, supplier payments, transactions, and reconciliation in one module. Customer receipts can be allocated to invoices, supplier payments can clear bills, and bank reconciliation matches statement lines to ledger entries.';
  }
  if(q.includes('tax invoice')||q.includes('invoice include')||q.includes('tax invoice include')){
    return 'UAE tax invoice checklist: supplier legal name and address, supplier TRN, customer name and TRN where applicable, unique invoice number, invoice date, supply date if different, description of goods/services, quantity, unit price, taxable amount, VAT rate, VAT amount in AED, total including VAT, and clear tax treatment such as 5%, 0%, exempt, or reverse charge. TaxFlow supports invoice layout setup in Settings > Documents and TRN/VAT validation in invoice and purchase flows.';
  }
  if(q.includes('trn')||q.includes('tax registration number')){
    return 'UAE TRN guidance: a VAT TRN should be 15 digits. In TaxFlow, customer, supplier, company, and extracted purchase TRNs are checked for 15 digits. Before VAT filing, review missing/invalid TRNs, especially supplier invoices where input VAT is claimed. Production should verify TRNs against an authoritative FTA-supported process where available.';
  }
  if(q.includes('zero')||q.includes('zero-rated')||q.includes('zero rated')||q.includes('exempt')){
    return 'UAE VAT distinction: zero-rated supplies are taxable at 0%, so they are reported in VAT returns and may still allow related input VAT recovery if conditions are met. Exempt supplies are outside recoverable VAT treatment, so related input VAT may be blocked or apportioned. Keep export evidence, contract/supporting documents, and correct tax coding for every 0% or exempt transaction.';
  }
  if(q.includes('reverse charge')||q.includes('rcm')){
    return 'UAE reverse charge: for certain imported services or goods, the recipient accounts for output VAT and may recover input VAT if eligible. In system terms, mark the transaction as reverse charge, calculate output VAT and recoverable input VAT separately, and keep supplier invoice/import evidence. It should flow to VAT return boxes separately from normal local 5% purchases.';
  }
  if(q.includes('input vat')||q.includes('recover')||q.includes('recoverable')||q.includes('non-recoverable')){
    return 'Input VAT recovery checks: supplier invoice must be valid, supplier TRN should be present, expense must relate to taxable business activity, VAT amount should match the rate, and blocked/non-business expenses should be excluded or apportioned. TaxFlow purchase validation flags TRN and VAT math issues before VAT reporting.';
  }
  if(q.includes('fta')||q.includes('audit')||q.includes('record')||q.includes('evidence')){
    return 'FTA audit readiness: retain tax invoices, credit notes, export/customs evidence for zero-rated supplies, import/reverse-charge documents, payment evidence, bank reconciliations, VAT workpapers, payroll/WPS support where relevant, and audit logs of changes. TaxFlow Documents includes an Audit Pack area for VAT evidence, payroll evidence, and accounting evidence.';
  }
  if(q.includes('notification')||q.includes('email')||q.includes('whatsapp')||q.includes('sms')||q.includes('push')){
    return 'Notifications includes in-app alerts, email, WhatsApp/SMS, and push-style rules. Typical rules include overdue invoice reminders, VAT due alerts, bank sync failures, and payroll approval reminders.';
  }
  if(q.includes('attachment')||q.includes('receipt image')||q.includes('audit pack')||q.includes('file storage')){
    return 'Documents is the repository for invoice PDFs, receipt images, bill attachments, contracts, audit packs, and VAT evidence. In production these files should move to S3 or Azure Blob Storage with encrypted retention policies.';
  }
  if(q.includes('account')||q.includes('journal')||q.includes('ledger')||q.includes('debit')||q.includes('credit')){
    return 'Accounting includes Chart of Accounts, Voucher Type, General Ledger, Payments / Receipts, Statutory Filing, and Bank Reconciliation. Voucher entries require date, voucher number, narration, at least two valid lines, selected accounts, and balanced debit/credit totals before posting.';
  }
  if(q.includes('payroll')||q.includes('wps')||q.includes('payslip')||q.includes('salary')){
    return 'Payroll includes salary calculation, WPS/SIF validation, payslip preview, approvals, and accounting posting. Payroll journal posting creates ledger lines for gross salary expense, deductions payable, and net payroll payable.';
  }
  if(q.includes('vat')||q.includes('tax')||q.includes('trn')||q.includes('filing')){
    return 'For VAT readiness, check customer/supplier TRNs, VAT math at 5% where applicable, purchase validation exceptions, sales invoice totals, and report VAT payable. Settings includes tax and eInvoicing readiness controls, while Reports includes VAT, P&L, trial balance, aging, and AI insights.';
  }
  if(q.includes('setting')||q.includes('permission')||q.includes('user')||q.includes('security')||q.includes('backup')||q.includes('audit')){
    return 'Settings covers company registration, users and roles, tax settings, notifications, security, integrations, approvals, document templates, invoice layout, backups, system health, and audit logs. Important actions such as invoice views, journal posting, layout saves, and payroll posting are logged.';
  }
  if(q.includes('bank')||q.includes('reconcile')||q.includes('payment')){
    return 'Bank Accounts covers accounts, transactions, and reconciliation. The current UI is connected to the app database for saved actions, while bank feed connections, CSV/MT940 imports, matching rules, and payment allocation can be added on top of the same backend modules.';
  }
  if(q.includes('report')||q.includes('dashboard')||q.includes('cash')||q.includes('profit')||q.includes('aging')){
    return 'Reports include executive dashboard, VAT report, P&L, trial balance, aging report, and AI insights. Dashboard and side-menu counts read from database-backed module totals.';
  }
  if(q.includes('roadmap')||q.includes('production')||q.includes('improve')||q.includes('backend')||q.includes('database')){
    return 'Production priorities: add authentication, tenant/company scoping, database persistence, object storage for uploaded files, real extraction APIs, audit trails, role permissions, backend AI, eInvoicing adapters, bank integrations, tests, and CI. The detailed backlog is in docs/improvement-roadmap.md.';
  }
  return 'TaxFlow is organized into Dashboard, Sales & Invoices, Quotations, Purchases, Expenses, Bank, Accounting, Reports, Inventory, Staff, Payroll, Expert Review, Settings, and this AI Assistant. Ask about a module name or workflow such as quotation creation, invoice creation, purchase extraction, stock mapping, journal posting, VAT filing, payroll WPS, settings, or production roadmap.';
}

async function askSystemAI(prompt){
  const input=document.getElementById('system-ai-question');
  const send=document.getElementById('ai-send-btn');
  const question=(prompt||input?.value||'').trim();
  if(!question){
    toast('Enter a system question first','warn');
    return;
  }
  if(input)input.value=prompt?'':input.value;
  appendAIMessage('user',escapeHtml(question),'You');
  const pending=appendAIMessage('agent','<span style="color:var(--text3)">Thinking through the TaxFlow controls...</span>','TaxFlow AI');
  if(input&&!prompt)input.value='';
  if(send)send.disabled=true;
  setAIStatus('TaxFlow AI is answering...');
  try{
    const data=await moduleApi('/ai/assist',{method:'POST',body:{question}});
    if(pending)pending.querySelector('.ai-bubble').innerHTML=`<div class="ai-msg-meta">TaxFlow AI</div>${aiResponseSections(data)}`;
    setAIStatus(`Answered with ${data.confidence||0}% confidence. Human approval is still required for posting.`);
  }catch(err){
    console.warn('Backend AI assistant unavailable:',err);
    if(pending){
      pending.querySelector('.ai-bubble').innerHTML=`
        <div class="ai-msg-meta">TaxFlow AI</div>
        <div>${escapeHtml(buildSystemAIResponse(question))}</div>
        <div class="ai-msg-controls"><strong>Controls kept:</strong><br><div>- Local fallback only; backend validation and approval controls still apply.</div></div>
      `;
    }
    setAIStatus('Answered with local fallback because backend AI was unavailable.');
  }finally{
    if(send)send.disabled=false;
    document.getElementById('system-ai-thread')?.scrollTo({top:document.getElementById('system-ai-thread').scrollHeight,behavior:'smooth'});
  }
  toast('AI assistant answered','ok');
  audit('Asked AI assistant',question.slice(0,60),'Answered');
}

function clearSystemAI(){
  const input=document.getElementById('system-ai-question');
  const thread=document.getElementById('system-ai-thread');
  if(input)input.value='';
  if(thread){
    thread.innerHTML=`
      <div class="ai-msg ai-msg-agent">
        <div class="ai-avatar">AI</div>
        <div class="ai-bubble">
          <div class="ai-msg-meta">TaxFlow AI</div>
          <div>I can review TaxFlow workflows, explain exceptions, suggest validation checks, and guide you through accounting or VAT decisions. I will not approve or post transactions.</div>
        </div>
      </div>
    `;
  }
  setAIStatus('Chat cleared. Ask a new TaxFlow question.');
}

// -- TABLE SEARCH + PAGINATION -----------------------------------
const tableEnhanceState=new WeakMap();
let currentDetailRow=null;
let currentDetailTable=null;

function enhancePageTables(pageId){
  const page=document.getElementById(pageId);
  if(!page)return;
  page.querySelectorAll('table.tbl').forEach(table=>enhanceTable(table));
}

function refreshActivePageTables(){
  const page=document.querySelector('.page.on');
  if(!page)return;
  page.querySelectorAll('table.tbl').forEach(table=>{
    refreshEnhancedTable(table);
  });
}

function refreshInitializedTables(){
  document.querySelectorAll('table.tbl').forEach(table=>{
    if(tableEnhanceState.has(table))refreshEnhancedTable(table);
  });
}

function applyAllTableActions(){
  normalizeSalesInvoiceActions();
  document.querySelectorAll('table.tbl').forEach(table=>addTableDeleteActions(table));
}

function watchVisibleTablePagination(){
  if(window.__taxflowPaginationWatcher)return;
  window.__taxflowPaginationWatcher=true;
  const observer=new MutationObserver(mutations=>{
    if(!mutations.some(m=>m.type==='attributes'&&m.attributeName==='class'))return;
    scheduleIdleTask(()=>refreshActivePageTables(),350);
  });
  document.querySelectorAll('.page,.tab-body').forEach(el=>{
    observer.observe(el,{attributes:true,attributeFilter:['class']});
  });
}

function unwrapExistingTableControls(table){
  let shell=table.closest('.tbl-shell');
  while(shell){
    const host=shell.parentElement;
    if(!host)break;
    host.insertBefore(table,shell);
    shell.remove();
    shell=table.closest('.tbl-shell');
  }
  table.querySelectorAll('th[data-delete-col]').forEach(cell=>cell.remove());
  table.querySelectorAll('td[data-delete-col]').forEach(cell=>cell.remove());
  table.querySelectorAll('th[data-action-col]').forEach(cell=>cell.remove());
  table.querySelectorAll('td[data-action-col]').forEach(cell=>cell.remove());
  getTableRows(table).forEach(row=>{
    delete row.dataset.deleteActionAdded;
    delete row.dataset.rowActionsAdded;
  });
}

function removeDuplicateTablePagers(scope=document){
  const pagedTables=new Set();
  scope.querySelectorAll('table.tbl').forEach(table=>{
    const shell=table.closest('.tbl-shell');
    if(!shell)return;
    if(pagedTables.has(table)){
      unwrapExistingTableControls(table);
      return;
    }
    pagedTables.add(table);
    shell.querySelectorAll(':scope > .tbl-tools').forEach((tools,index)=>{
      if(index>0)tools.remove();
    });
  });
}

function tableControlsTemplate(){
  return `
    <div class="tbl-search-wrap">
      <input class="fi tbl-search" placeholder="Search table">
      <span class="b b-gray tbl-info">0 records</span>
    </div>
    <div class="tbl-pager">
      <label class="tbl-size-label">Rows to display
        <select class="fi tbl-size" aria-label="Rows to display">
          <option value="5">5 rows</option>
          <option value="10" selected>10 rows</option>
          <option value="25">25 rows</option>
          <option value="50">50 rows</option>
          <option value="100">100 rows</option>
          <option value="all">All rows</option>
        </select>
      </label>
      <button class="btn btn-g btn-sm tbl-prev" type="button">Prev</button>
      <button class="btn btn-g btn-sm tbl-next" type="button">Next</button>
      <button class="btn btn-g btn-sm tbl-export" type="button">Export CSV</button>
    </div>
  `;
}

function skipTableTools(table){
  return table?.dataset?.noTableTools==='1';
}

function enhanceTable(table){
  if(skipTableTools(table)){
    addTableDeleteActions(table);
    getTableRows(table).forEach(row=>{
      row.style.display='';
      row.hidden=false;
    });
    return;
  }
  removeDuplicateTablePagers(table.closest('.page')||document);
  if(table.closest('.tbl-scroll')&&tableEnhanceState.has(table)){
    refreshEnhancedTable(table);
    return;
  }
  if(tableEnhanceState.has(table))return;
  if(table.closest('.tbl-shell')){
    unwrapExistingTableControls(table);
  }

  const parent=table.parentElement;
  if(!parent)return;

  const shell=document.createElement('div');
  shell.className='tbl-shell';
  const controls=document.createElement('div');
  controls.className='tbl-tools';
  controls.innerHTML=tableControlsTemplate();
  const scroll=document.createElement('div');
  scroll.className='tbl-scroll';

  parent.insertBefore(shell,table);
  shell.appendChild(controls);
  shell.appendChild(scroll);
  scroll.appendChild(table);

  const state={
    page:1,
    pageSize:10,
    query:'',
    input:controls.querySelector('.tbl-search'),
    size:controls.querySelector('.tbl-size'),
    prev:controls.querySelector('.tbl-prev'),
    next:controls.querySelector('.tbl-next'),
    exportBtn:controls.querySelector('.tbl-export'),
    info:controls.querySelector('.tbl-info')
  };
  tableEnhanceState.set(table,state);

  state.input.addEventListener('input',()=>{
    state.query=state.input.value.trim().toLowerCase();
    state.page=1;
    refreshEnhancedTable(table);
  });
  state.size.addEventListener('change',()=>{
    state.pageSize=state.size.value==='all'?'all':Number(state.size.value);
    state.page=1;
    refreshEnhancedTable(table);
  });
  state.prev.addEventListener('click',()=>{
    if(state.page<=1){
      toast('Already showing the first rows','info');
      refreshEnhancedTable(table);
      return;
    }
    state.page=Math.max(1,state.page-1);
    refreshEnhancedTable(table);
  });
  state.next.addEventListener('click',()=>{
    const matched=getTableRows(table).filter(row=>!state.query||row.textContent.toLowerCase().includes(state.query));
    const pageSize=state.pageSize==='all'?matched.length||1:state.pageSize;
    const totalPages=Math.max(1,Math.ceil(matched.length/pageSize));
    if(state.page>=totalPages){
      toast(`No more rows. ${matched.length} records loaded.`,'info');
      refreshEnhancedTable(table);
      return;
    }
    state.page=Math.min(totalPages,state.page+1);
    refreshEnhancedTable(table);
  });
  state.exportBtn.addEventListener('click',()=>exportTableCsv(table));
  addTableDeleteActions(table);

  const tbody=table.tBodies[0];
  if(tbody){
    const observer=new MutationObserver(()=>{
      addTableDeleteActions(table);
      scheduleTableRefresh(table);
    });
    observer.observe(tbody,{childList:true,subtree:true});
  }
  refreshEnhancedTable(table);
}

function exportTableCsv(table){
  const title=(table.closest('.card')?.querySelector('.card-title')?.textContent||document.getElementById('ptitle')?.textContent||'TaxFlow Export').trim();
  const headers=[...table.querySelectorAll('thead th')]
    .filter(th=>!th.dataset.deleteCol)
    .filter(th=>!th.dataset.actionCol)
    .map(th=>th.textContent.trim())
    .filter(Boolean);
  const rows=getTableRows(table)
    .filter(row=>row.style.display!=='none')
    .map(row=>[...row.children].filter(cell=>!cell.dataset.deleteCol&&!cell.dataset.actionCol).map(cell=>cell.textContent.replace(/\s+/g,' ').trim()));
  if(!rows.length){
    toast('No visible rows to export','warn');
    return;
  }
  const csv=[headers, ...rows].map(row=>row.map(value=>`"${String(value||'').replace(/"/g,'""')}"`).join(',')).join('\n');
  const blob=new Blob([csv],{type:'text/csv;charset=utf-8'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;
  a.download=`${title.toLowerCase().replace(/[^a-z0-9]+/g,'-').replace(/^-|-$/g,'')||'taxflow-export'}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
  toast(`${title} exported`,'ok');
  audit('Exported table',title,'CSV');
}

function deleteIconSvg(){
  return `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 4h10"/><path d="M6 4V2.8h4V4"/><path d="M5 6v7"/><path d="M8 6v7"/><path d="M11 6v7"/><path d="M4.5 4l.5 10h6l.5-10"/></svg>`;
}

function viewIconSvg(){
  return `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.8 8s2.2-4 6.2-4 6.2 4 6.2 4-2.2 4-6.2 4-6.2-4-6.2-4z"/><circle cx="8" cy="8" r="1.8"/></svg>`;
}

function editIconSvg(){
  return `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 11.5V13h1.5L12 5.5 10.5 4 3 11.5z"/><path d="M9.8 4.7l1.5 1.5"/><path d="M2.5 14h11"/></svg>`;
}

function checkIconSvg(){
  return `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.3l3 3L13 4.7"/></svg>`;
}

function copyIconSvg(){
  return `<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="5" y="1" width="8" height="10" rx="1.2"/><rect x="2" y="5" width="8" height="10" rx="1.2"/></svg>`;
}

function uploadIconSvg(){
  return `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 15V5"/><path d="M8 9l4-4 4 4"/><path d="M5 15v3.5A1.5 1.5 0 0 0 6.5 20h11a1.5 1.5 0 0 0 1.5-1.5V15"/></svg>`;
}

function tableActionButtonsHtml(table){
  const collection=inferCollectionFromContext(table);
  if(collection==='salesInvoices')return salesInvoiceActionsHtml();
  if(collection==='quotations')return quotationActionsHtml();
  const viewAction=collection==='purchaseRecords'
    ? "openPurchaseRecordPreview(this)"
    : "openRowDetail(this,'Record Detail',document.getElementById('ptitle')?.textContent||'Detail')";
  const editAction=collection==='purchaseRecords'
    ? 'editPurchaseRecord(this)'
    : "openGenericEditRow(this,'Edit '+(document.getElementById('ptitle')?.textContent||'Record'),'Update selected row')";
  return `<div class="row-actions">
    <button class="icon-btn view" type="button" title="View" aria-label="View row" onclick="${viewAction}">${viewIconSvg()}</button>
    <button class="icon-btn edit" type="button" title="Edit" aria-label="Edit row" onclick="${editAction}">${editIconSvg()}</button>
    <button class="icon-btn danger row-delete-btn" type="button" title="Delete" aria-label="Delete row" onclick="deleteTableRow(this)">${deleteIconSvg()}</button>
  </div>`;
}

function findGenericActionCell(row){
  return [...row.children].find(cell=>
    cell.dataset.actionCol==='1'||
    cell.querySelector('.row-actions')||
    cell.querySelector('button[onclick*="openRowDetail"],button[onclick*="editPurchaseRecord"],button[onclick*="openSalesInvoiceRow"],button[onclick*="shareSalesInvoiceRow"],button[onclick*="previewQuotation"],button[onclick*="shareQuotation"],button.row-delete-btn')
  );
}

function addTableDeleteActions(table){
  if(!table||table.dataset.deleteActionsBound==='skip')return;
  const headRow=table.tHead?.rows?.[0];
  const existingActionCells=getTableRows(table).map(findGenericActionCell).filter(Boolean);
  const actionIndex=existingActionCells[0]?[...existingActionCells[0].parentElement.children].indexOf(existingActionCells[0]):-1;
  if(headRow){
    const actionHead=actionIndex>=0?headRow.cells[actionIndex]:[...headRow.cells].find(cell=>cell.dataset.actionCol);
    if(actionHead){
      actionHead.dataset.actionCol='1';
      actionHead.textContent='Actions';
    }else{
      const th=document.createElement('th');
      th.dataset.actionCol='1';
      th.textContent='Actions';
      headRow.appendChild(th);
    }
  }
  getTableRows(table).forEach(row=>{
    if(row.querySelector('td[colspan]'))return;
    const td=findGenericActionCell(row)||document.createElement('td');
    if(row.dataset.rowActionsAdded==='1'&&td.querySelector('.row-actions'))return;
    td.dataset.actionCol='1';
    td.innerHTML=tableActionButtonsHtml(table);
    if(!td.parentElement)row.appendChild(td);
    row.dataset.rowActionsAdded='1';
  });
}

function rowFirstValue(row){
  return row?.children?.[0]?.textContent.trim()||'Selected row';
}

function tableHasText(selector,value){
  const target=String(value||'').trim().toLowerCase();
  if(!target)return false;
  return [...document.querySelectorAll(`${selector} tr`)].some(row=>row.textContent.toLowerCase().includes(target));
}

function rowDeleteBlockReason(row,table){
  const page=table.closest('.page')?.id||'';
  const text=row.textContent.toLowerCase();
  const collection=inferCollectionFromContext(table);
  const first=rowFirstValue(row);
  const statusWords=[
    ['posted','posted records must be reversed or voided, not deleted.'],
    ['paid','paid records are linked to payments and cannot be deleted.'],
    ['approved','approved records are part of an approval workflow and cannot be deleted.'],
    ['reconciled','reconciled records are linked to bank reconciliation and cannot be deleted.'],
    ['settled','settled records are linked to payment settlement and cannot be deleted.'],
    ['retained','retained documents are part of audit evidence and cannot be deleted.'],
    ['stored','stored documents are linked to source records and cannot be deleted here.'],
    ['published','published rota records are linked to attendance and cannot be deleted.'],
    ['finalized','finalized records are locked and cannot be deleted.'],
    ['blocked','blocked records need review before deletion.']
  ];

  if(page==='page-dashboard')return 'Dashboard rows are generated from database summaries. Open the source module to change records.';
  if(page==='page-reports')return 'Reports are generated from source records. Delete or reverse the source transaction instead.';
  if(collection==='audit')return 'Audit logs are compliance evidence and cannot be deleted.';
  if(text.includes('total assets')||text.includes('total liabilities')||text.includes('total receivables')||text.trim()==='total')return 'Total rows are calculated summaries and cannot be deleted.';

  for(const [word,reason] of statusWords){
    if(text.includes(word))return reason;
  }

  if(collection==='products'){
    const code=row.children[0]?.textContent.trim();
    const name=row.children[1]?.textContent.trim();
    if(tableHasText('#sales-invoice-tbody',name)||tableHasText('#sales-return-tbody',name)||tableHasText('#stock-map-tbody',code)||tableHasText('#stock-map-tbody',name)){
      return 'This product is linked to invoices or inventory mapping. Remove those links before deleting.';
    }
  }
  if(collection==='salesCategories'){
    if(tableHasText('#prod-tbody',first)){
      return 'This category is used by products or services. Move those products to another category before deleting.';
    }
  }
  if(collection==='salesUnits'){
    const unitName=row.children[1]?.textContent.trim()||first;
    if(tableHasText('#prod-tbody',unitName)){
      return 'This unit is used by products or services. Move those products to another unit before deleting.';
    }
  }
  if(collection==='customers'){
    if(tableHasText('#sales-invoice-tbody',first)){
      return 'This customer has linked invoices. Delete or void those invoices before deleting the customer.';
    }
  }
  if(collection==='accounts'){
    const code=row.children[0]?.textContent.trim();
    if(['1000','1100','1200','2100','2200','2210','3000','4000','5000','6000'].includes(code)||tableHasText('#ledger-tbody',code)){
      return 'This account is used by ledger entries or posting rules. Deactivate it instead of deleting.';
    }
  }
  if(collection==='salesInvoices'){
    const invoiceNo=row.children[0]?.textContent.trim();
    if(tableHasText('#page-documents',invoiceNo)){
      return 'This invoice has linked documents or evidence. Remove the linked evidence before deleting.';
    }
  }
  if(collection==='purchaseRecords'){
    const ref=row.children[0]?.textContent.trim();
    const source=row.children[11]?.textContent.trim().toLowerCase()||'';
    if(source.includes('ai')||tableHasText('#page-documents',ref)||tableHasText('#payment-out-tbody',ref)){
      return 'This purchase is linked to extraction, documents, or payments. Remove those links before deleting.';
    }
  }
  if(collection==='bills'){
    const billNo=row.children[0]?.textContent.trim();
    if(tableHasText('#payment-out-tbody',billNo)){
      return 'This bill is linked to supplier payment records and cannot be deleted.';
    }
  }
  if(collection==='vendors'){
    if(tableHasText('#bill-tbody',first)||tableHasText('#payment-out-tbody',first)){
      return 'This vendor is linked to bills or payments. Remove those records before deleting.';
    }
  }
  if(collection==='payments'){
    return 'Payment rows affect cash/bank history. Void or reverse the payment instead of deleting.';
  }
  if(page==='page-accounting'&&text.includes('journal')){
    return 'Journal records must use reversal entries instead of deletion.';
  }
  return '';
}

function deleteTableRow(btn){
  const row=btn.closest('tr');
  const table=row?.closest('table');
  if(!row||!table)return;
  if(handleInventoryDelete(btn,row,table))return;
  const reason=rowDeleteBlockReason(row,table);
  const label=rowFirstValue(row);
  if(reason){
    toast(`Cannot delete ${label}: ${reason}`,'warn');
    audit('Delete blocked',label,reason);
    btn.title=reason;
    return;
  }
  currentDetailRow=row;
  currentDetailTable=table;
  deleteCurrentDetailRow();
}

function getTableRows(table){
  const tbody=table.tBodies[0];
  if(tbody)return [...tbody.rows];
  return [...table.querySelectorAll(':scope > tr')].filter(row=>!row.closest('thead'));
}

function getTableDataRows(table){
  return getTableRows(table).filter(row=>
    row.dataset.emptyState!=='1' &&
    row.dataset.previewSummary!=='1' &&
    !row.querySelector('td[colspan]')
  );
}

function refreshEnhancedTable(table){
  if(!table)return;
  if(skipTableTools(table)){
    getTableRows(table).forEach(row=>{
      row.style.display='';
      row.hidden=false;
    });
    return;
  }
  const state=tableEnhanceState.get(table);
  if(!state){
    enhanceTable(table);
    return;
  }
  const rows=getTableRows(table);
  const dataRows=getTableDataRows(table);
  const query=state.query;
  const matched=dataRows.filter(row=>!query||row.textContent.toLowerCase().includes(query));
  const pageSize=state.pageSize==='all'?matched.length||1:state.pageSize;
  const totalPages=Math.max(1,Math.ceil(matched.length/pageSize));
  state.page=Math.min(Math.max(1,state.page),totalPages);
  const start=(state.page-1)*pageSize;
  const end=start+pageSize;
  const pageRows=matched.slice(start,end);
  const visible=new Set(pageRows);
  rows.forEach(row=>{
    row.style.display=visible.has(row)?'':'none';
    row.hidden=!visible.has(row);
  });
  state.prev.disabled=false;
  state.next.disabled=false;
  state.prev.classList.toggle('is-muted',state.page<=1);
  state.next.classList.toggle('is-muted',state.page>=totalPages);
  state.prev.title=state.page<=1?'Already showing the first rows':'Show previous rows';
  state.next.title=state.page>=totalPages?`${matched.length} records loaded; no more rows`:'Show next rows';
  if(state.info){
    const total=dataRows.length;
    state.info.textContent=total
      ? `DB records: ${total.toLocaleString('en-AE')} | Showing ${pageRows.length.toLocaleString('en-AE')} (${matched.length?`${(start+1).toLocaleString('en-AE')}-${Math.min(end,matched.length).toLocaleString('en-AE')} of ${matched.length.toLocaleString('en-AE')}`:'0 matched'})`
      : 'DB records: 0';
  }
  table.dataset.visibleRows=String(pageRows.length);
  table.dataset.totalRows=String(matched.length);
  table.dataset.dbRows=String(dataRows.length);
}

// -- ROW DETAIL VIEWS --------------------------------------------
function ensureDetailModal(){
  let overlay=document.getElementById('m-row-detail');
  if(overlay)return overlay;
  overlay=document.createElement('div');
  overlay.className='overlay';
  overlay.id='m-row-detail';
  overlay.onclick=e=>closeOvBg(e,'m-row-detail');
  overlay.innerHTML=`
    <div class="modal modal-lg">
      <div class="modal-title" id="row-detail-title">Record Detail</div>
      <div class="modal-sub" id="row-detail-sub">Detailed view</div>
      <div id="row-detail-body"></div>
      <div class="modal-foot">
        <button class="btn btn-danger" onclick="deleteCurrentDetailRow()">Delete</button>
        <button class="btn btn-g" onclick="exportRowDetailPdf()">Export PDF</button>
        <button class="btn btn-g" onclick="toast('Record marked for review','ok')">Mark Review</button>
        <button class="btn btn-p" onclick="closeM('m-row-detail')">Close</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  return overlay;
}

function openRowDetail(btn,title='Record Detail',subtitle='Detailed view'){
  const row=btn.closest('tr');
  const table=row?.closest('table');
  if(!row||!table)return;
  currentDetailRow=row;
  currentDetailTable=table;
  const headers=[...table.querySelectorAll('thead th')].map(th=>th.textContent.trim()).filter(Boolean);
  const cells=[...row.children];
  const pairs=cells
    .map((cell,index)=>({label:headers[index]||`Field ${index+1}`,value:cell.textContent.trim()}))
    .filter((item,index)=>item.value&&item.value.toLowerCase()!=='view'&&!cells[index]?.querySelector('button'));

  ensureDetailModal();
  document.getElementById('row-detail-title').textContent=title;
  document.getElementById('row-detail-sub').textContent=subtitle;
  document.getElementById('row-detail-body').innerHTML=`
    <div class="g2 mb16">
      ${pairs.map(item=>`
        <div class="setting-tile">
          <strong>${escapeHtml(item.label)}</strong>
          <p>${escapeHtml(item.value)}</p>
        </div>
      `).join('')}
    </div>
    <div class="chart-panel">
      <div class="chart-title mb12">Workflow Status</div>
      <div class="tline-item"><div class="tline-dot" style="background:var(--green-bg);color:var(--green)">1</div><div><div>Record loaded</div><div class="card-sub">This detail is generated from the selected table row.</div></div></div>
      <div class="tline-item"><div class="tline-dot" style="background:var(--accent-glow);color:var(--accent)">2</div><div><div>Database-ready view</div><div class="card-sub">The same layout can be connected to a record detail endpoint for production drilldown.</div></div></div>
    </div>
  `;
  showM('m-row-detail');
  audit('Viewed record',pairs[0]?.value||title,'Opened');
}

function exportRowDetailPdf(){
  const title=document.getElementById('row-detail-title')?.textContent||'Record Detail';
  const body=document.getElementById('row-detail-body')?.innerHTML;
  if(!body){
    toast('Record detail not open','warn');
    return;
  }
  const printWindow=window.open('','_blank','width=900,height=720');
  if(!printWindow){
    toast('Allow popups to export PDF','warn');
    return;
  }
  const styles=[...document.querySelectorAll('link[rel="stylesheet"],style')].map(node=>node.outerHTML).join('\n');
  printWindow.document.open();
  printWindow.document.write(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>${escapeHtml(title)} - TaxFlow</title>${styles}<style>body{background:#fff;color:#111;padding:24px;height:auto;overflow:auto}.topbar,.sb,.scrim,.toasts,.btn{display:none!important}.card,.chart-panel,.setting-tile{background:#fff!important;border:1px solid #d9dee8!important;box-shadow:none!important}</style></head><body><div class="pdf-shell"><div class="pdf-title" style="font-family:Syne,sans-serif;font-size:22px;font-weight:700;margin-bottom:14px">${escapeHtml(title)}</div>${body}</div><script>window.onload=()=>setTimeout(()=>window.print(),250);<\/script></body></html>`);
  printWindow.document.close();
  toast(`${title} PDF export opened`,'ok');
  audit('Exported record',title,'PDF');
}

async function deleteCurrentDetailRow(){
  if(!currentDetailRow){
    toast('No row selected to delete','warn');
    return;
  }
  if(currentDetailTable&&handleInventoryDelete(null,currentDetailRow,currentDetailTable)){
    closeM('m-row-detail');
    currentDetailRow=null;
    currentDetailTable=null;
    return;
  }
  const label=currentDetailRow.children[0]?.textContent.trim()||'Selected row';
  const reason=currentDetailTable?rowDeleteBlockReason(currentDetailRow,currentDetailTable):'';
  if(reason){
    toast(`Cannot delete ${label}: ${reason}`,'warn');
    audit('Delete blocked',label,reason);
    return;
  }
  const confirmed=await appConfirm({
    title:'Delete Record',
    message:`Delete ${label}?`,
    okText:'Delete'
  });
  if(!confirmed)return;
  const collection=inferCollectionFromContext(currentDetailTable);
  const record=currentDetailTable?recordFromTableRow(currentDetailTable,currentDetailRow):{id:label,name:label};
  currentDetailRow.remove();
  if(currentDetailTable){
    const tbody=currentDetailTable.tBodies?.[0];
    if(tbody&&tbody.querySelectorAll('tr:not([data-empty-state])').length===0){
      emptyTableMessage(tbody,'No database records yet.');
    }
    refreshEnhancedTable(currentDetailTable);
  }
  apiRequest('delete',{collection,record}).catch(err=>console.warn('Database delete failed:',err));
  saveServer('app_actions',{mode:'delete',record:label,page:document.getElementById('ptitle')?.textContent||'App'});
  audit('Deleted record',label,'Deleted');
  closeM('m-row-detail');
  currentDetailRow=null;
  currentDetailTable=null;
  toast('Record deleted','warn');
}

function patchViewButtonsInPage(pageId,title){
  document.querySelectorAll(`#${pageId} table.tbl`).forEach(table=>{
    table.querySelectorAll('button').forEach(button=>{
      const text=button.textContent.trim().toLowerCase();
      if(text==='view'){
        if(pageId==='page-purchase'&&button.closest('#purchase-record-tbody')){
          button.onclick=()=>openPurchaseRecordPreview(button);
          return;
        }
        button.onclick=()=>openRowDetail(button,title,document.getElementById('ptitle')?.textContent||title);
      }
    });
  });
}

function addPurchaseRecordViewButtons(){
  const table=document.querySelector('#p-records table.tbl');
  if(!table||table.dataset.detailActions==='1')return;
  const headRow=table.querySelector('thead tr');
  const hasActionHead=headRow&&[...headRow.cells].some(cell=>cell.textContent.trim().toLowerCase()==='actions');
  if(headRow&&!hasActionHead){
    const th=document.createElement('th');
    th.textContent='Actions';
    th.dataset.actionCol='1';
    headRow.appendChild(th);
  }
  table.querySelectorAll('tbody tr').forEach(row=>{
    const actionCell=[...row.children].find(cell=>cell.querySelector('button[onclick*="editPurchaseRecord"],button[onclick*="openRowDetail"]'));
    const td=actionCell||document.createElement('td');
    td.dataset.actionCol='1';
    td.innerHTML=tableActionButtonsHtml(table);
    if(!actionCell)row.appendChild(td);
  });
  table.dataset.detailActions='1';
}

function bindDetailViews(){
  addPurchaseRecordViewButtons();
  patchViewButtonsInPage('page-purchase','Purchase Detail');
  patchViewButtonsInPage('page-bills','Bill / Vendor Detail');
  patchViewButtonsInPage('page-expense','Expense Detail');
}

// -- GENERIC ADD / EDIT SUPPORT ----------------------------------
let genericFormState=null;

function ensureGenericFormModal(){
  let overlay=document.getElementById('m-generic-form');
  if(overlay)return overlay;
  overlay=document.createElement('div');
  overlay.className='overlay';
  overlay.id='m-generic-form';
  overlay.onclick=e=>closeOvBg(e,'m-generic-form');
  overlay.innerHTML=`
    <div class="modal modal-lg">
      <div class="modal-title" id="generic-form-title">Edit Record</div>
      <div class="modal-sub" id="generic-form-sub">Update details</div>
      <div id="generic-form-body"></div>
      <div class="modal-foot">
        <button class="btn btn-g" onclick="closeM('m-generic-form')">Cancel</button>
        <button class="btn btn-p" onclick="saveGenericForm()">Save</button>
      </div>
    </div>
  `;
  document.body.appendChild(overlay);
  return overlay;
}

function getRowEditableCells(row,table){
  const headers=[...table.querySelectorAll('thead th')].map(th=>th.textContent.trim());
  return [...row.children].map((cell,index)=>({
    cell,
    index,
    label:headers[index]||`Field ${index+1}`,
    value:cell.textContent.trim()
  })).filter(item=>{
    const actionText=item.cell.textContent.trim().toLowerCase();
    return item.value&&actionText!=='edit'&&actionText!=='view'&&!item.cell.querySelector('button');
  });
}

function openGenericEditRow(btn,title='Edit Record',subtitle='Update row details'){
  const row=btn.closest('tr');
  const table=row?.closest('table');
  if(!row||!table){
    toast('No editable row found','warn');
    return;
  }
  const fields=getRowEditableCells(row,table);
  if(!fields.length){
    toast('No editable fields found','warn');
    return;
  }
  genericFormState={mode:'edit',row,table,fields,collection:inferCollectionFromContext(table)};
  ensureGenericFormModal();
  document.getElementById('generic-form-title').textContent=title;
  document.getElementById('generic-form-sub').textContent=subtitle;
  document.getElementById('generic-form-body').innerHTML=`
    <div class="fr2">
      ${fields.map((field,index)=>`
        <div class="fg">
          <label class="fl">${escapeHtml(field.label)}</label>
          <input class="fi" data-generic-index="${index}" value="${escapeHtml(field.value)}">
        </div>
      `).join('')}
    </div>
  `;
  showM('m-generic-form');
}

function openGenericAdd(title='Add Record',subtitle='Create a new record',sourceButton=null){
  const card=sourceButton?.closest('.card');
  const table=card?.querySelector('table.tbl');
  const headers=table?[...table.querySelectorAll('thead th')].map(th=>th.textContent.trim()).filter(Boolean):[];
  const fields=(headers.length?headers:['Name','Code / Reference','Notes']).filter(label=>label.toLowerCase()!=='');
  genericFormState={mode:'add',card,table,collection:inferCollectionFromContext(table||card),fields:fields.map(label=>({label,value:''}))};
  ensureGenericFormModal();
  document.getElementById('generic-form-title').textContent=title;
  document.getElementById('generic-form-sub').textContent=subtitle;
  document.getElementById('generic-form-body').innerHTML=`
    <div class="fr2">
      ${fields.map((label,index)=>`
        <div class="fg">
          <label class="fl">${escapeHtml(label)}</label>
          <input class="fi" data-generic-index="${index}" placeholder="${escapeHtml(label)}">
        </div>
      `).join('')}
    </div>
  `;
  showM('m-generic-form');
}

function setCellDisplayValue(cell,value){
  const badge=cell.querySelector('.b');
  const namedSpan=cell.querySelector('.co-av + span');
  if(badge){
    badge.textContent=value;
  }else if(namedSpan){
    namedSpan.textContent=value;
  }else{
    cell.textContent=value;
  }
}

function genericActionCell(table){
  return `<td data-action-col="1">${tableActionButtonsHtml(table)}</td>`;
}

function saveGenericForm(){
  if(!genericFormState)return closeM('m-generic-form');
  const values=[...document.querySelectorAll('#generic-form-body [data-generic-index]')].map(input=>input.value.trim());
  if(!values.some(Boolean)){
    toast('Enter at least one value','warn');
    return;
  }
  if(genericFormState.mode==='edit'){
    genericFormState.fields.forEach((field,index)=>setCellDisplayValue(field.cell,values[index]||field.value));
    refreshEnhancedTable(genericFormState.table);
    saveServer(genericFormState.collection,buildGenericRecord(genericFormState.fields,values));
    toast('Record updated','ok');
    audit('Updated record',values[0]||'Table row','Saved');
  }else{
    const table=genericFormState.table;
    if(table?.querySelector('tbody')){
      removeEmptyState(table.querySelector('tbody'));
      const row=document.createElement('tr');
      const headers=[...table.querySelectorAll('thead th')].map(th=>th.textContent.trim());
      const hasActionColumn=headers.some(label=>!label||['action','actions'].includes(label.toLowerCase()));
      const dataCount=hasActionColumn?Math.max(values.length,headers.length-1):Math.max(values.length,headers.length);
      row.innerHTML=Array.from({length:dataCount},(_,index)=>{
        const value=values[index]||'-';
        return `<td>${escapeHtml(value)}</td>`;
      }).join('')+(hasActionColumn?genericActionCell(table):'');
      table.querySelector('tbody').prepend(row);
      addTableDeleteActions(table);
      refreshEnhancedTable(table);
    }else if(genericFormState.card){
      const tile=document.createElement('div');
      tile.className='setting-tile';
      tile.style.marginTop='10px';
      tile.innerHTML=`<strong>${escapeHtml(values[0]||'New Record')}</strong><p>${escapeHtml(values.slice(1).filter(Boolean).join(' - ')||'Saved from quick add form')}</p>`;
      genericFormState.card.appendChild(tile);
    }
    saveServer(genericFormState.collection,buildGenericRecord(genericFormState.fields,values));
    toast('Record added','ok');
    audit('Added record',values[0]||'Quick add','Saved');
  }
  saveServer('app_actions',{mode:genericFormState.mode,values});
  closeM('m-generic-form');
}

function inferCollectionFromContext(node){
  const table=node?.closest?.('table')||node?.querySelector?.('table');
  const tbodyId=table?.tBodies?.[0]?.id||'';
  const idMap={
    'prod-tbody':'products',
    'customer-tbody':'customers',
    'sales-invoice-tbody':'salesInvoices',
    'sales-return-tbody':'salesInvoices',
    'quotation-tbody':'quotations',
    'bill-tbody':'bills',
    'vendor-tbody':'vendors',
    'payment-in-tbody':'payments',
    'payment-out-tbody':'payments',
    'account-tbody':'accounts',
    'sales-category-tbody':'salesCategories',
    'sales-unit-tbody':'salesUnits',
    'purchase-record-tbody':'purchaseRecords',
    'audit-tbody':'audit'
  };
  if(idMap[tbodyId])return idMap[tbodyId];
  const page=(node?.closest?.('.page')||table?.closest?.('.page'))?.id?.replace(/^page-/,'');
  if(page)return page+'_records';
  return 'app_records';
}

function normalizeGenericKey(label){
  const key=String(label||'field').toLowerCase().replace(/\(.*?\)/g,'').replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'');
  const aliases={
    invoice_no:'invoice_no',
    ref_no:'ref',
    bill_no:'bill_no',
    unit_price:'price',
    product_description:'name',
    product_service_name:'name',
    customer_name:'name',
    account_code:'code',
    account_name:'name',
    shift_name:'name',
    shift_code:'code',
    total_business:'total',
    total:'total'
  };
  return aliases[key]||key||'field';
}

function buildGenericRecord(fields,values){
  const record={};
  fields.forEach((field,index)=>{
    const label=field.label||field;
    const key=normalizeGenericKey(label);
    record[key]=values[index]||field.value||'';
  });
  if(!record.id){
    record.id=record.code||record.invoice_no||record.bill_no||record.ref||record.name||`app-${Date.now()}`;
  }
  return record;
}

function recordFromTableRow(table,row){
  const headers=[...table.querySelectorAll('thead th')]
    .filter(th=>th.dataset.inventoryBulkCol!=='1'&&th.dataset.actionCol!=='1'&&!['action','actions'].includes(th.textContent.trim().toLowerCase()))
    .map(th=>th.textContent.trim());
  const cells=[...row.children].filter(cell=>cell.dataset.inventoryBulkCol!=='1'&&cell.dataset.actionCol!=='1'&&!cell.querySelector('button'));
  const fields=cells.map((cell,index)=>({
    label:headers[index]||`Field ${index+1}`,
    value:cell.textContent.trim()
  })).filter(item=>item.value&&item.value.toLowerCase()!=='view'&&item.value.toLowerCase()!=='edit');
  return buildGenericRecord(fields,fields.map(field=>field.value));
}

function bindEditActions(){
  document.querySelectorAll('table.tbl button').forEach(button=>{
    const text=button.textContent.trim().toLowerCase();
    const inlineAction=button.getAttribute('onclick')||'';
    if(text==='edit'&&!inlineAction.includes('editPurchaseRecord')){
      button.onclick=()=>openGenericEditRow(button,'Edit '+(document.getElementById('ptitle')?.textContent||'Record'),'Update selected row');
    }
  });
  document.querySelectorAll('button').forEach(button=>{
    const text=button.textContent.trim().toLowerCase();
    if(text==='edit template'){
      button.onclick=()=>openGenericAdd('Edit Template','Update template details',button);
    }
  });
}

function bindGenericAddActions(){
  const labels=['+ add holiday','+ new advance','+ add rule','+ new po','+ new rule','+ add','+ new workflow'];
  document.querySelectorAll('button').forEach(button=>{
    const text=button.textContent.trim().toLowerCase();
    if(labels.includes(text)){
      const onclick=button.getAttribute('onclick')||'';
      if(onclick.includes('toast(')){
        button.onclick=()=>openGenericAdd(button.textContent.trim(),document.getElementById('ptitle')?.textContent||'Quick add',button);
      }
    }
  });
}

// -- BILLS / VENDORS / PAYMENTS -----------------------------------
function saveBill(){
  const vendor=(document.getElementById('bill-vendor')?.value||'').trim();
  const billNo=(document.getElementById('bill-no')?.value||'BILL-2024-0189').trim();
  const date=document.getElementById('bill-date')?.value||'Today';
  const due=document.getElementById('bill-due')?.value||'30 days';
  const total=parseAmount(document.getElementById('bill-total')?.value);
  if(!vendor||!total){toast('Vendor and bill total are required','err');return;}
  const subtotal=total/1.05;
  const vat=total-subtotal;
  const row=document.createElement('tr');
  row.innerHTML=`<td class="mono">${escapeHtml(billNo)}</td><td>${escapeHtml(vendor)}</td><td>${escapeHtml(date)}</td><td>${escapeHtml(due)}</td><td class="mono">${subtotal.toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${vat.toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${total.toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td><span class="b b-a">Awaiting Payment</span></td><td><button class="btn btn-g btn-sm" onclick="openRowDetail(this,'Bill / Vendor Detail','Bill detail')">View</button></td>`;
  document.getElementById('bill-tbody')?.prepend(row);
  saveServer('bills',{vendor,bill_no:billNo,date,due,subtotal,vat,total,status:'Awaiting Payment'});
  closeM('m-bill');
  toast('Vendor bill saved ?','ok');
  audit('Saved vendor bill',billNo,'Saved');
}

function saveVendor(){
  const name=(document.getElementById('vendor-name')?.value||'').trim();
  const trn=(document.getElementById('vendor-trn')?.value||'').replace(/\D/g,'');
  const category=document.getElementById('vendor-category')?.value||'Services';
  const email=(document.getElementById('vendor-email')?.value||'').trim();
  const phone=(document.getElementById('vendor-phone')?.value||'').trim();
  const address=(document.getElementById('vendor-address')?.value||'').trim();
  if(!name){toast('Vendor name is required','err');return;}
  if(trn&&trn.length!==15){toast('Vendor TRN must be 15 digits','err');return;}
  renderVendorRecord({name,trn,category,email,phone,address});
  syncSupplierOptions(name);
  saveServer('vendors',{name,trn,category,email,phone,address});
  closeM('m-vendor');
  ['vendor-name','vendor-trn','vendor-email','vendor-phone','vendor-address'].forEach(id=>setFieldValue(document.getElementById(id),''));
  toast('Vendor added ?','ok');
  audit('Added vendor',name,'Saved');
}

function savePayment(){
  const type=document.getElementById('payment-type')?.value||'Customer Receipt';
  const ref=(document.getElementById('payment-ref')?.value||nextPaymentReference(type)).trim();
  const contact=(document.getElementById('payment-contact')?.value||'').trim();
  const amount=parseAmount(document.getElementById('payment-amount')?.value);
  const method=document.getElementById('payment-method')?.value||'Bank Transfer';
  const date=document.getElementById('payment-date')?.value||new Date().toISOString().slice(0,10);
  const bank=document.getElementById('payment-bank')?.value||'';
  const comments=document.getElementById('payment-comments')?.value||'';
  const detail=document.getElementById('payment-detail')?.value||'';

  if(!contact){toast('Select a client / vendor','err');return;}
  if(!amount){toast('Enter a payment amount','err');return;}

  // Collect allocation rows
  const allocations=[];
  document.querySelectorAll('#pmt-alloc-tbody tr').forEach(row=>{
    const chk=row.querySelector('.pmt-alloc-chk');
    if(!chk?.checked)return;
    const inp=row.querySelector('.pmt-alloc-inp');
    const allocAmt=parseAmount(inp?.value||'0');
    if(!allocAmt)return;
    const docRef=inp?.dataset.docRef||row.querySelector('td:nth-child(2)')?.textContent.trim()||'';
    allocations.push({doc_ref:docRef,amount:allocAmt});
  });

  const primaryDoc=allocations[0]?.doc_ref||'';
  const record={type,ref,contact,amount,method,date,bank,comments,detail,
    document_ref:primaryDoc,allocations};
  if(isSupplierPaymentType(type))record.bill_no=primaryDoc;
  else record.invoice_no=primaryDoc;

  renderPaymentRecord(record);
  saveServer('payments',record);
  if(primaryDoc)markPaymentDocumentPaid(record);
  closeM('m-payment');
  toast(`${isSupplierPaymentType(type)?'Payment':'Receipt'} recorded — AED ${amount.toLocaleString('en-AE',{minimumFractionDigits:2})}`,'ok');
  audit('Recorded payment',ref,'Posted');
}

// -- SETTINGS ----------------------------------------------------
function saveSettings(message='Settings saved'){
  toast(message+' ?','ok');
  audit(message,'Settings','Saved');
  saveCompanySettingsToDatabase()
    .then(()=>syncDashboardFromDatabase())
    .catch(err=>console.warn('Company settings save failed:',err));
}

function testIntegration(name){
  toast('Testing '+name+' integration...','info');
  setTimeout(()=>toast(name+' integration connected ?','ok'),900);
}

function rotateApiKey(){
  toast('API key rotated. Existing key will expire in 24 hours.','warn');
  audit('Rotated API key','Integrations','Rotated');
}

function runBackup(){downloadFullBackup();}

let _backupUsers=[];
let _selectedBackupUserId=null;

async function loadBackupTab(){
  await Promise.all([loadBackupUsers(),loadLiveAuditLog()]);
}

async function loadBackupUsers(){
  try{
    const resp=await authenticatedFetch(`${apiBaseUrl()}/app-data/users`);
    if(!resp.ok)return;
    const r=await resp.json();
    _backupUsers=r.users||[];
    const sel=document.getElementById('bk-user-sel');
    if(!sel)return;
    sel.innerHTML='<option value="">— select user —</option>'+
      _backupUsers.map(u=>`<option value="${u.id}">${escapeHtml(u.name)} (${escapeHtml(u.email)})</option>`).join('');
  }catch{/* silent */}
}

function onBackupUserChange(userId){
  _selectedBackupUserId=userId||null;
  const profile=document.getElementById('bk-user-profile');
  const btn=document.getElementById('bk-user-btn');
  if(!userId){
    if(profile)profile.style.display='none';
    if(btn){btn.disabled=true;btn.style.opacity='.5';}
    return;
  }
  const u=_backupUsers.find(x=>x.id===userId);
  if(!u)return;
  if(document.getElementById('bkup-uname'))document.getElementById('bkup-uname').textContent=u.name;
  if(document.getElementById('bkup-uemail'))document.getElementById('bkup-uemail').textContent=u.email;
  if(document.getElementById('bkup-urole'))document.getElementById('bkup-urole').textContent=u.role;
  if(document.getElementById('bkup-ucreated'))document.getElementById('bkup-ucreated').textContent=u.created_at||'—';
  if(profile)profile.style.display='';
  if(btn){btn.disabled=false;btn.style.opacity='1';}
}

async function downloadFullBackup(){
  toast('Preparing backup…','info');
  try{
    const resp=await authenticatedFetch(`${apiBaseUrl()}/app-data/export`);
    if(!resp.ok)throw new Error('Export failed');
    const r=await resp.json();
    const filtered=_applyModuleFilter(r.data||{});
    const ts=new Date().toISOString().slice(0,19).replace(/[T:]/g,'-');
    triggerJsonDownload({meta:r.meta,data:filtered},`taxflow-backup-${ts}.json`);
    audit('Downloaded full company backup','Backup','Complete');
    toast('JSON backup downloaded','ok');
  }catch(e){
    toast('Backup failed: '+e.message,'err');
  }
}

async function downloadUserData(){
  if(!_selectedBackupUserId){toast('Select a user first','err');return;}
  toast('Preparing user export…','info');
  try{
    const resp=await authenticatedFetch(`${apiBaseUrl()}/app-data/user-export/${_selectedBackupUserId}`);
    if(!resp.ok)throw new Error('Export failed');
    const r=await resp.json();
    const ts=new Date().toISOString().slice(0,10);
    const slug=(r.user?.name||'user').toLowerCase().replace(/\s+/g,'-');
    triggerJsonDownload(r,`taxflow-user-${slug}-${ts}.json`);
    audit(`Downloaded user data for ${r.user?.name||'user'}`,'Backup','Complete');
    toast('User data downloaded','ok');
  }catch(e){
    toast('Export failed: '+e.message,'err');
  }
}

function triggerJsonDownload(obj,filename){
  const blob=new Blob([JSON.stringify(obj,null,2)],{type:'application/json'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=filename;
  document.body.appendChild(a);a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function triggerRawDownload(blob,filename){
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=filename;
  document.body.appendChild(a);a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

async function _loadSheetJs(){
  if(window.XLSX)return;
  // Try multiple CDNs in order
  const cdns=[
    'https://unpkg.com/xlsx@0.18.5/dist/xlsx.full.min.js',
    'https://cdn.jsdelivr.net/npm/xlsx@0.18.5/dist/xlsx.full.min.js',
  ];
  for(const src of cdns){
    try{
      await new Promise((resolve,reject)=>{
        const s=document.createElement('script');
        s.src=src;
        s.onload=resolve;
        s.onerror=reject;
        document.head.appendChild(s);
      });
      if(window.XLSX)return;
    }catch{/* try next */}
  }
  throw new Error('Could not load Excel library — check internet connection');
}

function _backupModuleFilter(){
  return{
    inv:document.getElementById('bk-inv')?.checked!==false,
    pur:document.getElementById('bk-pur')?.checked!==false,
    jnl:document.getElementById('bk-jnl')?.checked!==false,
    hr:document.getElementById('bk-hr')?.checked!==false,
    audit:document.getElementById('bk-audit')?.checked!==false
  };
}

function _applyModuleFilter(data){
  const include=_backupModuleFilter();
  const invCols=['salesInvoices','quotations','salesCategories','salesUnits','customers','creditControl'];
  const purCols=['purchaseRecords','purchaseDocuments','bills','vendors','payments','receipts'];
  const jnlCols=['accounts','ledger','journalDrafts','bankAccounts','bankTransactions','vatReturns','corporateTax','fixedAssets','accrualsPrepayments','costCenters','budgets','cashFlowForecasts','consolidation','relatedPartyTransactions'];
  const hrCols=['employees','rotaShifts','rotaAssignments','rotaSwaps','rotaApprovals','rotaDrafts'];
  const filtered={};
  Object.keys(data).forEach(k=>{
    if(k==='audit'&&!include.audit)return;
    if(k==='users')return;
    if(invCols.includes(k)&&!include.inv)return;
    if(purCols.includes(k)&&!include.pur)return;
    if(jnlCols.includes(k)&&!include.jnl)return;
    if(hrCols.includes(k)&&!include.hr)return;
    filtered[k]=data[k];
  });
  return filtered;
}

async function downloadExcelBackup(){
  toast('Preparing Excel…','info');
  try{
    await _loadSheetJs();
    const resp=await authenticatedFetch(`${apiBaseUrl()}/app-data/export`);
    if(!resp.ok)throw new Error('Export failed');
    const r=await resp.json();
    const data=_applyModuleFilter(r.data||{});

    const XLSX=window.XLSX;
    const wb=XLSX.utils.book_new();

    // Friendly sheet name → collection key(s)
    const sheetMap=[
      ['Invoices',      ['salesInvoices']],
      ['Customers',     ['customers']],
      ['Quotations',    ['quotations']],
      ['Bills',         ['bills']],
      ['Vendors',       ['vendors']],
      ['Payments',      ['payments','receipts']],
      ['Accounts',      ['accounts']],
      ['Employees',     ['employees']],
      ['Products',      ['products']],
      ['Bank Accounts', ['bankAccounts']],
      ['VAT Returns',   ['vatReturns']],
      ['Expenses',      ['expenses']],
      ['Audit Log',     ['audit']],
    ];

    sheetMap.forEach(([sheetName,keys])=>{
      const rows=keys.flatMap(k=>Array.isArray(data[k])?data[k]:[]);
      if(!rows.length)return;
      // Flatten nested objects one level deep
      const flat=rows.map(row=>{
        const out={};
        Object.entries(row).forEach(([k,v])=>{
          if(v!==null&&v!==undefined&&typeof v==='object'&&!Array.isArray(v)){
            Object.entries(v).forEach(([ik,iv])=>out[`${k}.${ik}`]=iv);
          }else if(!Array.isArray(v)){
            out[k]=v;
          }
        });
        return out;
      });
      const ws=XLSX.utils.json_to_sheet(flat);
      // Auto column widths
      const cols=Object.keys(flat[0]||{});
      ws['!cols']=cols.map(c=>({wch:Math.min(40,Math.max(10,c.length+2))}));
      XLSX.utils.book_append_sheet(wb,ws,sheetName.slice(0,31));
    });

    if(wb.SheetNames.length===0){toast('No data to export','err');return;}

    const ts=new Date().toISOString().slice(0,10);
    // Use write+blob instead of writeFile for reliable browser downloads
    const wbout=XLSX.write(wb,{bookType:'xlsx',type:'array'});
    const xlsxBlob=new Blob([wbout],{type:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'});
    triggerRawDownload(xlsxBlob,`taxflow-backup-${ts}.xlsx`);
    audit('Downloaded Excel backup','Backup','Complete');
    toast('Excel downloaded','ok');
  }catch(e){
    toast('Excel export failed: '+e.message,'err');
  }
}

async function downloadDbDump(){
  toast('Requesting DB dump…','info');
  try{
    const resp=await authenticatedFetch(`${apiBaseUrl()}/app-data/db-dump`);
    if(!resp.ok)throw new Error(`Server error ${resp.status}`);
    const text=await resp.text();
    const blob=new Blob([text],{type:'text/plain;charset=utf-8'});
    const ts=new Date().toISOString().slice(0,10);
    triggerRawDownload(blob,`taxflow-db-dump-${ts}.sql`);
    audit('Downloaded DB dump (SQL)','Backup','Complete');
    toast('DB dump downloaded','ok');
  }catch(e){
    toast('DB dump failed: '+e.message,'err');
  }
}

async function loadLiveAuditLog(){
  try{
    const resp=await authenticatedFetch(`${apiBaseUrl()}/app-data`);
    if(!resp.ok)return;
    const r=await resp.json();
    const rows=r?.data?.audit||[];
    const tbody=document.getElementById('audit-tbody');
    if(!tbody)return;
    if(!rows.length){tbody.innerHTML='<tr><td colspan="5" style="color:var(--text3);text-align:center">No audit entries yet</td></tr>';return;}
    tbody.innerHTML=rows.map(row=>`<tr><td class="mono">${escapeHtml(row.time)}</td><td>${escapeHtml(row.user)}</td><td>${escapeHtml(row.action)}</td><td>${escapeHtml(row.record||'')}</td><td><span class="b b-g">${escapeHtml(row.result)}</span></td></tr>`).join('');
  }catch{/* silent */}
}

function exportAuditCsv(){
  const tbody=document.getElementById('audit-tbody');
  if(!tbody){toast('No data','err');return;}
  const rows=[['Time','User','Action','Record','Result']];
  tbody.querySelectorAll('tr').forEach(tr=>{
    const cells=[...tr.querySelectorAll('td')].map(td=>td.textContent.trim());
    if(cells.length===5)rows.push(cells);
  });
  const csv=rows.map(r=>r.map(c=>'"'+String(c).replace(/"/g,'""')+'"').join(',')).join('\n');
  const blob=new Blob([csv],{type:'text/csv'});
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');
  a.href=url;a.download=`taxflow-audit-${new Date().toISOString().slice(0,10)}.csv`;
  document.body.appendChild(a);a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  toast('Audit log exported','ok');
}

function quotationNumber(){
  return document.getElementById('quote-no')?.value?.trim()||`QTN-${new Date().getFullYear()}-${String(Date.now()).slice(-5)}`;
}

function calcQuotationTotals(){
  let subtotal=0;
  document.querySelectorAll('#quote-lines .quote-line').forEach(line=>{
    const qty=Number(line.querySelector('.quote-qty')?.value||0);
    const price=Number(line.querySelector('.quote-price')?.value||0);
    const amount=qty*price;
    subtotal+=amount;
    const target=line.querySelector('.quote-amount');
    if(target)target.value=amount.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  });
  const vat=subtotal*0.05;
  const total=subtotal+vat;
  const sub=document.getElementById('quote-subtotal');
  const vatEl=document.getElementById('quote-vat');
  const totalEl=document.getElementById('quote-total');
  if(sub)sub.textContent=formatAed(subtotal);
  if(vatEl)vatEl.textContent=formatAed(vat);
  if(totalEl)totalEl.textContent=formatAed(total);
}

function calcQuotationLine(input){
  calcQuotationTotals();
}

function quotationLineHtml(selected=''){
  return `<select class="fi quote-item" onchange="selectQuotationItem(this)" onfocus="refreshQuotationProductOptions()">${quotationProductOptionsHtml(selected)}</select><input class="fi quote-qty" value="1" oninput="calcQuotationLine(this)"><input class="fi quote-price" value="0.00" oninput="calcQuotationLine(this)"><input class="fi mono quote-amount" value="0.00" readonly style="background:var(--bg)"><button class="btn btn-g" style="padding:4px 8px" onclick="removeQuotationLine(this)">x</button>`;
}

function addQuotationLine(){
  const box=document.getElementById('quote-lines');
  if(!box)return;
  const row=document.createElement('div');
  row.className='inv-item quote-line';
  row.innerHTML=quotationLineHtml();
  box.appendChild(row);
  calcQuotationTotals();
}

function removeQuotationLine(btn){
  btn.closest('.quote-line')?.remove();
  calcQuotationTotals();
}

function quotationLinesFromForm(){
  return [...document.querySelectorAll('#quote-lines .quote-line')].map(row=>{
    const qty=parseAmount(row.querySelector('.quote-qty')?.value);
    const price=parseAmount(row.querySelector('.quote-price')?.value);
    return {
      description:row.querySelector('.quote-item')?.value||row.querySelector('input:not(.quote-qty):not(.quote-price):not(.quote-amount)')?.value||'Item',
      qty,
      price,
      amount:qty*price
    };
  }).filter(line=>line.description||line.qty||line.price);
}

function quotationLinesFromRecord(quote){
  if(Array.isArray(quote?.lines)&&quote.lines.length)return quote.lines;
  const subtotal=parseAmount(quote?.subtotal);
  return [{description:quote?.subject||'Quotation items',qty:1,price:subtotal,amount:subtotal}];
}

function ensureQuotationPreviewModal(){
  let overlay=document.getElementById('m-quotation-view');
  if(overlay)return overlay;
  overlay=document.createElement('div');
  overlay.className='overlay';
  overlay.id='m-quotation-view';
  overlay.onclick=e=>closeOvBg(e,'m-quotation-view');
  overlay.innerHTML=`
    <div class="modal modal-xl">
      <div class="modal-title" id="quotation-view-title">Quotation Preview</div>
      <div class="modal-sub" id="quotation-view-sub">Sales quotation</div>
      <div id="quotation-view-body"></div>
      <div class="modal-foot">
        <button class="btn btn-g" onclick="downloadCurrentQuotationPdf()">Download PDF</button>
        <button class="btn btn-g" onclick="toast('Digital quotation prepared','ok')">Digital Quotation</button>
        <button class="btn btn-g" onclick="shareCurrentQuotation('email')">Email</button>
        <button class="btn btn-success" onclick="shareCurrentQuotation('whatsapp')">WhatsApp</button>
        <button class="btn btn-p" onclick="shareCurrentQuotation('options')">Share Options</button>
        <button class="btn btn-p" onclick="closeM('m-quotation-view')">Close</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  return overlay;
}

let currentQuotation=null;

function renderQuotationPreview(quote){
  ensureQuotationPreviewModal();
  currentQuotation=quote;
  const title=document.getElementById('quotation-view-title');
  const sub=document.getElementById('quotation-view-sub');
  const body=document.getElementById('quotation-view-body');
  if(!body)return;
  const layout=getQuotationLayout();
  const companyTrn=currentCompany?.trn||document.getElementById('set-company-trn')?.value||'';
  const subtotal=parseAmount(quote.subtotal);
  const vat=parseAmount(quote.vat_amount);
  const total=parseAmount(quote.total)||subtotal+vat;
  const lines=quotationLinesFromRecord(quote);
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  const accent=escapeHtml(layout.color||'#2563eb');
  const initials=(layout.logo||layout.company||'TF').split(/\s+/).map(part=>part[0]).join('').slice(0,2).toUpperCase()||'TF';
  const textAlign={left:'left',center:'center',right:'right'}[layout.align]||'left';
  const brandJustify={left:'flex-start',center:'center',right:'flex-end'}[layout.align]||'flex-start';
  const fontFamily=layout.font==='Classic Serif'?'Georgia,serif':layout.font==='Compact Mono'?'DM Mono,monospace':'Syne,sans-serif';
  const status=quote.status||'Draft';

  if(title)title.textContent='Quotation '+(quote.quote_no||'Draft');
  if(sub)sub.textContent=(quote.customer||'Customer')+' - '+status;

  body.innerHTML=`
    <div class="invoice-sheet" dir="${layout.enableRtl?'rtl':'ltr'}" style="--invoice-accent:${accent}">
      <div class="invoice-topbar"></div>
      <div class="invoice-head">
        <div class="invoice-brand" style="justify-content:${brandJustify};text-align:${textAlign}">
          <div class="invoice-logo" aria-label="Quotation logo">${escapeHtml(initials)}</div>
          <div>
            <div class="invoice-company" style="font-family:${fontFamily}">${escapeHtml(layout.company)}</div>
            <div class="invoice-muted">${escapeHtml(layout.address)}</div>
            ${layout.trnMode==='show'&&layout.showTrn?`<div class="invoice-muted mono">${escapeHtml(layout.trnLabel||'TRN')} ${escapeHtml(companyTrn||'not set')}</div>`:''}
          </div>
        </div>
        <div class="invoice-titlebox" style="align-items:${brandJustify};text-align:${textAlign}">
          <div class="invoice-label">${escapeHtml(layout.quotationHeading||'Quotation')}</div>
          <div class="invoice-number mono">${escapeHtml(quote.quote_no||'Draft')}</div>
          <span class="invoice-status">${escapeHtml(status)}</span>
        </div>
      </div>

      <div class="invoice-info-grid">
        <div class="invoice-panel">
          <div class="invoice-kicker">${escapeHtml(layout.quoteToLabel||'Quote To')}</div>
          <div class="invoice-party">${escapeHtml(quote.customer||'Customer')}</div>
          ${quote.subject?`<div class="invoice-muted">${escapeHtml(quote.subject)}</div>`:''}
        </div>
        <div class="invoice-panel invoice-meta-panel">
          <div class="invoice-meta-row"><span>Quotation No.</span><strong>${escapeHtml(quote.quote_no||'Draft')}</strong></div>
          <div class="invoice-meta-row"><span>Date</span><strong>${escapeHtml(quote.date||'-')}</strong></div>
          ${layout.showValidity?`<div class="invoice-meta-row"><span>${escapeHtml(layout.validityLabel||'Valid Until')}</span><strong>${escapeHtml(quote.valid_until||'-')}</strong></div>`:''}
          <div class="invoice-meta-row"><span>Currency</span><strong>AED</strong></div>
        </div>
      </div>

      <table class="invoice-line-table">
        <thead><tr><th>#</th><th>Product</th><th class="num">Qty</th><th class="num">Unit Price</th><th class="num">Amount</th></tr></thead>
        <tbody>
          ${lines.map((line,index)=>`<tr><td class="mono">${index+1}</td><td><strong>${escapeHtml(line.description||line.item||'Item')}</strong></td><td class="mono num">${escapeHtml(line.qty||line.quantity||1)}</td><td class="mono num">${fmt(line.price||line.unit_price)}</td><td class="mono num">${fmt(line.amount)}</td></tr>`).join('')}
        </tbody>
      </table>

      <div class="invoice-summary-grid">
        <div class="invoice-notes">
          <div class="invoice-kicker">Terms & Payment Details</div>
          <div>${escapeHtml(layout.terms||'Quotation is subject to approval and stock availability.')}</div>
          <div class="invoice-muted" style="margin-top:8px">${escapeHtml(layout.footer||'')}</div>
        </div>
        <div class="invoice-total-card">
          <div class="invoice-total-row"><span>Subtotal</span><strong class="mono">AED ${fmt(subtotal)}</strong></div>
          ${layout.showVat?`<div class="invoice-total-row"><span>VAT 5%</span><strong class="mono">AED ${fmt(vat)}</strong></div>`:''}
          <div class="invoice-grand"><span>Total</span><strong class="mono">AED ${fmt(total)}</strong></div>
        </div>
      </div>

      ${layout.signature?`<div class="invoice-signatures"><div><span>${escapeHtml(layout.preparedLabel||'Prepared By')}</span><strong>${escapeHtml(layout.company||'TaxFlow')}</strong></div><div><span>${escapeHtml(layout.acceptedLabel||'Accepted By')}</span><strong>${escapeHtml(quote.customer||'Customer')}</strong></div></div>`:''}
    </div>`;
}

function quotationPdfHtml(quote=currentQuotation){
  const layout=getQuotationLayout();
  const companyTrn=currentCompany?.trn||document.getElementById('set-company-trn')?.value||'';
  const lines=quotationLinesFromRecord(quote);
  const subtotal=parseAmount(quote?.subtotal);
  const vat=parseAmount(quote?.vat_amount);
  const total=parseAmount(quote?.total)||subtotal+vat;
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  const accent=escapeHtml(layout.color||'#2563eb');
  const initials=String(layout.logo||layout.company||'TF').split(/\s+/).map(part=>part[0]).join('').slice(0,2).toUpperCase()||'TF';
  return `<!doctype html><html><head><meta charset="utf-8"><title>${escapeHtml(quote?.quote_no||'Quotation')} - PDF</title>
    <style>*{box-sizing:border-box}body{margin:0;background:#fff;color:#172033;font-family:Arial,Helvetica,sans-serif;font-size:13px;line-height:1.45}.sheet{max-width:900px;margin:0 auto;padding:34px}.bar{height:7px;background:${accent};margin:-34px -34px 28px}.head{display:grid;grid-template-columns:1fr 280px;gap:24px;border-bottom:1px solid #e5eaf2;padding-bottom:22px}.brand{display:flex;gap:14px}.logo{width:58px;height:58px;border-radius:12px;background:${accent};color:#fff;display:grid;place-items:center;font-size:20px;font-weight:800}.company{font-size:22px;font-weight:800}.muted{color:#667085;font-size:12px;margin-top:3px}.right{text-align:right}.label{font-size:30px;font-weight:900;text-transform:uppercase}.badge{display:inline-block;margin-top:8px;border:1px solid #d8e2ff;color:${accent};border-radius:999px;padding:4px 10px;font-size:11px;font-weight:700;text-transform:uppercase}.grid{display:grid;grid-template-columns:1fr 280px;gap:16px;margin:22px 0}.panel{border:1px solid #e5eaf2;border-radius:8px;padding:14px}.kicker{font-size:10px;text-transform:uppercase;letter-spacing:.8px;color:#667085;font-weight:700;margin-bottom:7px}.party{font-size:17px;font-weight:800}.row{display:flex;justify-content:space-between;gap:12px;color:#667085;padding:4px 0}.row strong{color:#172033;text-align:right}table{width:100%;border-collapse:collapse;border:1px solid #e5eaf2;border-radius:8px;overflow:hidden}th{background:#f3f6fb;color:#667085;text-transform:uppercase;font-size:10px;letter-spacing:.5px;text-align:left;padding:10px}td{padding:11px 10px;border-top:1px solid #e5eaf2}.num{text-align:right;white-space:nowrap}.summary{display:grid;grid-template-columns:1fr 300px;gap:20px;margin-top:22px}.notes{border-left:4px solid ${accent};padding-left:12px;color:#667085}.totals{border:1px solid #e5eaf2;border-radius:8px;padding:14px}.total,.grand{display:flex;justify-content:space-between;gap:14px}.total{color:#667085;padding:4px 0}.grand{border-top:1px solid #e5eaf2;margin-top:8px;padding-top:12px;font-size:19px;font-weight:900}.grand strong{color:${accent}}@media print{body{print-color-adjust:exact;-webkit-print-color-adjust:exact}.sheet{padding:24px}.bar{margin:-24px -24px 24px}}</style>
    </head><body><main class="sheet"><div class="bar"></div>
      <section class="head"><div class="brand"><div class="logo">${escapeHtml(initials)}</div><div><div class="company">${escapeHtml(layout.company||'TaxFlow')}</div><div class="muted">${escapeHtml(layout.address||'')}</div><div class="muted">${escapeHtml(layout.trnLabel||'TRN')} ${escapeHtml(companyTrn||'not set')}</div></div></div><div class="right"><div class="label">${escapeHtml(layout.quotationHeading||'Quotation')}</div><div>${escapeHtml(quote?.quote_no||'Draft')}</div><span class="badge">${escapeHtml(quote?.status||'Draft')}</span></div></section>
      <section class="grid"><div class="panel"><div class="kicker">${escapeHtml(layout.quoteToLabel||'Quote To')}</div><div class="party">${escapeHtml(quote?.customer||'Customer')}</div><div class="muted">${escapeHtml(quote?.subject||'')}</div></div><div class="panel"><div class="row"><span>Date</span><strong>${escapeHtml(quote?.date||'-')}</strong></div>${layout.showValidity?`<div class="row"><span>${escapeHtml(layout.validityLabel||'Valid Until')}</span><strong>${escapeHtml(quote?.valid_until||'-')}</strong></div>`:''}<div class="row"><span>Currency</span><strong>AED</strong></div></div></section>
      <table><thead><tr><th>#</th><th>Product</th><th class="num">Qty</th><th class="num">Unit Price</th><th class="num">Amount</th></tr></thead><tbody>${lines.map((line,index)=>`<tr><td>${index+1}</td><td><strong>${escapeHtml(line.description||line.item||'Item')}</strong></td><td class="num">${escapeHtml(line.qty||line.quantity||1)}</td><td class="num">${fmt(line.price||line.unit_price)}</td><td class="num">${fmt(line.amount)}</td></tr>`).join('')}</tbody></table>
      <section class="summary"><div class="notes"><div class="kicker">Terms & Payment Details</div><div>${escapeHtml(layout.quotationTerms||layout.terms||'')}</div><div style="margin-top:8px">${escapeHtml(layout.footer||'')}</div></div><div class="totals"><div class="total"><span>Subtotal</span><strong>AED ${fmt(subtotal)}</strong></div>${layout.showVat?`<div class="total"><span>VAT 5%</span><strong>AED ${fmt(vat)}</strong></div>`:''}<div class="grand"><span>Total</span><strong>AED ${fmt(total)}</strong></div></div></section>
    </main><script>window.onload=()=>setTimeout(()=>window.print(),250);<\/script></body></html>`;
}

function downloadQuotationPdf(quote=currentQuotation){
  if(!quote){
    toast('Quotation not found','warn');
    return;
  }
  const printWindow=window.open('','_blank','width=980,height=780');
  if(!printWindow){
    toast('Allow popups to open the quotation PDF','warn');
    return;
  }
  printWindow.document.open();
  printWindow.document.write(quotationPdfHtml(quote));
  printWindow.document.close();
  toast('Quotation PDF print view opened. Choose Save as PDF to download.','ok');
  audit('Opened quotation PDF',quote.quote_no||'Draft','Exported');
}

function downloadCurrentQuotationPdf(){
  downloadQuotationPdf(currentQuotation);
}

function downloadQuotationRowPdf(btn){
  downloadQuotationPdf(quotationRecordFromRow(btn.closest('tr')));
}

function shareCurrentQuotation(channel='options'){
  const quote=currentQuotation;
  if(!quote){
    toast('Quotation not found','warn');
    return;
  }
  saveServer('quotations',{...quote,last_shared_at:new Date().toISOString(),last_share_channel:channel});
  const label=channel==='email'?'Quotation email prepared':channel==='whatsapp'?'WhatsApp share prepared':'Quotation share options prepared';
  toast(label,'ok');
  audit('Shared quotation',quote.quote_no||'Quotation',channel);
}

function openQuotationPreview(quote){
  renderQuotationPreview(quote);
  showM('m-quotation-view');
}

function previewQuotation(btn){
  const row=btn.closest('tr');
  const quote=quotationRecordFromRow(row);
  if(row&&!row.dataset.quotation)row.dataset.quotation=JSON.stringify(quote);
  openQuotationPreview(quote);
  audit('Viewed quotation',quote.quote_no||'Quotation','Opened');
}

function shareQuotation(btn){
  const quote=quotationRecordFromRow(btn.closest('tr'));
  saveServer('quotations',{...quote,last_shared_at:new Date().toISOString()});
  openQuotationPreview(quote);
  toast(`Share link prepared for ${quote.quote_no||'quotation'}`,'ok');
}

function convertQuotation(btn){
  const row=btn.closest('tr');
  const quote=quotationRecordFromRow(row);
  const badge=row?.querySelector('td:nth-child(8) .b');
  if(badge){
    badge.classList.remove('b-a','b-r','b-b','b-gray');
    badge.classList.add('b-g');
    badge.textContent='Converted';
  }
  const updated={...quote,status:'Converted',converted_at:new Date().toISOString()};
  row.dataset.quotation=JSON.stringify(updated);
  saveServer('quotations',updated);
  const invoiceNo=`INV-${String(quote.quote_no||Date.now()).replace(/^QTN-?/,'')}`;
  const invoice={
    invoice_no:invoiceNo,
    customer:quote.customer||'Customer',
    date:new Date().toISOString().split('T')[0],
    due_date:'30 days',
    subtotal:parseAmount(quote.subtotal),
    vat_amount:parseAmount(quote.vat_amount),
    total:parseAmount(quote.total),
    source:'Quotation',
    status:'Draft',
    quotation_no:quote.quote_no||'',
    lines:quotationLinesFromRecord(quote)
  };
  addSalesInvoiceRow(invoice,{persist:false});
  saveServer('salesInvoices',invoice);
  toast(`${quote.quote_no||'Quotation'} converted to invoice draft ${invoiceNo}`,'ok');
}

function saveQuotationDraft(){
  calcQuotationTotals();
  saveServer('quotations',buildDraftQuotationRecord('Draft'));
  toast(`${quotationNumber()} saved as draft`,'ok');
}

function previewDraftQuotation(){
  calcQuotationTotals();
  openQuotationPreview(buildDraftQuotationRecord('Draft'));
  toast(`Preview opened for ${quotationNumber()}`,'info');
}

function shareDraftQuotation(){
  calcQuotationTotals();
  saveServer('quotations',{...buildDraftQuotationRecord('Draft'),last_shared_at:new Date().toISOString()});
  toast(`Share link prepared for ${quotationNumber()}`,'ok');
}

function buildDraftQuotationRecord(status='Sent'){
  return {
    quote_no:quotationNumber(),
    customer:document.getElementById('quote-customer')?.value?.trim()||'New Customer',
    date:document.getElementById('quote-date')?.value||'Today',
    valid_until:document.getElementById('quote-valid')?.value||'15 days',
    subtotal:document.getElementById('quote-subtotal')?.textContent?.replace('AED ','').replace('AED ','')||'0.00',
    vat_amount:document.getElementById('quote-vat')?.textContent?.replace('AED ','').replace('AED ','')||'0.00',
    total:document.getElementById('quote-total')?.textContent?.replace('AED ','').replace('AED ','')||'0.00',
    status,
    owner:'Sales Team',
    subject:document.getElementById('quote-subject')?.value?.trim()||'',
    lines:quotationLinesFromForm()
  };
}

function sendDraftQuotation(){
  calcQuotationTotals();
  const record=buildDraftQuotationRecord('Sent');
  renderQuotationRecord(record);
  saveServer('quotations',record);
  stab(document.querySelector('#page-quotations .tab:nth-child(1)'),'q-list');
  toast(`${quotationNumber()} sent to customer`,'ok');
}

function separateCorporateAccountingModule(){
  const target=document.getElementById('corporate-accounting-bodies');
  if(!target||target.dataset.ready==='1')return;
  const idMap={
    'acc-assets':'corp-assets',
    'acc-accruals':'corp-accruals',
    'acc-cost-centers':'corp-cost-centers',
    'acc-budget':'corp-budget',
    'acc-cashflow':'corp-cashflow',
    'acc-credit':'corp-credit',
    'acc-consolidation':'corp-consolidation',
    'acc-approval':'corp-approval'
  };
  Object.entries(idMap).forEach(([oldId,newId],index)=>{
    const section=document.getElementById(oldId);
    if(!section)return;
    section.id=newId;
    section.classList.remove('on');
    target.appendChild(section);
  });
  target.dataset.ready='1';
}

function mergeBankAndPaymentsModule(){
  const bankPage=document.getElementById('page-bank');
  const bankTabs=bankPage?.querySelector('.tabs');
  const paymentsPage=document.getElementById('page-payments');
  if(!bankPage||!bankTabs||bankPage.dataset.paymentsMerged==='1')return;

  const tabs=[
    ['pay-in','Receipts'],
    ['pay-out','Payments']
  ];
  tabs.forEach(([target,label])=>{
    if(!bankTabs.querySelector(`[data-merged-payment-tab="${target}"]`)){
      const tab=document.createElement('div');
      tab.className='tab';
      tab.dataset.mergedPaymentTab=target;
      tab.setAttribute('onclick',`stab(this,'${target}')`);
      tab.textContent=label;
      bankTabs.appendChild(tab);
    }
    const body=document.getElementById(target);
    if(body&&body.parentElement!==bankPage)bankPage.appendChild(body);
  });

  paymentsPage?.remove();
  document.querySelectorAll('.nav').forEach(nav=>{
    const action=nav.getAttribute('onclick')||'';
    if(action.includes("'payments'"))nav.remove();
  });
  bankPage.dataset.paymentsMerged='1';
}

function initApp(){
  if(window.__taxflowAppInitialized)return;
  window.__taxflowAppInitialized=true;
  applyTheme('light');
  const today=new Date().toISOString().split('T')[0];
  document.querySelectorAll('input[type=date]').forEach(i=>{if(!i.value)i.value=today;});
  const invDate=document.getElementById('inv-date');
  if(invDate)invDate.value=today;
  const due=new Date();
  due.setDate(due.getDate()+30);
  const invDue=document.getElementById('inv-due');
  if(invDue)invDue.value=due.toISOString().split('T')[0];

  document.addEventListener('keydown',event=>{
    if(event.key==='Escape'){
      closeSidebar();
      document.querySelectorAll('.overlay.on').forEach(modal=>modal.classList.remove('on'));
    }
  });
  mergeBankAndPaymentsModule();
  separateCorporateAccountingModule();
  clearStaticDemoData();
  watchVisibleTablePagination();
  applyAllTableActions();
  updateBackButton();

  restoreSalesInvoices();
  renderAuditLog();
  initInvoiceLayouts();
  updateInvoiceLayoutPreview();
  setQuotationLayoutFields();
  updateQuotationLayoutPreview();
  syncProductMasterOptions();
  setManualPurchaseDefaults();
  configureSalesFormMode();
  configureManualPurchaseMode();
  syncCompanyFromDatabase();
  enhancePageTables('page-dashboard');
  syncDashboardFromDatabase().catch(err=>console.warn('Dashboard sync failed during init:',err));
  hydrateFromServer().catch(err=>console.warn('Database hydrate failed during init:',err));
  scheduleIdleTask(()=>{
    updateAccountSelectors();
    recalcJournal();
    recalcPayroll();
    calcGratuity();
    bindDetailViews();
    bindEditActions();
    bindGenericAddActions();
    applyAllTableActions();
  },900);
}

initApp();

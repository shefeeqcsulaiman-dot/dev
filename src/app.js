const META={dashboard:{t:'Dashboard',s:'Good morning, Sara - Q2 2024',a:'+ New Invoice',ao:()=>go('sales')},company:{t:'Company Registration',s:'UAE Trade License & FTA Details',a:'Save All',ao:()=>toast('All changes saved','ok')},sales:{t:'Sales & Invoices',s:'Products - Invoices - Customers',a:'+ New Invoice',ao:()=>{go('sales');setTimeout(()=>stab(document.querySelectorAll('#page-sales .tab')[2],'s-create'),50)}},purchase:{t:'Purchases',s:'Upload - AI Extraction - Validation',a:'Upload Files',ao:()=>document.getElementById('pur-file').click()},bank:{t:'Bank Accounts',s:'Accounts - Transactions - Reconciliation',a:'+ Add Account',ao:()=>showM('m-bank')},inventory:{t:'Inventory',s:'Stock - Items - Movements',a:'+ Add Item',ao:()=>showM('m-inv-item')},expense:{t:'Expenses',s:'List - Create - Approvals',a:'+ New Expense',ao:()=>{go('expense');setTimeout(()=>stab(document.querySelectorAll('#page-expense .tab')[1],'exp-create'),50)}},accounting:{t:'Accounting',s:'Chart - Journal - Ledger',a:'+ New Entry',ao:()=>showM('m-acc')},reports:{t:'Reports',s:'VAT - P&L - Trial Balance',a:'Export PDF',ao:()=>toast('Exporting report...','info')},settings:{t:'Settings',s:'General - Users - Tax',a:'Save All',ao:()=>toast('Settings saved','ok')},mobile:{t:'Mobile App',s:'Preview - Features',a:'Download App',ao:()=>toast('Download link sent to email','ok')},staff:{t:'Staff Management',s:'Attendance - Leave - Corrections - Biometric',a:'+ Add Employee',ao:()=>showM('m-emp')},expert:{t:'Expert Review',s:'Find CA experts - Submit for review',a:'+ New Request',ao:()=>showM('m-newreview')},design:{t:'System Design',s:'Functional Spec - Fields - Validations - API',a:'Export Spec',ao:()=>toast('Exporting FRD to PDF...','info')}};
META.settings={t:'Settings',s:'Company - Users - Tax - Security - Integrations',a:'Save All',ao:()=>toast('Settings saved','ok')};
META.payroll={t:'Payroll',s:'Salary run - WPS/SIF - Payslips - Posting',a:'Run Payroll',ao:()=>{go('payroll');setTimeout(()=>runPayroll(),50)}};
META.sales={t:'Sales & Invoices',s:'Products - Upload - Invoices - Customers',a:'+ New Invoice',ao:()=>{go('sales');setTimeout(()=>stab(document.querySelectorAll('#page-sales .tab')[3],'s-create'),50)}};
META.ai={t:'AI Assistant',s:'Ask questions about TaxFlow modules and workflows',a:'Ask AI',ao:()=>askSystemAI()};
META.bills={t:'Bills & Vendors',s:'Vendor bills - Purchase orders - Supplier payments',a:'+ New Bill',ao:()=>showM('m-bill')};
META.payments={t:'Payments',s:'Receipts - Payouts - Gateway settlements',a:'+ Record Payment',ao:()=>showM('m-payment')};
META.documents={t:'Documents',s:'Receipts - PDFs - Audit files - Attachments',a:'Upload Document',ao:()=>toast('Choose files to upload...','info')};
META.notifications={t:'Notifications',s:'Email - WhatsApp - SMS - Push - In-app alerts',a:'+ New Rule',ao:()=>toast('Notification rule builder opened','info')};
META.rota={t:'Rota Planning',s:'Shift setup - Weekly rota - Coverage - Swap requests',a:'Publish Rota',ao:()=>publishRota()};
META.accounting={t:'Accounting',s:'Chart - Journal - Ledger - Tax - Closing',a:'+ Journal Entry',ao:()=>{go('accounting');setTimeout(()=>stab(document.querySelectorAll('#page-accounting .tab')[1],'acc-journal'),50)}};

function scheduleIdleTask(fn,timeout=800){
  if('requestIdleCallback' in window){
    window.requestIdleCallback(fn,{timeout});
  }else{
    setTimeout(fn,0);
  }
}

function go(page){
  if(page==='company'){
    go('settings');
    setTimeout(()=>stab(document.querySelectorAll('#page-settings .tab')[0],'set-company'),50);
    return;
  }
  if(page==='design'){
    toast('System Design is hidden','info');
    return;
  }
  document.querySelectorAll('.page').forEach(p=>p.classList.remove('on'));
  document.querySelectorAll('.nav').forEach(n=>n.classList.remove('on'));
  document.getElementById('page-'+page).classList.add('on');
  document.querySelectorAll('.nav').forEach(n=>{if((n.getAttribute('onclick')||'').includes("'"+page+"'"))n.classList.add('on');});
  const m=META[page];
  document.getElementById('ptitle').textContent=m.t;
  document.getElementById('psub').textContent=m.s;
  document.getElementById('topaction').textContent=m.a;
  document.getElementById('topaction').onclick=m.ao;
  closeSidebar();
}

function stab(el,target){
  if(!el)return;
  const tb=el.closest('.tabs');
  if(tb)tb.querySelectorAll('.tab').forEach(t=>t.classList.remove('on'));
  el.classList.add('on');
  const pg=el.closest('.page');
  if(pg)pg.querySelectorAll('.tab-body').forEach(b=>b.classList.remove('on'));
  const t=document.getElementById(target);
  if(t)t.classList.add('on');
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
  modal.classList.add('on');
  setTimeout(()=>modal.querySelector('input,select,textarea,button')?.focus(),30);
}
function closeM(id){
  document.getElementById(id)?.classList.remove('on');
  if(id==='m-customer')customerReturnToInvoice=false;
  if(id==='m-product'){
    productReturnToInvoice=false;
    productTargetLine=null;
  }
}
function closeOvBg(e,id){if(e.target.id===id)closeM(id);}
function saveM(id,msg){closeM(id);toast(msg,'ok');audit(msg.replace(/[?.]/g,'').trim(),id,'Saved');}

let currentStockMapRow=null;

function openStockMap(btn){
  currentStockMapRow=btn.closest('tr');
  const cells=currentStockMapRow?.querySelectorAll('td')||[];
  const product=document.getElementById('stock-map-product');
  const generated=document.getElementById('stock-map-generated');
  if(product)product.value=cells[0]?.textContent.trim()||'';
  if(generated)generated.value=cells[1]?.textContent.trim()||generateStockMapName(product?.value||'');
  document.getElementById('stock-map-empty')?.classList.add('hidden');
  document.getElementById('stock-map-panel')?.classList.remove('hidden');
  updateStockMapPricing();
  setTimeout(()=>product?.focus(),30);
}

function saveStockMap(){
  const product=(document.getElementById('stock-map-product')?.value||'').trim();
  const generated=(document.getElementById('stock-map-generated')?.value||generateStockMapName(product)).trim();
  if(!product||!generated){
    toast('Enter Product Name and Generated Name','warn');
    return;
  }
  if(currentStockMapRow){
    const cells=currentStockMapRow.querySelectorAll('td');
    cells[0].textContent=product;
    cells[1].textContent=generated;
    cells[4].innerHTML='<span class="b b-g">Mapped</span>';
  }
  toast('Stock product mapped','ok');
  audit('Mapped stock product',product,'Saved');
  filterStockMapList();
}

function clearStockMapPanel(){
  currentStockMapRow=null;
  const product=document.getElementById('stock-map-product');
  const generated=document.getElementById('stock-map-generated');
  if(product)product.value='';
  if(generated)generated.value='';
  document.getElementById('stock-map-panel')?.classList.add('hidden');
  document.getElementById('stock-map-empty')?.classList.remove('hidden');
}

function filterStockMapList(){
  const query=(document.getElementById('stock-map-search')?.value||'').trim().toLowerCase();
  document.querySelectorAll('#stock-map-tbody tr').forEach(row=>{
    const text=row.textContent.toLowerCase();
    row.style.display=!query||text.includes(query)?'':'none';
  });
}

function sortStockMapList(){
  const tbody=document.getElementById('stock-map-tbody');
  if(!tbody)return;
  const mode=document.getElementById('stock-map-sort')?.value||'product-asc';
  const rows=[...tbody.querySelectorAll('tr')];
  const col=mode.startsWith('generated')?1:mode==='status'?4:0;
  rows.sort((a,b)=>{
    const av=(a.children[col]?.textContent||'').trim().toLowerCase();
    const bv=(b.children[col]?.textContent||'').trim().toLowerCase();
    const result=av.localeCompare(bv);
    return mode==='product-desc'?-result:result;
  });
  rows.forEach(row=>tbody.appendChild(row));
  filterStockMapList();
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

function generateStockMapField(){
  const product=document.getElementById('stock-map-product');
  const generated=document.getElementById('stock-map-generated');
  if(!product?.value.trim()){
    toast('Enter Product Name first','warn');
    return;
  }
  if(generated)generated.value=generateStockMapName(product.value);
  toast('Generated name updated','info');
}

function updateStockMapPricing(){
  const costEl=document.getElementById('stock-map-cost');
  const markupEl=document.getElementById('stock-map-markup');
  const fixedEl=document.getElementById('stock-map-fixed');
  const priceEl=document.getElementById('stock-map-price');
  const taxEl=document.getElementById('stock-map-tax-rate');
  const incVatEl=document.getElementById('stock-map-inc-vat');
  if(!costEl||!markupEl||!fixedEl||!priceEl||!taxEl||!incVatEl)return;

  const cost=parseFloat(String(costEl.value).replace(/,/g,''))||0;
  const markup=parseFloat(String(markupEl.value).replace(/,/g,''))||0;
  const taxRate=parseFloat(taxEl.value)||0;
  let price=parseFloat(String(priceEl.value).replace(/,/g,''))||0;

  if(!fixedEl.checked){
    price=cost*(1+(markup/100));
    priceEl.value=price.toFixed(2);
  }

  incVatEl.value=(price*(1+(taxRate/100))).toFixed(2);
}

function logout(){
  go('dashboard');
  toast('Logged out. Session cleared locally.','info');
  audit('Logged out','Session','Logged');
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

const APP_CONFIG={
  apiEndpoint:'api.php',
  extractionEndpoint:'api.php?action=documents.extract',
  salesExtractionEndpoint:'api.php?action=invoices.import',
  demoExtractionFallback:true
};

const STORAGE_KEYS={
  salesInvoices:'taxflow.salesInvoices.v1',
  invoiceLayout:'taxflow.invoiceLayout.v1',
  audit:'taxflow.audit.v1'
};

function loadLocal(key,fallback){
  try{
    const raw=localStorage.getItem(key);
    return raw?JSON.parse(raw):fallback;
  }catch{
    return fallback;
  }
}

function saveLocal(key,value){
  try{
    localStorage.setItem(key,JSON.stringify(value));
  }catch(err){
    console.warn('Local storage unavailable:',err);
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
  const response=await fetch(`${APP_CONFIG.apiEndpoint}?action=${encodeURIComponent(action)}`,{
    method:options.method||'POST',
    headers:{'Content-Type':'application/json'},
    body:options.method==='GET'?undefined:JSON.stringify(payload||{})
  });
  if(!response.ok)throw new Error('PHP API returned '+response.status);
  const data=await response.json();
  if(data&&data.ok===false)throw new Error(data.error||'PHP API request failed');
  return data;
}

function saveServer(collection,record){
  apiRequest('save',{collection,record}).catch(err=>console.warn('PHP save failed:',err));
}

function saveInvoiceLayoutServer(layout){
  apiRequest('invoice-layout',layout).catch(err=>console.warn('PHP layout save failed:',err));
}

function audit(action,record='System',result='Logged'){
  const entry={time:new Date().toLocaleString('en-AE',{dateStyle:'short',timeStyle:'short'}),user:'Sara',action,record,result};
  const entries=[entry,...loadLocal(STORAGE_KEYS.audit,[])].slice(0,50);
  saveLocal(STORAGE_KEYS.audit,entries);
  saveServer('audit',entry);
  renderAuditLog(entries);
}

function renderAuditLog(entries=loadLocal(STORAGE_KEYS.audit,[])){
  const tbody=document.getElementById('audit-tbody');
  if(!tbody||entries.length===0)return;
  tbody.querySelectorAll('[data-audit-dynamic]').forEach(row=>row.remove());
  const template=document.createElement('tbody');
  template.innerHTML=entries.map(entry=>`<tr data-audit-dynamic><td class="mono">${escapeHtml(entry.time)}</td><td>${escapeHtml(entry.user)}</td><td>${escapeHtml(entry.action)}</td><td>${escapeHtml(entry.record)}</td><td><span class="b b-g">${escapeHtml(entry.result)}</span></td></tr>`).join('');
  [...template.children].reverse().forEach(row=>tbody.prepend(row));
}

function persistSalesInvoice(inv){
  const invoices=loadLocal(STORAGE_KEYS.salesInvoices,[]);
  if(invoices.some(item=>item.invoice_no===inv.invoice_no))return;
  saveLocal(STORAGE_KEYS.salesInvoices,[inv,...invoices].slice(0,200));
  saveServer('salesInvoices',inv);
}

function restoreSalesInvoices(){
  loadLocal(STORAGE_KEYS.salesInvoices,[]).reverse().forEach(inv=>addSalesInvoiceRow(inv,{persist:false}));
}

function hasFirstCellValue(tbody,value){
  return !!tbody&&[...tbody.querySelectorAll('tr td:first-child')].some(td=>td.textContent.trim().toLowerCase()===String(value||'').trim().toLowerCase());
}

function renderCustomerRecord(customer){
  const tbody=document.getElementById('customer-tbody');
  if(!tbody||!customer?.name||hasFirstCellValue(tbody,customer.name))return;
  const row=document.createElement('tr');
  row.dataset.serverRecord='customers';
  row.innerHTML=`<td>${escapeHtml(customer.name)}</td><td class="mono">${escapeHtml(customer.trn||'Not registered')}</td><td>${escapeHtml(customer.emirate||'Dubai')}</td><td>${escapeHtml(customer.email||customer.phone||'-')}</td><td class="mono" style="color:var(--accent)">AED 0</td><td><button class="btn btn-g btn-sm">View</button></td>`;
  tbody.prepend(row);
}

function renderProductRecord(product){
  const tbody=document.getElementById('prod-tbody');
  if(!tbody||!product?.name||hasFirstCellValue(tbody,product.code))return;
  const vatText=String(product.vat||'').includes('0')&&!String(product.vat||'').includes('5')?'0% Zero':String(product.vat||'').includes('Exempt')?'Exempt':'5%';
  const vatClass=vatText==='5%'?'b-b':'b-t';
  const row=document.createElement('tr');
  row.dataset.serverRecord='products';
  row.innerHTML=`<td class="mono">${escapeHtml(product.code||'PRD')}</td><td>${escapeHtml(product.name)}</td><td><span class="b b-gray">${escapeHtml(product.category||'Materials')}</span></td><td>${escapeHtml(product.unit||'Each')}</td><td class="mono">${Number(product.price||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2})}</td><td><span class="b ${vatClass}">${escapeHtml(vatText)}</span></td><td><span class="b b-g">Active</span></td><td><button class="btn btn-g btn-sm" onclick="editProd(this.closest('tr').querySelector('td').textContent)">Edit</button></td>`;
  tbody.prepend(row);
}

function renderBillRecord(bill){
  const tbody=document.getElementById('bill-tbody');
  if(!tbody||!bill?.bill_no||hasFirstCellValue(tbody,bill.bill_no))return;
  const row=document.createElement('tr');
  row.dataset.serverRecord='bills';
  row.innerHTML=`<td class="mono">${escapeHtml(bill.bill_no)}</td><td>${escapeHtml(bill.vendor)}</td><td>${escapeHtml(bill.date)}</td><td>${escapeHtml(bill.due)}</td><td class="mono">${Number(bill.subtotal||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${Number(bill.vat||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${Number(bill.total||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td><span class="b b-a">${escapeHtml(bill.status||'Awaiting Payment')}</span></td><td><button class="btn btn-g btn-sm" onclick="toast('Bill preview opened','info')">View</button></td>`;
  tbody.prepend(row);
}

function renderVendorRecord(vendor){
  const tbody=document.getElementById('vendor-tbody');
  if(!tbody||!vendor?.name||hasFirstCellValue(tbody,vendor.name))return;
  const row=document.createElement('tr');
  row.dataset.serverRecord='vendors';
  row.innerHTML=`<td>${escapeHtml(vendor.name)}</td><td class="mono">${escapeHtml(vendor.trn||'Not registered')}</td><td>${escapeHtml(vendor.category||'Services')}</td><td>${escapeHtml(vendor.email||'-')}</td><td class="mono">0.00</td><td><span class="b b-g">Active</span></td>`;
  tbody.prepend(row);
}

function renderPaymentRecord(payment){
  const tbody=document.getElementById(payment?.type==='Supplier Payment'?'payment-out-tbody':'payment-in-tbody');
  if(!tbody||!payment?.ref||hasFirstCellValue(tbody,payment.ref))return;
  const label=payment.type==='Supplier Payment'?'Vendor':'Manual';
  const row=document.createElement('tr');
  row.dataset.serverRecord='payments';
  row.innerHTML=`<td class="mono">${escapeHtml(payment.ref)}</td><td>${escapeHtml(payment.contact)}</td><td class="mono">${escapeHtml(label)}</td><td>${escapeHtml(payment.method||'Bank Transfer')}</td><td>${escapeHtml(payment.date)}</td><td class="mono">${Number(payment.amount||0).toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td><span class="b b-g">Posted</span></td>`;
  tbody.prepend(row);
}

function renderAccountRecord(account){
  const tbody=document.getElementById('account-tbody');
  if(!tbody||!account?.code||hasFirstCellValue(tbody,account.code))return;
  const typeClass={Asset:'b-t',Liability:'b-r',Revenue:'b-g',Expense:'b-p',Equity:'b-b'}[account.type]||'b-gray';
  const row=document.createElement('tr');
  row.dataset.serverRecord='accounts';
  row.innerHTML=`<td class="mono">${escapeHtml(account.code)}</td><td>${escapeHtml(account.name)}</td><td><span class="b ${typeClass}">${escapeHtml(account.type||'Asset')}</span></td><td>${escapeHtml(account.category||'Current')}</td><td class="mono">0.00</td><td><span class="b b-g">Active</span></td><td><button class="btn btn-g btn-sm" onclick="viewAccountLedger(this)">View</button></td>`;
  tbody.appendChild(row);
}

function hydrateFromServer(){
  apiRequest('bootstrap',{}, {method:'GET'}).then(({data})=>{
    if(!data)return;
    (data.products||[]).reverse().forEach(renderProductRecord);
    (data.customers||[]).reverse().forEach(renderCustomerRecord);
    (data.salesInvoices||[]).reverse().forEach(inv=>addSalesInvoiceRow(inv,{persist:false}));
    (data.accounts||[]).reverse().forEach(renderAccountRecord);
    (data.ledger||[]).reverse().forEach(line=>postLedgerLine(line,{persist:false}));
    (data.bills||[]).reverse().forEach(renderBillRecord);
    (data.vendors||[]).reverse().forEach(renderVendorRecord);
    (data.payments||[]).reverse().forEach(renderPaymentRecord);
    if(data.invoiceLayout){
      saveLocal(STORAGE_KEYS.invoiceLayout,data.invoiceLayout);
      setInvoiceLayoutFields(data.invoiceLayout);
      updateInvoiceLayoutPreview();
    }
    if(Array.isArray(data.audit)&&data.audit.length){
      renderAuditLog(data.audit);
    }
    updateAccountSelectors();
    filterLedger();
  }).catch(err=>console.warn('PHP bootstrap unavailable:',err));
}

function escapeHtml(value){
  return String(value??'').replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
}

function buildDemoExtraction(entry){
  const cleanBase=(entry?.name||'PUR-DEMO').replace(/\.[^.]+$/,'').replace(/[^a-z0-9]+/gi,'-').slice(0,18).toUpperCase()||'PUR-DEMO';
  return [
    {invoice_no:cleanBase+'-001',date:'15 Jun 2024',supplier:'Al Hamad Steel',supplier_trn:'100234567800003',subtotal:12400,vat_amount:620,total:13020,vat_rate_pct:5,confidence:94,status:'Valid',issues:''},
    {invoice_no:cleanBase+'-002',date:'16 Jun 2024',supplier:'Gulf Freight Services',supplier_trn:'MISSING',subtotal:3600,vat_amount:180,total:3780,vat_rate_pct:5,confidence:76,status:'Error',issues:'Supplier TRN is missing or invalid'},
    {invoice_no:cleanBase+'-003',date:'18 Jun 2024',supplier:'UAE Paints Co.',supplier_trn:'100987654300003',subtotal:5800,vat_amount:410,total:6210,vat_rate_pct:5,confidence:82,status:'Review',issues:'VAT amount does not match 5% calculation'}
  ];
}

async function requestInvoiceExtraction(entry){
  const payload={
    file:{name:entry.name,size:entry.size,type:entry.type,base64:entry.base64},
    category:entry.category,
    period:entry.period
  };

  try{
    const response=await fetch(APP_CONFIG.extractionEndpoint,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)
    });
    if(!response.ok)throw new Error('Extraction service returned '+response.status);
    const data=await response.json();
    const invoices=Array.isArray(data)?data:data.invoices;
    if(!Array.isArray(invoices))throw new Error('Extraction service returned an invalid payload');
    return invoices;
  }catch(err){
    if(!APP_CONFIG.demoExtractionFallback)throw err;
    console.warn('Using demo extraction fallback:',err);
    toast('Backend extraction unavailable; using demo data','warn');
    return buildDemoExtraction(entry);
  }
}

const salesUploadedFiles = [];
const salesExtractedInvoices = [];

function buildDemoSalesExtraction(entry){
  const cleanBase=(entry?.name||'INV-UPLOAD').replace(/\.[^.]+$/,'').replace(/[^a-z0-9]+/gi,'-').slice(0,16).toUpperCase()||'INV-UPLOAD';
  return [
    {invoice_no:cleanBase+'-001',customer:'Dubai Steel Co.',customer_trn:'100348712600001',date:'22 Jun 2024',due_date:'22 Jul 2024',subtotal:42000,vat_amount:2100,total:44100,confidence:96,status:'Ready',issues:''},
    {invoice_no:cleanBase+'-002',customer:'Gulf Logistics Ltd',customer_trn:'100874321500002',date:'23 Jun 2024',due_date:'23 Jul 2024',subtotal:18500,vat_amount:925,total:19425,confidence:91,status:'Ready',issues:''}
  ];
}

async function requestSalesInvoiceExtraction(entry){
  const payload={
    documentType:'sales_invoice',
    file:{name:entry.name,size:entry.size,type:entry.type,base64:entry.base64},
    importType:entry.importType,
    period:entry.period
  };

  try{
    const response=await fetch(APP_CONFIG.salesExtractionEndpoint,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload)
    });
    if(!response.ok)throw new Error('Invoice import service returned '+response.status);
    const data=await response.json();
    const invoices=Array.isArray(data)?data:data.invoices;
    if(!Array.isArray(invoices))throw new Error('Invoice import service returned an invalid payload');
    return invoices;
  }catch(err){
    if(!APP_CONFIG.demoExtractionFallback)throw err;
    console.warn('Using sales invoice demo extraction fallback:',err);
    toast('Invoice import backend unavailable; using demo extraction','warn');
    return buildDemoSalesExtraction(entry);
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
        const autoEl=document.getElementById('sales-auto');
        if(!autoEl||autoEl.value==='yes')setTimeout(()=>extractSalesInvoiceFile(entry),500);
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
  try{
    const invoices=await requestSalesInvoiceExtraction(entry);
    entry.status='Extracted';
    entry.invoices=invoices;
    salesExtractedInvoices.push(...invoices.map(inv=>({...inv,sourceFile:entry.name,stored:false})));
    renderSalesFileList();
    appendSalesExtractedRows(invoices);
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
    if([...tbody.querySelectorAll('td.mono')].some(td=>td.textContent===inv.invoice_no))return;
    const confCls=inv.confidence>=90?'b-g':inv.confidence>=70?'b-a':'b-r';
    const stCls=inv.status==='Ready'||inv.status==='Valid'?'b-g':inv.status==='Review'?'b-a':'b-r';
    const row=document.createElement('tr');
    row.dataset.salesInv=JSON.stringify(inv);
    row.innerHTML=`<td class="mono">${escapeHtml(inv.invoice_no)}</td><td>${escapeHtml(inv.customer)}</td><td class="mono">${escapeHtml(inv.customer_trn||'')}</td><td>${escapeHtml(inv.date)}</td><td class="mono">${fmt(inv.subtotal)}</td><td class="mono">${fmt(inv.vat_amount)}</td><td class="mono">${fmt(inv.total)}</td><td><span class="b ${confCls}">${Number(inv.confidence||0)}%</span></td><td><span class="b ${stCls}">${escapeHtml(inv.status||'Ready')}</span></td>`;
    tbody.prepend(row);
  });
}

function runSalesImport(){
  const ready=salesUploadedFiles.filter(f=>f.status==='Ready'||f.status==='Queued');
  if(ready.length===0){toast('No pending sales invoice files to read','warn');return;}
  ready.forEach((f,i)=>setTimeout(()=>extractSalesInvoiceFile(f),i*450));
}

function storeExtractedSalesInvoices(){
  const rows=[...document.querySelectorAll('#sales-ext-tbody tr[data-sales-inv]')];
  if(rows.length===0){toast('No extracted sales invoices to store','warn');return;}
  let stored=0;
  rows.forEach(row=>{
    const inv=JSON.parse(row.dataset.salesInv||'{}');
    if(addSalesInvoiceRow(inv)){
      stored++;
      row.querySelector('td:last-child').innerHTML='<span class="b b-g">Stored</span>';
    }
  });
  if(stored>0){
    const tab=document.querySelector('#page-sales .tab:nth-child(3)');
    if(tab)stab(tab,'s-invoices');
    audit('Stored imported sales invoices',stored+' invoice(s)','Saved');
  }
  toast(stored+' invoice(s) stored in system ?','ok');
}

function addSalesInvoiceRow(inv,options={persist:true}){
  const tbody=document.getElementById('sales-invoice-tbody');
  if(!tbody||!inv.invoice_no)return false;
  const exists=[...tbody.querySelectorAll('td.mono:first-child')].some(td=>td.textContent===inv.invoice_no);
  if(exists)return false;
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{maximumFractionDigits:2});
  const row=document.createElement('tr');
  row.dataset.salesInvoice=JSON.stringify(inv);
  row.innerHTML=`<td class="mono">${escapeHtml(inv.invoice_no)}</td><td>${escapeHtml(inv.customer)}</td><td>${escapeHtml(inv.date)}</td><td>${escapeHtml(inv.due_date||'30 days')}</td><td class="mono">${fmt(inv.subtotal)}</td><td class="mono">${fmt(inv.vat_amount)}</td><td class="mono">${fmt(inv.total)}</td><td><span class="b b-a">Pending</span></td><td><div class="flx"><button class="btn btn-g btn-sm" onclick="openSalesInvoiceRow(this)">View</button><button class="btn btn-p btn-sm" onclick="shareSalesInvoiceRow(this)">Share</button></div></td>`;
  tbody.prepend(row);
  if(options.persist)persistSalesInvoice(inv);
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
    status:cells[7]?.textContent.trim()||'Draft',
    lines:[{description:'Sales invoice items',qty:1,price:parseAmount(cells[4]?.textContent),amount:parseAmount(cells[4]?.textContent)}]
  };
}

function getInvoiceLayout(){
  const saved=loadLocal(STORAGE_KEYS.invoiceLayout,{});
  return {
    template:document.getElementById('inv-layout-template')?.value||saved.template||'Modern Tax Invoice',
    color:document.getElementById('inv-layout-color')?.value||saved.color||'#4f8ef0',
    company:document.getElementById('inv-layout-company')?.value||saved.company||'Acme Trading LLC',
    trnMode:document.getElementById('inv-layout-trn-mode')?.value||saved.trnMode||'show',
    address:document.getElementById('inv-layout-address')?.value||saved.address||'Dubai, United Arab Emirates',
    bank:document.getElementById('inv-layout-bank')?.value||saved.bank||'Bank transfer to Emirates NBD - IBAN AE070331234567890123456',
    footer:document.getElementById('inv-layout-footer')?.value||saved.footer||'Thank you for your business'
  };
}

function setInvoiceLayoutFields(layout=loadLocal(STORAGE_KEYS.invoiceLayout,{})){
  const fields={
    'inv-layout-template':layout.template,
    'inv-layout-color':layout.color,
    'inv-layout-company':layout.company,
    'inv-layout-trn-mode':layout.trnMode,
    'inv-layout-address':layout.address,
    'inv-layout-bank':layout.bank,
    'inv-layout-footer':layout.footer
  };
  Object.entries(fields).forEach(([id,value])=>{
    const field=document.getElementById(id);
    if(field&&value)field.value=value;
  });
}

function saveInvoiceLayout(){
  const layout=getInvoiceLayout();
  saveLocal(STORAGE_KEYS.invoiceLayout,layout);
  saveInvoiceLayoutServer(layout);
  updateInvoiceLayoutPreview();
  toast('Invoice layout saved ?','ok');
  audit('Saved invoice layout',layout.template,'Saved');
}

function updateInvoiceLayoutPreview(){
  const layout=getInvoiceLayout();
  const preview=document.getElementById('invoice-layout-preview');
  if(!preview)return;
  preview.innerHTML=`
    <div style="border-left:3px solid ${escapeHtml(layout.color)};padding-left:12px;margin-bottom:12px">
      <div class="card-title">${escapeHtml(layout.template)}</div>
      <div class="card-sub">${escapeHtml(layout.company)}</div>
    </div>
    <div style="font-size:12px;line-height:1.7;color:var(--text2)">
      <div>${escapeHtml(layout.address)}</div>
      <div>${layout.trnMode==='show'?'Company TRN visible':'Company TRN hidden'}</div>
      <div style="margin-top:8px">${escapeHtml(layout.bank)}</div>
      <div style="margin-top:8px;color:var(--text3)">${escapeHtml(layout.footer)}</div>
    </div>`;
}

function renderSalesInvoicePreview(inv){
  currentSalesInvoice=inv;
  const title=document.getElementById('sales-view-title');
  const sub=document.getElementById('sales-view-sub');
  const body=document.getElementById('sales-view-body');
  if(!body)return;
  const layout=getInvoiceLayout();
  const subtotal=Number(inv.subtotal||0);
  const vat=Number(inv.vat_amount||0);
  const total=Number(inv.total||subtotal+vat);
  const lines=(inv.lines&&inv.lines.length?inv.lines:[{description:'Sales invoice items',qty:1,price:subtotal,amount:subtotal}]);
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});

  if(title)title.textContent='Invoice '+(inv.invoice_no||'Draft');
  if(sub)sub.textContent=(inv.customer||'Customer')+' - '+(inv.status||'Draft');

  body.innerHTML=`
    <div style="border:1px solid var(--border);border-radius:10px;background:var(--bg3);padding:18px">
      <div class="flx-b mb16" style="border-top:4px solid ${escapeHtml(layout.color)};padding-top:14px">
        <div><div class="card-title">${escapeHtml(layout.company)}</div><div class="card-sub">${escapeHtml(layout.template)} - ${escapeHtml(layout.address)}</div>${layout.trnMode==='show'?'<div class="mono" style="color:var(--text3);font-size:11px;margin-top:3px">TRN 100123456700003</div>':''}</div>
        <span class="b b-b">${escapeHtml(inv.status||'Draft')}</span>
      </div>
      <div class="g2 mb16">
        <div>
          <div class="section-hd">Bill To</div>
          <div style="font-size:14px;font-weight:600">${escapeHtml(inv.customer||'Customer')}</div>
          <div class="mono" style="color:var(--text3);margin-top:4px">${escapeHtml(inv.customer_trn||'TRN not provided')}</div>
        </div>
        <div>
          <div class="section-hd">Invoice</div>
          <div class="flx-b"><span style="color:var(--text3)">Invoice No.</span><span class="mono">${escapeHtml(inv.invoice_no||'Draft')}</span></div>
          <div class="flx-b"><span style="color:var(--text3)">Date</span><span>${escapeHtml(inv.date||'-')}</span></div>
          <div class="flx-b"><span style="color:var(--text3)">Due Date</span><span>${escapeHtml(inv.due_date||'-')}</span></div>
        </div>
      </div>
      <table class="tbl">
        <thead><tr><th>Description</th><th style="text-align:right">Qty</th><th style="text-align:right">Unit Price</th><th style="text-align:right">Amount</th></tr></thead>
        <tbody>
          ${lines.map(line=>`<tr><td>${escapeHtml(line.description||'Item')}</td><td class="mono" style="text-align:right">${escapeHtml(line.qty||1)}</td><td class="mono" style="text-align:right">${fmt(line.price)}</td><td class="mono" style="text-align:right">${fmt(line.amount)}</td></tr>`).join('')}
        </tbody>
      </table>
      <div class="inv-total-row">
        <div class="inv-total-box">
          <div class="tot-row"><span style="color:var(--text3)">Subtotal</span><span class="mono">AED ${fmt(subtotal)}</span></div>
          <div class="tot-row"><span style="color:var(--text3)">VAT</span><span class="mono">AED ${fmt(vat)}</span></div>
          <div class="tot-final"><span>Total</span><span class="mono">AED ${fmt(total)}</span></div>
        </div>
      </div>
      <div class="divider"></div>
      <div style="font-size:12px;color:var(--text2);line-height:1.7">${escapeHtml(layout.bank)}</div>
      <div style="font-size:12px;color:var(--text3);margin-top:8px">${escapeHtml(layout.footer)}</div>
    </div>`;
}

let currentSalesInvoice=null;

function openSalesInvoiceRow(btn){
  const inv=invoiceFromSalesRow(btn.closest('tr'));
  renderSalesInvoicePreview(inv);
  showM('m-sales-view');
  audit('Viewed sales invoice',inv.invoice_no||'Draft','Viewed');
}

function shareSalesInvoiceRow(btn){
  const inv=invoiceFromSalesRow(btn.closest('tr'));
  currentSalesInvoice=inv;
  openInvoiceShareModal(inv);
}

function buildDraftInvoice(){
  const lines=[...document.querySelectorAll('#inv-lines .inv-item')].map(row=>{
    const inputs=[...row.querySelectorAll('input')];
    const qty=parseAmount(inputs[1]?.value);
    const price=parseAmount(inputs[2]?.value);
    return {description:inputs[0]?.value||'Item',qty,price,amount:qty*price};
  });
  const subtotal=lines.reduce((sum,line)=>sum+line.amount,0);
  const vat=subtotal*.05;
  return {
    invoice_no:document.getElementById('inv-no')?.value||'Draft',
    customer:document.getElementById('inv-cust')?.value||'Customer',
    customer_trn:document.getElementById('inv-ctrn')?.value||'TRN not provided',
    date:document.getElementById('inv-date')?.value||'',
    due_date:document.getElementById('inv-due')?.value||'',
    subtotal,
    vat_amount:vat,
    total:subtotal+vat,
    status:'Draft',
    lines
  };
}

function openDraftInvoicePreview(){
  const inv=buildDraftInvoice();
  renderSalesInvoicePreview(inv);
  showM('m-sales-view');
  audit('Previewed draft invoice',inv.invoice_no,'Viewed');
}

function openDraftInvoiceShare(){
  currentSalesInvoice=buildDraftInvoice();
  openInvoiceShareModal(currentSalesInvoice);
}

function invoiceShareMessage(inv=currentSalesInvoice){
  const total=Number(inv?.total||0).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
  return `Dear ${inv?.customer||'Customer'}, please find invoice ${inv?.invoice_no||'Draft'} for AED ${total}. Due date: ${inv?.due_date||'-'}.`;
}

function openInvoiceShareModal(inv=currentSalesInvoice){
  currentSalesInvoice=inv||currentSalesInvoice||buildDraftInvoice();
  const sub=document.getElementById('invoice-share-sub');
  const email=document.getElementById('share-email');
  const phone=document.getElementById('share-phone');
  const msg=document.getElementById('share-message');
  if(sub)sub.textContent=`${currentSalesInvoice.invoice_no||'Draft'} - ${currentSalesInvoice.customer||'Customer'}`;
  if(email&&!email.value)email.value='accounts@example.com';
  if(phone&&!phone.value)phone.value='+971 50 000 0000';
  if(msg)msg.value=invoiceShareMessage(currentSalesInvoice);
  showM('m-invoice-share');
}

function shareCurrentInvoice(channel){
  const inv=currentSalesInvoice||buildDraftInvoice();
  const msg=document.getElementById('share-message')?.value||invoiceShareMessage(inv);
  if(channel==='email'){
    toast(`Invoice ${inv.invoice_no||'Draft'} queued for email`, 'ok');
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

let customerReturnToInvoice=false;

function openAddCustomerFromInvoice(){
  customerReturnToInvoice=true;
  document.getElementById('cust-name').value=document.getElementById('inv-cust')?.value||'';
  document.getElementById('cust-trn').value=(document.getElementById('inv-ctrn')?.value||'').replace(/\D/g,'');
  showM('m-customer');
}

function saveCustomer(){
  const name=(document.getElementById('cust-name')?.value||'').trim();
  const trn=(document.getElementById('cust-trn')?.value||'').replace(/\D/g,'');
  const emirate=document.getElementById('cust-emirate')?.value||'Dubai';
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
    const row=document.createElement('tr');
    row.innerHTML=`<td>${escapeHtml(name)}</td><td class="mono">${escapeHtml(trn||'Not registered')}</td><td>${escapeHtml(emirate)}</td><td>${escapeHtml(email||phone||'-')}</td><td class="mono" style="color:var(--accent)">AED 0</td><td><button class="btn btn-g btn-sm">View</button></td>`;
    tbody.prepend(row);
  }
  saveServer('customers',{name,trn,emirate,email,phone});

  const shouldFillInvoice=customerReturnToInvoice;
  closeM('m-customer');
  if(shouldFillInvoice){
    const invoiceCustomer=document.getElementById('inv-cust');
    const invoiceTrn=document.getElementById('inv-ctrn');
    if(invoiceCustomer)invoiceCustomer.value=name;
    if(invoiceTrn)invoiceTrn.value=trn;
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
  const reader=new FileReader();
  reader.onload=function(e){
    const base64=e.target.result; // full data URL
    const cat=document.getElementById('pur-cat')?.value||'Purchase Invoices';
    const period=document.getElementById('pur-period')?.value||'June 2024';
    const entry={name:file.name,size:file.size,type:file.type,base64,category:cat,period,status:'Queued',id:'F'+Date.now()+Math.random().toString(36).slice(2,6)};
    uploadedFiles.push(entry);
    animateUpload(file.name,file.size,entry);
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
        renderFileList();
        updateFileCount();
        toast(name+' uploaded ?','ok');
        // auto-extract if setting says yes
        const autoEl=document.querySelector('#page-purchase select[id="pur-auto"]');
        if(!autoEl||autoEl.value.startsWith('Yes'))setTimeout(()=>extractSingleFile(entry),600);
      },300);
    }
    fill.style.width=Math.min(p,100)+'%';
    pct.textContent=Math.round(Math.min(p,100))+'%';
  },120);
}

function getFileIcon(name){
  const ext=name.split('.').pop().toLowerCase();
  return {pdf:'??',xlsx:'??',xls:'??',csv:'??',zip:'??',jpg:'??',jpeg:'??',png:'??'}[ext]||'??';
}
function fmtSize(bytes){if(bytes<1024*1024)return(bytes/1024).toFixed(0)+' KB';return(bytes/(1024*1024)).toFixed(1)+' MB';}

function renderFileList(){
  const list=document.getElementById('pur-file-list');
  // keep the 3 demo rows (they have no data-id), then append real ones
  const realRows=list.querySelectorAll('[data-file-id]');
  realRows.forEach(r=>r.remove());

  uploadedFiles.forEach(f=>{
    const statusBadge={
      Queued:'<span class="b b-gray">Queued</span>',
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
  if(badge) badge.textContent=(3+uploadedFiles.length)+' files';
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

    entry.status='Extracted';
    entry.invoices=invoices;
    renderFileList();
    appendExtractedRows(invoices,entry.name);
    updateExtractionStats();

    setTimeout(()=>{ep.style.display='none';ef.style.width='0%';},600);
    toast(`Extracted ${invoices.length} invoice(s) from ${entry.name} ?`,'ok');

    // auto-switch to validation if any errors
    if(invoices.some(i=>i.status!=='Valid')){
      setTimeout(()=>{
        const valTab=document.querySelector('#page-purchase .tab:nth-child(3)');
        if(valTab){stab(valTab,'p-validate');buildValidationPanel(invoices);}
        toast('Validation issues found - review required','warn');
      },800);
    }

  }catch(err){
    clearInterval(ticker);
    ep.style.display='none';
    entry.status='Error';
    renderFileList();
    toast('Extraction failed: '+err.message,'err');
    console.error(err);
  }
}

function appendExtractedRows(invoices,filename){
  const tbody=document.getElementById('ext-tbody');
  invoices.forEach(inv=>{
    // check if row already exists (by invoice_no)
    if([...tbody.querySelectorAll('td.mono')].some(td=>td.textContent===inv.invoice_no))return;
    const confCls=inv.confidence>=90?'b-g':inv.confidence>=70?'b-a':'b-r';
    const stCls={'Valid':'b-g','Review':'b-a','Error':'b-r'}[inv.status]||'b-gray';
    const trnColor=inv.supplier_trn&&inv.supplier_trn.length===15?'':'color:var(--red)';
    const fmt=n=>Number(n).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});
    const row=document.createElement('tr');
    row.setAttribute('data-inv',JSON.stringify(inv));
    row.innerHTML=`
      <td class="mono">${inv.invoice_no}</td>
      <td>${inv.date}</td>
      <td>${inv.supplier}</td>
      <td class="mono" style="${trnColor}">${inv.supplier_trn}</td>
      <td class="mono">${fmt(inv.subtotal)}</td>
      <td class="mono">${fmt(inv.vat_amount)}</td>
      <td class="mono">${fmt(inv.total)}</td>
      <td><span class="b ${confCls}">${inv.confidence}%</span></td>
      <td><span class="b ${stCls}">${inv.status}</span></td>
      <td><button class="btn btn-g btn-sm" onclick="openEditRow(this)">? Edit</button></td>`;
    tbody.insertBefore(row,tbody.firstChild);
  });
}

function updateExtractionStats(){
  const all=uploadedFiles.flatMap(f=>f.invoices||[]);
  const total=all.length;
  const review=all.filter(i=>i.status==='Review').length;
  const errors=all.filter(i=>i.status==='Error').length;
  const stats=document.querySelectorAll('#page-purchase #p-extract .stat .stat-val');
  if(stats[0])stats[0].textContent=total;
  if(stats[1])stats[1].textContent=review;
  if(stats[2])stats[2].textContent=errors;
}

function buildValidationPanel(invoices){
  const panel=document.getElementById('p-validate');
  // clear old dynamic issues (keep last green success banner as template)
  panel.querySelectorAll('.val-dynamic').forEach(e=>e.remove());
  const card=panel.querySelector('.card');

  const errors=invoices.filter(i=>i.status==='Error');
  const reviews=invoices.filter(i=>i.status==='Review');
  const valid=invoices.filter(i=>i.status==='Valid');

  errors.forEach(inv=>{
    const el=document.createElement('div');
    el.className='val-dynamic';
    el.style.cssText='background:var(--red-bg);border:1px solid var(--red-border);border-radius:10px;padding:14px 16px;margin-bottom:10px';
    el.innerHTML=`<div style="display:flex;align-items:flex-start;gap:10px">
      <span style="font-size:18px;flex-shrink:0">??</span>
      <div style="flex:1">
        <div style="font-size:13.5px;font-weight:600;margin-bottom:3px">Error - ${inv.supplier} (${inv.invoice_no})</div>
        <div style="font-size:12px;color:var(--text3)">${inv.issues||'TRN invalid or missing'}</div>
        <div style="margin-top:10px;display:flex;gap:8px;align-items:center">
          <input class="fi" style="width:200px" placeholder="Enter correct 15-digit TRN" id="fix-${inv.invoice_no.replace(/[^a-z0-9]/gi,'_')}">
          <button class="btn btn-danger btn-sm" onclick="fixTRN('${inv.invoice_no}',this)">Fix & Re-validate</button>
        </div>
      </div></div>`;
    card.insertBefore(el,card.firstChild);
  });

  reviews.forEach(inv=>{
    const expected=(inv.subtotal*0.05).toFixed(2);
    const el=document.createElement('div');
    el.className='val-dynamic';
    el.style.cssText='background:var(--amber-bg);border:1px solid var(--amber-border);border-radius:10px;padding:14px 16px;margin-bottom:10px';
    el.innerHTML=`<div style="display:flex;align-items:flex-start;gap:10px">
      <span style="font-size:18px;flex-shrink:0">??</span>
      <div style="flex:1">
        <div style="font-size:13.5px;font-weight:600;margin-bottom:3px">VAT Mismatch - ${inv.supplier} (${inv.invoice_no})</div>
        <div style="font-size:12px;color:var(--text3)">${inv.issues||'Extracted VAT AED '+inv.vat_amount+' ? 5% of AED '+inv.subtotal+' = AED '+expected}</div>
        <div style="margin-top:10px;display:flex;gap:8px">
          <button class="btn btn-g btn-sm" onclick="resolveReview(this,'keep')">Keep extracted (AED ${inv.vat_amount})</button>
          <button class="btn btn-g btn-sm" onclick="resolveReview(this,'calc')">Use calculated (AED ${expected})</button>
        </div>
      </div></div>`;
    card.insertBefore(el,card.firstChild);
  });

  // update the green banner count
  const greenBanner=card.querySelector('[style*="green-bg"]');
  if(greenBanner){
    const gTitle=greenBanner.querySelector('div div:first-child');
    const gSub=greenBanner.querySelector('div div:last-child');
    if(gTitle)gTitle.textContent=valid.length+' invoices passed all checks';
    if(gSub)gSub.textContent='TRN, VAT rate, date, duplicate scan - all clear';
  }
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
  const ready=uploadedFiles.filter(f=>f.status==='Ready'||f.status==='Queued');
  if(ready.length===0){toast('No new files to extract. Upload files first.','warn');return;}
  ready.forEach((f,i)=>setTimeout(()=>extractSingleFile(f),i*500));
}

let lineCount=1;
let productReturnToInvoice=false;
let productTargetLine=null;

function addLine(){
  lineCount++;
  const d=document.createElement('div');d.className='inv-item';
  d.innerHTML=`<input class="fi" placeholder="Description" style="font-size:12.5px"><input class="fi" value="1" style="font-size:12.5px" oninput="calcLine(this)"><input class="fi" value="0.00" style="font-size:12.5px" oninput="calcLine(this)"><input class="fi mono" value="0.00" readonly style="background:var(--bg)"><button class="btn btn-g" style="padding:4px 8px" onclick="remLine(this)">?</button>`;
  document.getElementById('inv-lines').appendChild(d);
  calcLine(null);
  return d;
}
function remLine(btn){btn.closest('.inv-item').remove();calcLine(null);}
function calcLine(inp){
  if(inp){const row=inp.closest('.inv-item');const inputs=[...row.querySelectorAll('input')];const qty=parseFloat(inputs[1].value)||0,price=parseFloat(inputs[2].value)||0;inputs[3].value=(qty*price).toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2});}
  let sub=0;
  document.querySelectorAll('#inv-lines .inv-item').forEach(r=>{const ins=[...r.querySelectorAll('input')];sub+=(parseFloat(ins[1].value)||0)*(parseFloat(ins[2].value)||0);});
  const vat=sub*0.05,tot=sub+vat;
  document.getElementById('subtotal').textContent='AED '+sub.toLocaleString('en-AE',{minimumFractionDigits:2});
  document.getElementById('vat-amt').textContent='AED '+vat.toLocaleString('en-AE',{minimumFractionDigits:2});
  document.getElementById('inv-total').textContent='AED '+tot.toLocaleString('en-AE',{minimumFractionDigits:2});
}

function editProd(code){toast('Opening '+code+' for editing...','info');}

function openAddProductFromInvoice(){
  productReturnToInvoice=true;
  productTargetLine=document.activeElement?.closest?.('.inv-item')||[...document.querySelectorAll('#inv-lines .inv-item')].find(row=>!(row.querySelector('input')?.value||'').trim())||document.querySelector('#inv-lines .inv-item')||addLine();
  const currentName=productTargetLine?.querySelector('input')?.value||'';
  const currentPrice=productTargetLine?.querySelectorAll('input')?.[2]?.value||'';
  document.getElementById('prod-name').value=currentName;
  document.getElementById('prod-price').value=currentPrice;
  showM('m-product');
}

function saveProd(){
  const tbody=document.getElementById('prod-tbody');
  const code=(document.getElementById('prod-code')?.value||`PRD-00${(tbody?.rows.length||0)+1}`).trim();
  const name=(document.getElementById('prod-name')?.value||'').trim();
  const category=document.getElementById('prod-category')?.value||'Materials';
  const unit=document.getElementById('prod-unit')?.value||'Each';
  const price=parseFloat(String(document.getElementById('prod-price')?.value||'0').replace(/,/g,''))||0;
  const vat=document.getElementById('prod-vat')?.value||'Standard 5%';

  if(!name){
    toast('Enter product or service name','warn');
    return;
  }

  const shouldFillInvoice=productReturnToInvoice;
  const targetLine=productTargetLine;
  const row=document.createElement('tr');
  const vatText=vat.includes('0')&&!vat.includes('5')?'0% Zero':vat.includes('Exempt')?'Exempt':'5%';
  const vatClass=vatText==='5%'?'b-b':'b-t';
  row.innerHTML=`<td class="mono">${escapeHtml(code)}</td><td>${escapeHtml(name)}</td><td><span class="b b-gray">${escapeHtml(category)}</span></td><td>${escapeHtml(unit)}</td><td class="mono">${price.toLocaleString('en-AE',{minimumFractionDigits:2,maximumFractionDigits:2})}</td><td><span class="b ${vatClass}">${escapeHtml(vatText)}</span></td><td><span class="b b-g">Active</span></td><td><button class="btn btn-g btn-sm" onclick="editProd(this.closest('tr').querySelector('td').textContent)">Edit</button></td>`;
  tbody.prepend(row);
  saveServer('products',{code,name,category,unit,price,vat});

  closeM('m-product');
  if(shouldFillInvoice&&targetLine){
    const inputs=[...targetLine.querySelectorAll('input')];
    if(inputs[0])inputs[0].value=name;
    if(inputs[2])inputs[2].value=price.toFixed(2);
    calcLine(inputs[2]||null);
  }

  ['prod-code','prod-name','prod-price','prod-desc'].forEach(id=>{
    const field=document.getElementById(id);
    if(field)field.value='';
  });
  toast('Product added to catalogue ?','ok');
  audit('Added product',name,'Saved');
}

// -- ACCOUNTING ---------------------------------------------------
function accountOptionsHtml(){
  const rows=[...document.querySelectorAll('#account-tbody tr')];
  return '<option>Select Account...</option>'+rows.map(row=>{
    const cells=row.querySelectorAll('td');
    return `<option>${escapeHtml(cells[1]?.textContent.trim()||'Account')} (${escapeHtml(cells[0]?.textContent.trim()||'0000')})</option>`;
  }).join('');
}

function addJournalLine(account='',debit='',credit=''){
  const wrap=document.getElementById('journal-lines');
  if(!wrap)return null;
  const row=document.createElement('div');
  row.className='inv-item journal-line';
  row.innerHTML=`<select class="fi journal-account" onchange="recalcJournal()">${accountOptionsHtml()}</select><input class="fi mono journal-debit" placeholder="0.00" value="${escapeHtml(debit)}" oninput="recalcJournal()"><input class="fi mono journal-credit" placeholder="0.00" value="${escapeHtml(credit)}" oninput="recalcJournal()"><button class="btn btn-g" style="padding:4px 8px" onclick="remJournalLine(this)">?</button>`;
  wrap.appendChild(row);
  if(account)row.querySelector('.journal-account').value=account;
  recalcJournal();
  return row;
}

function remJournalLine(btn){
  btn.closest('.journal-line')?.remove();
  recalcJournal();
}

function getJournalLines(){
  return [...document.querySelectorAll('#journal-lines .journal-line')].map(row=>({
    account:row.querySelector('.journal-account')?.value||'',
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
  toast('Journal draft saved','ok');
  audit('Saved journal draft',document.getElementById('journal-ref')?.value||'Draft','Saved');
}

function postLedgerLine({date,ref,description,debit=0,credit=0,account=''},{persist=true}={}){
  const tbody=document.getElementById('ledger-tbody');
  if(!tbody)return;
  const balance=debit-credit;
  const fmt=n=>Number(n||0).toLocaleString('en-AE',{maximumFractionDigits:2});
  const row=document.createElement('tr');
  row.dataset.account=account.replace(/\s*\(\d+\)\s*$/,'');
  row.innerHTML=`<td>${escapeHtml(date)}</td><td class="mono">${escapeHtml(ref)}</td><td>${escapeHtml(description)}${account?' - '+escapeHtml(account):''}</td><td class="mono">${debit?fmt(debit):'-'}</td><td class="mono">${credit?fmt(credit):'-'}</td><td class="mono">${fmt(balance)}</td>`;
  tbody.prepend(row);
  if(persist)saveServer('ledger',{date,ref,description,debit,credit,account});
}

function postJournalEntry(){
  const date=document.getElementById('journal-date')?.value||'';
  const ref=(document.getElementById('journal-ref')?.value||'').trim();
  const desc=(document.getElementById('journal-desc')?.value||'').trim();
  const rawLines=getJournalLines();
  const lines=getJournalLines().filter(line=>line.account&&line.account!=='Select Account...'&&(line.debit||line.credit));
  const totals=recalcJournal();

  if(!date){toast('Journal date is required','err');return;}
  if(!ref){toast('Reference number is required','err');return;}
  if(!desc){toast('Description is required','err');return;}
  if(rawLines.some(line=>(line.debit||line.credit)&&(!line.account||line.account==='Select Account...'))){toast('Select an account for every amount line','err');return;}
  if(lines.length<2){toast('Add at least two journal lines','err');return;}
  if(lines.some(line=>line.debit&&line.credit)){toast('A line cannot have both debit and credit','err');return;}
  if(Math.abs(totals.diff)>.01){toast('Journal must balance before posting','err');return;}

  lines.forEach(line=>postLedgerLine({date,ref,description:desc,debit:line.debit,credit:line.credit,account:line.account}));
  toast('Journal entry posted ?','ok');
  audit('Posted journal entry',ref,'Posted');
  filterLedger();
}

function saveAccount(){
  const code=(document.getElementById('acc-code')?.value||'').trim();
  const name=(document.getElementById('acc-name')?.value||'').trim();
  const type=document.getElementById('acc-type')?.value||'Asset';
  const category=document.getElementById('acc-category')?.value||'Current';
  if(!code||!name){toast('Account code and name are required','err');return;}
  const tbody=document.getElementById('account-tbody');
  if(!tbody)return;
  if([...tbody.querySelectorAll('td:first-child')].some(td=>td.textContent.trim()===code)){toast('Account code already exists','err');return;}
  const typeClass={Asset:'b-t',Liability:'b-r',Revenue:'b-g',Expense:'b-p',Equity:'b-b'}[type]||'b-gray';
  const row=document.createElement('tr');
  row.innerHTML=`<td class="mono">${escapeHtml(code)}</td><td>${escapeHtml(name)}</td><td><span class="b ${typeClass}">${escapeHtml(type)}</span></td><td>${escapeHtml(category)}</td><td class="mono">0.00</td><td><span class="b b-g">Active</span></td><td><button class="btn btn-g btn-sm" onclick="viewAccountLedger(this)">View</button></td>`;
  tbody.appendChild(row);
  saveServer('accounts',{code,name,type,category});
  updateAccountSelectors();
  closeM('m-acc');
  ['acc-code','acc-name'].forEach(id=>{const field=document.getElementById(id);if(field)field.value='';});
  toast('Account added to chart ?','ok');
  audit('Added account',code+' '+name,'Saved');
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
    filter.innerHTML='<option>All Accounts</option>'+[...document.querySelectorAll('#account-tbody tr td:nth-child(2)')].map(td=>`<option>${escapeHtml(td.textContent.trim())}</option>`).join('');
    if([...filter.options].some(option=>option.value===current))filter.value=current;
  }
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

function publishRota(){
  toast('Rota published. Employees notified and attendance timing updated','ok');
  audit('Rota published','Rota Planning','Published');
}

function copyPreviousRota(){
  toast('Previous week copied. Leave, inactive employee, and hour-limit checks completed.','ok');
  audit('Previous rota copied','Rota Planning','Draft');
}

function autoGenerateRota(){
  toast('Rota auto-generated from availability, leave, coverage, and rest-day rules.','ok');
  audit('Rota auto-generated','Rota Planning','Draft');
}

function copyPreviousMonthRota(){
  toast('Previous month copied. Leave, inactive employee, conflict, and hour-limit checks completed.','ok');
  audit('Previous month rota copied','Rota Planning','Draft');
}

function autoGenerateMonthlyRota(){
  toast('Monthly rota auto-generated from availability, holidays, coverage, rest gaps, and role skills.','ok');
  audit('Monthly rota auto-generated','Rota Planning','Draft');
}

function submitRotaApproval(){
  toast('Rota submitted to supervisor for approval','info');
  audit('Rota submitted for approval','Rota Planning','Pending');
}

function approveRotaRow(btn,msg='Rota approved'){
  const row=btn.closest('tr');
  const statusCell=row.querySelector('td:nth-last-child(2)');
  if(statusCell)statusCell.innerHTML='<span class="b b-g">Approved</span>';
  row.querySelector('td:last-child').innerHTML='<button class="btn btn-g btn-sm">View</button>';
  toast(msg+' ?','ok');
  audit(msg,'Rota Planning','Approved');
}

function rejectRotaRow(btn,msg='Rota rejected'){
  const row=btn.closest('tr');
  const statusCell=row.querySelector('td:nth-last-child(2)');
  if(statusCell)statusCell.innerHTML='<span class="b b-r">Rejected</span>';
  row.querySelector('td:last-child').innerHTML='<button class="btn btn-g btn-sm">View</button>';
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

  if(summary){
    summary.textContent='AI detected strong revenue growth, stable gross margin, and a collection risk concentrated in one 61-90 day customer balance. VAT payable is manageable if purchase invoice exceptions are cleared before filing.';
  }
  if(actions){
    actions.innerHTML='<div>- Prioritize Emirates Supplies collection before month-end.</div><div>- Clear supplier TRN and VAT math exceptions before VAT return approval.</div><div>- Model supplier discount impact because COGS is the largest profit lever.</div>';
  }
  if(output){
    output.innerHTML=`<strong style="color:var(--text)">${reportType}</strong><br>Revenue is trending ahead of prior month by 14.2%, while gross margin remains healthy at 34.0%. The main operational risk is receivables aging: AED 32,550 is now in the 61-90 day bucket. VAT payable is AED 16,410, with AI recommending invoice validation cleanup before export. Cash remains positive over 60 days but turns negative in the 90-day forecast unless collections improve.`;
  }
  if(risk){
    risk.style.color='var(--amber)';
    risk.textContent='Medium';
  }
  toast('AI report insights generated ?','ok');
  audit('Generated AI report',reportType,'Logged');
}

function simulateReportScenario(type){
  const output=document.getElementById('ai-report-output');
  const summary=document.getElementById('ai-summary');
  let msg='Scenario generated';
  if(type==='supplier'){
    msg='Supplier cost scenario: reducing COGS by 2% improves monthly net profit by about AED 19,300 and lifts gross margin from 34.0% to 36.0%.';
  }else if(type==='growth'){
    msg='Growth scenario: 10% revenue uplift adds about AED 96,500 revenue, AED 32,800 gross profit, and approximately AED 4,600 additional output VAT before input offsets.';
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
  let response='The current reports suggest revenue growth is healthy, but working capital needs attention. Review receivables aging, VAT exceptions, and expense variance before final management reporting.';
  if(lower.includes('cash')){
    response='Cash can decrease while revenue increases when invoices are unpaid, inventory purchases rise, or payroll and supplier payments happen before collections. In this data, the 61-90 day receivable balance is the strongest cash pressure signal.';
  }else if(lower.includes('vat')){
    response='VAT payable is AED 16,410. Before filing, resolve invoices with TRN or VAT calculation warnings so input VAT claims are supportable.';
  }else if(lower.includes('profit')||lower.includes('margin')){
    response='Net profit is AED 214,080 for June. Gross margin is 34.0%; a small COGS reduction has more impact than cutting smaller operating expense lines.';
  }
  if(answer)answer.textContent=response;
  toast('AI answer generated','ok');
}

// -- SYSTEM AI ASSISTANT ------------------------------------------
function buildSystemAIResponse(question){
  const q=question.toLowerCase();
  if(q.includes('invoice')||q.includes('sales')||q.includes('customer')||q.includes('product')){
    return 'For invoices: open Sales & Invoices, use Create Invoice, add or select a customer, add products in Line Items, then use Preview PDF to view the formatted invoice. Invoice layout is configured in Settings > Documents > Invoice Layout Setup. Uploaded sales invoices can also be imported from PDF, Excel, CSV, JPEG, or PNG and stored in the invoice register.';
  }
  if(q.includes('layout')||q.includes('template')||q.includes('footer')||q.includes('bank note')||q.includes('logo')){
    return 'Invoice layout is controlled from Settings > Documents. You can set the template style, accent color, company display name, TRN visibility, header address, bank/payment note, and footer message. The invoice preview uses those settings immediately.';
  }
  if(q.includes('purchase')||q.includes('ocr')||q.includes('extract')||q.includes('document')){
    return 'Purchases support document upload and extraction through /api/documents/extract. In this static prototype, if the backend is unavailable, deterministic demo data is used so the validation workflow still works. Extracted purchase invoices can be reviewed, corrected, and validated before becoming records.';
  }
  if(q.includes('bill')||q.includes('vendor')||q.includes('payable')||q.includes('purchase order')){
    return 'Bills & Vendors covers vendor bills, supplier directory, purchase orders, and aged payables. Use it to record payables, track due dates, manage vendor TRNs, and monitor supplier balances before payment.';
  }
  if(q.includes('payment')||q.includes('receipt')||q.includes('gateway')||q.includes('settlement')){
    return 'Payments tracks customer receipts, supplier payments, and gateway settlements. Customer receipts can be allocated to invoices, supplier payments can clear bills, and gateway settlement views show gross, fees, and net amounts.';
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
    return 'Accounting now includes Chart of Accounts, Journal Entry, and General Ledger. Journal entries require date, reference, description, at least two valid lines, selected accounts, and balanced debit/credit totals before posting. Posted journals appear in the ledger, and account View filters ledger entries.';
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
    return 'Bank Accounts covers accounts, transactions, and reconciliation. The current prototype has static bank data and reconciliation UI. A production version should add bank feed connections, CSV/MT940 imports, matching rules, and payment allocation to invoices and ledgers.';
  }
  if(q.includes('report')||q.includes('dashboard')||q.includes('cash')||q.includes('profit')||q.includes('aging')){
    return 'Reports include executive dashboard, VAT report, P&L, trial balance, aging report, and AI insights. The report AI can explain cash, VAT, profit, margin, receivables, and anomaly signals using the prototype data.';
  }
  if(q.includes('roadmap')||q.includes('production')||q.includes('improve')||q.includes('backend')||q.includes('database')){
    return 'Production priorities: add authentication, tenant/company scoping, database persistence, object storage for uploaded files, real extraction APIs, audit trails, role permissions, backend AI, eInvoicing adapters, bank integrations, tests, and CI. The detailed backlog is in docs/improvement-roadmap.md.';
  }
  return 'TaxFlow is organized into Dashboard, Sales & Invoices, Purchases, Expenses, Bank, Accounting, Reports, Inventory, Staff, Payroll, Expert Review, Mobile App, Settings, and this AI Assistant. Ask about a module name or workflow such as invoice creation, purchase extraction, journal posting, VAT filing, payroll WPS, settings, or production roadmap.';
}

function askSystemAI(prompt){
  const input=document.getElementById('system-ai-question');
  const answer=document.getElementById('system-ai-answer');
  const question=(prompt||input?.value||'').trim();
  if(!question){
    toast('Enter a system question first','warn');
    return;
  }
  if(input)input.value=question;
  if(answer){
    answer.innerHTML=`<strong style="color:var(--text)">Question</strong><br>${escapeHtml(question)}<div class="divider"></div><strong style="color:var(--text)">Answer</strong><br>${escapeHtml(buildSystemAIResponse(question))}`;
  }
  toast('AI assistant answered','ok');
  audit('Asked AI assistant',question.slice(0,60),'Answered');
}

function clearSystemAI(){
  const input=document.getElementById('system-ai-question');
  const answer=document.getElementById('system-ai-answer');
  if(input)input.value='';
  if(answer)answer.textContent='Ask a question to get a system-specific answer.';
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
  row.innerHTML=`<td class="mono">${escapeHtml(billNo)}</td><td>${escapeHtml(vendor)}</td><td>${escapeHtml(date)}</td><td>${escapeHtml(due)}</td><td class="mono">${subtotal.toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${vat.toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td class="mono">${total.toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td><span class="b b-a">Awaiting Payment</span></td><td><button class="btn btn-g btn-sm" onclick="toast('Bill preview opened','info')">View</button></td>`;
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
  if(!name){toast('Vendor name is required','err');return;}
  if(trn&&trn.length!==15){toast('Vendor TRN must be 15 digits','err');return;}
  const row=document.createElement('tr');
  row.innerHTML=`<td>${escapeHtml(name)}</td><td class="mono">${escapeHtml(trn||'Not registered')}</td><td>${escapeHtml(category)}</td><td>${escapeHtml(email||'-')}</td><td class="mono">0.00</td><td><span class="b b-g">Active</span></td>`;
  document.getElementById('vendor-tbody')?.prepend(row);
  saveServer('vendors',{name,trn,category,email});
  closeM('m-vendor');
  toast('Vendor added ?','ok');
  audit('Added vendor',name,'Saved');
}

function savePayment(){
  const type=document.getElementById('payment-type')?.value||'Customer Receipt';
  const ref=(document.getElementById('payment-ref')?.value||'PMT-NEW').trim();
  const contact=(document.getElementById('payment-contact')?.value||'').trim();
  const amount=parseAmount(document.getElementById('payment-amount')?.value);
  const method=document.getElementById('payment-method')?.value||'Bank Transfer';
  const date=document.getElementById('payment-date')?.value||'Today';
  if(!contact||!amount){toast('Contact and amount are required','err');return;}
  const row=document.createElement('tr');
  row.innerHTML=`<td class="mono">${escapeHtml(ref)}</td><td>${escapeHtml(contact)}</td><td class="mono">Manual</td><td>${escapeHtml(method)}</td><td>${escapeHtml(date)}</td><td class="mono">${amount.toLocaleString('en-AE',{maximumFractionDigits:2})}</td><td><span class="b b-g">Posted</span></td>`;
  if(type==='Customer Receipt')document.getElementById('payment-in-tbody')?.prepend(row);
  else document.getElementById('payment-out-tbody')?.prepend(row);
  saveServer('payments',{type,ref,contact,amount,method,date});
  closeM('m-payment');
  toast('Payment recorded ?','ok');
  audit('Recorded payment',ref,'Posted');
}

// -- SETTINGS ----------------------------------------------------
function saveSettings(message='Settings saved'){
  toast(message+' ?','ok');
  audit(message,'Settings','Saved');
}

function testIntegration(name){
  toast('Testing '+name+' integration...','info');
  setTimeout(()=>toast(name+' integration connected ?','ok'),900);
}

function rotateApiKey(){
  toast('API key rotated. Existing key will expire in 24 hours.','warn');
  audit('Rotated API key','Integrations','Rotated');
}

function runBackup(){
  toast('Backup started...','info');
  setTimeout(()=>{toast('Encrypted backup completed ?','ok');audit('Completed encrypted backup','Full tenant','Complete');},1200);
}

function initApp(){
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

  restoreSalesInvoices();
  renderAuditLog();
  setInvoiceLayoutFields();
  updateInvoiceLayoutPreview();
  hydrateFromServer();
  scheduleIdleTask(()=>{
    updateAccountSelectors();
    recalcJournal();
    recalcPayroll();
    calcGratuity();
  },900);
}

initApp();


  var ESS_TOKEN_KEY = 'ess_token';
  var ESS_COMPANY_KEY = 'ess_company_id';

  // Used by every render function added alongside the Dashboard redesign
  // (announcements, notifications, today's schedule, quick actions) --
  // admin-authored text (an announcement's title/message) still shouldn't
  // be interpolated into innerHTML raw.
  function escHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function(c) {
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function apiBase() {
    if (window.TAXFLOW_API_BASE_URL) return window.TAXFLOW_API_BASE_URL;
    var h = window.location.hostname;
    if (['localhost','127.0.0.1','::1',''].includes(h))
      return window.location.protocol + '//' + window.location.host + '/api/v1';
    return window.location.protocol + '//' + h + '/api/v1';
  }

  function essHeaders() {
    var t = localStorage.getItem(ESS_TOKEN_KEY);
    return {'Content-Type':'application/json','Authorization': t ? 'Bearer '+t : ''};
  }

  // The ESS company id comes from the link HR shares with employees
  // (?c=<company_id>) and is only used to disambiguate an Employee No.
  // login (not unique across companies on this platform). It is deliberately
  // NOT cached as a silent fallback for bare-URL visits — a stale cached
  // company from a previous visit would make login check the wrong company
  // and fail with a confusing "Invalid credentials", even with the right
  // username/password. Portal usernames are unique platform-wide, so a
  // username-based login works with no company reference at all.
  function essCompanyId() {
    var fromUrl = new URLSearchParams(window.location.search).get('c');
    if (fromUrl) { localStorage.setItem(ESS_COMPANY_KEY, fromUrl); return fromUrl; }
    return '';
  }

  async function essLogin() {
    var btn = document.getElementById('ess-login-btn');
    var err = document.getElementById('login-err');
    var user = (document.getElementById('ess-user').value || '').trim();
    var pass = document.getElementById('ess-pass').value;
    var companyId = essCompanyId();
    err.style.display = 'none';
    if (!user) { err.textContent = 'Please enter your Employee ID or username.'; err.style.display = 'block'; return; }
    btn.disabled = true; btn.textContent = 'Signing in…';
    try {
      var body = {username: user, password: pass || user};
      if (companyId) body.company_id = companyId;
      var r = await fetch(apiBase() + '/ess/login', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify(body)
      });
      var d = await r.json();
      if (r.ok && d.access_token) {
        localStorage.setItem(ESS_TOKEN_KEY, d.access_token);
        showApp();
      } else {
        err.textContent = d.detail || "We couldn't sign you in — check your username and password and try again.";
        err.style.display = 'block';
        btn.disabled = false; btn.textContent = 'Sign In';
      }
    } catch(e) {
      err.textContent = 'Cannot reach server. Please try again.';
      err.style.display = 'block';
      btn.disabled = false; btn.textContent = 'Sign In';
    }
  }

  async function essChangePassword() {
    var btn = document.getElementById('pw-btn');
    var err = document.getElementById('pw-err');
    var ok = document.getElementById('pw-ok');
    var current = document.getElementById('pw-current').value;
    var next = document.getElementById('pw-new').value;
    err.style.display = 'none'; ok.style.display = 'none';
    if (!current || !next) { err.textContent = 'Please fill in both fields.'; err.style.display = 'block'; return; }
    if (next.length < 6) { err.textContent = 'New password must be at least 6 characters.'; err.style.display = 'block'; return; }
    btn.disabled = true; btn.textContent = 'Updating…';
    try {
      var r = await fetch(apiBase() + '/ess/change-password', {
        method: 'POST', headers: essHeaders(),
        body: JSON.stringify({current_password: current, new_password: next})
      });
      if (r.status === 401) { essLogout(); return; }
      var d = await r.json();
      if (r.ok && d.ok) {
        ok.style.display = 'block';
        document.getElementById('pw-current').value = '';
        document.getElementById('pw-new').value = '';
      } else {
        err.textContent = d.detail || 'Could not update password.';
        err.style.display = 'block';
      }
    } catch(e) {
      err.textContent = 'Cannot reach server. Please try again.';
      err.style.display = 'block';
    }
    btn.disabled = false; btn.textContent = 'Update Password';
  }

  function essLogout() {
    stopGpsPingLoop();
    localStorage.removeItem(ESS_TOKEN_KEY);
    window.location.reload();
  }

  // ── GPS check-in/out — same "emp:" token as the rest of ESS, posted to
  // the RBAC/geofencing endpoints in app/routers/hr_access.py. Pings every
  // 2 minutes while checked in, matching the interval documented in
  // docs/hrms-architecture.md §7 (Live Employee Tracking).
  var gpsPingTimer = null;

  function getPosition() {
    return new Promise(function(resolve, reject) {
      if (!navigator.geolocation) { reject(new Error('GPS is not available on this device/browser.')); return; }
      navigator.geolocation.getCurrentPosition(resolve, function(e) {
        reject(new Error(e.code === 1 ? 'Location permission denied. Enable location access to check in.' : 'Could not get your location. Try again.'));
      }, {enableHighAccuracy:true, timeout:15000, maximumAge:0});
    });
  }

  function gpsSetStatus(text, isErr) {
    var s = document.getElementById('gps-status');
    if (s) s.textContent = text;
    var err = document.getElementById('gps-err');
    if (err) { err.style.display = isErr ? 'block' : 'none'; if (isErr) err.textContent = text; }
  }

  async function refreshGpsStatus() {
    try {
      var r = await fetch(apiBase() + '/hr/dashboard', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      var d = await r.json();
      if (d.checked_in) {
        gpsSetStatus('Checked in since ' + (d.check_in_time ? d.check_in_time.substring(0,16).replace('T',' ') : '—') + '.');
        document.getElementById('gps-checkin-btn').style.display = 'none';
        document.getElementById('gps-checkout-btn').style.display = 'inline-block';
        startGpsPingLoop();
      } else {
        gpsSetStatus('Not checked in.');
        document.getElementById('gps-checkin-btn').style.display = 'inline-block';
        document.getElementById('gps-checkout-btn').style.display = 'none';
        stopGpsPingLoop();
      }
    } catch(e) { gpsSetStatus('Cannot reach server.', true); }
  }

  async function gpsCheckIn() {
    var btn = document.getElementById('gps-checkin-btn');
    btn.disabled = true;
    try {
      var pos = await getPosition();
      var r = await fetch(apiBase() + '/hr/check-in', {
        method: 'POST', headers: essHeaders(),
        body: JSON.stringify({latitude: pos.coords.latitude, longitude: pos.coords.longitude, accuracy: pos.coords.accuracy, device: navigator.userAgent.substring(0,120)})
      });
      if (r.status === 401) { essLogout(); return; }
      var d = await r.json();
      if (r.ok) { await refreshGpsStatus(); }
      else { gpsSetStatus(d.detail || 'Check-in failed.', true); }
    } catch(e) { gpsSetStatus(e.message || 'Check-in failed.', true); }
    btn.disabled = false;
  }

  async function gpsCheckOut() {
    var btn = document.getElementById('gps-checkout-btn');
    btn.disabled = true;
    try {
      var pos = await getPosition().catch(function(){ return null; });
      var r = await fetch(apiBase() + '/hr/check-out', {
        method: 'POST', headers: essHeaders(),
        body: pos ? JSON.stringify({latitude: pos.coords.latitude, longitude: pos.coords.longitude}) : '{}'
      });
      if (r.status === 401) { essLogout(); return; }
      var d = await r.json();
      if (r.ok) { stopGpsPingLoop(); await refreshGpsStatus(); }
      else { gpsSetStatus(d.detail || 'Check-out failed.', true); }
    } catch(e) { gpsSetStatus(e.message || 'Check-out failed.', true); }
    btn.disabled = false;
  }

  function startGpsPingLoop() {
    if (gpsPingTimer) return;
    gpsPingTimer = setInterval(async function() {
      try {
        var pos = await getPosition();
        var r = await fetch(apiBase() + '/hr/location', {
          method: 'POST', headers: essHeaders(),
          body: JSON.stringify({latitude: pos.coords.latitude, longitude: pos.coords.longitude, accuracy: pos.coords.accuracy, device: navigator.userAgent.substring(0,120)})
        });
        var d = await r.json();
        if (r.ok && d.auto_checked_out) { stopGpsPingLoop(); refreshGpsStatus(); }
      } catch(e) { /* transient GPS/network errors are not surfaced on every ping */ }
    }, 120000);
  }

  function stopGpsPingLoop() {
    if (gpsPingTimer) { clearInterval(gpsPingTimer); gpsPingTimer = null; }
  }

  var ESS_PAGES = {
    dashboard: ['Dashboard', ''],
    attendance: ['Attendance', 'Your check-ins, hours and correction requests'],
    leave: ['Leave', 'Time off, balances and the team calendar'],
    requests: ['Requests', 'Everything you have sent to HR, and where it stands'],
    rota: ['Rota & Shift', 'Your shifts and your department’s schedule'],
    tasks: ['Tasks', 'Work assigned to you'],
    payslips: ['Payslips', 'Your pay history — print or save any payslip'],
    gps: ['GPS Check-In', 'Check in and out from your phone'],
    profile: ['Profile', 'Your details, documents and password'],
    team: ['Team', 'Who is in today across your department']
  };
  function essTab(el, id) {
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('on'));
    document.querySelectorAll('.tab-body').forEach(t => t.classList.remove('on'));
    document.querySelectorAll('.ess-nav').forEach(t => t.removeAttribute('aria-current'));
    el.classList.add('on');
    el.setAttribute('aria-current', 'page');
    document.getElementById('tb-' + id).classList.add('on');
    var hd = document.getElementById('ess-page-hd'), meta = ESS_PAGES[id] || [id, ''];
    if (hd) {
      // Dashboard and Rota draw their own header (Rota: the illustrated banner).
      hd.style.display = id === 'dashboard' || id === 'rota' ? 'none' : '';
      document.getElementById('ess-page-title').textContent = meta[0];
      document.getElementById('ess-page-sub').textContent = meta[1];
    }
    // The drawer is a phone/tablet pattern; on desktop the sidebar stays where the user left it.
    if (window.innerWidth <= 860) toggleEssSidebar(false);
    essSyncTabbar(id);
    var c = document.querySelector('.content');
    if (c) c.scrollTop = 0;
  }

  // The nav items, dashboard tiles/quick actions and calendar days are
  // clickable <div>s -- give them button semantics so they're reachable by
  // Tab and operable with Enter/Space (re-run after the calendar re-renders).
  var ESS_CLICKABLE = '.ess-nav,.ess-kpi,.ess-qa,.ess-appr-row,.hcal-cell:not(.hcal-empty)';
  function essMakeKeyboardOperable(root) {
    (root || document).querySelectorAll(ESS_CLICKABLE).forEach(function(el) {
      if (el.hasAttribute('tabindex')) return;
      el.setAttribute('tabindex', '0');
      el.setAttribute('role', 'button');
    });
  }
  document.addEventListener('keydown', function(e) {
    if ((e.key === 'Enter' || e.key === ' ') && e.target.matches && e.target.matches(ESS_CLICKABLE)) {
      e.preventDefault();
      e.target.click();
    }
  });
  essMakeKeyboardOperable();

  function toggleEssSidebar(force) {
    if (window.innerWidth > 860) {
      // Desktop: the hamburger collapses / restores the sidebar (like HRMS).
      if (force === false) return;
      document.body.classList.toggle('ess-sb-hidden');
      return;
    }
    var open = force !== undefined ? force : !document.body.classList.contains('ess-sb-open');
    document.body.classList.toggle('ess-sb-open', open);
  }

  // ── Topbar search: jump to a page or start an action ──
  var ESS_SEARCH_INDEX = [
    {t: 'Dashboard', kind: 'Page', k: 'home overview', go: function() { essGoTab('dashboard'); }},
    {t: 'Attendance', kind: 'Page', k: 'check in out punches hours calendar', go: function() { essGoTab('attendance'); }},
    {t: 'GPS Check-In', kind: 'Page', k: 'clock in clock out location', go: function() { essGoTab('gps'); }},
    {t: 'Rota & Shift', kind: 'Page', k: 'schedule shifts roster', go: function() { essGoTab('rota'); }},
    {t: 'Tasks', kind: 'Page', k: 'to do work assigned', go: function() { essGoTab('tasks'); }},
    {t: 'Leave', kind: 'Page', k: 'time off holiday balance calendar', go: function() { essGoTab('leave'); }},
    {t: 'Requests', kind: 'Page', k: 'status approvals pending hr', go: function() { essGoTab('requests'); }},
    {t: 'Payslips', kind: 'Page', k: 'salary pay net print pdf', go: function() { essGoTab('payslips'); }},
    {t: 'Team', kind: 'Page', k: 'colleagues department who is in', go: function() { essGoTab('team'); }},
    {t: 'Profile', kind: 'Page', k: 'my details documents visa passport expiry password', go: function() { essGoTab('profile'); }},
    {t: 'Request leave', kind: 'Action', k: 'apply annual sick time off', go: function() { essOpenLeaveModal(); }},
    {t: 'Request overtime', kind: 'Action', k: 'ot extra hours', go: function() { essOpenRequestModal('overtime'); }},
    {t: 'Request a salary advance', kind: 'Action', k: 'advance money', go: function() { essOpenRequestModal('advance'); }},
    {t: 'Request a loan', kind: 'Action', k: 'borrow emi', go: function() { essOpenRequestModal('loan'); }},
    {t: 'Request an attendance correction', kind: 'Action', k: 'fix missing check-out punch forgot', go: function() { essOpenRequestModal('correction'); }},
    {t: 'Change password', kind: 'Action', k: 'security', go: function() { essGoTab('profile'); setTimeout(function() { var el = document.getElementById('pw-current'); if (el) el.focus(); }, 60); }},
    {t: 'Edit contact details', kind: 'Action', k: 'phone mobile address emergency', go: function() { essGoTab('profile'); essEditContact(true); }}
  ];
  var _essSearchHits = [], _essSearchIdx = 0;
  function essSearch(q) {
    var box = document.getElementById('ess-search-results');
    if (!box) return;
    q = (q || '').trim().toLowerCase();
    _essSearchHits = ESS_SEARCH_INDEX.filter(function(e) { return !q || (e.t + ' ' + e.k).toLowerCase().indexOf(q) > -1; });
    // A match in the title beats a match only in the hidden keywords ("over" -> Request overtime, not Dashboard/"overview").
    var inTitle = function(e) { return e.t.toLowerCase().indexOf(q) > -1 ? 0 : 1; };
    _essSearchHits = _essSearchHits.sort(function(a, b) { return inTitle(a) - inTitle(b); }).slice(0, 8);
    _essSearchIdx = 0;
    box.innerHTML = _essSearchHits.length ? _essSearchHits.map(function(e, i) {
      return '<div class="ess-search-item' + (i === 0 ? ' hl' : '') + '" data-i="' + i + '" onmousedown="essSearchPick(' + i + ')"><span>' + escHtml(e.t) + '</span><small>' + e.kind + '</small></div>';
    }).join('') : '<div class="ess-search-empty">No matching pages or actions</div>';
    box.classList.add('on');
  }
  function essSearchPick(i) {
    var hit = _essSearchHits[i];
    var input = document.getElementById('ess-search');
    document.getElementById('ess-search-results').classList.remove('on');
    if (input) { input.value = ''; input.blur(); }
    if (hit) hit.go();
  }
  function essSearchKey(e) {
    var box = document.getElementById('ess-search-results');
    if (e.key === 'Escape') { box.classList.remove('on'); e.target.blur(); return; }
    if (!_essSearchHits.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      _essSearchIdx = (_essSearchIdx + (e.key === 'ArrowDown' ? 1 : -1) + _essSearchHits.length) % _essSearchHits.length;
      box.querySelectorAll('.ess-search-item').forEach(function(n, i) { n.classList.toggle('hl', i === _essSearchIdx); });
    } else if (e.key === 'Enter') { e.preventDefault(); essSearchPick(_essSearchIdx); }
  }
  document.addEventListener('click', function(e) {
    var wrap = document.querySelector('.ess-search');
    if (wrap && !wrap.contains(e.target)) { var b = document.getElementById('ess-search-results'); if (b) b.classList.remove('on'); }
  });

  // The <head> script (runs before body paints) already set data-theme
  // from localStorage/system preference to avoid a flash -- this just
  // keeps the toggle button's icon/title in sync with whatever that
  // resolved to, and handles switching it afterward.
  function essApplyTheme(mode) {
    document.documentElement.setAttribute('data-theme', mode);
    var btn = document.getElementById('ess-theme-toggle');
    if (btn) {
      btn.textContent = mode === 'dark' ? '☀' : '☾';
      btn.title = mode === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
    }
  }
  function essToggleTheme() {
    var next = document.documentElement.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
    try { localStorage.setItem('ess_theme', next); } catch (e) {}
    essApplyTheme(next);
  }
  essApplyTheme(document.documentElement.getAttribute('data-theme') || 'light');

  var _essData = { attendance: [], leave: [], rota: [], tasks: [], payslips: [] };
  var _essMe = null;            // last /ess/me payload (profile, print header)
  var _essCurrency = 'AED';     // replaced by the company's real currency once /ess/me loads
  function essMoney(n) { return _essCurrency + ' ' + fmt(n); }

  // Consistent "nothing here yet" block: icon + title + one line of guidance.
  var ES_ICONS = {
    calendar: '<rect x="3" y="4" width="18" height="17" rx="2"/><path d="M8 2v4M16 2v4M3 10h18"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    check: '<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M8 12l3 3 5-6"/>',
    wallet: '<rect x="3" y="6" width="18" height="14" rx="2"/><path d="M3 10h18M16 15h2"/>'
  };
  function emptyState(icon, title, sub) {
    return '<div class="empty-state"><div class="es-ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' +
      (ES_ICONS[icon] || ES_ICONS.check) + '</svg></div><div class="es-title">' + escHtml(title) + '</div>' +
      (sub ? '<div class="es-sub">' + escHtml(sub) + '</div>' : '') + '</div>';
  }

  async function loadProfile() {
    try {
      var r = await fetch(apiBase() + '/ess/me', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      var emp = await r.json();
      _essMe = emp;
      essProcessRequestUpdates();
      if (emp.currency && emp.currency !== _essCurrency) {
        _essCurrency = emp.currency;
        renderPayslips();       // payslips may have loaded first, formatted with the default
        updateDashboard();
      }
      var initial = (emp.full_name || '?').trim().charAt(0).toUpperCase();
      var topNameEl = document.getElementById('ess-top-name');
      if (topNameEl) topNameEl.textContent = emp.full_name;
      var avatarHtml = emp.photo
        ? '<img src="' + escHtml(emp.photo) + '" style="width:100%;height:100%;object-fit:cover">'
        : escHtml(initial);
      var topAvEl = document.getElementById('ess-top-av');
      if (topAvEl) {
        topAvEl.innerHTML = avatarHtml;
        topAvEl.style.overflow = emp.photo ? 'hidden' : '';
      }
      document.getElementById('profile-fields').innerHTML =
        kv('Employee ID', escHtml(emp.employee_no)) +
        kv('Full Name', escHtml(emp.full_name)) +
        kv('Department', escHtml(emp.department)) +
        kv('Designation', escHtml(emp.designation)) +
        kv('Company', escHtml(emp.company_name || '—')) +
        kv('Working Hours', (emp.shift_start && emp.shift_end) ? escHtml(_fmt12h(emp.shift_start) + ' – ' + _fmt12h(emp.shift_end)) : '—') +
        kv('Status', '<span class="badge' + (emp.status !== 'active' ? ' inactive' : '') + '">' + escHtml(emp.status) + '</span>');
      var heroEl = document.getElementById('profile-hero');
      if (heroEl) heroEl.innerHTML =
        '<div class="profile-avatar">' + avatarHtml + '</div>' +
        '<div><div class="profile-hero-name">' + escHtml(emp.full_name) + '</div>' +
        '<div class="profile-hero-sub">' + escHtml(emp.designation || '') + (emp.designation && emp.department ? ' · ' : '') + escHtml(emp.department || '') + '</div></div>';
      var avEl = document.getElementById('dash-avatar');
      if (avEl) {
        avEl.innerHTML = avatarHtml;
        avEl.style.overflow = emp.photo ? 'hidden' : '';
      }
      var nameEl = document.getElementById('dash-name');
      if (nameEl) nameEl.textContent = _essGreeting() + ', ' + (emp.full_name || '').split(' ')[0];
      var sbCo = document.getElementById('ess-sb-company');
      if (sbCo && emp.company_name) sbCo.textContent = emp.company_name;
      var roleEl = document.getElementById('dash-role');
      if (roleEl) roleEl.textContent = (emp.designation || '') + (emp.designation && emp.department ? ' · ' : '') + (emp.department || '');
      var companyEl = document.getElementById('dash-hero-company');
      if (companyEl) companyEl.textContent = emp.company_name || '—';
      var todayEl = document.getElementById('dash-hero-today');
      if (todayEl) todayEl.textContent = new Date().toLocaleDateString('en-US', {weekday: 'short', month: 'short', day: 'numeric', year: 'numeric'});
      var hoursEl = document.getElementById('dash-hero-hours');
      if (hoursEl) hoursEl.textContent = (emp.shift_start && emp.shift_end) ? (_fmt12h(emp.shift_start) + ' – ' + _fmt12h(emp.shift_end)) : '—';
    } catch(e) { console.error(e); }
  }

  function _fmt12h(hhmm) {
    var parts = (hhmm || '').split(':');
    var h = parseInt(parts[0], 10);
    var m = parts[1] || '00';
    if (isNaN(h)) return hhmm || '—';
    var suffix = h >= 12 ? 'PM' : 'AM';
    var h12 = h % 12 || 12;
    return h12 + ':' + m + ' ' + suffix;
  }

  function _essGreeting() {
    var h = new Date().getHours();
    if (h < 12) return 'Good morning';
    if (h < 17) return 'Good afternoon';
    return 'Good evening';
  }

  function kv(label, value) {
    return '<div class="kv"><div class="kv-label">' + label + '</div><div class="kv-value">' + value + '</div></div>';
  }

  // Raw punches -> one row per day (first IN, last OUT, worked hours).
  function _punchMins(t) { t = String(t || ''); return parseInt(t.substr(11, 2), 10) * 60 + parseInt(t.substr(14, 2), 10); }
  function _fmtDur(m) { return Math.floor(m / 60) + 'h ' + String(m % 60).padStart(2, '0') + 'm'; }
  function _fmtClock(m) { return String(Math.floor(m / 60)).padStart(2, '0') + ':' + String(Math.round(m % 60)).padStart(2, '0'); }
  function essGroupAttendance(rows) {
    var byDate = {};
    rows.forEach(function(p) { (byDate[p.punch_date] = byDate[p.punch_date] || []).push(p); });
    return Object.keys(byDate).sort().reverse().map(function(d) {
      var ps = byDate[d].slice().sort(function(a, b) { return String(a.punch_time).localeCompare(String(b.punch_time)); });
      var ins = ps.filter(function(p) { return p.direction === 'in'; });
      var outs = ps.filter(function(p) { return p.direction !== 'in'; });
      var first = ins.length ? ins[0] : ps[0];
      var last = outs.length ? outs[outs.length - 1] : null;
      if (last && String(last.punch_time) <= String(first.punch_time)) last = null;
      var sources = ps.map(function(p) { return p.source; }).filter(function(v, i, a) { return v && a.indexOf(v) === i; });
      return {date: d, first: first, last: last, mins: last ? _punchMins(last.punch_time) - _punchMins(first.punch_time) : null, count: ps.length, source: sources.join(', ') || '—'};
    });
  }

  function renderAttendance() {
    var rows = _essData.attendance || [];
    var body = document.getElementById('att-body');
    var sumEl = document.getElementById('att-summary');
    var days = essGroupAttendance(rows);
    var todayStr = _todayISO();
    var month = todayStr.slice(0, 7);
    var monthDays = days.filter(function(d) { return d.date.indexOf(month) === 0; });
    var withHours = monthDays.filter(function(d) { return d.mins != null; });
    var avgIn = monthDays.length ? Math.round(monthDays.reduce(function(a, d) { return a + _punchMins(d.first.punch_time); }, 0) / monthDays.length) : null;
    var totalMins = withHours.reduce(function(a, d) { return a + d.mins; }, 0);
    var avgHrs = withHours.length ? Math.round(totalMins / withHours.length) : null;
    var openDays = monthDays.length - withHours.length;
    var lastPunch = rows[0];   // API returns newest first
    if (sumEl) {
      var stat = function(label, val, sub, cls) { return '<div class="att-stat' + (cls ? ' ' + cls : '') + '"><div class="att-stat-label">' + label + '</div><div class="att-stat-val">' + val + '</div><div class="att-stat-sub">' + sub + '</div></div>'; };
      var monthName = new Date(month + '-01T00:00:00').toLocaleDateString('en-US', {month: 'long'});
      sumEl.innerHTML =
        stat('Total Hours', withHours.length ? _fmtDur(totalMins) : '—', monthName + (openDays ? ' · ' + openDays + ' day' + (openDays === 1 ? '' : 's') + ' without check-out not counted' : ''), 'is-total') +
        stat('Days Present', monthDays.length, 'This month') +
        stat('Avg. Check-in', avgIn == null ? '—' : _fmtClock(avgIn), 'This month') +
        stat('Avg. Hours / Day', avgHrs == null ? '—' : _fmtDur(avgHrs), withHours.length ? 'Over ' + withHours.length + ' day' + (withHours.length === 1 ? '' : 's') + ' with a check-out' : 'No completed days yet') +
        stat('Last Punch', lastPunch ? String(lastPunch.punch_time).substring(11, 16) + ' ' + (lastPunch.direction === 'in' ? 'IN' : 'OUT') : '—', lastPunch ? fmtShortDate(lastPunch.punch_date) : 'No punches yet');
    }
    renderAttToday(days, todayStr);
    renderAttCalendar(days);
    if (!body) return;
    if (!days.length) { body.innerHTML = '<tr><td colspan="6">' + emptyState('clock', 'No attendance records yet', 'Your check-ins from the biometric device or GPS will show up here, one row per day.') + '</td></tr>'; return; }
    body.innerHTML = days.map(function(d) {
      var out;
      if (d.last) out = String(d.last.punch_time).substring(11, 16);
      else if (d.date === todayStr) out = '<span class="att-pill live">On shift</span>';
      else {
        var fixPending = (_essRequests || []).some(function(q) { return q.kind === 'correction' && q.status === 'pending' && q.date === d.date; });
        out = fixPending ? '<span class="att-pill pend">Correction pending</span>'
          : '<span class="att-pill miss">No check-out</span> <button class="link-btn" style="margin:0 0 0 6px" data-date="' + escHtml(d.date) + '" data-in="' + escHtml(String(d.first.punch_time).substring(11, 16)) + '" onclick="essOpenRequestModal(\'correction\',{date:this.dataset.date,checkin:this.dataset.in})">Request fix</button>';
      }
      return '<tr><td>' + fmtShortDate(d.date) + '</td><td><span class="dir-in">' + String(d.first.punch_time).substring(11, 16) + '</span></td><td>' + out +
        '</td><td>' + (d.mins != null ? _fmtDur(d.mins) : '—') + '</td><td>' + d.count + '</td><td>' + escHtml(d.source) + '</td></tr>';
    }).join('');
  }

  function _attCorrections() {
    var fixes = (_essRequests || []).filter(function(q) { return q.kind === 'correction'; }).slice(0, 4);
    return '<div class="att-corr"><div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><div class="card-title" style="font-size:12.5px;margin:0">Correction requests</div>' +
      '<button class="link-btn" style="margin:0" onclick="essOpenRequestModal(\'correction\')">+ New</button></div>' +
      (fixes.length ? fixes.map(function(q) {
        return '<div class="att-corr-row"><span>' + _dateOrDash(q.date) + ' · ' + escHtml(q.checkin || '—') + ' → ' + escHtml(q.checkout || '—') + '</span><span class="st-badge st-' + _reqStatusClass(q.status) + '">' + escHtml(q.status) + '</span></div>';
      }).join('') : '<div class="hint" style="margin:6px 0 0">Missing a punch? Send HR the times that should have been recorded.</div>') + '</div>';
  }

  function renderAttToday(days, todayStr) {
    _renderAttTodayMain(days, todayStr);
    var el = document.getElementById('att-today');
    if (el) el.insertAdjacentHTML('beforeend', _attCorrections());
  }

  function _renderAttTodayMain(days, todayStr) {
    var el = document.getElementById('att-today');
    if (!el) return;
    var d = days.filter(function(x) { return x.date === todayStr; })[0];
    if (!d) {
      el.innerHTML = '<div class="att-today-status">Not checked in yet</div><div class="att-today-sub">No punches recorded today. Use GPS check-in if you are working off-site.</div>' +
        '<div class="att-today-actions"><button class="btn-login" style="width:auto;padding:9px 18px;font-size:13px" onclick="essGoTab(\'gps\')">GPS Check-In</button></div>';
      return;
    }
    var inT = String(d.first.punch_time).substring(11, 16);
    if (d.last) {
      el.innerHTML = '<div class="att-today-status">Checked out ' + String(d.last.punch_time).substring(11, 16) + '</div><div class="att-today-sub">In at ' + inT + ' · worked <b>' + _fmtDur(d.mins) + '</b> · ' + d.count + ' punches via ' + escHtml(d.source) + '</div>';
    } else {
      el.innerHTML = '<div class="att-today-status">Checked in ' + inT + '</div><div class="att-today-sub"><span class="att-pill live">On shift</span> &nbsp;' + d.count + ' punch' + (d.count === 1 ? '' : 'es') + ' today via ' + escHtml(d.source) + '</div>';
    }
  }

  // Month calendar in the HRMS Attendance style: Present / No check-out / Leave / Holiday.
  // Working-day "absent" is deliberately not inferred -- weekend days and per-company
  // rules aren't known here, and a wrong "Absent" would be worse than none.
  var _attCalMonth = null;
  var _attCalRota = {}; // 'YYYY-MM' -> rota rows for that month (null while loading)
  // Same rule as the server (app/rota_days.py): Off/Leave/Holiday marks, or a day without
  // a rota entry in a Mon-Sun week that has entries.
  function _rotaOffDays(rows) {
    var byDate = {}, weeks = {}, out = {};
    rows.forEach(function(a) {
      if (!a.date) return;
      byDate[a.date] = a;
      weeks[_isoDateLocal(_mondayOf(new Date(a.date + 'T00:00:00')))] = 1;
    });
    return {byDate: byDate, weeks: weeks, kindOf: function(iso) {
      var a = byDate[iso];
      if (a) { var k = _rotaKind(a); return k === 'shift' || k === 'ot' || k === 'training' ? '' : k; }
      return weeks[_isoDateLocal(_mondayOf(new Date(iso + 'T00:00:00')))] ? 'off' : '';
    }};
  }
  function _loadAttCalRota(month) {
    if (month in _attCalRota) return;
    _attCalRota[month] = null;
    fetch(apiBase() + '/ess/rota?month=' + encodeURIComponent(month), {headers: essHeaders()})
      .then(function(r) { return r.ok ? r.json() : []; })
      .then(function(rows) { _attCalRota[month] = Array.isArray(rows) ? rows : []; renderAttCalendar(); })
      .catch(function() { _attCalRota[month] = []; });
  }
  function essShiftAttMonth(delta) {
    var d = new Date((_attCalMonth || _todayISO().slice(0, 7)) + '-01T00:00:00');
    d.setMonth(d.getMonth() + delta);
    _attCalMonth = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
    renderAttCalendar();
  }
  function renderAttCalendar(days) {
    var grid = document.getElementById('att-cal-grid');
    if (!grid) return;
    days = days || essGroupAttendance(_essData.attendance || []);
    var month = _attCalMonth || (_attCalMonth = _todayISO().slice(0, 7));
    var title = document.getElementById('acal-title');
    if (title) title.textContent = new Date(month + '-01T00:00:00').toLocaleDateString('en-US', {month: 'long', year: 'numeric'}) + ' — Attendance';
    var totalEl = document.getElementById('acal-total');
    if (totalEl) {
      var inMonth = days.filter(function(d) { return d.date.indexOf(month) === 0 && d.mins != null; });
      var oldest = days.length ? days[days.length - 1].date.slice(0, 7) : null;
      totalEl.innerHTML = inMonth.length
        ? 'Worked <b>' + _fmtDur(inMonth.reduce(function(a, d) { return a + d.mins; }, 0)) + '</b> over ' + inMonth.length + ' day' + (inMonth.length === 1 ? '' : 's')
        : (oldest && month < oldest ? 'Hours for this month aren’t available here' : 'No completed days this month');
    }
    var byDate = {};
    days.forEach(function(d) { byDate[d.date] = d; });
    var leaveDates = {};
    (_essData.leave || []).filter(function(l) { return l.status === 'approved'; }).forEach(function(l) {
      var c = new Date(l.start_date + 'T00:00:00'), end = new Date(l.end_date + 'T00:00:00'), guard = 0;
      while (c <= end && guard++ < 366) { leaveDates[c.getFullYear() + '-' + String(c.getMonth() + 1).padStart(2, '0') + '-' + String(c.getDate()).padStart(2, '0')] = 1; c.setDate(c.getDate() + 1); }
    });
    var holidays = {};
    (_essHolidaysCache || []).forEach(function(h) { holidays[h.date] = h.name; });
    _loadAttCalRota(month);
    var rota = _rotaOffDays(_attCalRota[month] || []);
    var y = Number(month.slice(0, 4)), m = Number(month.slice(5, 7));
    var firstDow = (new Date(y, m - 1, 1).getDay() + 6) % 7, dim = new Date(y, m, 0).getDate(), todayStr = _todayISO();
    var html = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(function(w) { return '<div class="acal-wd">' + w + '</div>'; }).join('');
    for (var i = 0; i < firstDow; i++) html += '<div class="acal-cell empty"></div>';
    for (var day = 1; day <= dim; day++) {
      var ds = month + '-' + String(day).padStart(2, '0'), rec = byDate[ds], cls = '', sub = '', tip = '';
      if (rec) { cls = (rec.last || ds === todayStr) ? 'acal-present' : 'acal-incomplete'; sub = String(rec.first.punch_time).substring(11, 16); tip = rec.last ? 'Present' : (ds === todayStr ? 'On shift' : 'No check-out'); }
      else if (leaveDates[ds]) { cls = 'acal-leave'; sub = 'Leave'; tip = 'Approved leave'; }
      else if (holidays[ds]) { cls = 'acal-holiday'; sub = 'Holiday'; tip = holidays[ds]; }
      else {
        var rk = rota.kindOf(ds);
        if (rk === 'off') { cls = 'acal-off'; sub = 'Off'; tip = 'Day off on the rota'; }
        else if (rk === 'leave') { cls = 'acal-leave'; sub = 'Leave'; tip = 'Leave on the rota'; }
        else if (rk === 'holiday') { cls = 'acal-holiday'; sub = 'Holiday'; tip = 'Holiday on the rota'; }
      }
      html += '<div class="acal-cell ' + cls + (ds === todayStr ? ' today' : '') + '" title="' + escHtml(tip) + '">' + day + (sub ? '<small>' + escHtml(sub) + '</small>' : '') + '</div>';
    }
    grid.innerHTML = html;
  }

  async function loadAttendance() {
    try {
      var r = await fetch(apiBase() + '/ess/attendance', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      _essData.attendance = await r.json();
      renderAttendance();
      updateDashboard();
    } catch(e) { document.getElementById('att-body').innerHTML = '<tr><td colspan="6" class="empty">Failed to load.</td></tr>'; }
  }

  function _psPeriodLabel(period) {
    if (/^\d{4}-\d{2}$/.test(period || '')) return new Date(period + '-01T00:00:00').toLocaleDateString('en-US', {month: 'long', year: 'numeric'});
    return period || '—';
  }
  function _psStatusClass(status) {
    var s = String(status || '').toLowerCase();
    if (['paid', 'approved', 'finalized', 'finalised', 'completed', 'posted'].indexOf(s) > -1) return 'approved';
    if (s === 'draft' || s === 'pending') return 'pending';
    return 'todo';
  }

  function renderPayslips() {
    var items = _essData.payslips || [];
    var el = document.getElementById('payslip-content');
    if (!el) return;
    el.classList.remove('loading');
    if (!items.length) { el.innerHTML = emptyState('wallet', 'No payslips yet', 'Your payslips appear here once payroll has been run for you.'); return; }
    el.innerHTML = '<div class="payslip-grid">' + items.map(function(p, i) {
      var row = function(label, val, cls) { return '<div class="payslip-row' + (cls ? ' ' + cls : '') + '"><span>' + label + '</span><span>' + (cls === 'neg' && Number(val) ? '− ' : '') + essMoney(val) + '</span></div>'; };
      return '<div class="payslip-card">' +
        '<div class="ps-head"><div class="payslip-period">' + escHtml(_psPeriodLabel(p.period)) + '</div><span class="st-badge st-' + _psStatusClass(p.run_status) + '">' + escHtml(p.run_status || '') + '</span></div>' +
        '<div class="ps-net"><div class="ps-net-label">Net pay</div><div class="ps-net-val">' + essMoney(p.net_pay) + '</div></div>' +
        row('Basic', p.basic) + row('Allowances', p.allowances) + row('Overtime', p.overtime) + row('Deductions', p.deductions, 'neg') +
        '<button class="btn-ghost ps-print" onclick="essPrintPayslip(' + i + ')">Print / Save PDF</button>' +
        '</div>';
    }).join('') + '</div>';
  }

  // Opens a clean, printable single-payslip page (browser "Save as PDF" works
  // from its print dialog) -- no server-side PDF endpoint needed.
  function essPrintPayslip(i) {
    var p = (_essData.payslips || [])[i];
    if (!p) return;
    var me = _essMe || {};
    var w = window.open('', '_blank', 'width=760,height=900');
    if (!w) { essToast('Please allow pop-ups for this site to print your payslip.', 'err'); return; }
    var line = function(label, val, bold) { return '<tr' + (bold ? ' class="b"' : '') + '><td>' + label + '</td><td class="n">' + essMoney(val) + '</td></tr>'; };
    w.document.write('<!doctype html><html><head><meta charset="utf-8"><title>Payslip ' + escHtml(_psPeriodLabel(p.period)) + '</title>' +
      '<style>body{font-family:Arial,Helvetica,sans-serif;color:#0f172a;margin:40px}h1{font-size:20px;margin:0}h2{font-size:13px;color:#475569;font-weight:400;margin:4px 0 24px}' +
      '.meta{display:grid;grid-template-columns:1fr 1fr;gap:6px 24px;font-size:13px;margin-bottom:24px}.meta b{color:#64748b;font-weight:600;font-size:11px;text-transform:uppercase;display:block}' +
      'table{width:100%;border-collapse:collapse;font-size:14px}td{padding:9px 4px;border-bottom:1px solid #e2e8f0}td.n{text-align:right}tr.b td{font-weight:700;font-size:16px;border-top:2px solid #0f172a;border-bottom:none}' +
      '.foot{margin-top:32px;font-size:11px;color:#94a3b8}</style></head><body>' +
      '<h1>' + escHtml(me.company_name || 'Payslip') + '</h1><h2>Payslip — ' + escHtml(_psPeriodLabel(p.period)) + ' (' + escHtml(p.run_status || '') + ')</h2>' +
      '<div class="meta"><div><b>Employee</b>' + escHtml(me.full_name || '') + '</div><div><b>Employee ID</b>' + escHtml(me.employee_no || '') + '</div>' +
      '<div><b>Department</b>' + escHtml(me.department || '') + '</div><div><b>Designation</b>' + escHtml(me.designation || '') + '</div></div>' +
      '<table>' + line('Basic', p.basic) + line('Allowances', p.allowances) + line('Overtime', p.overtime) +
      '<tr><td>Deductions</td><td class="n">− ' + essMoney(p.deductions) + '</td></tr>' + line('Net Pay', p.net_pay, true) + '</table>' +
      '<div class="foot">Generated from the Employee Self-Service portal on ' + escHtml(new Date().toLocaleDateString('en-US', {day: 'numeric', month: 'long', year: 'numeric'})) + '.</div>' +
      '</body></html>');
    w.document.close();
    w.focus();
    setTimeout(function() { w.print(); }, 250);
  }

  async function loadPayslips() {
    try {
      var r = await fetch(apiBase() + '/ess/payslips', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      _essData.payslips = await r.json();
      renderPayslips();
      updateDashboard();
    } catch(e) { var pEl = document.getElementById('payslip-content'); pEl.classList.remove('loading'); pEl.innerHTML = '<div class="empty">Failed to load.</div>'; }
  }

  // ── Leave ──────────────────────────────────────────────────────────────
  async function loadLeave() {
    try {
      var r = await fetch(apiBase() + '/ess/leave', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      var rows = await r.json();
      _essData.leave = rows;
      if (_rotaWaitingForLeave) { _rotaWaitingForLeave = false; loadRota(); }
      if (typeof renderAttCalendar === 'function') renderAttCalendar();
      var el = document.getElementById('leave-content');
      el.classList.remove('loading');
      if (!rows.length) { el.innerHTML = emptyState('calendar', 'No leave requests yet', 'Use “Request Leave” or click a day on the calendar — your requests and their status show up here.'); }
      else {
        el.innerHTML = rows.map(function(l) {
          var st = (l.status || 'pending').toLowerCase();
          return '<div class="leave-card">' +
            '<div style="min-width:0">' +
              '<div class="leave-type">' + escHtml(l.leave_type) + '</div>' +
              '<div class="leave-dates">' + escHtml(l.start_date) + ' → ' + escHtml(l.end_date) + ' (' + escHtml(l.days) + ' day' + (l.days === 1 ? '' : 's') + ')</div>' +
              (l.reason ? '<div class="leave-reason">' + escHtml(l.reason) + '</div>' : '') +
              (st === 'cancelled' && l.cancelled_by ? '<div class="leave-reason">Cancelled by ' + escHtml(l.cancelled_by) + (l.cancel_reason ? ' — ' + escHtml(l.cancel_reason) : '') + '</div>' : '') +
              (l.can_cancel ? '<button class="link-btn danger" data-id="' + escHtml(l.id) + '" onclick="essCancelLeave(this.dataset.id)">' + (st === 'approved' ? 'Cancel leave' : 'Cancel request') + '</button>' : '') +
            '</div>' +
            '<span class="st-badge st-' + escHtml(st) + '">' + escHtml(l.status || 'pending') + '</span>' +
          '</div>';
        }).join('');
      }
      updateDashboard();
    } catch(e) { var lEl = document.getElementById('leave-content'); lEl.classList.remove('loading'); lEl.innerHTML = '<div class="empty">Failed to load.</div>'; }
  }

  var LEAVE_TYPE_ICONS = {
    'Annual Leave': '🌴', 'Sick Leave': '❤️', 'Emergency Leave': '🛡️',
    'Maternity Leave': '🤱', 'Paternity Leave': '👨', 'Hajj Leave': '🕋',
    'Casual Leave': '🎈', 'Unpaid Leave': '📄', 'Work From Home': '🏠', 'Lieu Days': '🔁'
  };

  // Dashboard's Leave Balance widget -- entitlement/used/remaining per
  // type, from GET /ess/leave-balance (this employee's own numbers; the
  // admin-only company-wide equivalent is HRMS's Leave Balance Summary).
  async function loadLeaveBalance() {
    var targets = ['dash-leave-balance', 'leave-balance-grid'].map(function(id) { return document.getElementById(id); }).filter(Boolean);
    if (!targets.length) return;
    var setAll = function(html) { targets.forEach(function(t) { t.innerHTML = html; }); };
    try {
      var r = await fetch(apiBase() + '/ess/leave-balance', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      if (!r.ok) throw new Error('bad response');
      var data = await r.json();
      _essLeaveBal = data.by_type || {};
      essUpdateLeavePreview();
      var types = Object.keys(data.by_type || {});
      var annual = _essLeaveBal['Annual Leave'];
      var av = document.getElementById('dash-annual-left'), as = document.getElementById('dash-annual-sub');
      if (av) av.textContent = annual ? annual.remaining + (annual.remaining === 1 ? ' day' : ' days') : '—';
      if (as) as.textContent = annual ? 'of ' + annual.entitlement + ' entitlement' : 'Your balance';
      if (!types.length) { setAll('<div class="empty">No leave types configured yet.</div>'); return; }
      setAll(types.map(function(type) {
        var b = data.by_type[type];
        var pct = b.entitlement > 0 ? Math.min(100, Math.round((b.used / b.entitlement) * 100)) : 0;
        var low = b.entitlement > 0 && b.remaining <= Math.max(1, Math.round(b.entitlement * 0.15));
        return '<div class="lb-item">' +
          '<div class="lb-item-icon">' + (LEAVE_TYPE_ICONS[type] || '📅') + '</div>' +
          '<div class="lb-item-type">' + escHtml(type) + '</div>' +
          '<div class="lb-item-days">' + b.remaining + ' <span>/ ' + b.entitlement + ' days left</span></div>' +
          '<div class="lb-bar"><div class="lb-bar-fill' + (low ? ' low' : '') + '" style="width:' + pct + '%"></div></div>' +
        '</div>';
      }).join(''));
    } catch (e) { setAll('<div class="empty">Failed to load.</div>'); }
  }

  // Dashboard's Latest Announcements card -- read-only here; posting one
  // is an HR Settings action (HRMS's Manager Portal), see GET /ess/
  // announcements' docstring for the write path.
  var _essAnnouncements = [];
  async function loadAnnouncements() {
    var el = document.getElementById('dash-announcements');
    try {
      var r = await fetch(apiBase() + '/ess/announcements', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      if (!r.ok) throw new Error('bad response');
      var rows = await r.json();
      _essAnnouncements = rows;
      if (el) {
        if (!rows.length) el.innerHTML = '<div class="empty" style="padding:12px 0">No announcements yet.</div>';
        else el.innerHTML = rows.slice(0, 5).map(function(a) {
          return '<div class="dash-announce-row">' +
            '<div class="dash-sched-icon"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M2 6v4h2l6 3V3L4 6H2z"/><path d="M11 5.5a3 3 0 010 5"/></svg></div>' +
            '<div style="flex:1;min-width:0">' +
              '<div class="dash-sched-title">' + escHtml(a.title) + '</div>' +
              (a.message ? '<div class="dash-announce-msg">' + escHtml(a.message) + '</div>' : '') +
              (a.date ? '<div class="dash-sched-time">' + escHtml(a.date) + '</div>' : '') +
            '</div>' +
          '</div>';
        }).join('');
      }
      refreshNotifications();
    } catch (e) { if (el) el.innerHTML = '<div class="empty">Failed to load.</div>'; }
  }

  // Topbar notification bell -- a real-data digest of what's actually
  // waiting on the employee (pending leave, open tasks) plus the most
  // recent announcements, not a fabricated count.
  function refreshNotifications() {
    var items = [];
    essUnseenDecisions().forEach(function(q) {
      items.push({title: 'Your ' + (REQ_KIND[q.kind] || {label: 'request'}).label.toLowerCase() + ' request was ' + q.status, sub: _reqTitle(q), tab: 'requests'});
    });
    (_essDocs || []).filter(function(d) { return d.state === 'expired' || d.state === 'critical'; }).forEach(function(d) {
      items.push({title: d.label + (d.state === 'expired' ? ' has expired' : ' expires in ' + d.days_left + ' day' + (d.days_left === 1 ? '' : 's')), sub: 'Contact HR to renew it', tab: 'profile'});
    });
    var pendingLeave = (_essData.leave || []).filter(function(l) { return l.status === 'pending'; });
    if (pendingLeave.length) items.push({title: pendingLeave.length + ' pending leave request' + (pendingLeave.length === 1 ? '' : 's'), sub: 'Awaiting approval', tab: 'leave'});
    var openTasks = (_essData.tasks || []).filter(function(t) { return t.status !== 'done'; });
    if (openTasks.length) items.push({title: openTasks.length + ' open task' + (openTasks.length === 1 ? '' : 's'), sub: 'Assigned to you', tab: 'tasks'});
    _essAnnouncements.slice(0, 3).forEach(function(a) {
      items.push({title: a.title, sub: a.date || 'Announcement', tab: null});
    });
    var dot = document.getElementById('ess-notif-dot');
    if (dot) dot.style.display = items.length ? '' : 'none';
    var list = document.getElementById('ess-notif-list');
    if (!list) return;
    if (!items.length) { list.innerHTML = '<div class="empty" style="padding:16px 0">Nothing new.</div>'; return; }
    list.innerHTML = items.map(function(it) {
      return '<div class="ess-notif-row"' + (it.tab ? ' onclick="essNotifGo(\'' + it.tab + '\')"' : '') + '>' +
        '<div><div class="ess-notif-row-title">' + escHtml(it.title) + '</div><div class="ess-notif-row-sub">' + escHtml(it.sub) + '</div></div>' +
      '</div>';
    }).join('');
  }
  function essNotifGo(tabName) {
    var tabEl = document.querySelector('.tab[onclick*=' + tabName + ']');
    if (tabEl) tabEl.click();
    essToggleNotifPanel(false);
  }
  function essToggleNotifPanel(force) {
    var panel = document.getElementById('ess-notif-panel');
    if (!panel) return;
    var open = force !== undefined ? force : !panel.classList.contains('on');
    panel.classList.toggle('on', open);
  }
  document.addEventListener('click', function(e) {
    var wrap = document.querySelector('.ess-notif-wrap');
    if (wrap && !wrap.contains(e.target)) essToggleNotifPanel(false);
  });

  function essOpenLeaveModal(prefillDate) {
    document.getElementById('leave-form-err').style.display = 'none';
    document.getElementById('leave-type').value = 'Annual Leave';
    document.getElementById('leave-start').value = prefillDate || '';
    document.getElementById('leave-end').value = prefillDate || '';
    document.getElementById('leave-reason').value = '';
    essUpdateLeavePreview();
    document.getElementById('leave-modal-overlay').classList.add('on');
  }
  function essCloseLeaveModal() {
    document.getElementById('leave-modal-overlay').classList.remove('on');
  }
  async function essSubmitLeave() {
    var btn = document.getElementById('leave-submit-btn');
    var err = document.getElementById('leave-form-err');
    err.style.display = 'none';
    var type = document.getElementById('leave-type').value;
    var start = document.getElementById('leave-start').value;
    var end = document.getElementById('leave-end').value;
    var reason = document.getElementById('leave-reason').value;
    if (!start || !end) { err.textContent = 'Please choose both a start and end date.'; err.style.display = 'block'; return; }
    btn.disabled = true; btn.textContent = 'Submitting…';
    try {
      var r = await fetch(apiBase() + '/ess/leave', {
        method: 'POST', headers: essHeaders(),
        body: JSON.stringify({leave_type: type, start_date: start, end_date: end, reason: reason || null})
      });
      if (r.status === 401) { essLogout(); return; }
      var d = await r.json();
      if (r.ok) { essCloseLeaveModal(); essToast('Leave request sent to HR'); loadLeave(); essRefreshRequests(); loadLeaveBalance(); }
      else { err.textContent = d.detail || 'Could not submit leave request.'; err.style.display = 'block'; }
    } catch(e) {
      err.textContent = 'Cannot reach server. Please try again.'; err.style.display = 'block';
    }
    btn.disabled = false; btn.textContent = 'Submit Request';
  }

  // ── Rota ───────────────────────────────────────────────────────────────
  // Type-specific styling/labels so an Off day, approved Leave, a Public
  // Holiday, Overtime, and Training read as visibly different row kinds
  // instead of identically-styled "Shift" rows with different text — and so
  // non-working days (Off/Leave/Holiday) don't show a meaningless blank
  // time range where a real shift's start–end would normally go.
  function _rotaTypeMeta(rawType) {
    var t = (rawType || 'shift').toLowerCase();
    var map = {
      shift:    {cls: '',                  fallback: 'Scheduled shift'},
      off:      {cls: 'rota-type-off',     fallback: 'Day off'},
      leave:    {cls: 'rota-type-leave',   fallback: 'On approved leave'},
      holiday:  {cls: 'rota-type-holiday', fallback: 'Public holiday'},
      ot:       {cls: 'rota-type-ot',      fallback: 'Overtime shift'},
      overtime: {cls: 'rota-type-ot',      fallback: 'Overtime shift'},
      training: {cls: 'rota-type-training',fallback: 'Training session'},
    };
    return map[t] || {cls: '', fallback: 'Scheduled'};
  }
  var _rotaWaitingForLeave = false;
  function _rotaKind(a) {
    var raw = String(a.mark && a.mark !== 'Shift' ? a.mark : (a.type || a.code || 'shift')).toLowerCase();
    if (raw === 'off' || raw === 'leave' || raw === 'holiday' || raw === 'training') return raw;
    if (raw === 'ot' || raw === 'overtime') return 'ot';
    if (raw === 'l') return 'leave';
    if (raw === 'ph') return 'holiday';
    return 'shift';
  }
  // Days between the first and last scheduled day with no rota entry are days off
  // (or leave, when an approved leave covers them).
  function _fillRotaDayOffs(rows) {
    var byDate = {};
    rows.forEach(function(a) { if (a.date) byDate[a.date] = true; });
    var dates = Object.keys(byDate).sort();
    if (dates.length < 2) return rows;
    if (!_essData.leave) _rotaWaitingForLeave = true;
    var leaves = (_essData.leave || []).filter(function(l) { return /approved/i.test(l.status || ''); });
    var out = rows.slice();
    var d = new Date(dates[0] + 'T00:00:00'), last = new Date(dates[dates.length - 1] + 'T00:00:00');
    for (var guard = 0; d <= last && guard < 400; guard++, d.setDate(d.getDate() + 1)) {
      var iso = _isoDateLocal(d);
      if (byDate[iso]) continue;
      var lv = leaves.find(function(l) { return l.start_date <= iso && iso <= l.end_date; });
      out.push(lv ? {date: iso, type: 'Leave', _gap: true, _leaveType: lv.leave_type} : {date: iso, type: 'Off', _gap: true});
    }
    return out.sort(function(a, b) { return (a.date || '') < (b.date || '') ? -1 : 1; });
  }
  function _mondayOf(d) {
    var day = d.getDay();
    var m = new Date(d);
    m.setDate(d.getDate() + (day === 0 ? -6 : 1) - day);
    m.setHours(0, 0, 0, 0);
    return m;
  }
  // Groups the flat 30-day list under "This Week" / "Next Week" / a dated
  // range for weeks further out — a single unbroken 30-row column made it
  // hard to tell at a glance which shifts were coming up soon vs. weeks away.
  function _rotaWeekLabel(shiftDate, thisMonday) {
    var mon = _mondayOf(shiftDate);
    var diffWeeks = Math.round((mon - thisMonday) / (7 * 86400000));
    if (diffWeeks <= 0) return 'This Week';
    if (diffWeeks === 1) return 'Next Week';
    var end = new Date(mon); end.setDate(mon.getDate() + 6);
    var fmt = function(d) { return d.toLocaleDateString('en-US', {month: 'short', day: 'numeric'}); };
    return fmt(mon) + ' – ' + fmt(end);
  }
  // ── My Rota row look: icon, label and colour for a day ────────────────────
  var ROTA_ICONS = {
    sun: '<circle cx="12" cy="12" r="4.2" fill="currentColor" stroke="none"/><path d="M12 2.5v2.5M12 19v2.5M2.5 12H5M19 12h2.5M5.3 5.3l1.8 1.8M16.9 16.9l1.8 1.8M5.3 18.7l1.8-1.8M16.9 7.1l1.8-1.8"/>',
    sunset: '<path d="M7 15a5 5 0 0 1 10 0" fill="currentColor" stroke="none"/><path d="M3 18.5h18M12 4v3M4.9 8.9l1.8 1.8M19.1 8.9l-1.8 1.8M2 15h2.5M19.5 15H22"/>',
    moon: '<path d="M20 14.5A8.5 8.5 0 1 1 9.5 4a7 7 0 0 0 10.5 10.5z" fill="currentColor" stroke="none"/>',
    plane: '<path d="M10.5 13.5 3 11l1.5-1.5 7.5 1.5 4.5-4.5a2 2 0 0 1 3 3L15 14l1.5 7.5L15 23l-2.5-7.5L9 19v2.5L7.5 23 6 18l-5-1.5L2.5 15H5z" fill="currentColor" stroke="none" transform="scale(.9) translate(1 -1)"/>',
    star: '<path d="m12 3 2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z" fill="currentColor" stroke="none"/>',
    bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z" fill="currentColor" stroke="none"/>',
    book: '<path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2zM4 21a2 2 0 0 1 2-2h13v2" />',
    alarm: '<circle cx="12" cy="13" r="7.5"/><path d="M12 9.5V13l2.5 1.5M4.5 4.5 2.5 6.5M19.5 4.5l2 2"/>',
    pin: '<path d="M12 21.5s-7-6-7-11.5a7 7 0 0 1 14 0c0 5.5-7 11.5-7 11.5z" fill="currentColor" stroke="none"/><circle cx="12" cy="10" r="2.6" fill="#fff" stroke="none"/>',
    chevron: '<path d="m9 6 6 6-6 6"/>'
  };
  function _rotaIcon(name, size) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="' + size + '" height="' + size + '" aria-hidden="true">' + (ROTA_ICONS[name] || '') + '</svg>';
  }
  // Morning / Afternoon / Night from the shift's own name, else from its start time.
  function _rotaLook(a, kind) {
    if (kind === 'off') return {icon: 'moon', tone: 'off', pill: 'Day Off', title: 'Day off', sub: a._gap ? 'No shift scheduled' : ''};
    if (kind === 'leave') return {icon: 'plane', tone: 'leave', pill: 'Leave', title: 'On leave', sub: a._leaveType || 'Approved leave'};
    if (kind === 'holiday') return {icon: 'star', tone: 'holiday', pill: 'Holiday', title: 'Public holiday', sub: a.holiday_name || ''};
    if (kind === 'ot') return {icon: 'bolt', tone: 'ot', pill: 'Overtime'};
    if (kind === 'training') return {icon: 'book', tone: 'training', pill: 'Training'};
    var name = String(a.type || '').toLowerCase();
    var hour = parseInt(String(a.start || '').slice(0, 2), 10);
    if (/night/.test(name) || (!/morning|evening|afternoon/.test(name) && !isNaN(hour) && (hour >= 18 || hour < 4))) return {icon: 'moon', tone: 'night', pill: 'Night'};
    if (/evening|afternoon/.test(name) || (!/morning/.test(name) && !isNaN(hour) && hour >= 12)) return {icon: 'sunset', tone: 'evening', pill: /afternoon/.test(name) ? 'Afternoon' : 'Evening'};
    return {icon: 'sun', tone: 'morning', pill: 'Morning'};
  }
  function _rotaShiftHours(a) {
    var p = function(s) { var m = /^(\d{1,2}):(\d{2})/.exec(s || ''); return m ? (+m[1]) * 60 + (+m[2]) : null; };
    var s = p(a.start), e = p(a.end);
    if (s === null || e === null) return '';
    var mins = (e - s + 1440) % 1440 - (+a.break_minutes || 0);
    return mins > 0 ? (Math.round(mins / 6) / 10) + ' hrs' : '';
  }
  function toggleRotaRow(btn) {
    var open = btn.getAttribute('aria-expanded') !== 'true';
    btn.setAttribute('aria-expanded', String(open));
    var panel = document.getElementById(btn.getAttribute('aria-controls'));
    if (panel) panel.hidden = !open;
  }
  async function loadRota() {
    var month = document.getElementById('rota-month-picker') ? document.getElementById('rota-month-picker').value : '';
    var titleEl = document.getElementById('rota-card-title');
    var resetBtn = document.getElementById('rota-reset-btn');
    var rangeEl = document.getElementById('rota-range-label');
    var monthLabel = month ? new Date(month + '-01T00:00:00').toLocaleDateString('en-US', {month: 'long', year: 'numeric'}) : '';
    if (titleEl) titleEl.innerHTML = month ? 'My Shifts <small>' + escHtml(monthLabel) + '</small>' : 'My Upcoming Shifts <small>(next 30 days)</small>';
    if (rangeEl) {
      if (month) rangeEl.textContent = monthLabel;
      else {
        var from = new Date(), to = new Date();
        to.setDate(from.getDate() + 29);
        var md = function(d) { return d.toLocaleDateString('en-US', {month: 'short', day: 'numeric'}); };
        rangeEl.textContent = md(from) + ' – ' + md(to) + ', ' + to.getFullYear();
      }
    }
    if (resetBtn) resetBtn.style.display = month ? '' : 'none';
    try {
      var url = apiBase() + '/ess/rota' + (month ? ('?month=' + encodeURIComponent(month)) : '');
      var r = await fetch(url, {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      var rows = await r.json();
      // Only the default (no month picked) window feeds the Dashboard's
      // Next Shift / Today's Schedule tiles -- looking up a past/future
      // month shouldn't make "next shift" suddenly point at October.
      if (!month) _essData.rota = rows;
      var el = document.getElementById('rota-content');
      el.classList.remove('loading');
      if (!rows.length) { el.innerHTML = emptyState('calendar', month ? 'No shifts scheduled that month' : 'No shifts in the next 30 days', 'Published shifts from your manager will show up here.'); }
      else {
        var today = new Date(); today.setHours(0, 0, 0, 0);
        var tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1);
        var thisMonday = _mondayOf(today);
        var lastWeekLabel = null;
        var html = '';
        rows = _fillRotaDayOffs(rows);
        rows.forEach(function(a, index) {
          var d = new Date((a.date || '') + 'T00:00:00');
          var valid = !isNaN(d.getTime());
          var weekLabel = valid ? _rotaWeekLabel(d, thisMonday) : 'Upcoming';
          if (weekLabel !== lastWeekLabel) {
            html += '<div class="rota-week-label">' + weekLabel + '</div>';
            lastWeekLabel = weekLabel;
          }
          var dowShort = valid ? d.toLocaleDateString('en-US', {weekday: 'short'}) : '';
          var dayNum = valid ? d.getDate() : '—';
          var isToday = valid && d.getTime() === today.getTime();
          var isTomorrow = valid && d.getTime() === tomorrow.getTime();
          var kind = _rotaKind(a);
          var meta = _rotaTypeMeta(kind);
          var working = ['off', 'leave', 'holiday'].indexOf(kind) < 0;
          var timeRange = working ? ((a.start || '') + (a.end ? (' – ' + a.end) : '')) : '';
          var pill = isToday ? '<span class="rota-today-pill">Today</span>' : (isTomorrow ? '<span class="rota-today-pill">Tomorrow</span>' : '');
          var look = _rotaLook(a, kind);
          var shiftTasks = Array.isArray(a.tasks) ? a.tasks : [];
          var rowId = 'rota-d-' + (a.date || index);
          var mainHtml = working
            ? '<div class="rota-line-time">' + _rotaIcon('alarm', 16) + '<b>' + escHtml(timeRange || meta.fallback) + '</b>' + pill + '</div>' +
              (a.location ? '<div class="rota-line-sub">' + _rotaIcon('pin', 15) + escHtml(a.location) + '</div>' : '')
            : '<div class="rota-line-time"><b>' + escHtml(look.title) + '</b>' + pill + '</div>' +
              (look.sub ? '<div class="rota-line-sub">' + escHtml(look.sub) + '</div>' : '');
          // Shift details and the tasks the HRMS rota attached to this day (/ess/rota
          // returns them per assignment), shown when the row is opened.
          var hours = working ? _rotaShiftHours(a) : '';
          var facts = [];
          if (working) facts.push(['Shift', a.type || a.code || 'Shift']);
          if (hours) facts.push(['Hours', hours]);
          if (working && a.break_minutes) facts.push(['Break', a.break_minutes + ' min']);
          if (a.location) facts.push(['Location', a.location]);
          if (a.department) facts.push(['Department', a.department]);
          var detailHtml = '<div class="rota-detail" id="' + rowId + '" hidden>' +
            (facts.length ? '<dl class="rota-facts">' + facts.map(function(f) { return '<div><dt>' + escHtml(f[0]) + '</dt><dd>' + escHtml(String(f[1])) + '</dd></div>'; }).join('') + '</dl>' : '') +
            (shiftTasks.length ? '<div class="rota-shift-tasks">' + shiftTasks.map(function(t) {
              var taskTime = (t.start && t.end) ? (' <span class="rota-shift-task-time">' + escHtml(t.start) + '–' + escHtml(t.end) + '</span>') : '';
              return '<div class="rota-shift-task-item"><span class="rota-shift-task-dot" style="background:' + escHtml(t.color || '#2563eb') + '"></span>' + escHtml(t.title || 'Task') + taskTime + '</div>';
            }).join('') + '</div>' : '<div class="rota-detail-empty">' + (working ? 'No tasks attached to this shift.' : escHtml(look.sub || meta.fallback)) + '</div>') +
          '</div>';
          var dowIdx = valid ? (d.getDay() + 6) % 7 : 0;
          html += '<div class="rota-item ' + meta.cls + (isToday ? ' is-today' : '') + '">' +
            '<div class="rota-date-chip rota-dow-' + (working || kind === 'leave' || kind === 'holiday' ? dowIdx : 'off') + '"><div class="dow">' + escHtml(dowShort) + '</div><div class="d">' + dayNum + '</div></div>' +
            '<button type="button" class="rota-row" aria-expanded="false" aria-controls="' + rowId + '" onclick="toggleRotaRow(this)">' +
              '<span class="rota-row-icon rota-icon-' + look.icon + '">' + _rotaIcon(look.icon, 30) + '</span>' +
              '<span class="rota-row-main">' + mainHtml + (shiftTasks.length ? '<span class="rota-task-count">' + shiftTasks.length + ' task' + (shiftTasks.length > 1 ? 's' : '') + '</span>' : '') + '</span>' +
              '<span class="rota-type-pill rota-pill-' + look.tone + '">' + _rotaIcon(look.icon, 16) + escHtml(look.pill) + '</span>' +
              '<span class="rota-chevron" aria-hidden="true">' + _rotaIcon('chevron', 18) + '</span>' +
            '</button>' +
            detailHtml +
          '</div>';
        });
        el.innerHTML = html;
      }
      updateDashboard();
    } catch(e) { var roEl = document.getElementById('rota-content'); roEl.classList.remove('loading'); roEl.innerHTML = '<div class="empty">Failed to load.</div>'; }
  }

  // YYYY-MM-DD in the VIEWER's local calendar day -- Date#toISOString()
  // converts to UTC first, which silently shifts to the wrong date for
  // any timezone ahead of UTC (e.g. UAE, UTC+4: local midnight Monday is
  // still Sunday evening in UTC) once the hour is zeroed out by _mondayOf().
  function _isoDateLocal(d) {
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }

  var _deptRotaWeekStart = null; // Date, Monday of the week currently shown

  function showRotaView(view) {
    var mineBtn = document.getElementById('rota-view-mine-btn');
    var deptBtn = document.getElementById('rota-view-dept-btn');
    var mineView = document.getElementById('rota-view-mine');
    var deptView = document.getElementById('rota-view-dept');
    var isDept = view === 'dept';
    if (mineBtn) { mineBtn.classList.toggle('on', !isDept); mineBtn.setAttribute('aria-selected', String(!isDept)); }
    if (deptBtn) { deptBtn.classList.toggle('on', isDept); deptBtn.setAttribute('aria-selected', String(isDept)); }
    if (mineView) mineView.style.display = isDept ? 'none' : '';
    if (deptView) deptView.style.display = isDept ? '' : 'none';
    if (isDept) {
      if (!_deptRotaWeekStart) _deptRotaWeekStart = _mondayOf(new Date());
      loadDeptRota();
    }
  }

  function shiftDeptRotaWeek(deltaWeeks) {
    if (!_deptRotaWeekStart) _deptRotaWeekStart = _mondayOf(new Date());
    _deptRotaWeekStart = new Date(_deptRotaWeekStart);
    _deptRotaWeekStart.setDate(_deptRotaWeekStart.getDate() + deltaWeeks * 7);
    loadDeptRota();
  }

  var ROTA_WEEK_DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

  // Department Rota -- everyone in the viewer's own department, one Mon-Sun
  // week at a time, mirroring what HRMS admins already see in Rota &
  // Shift's own "Department Rota" tab. Previously ESS only ever showed an
  // employee their OWN shifts with no way to see who else is on with them.
  async function loadDeptRota() {
    var el = document.getElementById('dept-rota-content');
    var subEl = document.getElementById('dept-rota-sub');
    if (!_deptRotaWeekStart) _deptRotaWeekStart = _mondayOf(new Date());
    var weekStartStr = _isoDateLocal(_deptRotaWeekStart);
    var weekEnd = new Date(_deptRotaWeekStart);
    weekEnd.setDate(weekEnd.getDate() + 6);
    if (subEl) {
      subEl.textContent = _deptRotaWeekStart.toLocaleDateString('en-GB', {day: '2-digit', month: 'short'}) +
        ' – ' + weekEnd.toLocaleDateString('en-GB', {day: '2-digit', month: 'short', year: 'numeric'});
    }
    el.classList.add('loading');
    el.innerHTML = 'Loading…';
    try {
      var r = await fetch(apiBase() + '/ess/team/rota?week=' + encodeURIComponent(weekStartStr), {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      if (r.status === 403) { el.classList.remove('loading'); el.innerHTML = '<div class="empty">Your role is not scoped to any department.</div>'; return; }
      var data = await r.json();
      el.classList.remove('loading');
      var employees = data.employees || [];
      if (!employees.length) { el.innerHTML = '<div class="empty">No one else in your department yet.</div>'; return; }
      var byEmpDate = {};
      (data.assignments || []).forEach(function(a) {
        byEmpDate[a.employee_id + '|' + a.date] = a;
      });
      var todayStr = _isoDateLocal(new Date());
      var dayDates = [];
      for (var i = 0; i < 7; i++) {
        var dd = new Date(_deptRotaWeekStart);
        dd.setDate(dd.getDate() + i);
        dayDates.push(dd);
      }
      var headHtml = '<th>Employee</th>' + dayDates.map(function(dd, i) {
        var iso = _isoDateLocal(dd);
        return '<th class="' + (iso === todayStr ? 'is-today' : '') + '">' + ROTA_WEEK_DAY_LABELS[i] + '<br>' + dd.getDate() + '</th>';
      }).join('');
      var bodyHtml = employees.map(function(emp) {
        // Someone with shifts this week is off on the days without one; with none, the rota isn't set yet.
        var scheduled = dayDates.some(function(dd) { return byEmpDate[emp.employee_no + '|' + _isoDateLocal(dd)]; });
        var cells = dayDates.map(function(dd) {
          var iso = _isoDateLocal(dd);
          var a = byEmpDate[emp.employee_no + '|' + iso];
          var kind = a ? _rotaKind(a) : (scheduled ? 'off' : '');
          if (!kind) return '<td><div class="dept-rota-cell is-blank" title="Not scheduled yet">–</div></td>';
          if (kind === 'off' || !a.code) return '<td><div class="dept-rota-cell is-off">Off</div></td>';
          if (kind === 'leave') return '<td><div class="dept-rota-cell is-leave">Leave</div></td>';
          if (kind === 'holiday') return '<td><div class="dept-rota-cell is-holiday">Holiday</div></td>';
          var timeRange = (a.start && a.end) ? (a.start + '–' + a.end) : '';
          var taskCount = Array.isArray(a.tasks) ? a.tasks.length : 0;
          return '<td><div class="dept-rota-cell"><div class="code">' + escHtml(a.code) + '</div>' +
            (timeRange ? '<div class="time">' + escHtml(timeRange) + '</div>' : '') +
            (taskCount ? '<div class="task-count">' + taskCount + ' task' + (taskCount === 1 ? '' : 's') + '</div>' : '') +
            '</div></td>';
        }).join('');
        return '<tr class="dept-rota-row' + (emp.is_me ? ' is-me' : '') + '">' +
          '<td>' + escHtml(emp.full_name) + (emp.is_me ? ' (You)' : '') + '<div class="dept-rota-employee-sub">' + escHtml(emp.designation || '') + '</div></td>' +
          cells + '</tr>';
      }).join('');
      el.innerHTML = '<div class="dept-rota-table-wrap"><table class="dept-rota-table"><thead><tr>' + headHtml + '</tr></thead><tbody>' + bodyHtml + '</tbody></table></div>';
    } catch (e) {
      el.classList.remove('loading');
      el.innerHTML = '<div class="empty">Failed to load.</div>';
    }
  }

  // ── Tasks ──────────────────────────────────────────────────────────────
  function _taskStatusLabel(status) {
    var s = status === 'in_progress' ? 'progress' : (status || 'todo');
    var labels = {todo: 'To Do', progress: 'In Progress', done: 'Done'};
    return {cls: labels[s] ? s : 'todo', text: labels[s] || s};
  }

  function _taskActions(t) {
    if (!t.id) return '';
    var cur = _taskStatusLabel(t.status).cls;
    var b = function(label, to, cls, icon) {
      return '<button type="button" class="tk-btn ' + cls + '" data-id="' + escHtml(t.id) + '" onclick="essSetTaskStatus(this.dataset.id,\'' + to + '\',this)">' + _tkIcon(icon, 16) + '<span>' + label + '</span></button>';
    };
    var inner = cur === 'done' ? b('Reopen', 'todo', 'tk-btn-outline', 'undo')
      : cur === 'progress' ? b('Mark done', 'done', 'tk-btn-primary', 'check')
      : b('Start', 'progress', 'tk-btn-primary', 'play') + b('Mark done', 'done', 'tk-btn-outline', 'check');
    return '<div class="tk-actions">' + inner + '</div>';
  }

  async function essSetTaskStatus(id, status, btn) {
    if (btn) btn.disabled = true;
    try {
      await essApi('PATCH', '/ess/tasks/' + encodeURIComponent(id), {status: status});
      essToast(status === 'done' ? 'Task marked as done' : 'Task updated');
      await loadTasks();
    } catch (e) {
      essToast(e.message, 'err');
      if (btn) btn.disabled = false;
    }
  }

  async function loadTasks() {
    try {
      var r = await fetch(apiBase() + '/ess/tasks', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      var rows = await r.json();
      _essData.tasks = rows;
      document.getElementById('tasks-content').classList.remove('loading');
      renderEssTasks();
      updateDashboard();
    } catch(e) { var tEl = document.getElementById('tasks-content'); tEl.classList.remove('loading'); tEl.innerHTML = '<div class="empty">Failed to load.</div>'; }
  }

  // ── Tasks page: priority filter pills, search, sort and one card per task ──
  var _tkFilter = 'all';
  var TK_PRIORITY_RANK = {high: 0, medium: 1, low: 2};
  var TK_ICONS = {
    clipboard: '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4V3h6v1M9 12l2 2 4-4"/>',
    doc: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6M9 9h2"/>',
    cert: '<path d="M13 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h5"/><path d="M13 3v5h5v3M8 9h3M8 13h4"/><circle cx="16" cy="16" r="3"/><path d="m14.5 18.5-.5 3 2-1 2 1-.5-3"/>',
    invoice: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h4"/><path d="M14 3v5h5v2M8 9h3M8 13h4"/><circle cx="16.5" cy="16.5" r="4"/><path d="M16.5 14.5v2l1.5 1"/>',
    people: '<circle cx="9" cy="8" r="3.2"/><path d="M3 19c.6-3.2 3-5 6-5s5.4 1.8 6 5"/><circle cx="17" cy="9" r="2.4"/><path d="M16.5 14c2.4.2 4 1.7 4.5 4.5"/>',
    phone: '<path d="M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/>',
    box: '<path d="M21 8 12 3 3 8v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
    task: '<rect x="4" y="4" width="16" height="16" rx="3"/><path d="m8.5 12 2.5 2.5 4.5-5"/>',
    play: '<path d="M7 4.5v15l12-7.5z" fill="currentColor"/>',
    check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    undo: '<path d="M9 7 4 12l5 5"/><path d="M4 12h11a5 5 0 0 1 0 10h-2"/>',
    cal: '<rect x="3" y="4.5" width="18" height="16" rx="2.5"/><path d="M3 9.5h18M8 2.5v4M16 2.5v4"/>',
    user: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20c.8-3.6 3.6-5.5 7-5.5s6.2 1.9 7 5.5"/>',
    alert: '<circle cx="12" cy="12" r="9" fill="currentColor" stroke="none"/><path d="M12 7.5v5.5M12 16.5h.01" stroke="#fff"/>',
    bars: '<path d="M6 20v-5M12 20V10M18 20V4" stroke-width="3.2"/>',
    ok: '<circle cx="12" cy="12" r="9" fill="currentColor" stroke="none"/><path d="m8 12 3 3 5-6" stroke="#fff"/>'
  };
  function _tkIcon(name, size) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="' + size + '" height="' + size + '" aria-hidden="true">' + (TK_ICONS[name] || '') + '</svg>';
  }
  // An icon that suits the task, picked from words in its title.
  function _tkKindIcon(t) {
    var s = ((t.title || '') + ' ' + (t.description || '')).toLowerCase();
    if (/licen[cs]e|renew|certif|permit|visa|contract/.test(s)) return 'cert';
    if (/invoice|payment|overdue|collect|receivable|bill/.test(s)) return 'invoice';
    if (/stock|count|inventory|audit|check/.test(s)) return 'clipboard';
    if (/delivery|shipment|order|package|warehouse/.test(s)) return 'box';
    if (/call|phone|follow up|follow-up|contact/.test(s)) return 'phone';
    if (/meeting|team|train|interview|onboard/.test(s)) return 'people';
    if (/price|list|report|update|document|file|prepare/.test(s)) return 'doc';
    return 'task';
  }
  function _tkPriority(t) {
    var p = String(t.priority || 'Medium').toLowerCase();
    return p === 'high' || p === 'urgent' ? 'high' : p === 'low' ? 'low' : 'medium';
  }
  function _tkDue(t, done) {
    if (!t.due_date) return {text: 'No due date', overdue: false};
    var d = new Date(t.due_date + 'T00:00:00');
    if (isNaN(d.getTime())) return {text: 'Due ' + t.due_date, overdue: false};
    var today = new Date(); today.setHours(0, 0, 0, 0);
    var days = Math.round((d - today) / 86400000);
    var text = 'Due ' + d.toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'});
    if (!done && days === 0) text += ' · Today';
    else if (!done && days === 1) text += ' · Tomorrow';
    return {text: text, overdue: !done && days < 0};
  }
  function setEssTaskFilter(f) { _tkFilter = f; renderEssTasks(); }
  function renderEssTasks() {
    var el = document.getElementById('tasks-content');
    var all = _essData.tasks || [];
    var counts = {all: all.length, high: 0, medium: 0, low: 0};
    all.forEach(function(t) { counts[_tkPriority(t)] += 1; });
    var filtersEl = document.getElementById('tk-filters');
    if (filtersEl) {
      filtersEl.innerHTML = [['all', 'All Tasks'], ['high', 'High Priority'], ['medium', 'Medium Priority'], ['low', 'Low Priority']].map(function(f) {
        var on = _tkFilter === f[0];
        return '<button type="button" role="tab" aria-selected="' + on + '" class="tk-filter tk-filter-' + f[0] + (on ? ' on' : '') + '" onclick="setEssTaskFilter(\'' + f[0] + '\')">' + f[1] + '<b>' + counts[f[0]] + '</b></button>';
      }).join('');
    }
    if (!el) return;
    if (!all.length) { el.innerHTML = emptyState('check', 'No tasks assigned to you', 'Tasks your manager assigns will appear here.'); return; }
    var q = (document.getElementById('tk-search') || {}).value || '';
    q = q.trim().toLowerCase();
    var sort = (document.getElementById('tk-sort') || {}).value || 'due-asc';
    var statusRank = {progress: 0, todo: 1, done: 2};
    var rows = all.filter(function(t) {
      if (_tkFilter !== 'all' && _tkPriority(t) !== _tkFilter) return false;
      return !q || ((t.title || '') + ' ' + (t.description || '')).toLowerCase().indexOf(q) >= 0;
    }).sort(function(a, b) {
      // Finished work sinks to the bottom whatever the sort.
      var doneA = _taskStatusLabel(a.status).cls === 'done', doneB = _taskStatusLabel(b.status).cls === 'done';
      if (doneA !== doneB) return doneA ? 1 : -1;
      var da = a.due_date || '9999-99-99', db = b.due_date || '9999-99-99';
      if (sort === 'due-desc') return (b.due_date || '') < (a.due_date || '') ? -1 : (b.due_date || '') > (a.due_date || '') ? 1 : 0;
      if (sort === 'priority') return (TK_PRIORITY_RANK[_tkPriority(a)] - TK_PRIORITY_RANK[_tkPriority(b)]) || (da < db ? -1 : 1);
      if (sort === 'status') return (statusRank[_taskStatusLabel(a.status).cls] - statusRank[_taskStatusLabel(b.status).cls]) || (da < db ? -1 : 1);
      return da < db ? -1 : da > db ? 1 : 0;
    });
    if (!rows.length) { el.innerHTML = emptyState('check', 'No matching tasks', 'Try another priority or search.'); return; }
    var priMeta = {high: ['alert', 'High priority'], medium: ['bars', 'Medium priority'], low: ['ok', 'Low priority']};
    el.innerHTML = rows.map(function(t) {
      var st = _taskStatusLabel(t.status);
      var pri = _tkPriority(t);
      var done = st.cls === 'done';
      var due = _tkDue(t, done);
      var color = /^#[0-9a-f]{3,8}$/i.test(t.color || '') ? t.color : '';
      var progress = Math.max(0, Math.min(100, Number(t.progress) || 0));
      var id = escHtml(t.id || '');
      return '<article class="tk-item tk-pri-' + pri + (done ? ' is-done' : '') + '"' + (color ? ' style="--tk-accent:' + escHtml(color) + '"' : '') + '>' +
        '<div class="tk-icon">' + _tkIcon(_tkKindIcon(t), 30) + '</div>' +
        '<div class="tk-body">' +
          '<h3 class="tk-title">' + escHtml(t.title || 'Untitled task') + '</h3>' +
          (t.description ? '<p class="tk-desc">' + escHtml(t.description) + '</p>' : '') +
          '<span class="tk-pri">' + _tkIcon(priMeta[pri][0], 16) + priMeta[pri][1] + '</span>' +
          '<div class="tk-meta">' +
            '<span class="' + (due.overdue ? 'tk-overdue' : '') + '">' + _tkIcon('cal', 16) + escHtml(due.text) + (due.overdue ? ' · Overdue' : '') + '</span>' +
            '<span class="tk-sep" aria-hidden="true"></span>' +
            '<span>' + _tkIcon('user', 16) + 'Assigned to: You</span>' +
          '</div>' +
          (progress > 0 && !done ? '<div class="tk-progress" role="progressbar" aria-valuenow="' + progress + '" aria-valuemin="0" aria-valuemax="100" aria-label="Progress"><i style="width:' + progress + '%"></i><span>' + progress + '%</span></div>' : '') +
        '</div>' +
        '<div class="tk-side">' +
          '<div class="tk-side-top">' +
            '<span class="tk-status tk-status-' + st.cls + '">' + escHtml(st.text) + '</span>' +
            (t.id ? '<button type="button" class="tk-more" aria-label="More actions" data-id="' + id + '" data-status="' + st.cls + '" onclick="openEssTaskMenu(event,this)">&#8943;</button>' : '') +
          '</div>' +
          _taskActions(t) +
        '</div>' +
      '</article>';
    }).join('');
  }
  // "⋯": move the task to any status.
  function openEssTaskMenu(event, btn) {
    event.stopPropagation();
    var old = document.getElementById('tk-menu');
    if (old) { old.remove(); if (old._for === btn) return; }
    var menu = document.createElement('div');
    menu.id = 'tk-menu';
    menu.className = 'tk-menu';
    menu.setAttribute('role', 'menu');
    menu._for = btn;
    var cur = btn.dataset.status;
    menu.innerHTML = [['todo', 'Move to To Do'], ['progress', 'Move to In Progress'], ['done', 'Mark as done']].map(function(o) {
      return '<button type="button" role="menuitem"' + (o[0] === cur ? ' disabled' : '') + ' data-to="' + o[0] + '">' + o[1] + '</button>';
    }).join('');
    menu.addEventListener('click', function(e) {
      var item = e.target.closest('button[data-to]');
      if (!item || item.disabled) return;
      menu.remove();
      essSetTaskStatus(btn.dataset.id, item.dataset.to, btn);
    });
    document.body.appendChild(menu);
    var r = btn.getBoundingClientRect();
    menu.style.top = (r.bottom + 6) + 'px';  // position:fixed -- ESS scrolls .content, not the window
    menu.style.left = Math.max(8, r.right - menu.offsetWidth) + 'px';
    setTimeout(function() {
      document.addEventListener('click', function close(e) {
        if (!menu.contains(e.target)) { menu.remove(); document.removeEventListener('click', close); }
      });
    });
    var first = menu.querySelector('button:not([disabled])');
    if (first) first.focus();
  }

  // ── Dashboard summary — derived entirely from data the other tabs'
  // loaders already fetched (_essData), so opening the portal never fires
  // an extra request just for the summary tiles. Called (harmlessly, and
  // idempotently) by every loader as it resolves, since they don't all
  // finish at the same time.
  function fmtShortDate(iso) {
    try {
      var d = new Date(iso + 'T00:00:00');
      if (isNaN(d.getTime())) return iso;
      return d.toLocaleDateString('en-US', {weekday: 'short', month: 'short', day: 'numeric'});
    } catch(e) { return iso; }
  }

  function renderDashPayslip() {
    var el = document.getElementById('dash-payslip-body');
    if (!el) return;
    var p = (_essData.payslips || [])[0];
    if (!p) { el.innerHTML = '<div style="font-size:13px;color:var(--text3);padding:6px 0">No payslip yet — it appears here once payroll has been run for you.</div>'; return; }
    var row = function(label, val, cls) { return '<div class="payslip-row' + (cls ? ' ' + cls : '') + '"><span>' + label + '</span><span>' + (cls === 'neg' && Number(val) ? '− ' : '') + essMoney(val) + '</span></div>'; };
    el.innerHTML = '<div style="display:flex;justify-content:space-between;align-items:baseline;gap:8px;margin-bottom:8px"><div style="font-size:12px;color:var(--text3)">' + escHtml(_psPeriodLabel(p.period)) + '</div><span class="st-badge st-' + _psStatusClass(p.run_status) + '">' + escHtml(p.run_status || '') + '</span></div>' +
      '<div class="ps-net-val" style="font-size:24px;margin-bottom:8px">' + essMoney(p.net_pay) + '</div>' +
      row('Basic', p.basic) + row('Allowances', p.allowances) + row('Overtime', p.overtime) + row('Deductions', p.deductions, 'neg') +
      '<button class="btn-ghost ps-print" style="margin-top:10px" onclick="essGoTab(\'payslips\')">All payslips →</button>';
  }

  // "Pending With HR" -- same shape as HRMS's Pending Approvals list, but for the requests THIS employee is waiting on.
  function renderDashReqSummary() {
    var el = document.getElementById('dash-req-summary');
    if (!el || !_essRequestsLoaded) return;
    var pend = function(kinds) { return _essRequests.filter(function(q) { return q.status === 'pending' && kinds.indexOf(q.kind) > -1; }).length; };
    var rows = [
      ['Leave Requests', ['leave'], '#eff6ff', '#2563eb', '#dbeafe', '#1d4ed8', '<rect x="2" y="2" width="10" height="11" rx="1"/><path d="M4 5h6M4 7.5h6M4 10h3"/>'],
      ['Overtime', ['overtime'], '#fff7ed', '#ea580c', '#fed7aa', '#c2410c', '<circle cx="7" cy="7" r="5"/><path d="M7 4v3.5l2 1.5"/>'],
      ['Attendance Fixes', ['correction'], '#f0fdf4', '#16a34a', '#dcfce7', '#15803d', '<path d="M2 7l3 3 7-6"/>'],
      ['Loans & Advances', ['loan', 'advance'], '#faf5ff', '#7c3aed', '#ede9fe', '#6d28d9', '<rect x="1.5" y="3" width="11" height="8" rx="1.5"/><path d="M1.5 6h11"/>']
    ];
    el.innerHTML = rows.map(function(r) {
      var n = pend(r[1]);
      return '<div class="ess-appr-row" onclick="essGoTab(\'requests\')"><div class="ess-appr-icon" style="background:' + r[2] + '"><svg viewBox="0 0 14 14" width="14" height="14" fill="none" stroke="' + r[3] + '" stroke-width="1.4">' + r[6] + '</svg></div>' +
        '<div class="ess-appr-name">' + r[0] + '</div><div class="ess-appr-badge" style="background:' + r[4] + ';color:' + r[5] + '">' + n + '</div></div>';
    }).join('');
    essMakeKeyboardOperable(el);
  }

  function updateDashboard() {
    var todayStr = _todayISO();
    var todaysPunches = _essData.attendance.filter(function(p) { return p.punch_date === todayStr; });
    var attEl = document.getElementById('dash-att-status');
    if (attEl) {
      if (!todaysPunches.length) attEl.textContent = 'Not clocked in';
      else attEl.textContent = todaysPunches[0].direction === 'in' ? 'Checked In' : 'Checked Out';
    }
    var reqEl = document.getElementById('dash-requests-list');
    if (reqEl && _essRequestsLoaded) {
      if (!_essRequests.length) {
        reqEl.innerHTML = '<div class="empty" style="padding:16px 0">No requests yet.</div>';
      } else {
        reqEl.innerHTML = _essRequests.slice(0, 4).map(function(q) { return _reqRowHtml(q, true); }).join('');
        if (_essRequests.length > 4) {
          reqEl.innerHTML += '<div style="text-align:center;margin-top:4px"><a style="font-size:12.5px;color:var(--accent-text);cursor:pointer" onclick="essGoTab(\'requests\')">View all ' + _essRequests.length + ' →</a></div>';
        }
      }
    }
    var leaveEl = document.getElementById('dash-leave-status');
    if (leaveEl) {
      var pending = _essData.leave.filter(function(l) { return l.status === 'pending'; }).length;
      leaveEl.textContent = pending ? (pending + ' Pending') : 'All clear';
    }
    var nextEl = document.getElementById('dash-next-shift');
    if (nextEl) {
      // The rota list starts a week back: take the first working shift from today on (same rule as the mobile card).
      var upcoming = _essData.rota.find(function(a) { return (a.date || '') >= todayStr && ['off', 'leave', 'holiday'].indexOf(_rotaKind(a)) < 0; });
      nextEl.textContent = upcoming ? (fmtShortDate(upcoming.date) + (upcoming.code ? ' · ' + upcoming.code : '')) : 'No upcoming shifts';
    }
    var openTasks = _essData.tasks.filter(function(t) { return t.status !== 'done'; }).length;
    var tasksEl = document.getElementById('dash-tasks-count');
    if (tasksEl) tasksEl.textContent = openTasks ? (openTasks + ' Open') : 'All done';
    var taskBadge = document.getElementById('ess-nav-tasks-badge');
    if (taskBadge) { taskBadge.textContent = openTasks; taskBadge.style.display = openTasks ? '' : 'none'; }
    var paySlipEl = document.getElementById('dash-last-payslip');
    if (paySlipEl) {
      var latest = _essData.payslips[0];
      paySlipEl.textContent = latest ? essMoney(latest.net_pay) : '—';
    }
    renderDashPayslip();
    renderDashReqSummary();
    var schedEl = document.getElementById('dash-today-schedule');
    if (schedEl) {
      var todaysShifts = _essData.rota.filter(function(a) { return a.date === todayStr; });
      if (!todaysShifts.length) {
        schedEl.innerHTML = '<div class="empty" style="padding:12px 0">No shift scheduled today.</div>';
      } else {
        schedEl.innerHTML = todaysShifts.map(function(a) {
          var timeRange = (a.start || '') + (a.end ? (' – ' + a.end) : '');
          return '<div class="dash-sched-row">' +
            '<div class="dash-sched-icon"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><rect x="2" y="3" width="12" height="11" rx="1"/><path d="M5 1.5v3M11 1.5v3M2 6h12"/></svg></div>' +
            '<div style="flex:1;min-width:0">' +
              '<div class="dash-sched-time">' + escHtml(timeRange || '—') + '</div>' +
              '<div class="dash-sched-title">' + escHtml(a.type || a.code || 'Shift') + (a.location ? (' · ' + escHtml(a.location)) : '') + '</div>' +
            '</div>' +
          '</div>';
        }).join('');
      }
    }
    refreshNotifications();
  }

  function fmt(n) {
    // row('Deductions', '- ' + p.deductions) used to pre-concatenate a text
    // prefix before the number, so Number() on that string ("- 50" — note
    // the space Number() can't parse, or "- undefined" when payroll hadn't
    // populated a deductions value at all) was ALWAYS NaN, not just when
    // deductions was missing. Now passed the raw number; this guard still
    // covers a genuinely missing/non-numeric value defensively.
    var v = Number(n);
    return (isNaN(v) ? 0 : v).toLocaleString('en-AE', {minimumFractionDigits:2,maximumFractionDigits:2});
  }

  // ══ Self-service requests, profile details, documents ═══════════════════════
  function _todayISO() {
    var d = new Date();
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }
  function _addDaysISO(iso, n) {
    var d = new Date(iso + 'T00:00:00');
    d.setDate(d.getDate() + n);
    return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
  }
  function _dateOrDash(iso) { return iso ? fmtShortDate(iso) : '—'; }
  function essGoTab(name) {
    var tabEl = document.querySelector('.tab[onclick*=' + name + ']');
    if (tabEl) tabEl.click();
  }

  var _essToastTimer = null;
  function essToast(msg, kind) {
    var el = document.getElementById('ess-toast');
    if (!el) return;
    el.textContent = msg;
    el.className = 'ess-toast on' + (kind === 'err' ? ' err' : '');
    clearTimeout(_essToastTimer);
    _essToastTimer = setTimeout(function() { el.className = 'ess-toast'; }, 3500);
  }

  // Promise-based styled confirm, mirroring app.js's own appConfirm() (main
  // app / hrms.html) -- ess.js is a fully separate script/bundle (no shared
  // code with app.js), so this is its own small equivalent built from the
  // same .ess-overlay/.ess-modal classes every other ESS modal already uses,
  // rather than falling back to a native confirm() the rest of the app has
  // moved away from.
  function essConfirm(opts) {
    opts = opts || {};
    var overlay = document.getElementById('ess-confirm-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'ess-overlay';
      overlay.id = 'ess-confirm-overlay';
      overlay.innerHTML =
        '<div class="ess-modal" style="max-width:360px">' +
          '<div class="ess-modal-title" id="ess-confirm-title">Confirm</div>' +
          '<div id="ess-confirm-message" style="font-size:13.5px;color:var(--text2);margin-bottom:20px"></div>' +
          '<div style="display:flex;gap:10px;justify-content:flex-end">' +
            '<button class="btn-ghost" id="ess-confirm-cancel" type="button">Cancel</button>' +
            '<button class="btn-login" id="ess-confirm-ok" type="button" style="width:auto;padding:9px 18px">OK</button>' +
          '</div>' +
        '</div>';
      document.body.appendChild(overlay);
    }
    document.getElementById('ess-confirm-title').textContent = opts.title || 'Confirm';
    document.getElementById('ess-confirm-message').textContent = opts.message || '';
    var ok = document.getElementById('ess-confirm-ok');
    ok.textContent = opts.okText || 'OK';
    var cancel = document.getElementById('ess-confirm-cancel');
    cancel.textContent = opts.cancelText || 'Cancel';
    overlay.classList.add('on');
    return new Promise(function(resolve) {
      function done(value) {
        overlay.classList.remove('on');
        ok.onclick = null;
        cancel.onclick = null;
        overlay.onclick = null;
        resolve(value);
      }
      ok.onclick = function() { done(true); };
      cancel.onclick = function() { done(false); };
      overlay.onclick = function(e) { if (e.target === overlay) done(false); };
    });
  }

  // One place for the write calls: 401 -> sign out, errors -> Error(message).
  async function essApi(method, path, body) {
    var r = await fetch(apiBase() + path, {method: method, headers: essHeaders(), body: body === undefined ? undefined : JSON.stringify(body)});
    if (r.status === 401) { essLogout(); throw new Error('Your session expired — please sign in again'); }
    var data = null;
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) {
      var d = data && data.detail;
      throw new Error(typeof d === 'string' ? d : (Array.isArray(d) && d[0] && d[0].msg) || 'Something went wrong — please try again');
    }
    return data;
  }

  // ── Requests list ──
  var _essRequests = [];
  var _essRequestsLoaded = false;
  var _essReqFilter = 'all';
  var _essNewIds = {};
  var REQ_KIND = {
    leave: {label: 'Leave', icon: 'calendar'}, overtime: {label: 'Overtime', icon: 'clock'},
    loan: {label: 'Loan', icon: 'wallet'}, advance: {label: 'Salary advance', icon: 'wallet'},
    correction: {label: 'Attendance correction', icon: 'edit'}
  };
  ES_ICONS.edit = '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>';
  function essIconSvg(name) {
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + (ES_ICONS[name] || ES_ICONS.check) + '</svg>';
  }
  var OT_TYPE_LABEL = {normal: 'Normal', ramadan: 'Ramadan', weekend: 'Weekend', holiday: 'Public holiday'};

  function _reqStatusClass(st) { return {pending: 'pending', approved: 'approved', rejected: 'rejected', cancelled: 'cancelled'}[st] || 'todo'; }
  function _reqTitle(q) {
    if (q.kind === 'leave') return q.leave_type || 'Leave';
    if (q.kind === 'overtime') return 'Overtime · ' + q.ot_hours + ' h';
    if (q.kind === 'loan') return (q.loan_type || 'Loan') + ' · ' + essMoney(q.amount);
    if (q.kind === 'advance') return 'Salary advance · ' + essMoney(q.amount);
    return 'Attendance correction';
  }
  function _reqSub(q) {
    if (q.kind === 'leave') return q.start_date + ' → ' + q.end_date + ' (' + q.days + ' day' + (q.days === 1 ? '' : 's') + ')';
    if (q.kind === 'overtime') return _dateOrDash(q.date) + ' · ' + (OT_TYPE_LABEL[q.ot_type] || 'Normal') + (q.multiplier ? ' · ' + q.multiplier : '');
    if (q.kind === 'loan') return q.months + ' month' + (Number(q.months) === 1 ? '' : 's') + ' · EMI ' + essMoney(q.emi);
    if (q.kind === 'advance') return 'Recover from ' + _psPeriodLabel(q.month);
    return _dateOrDash(q.date) + ' · in ' + (q.checkin || '—') + ' · out ' + (q.checkout || '—');
  }
  function _reqRowHtml(q, compact) {
    var k = REQ_KIND[q.kind] || REQ_KIND.leave;
    var when = q.submitted ? 'Sent ' + fmtShortDate(String(q.submitted).slice(0, 10)) : '';
    var cancel = (q.kind === 'leave' && q.can_cancel && !compact)
      ? '<button class="link-btn danger" data-id="' + escHtml(q.id) + '" onclick="essCancelLeave(this.dataset.id)">' + (q.status === 'approved' ? 'Cancel leave' : 'Cancel request') + '</button>' : '';
    if (q.kind === 'leave' && q.status === 'cancelled' && q.cancelled_by && !compact)
      cancel = '<div class="req-reason">Cancelled by ' + escHtml(q.cancelled_by) + (q.cancel_reason ? ' — ' + escHtml(q.cancel_reason) : '') + '</div>';
    return '<div class="req-row' + (!compact && _essNewIds[q.id] ? ' is-new' : '') + '"' + (compact ? ' style="cursor:pointer" onclick="essGoTab(\'requests\')"' : '') + '>' +
      '<div class="req-ico">' + essIconSvg(k.icon) + '</div>' +
      '<div class="req-main"><div class="req-title">' + escHtml(_reqTitle(q)) + '</div><div class="req-sub">' + escHtml(_reqSub(q)) + '</div>' +
        (q.reason ? '<div class="req-reason">' + escHtml(q.reason) + '</div>' : '') + cancel + '</div>' +
      '<div class="req-side"><span class="st-badge st-' + _reqStatusClass(q.status) + '">' + escHtml(q.status) + '</span><div class="req-when">' + escHtml(when) + '</div></div>' +
    '</div>';
  }

  function essSetReqFilter(kind) { _essReqFilter = kind; renderRequests(); }
  function renderRequests() {
    var el = document.getElementById('requests-content');
    if (!el) return;
    el.classList.remove('loading');
    var f = document.getElementById('req-filter');
    var counts = {all: _essRequests.length};
    _essRequests.forEach(function(q) { counts[q.kind] = (counts[q.kind] || 0) + 1; });
    if (f) {
      f.style.display = _essRequests.length ? '' : 'none';
      f.innerHTML = ['all', 'leave', 'overtime', 'loan', 'advance', 'correction'].filter(function(k) { return k === 'all' || counts[k]; }).map(function(k) {
        return '<button type="button" class="ess-view-toggle-btn' + (_essReqFilter === k ? ' on' : '') + '" onclick="essSetReqFilter(\'' + k + '\')">' +
          (k === 'all' ? 'All' : REQ_KIND[k].label) + ' (' + counts[k] + ')</button>';
      }).join('');
    }
    if (!_essRequests.length) { el.innerHTML = emptyState('check', 'No requests yet', 'Use the buttons above to send leave, overtime, a salary advance, a loan or an attendance fix to HR.'); return; }
    var shown = _essRequests.filter(function(q) { return _essReqFilter === 'all' || q.kind === _essReqFilter; });
    el.innerHTML = shown.length ? shown.map(function(q) { return _reqRowHtml(q, false); }).join('') : emptyState('check', 'Nothing in this category', '');
  }

  async function essRefreshRequests() {
    try {
      _essRequests = await essApi('GET', '/ess/requests');
      _essRequestsLoaded = true;
    } catch (e) {
      var el = document.getElementById('requests-content');
      if (!_essRequestsLoaded && el) { el.classList.remove('loading'); el.innerHTML = '<div class="empty">Failed to load.</div>'; }
      return;
    }
    renderRequests();
    renderAttendance();
    updateDashboard();
    essProcessRequestUpdates();
    essLoadOtEligibility();
  }

  var _essOtElig = [];
  async function essLoadOtEligibility() {
    var el = document.getElementById('ot-elig-content');
    if (!el) return;
    try {
      var data = await essApi('GET', '/ess/overtime-eligibility');
      _essOtElig = data.rows || [];
      var note = document.getElementById('ot-elig-note');
      if (note) note.textContent = data.cooloff_minutes ? 'The first ' + data.cooloff_minutes + ' min past your standard day are not counted as overtime' : 'Days you worked past your standard day (last 31 days)';
    } catch (e) { el.innerHTML = '<div class="empty">Failed to load.</div>'; return; }
    if (!_essOtElig.length) { el.innerHTML = emptyState('clock', 'No overtime detected', 'Days you work past your standard hours will appear here.'); return; }
    el.innerHTML = '<div style="overflow-x:auto"><table class="tbl" style="width:100%;font-size:12.5px"><thead><tr><th>Date</th><th>In</th><th>Out</th><th>Worked</th><th>Extra</th><th>Eligible OT</th><th>Status</th><th></th></tr></thead><tbody>' +
      _essOtElig.map(function(r, i) {
        var cls = !r.eligible ? 'rejected' : (r.eligibility.indexOf('Requested') === 0 ? 'approved' : 'pending');
        var act = r.eligible && !r.request_id ? '<button class="btn-ghost" style="padding:5px 10px;font-size:11.5px" onclick="essRequestOtFromRow(' + i + ')">Request</button>' : '';
        return '<tr><td>' + escHtml(r.date) + (r.day_type === 'weekend' ? ' <span class="st-badge st-pending">Weekend</span>' : '') + '</td><td>' + escHtml(r.clock_in || '—') + '</td><td>' + escHtml(r.clock_out || '—') + '</td><td>' + escHtml(r.worked) + (r.break ? '<div class="m-muted" style="font-size:11px">after ' + escHtml(r.break) + ' break</div>' : '') + '</td><td>' + escHtml(r.extra) + '</td><td>' + escHtml(r.eligible_ot) + '</td><td><span class="st-badge st-' + cls + '">' + escHtml(r.eligibility) + '</span></td><td>' + act + '</td></tr>';
      }).join('') + '</tbody></table></div>';
  }
  function essRequestOtFromRow(i) {
    var r = _essOtElig[i];
    if (r) essOpenRequestModal('overtime', {date: r.date, hours: r.eligible_hours, type: r.day_type === 'weekend' ? 'weekend' : 'normal'});
  }

  async function essCancelLeave(id) {
    var rec = (_essData.leave || []).find(function(l) { return l.id === id; }) || {};
    var approved = rec.status === 'approved';
    var ok = await essConfirm(approved
      ? {title: 'Cancel Approved Leave', message: 'Cancel your approved ' + (rec.leave_type || 'leave') + ' (' + rec.start_date + ' → ' + rec.end_date + ')? The days go back to your balance and HR will see it was cancelled.', okText: 'Cancel Leave', cancelText: 'Keep Leave'}
      : {title: 'Cancel Leave Request', message: 'Cancel this leave request?', okText: 'Cancel Request', cancelText: 'Keep Request'});
    if (!ok) return;
    try {
      await essApi('POST', '/ess/leave/' + encodeURIComponent(id) + '/cancel');
      essToast(approved ? 'Leave cancelled — the days are back in your balance' : 'Leave request cancelled');
      loadLeave();
      essRefreshRequests();
      loadLeaveBalance();
    } catch (e) { essToast(e.message, 'err'); }
  }

  // ── "Your request was approved/rejected" notifications: remember which
  // decisions this browser has already shown (per employee), so a decision
  // announces itself once. The first visit on a browser only baselines.
  function _seenKey() { return _essMe ? 'ess_seen_' + _essMe.id : null; }
  function _readSeen() {
    var k = _seenKey();
    if (!k) return null;
    try { return JSON.parse(localStorage.getItem(k) || 'null'); } catch (e) { return null; }
  }
  function _writeSeen(map) {
    var k = _seenKey();
    if (!k) return;
    try { localStorage.setItem(k, JSON.stringify(map)); } catch (e) {}
  }
  function _decidedMap() {
    var m = {};
    _essRequests.forEach(function(q) { if (q.status !== 'pending') m[q.id] = q.status; });
    return m;
  }
  function essUnseenDecisions() {
    if (!_essRequestsLoaded || !_essMe) return [];
    var seen = _readSeen();
    if (seen === null) { _writeSeen(_decidedMap()); return []; }
    return _essRequests.filter(function(q) { return q.status !== 'pending' && q.status !== 'cancelled' && seen[q.id] !== q.status; });
  }
  function essMarkRequestsSeen() {
    if (!_essRequestsLoaded || !_essMe) return;
    _essNewIds = {};
    essUnseenDecisions().forEach(function(q) { _essNewIds[q.id] = true; });
    _writeSeen(_decidedMap());
    renderRequests();
    essProcessRequestUpdates();
  }
  function essProcessRequestUpdates() {
    var n = essUnseenDecisions().length;
    var b = document.getElementById('ess-nav-requests-badge');
    if (b) { b.textContent = n; b.style.display = n ? '' : 'none'; }
    refreshNotifications();
  }

  // ── Request forms (one modal, four bodies) ──
  var _essReqKind = null;
  var _RQ_REASON = function(label, required) {
    return '<div class="field last"><label for="rq-reason">' + label + (required ? '' : ' (optional)') + '</label><input id="rq-reason" type="text" maxlength="500" placeholder="' + (required ? 'What happened?' : 'Brief note for HR') + '"></div>';
  };
  function _rqVal(id) { var el = document.getElementById(id); return el ? el.value.trim() : ''; }
  function essOtHours() {
    var a = _rqVal('rq-login'), b = _rqVal('rq-logout'), h = document.getElementById('rq-hours');
    if (!a || !b || !h) return;
    var m = function(t) { return parseInt(t.substr(0, 2), 10) * 60 + parseInt(t.substr(3, 2), 10); };
    var diff = m(b) - m(a);
    if (diff <= 0) diff += 24 * 60;
    h.value = (Math.round(diff / 60 * 100) / 100).toString();
  }
  function essLoanEmi() {
    var amt = parseFloat(_rqVal('rq-amount')), mo = parseInt(_rqVal('rq-months'), 10);
    var el = document.getElementById('rq-emi');
    if (!el) return;
    if (amt > 0 && mo > 0) { el.style.display = 'block'; el.innerHTML = 'Monthly repayment ≈ <b>' + essMoney(amt / mo) + '</b> for ' + mo + ' month' + (mo === 1 ? '' : 's') + ' — HR sets the final terms when approving.'; }
    else el.style.display = 'none';
  }
  var REQ_FORMS = {
    overtime: {
      title: 'Request Overtime', path: '/ess/overtime', ok: 'Overtime request sent to HR',
      html: function(p) {
        var today = _todayISO();
        return '<div class="fr2"><div class="field"><label for="rq-date">Date</label><input id="rq-date" type="date" min="' + _addDaysISO(today, -31) + '" max="' + today + '" value="' + (p.date || today) + '"></div>' +
          '<div class="field"><label for="rq-ottype">Type</label><select id="rq-ottype" class="ess-select"><option value="normal">Normal</option><option value="ramadan">Ramadan</option><option value="weekend"' + (p.type === 'weekend' ? ' selected' : '') + '>Weekend</option><option value="holiday">Public holiday</option></select></div></div>' +
          '<div class="fr2"><div class="field"><label for="rq-login">From</label><input id="rq-login" type="time" oninput="essOtHours()"></div>' +
          '<div class="field"><label for="rq-logout">To</label><input id="rq-logout" type="time" oninput="essOtHours()"></div></div>' +
          '<div class="field"><label for="rq-hours">Hours</label><input id="rq-hours" type="number" step="0.25" min="0.25" max="12" placeholder="e.g. 2.5" value="' + (p.hours || '') + '"><div class="hint">Filled in from the times above, or type the hours directly. The pay rate follows your company’s OT rules.</div></div>' +
          _RQ_REASON('Reason', false);
      },
      collect: function() {
        var login = _rqVal('rq-login'), logout = _rqVal('rq-logout'), hours = parseFloat(_rqVal('rq-hours'));
        if (!_rqVal('rq-date')) throw new Error('Choose the date you worked overtime');
        if (!(login && logout) && !(hours > 0)) throw new Error('Enter the start and end times, or the number of hours');
        return {date: _rqVal('rq-date'), login: login || null, logout: logout || null, ot_hours: hours > 0 ? hours : null, ot_type: _rqVal('rq-ottype'), reason: _rqVal('rq-reason') || null};
      }
    },
    advance: {
      title: 'Request Salary Advance', path: '/ess/advances', ok: 'Salary advance request sent to HR',
      html: function() {
        return '<div class="fr2"><div class="field"><label for="rq-amount">Amount (' + escHtml(_essCurrency) + ')</label><input id="rq-amount" type="number" min="1" step="0.01" placeholder="0.00"></div>' +
          '<div class="field"><label for="rq-month">Recover from</label><input id="rq-month" type="month" value="' + _todayISO().slice(0, 7) + '"></div></div>' + _RQ_REASON('Reason', false);
      },
      collect: function() {
        var amt = parseFloat(_rqVal('rq-amount'));
        if (!(amt > 0)) throw new Error('Enter the advance amount');
        return {amount: amt, month: _rqVal('rq-month') || null, reason: _rqVal('rq-reason') || null};
      }
    },
    loan: {
      title: 'Request a Loan', path: '/ess/loans', ok: 'Loan request sent to HR',
      html: function() {
        var types = ['Personal Loan', 'Emergency Loan', 'Medical Loan', 'Home Furnishing Loan', 'Education Loan', 'Vehicle Loan'];
        return '<div class="field"><label for="rq-loantype">Loan type</label><select id="rq-loantype" class="ess-select">' + types.map(function(t) { return '<option>' + t + '</option>'; }).join('') + '</select></div>' +
          '<div class="fr2"><div class="field"><label for="rq-amount">Amount (' + escHtml(_essCurrency) + ')</label><input id="rq-amount" type="number" min="1" step="0.01" placeholder="0.00" oninput="essLoanEmi()"></div>' +
          '<div class="field"><label for="rq-months">Repay over (months)</label><input id="rq-months" type="number" min="1" max="60" value="6" oninput="essLoanEmi()"></div></div>' +
          '<div id="rq-emi" class="leave-preview"></div>' + _RQ_REASON('Reason', false);
      },
      collect: function() {
        var amt = parseFloat(_rqVal('rq-amount')), mo = parseInt(_rqVal('rq-months'), 10);
        if (!(amt > 0)) throw new Error('Enter the loan amount');
        if (!(mo >= 1 && mo <= 60)) throw new Error('Repayment period must be between 1 and 60 months');
        return {type: _rqVal('rq-loantype'), amount: amt, months: mo, reason: _rqVal('rq-reason') || null};
      }
    },
    correction: {
      title: 'Request an Attendance Correction', path: '/ess/attendance-corrections', ok: 'Correction request sent to HR',
      html: function(p) {
        var today = _todayISO();
        return '<div class="field"><label for="rq-date">Date</label><input id="rq-date" type="date" min="' + _addDaysISO(today, -60) + '" max="' + today + '" value="' + (p.date || '') + '"></div>' +
          '<div class="fr2"><div class="field"><label for="rq-checkin">Check-in</label><input id="rq-checkin" type="time" value="' + (p.checkin || '') + '"></div>' +
          '<div class="field"><label for="rq-checkout">Check-out</label><input id="rq-checkout" type="time"></div></div>' +
          '<div class="hint" style="margin:-6px 0 14px">Enter the times that should have been recorded. Once HR approves, they are added to your attendance.</div>' +
          _RQ_REASON('Reason', true);
      },
      collect: function() {
        if (!_rqVal('rq-date')) throw new Error('Choose the date to correct');
        if (!_rqVal('rq-checkin') && !_rqVal('rq-checkout')) throw new Error('Enter the check-in time, the check-out time, or both');
        if (!_rqVal('rq-reason')) throw new Error('Please give a short reason');
        return {date: _rqVal('rq-date'), checkin: _rqVal('rq-checkin') || null, checkout: _rqVal('rq-checkout') || null, reason: _rqVal('rq-reason')};
      }
    }
  };

  function essOpenRequestModal(kind, prefill) {
    var f = REQ_FORMS[kind];
    if (!f) return;
    _essReqKind = kind;
    document.getElementById('req-modal-title').textContent = f.title;
    document.getElementById('req-form-body').innerHTML = f.html(prefill || {});
    document.getElementById('req-form-err').style.display = 'none';
    var btn = document.getElementById('req-submit-btn');
    btn.disabled = false; btn.textContent = 'Submit Request';
    document.getElementById('req-modal-overlay').classList.add('on');
    var first = document.querySelector('#req-form-body input:not([value]), #req-form-body input, #req-form-body select');
    if (first) first.focus();
  }
  function essCloseRequestModal() {
    var o = document.getElementById('req-modal-overlay');
    if (o) o.classList.remove('on');
  }
  async function essSubmitRequest() {
    var f = REQ_FORMS[_essReqKind];
    if (!f) return;
    var err = document.getElementById('req-form-err'), btn = document.getElementById('req-submit-btn');
    err.style.display = 'none';
    var body;
    try { body = f.collect(); } catch (e) { err.textContent = e.message; err.style.display = 'block'; return; }
    btn.disabled = true; btn.textContent = 'Sending…';
    try {
      await essApi('POST', f.path, body);
      essCloseRequestModal();
      essToast(f.ok);
      await essRefreshRequests();
    } catch (e) {
      err.textContent = e.message; err.style.display = 'block';
    }
    btn.disabled = false; btn.textContent = 'Submit Request';
  }
  document.addEventListener('keydown', function(e) {
    if (e.key === 'Escape') { essCloseLeaveModal(); essCloseRequestModal(); }
  });

  // ── Leave form: live day count, balance and overlap check ──
  var _essLeaveBal = {};
  function essUpdateLeavePreview() {
    var el = document.getElementById('leave-preview');
    if (!el) return;
    var type = _rqVal('leave-type'), s = _rqVal('leave-start'), e = _rqVal('leave-end');
    if (!s || !e) { el.style.display = 'none'; return; }
    var days = Math.round((new Date(e + 'T00:00:00') - new Date(s + 'T00:00:00')) / 86400000) + 1;
    var html, warn = false;
    if (days < 1) { html = 'The end date is before the start date.'; warn = true; }
    else {
      html = '<b>' + days + ' day' + (days === 1 ? '' : 's') + '</b>';
      var b = _essLeaveBal[type];
      if (b) {
        html += ' · ' + escHtml(type) + ' balance: <b>' + b.remaining + '</b> of ' + b.entitlement + ' days left';
        if (days > b.remaining) { html += '<br>That is more than your remaining balance — HR may decline it or treat the extra days as unpaid.'; warn = true; }
      }
      var clash = (_essData.leave || []).filter(function(l) { return (l.status === 'pending' || l.status === 'approved') && l.start_date <= e && l.end_date >= s; })[0];
      if (clash) { html += '<br>This overlaps your ' + escHtml(clash.status) + ' leave (' + escHtml(clash.start_date) + ' → ' + escHtml(clash.end_date) + ').'; warn = true; }
    }
    el.className = 'leave-preview' + (warn ? ' warn' : '');
    el.innerHTML = html;
    el.style.display = 'block';
  }

  // ── Contact details (self-edit) ──
  var _essContact = null;
  var _essContactEditing = false;
  async function loadProfileDetails() {
    try { _essContact = await essApi('GET', '/ess/profile-details'); } catch (e) { _essContact = null; }
    renderContactCard();
  }
  function essEditContact(on) { _essContactEditing = on; renderContactCard(); }
  function renderContactCard() {
    var el = document.getElementById('contact-content');
    if (!el) return;
    el.classList.remove('loading');
    var c = _essContact;
    if (!c) { el.innerHTML = '<div class="empty">Failed to load.</div>'; return; }
    if (!c.has_record) { el.innerHTML = '<div class="hint" style="margin:0">Your HR profile hasn’t been completed yet — ask HR to finish it, then you can add your contact details here.</div>'; return; }
    if (!_essContactEditing) {
      el.innerHTML = '<div class="profile-row">' +
        kv('Mobile', escHtml(c.mobile || '—')) + kv('Email', escHtml(c.email || '—')) +
        kv('Address', escHtml(c.address || '—')) + kv('Emergency contact', escHtml(c.emergency_contact || '—')) +
        kv('Emergency mobile', escHtml(c.emergency_mobile || '—')) + '</div>' +
        '<div style="margin-top:16px"><button class="btn-ghost" onclick="essEditContact(true)">Edit contact details</button></div>';
      return;
    }
    var fld = function(id, label, val, ph, type) { return '<div class="field"><label for="' + id + '">' + label + '</label><input id="' + id + '" type="' + (type || 'text') + '" value="' + escHtml(val || '') + '" placeholder="' + ph + '"></div>'; };
    el.innerHTML = '<div id="contact-err" class="err-box"></div>' +
      '<div class="fr2">' + fld('ct-mobile', 'Mobile', c.mobile, '+971 50 000 0000', 'tel') + fld('ct-emg-mobile', 'Emergency mobile', c.emergency_mobile, '+971 50 000 0000', 'tel') + '</div>' +
      fld('ct-emg', 'Emergency contact name', c.emergency_contact, 'Who should we call?') +
      fld('ct-address', 'Address', c.address, 'Home address') +
      '<div class="hint" style="margin:-6px 0 14px">Name, department, salary, bank details and documents can only be changed by HR.</div>' +
      '<div style="display:flex;gap:10px"><button id="ct-save" class="btn-login" style="width:auto;padding:10px 20px" onclick="essSaveContact()">Save changes</button><button class="btn-ghost" onclick="essEditContact(false)">Cancel</button></div>';
  }
  async function essSaveContact() {
    var err = document.getElementById('contact-err'), btn = document.getElementById('ct-save');
    err.style.display = 'none';
    btn.disabled = true; btn.textContent = 'Saving…';
    try {
      await essApi('PUT', '/ess/profile-details', {mobile: _rqVal('ct-mobile'), emergency_mobile: _rqVal('ct-emg-mobile'), emergency_contact: _rqVal('ct-emg'), address: _rqVal('ct-address')});
      _essContactEditing = false;
      essToast('Contact details updated');
      await loadProfileDetails();
    } catch (e) {
      err.textContent = e.message; err.style.display = 'block';
      btn.disabled = false; btn.textContent = 'Save changes';
    }
  }

  // ── Documents (visa / passport / ID expiry) ──
  var _essDocs = [];
  function _docPill(d) {
    var n = d.days_left == null ? 0 : Math.abs(d.days_left);
    if (d.state === 'expired') return '<span class="doc-pill expired">Expired ' + n + ' day' + (n === 1 ? '' : 's') + ' ago</span>';
    if (d.state === 'valid') return '<span class="doc-pill valid">Valid</span>';
    return '<span class="doc-pill ' + d.state + '">' + n + ' day' + (n === 1 ? '' : 's') + ' left</span>';
  }
  async function loadDocuments() {
    try { _essDocs = await essApi('GET', '/ess/documents'); } catch (e) { _essDocs = null; }
    renderDocuments();
    if (_essDocs) { renderDocAlert(); refreshNotifications(); }
  }
  function renderDocuments() {
    var el = document.getElementById('docs-content');
    if (!el) return;
    el.classList.remove('loading');
    if (!_essDocs) { el.innerHTML = '<div class="empty">Failed to load.</div>'; return; }
    var have = _essDocs.filter(function(d) { return d.state !== 'missing'; });
    var missing = _essDocs.filter(function(d) { return d.state === 'missing'; });
    if (!have.length) { el.innerHTML = emptyState('check', 'No documents on file', 'Your visa, passport and ID expiry dates appear here once HR adds them.'); return; }
    el.innerHTML = have.map(function(d) {
      return '<div class="doc-row"><div><div class="doc-name">' + escHtml(d.label) + '</div><div class="doc-date">Expires ' + escHtml(new Date(d.expiry + 'T00:00:00').toLocaleDateString('en-US', {day: 'numeric', month: 'short', year: 'numeric'})) + '</div></div>' + _docPill(d) + '</div>';
    }).join('') +
      (missing.length ? '<div class="hint">Not on file: ' + escHtml(missing.map(function(d) { return d.label; }).join(', ')) + '</div>' : '') +
      '<div class="hint">To update a document, please contact HR.</div>';
  }
  function renderDocAlert() {
    var el = document.getElementById('dash-doc-alert-card');
    if (!el) return;
    var urgent = (_essDocs || []).filter(function(d) { return d.state === 'expired' || d.state === 'critical' || d.state === 'soon'; });
    if (!urgent.length) { el.style.display = 'none'; return; }
    el.style.display = '';
    el.innerHTML = '<div class="ess-alert-title"><svg viewBox="0 0 14 14" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.4"><path d="M7 1.5L1 12.5h12L7 1.5z"/><path d="M7 6v3M7 10.5v.5"/></svg>Document Alerts</div>' +
      urgent.map(function(d) {
        var n = d.days_left == null ? 0 : Math.abs(d.days_left);
        var txt = d.state === 'expired' ? 'Expired ' + n + 'd ago' : n + ' day' + (n === 1 ? '' : 's') + ' left';
        return '<div class="ess-alert-row' + (d.state === 'soon' ? ' warn' : '') + '"><div class="ess-alert-dot"></div><div class="ess-alert-name">' + escHtml(d.label) + '</div><div class="ess-alert-cnt">' + txt + '</div></div>';
      }).join('') + '<div class="hint" style="margin-top:8px">Contact HR to renew. Tap for details.</div>';
  }

  function showApp() {
    document.getElementById('login-screen').style.display = 'none';
    document.getElementById('app-screen').style.display = 'flex';
    var dateEl = document.getElementById('ess-tb-date');
    if (dateEl) dateEl.textContent = new Date().toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric'});
    loadProfile();
    loadAttendance();
    loadPayslips();
    loadLeave();
    loadLeaveBalance();
    loadAnnouncements();
    loadRota();
    loadTasks();
    loadRoleAndPermissions();
    loadTeamToday();
    loadHolidayCalendar();
    essRefreshRequests();
    loadProfileDetails();
    loadDocuments();
    essLoadMobile();
  }

  // ── Role-based view — the same token also carries RBAC role/permission
  // data (see app/routers/hr_access.py /hr/me), so an employee with an
  // elevated role (HR Manager, Administrator, Manager) sees an extra
  // "Team" tab here, without needing a separate HR-only login.
  var ESS_PERMISSIONS = [];
  var ESS_DEPARTMENT_SCOPE = [];

  async function loadRoleAndPermissions() {
    try {
      var r = await fetch(apiBase() + '/hr/me', {headers: essHeaders()});
      if (!r.ok) return; // role/permissions are additive — a failure here shouldn't block the rest of ESS
      var d = await r.json();
      ESS_PERMISSIONS = d.permissions || [];
      ESS_DEPARTMENT_SCOPE = d.department_scope || [];
      var badge = document.getElementById('ess-role-badge');
      if (badge) badge.textContent = d.role_name || 'Employee';
      // The Team tab's "My Team Today" card is visible to everyone; a
      // company-wide GPS permission or a department-scoped role
      // additionally unlocks the manager-only cards further down --
      // loadTeamTab() below shows only those. Team Holiday Calendar lives
      // under the Leave tab instead (loadHolidayCalendar(), wired to the
      // Leave nav button) since it's about leave, not team status.
    } catch(e) { /* role badge just stays default on failure */ }
  }

  function loadTeamTab() {
    loadTeamToday();
    if (ESS_DEPARTMENT_SCOPE.length) loadDeptTeamView(); else document.getElementById('dept-team-card').style.display = 'none';
    if (ESS_PERMISSIONS.includes('hr:view_all_attendance')) { document.getElementById('gps-team-card').style.display = ''; loadTeamView(); }
    else document.getElementById('gps-team-card').style.display = 'none';
  }

  var TEAM_STATUS_META = {
    present: {label: 'Present', color: '#10b981', bg: 'rgba(16,185,129,.12)'},
    absent: {label: 'Absent', color: '#ef4444', bg: 'rgba(239,68,68,.12)'},
    leave: {label: 'On Leave', color: '#f59e0b', bg: 'rgba(245,158,11,.12)'},
    holiday: {label: 'Holiday', color: '#8b5cf6', bg: 'rgba(139,92,246,.12)'},
    weekend: {label: 'Weekend', color: '#64748b', bg: 'rgba(100,116,139,.12)'},
    off: {label: 'Day Off', color: '#94a3b8', bg: 'rgba(148,163,184,.16)'}
  };

  // Dashboard banner: the caller's own department today -- counts, a presence bar, and who's in.
  function renderDashTeamAtt(rows) {
    var el = document.getElementById('dash-team-att');
    if (!el) return;
    if (!rows.length) { el.innerHTML = '<div class="empty" style="padding:6px 0">No teammates found in your department.</div>'; return; }
    var cnt = function(st) { return rows.filter(function(e) { return e.status === st; }).length; };
    var present = cnt('present'), leave = cnt('leave'), absent = cnt('absent'), off = cnt('off');
    var expected = present + leave + absent;
    var offDay = rows.every(function(e) { return e.status === 'weekend' || e.status === 'holiday'; });
    var pct = expected ? Math.round(present / expected * 100) : 0;
    var caption = offDay ? (rows[0].status === 'holiday' ? 'Public holiday — no attendance expected' : 'Weekend — no attendance expected')
      : present + ' of ' + expected + ' in today (' + pct + '%)' + (off ? ' · ' + off + ' on a day off' : '');
    var rank = {present: 0, leave: 1, absent: 2, off: 3};
    var sorted = rows.slice().sort(function(a, b) { return (rank[a.status] == null ? 3 : rank[a.status]) - (rank[b.status] == null ? 3 : rank[b.status]); });
    var list = sorted.slice(0, 5).map(function(e) {
      var meta = TEAM_STATUS_META[e.status] || TEAM_STATUS_META.absent;
      var name = escHtml(e.full_name) + (e.is_me ? ' <span style="color:var(--text3)">(You)</span>' : '');
      var sub = escHtml(e.designation || '') + (e.status === 'present' && e.check_in ? ' · in ' + escHtml(e.check_in) : '');
      return '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px;padding:7px 0;border-bottom:1px solid var(--border)"><div style="min-width:0"><div style="font-size:13px;font-weight:600;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + name + '</div><div style="font-size:11px;color:var(--text3);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">' + sub + '</div></div>' +
        '<span style="display:inline-block;padding:2px 8px;border-radius:99px;font-size:10.5px;font-weight:600;white-space:nowrap;background:' + meta.bg + ';color:' + meta.color + '">' + meta.label + '</span></div>';
    }).join('');
    el.innerHTML =
      '<div class="att-mini" style="margin-top:0;padding-top:0;border-top:none;grid-template-columns:repeat(4,1fr)">' +
        '<div><b style="color:var(--green)">' + present + '</b><span>Present</span></div>' +
        '<div><b style="color:var(--amber)">' + leave + '</b><span>On leave</span></div>' +
        '<div><b style="color:var(--red)">' + absent + '</b><span>Absent</span></div>' +
        '<div><b style="color:var(--text2)">' + off + '</b><span>Day off</span></div></div>' +
      '<div class="lb-bar" style="margin:10px 0 4px"><div class="lb-bar-fill" style="width:' + pct + '%;background:var(--green)"></div></div>' +
      '<div class="hint" style="margin:0 0 6px">' + caption + '</div>' + list +
      '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px"><span class="hint" style="margin:0">' + (rows.length > 5 ? '+' + (rows.length - 5) + ' more' : rows.length + ' in your team') + '</span>' +
      '<a style="font-size:12px;color:var(--accent-text);cursor:pointer;font-weight:600" onclick="essGoTab(\'team\')">View team →</a></div>';
  }

  async function loadTeamToday() {
    var body = document.getElementById('team-today-body');
    var dashBody = document.getElementById('dash-team-att');
    if (!body && !dashBody) return;
    try {
      var r = await fetch(apiBase() + '/ess/team/today', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      var rows = await r.json();
      var sumEl = document.getElementById('team-summary');
      if (sumEl) {
        var cnt = function(st) { return rows.filter(function(e) { return e.status === st; }).length; };
        var stat = function(label, val, sub) { return '<div class="att-stat"><div class="att-stat-label">' + label + '</div><div class="att-stat-val">' + val + '</div><div class="att-stat-sub">' + sub + '</div></div>'; };
        sumEl.innerHTML = stat('Present Today', cnt('present'), 'In your department') + stat('Team Size', rows.length, 'Active colleagues') + stat('On Leave', cnt('leave'), 'Approved leave today') + stat('Day Off', cnt('off'), 'Off on the rota today') + stat('Absent', cnt('absent'), 'No punch yet');
      }
      if (body) {
        body.innerHTML = rows.length ? rows.map(function(e) {
          var meta = TEAM_STATUS_META[e.status] || TEAM_STATUS_META.absent;
          var name = escHtml(e.full_name) + (e.is_me ? ' <span style="color:var(--text3)">(You)</span>' : '');
          var pill = '<span style="display:inline-block;padding:2px 10px;border-radius:99px;font-size:11px;font-weight:600;background:' + meta.bg + ';color:' + meta.color + '">' + meta.label + '</span>';
          return '<tr><td>' + name + '</td><td>' + escHtml(e.designation || '') + '</td><td>' + pill + '</td><td>' + (e.check_in || '—') + '</td></tr>';
        }).join('') : '<tr><td colspan="4" class="empty">No teammates found in your department.</td></tr>';
      }
      renderDashTeamAtt(rows);
    } catch(e) {
      if (body) body.innerHTML = '<tr><td colspan="4" class="empty">Failed to load.</td></tr>';
      if (dashBody) dashBody.innerHTML = '<div class="empty">Failed to load.</div>';
    }
  }

  var _essHolidaysCache = null;
  var _holidayCalMonth = null; // 'YYYY-MM', the month the grid is currently showing
  var _holidayCalLeaveCache = {}; // month -> team leave rows, so paging back and forth doesn't re-fetch

  async function loadHolidayCalendar() {
    var grid = document.getElementById('holiday-cal-grid');
    var nextHolidayEl = document.getElementById('dash-next-holiday');
    if (!grid && !nextHolidayEl) return;
    try {
      var r = await fetch(apiBase() + '/ess/holidays', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      _essHolidaysCache = await r.json();
      renderAttCalendar();
      var todayStr = _todayISO();
      // Nearest upcoming holiday, shown as a one-line teaser on the
      // Dashboard tab -- rows are already sorted by date ascending (see
      // GET /ess/holidays), so the first one >= today is the next one.
      if (nextHolidayEl) {
        var upcoming = _essHolidaysCache.filter(function(h) { return h.date >= todayStr; })[0];
        if (upcoming) {
          var upLabel = new Date(upcoming.date + 'T00:00:00').toLocaleDateString('en-US', {day: '2-digit', month: 'short'});
          nextHolidayEl.textContent = 'Next holiday: ' + upcoming.name + ' — ' + upLabel;
        } else {
          nextHolidayEl.textContent = _essHolidaysCache.length ? 'No upcoming holidays' : 'No holidays configured yet';
        }
      }
      if (grid) await renderHolidayCalendarGrid();
    } catch(e) {
      if (grid) grid.innerHTML = '<div class="empty" style="padding:12px 0">Failed to load.</div>';
      if (nextHolidayEl) nextHolidayEl.textContent = 'Failed to load';
    }
  }

  function shiftHolidayCalendarMonth(delta) {
    var d = new Date((_holidayCalMonth || _todayISO().slice(0, 7)) + '-01T00:00:00');
    d.setMonth(d.getMonth() + delta);
    _holidayCalMonth = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
    renderHolidayCalendarGrid();
  }

  // Real month-grid calendar view (not a table) -- overlays company
  // holidays (GET /ess/holidays, cached, not month-scoped) with this
  // employee's own department peers' approved leave for the month being
  // viewed (GET /ess/team/leave?month=, fetched per-month and cached) so
  // "already taken" team leave shows directly on the day it falls on,
  // not just company-wide public holidays.
  async function renderHolidayCalendarGrid() {
    var grid = document.getElementById('holiday-cal-grid');
    if (!grid) return;
    if (!_holidayCalMonth) _holidayCalMonth = _todayISO().slice(0, 7);
    var labelEl = document.getElementById('holiday-cal-month-label');
    if (labelEl) labelEl.textContent = new Date(_holidayCalMonth + '-01T00:00:00').toLocaleDateString('en-US', {month: 'long', year: 'numeric'});

    var leaveRows = _holidayCalLeaveCache[_holidayCalMonth];
    if (!leaveRows) {
      try {
        var r = await fetch(apiBase() + '/ess/team/leave?month=' + encodeURIComponent(_holidayCalMonth), {headers: essHeaders()});
        if (r.status === 401) { essLogout(); return; }
        leaveRows = r.ok ? await r.json() : [];
      } catch(e) { leaveRows = []; }
      _holidayCalLeaveCache[_holidayCalMonth] = leaveRows;
    }

    var holidaysByDate = {};
    (_essHolidaysCache || []).forEach(function(h) { holidaysByDate[h.date] = h; });
    var leaveByDate = {};
    leaveRows.forEach(function(lr) {
      var d = new Date(lr.start_date + 'T00:00:00');
      var end = new Date(lr.end_date + 'T00:00:00');
      var guard = 0;
      while (d <= end && guard < 366) {
        var key = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
        (leaveByDate[key] = leaveByDate[key] || []).push(lr);
        d.setDate(d.getDate() + 1);
        guard++;
      }
    });

    var parts = _holidayCalMonth.split('-');
    var y = Number(parts[0]), m = Number(parts[1]);
    var firstDow = (new Date(y, m - 1, 1).getDay() + 6) % 7; // Monday-first
    var daysInMonth = new Date(y, m, 0).getDate();
    var todayStr = _todayISO();

    var weekdayLabels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    var html = weekdayLabels.map(function(w) { return '<div class="hcal-weekday">' + w + '</div>'; }).join('');
    for (var i = 0; i < firstDow; i++) html += '<div class="hcal-cell hcal-empty"></div>';
    for (var day = 1; day <= daysInMonth; day++) {
      var dateStr = _holidayCalMonth + '-' + String(day).padStart(2, '0');
      var holiday = holidaysByDate[dateStr];
      var onLeave = leaveByDate[dateStr] || [];
      var cls = 'hcal-cell' + (dateStr === todayStr ? ' hcal-today' : '') + (holiday ? ' hcal-is-holiday' : '');
      var inner = '<div class="hcal-daynum">' + day + '</div>';
      if (holiday) inner += '<div class="hcal-chip hcal-chip-holiday" title="' + escHtml(holiday.name) + '">' + escHtml(holiday.name) + '</div>';
      onLeave.slice(0, 2).forEach(function(lr) {
        var first = (lr.employee_name || '').split(' ')[0];
        inner += '<div class="hcal-chip hcal-chip-leave" title="' + escHtml(lr.employee_name) + ' — ' + escHtml(lr.leave_type) + '">' + escHtml(first) + (lr.is_me ? ' (You)' : '') + '</div>';
      });
      if (onLeave.length > 2) inner += '<div class="hcal-more">+' + (onLeave.length - 2) + ' more</div>';
      html += '<div class="' + cls + '" title="Click to request leave for ' + dateStr + '" onclick="essOpenLeaveModal(\'' + dateStr + '\')">' + inner + '</div>';
    }
    grid.innerHTML = html;
    essMakeKeyboardOperable(grid);
  }

  async function loadDeptTeamView() {
    var card = document.getElementById('dept-team-card');
    var body = document.getElementById('dept-team-body');
    card.style.display = '';
    document.getElementById('dept-team-title').textContent = 'Department Team — ' + ESS_DEPARTMENT_SCOPE.join(', ');
    try {
      var r = await fetch(apiBase() + '/ess/team', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      if (r.status === 403) { body.innerHTML = '<tr><td colspan="4" class="empty">Your role is not scoped to any department.</td></tr>'; return; }
      var rows = await r.json();
      if (!rows.length) { body.innerHTML = '<tr><td colspan="4" class="empty">No employees found in this department.</td></tr>'; return; }
      body.innerHTML = rows.map(function(e) {
        var st = e.status !== 'active' ? '<span class="badge inactive">' + escHtml(e.status) + '</span>' : '<span class="badge">Active</span>';
        return '<tr><td>' + escHtml(e.full_name) + '</td><td>' + escHtml(e.department) + '</td><td>' + escHtml(e.designation) + '</td><td>' + st + '</td></tr>';
      }).join('');
    } catch(e) { body.innerHTML = '<tr><td colspan="4" class="empty">Failed to load.</td></tr>'; }
  }

  async function loadTeamView() {
    var body = document.getElementById('team-body');
    try {
      var r = await fetch(apiBase() + '/hr/live-locations', {headers: essHeaders()});
      if (r.status === 401) { essLogout(); return; }
      if (r.status === 403) { body.innerHTML = '<tr><td colspan="4" class="empty">Your role does not have team visibility.</td></tr>'; return; }
      var rows = await r.json();
      if (!rows.length) { body.innerHTML = '<tr><td colspan="4" class="empty">No one is currently checked in.</td></tr>'; return; }
      body.innerHTML = rows.map(function(p) {
        var geo = p.inside_geofence ? '<span class="dir-in">Inside</span>' : '<span class="dir-out">Outside</span>';
        return '<tr><td>' + escHtml(p.employee_name) + '</td><td>' + escHtml(p.check_in.substring(0,16).replace('T',' ')) + '</td><td>' + geo + '</td><td>' + escHtml(p.last_ping.substring(0,16).replace('T',' ')) + '</td></tr>';
      }).join('');
    } catch(e) { body.innerHTML = '<tr><td colspan="4" class="empty">Failed to load.</td></tr>'; }
  }


  // ── Voice (Phase 4) ──
  // Topbar mic / Ctrl+Space. Pages open directly; requests open their usual
  // form pre-filled for the employee to check and submit; balance / next-shift
  // questions are answered from the employee's own data. Nothing is submitted.
  var _essVoiceTimer = null;
  // Same assistant-style popup as the main app (vc-* classes, see ess.css).
  function _essVoicePop() {
    var pop = document.getElementById('ess-voice-pop');
    if (pop) return pop;
    pop = document.createElement('div');
    pop.id = 'ess-voice-pop';
    pop.className = 'vc-pop';
    pop.setAttribute('role', 'status');
    pop.setAttribute('aria-live', 'polite');
    pop.hidden = true;
    pop.innerHTML =
      '<div class="vc-head">' +
        '<span class="vc-avatar" aria-hidden="true"><svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><path d="M12 2l1.9 5.6L19.5 9.5l-5.6 1.9L12 17l-1.9-5.6L4.5 9.5l5.6-1.9z"/><path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" opacity=".7"/></svg></span>' +
        '<div class="vc-head-text"><b>TaxFlow AI</b><span id="ess-voice-mode"></span></div>' +
        '<button type="button" class="vc-pop-close" onclick="essVoiceClose()" aria-label="Close">×</button>' +
      '</div>' +
      '<div class="vc-stage">' +
        '<div class="vc-orb" aria-hidden="true"><span></span><span></span><span></span><span></span><span></span></div>' +
        '<div class="vc-stage-text" id="ess-voice-stage-text"></div>' +
        '<button type="button" class="vc-done" onclick="window.VoiceInput&&VoiceInput.stop()">Done</button>' +
      '</div>' +
      '<div class="vc-convo" id="ess-voice-convo">' +
        '<div class="vc-msg vc-me" id="ess-voice-heard"></div>' +
        '<div class="vc-msg vc-ai" id="ess-voice-ai"><div class="vc-typing" id="ess-voice-typing"><i></i><i></i><i></i></div><div class="vc-ai-text" id="ess-voice-status"></div></div>' +
      '</div>' +
      '<div class="vc-actions" id="ess-voice-actions"><button type="button" class="vc-retry" onclick="essVoice()"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3"/><path d="M5 10a7 7 0 0 0 14 0"/><path d="M12 17v4"/></svg>Try again</button></div>' +
      '<div class="vc-foot"><span>Try “Apply annual leave next Monday to Wednesday”, “Request 2 hours overtime yesterday”, “What’s my leave balance?”</span></div>';
    document.body.appendChild(pop);
    return pop;
  }
  // retry: show "Try again" (and stay open) after a miss or an error.
  function _essVoiceShow(status, heard, autoHide, retry) {
    var pop = _essVoicePop();
    clearTimeout(_essVoiceTimer);
    pop.hidden = false;
    var mode = /^Listening/.test(status) ? 'listening' : /^(Transcribing|Working)/.test(status) ? 'thinking' : 'answer';
    pop.dataset.mode = mode;
    document.getElementById('ess-voice-mode').textContent = mode === 'listening' ? 'Listening…' : mode === 'thinking' ? 'Thinking…' : 'Voice assistant';
    document.getElementById('ess-voice-stage-text').textContent = status;
    document.getElementById('ess-voice-typing').hidden = mode !== 'thinking';
    document.getElementById('ess-voice-status').textContent = mode === 'answer' ? status : '';
    var h = document.getElementById('ess-voice-heard');
    h.textContent = heard || '';
    h.hidden = !heard;
    document.getElementById('ess-voice-ai').hidden = mode === 'listening';
    document.getElementById('ess-voice-convo').hidden = mode === 'listening' && !heard;
    var canRetry = !!retry && mode === 'answer';
    document.getElementById('ess-voice-actions').hidden = !canRetry;
    if (autoHide && !canRetry) _essVoiceTimer = setTimeout(essVoiceClose, autoHide);
  }
  // Speech ended with nothing heard: the popup would otherwise stay on "Listening…".
  function _essNoSpeech() {
    setTimeout(function() {
      var pop = document.getElementById('ess-voice-pop');
      if (pop && !pop.hidden && pop.dataset.mode === 'listening') _essVoiceShow('I didn’t catch that — tap Try again and speak', null, 0, true);
    }, 0);
  }
  function essVoiceClose() {
    clearTimeout(_essVoiceTimer);
    if (window.VoiceInput && VoiceInput.isActive()) VoiceInput.cancel();
    var pop = document.getElementById('ess-voice-pop');
    if (pop) pop.hidden = true;
    _essMicState('idle');
  }
  function _essMicState(state) {
    ['ess-mic-btn', 'm-tab-mic'].forEach(function(id) {
      var btn = document.getElementById(id);
      if (!btn) return;
      btn.classList.toggle('listening', state === 'listening');
      btn.classList.toggle('processing', state === 'processing');
      btn.setAttribute('aria-pressed', state === 'listening' ? 'true' : 'false');
    });
  }
  async function _essTranscribe(blob, lang) {
    var form = new FormData();
    form.append('file', blob, 'voice.' + ((blob.type.split('/')[1] || 'webm').split(';')[0]));
    form.append('lang', lang);
    var t = localStorage.getItem(ESS_TOKEN_KEY);
    var r = await fetch(apiBase() + '/ess/voice-transcribe', {method: 'POST', headers: t ? {Authorization: 'Bearer ' + t} : {}, body: form});
    var data = null;
    try { data = await r.json(); } catch (e) {}
    if (!r.ok) throw new Error((data && data.detail) || 'Transcription failed');
    return data.text;
  }
  // Company voice switch (Settings > AI & Voice). Voice is English-only.
  var _essVoiceSettings = null;
  async function essApplyVoiceSettings() {
    try {
      _essVoiceSettings = await essApi('GET', '/ess/voice-settings');
    } catch (e) { return null; }
    ['ess-mic-btn', 'm-tab-mic'].forEach(function(id) {
      var btn = document.getElementById(id);
      if (btn) btn.hidden = !_essVoiceSettings.enabled;
    });
    return _essVoiceSettings;
  }
  if (localStorage.getItem(ESS_TOKEN_KEY)) setTimeout(essApplyVoiceSettings, 1200);
  async function essVoice() {
    if (!window.VoiceInput || !VoiceInput.supported()) { essToast('Voice input is not supported in this browser', 'err'); return; }
    // First use after signing in: settings weren't loaded at page start.
    if (!_essVoiceSettings && !(await essApplyVoiceSettings())) return;
    if (!_essVoiceSettings.enabled) { essToast('Voice is turned off for your company', 'err'); return; }
    if (VoiceInput.isActive()) { VoiceInput.stop(); return; }
    if (window.VoiceOutput) VoiceOutput.stop();
    var lang = VoiceInput.getLang();
    VoiceInput.start({
      lang: lang,
      transcribe: _essTranscribe,
      onState: function(s) {
        _essMicState(s);
        if (s === 'listening') _essVoiceShow('Listening… tap the mic again when done');
        else if (s === 'processing') _essVoiceShow('Transcribing…');
        else _essNoSpeech();
      },
      onInterim: function(text) {
        var h = document.getElementById('ess-voice-heard');
        if (!h) return;
        h.textContent = text; h.hidden = !text;
        document.getElementById('ess-voice-convo').hidden = !text;
      },
      onText: function(text) { essRunVoice(text, lang); },
      onError: function(msg) { _essVoiceShow(msg, null, 0, true); }
    });
  }
  function _essVoiceSet(id, value) {
    var el = document.getElementById(id);
    if (!el || value == null || value === '') return;
    if (el.tagName === 'SELECT') {
      var opt = Array.prototype.find.call(el.options, function(o) { return o.value === value || o.textContent.trim() === value; });
      if (!opt) return;
      el.value = opt.value;
    } else {
      el.value = value;
    }
    el.dispatchEvent(new Event('input', {bubbles: true}));
    el.dispatchEvent(new Event('change', {bubbles: true}));
    el.classList.add('vf-filled');
  }
  var _ESS_VOICE_FORMS = {
    request_overtime: function(f) {
      essOpenRequestModal('overtime', {date: f.date, hours: f.hours, type: f.ot_type});
      _essVoiceSet('rq-login', f.login); _essVoiceSet('rq-logout', f.logout);
      if (f.hours) _essVoiceSet('rq-hours', f.hours);
      _essVoiceSet('rq-ottype', f.ot_type); _essVoiceSet('rq-reason', f.reason);
    },
    request_correction: function(f) {
      essOpenRequestModal('correction', {date: f.date, checkin: f.checkin});
      _essVoiceSet('rq-checkout', f.checkout); _essVoiceSet('rq-reason', f.reason);
    },
    request_advance: function(f) {
      essOpenRequestModal('advance', {});
      _essVoiceSet('rq-amount', f.amount); _essVoiceSet('rq-reason', f.reason);
    },
    request_loan: function(f) {
      essOpenRequestModal('loan', {});
      _essVoiceSet('rq-loantype', f.loan_type); _essVoiceSet('rq-amount', f.amount);
      _essVoiceSet('rq-months', f.months); _essVoiceSet('rq-reason', f.reason);
      if (typeof essLoanEmi === 'function') essLoanEmi();
    },
    apply_leave: function(f) {
      essOpenLeaveModal(f.start_date || '');
      _essVoiceSet('leave-type', f.leave_type); _essVoiceSet('leave-end', f.end_date || f.start_date);
      _essVoiceSet('leave-reason', f.reason);
      essUpdateLeavePreview();
    }
  };
  async function essRunVoice(text, lang) {
    var transcript = String(text || '').trim();
    if (!transcript) return;
    _essVoiceShow('Working it out…', transcript);
    var r;
    try {
      r = await essApi('POST', '/ess/voice-intent', {transcript: transcript, lang: lang === 'ar-AE' ? 'ar' : 'en'});
    } catch (e) {
      // Company has the AI module off: hide the mic rather than fail every time.
      if (/isn.t enabled/.test(e.message)) { var mb = document.getElementById('ess-mic-btn'); if (mb) mb.hidden = true; }
      _essVoiceShow(e.message, transcript, 0, true);
      return;
    }
    if (r.answer) {
      _essVoiceShow(r.answer, transcript);
      if (window.VoiceOutput) VoiceOutput.speak(r.answer);
      return;
    }
    if (r.intent === 'navigate' && r.page) {
      essGoTab(r.page);
      _essVoiceShow('Opening ' + r.page, transcript, 2500);
      return;
    }
    var fill = _ESS_VOICE_FORMS[r.intent];
    if (fill) {
      fill(r.fields || {});
      var n = Object.keys(r.fields || {}).length;
      _essVoiceShow((n ? 'Filled ' + n + ' detail' + (n === 1 ? '' : 's') + ' — ' : '') + 'check the form and tap Submit', transcript, 6000);
      return;
    }
    _essVoiceShow('Sorry, I didn’t catch a request in that.', transcript, 0, true);
  }
  document.addEventListener('keydown', function(e) {
    if (e.ctrlKey && !e.altKey && !e.metaKey && !e.shiftKey && e.code === 'Space' && document.getElementById('ess-mic-btn') && localStorage.getItem(ESS_TOKEN_KEY)) {
      e.preventDefault();
      essVoice();
    } else if (e.key === 'Escape' && document.getElementById('ess-voice-pop') && !document.getElementById('ess-voice-pop').hidden) {
      essVoiceClose();
    }
  });

  // ── Mobile app layer (phones ≤ 760px) ──
  // Home / Attend / Requests / Me tab bar, a one-tap check-in, bottom-sheet
  // forms and "Add to Home Screen". Reuses the same endpoints and functions
  // as the desktop views; desktop/tablet layouts are untouched.
  var _essMq = window.matchMedia('(max-width: 760px)');
  var _essCheck = null;
  var _essInstallEvt = null;
  var _M_TAB_PARENT = {dashboard: 'dashboard', attendance: 'attendance', gps: 'attendance', requests: 'requests'};
  function essIsMobile() { return _essMq.matches; }
  function _mSet(id, text) { var el = document.getElementById(id); if (el) el.textContent = text; }
  function _mISO(d) { return d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0'); }
  function _mInitials(name) { return String(name || '?').split(/\s+/).map(function(w) { return w[0] || ''; }).join('').slice(0, 2).toUpperCase(); }
  function _mHours(mins) { if (!(mins > 0)) return '0h'; var h = Math.floor(mins / 60), m = Math.round(mins % 60); return h ? h + 'h' + (m ? ' ' + m + 'm' : '') : Math.max(1, m) + 'm'; }
  function _mLocalHHMM(iso) { var d = new Date(iso); return isNaN(d) ? '' : d.toTimeString().slice(0, 5); }
  function _mMinutes(t) { var p = String(t || '').split(':'); return p.length >= 2 ? (+p[0]) * 60 + (+p[1]) : null; }

  function essSyncTabbar(id) {
    var parent = _M_TAB_PARENT[id] || 'profile';
    document.querySelectorAll('.m-tabbar [data-tab]').forEach(function(b) {
      var on = b.getAttribute('data-tab') === parent;
      b.classList.toggle('on', on);
      if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    var c = document.querySelector('.content');
    if (c && essIsMobile()) c.scrollTop = 0;
  }

  // Per-day first-in / last-out from the raw punch list.
  function _mDays(punches) {
    var days = {};
    (punches || []).forEach(function(p) {
      var d = p.punch_date, t = String(p.punch_time || '').substring(11, 16);
      if (!d || !t) return;
      var day = days[d] || (days[d] = {inT: null, outT: null});
      var dir = String(p.direction || '').toLowerCase();
      if (dir === 'out') { if (!day.outT || t > day.outT) day.outT = t; }
      else if (!day.inT || t < day.inT) day.inT = t;
    });
    return days;
  }

  async function _mLoadCheck() {
    try { _essCheck = await essApi('GET', '/hr/dashboard'); } catch (e) { _essCheck = null; }
    var checked = !!(_essCheck && _essCheck.checked_in);
    var label = checked ? 'Check out' : 'Check in';
    _mSet('m-shift-btn', label);
    _mSet('m-att-btn-label', label);
    var btn = document.getElementById('m-att-btn');
    if (btn) btn.classList.toggle('out', checked);
    var since = _essCheck && _essCheck.check_in_at ? _mLocalHHMM(_essCheck.check_in_at) : '';
    _mSet('m-shift-state', checked ? 'Checked in' + (since ? ' at ' + since : '') : 'Not checked in yet');
  }

  async function _mLoadToday() {
    var punches = [];
    try { punches = await essApi('GET', '/ess/attendance'); } catch (e) {}
    var days = _mDays(Array.isArray(punches) ? punches : (punches && punches.rows) || []);
    var today = _mISO(new Date()), t = days[today] || (days[today] = {inT: null, outT: null});
    ((_essCheck && _essCheck.today_sessions) || []).forEach(function(s) {
      var i = _mLocalHHMM(s.check_in), o = s.check_out ? _mLocalHHMM(s.check_out) : null;
      if (i && (!t.inT || i < t.inT)) t.inT = i;
      if (o && (!t.outT || o > t.outT)) t.outT = o;
    });
    if (_essCheck && _essCheck.checked_in) t.outT = null;  // still on the clock
    _mSet('m-att-in', t.inT || '--:--');
    _mSet('m-att-out', t.outT || '--:--');
    var endT = t.outT || (t.inT ? new Date().toTimeString().slice(0, 5) : null);
    _mSet('m-att-hours', t.inT && endT ? _mHours(_mMinutes(endT) - _mMinutes(t.inT)) : '0h');
    var week = document.getElementById('m-week');
    if (week) {
      var html = '';
      for (var i = 6; i >= 0; i--) {
        var d = new Date(); d.setDate(d.getDate() - i);
        var iso = _mISO(d), day = days[iso], pill;
        if (day && day.inT && day.outT) pill = '<span class="m-pill ok">' + _mHours(_mMinutes(day.outT) - _mMinutes(day.inT)).replace(/ \d+m$/, '') + '</span>';
        else if (day && day.inT) pill = '<span class="m-pill in">In</span>';
        else pill = '<span class="m-pill">–</span>';
        html += '<div><span>' + d.toLocaleDateString('en-US', {weekday: 'narrow'}) + '</span>' + pill + '</div>';
      }
      week.innerHTML = html;
    }
  }

  async function _mLoadShift() {
    var rows = [];
    try { rows = await essApi('GET', '/ess/rota'); } catch (e) {}
    var today = _mISO(new Date());
    var work = (rows || []).filter(function(r) { return !/^(off|leave|holiday)$/i.test(String(r.type || r.code || '')); });
    var todays = work.find(function(r) { return r.date === today; });
    var next = todays || work.find(function(r) { return (r.date || '') > today; });
    if (!next) { _mSet('m-shift-kick', 'Today'); _mSet('m-shift-time', 'No shift scheduled'); return; }
    var label = next.type || next.code || 'Shift';
    var when = next.date === today ? "Today's shift" : 'Next shift · ' + new Date(next.date + 'T00:00:00').toLocaleDateString('en-US', {weekday: 'short', day: 'numeric', month: 'short'});
    _mSet('m-shift-kick', when + ' · ' + label);
    _mSet('m-shift-time', next.start && next.end ? next.start + ' – ' + next.end : label);
  }

  async function _mLoadSummary() {
    try {
      var me = await essApi('GET', '/ess/me');
      var h = new Date().getHours();
      _mSet('m-greet', h < 12 ? 'Good morning' : h < 17 ? 'Good afternoon' : 'Good evening');
      ['m-name', 'm-me-name'].forEach(function(id) { _mSet(id, me.full_name || '—'); });
      ['m-avatar', 'm-me-avatar'].forEach(function(id) { _mSet(id, _mInitials(me.full_name)); });
      _mSet('m-me-role', [me.designation, me.department].filter(function(x) { return x && x !== '-'; }).join(' · ') || me.employee_no || '');
    } catch (e) {}
    try {
      var bal = await essApi('GET', '/ess/leave-balance');
      var a = bal && bal.by_type && bal.by_type['Annual Leave'];
      var el = document.getElementById('m-annual');
      if (el && a) el.innerHTML = escHtml(a.remaining) + ' <small>of ' + escHtml(a.entitlement) + ' days</small>';
    } catch (e) {}
    try {
      var reqs = await essApi('GET', '/ess/requests');
      var pending = (reqs || []).filter(function(r) { return String(r.status || '').toLowerCase() === 'pending'; }).length;
      _mSet('m-pending', String(pending));
    } catch (e) {}
    try {
      var ann = await essApi('GET', '/ess/announcements');
      var box = document.getElementById('m-announce');
      if (box && ann && ann.length) { box.hidden = false; _mSet('m-announce-text', ann[0].title || ann[0].message || ''); }
    } catch (e) {}
    var dot = document.getElementById('ess-notif-dot'), mdot = document.getElementById('m-dot');
    if (dot && mdot) mdot.hidden = dot.style.display === 'none';
  }

  function essLoadMobile() {
    if (!essIsMobile() || !localStorage.getItem(ESS_TOKEN_KEY)) return;
    var cur = document.querySelector('.tab-body.on');
    essSyncTabbar(cur ? cur.id.replace(/^tb-/, '') : 'dashboard');
    _mLoadSummary(); _mLoadShift();
    _mLoadCheck().then(_mLoadToday);
    _mTick();
  }

  function _mTick() {
    var now = new Date();
    _mSet('m-att-clock', now.toTimeString().slice(0, 5));
    _mSet('m-att-date', now.toLocaleDateString('en-US', {weekday: 'long', day: 'numeric', month: 'long'}));
  }
  setInterval(function() { if (essIsMobile()) _mTick(); }, 20000);

  async function essMobileCheck() {
    var checked = !!(_essCheck && _essCheck.checked_in);
    // Checking out ends the working day, so confirm it; checking in is one tap.
    if (checked && !(await essConfirm({title: 'Check out now?', message: 'This records the end of your working day.', okText: 'Check out'}))) return;
    var btns = [document.getElementById('m-shift-btn'), document.getElementById('m-att-btn')];
    btns.forEach(function(b) { if (b) b.disabled = true; });
    _mSet('m-att-btn-label', 'Locating…');
    try {
      if (checked) await gpsCheckOut(); else await gpsCheckIn();
    } finally {
      btns.forEach(function(b) { if (b) b.disabled = false; });
    }
    var err = document.getElementById('gps-err');
    await _mLoadCheck();
    _mLoadToday();
    if (err && err.style.display === 'block') essToast(err.textContent, 'err');
    else essToast(checked ? 'Checked out' : 'Checked in');
  }

  // Leave type as tap-to-pick chips on phones; the <select> stays the source of truth.
  function _mBuildLeaveChips() {
    var sel = document.getElementById('leave-type');
    if (!sel || document.getElementById('m-leave-chips')) return;
    var box = document.createElement('div');
    box.id = 'm-leave-chips';
    box.className = 'm-only m-chips';
    box.setAttribute('role', 'radiogroup');
    box.setAttribute('aria-label', 'Leave type');
    Array.prototype.forEach.call(sel.options, function(o) {
      var b = document.createElement('button');
      b.type = 'button';
      b.textContent = o.textContent.replace(/ Leave$/, '');
      b.setAttribute('role', 'radio');
      b.dataset.value = o.value;
      b.onclick = function() { sel.value = o.value; sel.dispatchEvent(new Event('change', {bubbles: true})); };
      box.appendChild(b);
    });
    sel.parentNode.insertBefore(box, sel.nextSibling);
    sel.addEventListener('change', _mSyncLeaveChips);
  }
  function _mSyncLeaveChips() {
    var sel = document.getElementById('leave-type');
    document.querySelectorAll('#m-leave-chips button').forEach(function(b) {
      var on = sel && b.dataset.value === sel.value;
      b.classList.toggle('on', on);
      b.setAttribute('aria-checked', on ? 'true' : 'false');
    });
  }
  var _essOpenLeaveDesktop = essOpenLeaveModal;
  essOpenLeaveModal = function(prefillDate) { _essOpenLeaveDesktop(prefillDate); _mSyncLeaveChips(); };

  // Add to Home Screen: Android/desktop Chrome fire beforeinstallprompt; iOS
  // Safari has no prompt API, so the menu item explains the Share-sheet step.
  var _essIsIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  function _essShowInstall(show) {
    ['m-install', 'login-install'].forEach(function(id) { var b = document.getElementById(id); if (b) b.hidden = !show; });
  }
  window.addEventListener('beforeinstallprompt', function(e) {
    e.preventDefault();
    _essInstallEvt = e;
    _essShowInstall(true);
  });
  window.addEventListener('appinstalled', function() { _essInstallEvt = null; _essShowInstall(false); });
  function essInstallApp() {
    if (_essInstallEvt) {
      _essInstallEvt.prompt();
      _essInstallEvt.userChoice.then(function(c) { if (c && c.outcome === 'accepted') _essShowInstall(false); });
      _essInstallEvt = null;
      return;
    }
    // No prompt available (iOS, or a browser without one): explain the manual step.
    var msg = _essIsIOS
      ? 'Tap the Share button, then “Add to Home Screen”.'
      : 'Open your browser menu (⋮), then tap “Install app” or “Add to Home screen”.';
    var help = document.getElementById('login-install-help');
    var onLogin = document.getElementById('login-screen') && document.getElementById('login-screen').style.display !== 'none';
    if (help && onLogin) { help.textContent = msg; help.hidden = false; }
    else essToast(msg);
  }
  (function() {
    var standalone = window.navigator.standalone || window.matchMedia('(display-mode: standalone)').matches;
    if (standalone) return;
    // Login screen always offers it on phones (instructions if no prompt); the Me menu only on iOS or once prompted.
    var login = document.getElementById('login-install');
    if (login) login.hidden = false;
    if (_essIsIOS) {
      var b = document.getElementById('m-install');
      if (b) b.hidden = false;
      _mSet('m-install-label', 'Add to Home Screen');
    }
  })();

  _mBuildLeaveChips();
  _essMq.addEventListener('change', function() { essLoadMobile(); });
  // Capture ?c=<company_id> from the link on every load (even if already
  // logged in), and auto-login if a token already exists. Runs last so the
  // voice and mobile code above is defined before showApp() uses it.
  (function(){
    essCompanyId();
    if (localStorage.getItem(ESS_TOKEN_KEY)) showApp();
  })();

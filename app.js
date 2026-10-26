const grid = document.getElementById('grid');
const intervalSel = document.getElementById('interval');
const autosortCheckbox = document.getElementById('autosort');
const adminDlg = document.getElementById('admin');
const zoomDlg = document.getElementById('zoom');
let zoomedImg = null;
let zoomedPlaceholder = null;
const zoomTitle = document.getElementById('zoom-title');
const zoomLiveBtn = document.getElementById('zoom-live');
const cfgRefresh = document.getElementById('cfg-refresh');
const cfgConf = document.getElementById('cfg-conf');
const hostListEl = document.getElementById('host-list');
const newHostInput = document.getElementById('new-host');
const newSnapInput = document.getElementById('new-snap');
const newStreamInput = document.getElementById('new-stream');
const addHostMsg = document.getElementById('add-host-msg');
const DEFAULT_SNAP = '/cgi-bin/snapshot.sh?res=high';
const DEFAULT_STREAM = '/ch0_1.h264';
let timer = null;
let config = { refresh: 10000, min_conf: 18, hosts: [] };
let capsMap = {};
let draftHosts = [];
let latestDetections = {};
let initialized = false;
const liveHosts = new Set();

const normalizeHost = (h) => String(h).trim().replace(/^https?:\/\//i, '').replace(/\/+$/, '');
const normalizeCam = (item) => {
  if (typeof item === 'string') {
    const host = normalizeHost(item);
    return host ? { host, snapshot: DEFAULT_SNAP, stream: DEFAULT_STREAM } : null;
  }
  if (item && typeof item === 'object') {
    const host = normalizeHost(item.host || '');
    if (!host) return null;
    const snapshot = String(item.snapshot || '').trim() || DEFAULT_SNAP;
    const stream = String(item.stream || '').trim() || DEFAULT_STREAM;
    return { host, snapshot, stream };
  }
  return null;
};
const hostIndex = (h) => config.hosts.findIndex(c => c.host === h);

async function loadConfig() {
  const [res, capsRes] = await Promise.all([
    fetch('./config.json', { cache: 'no-store' }).catch(() => fetch('./hosts.json', { cache: 'no-store' })),
    fetch('./capabilities', { cache: 'no-store' }).catch(() => null),
  ]);
  const raw = res.ok ? await res.json() : { refresh: 10000, min_conf: 18, hosts: [] };
  if (capsRes?.ok) capsMap = await capsRes.json();
  const list = (Array.isArray(raw) ? raw : (raw.hosts ?? [])).map(normalizeCam).filter(Boolean);
  config = { refresh: raw.refresh ?? 10000, min_conf: raw.min_conf ?? 18, hosts: list };
  if (!initialized) {
    intervalSel.value = String(config.refresh);
    autosortCheckbox.checked = localStorage.getItem('gatochecker_autosort') === '1';
    initialized = true;
  }
}

async function saveConfig(refresh, min_conf, hosts) {
  const res = await fetch('./config.json', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh, min_conf, hosts })
  });
  if (res.ok) config = { refresh, min_conf, hosts };
  return res.ok;
}

function applyAutoSort() {
  if (!autosortCheckbox.checked) return;
  const cards = [...grid.querySelectorAll('.cam')];
  if (cards.length <= 1) return;
  function getScore(card) {
    const h = card.dataset.host;
    if (liveHosts.has(h)) return { count: 0, maxConf: 0 };
    if (latestDetections[h]?.cats?.length) {
      const cats = latestDetections[h].cats;
      return { count: cats.length, maxConf: Math.max(...cats.map(c => c.conf)) };
    }
    if (card.classList.contains('has-cat')) {
      const text = card.querySelector('.cat-badge')?.textContent || '';
      const m = text.match(/🐱\s*(?:(\d+)\s*·\s*)?(\d+)%/);
      if (m) {
        return { count: m[1] ? Number(m[1]) : 1, maxConf: Number(m[2]) / 100 };
      }
      return { count: 1, maxConf: 0.5 };
    }
    return { count: 0, maxConf: 0 };
  }
  const sortedCards = [...cards].sort((ca, cb) => {
    const sa = getScore(ca);
    const sb = getScore(cb);
    if (sb.count !== sa.count) return sb.count - sa.count;
    if (sa.count > 0 && sb.maxConf !== sa.maxConf) return sb.maxConf - sa.maxConf;
    return hostIndex(ca.dataset.host) - hostIndex(cb.dataset.host);
  });
  const curIds = cards.map(c => c.dataset.host).join(',');
  const newIds = sortedCards.map(c => c.dataset.host).join(',');
  if (curIds !== newIds) grid.append(...sortedCards);
}

let detPollTimer = null;

function renderCardDetections(card) {
  const host = card.dataset.host;
  const img = card.querySelector('img') || (zoomDlg.open && zoomDlg.dataset.host === host ? zoomedImg : null);
  const det = latestDetections[host];
  const cats = (!liveHosts.has(host) && det?.cats) ? det.cats : [];
  card.classList.toggle('has-cat', cats.length > 0);
  const badge = card.querySelector('.cat-badge');
  if (badge) {
    if (cats.length) {
      const maxConf = Math.round(Math.max(...cats.map(c => c.conf)) * 100);
      badge.textContent = `🐱 ${cats.length > 1 ? `${cats.length} · ` : ''}${maxConf}%`;
    } else {
      badge.textContent = '';
    }
  }
  const boxesEl = card.querySelector('.cat-boxes');
  if (!boxesEl) return;
  let ox = 0, oy = 0, sw = 100, sh = 100;
  if (img?.naturalWidth && img?.naturalHeight) {
    const imgRatio = img.naturalWidth / img.naturalHeight;
    const viewRatio = 16 / 9;
    if (imgRatio < viewRatio) {
      sw = (imgRatio / viewRatio) * 100;
      ox = (100 - sw) / 2;
    } else if (imgRatio > viewRatio) {
      sh = (viewRatio / imgRatio) * 100;
      oy = (100 - sh) / 2;
    }
  }
  boxesEl.innerHTML = cats.map(c => {
    const [x, y, w, h] = c.box;
    const top = oy + y * sh;
    const cls = top < 7 ? 'label-below' : 'label-above';
    return `<div class="cat-box" style="left:${ox + x * sw}%;top:${top}%;width:${w * sw}%;height:${h * sh}%"><span class="${cls}">🐱 ${Math.round(c.conf * 100)}%</span></div>`;
  }).join('');
  if (zoomDlg.open && zoomDlg.dataset.host === host) {
    syncZoomDetections(host, img);
  }
}

async function updateDetections() {
  try {
    const res = await fetch('./detections', { cache: 'no-store' });
    if (!res.ok) return;
    latestDetections = await res.json();
    for (const card of grid.querySelectorAll('.cam')) {
      renderCardDetections(card);
    }
    applyAutoSort();
    const anyPending = Object.values(latestDetections).some(d => d?.pending);
    clearTimeout(detPollTimer);
    if (anyPending) {
      detPollTimer = setTimeout(updateDetections, 1000);
    }
  } catch {}
}

function syncZoomDetections(h, img) {
  const zoomBoxes = document.getElementById('zoom-cat-boxes');
  const zoomBadge = document.getElementById('zoom-cat-badge');
  const card = grid.querySelector(`.cam[data-host="${CSS.escape(h)}"]`);
  const isLive = liveHosts.has(h);
  if (!card || isLive) {
    zoomDlg.classList.remove('has-cat');
    if (zoomBoxes) zoomBoxes.innerHTML = '';
    return;
  }
  zoomDlg.classList.toggle('has-cat', card.classList.contains('has-cat'));
  if (zoomBadge) zoomBadge.textContent = card.querySelector('.cat-badge')?.textContent || '';
  if (zoomBoxes) {
    const cardBoxes = card.querySelector('.cat-boxes');
    zoomBoxes.innerHTML = cardBoxes ? cardBoxes.innerHTML : '';
  }
}

function applyCardSource(card, t = Date.now()) {
  const h = card.dataset.host;
  const img = card.querySelector('img') || (zoomDlg.open && zoomDlg.dataset.host === h ? zoomedImg : null);
  if (!img) return;
  const status = card.querySelector('.status');
  const btn = card.querySelector('.live-btn');
  const isLive = liveHosts.has(h);
  if (btn) {
    btn.classList.toggle('active', isLive);
    btn.textContent = isLive ? '⏹' : '▶';
  }
  const refreshBtn = card.querySelector('.snap-refresh-btn');
  if (refreshBtn) refreshBtn.disabled = isLive;
  if (zoomDlg.open && zoomDlg.dataset.host === h) {
    zoomLiveBtn.classList.toggle('active', isLive);
    zoomLiveBtn.textContent = isLive ? '⏹' : '▶';
    document.getElementById('zoom-refresh').disabled = isLive;
  }
  if (isLive) {
    card.classList.remove('has-cat');
    const boxesEl = card.querySelector('.cat-boxes');
    if (boxesEl) boxesEl.innerHTML = '';
    const url = `/stream?host=${encodeURIComponent(h)}&t=${t}`;
    if (status) {
      status.className = 'status';
      status.textContent = 'Connecting…';
    }
    img.onload = () => {
      card.classList.remove('offline');
      if (status) { status.className = 'status ok'; status.textContent = 'LIVE'; }
    };
    img.onerror = () => {
      card.classList.add('offline');
      if (status) { status.className = 'status err'; status.textContent = 'Offline'; }
    };
    img.src = url;
    return;
  }

  const url = `/snapshot?host=${encodeURIComponent(h)}&t=${t}`;
  if (status) {
    status.className = 'status';
    status.textContent = 'Updating…';
  }
  clearTimeout(card._snapTimeout);
  card._snapTimeout = setTimeout(() => {
    if (liveHosts.has(h)) return;
    card.classList.add('offline');
    card.classList.remove('has-cat');
    if (status) {
      status.className = 'status err';
      status.textContent = 'Offline';
    }
  }, 16000);
  img.onload = () => {
    clearTimeout(card._snapTimeout);
    if (liveHosts.has(h)) return;
    card.classList.remove('offline');
    if (status) {
      status.className = 'status ok';
      status.textContent = new Date().toLocaleTimeString();
    }
    updateDetections();
  };
  img.onerror = () => {
    clearTimeout(card._snapTimeout);
    if (liveHosts.has(h)) return;
    card.classList.add('offline');
    card.classList.remove('has-cat');
    if (status) {
      status.className = 'status err';
      status.textContent = 'Offline';
    }
  };
  img.src = url;
}

function renderBottomLinks(h) {
  const c = capsMap[h] || {};
  return [
    c.high_res_stream ? `<a class="icon-btn" href="${c.high_res_stream}" title="Open High-Res RTSP stream">🎥<sub>+</sub></a>` : '',
    c.low_res_stream ? `<a class="icon-btn" href="${c.low_res_stream}" title="Open Low-Res RTSP stream">🎥</a>` : '',
    c.high_res_snapshot ? `<a class="icon-btn" href="${c.high_res_snapshot}" target="_blank" rel="noopener" title="Open High-Res Snapshot in new tab">📷<sub>+</sub></a>` : '',
    c.low_res_snapshot ? `<a class="icon-btn" href="${c.low_res_snapshot}" target="_blank" rel="noopener" title="Open Low-Res Snapshot in new tab">📷</a>` : '',
    `<a class="icon-btn" href="http://${h}" target="_blank" rel="noopener" title="Open Yi-Hack web interface">⚙️</a>`,
  ].filter(Boolean).join('');
}

async function refreshAll() {
  await loadConfig();
  const current = [...grid.querySelectorAll('.cam')].map(c => c.dataset.host);
  const nextNames = config.hosts.map(c => c.host);
  if (JSON.stringify(nextNames) !== JSON.stringify(current)) {
    if (zoomDlg.open) zoomDlg.close();
    grid.innerHTML = nextNames.map(h => `
      <article class="cam" draggable="true" data-host="${h}">
        <div class="cam-bar">
          <div class="cam-links">
            <span title="Drag to reorder">⠿</span>
            <span class="cam-host">${h}</span>
            <button type="button" class="icon-btn live-btn" data-live="${h}" title="Live RTSP stream in browser">▶</button>
            <button type="button" class="icon-btn snap-refresh-btn" data-refresh="${h}" title="Refresh snapshot">🔄</button>
            <span class="cat-badge" title="Cat detected"></span>
          </div>
          <span class="status">Loading…</span>
        </div>
        <div class="cam-viewport">
          <div class="spinner"></div>
          <span class="offline-badge">📷 Offline</span>
          <img alt="${h}" draggable="false">
          <div class="cat-boxes"></div>
        </div>
        <div class="cam-footer">
          ${renderBottomLinks(h)}
        </div>
      </article>
    `).join('');
  }

  const t = Date.now();
  for (const card of grid.querySelectorAll('.cam')) {
    const img = card.querySelector('img') || (zoomDlg.open && zoomDlg.dataset.host === card.dataset.host ? zoomedImg : null);
    if (!liveHosts.has(card.dataset.host) || !img?.getAttribute('src')) {
      applyCardSource(card, t);
    }
  }
}

function toggleLive(h) {
  if (liveHosts.has(h)) liveHosts.delete(h);
  else liveHosts.add(h);
  const card = grid.querySelector(`.cam[data-host="${CSS.escape(h)}"]`);
  if (card) applyCardSource(card);
}

function refreshHost(h) {
  if (liveHosts.has(h)) return;
  const card = grid.querySelector(`.cam[data-host="${CSS.escape(h)}"]`);
  if (card) applyCardSource(card, Date.now());
}

grid.onclick = (e) => {
  const liveBtn = e.target.closest('.live-btn');
  if (liveBtn) return toggleLive(liveBtn.dataset.live);
  const refreshBtn = e.target.closest('.snap-refresh-btn');
  if (refreshBtn) return refreshHost(refreshBtn.dataset.refresh);
  const img = e.target.closest('.cam img');
  if (!img) return;
  const card = img.closest('.cam');
  const h = card.dataset.host;
  const isLive = liveHosts.has(h);
  zoomDlg.dataset.host = h;
  zoomTitle.textContent = h;
  zoomLiveBtn.classList.toggle('active', isLive);
  zoomLiveBtn.textContent = isLive ? '⏹' : '▶';
  document.getElementById('zoom-refresh').disabled = isLive;
  zoomedImg = img;
  zoomedPlaceholder = document.createElement('div');
  zoomedPlaceholder.className = 'cam-placeholder';
  img.replaceWith(zoomedPlaceholder);
  zoomDlg.querySelector('.zoom-viewport').prepend(img);
  syncZoomDetections(h, img);

  zoomDlg.showModal();
};
zoomDlg.onclose = () => {
  if (zoomedImg && zoomedPlaceholder && zoomedPlaceholder.parentNode) {
    zoomedPlaceholder.replaceWith(zoomedImg);
  }
  zoomedImg = null;
  zoomedPlaceholder = null;
};
zoomLiveBtn.onclick = () => toggleLive(zoomDlg.dataset.host);
document.getElementById('zoom-refresh').onclick = () => refreshHost(zoomDlg.dataset.host);
document.getElementById('close-zoom').onclick = () => zoomDlg.close();
zoomDlg.onclick = (e) => { if (e.target === zoomDlg || e.target === zoomedImg) zoomDlg.close(); };

function enableDragSort(container, itemSel, onSorted) {
  let dragged = null;
  container.addEventListener('dragstart', (e) => {
    dragged = e.target.closest(itemSel);
    if (dragged) dragged.classList.add('dragging');
  });
  container.addEventListener('dragover', (e) => {
    e.preventDefault();
    const target = e.target.closest(itemSel);
    if (!dragged || !target || target === dragged) return;
    const items = [...container.querySelectorAll(itemSel)];
    if (items.indexOf(dragged) < items.indexOf(target)) target.after(dragged);
    else target.before(dragged);
  });
  container.addEventListener('dragend', () => {
    if (!dragged) return;
    dragged.classList.remove('dragging');
    dragged = null;
    onSorted([...container.querySelectorAll(itemSel)]);
  });
}

enableDragSort(grid, '.cam', (items) => {
  const nextNames = items.map(c => c.dataset.host);
  const curNames = config.hosts.map(c => c.host);
  if (JSON.stringify(nextNames) !== JSON.stringify(curNames)) {
    if (autosortCheckbox.checked) {
      autosortCheckbox.checked = false;
      localStorage.setItem('gatochecker_autosort', '0');
    }
    const byHost = Object.fromEntries(config.hosts.map(c => [c.host, c]));
    const next = nextNames.map(h => byHost[h] || normalizeCam(h)).filter(Boolean);
    saveConfig(config.refresh, config.min_conf, next);
  }
});

enableDragSort(hostListEl, 'li', (items) => {
  const byHost = Object.fromEntries(draftHosts.map(c => [c.host, c]));
  draftHosts = items.map(li => byHost[li.dataset.host]).filter(Boolean);
  renderDraftHosts();
});

function renderDraftHosts() {
  hostListEl.innerHTML = draftHosts.map((c, i) => `
    <li draggable="true" data-host="${c.host}">
      <div class="host-info">
        <span>⠿ <strong>${c.host}</strong></span>
        <span class="host-meta">📷 ${c.snapshot} · 🎥 ${c.stream}</span>
      </div>
      <div class="host-actions">
        <button type="button" data-edit="${i}" title="Edit paths">✎</button>
        <button type="button" data-rm="${i}" title="Remove">✕</button>
      </div>
    </li>
  `).join('');
}

hostListEl.onclick = (e) => {
  const editBtn = e.target.closest('[data-edit]');
  if (editBtn) {
    const c = draftHosts.splice(Number(editBtn.dataset.edit), 1)[0];
    if (c) {
      newHostInput.value = c.host;
      if (newSnapInput) newSnapInput.value = c.snapshot || DEFAULT_SNAP;
      if (newStreamInput) newStreamInput.value = c.stream || DEFAULT_STREAM;
      renderDraftHosts();
      newHostInput.focus();
    }
    return;
  }
  const rmBtn = e.target.closest('[data-rm]');
  if (rmBtn) {
    draftHosts.splice(Number(rmBtn.dataset.rm), 1);
    renderDraftHosts();
  }
};

async function addDraftHost(force = false) {
  const val = normalizeHost(newHostInput.value);
  if (!val) return;
  const snapshot = String(newSnapInput?.value || '').trim() || DEFAULT_SNAP;
  const stream = String(newStreamInput?.value || '').trim() || DEFAULT_STREAM;
  if (draftHosts.some(c => c.host === val)) {
    if (addHostMsg) { addHostMsg.className = 'field-msg err'; addHostMsg.textContent = `⚠️ Camera ${val} already added`; }
    return;
  }
  if (!force) {
    const addBtn = document.getElementById('add-host');
    addBtn.disabled = true;
    addBtn.textContent = 'Checking…';
    if (addHostMsg) { addHostMsg.className = 'field-msg info'; addHostMsg.textContent = `Connecting to ${val}…`; }
    try {
      const res = await fetch(`./validate?host=${encodeURIComponent(val)}&snapshot=${encodeURIComponent(snapshot)}`);
      const data = await res.json();
      if (!data.ok) {
        if (addHostMsg) {
          addHostMsg.className = 'field-msg err';
          addHostMsg.innerHTML = `⚠️ ${data.error || 'Failed to connect'}. <button type="button" class="link-btn" id="force-add-host">Add anyway</button>`;
          document.getElementById('force-add-host')?.addEventListener('click', () => addDraftHost(true));
        }
        return;
      }
      if (addHostMsg) {
        addHostMsg.className = 'field-msg ok';
        const modelStr = data.model ? ` (${data.model})` : '';
        addHostMsg.textContent = `✓ ${data.hostname || val}${modelStr} - ${data.name || 'camera'}`;
      }
    } catch (e) {
      if (addHostMsg) {
        addHostMsg.className = 'field-msg err';
        addHostMsg.innerHTML = `⚠️ Could not reach camera. <button type="button" class="link-btn" id="force-add-host">Add anyway</button>`;
        document.getElementById('force-add-host')?.addEventListener('click', () => addDraftHost(true));
      }
      return;
    } finally {
      addBtn.disabled = false;
      addBtn.textContent = '+ Add';
    }
  }
  draftHosts.push({ host: val, snapshot, stream });
  newHostInput.value = '';
  if (newSnapInput) newSnapInput.value = DEFAULT_SNAP;
  if (newStreamInput) newStreamInput.value = DEFAULT_STREAM;
  renderDraftHosts();
}

document.getElementById('add-host').onclick = () => addDraftHost(false);
newHostInput.onkeydown = (e) => { if (e.key === 'Enter') addDraftHost(false); };

document.getElementById('open-admin').onclick = async () => {
  await loadConfig();
  cfgRefresh.value = String(config.refresh);
  if (cfgConf) cfgConf.value = String(config.min_conf);
  if (newSnapInput) newSnapInput.value = DEFAULT_SNAP;
  if (newStreamInput) newStreamInput.value = DEFAULT_STREAM;
  if (addHostMsg) { addHostMsg.className = 'field-msg'; addHostMsg.textContent = ''; }
  draftHosts = config.hosts.map(c => ({ ...c }));
  renderDraftHosts();
  adminDlg.showModal();
};

document.getElementById('cancel-admin').onclick = () => adminDlg.close();

document.getElementById('save-admin').onclick = async () => {
  const refresh = Number(cfgRefresh.value);
  const min_conf = Number(cfgConf?.value || config.min_conf);
  if (!(await saveConfig(refresh, min_conf, draftHosts))) {
    return alert('Failed to save config.json (run python3 server.py)');
  }
  intervalSel.value = String(refresh);
  resetTimer();
  adminDlg.close();
  await refreshAll();
};

function resetTimer() {
  clearInterval(timer);
  const ms = Number(intervalSel.value);
  if (ms > 0) timer = setInterval(refreshAll, ms);
}

document.getElementById('refresh').onclick = refreshAll;
intervalSel.onchange = resetTimer;
autosortCheckbox.onchange = () => {
  localStorage.setItem('gatochecker_autosort', autosortCheckbox.checked ? '1' : '0');
  if (autosortCheckbox.checked) {
    applyAutoSort();
  } else {
    const cards = [...grid.querySelectorAll('.cam')];
    cards.sort((a, b) => hostIndex(a.dataset.host) - hostIndex(b.dataset.host));
    grid.append(...cards);
  }
};
await refreshAll();
resetTimer();

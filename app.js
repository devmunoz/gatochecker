const grid = document.getElementById('grid');
const intervalSel = document.getElementById('interval');
const adminDlg = document.getElementById('admin');
const zoomDlg = document.getElementById('zoom');
let zoomedImg = null;
let zoomedPlaceholder = null;
const zoomTitle = document.getElementById('zoom-title');
const zoomLiveBtn = document.getElementById('zoom-live');
const cfgRefresh = document.getElementById('cfg-refresh');
const hostListEl = document.getElementById('host-list');
const newHostInput = document.getElementById('new-host');
let timer = null;
let config = { refresh: 10000, hosts: [] };
let capsMap = {};
let draftHosts = [];
let initialized = false;
const liveHosts = new Set();

const normalizeHost = (h) => String(h).trim().replace(/^https?:\/\//i, '').replace(/\/+$/, '');

async function loadConfig() {
  const [res, capsRes] = await Promise.all([
    fetch('./hosts.json', { cache: 'no-store' }),
    fetch('./capabilities', { cache: 'no-store' }).catch(() => null),
  ]);
  const raw = res.ok ? await res.json() : { refresh: 10000, hosts: [] };
  if (capsRes?.ok) capsMap = await capsRes.json();
  const list = (Array.isArray(raw) ? raw : (raw.hosts ?? [])).map(normalizeHost).filter(Boolean);
  config = { refresh: Array.isArray(raw) ? 10000 : (raw.refresh ?? 10000), hosts: list };
  if (!initialized) {
    intervalSel.value = String(config.refresh);
    initialized = true;
  }
}

async function saveConfig(refresh, hosts) {
  const res = await fetch('./hosts.json', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh, hosts })
  });
  if (res.ok) config = { refresh, hosts };
  return res.ok;
}

async function updateDetections(host, card, img) {
  try {
    const res = await fetch('./detections', { cache: 'no-store' });
    if (!res.ok) return;
    const all = await res.json();
    const det = all[host];
    const cats = (!liveHosts.has(host) && det?.cats) ? det.cats : [];
    card.classList.toggle('has-cat', cats.length > 0);
    const badge = card.querySelector('.cat-badge');
    if (badge && cats.length) {
      const maxConf = Math.round(Math.max(...cats.map(c => c.conf)) * 100);
      badge.textContent = `🐱 ${cats.length > 1 ? `${cats.length} · ` : ''}${maxConf}%`;
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

  img.onload = null;
  img.onerror = null;
  const url = `/snapshot?host=${encodeURIComponent(h)}&t=${t}`;
  if (status) {
    status.className = 'status';
    status.textContent = 'Updating…';
  }
  const loader = new Image();
  const timeoutId = setTimeout(() => {
    loader.src = '';
    if (liveHosts.has(h)) return;
    card.classList.add('offline');
    card.classList.remove('has-cat');
    if (status) {
      status.className = 'status err';
      status.textContent = 'Offline';
    }
  }, 16000);
  loader.onload = () => {
    clearTimeout(timeoutId);
    if (liveHosts.has(h)) return;
    card.classList.remove('offline');
    img.src = url;
    if (status) {
      status.className = 'status ok';
      status.textContent = new Date().toLocaleTimeString();
    }
    updateDetections(h, card, loader);
  };
  loader.onerror = () => {
    clearTimeout(timeoutId);
    if (liveHosts.has(h)) return;
    card.classList.add('offline');
    card.classList.remove('has-cat');
    if (status) {
      status.className = 'status err';
      status.textContent = 'Offline';
    }
  };
  loader.src = url;
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
  if (JSON.stringify(config.hosts) !== JSON.stringify(current)) {
    if (zoomDlg.open) zoomDlg.close();
    grid.innerHTML = config.hosts.map(h => `
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
  const next = items.map(c => c.dataset.host);
  if (JSON.stringify(next) !== JSON.stringify(config.hosts)) {
    saveConfig(config.refresh, next);
  }
});

enableDragSort(hostListEl, 'li', (items) => {
  draftHosts = items.map(li => li.dataset.host);
  renderDraftHosts();
});

function renderDraftHosts() {
  hostListEl.innerHTML = draftHosts.map((h, i) => `
    <li draggable="true" data-host="${h}">
      <span>⠿ ${h}</span>
      <button type="button" data-rm="${i}" title="Remove">✕</button>
    </li>
  `).join('');
}

hostListEl.onclick = (e) => {
  const idx = e.target.dataset.rm;
  if (idx !== undefined) {
    draftHosts.splice(Number(idx), 1);
    renderDraftHosts();
  }
};

function addDraftHost() {
  const val = normalizeHost(newHostInput.value);
  if (val && !draftHosts.includes(val)) {
    draftHosts.push(val);
    newHostInput.value = '';
    renderDraftHosts();
  }
}

document.getElementById('add-host').onclick = addDraftHost;
newHostInput.onkeydown = (e) => { if (e.key === 'Enter') addDraftHost(); };

document.getElementById('open-admin').onclick = async () => {
  await loadConfig();
  cfgRefresh.value = String(config.refresh);
  draftHosts = [...config.hosts];
  renderDraftHosts();
  adminDlg.showModal();
};

document.getElementById('cancel-admin').onclick = () => adminDlg.close();

document.getElementById('save-admin').onclick = async () => {
  addDraftHost();
  const refresh = Number(cfgRefresh.value);
  if (!(await saveConfig(refresh, draftHosts))) {
    return alert('Failed to save hosts.json (run python3 server.py)');
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
await refreshAll();
resetTimer();

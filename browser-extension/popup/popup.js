/**
 * popup.js – Anomaly Detection System Popup Logic
 *
 * Responsibilities:
 *  - Query background.js for live stats on load
 *  - Display connection status, events sent, batches, session ID
 *  - Render last 5 alerts from chrome.storage.local
 *  - Handle monitoring toggle (sync to background via message)
 *  - Poll for updates every 2 seconds
 *  - Format timestamps as MM/DD HH:MM:SS
 */

'use strict';

// ─── Timestamp formatter ────────────────────────────────────────────────────
/**
 * Format an ISO-8601 timestamp string as "MM/DD HH:MM:SS"
 * @param {string|null} isoStr
 * @returns {string}
 */
function formatTimestamp(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return '—';

    const MM  = String(d.getMonth() + 1).padStart(2, '0');
    const DD  = String(d.getDate()).padStart(2, '0');
    const HH  = String(d.getHours()).padStart(2, '0');
    const min = String(d.getMinutes()).padStart(2, '0');
    const SS  = String(d.getSeconds()).padStart(2, '0');

    return `${MM}/${DD} ${HH}:${min}:${SS}`;
  } catch (_) {
    return '—';
  }
}

// ─── Truncate session ID for display ────────────────────────────────────────
function truncateSessionId(id) {
  if (!id || id === '—') return '—';
  if (id.length <= 16) return id;
  return id.slice(0, 8) + '…' + id.slice(-6);
}

// ─── Format large numbers ────────────────────────────────────────────────────
function formatNumber(n) {
  if (n == null || isNaN(n)) return '—';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

// ─── Alert severity ──────────────────────────────────────────────────────────
function getSeverityClass(alert) {
  const sev = (alert.severity || alert.level || '').toLowerCase();
  if (sev === 'high'   || sev === 'critical') return 'severity-high';
  if (sev === 'medium' || sev === 'warning')  return 'severity-medium';
  return 'severity-low';
}

function getSeverityIcon(alert) {
  const sev = (alert.severity || alert.level || '').toLowerCase();
  if (sev === 'high'   || sev === 'critical') return '🔴';
  if (sev === 'medium' || sev === 'warning')  return '🟡';
  return '🟢';
}

// ─── DOM refs ────────────────────────────────────────────────────────────────
const el = {
  statusBadge      : document.getElementById('statusBadge'),
  statusText       : document.getElementById('statusText'),
  statEvents       : document.getElementById('statEvents'),
  statAlerts       : document.getElementById('statAlerts'),
  statBatches      : document.getElementById('statBatches'),
  sessionId        : document.getElementById('sessionId'),
  queueCount       : document.getElementById('queueCount'),
  queueFill        : document.getElementById('queueFill'),
  alertsList       : document.getElementById('alertsList'),
  alertCount       : document.getElementById('alertCount'),
  lastFlush        : document.getElementById('lastFlush'),
  monitoringToggle : document.getElementById('monitoringToggle'),
  toggleLabel      : document.getElementById('toggleLabel'),
  settingsBtn      : document.getElementById('settingsBtn'),
};

// ─── Update connection status badge ─────────────────────────────────────────
let lastSuccessfulPoll = 0;
const STALE_THRESHOLD_MS = 12_000; // consider disconnected if no response for 12 s

function setConnectionStatus(connected) {
  el.statusBadge.className = 'status-badge ' + (connected ? 'connected' : 'disconnected');
  el.statusText.textContent = connected ? 'Live' : 'Off';
}

// ─── Render alerts list ──────────────────────────────────────────────────────
function renderAlerts(alerts) {
  const list = Array.isArray(alerts) ? alerts : [];

  // Keep last 5
  const recent = list.slice(-5).reverse();

  el.alertCount.textContent = String(list.length);

  if (recent.length === 0) {
    el.alertsList.innerHTML = `
      <div class="no-alerts">
        <div class="no-alerts-icon">🛡️</div>
        <span>No alerts detected</span>
      </div>`;
    return;
  }

  el.alertsList.innerHTML = recent.map((alert) => {
    const sevClass = getSeverityClass(alert);
    const icon     = getSeverityIcon(alert);
    const type     = alert.type || alert.anomaly_type || alert.category || 'Unknown Alert';
    const time     = formatTimestamp(alert.timestamp || alert.created_at || alert.time);

    return `
      <div class="alert-item ${sevClass}">
        <div class="alert-icon">${icon}</div>
        <div class="alert-content">
          <div class="alert-type" title="${type}">${type}</div>
          <div class="alert-time">${time}</div>
        </div>
      </div>`;
  }).join('');
}

// ─── Render queue progress bar ───────────────────────────────────────────────
function renderQueue(queued) {
  const count = queued || 0;
  const pct   = Math.min(100, (count / 50) * 100); // 50 = BATCH_MAX_SIZE
  el.queueCount.textContent = `${count} event${count !== 1 ? 's' : ''}`;
  el.queueFill.style.width  = pct + '%';
}

// ─── Update all UI from stats object ────────────────────────────────────────
function updateUI(data) {
  const { stats = {}, sessionId, monitoringEnabled, queuedEvents = 0 } = data;

  // Events sent
  el.statEvents.textContent  = formatNumber(stats.totalEventsSent ?? 0);
  el.statBatches.textContent = formatNumber(stats.totalBatches    ?? 0);

  // Session ID
  if (sessionId) {
    el.sessionId.textContent = truncateSessionId(sessionId);
    el.sessionId.title       = sessionId;
  }

  // Last flush
  el.lastFlush.textContent = formatTimestamp(stats.lastFlushTime);

  // Queue
  renderQueue(queuedEvents);

  // Monitoring toggle
  const enabled = monitoringEnabled !== false;
  el.monitoringToggle.checked = enabled;
  el.toggleLabel.textContent  = enabled ? 'Monitoring On' : 'Monitoring Off';
}

// ─── Fetch stats from background ─────────────────────────────────────────────
async function pollStats() {
  try {
    const response = await chrome.runtime.sendMessage({ type: 'GET_STATS' });

    if (response && response.ok) {
      lastSuccessfulPoll = Date.now();
      setConnectionStatus(true);
      updateUI(response);
    } else {
      setConnectionStatus(false);
    }
  } catch (err) {
    // Extension context may not be ready yet
    const isStale = (Date.now() - lastSuccessfulPoll) > STALE_THRESHOLD_MS;
    setConnectionStatus(!isStale && lastSuccessfulPoll > 0);
    console.warn('[ADS Popup] pollStats error:', err.message);
  }
}

// ─── Fetch alerts from chrome.storage.local ──────────────────────────────────
async function pollAlerts() {
  try {
    const data = await chrome.storage.local.get(['ads_alerts', 'ads_stats']);
    const alerts = data.ads_alerts || [];
    renderAlerts(alerts);

    // Update alerts count stat
    el.statAlerts.textContent = formatNumber(alerts.length);
  } catch (err) {
    console.warn('[ADS Popup] pollAlerts error:', err.message);
  }
}

// ─── Full poll cycle ──────────────────────────────────────────────────────────
async function fullPoll() {
  await Promise.all([pollStats(), pollAlerts()]);
}

// ─── Monitoring toggle handler ────────────────────────────────────────────────
el.monitoringToggle.addEventListener('change', async () => {
  const enabled = el.monitoringToggle.checked;
  el.toggleLabel.textContent = enabled ? 'Monitoring On' : 'Monitoring Off';

  try {
    await chrome.runtime.sendMessage({ type: 'SET_MONITORING', enabled });
    // Persist locally too for immediate feedback
    await chrome.storage.sync.set({ monitoringEnabled: enabled });
  } catch (err) {
    console.warn('[ADS Popup] SET_MONITORING error:', err.message);
    // Roll back toggle on error
    el.monitoringToggle.checked = !enabled;
    el.toggleLabel.textContent  = !enabled ? 'Monitoring On' : 'Monitoring Off';
  }
});

// ─── Settings button ──────────────────────────────────────────────────────────
el.settingsBtn.addEventListener('click', () => {
  if (chrome.runtime.openOptionsPage) {
    chrome.runtime.openOptionsPage();
  } else {
    // Fallback for Firefox
    const optionsUrl = chrome.runtime.getURL('options/options.html');
    chrome.tabs.create({ url: optionsUrl });
  }
  window.close();
});

// ─── Real-time storage change listener ───────────────────────────────────────
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'local') {
    if (changes.ads_alerts) {
      renderAlerts(changes.ads_alerts.newValue || []);
      el.statAlerts.textContent = formatNumber((changes.ads_alerts.newValue || []).length);
    }
    if (changes.ads_stats && changes.ads_stats.newValue) {
      const s = changes.ads_stats.newValue;
      el.statEvents.textContent  = formatNumber(s.totalEventsSent ?? 0);
      el.statBatches.textContent = formatNumber(s.totalBatches    ?? 0);
      el.lastFlush.textContent   = formatTimestamp(s.lastFlushTime);
    }
  }
});

// ─── Initialise ───────────────────────────────────────────────────────────────
(async function init() {
  // Show loading state
  setConnectionStatus(false);
  el.statEvents.textContent  = '…';
  el.statAlerts.textContent  = '…';
  el.statBatches.textContent = '…';

  // Restore toggle state from sync storage
  try {
    const sync = await chrome.storage.sync.get(['monitoringEnabled']);
    const enabled = sync.monitoringEnabled !== false;
    el.monitoringToggle.checked = enabled;
    el.toggleLabel.textContent  = enabled ? 'Monitoring On' : 'Monitoring Off';
  } catch (_) {}

  // Initial data load
  await fullPoll();

  // Poll every 2 seconds while popup is open
  const pollInterval = setInterval(fullPoll, 2_000);

  // Clean up interval when popup window closes
  window.addEventListener('unload', () => clearInterval(pollInterval), { once: true });
})();

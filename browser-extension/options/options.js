/**
 * options.js – Anomaly Detection System Options Page Logic
 *
 * Loads settings from chrome.storage.sync, allows editing, and saves back.
 */

'use strict';

const DEFAULTS = {
  apiEndpoint      : 'http://localhost:3000/api/v1/telemetry',
  dashboardUrl     : 'http://localhost:3000',
  authToken        : '',
  monitoringEnabled: true,
  trackXhr         : true,
  trackFetch       : true,
  trackMutations   : true,
  flushInterval    : 5,
  batchSize        : 50,
};

// ─── Helper: show toast ───────────────────────────────────────────────────────
function showToast(msg = '✓ Settings saved', isError = false) {
  const toast = document.getElementById('toast');
  toast.textContent = msg;
  toast.style.background = isError ? '#ef4444' : '#10b981';
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 2500);
}

// ─── Load settings ────────────────────────────────────────────────────────────
async function loadSettings() {
  try {
    const stored = await chrome.storage.sync.get(Object.keys(DEFAULTS));
    const s = { ...DEFAULTS, ...stored };

    document.getElementById('apiEndpoint').value     = s.apiEndpoint;
    document.getElementById('dashboardUrl').value    = s.dashboardUrl;
    document.getElementById('authToken').value       = s.authToken;
    document.getElementById('flushInterval').value   = s.flushInterval;
    document.getElementById('batchSize').value       = s.batchSize;

    document.getElementById('optMonitoring').checked = s.monitoringEnabled;
    document.getElementById('optXhr').checked        = s.trackXhr;
    document.getElementById('optFetch').checked      = s.trackFetch;
    document.getElementById('optMutations').checked  = s.trackMutations;
  } catch (err) {
    showToast('⚠ Failed to load settings', true);
    console.error('[ADS Options] loadSettings:', err);
  }
}

// ─── Save settings ────────────────────────────────────────────────────────────
async function saveSettings() {
  const apiEndpoint   = document.getElementById('apiEndpoint').value.trim();
  const dashboardUrl  = document.getElementById('dashboardUrl').value.trim();
  const authToken     = document.getElementById('authToken').value.trim();
  const flushInterval = parseInt(document.getElementById('flushInterval').value, 10);
  const batchSize     = parseInt(document.getElementById('batchSize').value, 10);

  // Validate URLs
  if (apiEndpoint) {
    try { new URL(apiEndpoint); } catch (_) {
      showToast('⚠ Invalid API endpoint URL', true); return;
    }
  }
  if (dashboardUrl) {
    try { new URL(dashboardUrl); } catch (_) {
      showToast('⚠ Invalid Dashboard URL', true); return;
    }
  }

  const settings = {
    apiEndpoint,
    dashboardUrl,
    authToken,
    monitoringEnabled: document.getElementById('optMonitoring').checked,
    trackXhr         : document.getElementById('optXhr').checked,
    trackFetch       : document.getElementById('optFetch').checked,
    trackMutations   : document.getElementById('optMutations').checked,
    flushInterval    : isNaN(flushInterval) ? DEFAULTS.flushInterval : Math.max(1, Math.min(60, flushInterval)),
    batchSize        : isNaN(batchSize)     ? DEFAULTS.batchSize     : Math.max(5, Math.min(500, batchSize)),
  };

  try {
    await chrome.storage.sync.set(settings);
    // Notify background of updated settings
    chrome.runtime.sendMessage({ type: 'SETTINGS_UPDATED', settings }).catch(() => {});
    showToast('✓ Settings saved');
  } catch (err) {
    showToast('⚠ Failed to save settings', true);
    console.error('[ADS Options] saveSettings:', err);
  }
}

// ─── Reset to defaults ────────────────────────────────────────────────────────
async function resetDefaults() {
  await chrome.storage.sync.set(DEFAULTS);
  await loadSettings();
  showToast('↺ Reset to defaults');
}

// ─── Bind buttons ─────────────────────────────────────────────────────────────
document.getElementById('btnSave').addEventListener('click', saveSettings);
document.getElementById('btnReset').addEventListener('click', resetDefaults);

// ─── Init ─────────────────────────────────────────────────────────────────────
loadSettings();

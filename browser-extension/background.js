/**
 * background.js – Anomaly Detection System MV3 Service Worker
 *
 * Responsibilities:
 *  - Generate & persist a session_id per browser session
 *  - Listen to webNavigation, webRequest, and tabs events
 *  - Tag every event with {event_id, session_id, type, url, timestamp, tab_id, metadata}
 *  - Batch events (max 50 or 5-second flush) and POST to Rails API
 *  - Exponential-backoff retry (3 attempts) on network failure
 *  - Handle messages from content.js and popup.js
 *  - Maintain runtime stats (totalEventsSent, totalBatches, lastFlushTime)
 */

// ─── Constants ───────────────────────────────────────────────────────────────
const API_ENDPOINT      = 'http://localhost:3000/api/v1/telemetry';
const BATCH_MAX_SIZE    = 50;
const FLUSH_INTERVAL_MS = 5_000;   // 5 seconds
const MAX_RETRIES       = 3;
const RETRY_BASE_MS     = 1_000;   // 1 s base for exponential backoff
const ALARM_NAME        = 'flush_telemetry';

// ─── Runtime state (lives only while the service worker is alive) ─────────────
let eventBatch     = [];          // pending events not yet sent
let sessionId      = null;        // set on startup from chrome.storage.session
let monitoringEnabled = true;     // toggled by popup
let authToken      = null;        // optional bearer token

const stats = {
  totalEventsSent : 0,
  totalBatches    : 0,
  lastFlushTime   : null,
  lastError       : null,
};

// ─── UUID v4 generator (no external deps) ────────────────────────────────────
function uuidv4() {
  // Use crypto.randomUUID if available (Chrome 92+, Firefox 95+)
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // Polyfill for older environments
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// ─── Initialisation ───────────────────────────────────────────────────────────
async function initialise() {
  // Restore / create session ID
  try {
    const sessionData = await chrome.storage.session.get(['session_id']);
    if (sessionData.session_id) {
      sessionId = sessionData.session_id;
    } else {
      sessionId = uuidv4();
      await chrome.storage.session.set({ session_id: sessionId });
    }
  } catch (_) {
    // storage.session not available in Firefox <112 – fall back to runtime id
    sessionId = chrome.runtime.id + '-' + Date.now();
  }

  // Restore user preferences
  try {
    const syncData = await chrome.storage.sync.get(['monitoringEnabled', 'authToken']);
    monitoringEnabled = syncData.monitoringEnabled !== false; // default true
    authToken         = syncData.authToken || null;
  } catch (_) {}

  // Schedule periodic alarm (MV3 service workers wake on alarms)
  if (chrome.alarms) {
    await chrome.alarms.create(ALARM_NAME, { periodInMinutes: FLUSH_INTERVAL_MS / 60_000 });
  }

  console.log('[ADS Background] Initialised. Session:', sessionId);
}

// ─── Event factory ────────────────────────────────────────────────────────────
function makeEvent(type, url, tabId, metadata = {}) {
  return {
    event_id  : uuidv4(),
    session_id: sessionId,
    type,
    url       : url || '',
    timestamp : new Date().toISOString(),
    tab_id    : tabId ?? -1,
    metadata,
  };
}

// ─── Queue an event ───────────────────────────────────────────────────────────
function queueEvent(event) {
  if (!monitoringEnabled) return;
  eventBatch.push(event);
  if (eventBatch.length >= BATCH_MAX_SIZE) {
    flushBatch();
  }
}

// ─── HTTP POST with exponential-backoff retry ─────────────────────────────────
async function postWithRetry(payload, attempt = 1) {
  const headers = { 'Content-Type': 'application/json' };
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`;

  try {
    const response = await fetch(API_ENDPOINT, {
      method : 'POST',
      headers,
      body   : JSON.stringify(payload),
      signal : AbortSignal.timeout(10_000), // 10-second timeout
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }

    return true;
  } catch (err) {
    if (attempt < MAX_RETRIES) {
      const delay = RETRY_BASE_MS * Math.pow(2, attempt - 1); // 1s, 2s, 4s
      console.warn(`[ADS Background] POST failed (attempt ${attempt}), retrying in ${delay}ms:`, err.message);
      await new Promise((r) => setTimeout(r, delay));
      return postWithRetry(payload, attempt + 1);
    }
    throw err; // exhausted retries
  }
}

// ─── Flush batch to API ───────────────────────────────────────────────────────
async function flushBatch() {
  if (eventBatch.length === 0) return;

  const toSend   = eventBatch.splice(0, eventBatch.length); // drain atomically
  const batchId  = uuidv4();
  const payload  = {
    batch_id   : batchId,
    session_id : sessionId,
    sent_at    : new Date().toISOString(),
    event_count: toSend.length,
    events     : toSend,
  };

  console.log(`[ADS Background] Flushing batch ${batchId} (${toSend.length} events)`);

  try {
    await postWithRetry(payload);

    stats.totalEventsSent += toSend.length;
    stats.totalBatches    += 1;
    stats.lastFlushTime    = new Date().toISOString();
    stats.lastError        = null;

    // Persist stats so popup can read them even after SW restarts
    await chrome.storage.local.set({ ads_stats: stats });

    console.log(`[ADS Background] Batch flushed OK. Total sent: ${stats.totalEventsSent}`);
  } catch (err) {
    console.error('[ADS Background] Failed to send batch after retries:', err.message);
    stats.lastError = err.message;
    await chrome.storage.local.set({ ads_stats: stats });

    // Put events back at the front so they are retried on next flush
    eventBatch.unshift(...toSend);
  }
}

// ─── Alarm listener (periodic flush) ─────────────────────────────────────────
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) {
    flushBatch();
  }
});

// ─── webNavigation listeners ──────────────────────────────────────────────────
chrome.webNavigation.onBeforeNavigate.addListener((details) => {
  queueEvent(makeEvent('navigation.before', details.url, details.tabId, {
    frame_id       : details.frameId,
    parent_frame_id: details.parentFrameId,
    process_id     : details.processId,
  }));
});

chrome.webNavigation.onCompleted.addListener((details) => {
  queueEvent(makeEvent('navigation.completed', details.url, details.tabId, {
    frame_id: details.frameId,
  }));
});

chrome.webNavigation.onErrorOccurred.addListener((details) => {
  queueEvent(makeEvent('navigation.error', details.url, details.tabId, {
    frame_id: details.frameId,
    error   : details.error,
  }));
});

chrome.webNavigation.onCreatedNavigationTarget.addListener((details) => {
  queueEvent(makeEvent('navigation.new_target', details.url, details.tabId, {
    source_tab_id  : details.sourceTabId,
    source_frame_id: details.sourceFrameId,
  }));
});

chrome.webNavigation.onReferenceFragmentUpdated.addListener((details) => {
  queueEvent(makeEvent('navigation.fragment_update', details.url, details.tabId, {
    frame_id: details.frameId,
  }));
});

// ─── webRequest listeners ─────────────────────────────────────────────────────
const pendingRequests = new Map(); // requestId -> {startTime, url, method, tabId}

chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    pendingRequests.set(details.requestId, {
      startTime: details.timeStamp,
      url      : details.url,
      method   : details.method,
      tabId    : details.tabId,
      type     : details.type,
    });

    queueEvent(makeEvent('request.before', details.url, details.tabId, {
      request_id  : details.requestId,
      method      : details.method,
      resource_type: details.type,
      initiator   : details.initiator || null,
    }));
  },
  { urls: ['<all_urls>'] }
);

chrome.webRequest.onCompleted.addListener(
  (details) => {
    const pending = pendingRequests.get(details.requestId);
    const duration = pending ? details.timeStamp - pending.startTime : null;
    pendingRequests.delete(details.requestId);

    queueEvent(makeEvent('request.completed', details.url, details.tabId, {
      request_id       : details.requestId,
      status_code      : details.statusCode,
      status_line      : details.statusLine,
      method           : details.method,
      duration_ms      : duration,
      response_size    : details.responseHeaders
        ? details.responseHeaders.find(h => h.name.toLowerCase() === 'content-length')?.value
        : null,
      from_cache       : details.fromCache,
      resource_type    : details.type,
    }));
  },
  { urls: ['<all_urls>'] },
  ['responseHeaders']
);

chrome.webRequest.onErrorOccurred.addListener(
  (details) => {
    const pending = pendingRequests.get(details.requestId);
    const duration = pending ? details.timeStamp - pending.startTime : null;
    pendingRequests.delete(details.requestId);

    queueEvent(makeEvent('request.error', details.url, details.tabId, {
      request_id   : details.requestId,
      error        : details.error,
      method       : details.method,
      duration_ms  : duration,
      resource_type: details.type,
    }));
  },
  { urls: ['<all_urls>'] }
);

// ─── Tabs listeners ───────────────────────────────────────────────────────────
chrome.tabs.onCreated.addListener((tab) => {
  queueEvent(makeEvent('tab.created', tab.url || '', tab.id, {
    opener_tab_id: tab.openerTabId ?? null,
    window_id    : tab.windowId,
    index        : tab.index,
  }));
});

chrome.tabs.onRemoved.addListener((tabId, removeInfo) => {
  queueEvent(makeEvent('tab.removed', '', tabId, {
    window_id        : removeInfo.windowId,
    is_window_closing: removeInfo.isWindowClosing,
  }));
});

chrome.tabs.onActivated.addListener((activeInfo) => {
  queueEvent(makeEvent('tab.activated', '', activeInfo.tabId, {
    window_id: activeInfo.windowId,
  }));
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  // Only track meaningful state changes
  if (!changeInfo.url && !changeInfo.status && !changeInfo.title) return;

  queueEvent(makeEvent('tab.updated', tab.url || '', tabId, {
    status     : changeInfo.status || null,
    title      : changeInfo.title  || null,
    changed_url: changeInfo.url    || null,
    window_id  : tab.windowId,
  }));
});

// ─── Runtime message handler (from content.js + popup.js) ────────────────────
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const tabId = sender.tab?.id ?? -1;

  switch (message.type) {

    // Content script reporting DOM/XHR/fetch events
    case 'CONTENT_EVENTS': {
      const events = message.events || [];
      for (const ev of events) {
        queueEvent({
          event_id  : uuidv4(),
          session_id: sessionId,
          type      : ev.type,
          url       : ev.url || sender.tab?.url || '',
          timestamp : ev.timestamp || new Date().toISOString(),
          tab_id    : tabId,
          metadata  : ev.metadata || {},
        });
      }
      sendResponse({ ok: true, queued: events.length });
      break;
    }

    // Popup requesting current stats
    case 'GET_STATS': {
      sendResponse({
        ok            : true,
        stats,
        sessionId,
        monitoringEnabled,
        queuedEvents  : eventBatch.length,
      });
      break;
    }

    // Popup toggling monitoring on/off
    case 'SET_MONITORING': {
      monitoringEnabled = !!message.enabled;
      chrome.storage.sync.set({ monitoringEnabled });
      sendResponse({ ok: true, monitoringEnabled });
      break;
    }

    // Popup requesting immediate flush
    case 'FLUSH_NOW': {
      flushBatch().then(() => sendResponse({ ok: true }));
      return true; // keep channel open for async
    }

    default:
      sendResponse({ ok: false, error: 'Unknown message type' });
  }

  return false; // synchronous response (except FLUSH_NOW above)
});

// ─── onInstalled: set defaults ────────────────────────────────────────────────
chrome.runtime.onInstalled.addListener(async (details) => {
  console.log('[ADS Background] onInstalled reason:', details.reason);

  await chrome.storage.sync.set({
    monitoringEnabled: true,
    dashboardUrl     : 'http://localhost:3000',
    authToken        : '',
    apiEndpoint      : API_ENDPOINT,
  });

  await chrome.storage.local.set({
    ads_stats: {
      totalEventsSent: 0,
      totalBatches   : 0,
      lastFlushTime  : null,
      lastError      : null,
    },
    ads_alerts: [],
  });

  if (details.reason === 'install') {
    // Open options page on first install
    chrome.runtime.openOptionsPage?.();
  }
});

// ─── onStartup: reinitialise session ─────────────────────────────────────────
chrome.runtime.onStartup.addListener(() => {
  initialise();
});

// ─── Bootstrap ───────────────────────────────────────────────────────────────
// MV3 service workers run initialise() when first loaded
initialise();

// Fallback periodic flush via setInterval (active while SW is alive)
setInterval(flushBatch, FLUSH_INTERVAL_MS);

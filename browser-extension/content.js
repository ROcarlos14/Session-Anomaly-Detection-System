/**
 * content.js – Anomaly Detection System Content Script
 *
 * Injected into every page (all_frames, document_start).
 *
 * Collects:
 *  1. DOM mutation rate via MutationObserver
 *  2. XMLHttpRequest timing, URL, method, status, size
 *  3. fetch() timing, URL, method, status, size
 *  4. Redirect-chain detection via location/referrer comparison
 *  5. Cross-origin request counts
 *
 * All collected events are batched locally and sent to background.js
 * every 3 seconds via chrome.runtime.sendMessage.
 */

(function () {
  'use strict';

  // Guard against double-injection (e.g., same-origin iframes re-injected)
  if (window.__ADS_INJECTED__) return;
  window.__ADS_INJECTED__ = true;

  // ─── Config ────────────────────────────────────────────────────────────────
  const SEND_INTERVAL_MS         = 3_000;  // flush to background every 3 s
  const MUTATION_RATE_THRESHOLD  = 10;     // mutations/sec to trigger event
  const MAX_LOCAL_BATCH          = 200;    // cap local queue to avoid OOM

  // ─── State ─────────────────────────────────────────────────────────────────
  const localQueue        = [];
  let mutationCount       = 0;
  let mutationWindow      = [];   // timestamps of recent mutations
  const pageOrigin        = (() => { try { return location.origin; } catch (_) { return ''; } })();
  const pageLoadTime      = performance.now();

  // ─── Helper: ISO timestamp ─────────────────────────────────────────────────
  function now() { return new Date().toISOString(); }

  // ─── Helper: enqueue local event ──────────────────────────────────────────
  function enqueue(type, url, metadata = {}) {
    if (localQueue.length >= MAX_LOCAL_BATCH) localQueue.shift(); // drop oldest
    localQueue.push({ type, url: url || location.href, timestamp: now(), metadata });
  }

  // ─── Helper: is cross-origin? ──────────────────────────────────────────────
  function isCrossOrigin(url) {
    try {
      return new URL(url).origin !== pageOrigin;
    } catch (_) {
      return false;
    }
  }

  // ─── 1. MutationObserver – DOM mutation rate ───────────────────────────────
  let mutationRateTimer = null;

  function resetMutationWindow() {
    const windowStart = Date.now() - 1_000;
    mutationWindow = mutationWindow.filter(t => t >= windowStart);
  }

  function onMutations(records) {
    const ts = Date.now();
    for (let i = 0; i < records.length; i++) {
      mutationWindow.push(ts);
    }
    mutationCount += records.length;

    resetMutationWindow();
    const rate = mutationWindow.length; // mutations in last second

    if (rate > MUTATION_RATE_THRESHOLD) {
      enqueue('dom.mutation_burst', location.href, {
        rate_per_sec     : rate,
        total_since_load : mutationCount,
        page_age_ms      : Math.round(performance.now() - pageLoadTime),
        record_count     : records.length,
        types            : [...new Set(records.map(r => r.type))],
      });
    }
  }

  // Only observe if we have a document body (might be called at document_start)
  function attachMutationObserver() {
    const target = document.body || document.documentElement;
    if (!target) return;

    const observer = new MutationObserver(onMutations);
    observer.observe(target, {
      childList : true,
      subtree   : true,
      attributes: true,
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', attachMutationObserver, { once: true });
  } else {
    attachMutationObserver();
  }

  // ─── 2. XMLHttpRequest patching ───────────────────────────────────────────
  const OriginalXHR   = window.XMLHttpRequest;
  const xhrOpen       = OriginalXHR.prototype.open;
  const xhrSend       = OriginalXHR.prototype.send;
  const xhrSetHeader  = OriginalXHR.prototype.setRequestHeader;

  window.XMLHttpRequest = function () {
    const xhr       = new OriginalXHR(...arguments);
    let   xhrMeta   = { url: '', method: 'GET', startTime: 0, headers: {} };

    // Patch open()
    xhr.open = function (method, url, ...rest) {
      xhrMeta.method = method;
      xhrMeta.url    = url;
      return xhrOpen.apply(this, [method, url, ...rest]);
    };

    // Patch setRequestHeader() for header tracking
    xhr.setRequestHeader = function (name, value) {
      xhrMeta.headers[name.toLowerCase()] = value;
      return xhrSetHeader.apply(this, arguments);
    };

    // Patch send()
    xhr.send = function (body) {
      xhrMeta.startTime = performance.now();

      const handleEnd = () => {
        const duration      = Math.round(performance.now() - xhrMeta.startTime);
        const crossOrigin   = isCrossOrigin(xhrMeta.url);
        const contentLength = xhr.getResponseHeader?.('content-length');

        enqueue('xhr.completed', xhrMeta.url, {
          method         : xhrMeta.method,
          status         : xhr.status,
          status_text    : xhr.statusText,
          duration_ms    : duration,
          response_size  : contentLength ? parseInt(contentLength, 10) : null,
          response_type  : xhr.responseType || 'text',
          cross_origin   : crossOrigin,
          has_body       : body != null && body !== '',
        });
      };

      const handleError = () => {
        enqueue('xhr.error', xhrMeta.url, {
          method      : xhrMeta.method,
          duration_ms : Math.round(performance.now() - xhrMeta.startTime),
          cross_origin: isCrossOrigin(xhrMeta.url),
        });
      };

      xhr.addEventListener('loadend',  handleEnd,   { once: true });
      xhr.addEventListener('error',    handleError, { once: true });
      xhr.addEventListener('abort',    handleError, { once: true });
      xhr.addEventListener('timeout',  handleError, { once: true });

      return xhrSend.apply(this, arguments);
    };

    return xhr;
  };

  // Copy prototype so instanceof checks still work
  window.XMLHttpRequest.prototype = OriginalXHR.prototype;

  // ─── 3. fetch() patching ──────────────────────────────────────────────────
  const originalFetch = window.fetch;

  window.fetch = async function (input, init = {}) {
    let url    = '';
    let method = (init.method || 'GET').toUpperCase();

    try {
      if (typeof input === 'string') {
        url = input;
      } else if (input instanceof URL) {
        url = input.href;
      } else if (input instanceof Request) {
        url    = input.url;
        method = (input.method || method).toUpperCase();
      }
    } catch (_) {}

    const startTime   = performance.now();
    const crossOrigin = isCrossOrigin(url);

    try {
      const response = await originalFetch.apply(this, arguments);
      const duration = Math.round(performance.now() - startTime);

      // Read content-length without consuming the body
      const contentLength = response.headers.get('content-length');

      enqueue('fetch.completed', url, {
        method        : method,
        status        : response.status,
        status_text   : response.statusText,
        duration_ms   : duration,
        response_size : contentLength ? parseInt(contentLength, 10) : null,
        content_type  : response.headers.get('content-type') || null,
        cross_origin  : crossOrigin,
        ok            : response.ok,
        redirected    : response.redirected,
        response_url  : response.url !== url ? response.url : null,
      });

      // Detect redirect chain (response URL differs from request URL)
      if (response.redirected && response.url && response.url !== url) {
        enqueue('fetch.redirect', url, {
          original_url : url,
          final_url    : response.url,
          duration_ms  : duration,
          cross_origin : isCrossOrigin(response.url),
        });
      }

      return response;
    } catch (err) {
      const duration = Math.round(performance.now() - startTime);
      enqueue('fetch.error', url, {
        method      : method,
        duration_ms : duration,
        error       : err.message,
        cross_origin: crossOrigin,
      });
      throw err; // re-throw so calling code still sees the error
    }
  };

  // ─── 4. Redirect-chain detection via location/referrer ────────────────────
  const initialHref     = location.href;
  const initialReferrer = document.referrer;

  // Detect referrer origin mismatch (cross-origin redirect)
  if (initialReferrer && isCrossOrigin(initialReferrer)) {
    enqueue('redirect.cross_origin_referrer', location.href, {
      referrer      : initialReferrer,
      referrer_origin: (() => { try { return new URL(initialReferrer).origin; } catch (_) { return ''; } })(),
      page_origin   : pageOrigin,
    });
  }

  // Watch for SPA navigation (history API pushState / replaceState)
  const originalPushState    = history.pushState;
  const originalReplaceState = history.replaceState;

  function onHistoryChange(type, url) {
    const from = location.href;
    enqueue('navigation.spa_' + type, url || from, {
      from_url: from,
      to_url  : url || from,
    });
  }

  history.pushState = function (...args) {
    onHistoryChange('push', args[2]);
    return originalPushState.apply(this, args);
  };

  history.replaceState = function (...args) {
    onHistoryChange('replace', args[2]);
    return originalReplaceState.apply(this, args);
  };

  window.addEventListener('popstate', () => {
    enqueue('navigation.spa_pop', location.href, { url: location.href });
  });

  // ─── 5. Cross-origin request counters ────────────────────────────────────
  // Reported in the periodic flush summary
  let crossOriginXhrCount   = 0;
  let crossOriginFetchCount = 0;

  // Count cross-origin events accumulating in localQueue
  function countCrossOrigin() {
    let xhrCo = 0, fetchCo = 0;
    for (const ev of localQueue) {
      if (ev.metadata.cross_origin) {
        if (ev.type.startsWith('xhr.'))   xhrCo++;
        if (ev.type.startsWith('fetch.')) fetchCo++;
      }
    }
    crossOriginXhrCount   += xhrCo;
    crossOriginFetchCount += fetchCo;
  }

  // ─── 6. Periodic flush to background.js ──────────────────────────────────
  function flushToBackground() {
    if (localQueue.length === 0) return;

    countCrossOrigin();

    const toSend = localQueue.splice(0, localQueue.length);

    // Append a summary metric event
    toSend.push({
      type     : 'content.summary',
      url      : location.href,
      timestamp: now(),
      metadata : {
        total_mutations        : mutationCount,
        cross_origin_xhr_total : crossOriginXhrCount,
        cross_origin_fetch_total: crossOriginFetchCount,
        page_age_ms            : Math.round(performance.now() - pageLoadTime),
        batch_size             : toSend.length,
      },
    });

    try {
      chrome.runtime.sendMessage(
        { type: 'CONTENT_EVENTS', events: toSend },
        (response) => {
          // Ignore response; suppress "Extension context invalidated" errors
          void chrome.runtime.lastError;
        }
      );
    } catch (_) {
      // Extension context may be invalidated after reload – silently ignore
    }
  }

  // Start periodic flushing
  const flushTimer = setInterval(flushToBackground, SEND_INTERVAL_MS);

  // Flush remaining events when the page is being hidden/unloaded
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushToBackground();
  });

  window.addEventListener('pagehide', flushToBackground, { once: true });

  // Cleanup on unload
  window.addEventListener('unload', () => {
    clearInterval(flushTimer);
  });

  // ─── 7. Track page load performance ──────────────────────────────────────
  window.addEventListener('load', () => {
    const nav = performance.getEntriesByType('navigation')[0];
    if (nav) {
      enqueue('page.load', location.href, {
        dns_ms          : Math.round(nav.domainLookupEnd  - nav.domainLookupStart),
        connect_ms      : Math.round(nav.connectEnd       - nav.connectStart),
        ttfb_ms         : Math.round(nav.responseStart    - nav.requestStart),
        dom_content_ms  : Math.round(nav.domContentLoadedEventEnd - nav.startTime),
        load_ms         : Math.round(nav.loadEventEnd     - nav.startTime),
        transfer_size   : nav.transferSize    || 0,
        encoded_size    : nav.encodedBodySize || 0,
        decoded_size    : nav.decodedBodySize || 0,
        redirect_count  : nav.redirectCount  || 0,
        protocol        : nav.nextHopProtocol || null,
        nav_type        : nav.type,
      });
    }
  }, { once: true });

})();

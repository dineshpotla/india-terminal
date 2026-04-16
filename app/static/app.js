/* ═══════════════════════════════════════════════════════════════════════
   India Market Terminal — Frontend
   ═══════════════════════════════════════════════════════════════════════ */

(function () {
    "use strict";

    let ws = null;
    let chart = null;
    let chartSeries = null;
    let volumeSeries = null;
    let currentNewsTab = "all";
    let selectedStock = null;
    let dashboardData = null;
    let currentView = "investing";
    let watchlist = [];
    let mfChart = null;
    let mfFundSeries = null;
    let mfBenchmarkSeries = null;
    let mutualChartRenderKey = "";
    let newsLlmState = {
        news_llm_pending: undefined,
        news_llm_enabled: undefined,
    };
    let panelState = {
        bootstrap: null,
        overview: null,
        global: null,
        watchlistQuotes: null,
        news: {
            all: null,
            breaking: null,
            watchlist: null,
        },
    };
    let mutualState = {
        watchlist: [],
        storage: null,
        durable: false,
        selectedSchemeCode: null,
        selectedBenchmark: null,
        selectedRange: "max",
        compare: null,
        compareLoading: false,
        compareError: null,
    };
    let stockCache = {};
    const stockFetchInflight = {};
    let watchlistLoadPromise = null;
    let lastWatchlistQuoteRefreshAt = 0;
    let watchlistQuotesLoadPromise = null;
    let mutualHoldingsLoadPromise = null;
    let mutualCompareLoadPromise = null;
    let mutualCompareRequestSeq = 0;
    let mutualCompareAbortController = null;
    let mutualSearchAbortController = null;
    let mutualSearchResults = [];
    let newsRetryTimers = {
        all: null,
        breaking: null,
        watchlist: null,
    };

    const $ = (id) => document.getElementById(id);
    const $clock       = $("clock");
    const $status      = $("market-status");
    const $lastUpdate  = $("last-update");
    const $indices     = $("indices-body");
    const $breadth     = $("market-breadth");
    const $movers      = $("movers-body");
    const $news        = $("news-body");
    const $stockTitle  = $("stock-title");
    const $stockHero   = $("stock-hero");
    const $chartBox    = $("chart-container");
    const $sectors     = $("sectors-body");
    const $gmGrid      = $("gm-grid");
    const $gmStatus    = $("gm-status");
    const $gmUpdated   = $("gm-updated");
    const $gmHeatmap   = $("gm-heatmap");
    const $mfInput     = $("mf-input");
    const $mfSuggest   = $("mf-suggest");
    const $mfStatus    = $("mf-status");
    const $mfHoldings  = $("mf-holdings");
    const $mfDetail    = $("mf-detail");
    const $mfHero      = $("mf-hero");
    const $mfBenchmarkBlock = $("mf-benchmark-block");
    const $mfBenchmarks = $("mf-benchmarks");
    const $mfRangeBlock = $("mf-range-block");
    const $mfRanges    = $("mf-ranges");
    const $mfStats     = $("mf-stats");
    const $mfChartShell = $("mf-chart-shell");
    const $mfChartTitle = $("mf-chart-title");
    const $mfChartNote = $("mf-chart-note");
    const $mfChartBox  = $("mf-chart");
    const $search      = $("search-input");
    const $results     = $("search-results");
    const $wlInput     = $("wl-input");
    const $wlSuggest   = $("wl-suggest");
    const $wlTable     = $("wl-table");
    const $wlTableWrap = $("wl-table-wrap");
    const $chartCtrl   = $("chart-controls");
    const $giftPrice   = $("gift-price");
    const $giftPts     = $("gift-pts");
    const $giftPct     = $("gift-pct");
    const $newsLlmStack = $("news-llm-stack");
    const $newsLlmCount = $("news-llm-stack-count");

    const GM_HEATMAP_BACKDROP = [
        "M38 176 C60 136 109 112 170 108 C226 104 284 118 332 146 C366 168 388 205 389 238 C388 266 374 293 349 312 C319 335 281 348 235 354 C189 361 143 355 104 337 C76 323 58 302 47 278 C34 250 29 209 38 176 Z",
        "M284 338 C303 352 320 375 331 402 C344 433 345 469 335 499 C326 526 309 548 291 556 C278 560 267 550 264 534 C259 511 261 488 256 467 C248 438 236 415 231 390 C228 369 236 350 252 339 C262 331 273 330 284 338 Z",
        "M382 88 C405 75 438 71 470 77 C494 83 510 98 511 118 C512 140 497 158 473 170 C447 181 419 179 398 166 C379 153 370 134 370 114 C371 103 375 95 382 88 Z",
        "M566 180 C584 163 613 155 642 156 C664 158 682 168 690 182 C698 197 694 214 680 228 C661 244 633 252 605 252 C584 251 568 243 559 229 C550 215 552 196 566 180 Z",
        "M592 256 C617 248 649 252 677 266 C702 282 720 308 724 340 C728 373 720 408 704 440 C685 477 659 505 630 514 C610 519 592 510 582 491 C572 473 573 447 575 421 C578 395 574 367 568 339 C564 314 570 287 592 256 Z",
        "M700 152 C744 129 803 118 866 117 C926 116 986 125 1036 144 C1083 161 1120 188 1132 222 C1141 250 1135 281 1116 309 C1090 344 1046 366 995 373 C942 381 889 373 844 355 C806 341 771 321 742 291 C716 264 697 232 693 200 C691 181 694 164 700 152 Z",
        "M978 412 C1004 398 1040 395 1071 403 C1098 411 1118 427 1122 449 C1126 471 1119 494 1099 511 C1075 529 1042 536 1012 531 C986 525 967 508 963 486 C959 461 964 433 978 412 Z",
    ];

    const GM_HEATMAP_SPECS = [
        {
            key: "usa",
            label: "USA",
            shortLabel: "US",
            source: "US composite",
            names: ["S&P 500", "NASDAQ", "DOW JONES", "RUSSELL 2000"],
            mode: "average",
            path: "M114 229 C124 207 153 191 188 185 C224 178 266 184 298 198 C324 210 337 226 335 242 C332 255 318 266 299 274 C282 281 270 290 250 295 C222 302 190 303 163 296 C139 289 121 277 113 261 C108 251 108 240 114 229 Z",
            labelX: 223, labelY: 213, pctX: 223, pctY: 234, metaX: 223, metaY: 252,
        },
        {
            key: "uk",
            label: "UK",
            source: "FTSE 100",
            names: ["FTSE 100"],
            path: "M622 186 C628 178 637 176 644 181 C649 188 649 198 644 207 C638 214 629 215 622 209 C618 202 617 193 622 186 Z",
            labelX: 633, labelY: 190, pctX: 633, pctY: 206, metaX: 633, metaY: 223,
        },
        {
            key: "france",
            label: "FRANCE",
            shortLabel: "FR",
            source: "CAC 40",
            names: ["CAC 40"],
            path: "M642 223 C651 215 664 213 676 219 C686 228 687 243 679 254 C668 263 652 264 641 253 C634 243 635 232 642 223 Z",
            labelX: 660, labelY: 228, pctX: 660, pctY: 244, metaX: 660, metaY: 260,
        },
        {
            key: "germany",
            label: "GERMANY",
            shortLabel: "DE",
            source: "DAX",
            names: ["DAX"],
            path: "M682 198 C690 192 703 192 712 200 C719 211 718 227 711 238 C703 246 691 247 682 240 C675 229 675 210 682 198 Z",
            labelX: 698, labelY: 205, pctX: 698, pctY: 221, metaX: 698, metaY: 237,
        },
        {
            key: "eurozone",
            label: "EU STOXX",
            shortLabel: "EU",
            source: "EURO STOXX 50",
            names: ["EURO STOXX 50"],
            path: "M716 213 C732 205 752 205 767 214 C776 225 775 240 764 250 C749 258 728 258 714 248 C706 238 707 224 716 213 Z",
            labelX: 741, labelY: 220, pctX: 741, pctY: 236, metaX: 741, metaY: 252,
        },
        {
            key: "india",
            label: "INDIA",
            shortLabel: "IN",
            source: "GIFT NIFTY",
            names: ["GIFT NIFTY"],
            path: "M878 300 C887 291 900 289 911 293 C920 299 924 309 922 320 C920 331 916 341 911 350 C905 360 895 364 885 360 C877 354 873 344 872 333 C870 322 871 309 878 300 Z",
            labelX: 897, labelY: 306, pctX: 897, pctY: 326, metaX: 897, metaY: 345,
        },
        {
            key: "china",
            label: "CHINA",
            shortLabel: "CN",
            source: "SHANGHAI",
            names: ["SHANGHAI"],
            path: "M931 231 C954 217 985 213 1014 218 C1039 223 1058 236 1063 253 C1065 269 1060 283 1046 294 C1026 302 1000 304 974 301 C955 299 940 291 932 279 C927 266 926 246 931 231 Z",
            labelX: 988, labelY: 238, pctX: 988, pctY: 261, metaX: 988, metaY: 280,
        },
        {
            key: "hongkong",
            label: "HK",
            source: "HANG SENG",
            names: ["HANG SENG"],
            path: "M977 287 C983 282 993 282 1001 286 C1006 293 1006 304 1000 311 C993 316 983 316 976 311 C971 304 971 293 977 287 Z",
            labelX: 989, labelY: 290, pctX: 989, pctY: 305, metaX: 989, metaY: 320,
        },
        {
            key: "korea",
            label: "KOREA",
            shortLabel: "KR",
            source: "KOSPI",
            names: ["KOSPI"],
            path: "M1028 245 C1036 238 1047 236 1056 243 C1062 252 1062 264 1056 274 C1048 281 1037 282 1029 274 C1024 265 1023 253 1028 245 Z",
            labelX: 1041, labelY: 249, pctX: 1041, pctY: 267, metaX: 1041, metaY: 285,
        },
        {
            key: "taiwan",
            label: "TAIWAN",
            shortLabel: "TW",
            source: "TAIWAN",
            names: ["TAIWAN"],
            path: "M1048 293 C1055 286 1066 286 1072 294 C1077 303 1074 316 1066 325 C1058 332 1049 329 1044 319 C1041 310 1042 300 1048 293 Z",
            labelX: 1058, labelY: 297, pctX: 1058, pctY: 315, metaX: 1058, metaY: 333,
        },
        {
            key: "japan",
            label: "JAPAN",
            shortLabel: "JP",
            source: "NIKKEI 225",
            names: ["NIKKEI 225"],
            path: "M1087 216 C1096 208 1107 210 1111 221 C1112 230 1107 238 1109 248 C1112 257 1110 266 1101 270 C1093 269 1089 261 1087 252 C1085 243 1079 236 1079 228 C1080 222 1082 219 1087 216 Z",
            labelX: 1098, labelY: 220, pctX: 1098, pctY: 240, metaX: 1098, metaY: 259,
        },
        {
            key: "singapore",
            label: "SG",
            source: "STRAITS TIMES",
            names: ["STRAITS TIMES"],
            path: "M951 360 C958 356 969 356 977 360 C983 366 983 376 978 383 C970 388 959 388 950 384 C944 378 944 367 951 360 Z",
            labelX: 964, labelY: 365, pctX: 964, pctY: 379, metaX: 964, metaY: 394,
        },
        {
            key: "thailand",
            label: "THAILAND",
            shortLabel: "TH",
            source: "SET COMPOSITE",
            names: ["SET COMPOSITE"],
            path: "M932 321 C943 314 958 314 970 321 C976 331 974 344 964 352 C951 358 936 357 928 348 C923 340 923 329 932 321 Z",
            labelX: 951, labelY: 327, pctX: 951, pctY: 344, metaX: 951, metaY: 360,
        },
        {
            key: "indonesia",
            label: "INDONESIA",
            shortLabel: "ID",
            source: "JAKARTA",
            names: ["JAKARTA"],
            path: "M978 391 C994 385 1015 384 1036 387 C1052 390 1064 398 1066 406 C1065 414 1056 421 1043 425 C1024 429 1002 430 984 426 C972 421 966 414 966 405 C968 398 972 393 978 391 Z",
            labelX: 1016, labelY: 394, pctX: 1016, pctY: 409, metaX: 1016, metaY: 425,
        },
    ];

    // ── Price change tracking (orderbook-style flash) ──────────────────

    var prevPrices = {};

    function flashDir(key, price) {
        var prev = prevPrices[key];
        prevPrices[key] = price;
        if (prev == null || prev === price) return null;
        return price > prev ? "green" : "red";
    }

    function applyFlash(element, dir) {
        if (!dir) return;
        element.classList.remove("flash-green", "flash-red");
        void element.offsetWidth;
        element.classList.add(dir === "green" ? "flash-green" : "flash-red");
    }

    function applyTextFlash(element, dir) {
        if (!dir) return;
        element.classList.remove("flash-green-text", "flash-red-text");
        void element.offsetWidth;
        element.classList.add(dir === "green" ? "flash-green-text" : "flash-red-text");
    }

    // ── Helpers ────────────────────────────────────────────────────────

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function fmtPrice(n) {
        if (n == null) return "\u2014";
        return "\u20b9" + Number(n).toLocaleString("en-IN", {
            minimumFractionDigits: 2, maximumFractionDigits: 2,
        });
    }

    function fmtChange(n) {
        if (n == null) return "\u2014";
        return (n >= 0 ? "+" : "") + n.toFixed(2);
    }

    function fmtPct(n) {
        if (n == null) return "\u2014";
        return (n >= 0 ? "+" : "") + n.toFixed(2) + "%";
    }

    function fmtVol(n) {
        if (n == null) return "\u2014";
        if (n >= 1e7) return (n / 1e7).toFixed(2) + " Cr";
        if (n >= 1e5) return (n / 1e5).toFixed(2) + " L";
        if (n >= 1e3) return (n / 1e3).toFixed(1) + " K";
        return String(n);
    }

    function fmtCompactMoney(n) {
        if (n == null) return "\u2014";
        var abs = Math.abs(Number(n));
        if (abs >= 1e7) return "\u20b9" + (abs / 1e7).toFixed(abs >= 1e8 ? 1 : 2) + " Cr";
        if (abs >= 1e5) return "\u20b9" + (abs / 1e5).toFixed(abs >= 1e6 ? 1 : 2) + " L";
        if (abs >= 1e3) return "\u20b9" + (abs / 1e3).toFixed(abs >= 1e4 ? 1 : 2) + " K";
        return "\u20b9" + abs.toLocaleString("en-IN", { maximumFractionDigits: 2 });
    }

    function fmtSignedMoney(n) {
        if (n == null) return "\u2014";
        var value = Number(n);
        var sign = value >= 0 ? "+" : "-";
        return sign + fmtCompactMoney(Math.abs(value)).replace("\u20b9", "\u20b9");
    }

    function fmtDateLabel(value) {
        if (!value) return "\u2014";
        var dt = new Date(value);
        if (isNaN(dt.getTime())) return value;
        return dt.toLocaleDateString("en-IN", {
            day: "2-digit",
            month: "short",
            year: "numeric",
        });
    }

    function titleCase(text) {
        return String(text || "")
            .replace(/_/g, " ")
            .trim()
            .split(/\s+/)
            .map(function (part) {
                return part ? part.charAt(0).toUpperCase() + part.slice(1).toLowerCase() : "";
            })
            .join(" ");
    }

    function cls(val) {
        return val > 0 ? "up" : val < 0 ? "down" : "flat";
    }

    function arrow(val) {
        return val > 0 ? "\u25b2" : val < 0 ? "\u25bc" : "\u25cf";
    }

    function clearChildren(node) {
        while (node.firstChild) node.removeChild(node.firstChild);
    }

    /** Bottom-right badge: headlines waiting for server-side LLM classification. */
    function renderNewsLlmStack(data) {
        if (!$newsLlmStack || !$newsLlmCount) return;
        if (!data || (data.news_llm_pending === undefined && data.news_llm_enabled === undefined)) {
            return;
        }
        var pending = Number(data.news_llm_pending);
        if (isNaN(pending) || pending < 0) pending = 0;
        var enabled = data.news_llm_enabled !== false;
        var suffix = $newsLlmStack.querySelector(".news-llm-stack-suffix");
        $newsLlmStack.hidden = false;
        if (!enabled) {
            $newsLlmStack.className = "news-llm-stack news-llm-stack--off";
            $newsLlmCount.textContent = "OFF";
            if (suffix) suffix.textContent = "disabled";
            $newsLlmStack.title = "Headline AI classification is not enabled on this server";
            return;
        }
        $newsLlmStack.className = "news-llm-stack" + (pending > 0 ? " news-llm-stack--busy" : "");
        $newsLlmCount.textContent = String(pending);
        if (suffix) suffix.textContent = "queued";
        $newsLlmStack.title = pending === 0
            ? "No headlines waiting for AI classification"
            : pending + " headline(s) queued for AI classification";
    }

    function updateNewsLlmState(data) {
        if (!data) return;
        var changed = false;
        if (data.news_llm_enabled !== undefined) {
            newsLlmState.news_llm_enabled = data.news_llm_enabled !== false;
            changed = true;
        }
        if (data.news_llm_pending !== undefined) {
            var pending = Number(data.news_llm_pending);
            newsLlmState.news_llm_pending = isNaN(pending) || pending < 0 ? 0 : pending;
            changed = true;
        }
        if (changed) {
            renderNewsLlmStack(newsLlmState);
        }
    }

    // Show an immediate placeholder so the badge doesn't look "broken" while the
    // first dashboard fetch can take time (especially on serverless cold starts).
    if ($newsLlmStack && $newsLlmCount) {
        var suffix0 = $newsLlmStack.querySelector(".news-llm-stack-suffix");
        $newsLlmStack.hidden = false;
        $newsLlmStack.className = "news-llm-stack news-llm-stack--busy";
        $newsLlmCount.textContent = "…";
        if (suffix0) suffix0.textContent = "sync";
        $newsLlmStack.title = "Syncing with server…";
    }

    function watchlistHash(symbols) {
        var input = (symbols || []).join(",");
        var hash = 2166136261;
        for (var i = 0; i < input.length; i++) {
            hash ^= input.charCodeAt(i);
            hash = Math.imul(hash, 16777619);
        }
        return (hash >>> 0).toString(16);
    }

    function panelCacheKey(kind) {
        if (kind === "news:watchlist") {
            return "imt_panel:news:watchlist:" + watchlistHash(watchlist);
        }
        return "imt_panel:" + kind;
    }

    function loadPanelCache(kind) {
        try {
            var raw = localStorage.getItem(panelCacheKey(kind));
            return raw ? JSON.parse(raw) : null;
        } catch (err) {
            return null;
        }
    }

    function savePanelCache(kind, payload) {
        try {
            localStorage.setItem(panelCacheKey(kind), JSON.stringify(payload));
        } catch (err) {}
    }

    function removePanelCache(kind, hash) {
        try {
            var key = kind === "news:watchlist"
                ? "imt_panel:news:watchlist:" + (hash || watchlistHash(watchlist))
                : "imt_panel:" + kind;
            localStorage.removeItem(key);
        } catch (err) {}
    }

    function panelAgeMs(data) {
        if (!data || !data.as_of) return Infinity;
        var ts = Date.parse(data.as_of);
        return isNaN(ts) ? Infinity : Math.max(0, Date.now() - ts);
    }

    function shouldRefreshNewsPanel(tab, maxAgeMs) {
        var env = tab === "watchlist"
            ? panelState.news.watchlist
            : (tab === "breaking" ? panelState.news.breaking : panelState.news.all);
        if (!env) return true;
        if (env.refreshing) return false;
        if (env.stale) return true;
        return panelAgeMs(env) >= maxAgeMs;
    }

    function activeNewsRequestTab() {
        if (currentNewsTab === "watchlist") return "watchlist";
        if (currentNewsTab === "breaking") return "breaking";
        return "all";
    }

    function clearNewsRetry(tab) {
        if (!newsRetryTimers[tab]) return;
        clearTimeout(newsRetryTimers[tab]);
        newsRetryTimers[tab] = null;
    }

    function scheduleNewsRetry(tab, delayMs) {
        clearNewsRetry(tab);
        newsRetryTimers[tab] = setTimeout(function () {
            newsRetryTimers[tab] = null;
            if (document.hidden || !isNewsVisible()) return;
            if (tab === "watchlist" && currentNewsTab !== "watchlist") return;
            if (tab === "breaking" && currentNewsTab !== "breaking") return;
            if (tab === "all" && ["all", "india", "global", "gold_silver"].indexOf(currentNewsTab) === -1) return;
            loadNewsPanel(tab);
        }, delayMs);
    }

    async function fetchJson(url, opts) {
        opts = opts || {};
        var timeoutMs = Number(opts.timeoutMs || 0);
        var externalSignal = opts.signal || null;
        var controller = null;
        var timer = null;
        var abortListener = null;
        if (timeoutMs > 0 && typeof AbortController !== "undefined") {
            controller = new AbortController();
            timer = setTimeout(function () {
                controller.abort();
            }, timeoutMs);
        }
        var res;
        var fetchOpts = {};
        if (controller && externalSignal) {
            if (externalSignal.aborted) controller.abort();
            else {
                abortListener = function () { controller.abort(); };
                externalSignal.addEventListener("abort", abortListener, { once: true });
            }
        }
        if (controller) fetchOpts.signal = controller.signal;
        else if (externalSignal) fetchOpts.signal = externalSignal;
        if (opts.method) fetchOpts.method = opts.method;
        if (opts.headers) fetchOpts.headers = opts.headers;
        if (opts.body !== undefined) fetchOpts.body = opts.body;
        try {
            res = await fetch(url, fetchOpts);
        } catch (err) {
            if (abortListener && externalSignal) {
                externalSignal.removeEventListener("abort", abortListener);
            }
            if (timer) clearTimeout(timer);
            if (err && err.name === "AbortError") {
                throw new Error("Request cancelled");
            }
            throw err;
        } finally {
            if (abortListener && externalSignal) {
                externalSignal.removeEventListener("abort", abortListener);
            }
            if (timer) clearTimeout(timer);
        }
        if (!res.ok) {
            var message = "request failed: " + url;
            try {
                var errPayload = await res.json();
                message = errPayload.detail || errPayload.error || errPayload.message || message;
            } catch (err) {}
            throw new Error(message);
        }
        return await res.json();
    }

    function isPanelVisible(panelId) {
        if (document.hidden || currentView !== "investing") return false;
        if (!isMobile.matches) return true;
        var node = document.getElementById(panelId);
        return !!(node && node.classList.contains("mobile-active"));
    }

    function isNewsVisible() {
        return isPanelVisible("panel-news");
    }

    function isWatchlistVisible() {
        return isPanelVisible("panel-stock");
    }

    // ── Clock ──────────────────────────────────────────────────────────

    function tickClock() {
        const now = new Date();
        const ist = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Kolkata" }));
        $clock.textContent =
            String(ist.getHours()).padStart(2, "0") + ":" +
            String(ist.getMinutes()).padStart(2, "0") + ":" +
            String(ist.getSeconds()).padStart(2, "0");
    }
    setInterval(tickClock, 1000);
    tickClock();

    // ── Render: GIFT Nifty ───────────────────────────────────────────────

    function renderGiftNifty(gn) {
        if (!gn) return;
        $giftPrice.textContent = Number(gn.price).toLocaleString("en-IN", { maximumFractionDigits: 2 });
        var c = gn.change_pct >= 0 ? "up" : "down";
        $giftPts.textContent = fmtChange(gn.change);
        $giftPts.className = "gift-pts " + c;
        $giftPct.textContent = fmtPct(gn.change_pct);
        $giftPct.className = "gift-pct " + c;

        var dir = flashDir("gift-nifty", gn.price);
        applyTextFlash($giftPrice, dir);
    }

    // ── Render: Indices ────────────────────────────────────────────────

    function renderIndices(indices) {
        if (!indices || !indices.length) return;
        clearChildren($indices);

        indices.forEach(function (idx) {
            var chip = el("div", "idx-chip");
            chip.appendChild(el("span", "idx-chip-name", idx.name));
            var priceSpan = el("span", "idx-chip-price",
                Number(idx.price).toLocaleString("en-IN", { maximumFractionDigits: 2 }));
            chip.appendChild(priceSpan);
            var chgSpan = el("span", "idx-chip-chg " + cls(idx.change_pct),
                arrow(idx.change) + " " + fmtPct(idx.change_pct));
            chip.appendChild(chgSpan);
            $indices.appendChild(chip);

            var dir = flashDir("idx:" + idx.name, idx.price);
            applyFlash(chip, dir);
            applyTextFlash(priceSpan, dir);
        });

        clearChildren($breadth);
        var breadthData = panelState.overview && panelState.overview.breadth;
        if (breadthData && breadthData.advances) {
            $breadth.appendChild(el("span", "up", "ADV:" + breadthData.advances));
            $breadth.appendChild(document.createTextNode(" "));
            $breadth.appendChild(el("span", "down", "DEC:" + breadthData.declines));
        }
    }

    // ── Render: Movers ─────────────────────────────────────────────────

    function renderMovers(movers) {
        if (!movers) return;
        clearChildren($movers);

        var gainers = movers.gainers || [];
        var losers = movers.losers || [];
        if (!gainers.length && !losers.length) {
            $movers.appendChild(el("div", "loading-placeholder", "No data yet"));
            return;
        }

        var wrap = el("div", "movers-split");

        function buildCol(title, list, colCls) {
            var col = el("div", "movers-col");
            col.appendChild(el("div", "movers-col-head " + colCls, title));
            list.forEach(function (s) {
                var row = el("div", "mover-row");
                row.dataset.sym = s.symbol;
                row.appendChild(el("span", "mover-sym", s.symbol));
                row.appendChild(el("span", "mover-price", fmtPrice(s.price)));
                row.appendChild(el("span", "mover-change " + cls(s.change_pct), fmtPct(s.change_pct)));
                row.addEventListener("click", function () { selectStock(s.symbol); });
                col.appendChild(row);
            });
            return col;
        }

        wrap.appendChild(buildCol("GAINERS", gainers, "up"));
        wrap.appendChild(buildCol("LOSERS", losers, "down"));
        $movers.appendChild(wrap);
    }

    // ── Render: News (Bloomberg wire style) ─────────────────────────────

    var prevNewsKeys = {};
    var newsRenderTs = 0;

    function filterNews(news) {
        if (!news) return [];
        if (currentNewsTab === "all") return news;
        if (currentNewsTab === "breaking") {
            return news.filter(function (n) {
                return n.breaking && !n.stock_event && !n.company_specific && n.india_market_impact;
            });
        }
        if (currentNewsTab === "global") {
            return news.filter(function (n) {
                return n.global_news && n.market_relevant && !n.stock_event && !n.company_specific;
            });
        }
        if (currentNewsTab === "india") {
            return news.filter(function (n) { return n.india_news; });
        }
        if (currentNewsTab === "gold_silver") {
            return news.filter(function (n) { return n.gold_silver; });
        }
        if (currentNewsTab === "watchlist") {
            if (!watchlist.length) return [];
            var wlSet = {};
            watchlist.forEach(function (s) { wlSet[s] = true; });
            return news.filter(function (n) {
                var linked = (n.watchlist_stocks && n.watchlist_stocks.length)
                    ? n.watchlist_stocks
                    : (n.keyword_stocks || []);
                return linked.some(function (s) { return wlSet[s]; });
            });
        }
        return news;
    }

    function timeClass(ageSecs) {
        if (ageSecs < 900) return "wire-time-fresh";
        if (ageSecs < 3600) return "wire-time-warm";
        return "wire-time-old";
    }

    function renderNews(news, isLiveUpdate) {
        var filtered = filterNews(news);
        var wasScrolledTop = $news.scrollTop < 30;
        var newKeys = {};
        filtered.forEach(function (n) { newKeys[n.title.substring(0, 50)] = true; });

        clearChildren($news);

        if (!filtered || !filtered.length) {
            var emptyMsg = "Waiting for headlines\u2026";
            if (currentNewsTab === "breaking") emptyMsg = "No breaking news right now";
            else if (currentNewsTab === "india") emptyMsg = "No India news right now";
            else if (currentNewsTab === "global") emptyMsg = "No global news right now";
            else if (currentNewsTab === "gold_silver") emptyMsg = "No gold & silver news right now";
            else if (currentNewsTab === "watchlist") emptyMsg = "No watchlist news \u2014 add stocks above";
            $news.appendChild(el("div", "news-empty", emptyMsg));
            return;
        }

        newsRenderTs = Date.now();

        filtered.forEach(function (n) {
            var key = n.title.substring(0, 50);
            var isNew = isLiveUpdate && !prevNewsKeys[key];

            var row = el("div", "wire-row" + (isNew ? " wire-new" : ""));
            if (n.link) {
                row.addEventListener("click", function () { window.open(n.link, "_blank"); });
            }

            var tag = el("span", "wire-tag");
            if (n.breaking) {
                tag.className = "wire-tag wire-tag-breaking";
                tag.textContent = "BREAKING";
            } else if (n.is_fresh) {
                tag.className = "wire-tag wire-tag-just-in";
                tag.textContent = "JUST IN";
            }
            row.appendChild(tag);

            var timeEl = el("span", "wire-time " + timeClass(n.age_secs || 999999), n.time || "");
            timeEl.dataset.age = n.age_secs || 999999;
            row.appendChild(timeEl);

            var srcShort = (n.source || "").replace(/Markets?[-\s]*/i, "").substring(0, 10);
            row.appendChild(el("span", "wire-src", srcShort));

            var titleCls = "wire-title";
            if (n.sentiment && n.sentiment !== "neutral") {
                titleCls += " wire-sent-" + n.sentiment;
            }
            var titleEl = el("span", titleCls, n.title);
            var chipStocks = (n.watchlist_stocks && n.watchlist_stocks.length) ? n.watchlist_stocks
                : (n.keyword_stocks && n.keyword_stocks.length) ? n.keyword_stocks : [];
            if (chipStocks.length) {
                var chips = el("span", "wire-chips");
                chipStocks.slice(0, 3).forEach(function (sym) {
                    var chip = el("span", "wire-chip", sym);
                    chip.addEventListener("click", function (e) {
                        e.stopPropagation();
                        selectStock(sym);
                    });
                    chips.appendChild(chip);
                });
                titleEl.appendChild(chips);
            }
            row.appendChild(titleEl);
            if (n.impact === "high") {
                row.appendChild(el("span", "wire-impact-high", "HI"));
            }

            $news.appendChild(row);
        });

        if (wasScrolledTop) $news.scrollTop = 0;
        prevNewsKeys = newKeys;
    }

    function refreshNewsTimes() {
        if (!newsRenderTs) return;
        var elapsed = Math.floor((Date.now() - newsRenderTs) / 1000);
        var timeEls = $news.querySelectorAll(".wire-time");
        timeEls.forEach(function (te) {
            var base = parseInt(te.dataset.age, 10);
            if (isNaN(base) || base > 900000) return;
            var cur = base + elapsed;
            te.className = "wire-time " + timeClass(cur);
            if (cur < 60)        te.textContent = "just now";
            else if (cur < 3600) te.textContent = Math.floor(cur / 60) + "m ago";
            else if (cur < 86400)te.textContent = Math.floor(cur / 3600) + "h ago";
            else                 te.textContent = Math.floor(cur / 86400) + "d ago";
        });
    }
    setInterval(refreshNewsTimes, 30000);

    // ── Render: Sectors ────────────────────────────────────────────────

    function renderSectors(sectors) {
        if (!sectors || !sectors.length) return;
        clearChildren($sectors);

        var maxPos = 0.01, maxNeg = 0.01;
        sectors.forEach(function (s) {
            if (s.change_pct > maxPos) maxPos = s.change_pct;
            if (s.change_pct < -maxNeg) maxNeg = -s.change_pct;
        });
        var maxAbs = Math.max(maxPos, maxNeg, 0.01);

        sectors.forEach(function (s) {
            var row = el("div", "sector-row");
            row.appendChild(el("span", "sector-name", s.name));

            var barBg = el("div", "sector-bar-bg");
            var barLeft = el("div", "sector-bar-half sector-bar-neg");
            var barRight = el("div", "sector-bar-half sector-bar-pos");

            if (s.change_pct >= 0) {
                var w = Math.max(Math.abs(s.change_pct) / maxAbs * 100, 2);
                barRight.style.width = w + "%";
            } else {
                var w = Math.max(Math.abs(s.change_pct) / maxAbs * 100, 2);
                barLeft.style.width = w + "%";
            }
            barBg.appendChild(barLeft);
            barBg.appendChild(barRight);
            row.appendChild(barBg);

            row.appendChild(el("span", "sector-pct " + cls(s.change_pct), fmtPct(s.change_pct)));
            $sectors.appendChild(row);
        });
    }

    // ── Render: Global Markets View ──────────────────────────────────────

    function fmtGlobalPrice(n) {
        if (n == null) return "\u2014";
        if (n >= 1000) return Number(n).toLocaleString("en-US", {
            minimumFractionDigits: 2, maximumFractionDigits: 2,
        });
        return Number(n).toFixed(n < 10 ? 4 : 2);
    }

    function fmtGlobalChange(change, pct) {
        if (change == null || pct == null) return "\u2014";
        var sign = change >= 0 ? "+" : "-";
        var absChange = Math.abs(change);
        var changeDigits = absChange >= 100 ? 0 : (absChange >= 10 ? 1 : 2);
        var pctDigits = Math.abs(pct) >= 10 ? 1 : 2;
        var changeText = sign + absChange.toFixed(changeDigits);
        var pctText = (pct >= 0 ? "+" : "-") + Math.abs(pct).toFixed(pctDigits) + "%";
        return changeText + " (" + pctText + ")";
    }

    function globalHeatTone(pct) {
        var value = Number(pct);
        if (!isFinite(value)) {
            return {
                fill: "rgba(120, 136, 158, 0.18)",
                stroke: "rgba(148, 163, 184, 0.28)",
                text: "#98a7bb",
                shadow: "rgba(15, 23, 42, 0.08)",
            };
        }
        var strength = Math.min(Math.abs(value), 2.5) / 2.5;
        if (value > 0) {
            return {
                fill: "rgba(35, 197, 94, " + (0.20 + strength * 0.58).toFixed(3) + ")",
                stroke: "rgba(110, 231, 183, " + (0.34 + strength * 0.36).toFixed(3) + ")",
                text: "#f3fff7",
                shadow: "rgba(34, 197, 94, " + (0.10 + strength * 0.28).toFixed(3) + ")",
            };
        }
        if (value < 0) {
            return {
                fill: "rgba(239, 68, 68, " + (0.20 + strength * 0.58).toFixed(3) + ")",
                stroke: "rgba(252, 165, 165, " + (0.34 + strength * 0.34).toFixed(3) + ")",
                text: "#fff5f5",
                shadow: "rgba(239, 68, 68, " + (0.10 + strength * 0.28).toFixed(3) + ")",
            };
        }
        return {
            fill: "rgba(96, 165, 250, 0.12)",
            stroke: "rgba(125, 211, 252, 0.24)",
            text: "#d6deea",
            shadow: "rgba(59, 130, 246, 0.08)",
        };
    }

    function buildGlobalHeatmapEntries(futures) {
        var byName = {};
        (futures || []).forEach(function (row) {
            byName[row.name] = row;
        });
        return GM_HEATMAP_SPECS.map(function (spec) {
            var rows = (spec.names || []).map(function (name) { return byName[name]; }).filter(Boolean);
            var primary = rows[0] || null;
            var pct = null;
            if (rows.length) {
                if (spec.mode === "average" && rows.length > 1) {
                    var total = rows.reduce(function (sum, row) {
                        return sum + (isFinite(Number(row.change_pct)) ? Number(row.change_pct) : 0);
                    }, 0);
                    pct = total / rows.length;
                } else {
                    pct = Number(primary.change_pct);
                }
                if (!isFinite(pct)) pct = null;
            }
            var anyOpen = rows.some(function (row) { return row.session_status === "OPEN"; });
            var anyHoliday = rows.some(function (row) { return row.session_status === "HOLIDAY"; });
            return {
                spec: spec,
                rows: rows,
                primary: primary,
                pct: pct,
                status: anyOpen ? "OPEN" : (anyHoliday ? "HOLIDAY" : (primary ? primary.session_status : "UNKNOWN")),
            };
        });
    }

    function globalHeatDisplayLabel(spec) {
        return spec.shortLabel || spec.label;
    }

    function renderGlobalHeatmap(futures) {
        if (!$gmHeatmap) return;
        clearChildren($gmHeatmap);
        if (!futures || !futures.length) {
            $gmHeatmap.appendChild(el("div", "gm-loading", "Building country heatmap…"));
            return;
        }

        var entries = buildGlobalHeatmapEntries(futures);
        var svgMarkup = [
            '<svg class="gm-map-svg" viewBox="0 0 1200 560" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Live global markets heatmap">',
            '<defs>',
            '<filter id="gmCountryGlow" x="-30%" y="-30%" width="160%" height="160%">',
            '<feDropShadow dx="0" dy="8" stdDeviation="9" flood-color="rgba(0,0,0,.24)" />',
            '</filter>',
            '<filter id="gmChipShadow" x="-30%" y="-30%" width="160%" height="160%">',
            '<feDropShadow dx="0" dy="4" stdDeviation="6" flood-color="rgba(0,0,0,.22)" />',
            '</filter>',
            '</defs>',
            '<rect class="gm-map-bg" x="0" y="0" width="1200" height="560" rx="24" ry="24"></rect>',
            GM_HEATMAP_BACKDROP.map(function (path) {
                return '<path class="gm-map-backdrop" d="' + path + '"></path>';
            }).join(""),
            '<g class="gm-map-meridians">',
            '<path d="M150 80 C245 150 250 395 172 498"></path>',
            '<path d="M350 58 C430 152 435 392 368 514"></path>',
            '<path d="M570 52 C630 148 640 392 594 520"></path>',
            '<path d="M780 64 C840 160 852 392 814 506"></path>',
            '<path d="M990 84 C1038 178 1052 378 1030 474"></path>',
            '</g>',
        ];

        entries.forEach(function (entry) {
            var spec = entry.spec;
            var tone = globalHeatTone(entry.pct);
            var pctText = entry.pct == null ? "\u2014" : fmtPct(entry.pct);
            var displayLabel = globalHeatDisplayLabel(spec);
            var chipWidth = Math.max(42, 28 + displayLabel.length * 8);
            var chipHeight = window.innerWidth < 920 ? 32 : 38;
            var chipX = spec.pctX - chipWidth / 2;
            var chipY = spec.pctY - chipHeight / 2 - 7;
            var title = spec.label + " \u00b7 " + spec.source + " \u00b7 " + pctText;
            if (entry.status === "OPEN") title += " \u00b7 open";
            else if (entry.status === "HOLIDAY") title += " \u00b7 holiday";
            svgMarkup.push(
                '<g class="gm-country' + (entry.status === "OPEN" ? ' is-open' : '') + '" tabindex="0">',
                '<title>' + title + '</title>',
                '<path class="gm-country-shape" d="' + spec.path + '" fill="' + tone.fill + '" stroke="' + tone.stroke + '" filter="url(#gmCountryGlow)" style="--gm-country-shadow:' + tone.shadow + ';"></path>',
                '<g class="gm-country-chip" filter="url(#gmChipShadow)">',
                '<rect class="gm-country-chip-bg" x="' + chipX.toFixed(1) + '" y="' + chipY.toFixed(1) + '" width="' + chipWidth.toFixed(1) + '" height="' + chipHeight + '" rx="14" ry="14" stroke="' + tone.stroke + '" fill="rgba(8,12,20,0.64)"></rect>',
                '<text class="gm-country-chip-label" x="' + spec.labelX + '" y="' + (spec.labelY + 2) + '" text-anchor="middle">' + displayLabel + '</text>',
                '<text class="gm-country-chip-pct" x="' + spec.pctX + '" y="' + (spec.pctY + 2) + '" text-anchor="middle" fill="' + tone.text + '">' + pctText + '</text>',
                '</g>',
                '</g>'
            );
        });

        svgMarkup.push("</svg>");
        $gmHeatmap.innerHTML = svgMarkup.join("");
    }

    function summarizeGlobalSessions(futures) {
        var summary = { open: 0, closed: 0, holiday: 0 };
        (futures || []).forEach(function (f) {
            if (f.session_status === "OPEN") summary.open += 1;
            else if (f.session_status === "HOLIDAY") summary.holiday += 1;
            else if (f.session_status === "CLOSED") summary.closed += 1;
        });
        return summary;
    }

    function updateGlobalStatus(streaming, lastUpdate, futures) {
        if ($gmStatus) {
            if (streaming) {
                $gmStatus.textContent = "LIVE STREAMING";
                $gmStatus.className = "gm-status streaming";
            } else {
                $gmStatus.textContent = "POLLING";
                $gmStatus.className = "gm-status polling";
            }
        }
        if ($gmUpdated) {
            var parts = [];
            if (lastUpdate) parts.push("Updated " + lastUpdate);
            if (futures && futures.length) {
                var summary = summarizeGlobalSessions(futures);
                parts.push(summary.open + " open");
                parts.push(summary.closed + " closed");
                if (summary.holiday) parts.push(summary.holiday + " holiday");
            }
            $gmUpdated.textContent = parts.join(" \u00b7 ");
        }
    }

    function renderGlobalMarkets(futures) {
        renderGlobalHeatmap(futures);
        clearChildren($gmGrid);
        if (!futures || !futures.length) {
            $gmGrid.appendChild(el("div", "gm-loading", "Fetching global markets…"));
            return;
        }

        var regions = [];
        var regionMap = {};
        futures.forEach(function (f) {
            var r = f.region || "OTHER";
            if (!regionMap[r]) {
                regionMap[r] = [];
                regions.push(r);
            }
            regionMap[r].push(f);
        });

        regions.forEach(function (region) {
            var section = el("div", "gm-section");

            var head = el("div", "gm-section-head");
            head.appendChild(el("span", "gm-section-title", region));
            section.appendChild(head);

            var table = el("table", "gm-table");
            var thead = el("thead");
            var headerRow = el("tr");
            ["NAME", "LTP", "CHANGE"].forEach(function (h) {
                headerRow.appendChild(el("th", "", h));
            });
            thead.appendChild(headerRow);
            table.appendChild(thead);

            var tbody = el("tbody");
            regionMap[region].forEach(function (f) {
                var tr = el("tr");

                var nameTd = el("td", "gm-name-cell");
                var nameWrap = el("div", "gm-name-wrap");
                nameWrap.appendChild(el("div", "gm-name", f.name));

                var chip = el("div", "gm-market-chip");
                var sessionState = f.session_status || "—";
                var chipClass = "gm-market-chip";
                if (sessionState === "OPEN") chipClass += " is-open";
                else if (sessionState === "HOLIDAY") chipClass += " is-holiday";
                else if (sessionState === "CLOSED") chipClass += " is-closed";
                else chipClass += " is-unknown";
                chip.className = chipClass;

                var chipState = el("span", "gm-market-chip-state", sessionState);
                chip.appendChild(chipState);

                var chipCountdown = el(
                    "span",
                    "gm-market-chip-countdown",
                    f.session_hint || "Awaiting live session clock"
                );
                chip.appendChild(chipCountdown);
                nameWrap.appendChild(chip);

                if (f.session_local_time) {
                    nameWrap.appendChild(el("div", "gm-name-meta", f.session_local_time));
                }
                nameTd.appendChild(nameWrap);
                tr.appendChild(nameTd);

                var priceTd = el("td", "gm-price", fmtGlobalPrice(f.price));
                tr.appendChild(priceTd);

                var chgTd = el("td", "gm-chg " + cls(f.change_pct), fmtGlobalChange(f.change, f.change_pct));
                tr.appendChild(chgTd);

                tbody.appendChild(tr);

                var dir = flashDir("gm:" + f.name, f.price);
                applyFlash(tr, dir);
                applyTextFlash(priceTd, dir);
            });
            table.appendChild(tbody);
            section.appendChild(table);
            $gmGrid.appendChild(section);
        });
    }

    function applyBootstrap(data) {
        panelState.bootstrap = data || null;
        updateNewsLlmState(data);
    }

    function applyOverviewPanel(data, opts) {
        opts = opts || {};
        if (!data) return;
        panelState.overview = data;
        dashboardData = dashboardData || {};
        dashboardData.indices = data.indices || [];
        dashboardData.movers = data.movers || { gainers: [], losers: [] };
        dashboardData.sectors = data.sectors || [];
        dashboardData.gift_nifty = data.gift_nifty || null;
        dashboardData.breadth = data.breadth || { advances: 0, declines: 0 };
        dashboardData.market_status = data.market_status || dashboardData.market_status;
        dashboardData.last_update = data.last_update || dashboardData.last_update;

        var st = data.market_status || "CLOSED";
        $status.textContent = st;
        $status.className = "market-badge" + (st === "LIVE" ? " live" : "");
        if (data.last_update) $lastUpdate.textContent = "Updated " + data.last_update;

        renderGiftNifty(data.gift_nifty);
        renderIndices(data.indices || []);
        renderMovers(data.movers || {});
        renderSectors(data.sectors || []);

        if (!opts.skipCache) savePanelCache("overview", data);
        if (selectedStock) {
            var stock = findKnownStock(selectedStock);
            if (stock) renderStockDetail(stock);
        }
    }

    function applyGlobalPanel(data, opts) {
        opts = opts || {};
        if (!data) return;
        panelState.global = data;
        dashboardData = dashboardData || {};
        dashboardData.global_futures = data.global_futures || [];
        dashboardData.last_global_update = data.last_global_update || null;
        dashboardData.global_streaming = !!data.global_streaming;
        updateGlobalStatus(data.global_streaming, data.last_global_update, data.global_futures || []);
        if (currentView === "global") renderGlobalMarkets(data.global_futures || []);
        if (!opts.skipCache) savePanelCache("global", data);
    }

    function activeNewsEnvelope() {
        if (currentNewsTab === "watchlist") return panelState.news.watchlist;
        if (currentNewsTab === "breaking") return panelState.news.breaking || panelState.news.all;
        return panelState.news.all;
    }

    function renderCurrentNews(isLiveUpdate) {
        var env = activeNewsEnvelope();
        renderNews((env && env.items) || [], isLiveUpdate);
        renderNewsLlmStack(newsLlmState);
    }

    function applyNewsPanel(tab, data, opts) {
        opts = opts || {};
        if (!data) return;
        panelState.news[tab] = data;
        updateNewsLlmState(data);
        if (!opts.skipCache) {
            if (tab === "all") savePanelCache("news:all", data);
            else if (tab === "breaking") savePanelCache("news:breaking", data);
            else if (tab === "watchlist") savePanelCache("news:watchlist", data);
        }
        if (
            (tab === "all" && ["all", "india", "global", "gold_silver"].indexOf(currentNewsTab) !== -1)
            || (tab === "breaking" && currentNewsTab === "breaking")
            || (tab === "watchlist" && currentNewsTab === "watchlist")
        ) {
            renderCurrentNews(opts.isLiveUpdate);
        }
    }

    function applyWatchlistQuotesPanel(data, opts) {
        opts = opts || {};
        if (!data) return;
        panelState.watchlistQuotes = data;
        rememberStocks(data.rows || []);
        renderWatchlistTable();
        if (!opts.skipCache) savePanelCache("watchlist:quotes", data);
        if (selectedStock) {
            var stock = findKnownStock(selectedStock);
            if (stock) renderStockDetail(stock);
        }
    }

    function loadLocalPanelCaches() {
        var overview = loadPanelCache("overview");
        var global = loadPanelCache("global");
        var newsAll = loadPanelCache("news:all");
        var newsBreaking = loadPanelCache("news:breaking");
        var watchlistNews = loadPanelCache("news:watchlist");
        var watchlistQuotes = loadPanelCache("watchlist:quotes");
        if (overview) applyOverviewPanel(overview, { skipCache: true });
        if (global) applyGlobalPanel(global, { skipCache: true });
        if (newsAll) applyNewsPanel("all", newsAll, { skipCache: true });
        if (newsBreaking) applyNewsPanel("breaking", newsBreaking, { skipCache: true });
        if (watchlistNews) applyNewsPanel("watchlist", watchlistNews, { skipCache: true });
        if (watchlistQuotes) applyWatchlistQuotesPanel(watchlistQuotes, { skipCache: true });
    }

    async function loadBootstrap() {
        try {
            var data = await fetchJson("/api/bootstrap");
            applyBootstrap(data);
            return data;
        } catch (err) {
            console.error("Bootstrap fetch error:", err);
            return panelState.bootstrap;
        }
    }

    async function loadOverviewPanel() {
        try {
            var data = await fetchJson("/api/panel/overview");
            applyOverviewPanel(data);
            return data;
        } catch (err) {
            console.error("Overview fetch error:", err);
            return panelState.overview;
        }
    }

    async function loadGlobalPanel() {
        try {
            var data = await fetchJson("/api/panel/global", { timeoutMs: 10000 });
            applyGlobalPanel(data);
            if ((!data.global_futures || !data.global_futures.length) && data.refreshing) {
                setTimeout(function () {
                    if (!document.hidden && currentView === "global") loadGlobalPanel();
                }, 5000);
            }
            return data;
        } catch (err) {
            console.error("Global fetch error:", err);
            if (dashboardData && dashboardData.global_futures && dashboardData.global_futures.length) {
                applyGlobalPanel({
                    global_futures: dashboardData.global_futures,
                    last_global_update: dashboardData.last_global_update || null,
                    global_streaming: !!dashboardData.global_streaming,
                }, { skipCache: true });
                return panelState.global;
            }
            return panelState.global;
        }
    }

    async function loadNewsPanel(tab) {
        var normalized = tab === "watchlist" ? "watchlist" : (tab === "breaking" ? "breaking" : "all");
        try {
            var data = await fetchJson("/api/panel/news?tab=" + encodeURIComponent(normalized), {
                timeoutMs: normalized === "watchlist" ? 18000 : 12000,
            });
            applyNewsPanel(normalized, data);
            if (data && data.refreshing) scheduleNewsRetry(normalized, 5000);
            else clearNewsRetry(normalized);
            return data;
        } catch (err) {
            console.error("News fetch error:", normalized, err);
            clearNewsRetry(normalized);
            return panelState.news[normalized];
        }
    }

    // ── Mutual Funds ──────────────────────────────────────────────────

    function loadMutualSelectionPrefs() {
        try {
            var raw = localStorage.getItem("imt_mf_selection");
            return raw ? JSON.parse(raw) : null;
        } catch (err) {
            return null;
        }
    }

    function saveMutualSelectionPrefs() {
        try {
            localStorage.setItem("imt_mf_selection", JSON.stringify({
                schemeCode: mutualState.selectedSchemeCode,
                benchmark: mutualState.selectedBenchmark,
                range: mutualState.selectedRange,
            }));
        } catch (err) {}
    }

    function currentMutualFund() {
        var code = String(mutualState.selectedSchemeCode || "").trim();
        if (!code) return null;
        return (mutualState.watchlist || []).find(function (item) {
            return String(item.scheme_code || "").trim() === code;
        }) || null;
    }

    function mutualBenchmarkOptions(fund) {
        var compare = mutualState.compare;
        if (compare && compare.fund && compare.fund.scheme_code === (fund && fund.scheme_code) && compare.benchmark_options && compare.benchmark_options.length) {
            return compare.benchmark_options;
        }
        return (fund && fund.benchmark_options && fund.benchmark_options.length)
            ? fund.benchmark_options
            : ["NIFTY 500"];
    }

    function mutualRangeOptions() {
        var compare = mutualState.compare;
        if (compare && compare.range_options && compare.range_options.length) {
            return compare.range_options;
        }
        return [
            { key: "1y", label: "1Y" },
            { key: "3y", label: "3Y" },
            { key: "5y", label: "5Y" },
            { key: "max", label: "Since Inception" },
        ];
    }

    function clearMutualSuggestions() {
        mutualSearchResults = [];
        if ($mfSuggest) {
            clearChildren($mfSuggest);
            $mfSuggest.classList.remove("visible");
        }
    }

    function prepareMutualComparePayload(data) {
        if (!data || !Array.isArray(data.series)) return data;
        data.fund_chart_data = data.series.map(function (point) {
            return { time: point.time, value: point.fund };
        });
        data.benchmark_chart_data = data.series.map(function (point) {
            return { time: point.time, value: point.benchmark };
        });
        data.render_points = data.render_points || data.series.length;
        delete data.series;
        return data;
    }

    function renderMutualStatus() {
        if (!$mfStatus) return;
        var count = (mutualState.watchlist || []).length;
        var parts = [
            "Shared",
            count + " fund" + (count === 1 ? "" : "s"),
        ];
        if (mutualState.storage) {
            parts.push(mutualState.durable ? "saved" : "session");
        }
        var clsName = "mf-status";
        if (count) {
            clsName += " is-ready";
        } else {
            clsName += " is-muted";
        }
        $mfStatus.className = clsName;
        $mfStatus.textContent = parts.join(" · ");
    }

    function renderMutualWatchlist() {
        if (!$mfHoldings) return;
        clearChildren($mfHoldings);
        var funds = mutualState.watchlist || [];
        if (!funds.length) {
            $mfHoldings.appendChild(el("div", "mf-list-empty", "No funds tracked yet."));
            return;
        }
        var frag = document.createDocumentFragment();
        funds.forEach(function (fund) {
            var active = String(fund.scheme_code || "") === String(mutualState.selectedSchemeCode || "");
            var card = el("button", "mf-holding-card" + (active ? " active" : ""));
            card.type = "button";

            var top = el("div", "mf-holding-top");
            top.appendChild(el("span", "mf-holding-name", fund.scheme_name || fund.scheme_code || "Mutual Fund"));
            var removeBtn = el("button", "mf-remove", "×");
            removeBtn.type = "button";
            removeBtn.addEventListener("click", function (e) {
                e.stopPropagation();
                removeMutualFund(fund.scheme_code);
            });
            top.appendChild(removeBtn);
            card.appendChild(top);

            var meta = el("div", "mf-holding-meta");
            var categoryLabel = fund.category ? titleCase(fund.category) : "Unclassified";
            meta.appendChild(el("span", "mf-holding-chip", categoryLabel));
            if (fund.benchmark_options && fund.benchmark_options.length) {
                meta.appendChild(el("span", "mf-holding-chip muted", fund.benchmark_options[0]));
            }
            meta.appendChild(el("span", "mf-holding-chip muted", "Code " + (fund.scheme_code || "—")));
            card.appendChild(meta);

            var foot = el("div", "mf-holding-foot");
            foot.appendChild(el("span", "mf-holding-value", fund.latest_nav != null ? fmtPrice(fund.latest_nav) : "NAV —"));
            foot.appendChild(el("span", "mf-holding-date", fund.latest_nav_date ? "NAV " + fmtDateLabel(fund.latest_nav_date) : "NAV date unavailable"));
            card.appendChild(foot);

            card.addEventListener("click", function () {
                setMutualSelection(String(fund.scheme_code || ""));
            });
            frag.appendChild(card);
        });
        $mfHoldings.appendChild(frag);
    }

    function ensureMutualChart() {
        if (!$mfChartBox) return null;
        if (mfChart) return mfChart;
        mfChart = LightweightCharts.createChart($mfChartBox, {
            layout: {
                background: { color: "transparent" },
                textColor: "#8ea0b8",
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: 10,
            },
            grid: {
                vertLines: { color: "rgba(30, 41, 59, 0.32)" },
                horzLines: { color: "rgba(30, 41, 59, 0.32)" },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: "rgba(255,140,0,.18)", labelBackgroundColor: "#ff8c00" },
                horzLine: { color: "rgba(125,211,252,.2)", labelBackgroundColor: "#26c6da" },
            },
            rightPriceScale: {
                borderColor: "#1e293b",
                scaleMargins: { top: 0.1, bottom: 0.1 },
            },
            timeScale: {
                borderColor: "#1e293b",
                timeVisible: true,
                secondsVisible: false,
            },
            handleScroll: true,
            handleScale: true,
        });
        mfFundSeries = mfChart.addLineSeries({
            color: "#ff9d3f",
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: true,
            title: "Fund",
        });
        mfBenchmarkSeries = mfChart.addLineSeries({
            color: "#4fd1c5",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: true,
            title: "Benchmark",
        });
        return mfChart;
    }

    function showMutualChartPlaceholder(message) {
        if (!$mfChartBox) return;
        $mfChartBox.classList.add("is-empty");
        var placeholder = $mfChartBox.querySelector(".mf-chart-placeholder");
        if (!placeholder) {
            placeholder = el("div", "mf-chart-placeholder");
            $mfChartBox.appendChild(placeholder);
        }
        placeholder.textContent = message;
        placeholder.hidden = false;
    }

    function hideMutualChartPlaceholder() {
        if (!$mfChartBox) return;
        $mfChartBox.classList.remove("is-empty");
        var placeholder = $mfChartBox.querySelector(".mf-chart-placeholder");
        if (placeholder) placeholder.hidden = true;
    }

    function renderMutualChart(compare) {
        if (!$mfChartBox) return;
        if (!compare || !compare.fund_chart_data || !compare.fund_chart_data.length) {
            if (mfFundSeries) mfFundSeries.setData([]);
            if (mfBenchmarkSeries) mfBenchmarkSeries.setData([]);
            mutualChartRenderKey = "";
            showMutualChartPlaceholder(
                mutualState.compareLoading
                    ? "Loading official NAV and benchmark history…"
                    : (mutualState.compareError || ((mutualState.selectedSchemeCode || "").trim() ? "Choose a benchmark and range." : "Select a tracked fund."))
            );
            return;
        }
        hideMutualChartPlaceholder();
        var chartRef = ensureMutualChart();
        if (!chartRef || !mfFundSeries || !mfBenchmarkSeries) return;
        var renderKey = [
            compare.fund && compare.fund.scheme_code,
            compare.benchmark,
            compare.range,
            compare.render_points || 0,
            compare.to_date || "",
        ].join("|");
        if (mutualChartRenderKey === renderKey) return;
        mfFundSeries.setData(compare.fund_chart_data || []);
        mfBenchmarkSeries.setData(compare.benchmark_chart_data || []);
        chartRef.timeScale().fitContent();
        mutualChartRenderKey = renderKey;
    }

    function renderMutualDetail() {
        if (
            !$mfDetail ||
            !$mfHero ||
            !$mfBenchmarkBlock ||
            !$mfBenchmarks ||
            !$mfRangeBlock ||
            !$mfRanges ||
            !$mfStats ||
            !$mfChartShell ||
            !$mfChartTitle ||
            !$mfChartNote
        ) {
            return;
        }
        var fund = currentMutualFund();
        if (!fund) {
            clearChildren($mfHero);
            clearChildren($mfBenchmarks);
            clearChildren($mfRanges);
            clearChildren($mfStats);
            $mfDetail.classList.add("is-empty");
            $mfHero.hidden = true;
            $mfBenchmarkBlock.hidden = true;
            $mfRangeBlock.hidden = true;
            $mfStats.hidden = true;
            $mfChartShell.classList.add("is-empty");
            $mfChartTitle.textContent = "NAV COMPARISON";
            $mfChartNote.textContent = "Select a tracked fund";
            renderMutualChart(null);
            return;
        }
        $mfDetail.classList.remove("is-empty");
        $mfHero.hidden = false;
        $mfBenchmarkBlock.hidden = false;
        $mfRangeBlock.hidden = false;
        $mfStats.hidden = false;
        $mfChartShell.classList.remove("is-empty");

        clearChildren($mfHero);
        clearChildren($mfBenchmarks);
        clearChildren($mfRanges);
        clearChildren($mfStats);

        var heroTop = el("div", "mf-hero-top");
        var titleWrap = el("div", "mf-hero-copy");
        titleWrap.appendChild(el("div", "mf-hero-kicker", "OFFICIAL NAV TRACKER"));
        titleWrap.appendChild(el("h3", "mf-hero-name", fund.scheme_name || fund.scheme_code || "Mutual Fund"));
        var meta = [];
        if (fund.category) meta.push(titleCase(fund.category));
        if (fund.scheme_code) meta.push("Code " + fund.scheme_code);
        if (fund.latest_nav_date) meta.push("NAV " + fmtDateLabel(fund.latest_nav_date));
        titleWrap.appendChild(el("div", "mf-hero-meta", meta.join(" · ") || "Comparison against official NSE benchmarks"));
        heroTop.appendChild(titleWrap);

        var heroValue = el("div", "mf-hero-value");
        heroValue.appendChild(el("span", "mf-hero-value-label", "Latest NAV"));
        heroValue.appendChild(el("span", "mf-hero-value-number", fund.latest_nav != null ? fmtPrice(fund.latest_nav) : "—"));
        heroValue.appendChild(el("span", "mf-hero-value-change flat", fund.latest_nav_date ? "Updated " + fmtDateLabel(fund.latest_nav_date) : "Official NAV date unavailable"));
        heroTop.appendChild(heroValue);
        $mfHero.appendChild(heroTop);

        mutualBenchmarkOptions(fund).forEach(function (benchmark) {
            var chip = el("button", "mf-chip" + (benchmark === mutualState.selectedBenchmark ? " active" : ""), benchmark);
            chip.type = "button";
            chip.addEventListener("click", function () {
                setMutualSelection(String(fund.scheme_code || ""), {
                    benchmark: benchmark,
                    range: mutualState.selectedRange || "max",
                    forceCompare: true,
                });
            });
            $mfBenchmarks.appendChild(chip);
        });

        mutualRangeOptions().forEach(function (rangeOpt) {
            var chip = el("button", "mf-chip" + (rangeOpt.key === mutualState.selectedRange ? " active" : ""), rangeOpt.label);
            chip.type = "button";
            chip.addEventListener("click", function () {
                setMutualSelection(String(fund.scheme_code || ""), {
                    benchmark: mutualState.selectedBenchmark,
                    range: rangeOpt.key,
                    forceCompare: true,
                });
            });
            $mfRanges.appendChild(chip);
        });

        var compare = mutualState.compare && mutualState.compare.fund && mutualState.compare.fund.scheme_code === fund.scheme_code
            ? mutualState.compare
            : null;
        var statCards = [
            ["LATEST NAV", fund.latest_nav != null ? fmtPrice(fund.latest_nav) : "—"],
            ["NAV DATE", fmtDateLabel(fund.latest_nav_date)],
            ["BENCHMARK", mutualState.selectedBenchmark || "\u2014"],
        ];
        if (compare) {
            statCards.push(["FUND RETURN", fmtPct(compare.fund_return_pct || 0)]);
            statCards.push(["BENCHMARK RETURN", fmtPct(compare.benchmark_return_pct || 0)]);
            statCards.push(["ALPHA", fmtPct(compare.alpha_pct || 0)]);
        }
        statCards.forEach(function (pair) {
            var card = el("div", "mf-stat-card");
            card.appendChild(el("span", "mf-stat-label", pair[0]));
            var tone = "neutral";
            var raw = pair[1];
            if (pair[0] === "FUND RETURN" || pair[0] === "ALPHA") {
                var numeric = Number(String(raw).replace(/[^0-9.-]/g, ""));
                tone = numeric > 0 ? "up" : (numeric < 0 ? "down" : "neutral");
            }
            card.appendChild(el("span", "mf-stat-value " + tone, raw));
            $mfStats.appendChild(card);
        });

        $mfChartTitle.textContent = (fund.scheme_name || "Mutual Fund") + " vs " + (mutualState.selectedBenchmark || "Benchmark");
        if (compare) {
            $mfChartNote.textContent =
                "Normalized to 100 from " + fmtDateLabel(compare.from_date) +
                " · " +
                ((compare.render_points && compare.render_points < compare.points)
                    ? (compare.render_points + "/" + compare.points + " points rendered")
                    : (compare.points + " NAV points")) +
                " · " +
                (compare.source && compare.source.fund ? compare.source.fund : "AMFI") +
                " vs " +
                (compare.source && compare.source.benchmark ? compare.source.benchmark : "NSE");
        } else if (mutualState.compareLoading) {
            $mfChartNote.textContent = "Pulling official NAV and index history…";
        } else if (mutualState.compareError) {
            $mfChartNote.textContent = mutualState.compareError;
        } else {
            $mfChartNote.textContent = "Pick a benchmark and range to compare normalized NAV performance.";
        }

        renderMutualChart(compare);
    }

    function renderMutualPage() {
        renderMutualStatus();
        renderMutualWatchlist();
        renderMutualDetail();
    }

    function setMutualSelection(schemeCode, opts) {
        opts = opts || {};
        var fund = (mutualState.watchlist || []).find(function (item) {
            return String(item.scheme_code || "") === String(schemeCode || "");
        }) || mutualState.watchlist[0];
        if (!fund) {
            renderMutualPage();
            return;
        }
        mutualState.selectedSchemeCode = String(fund.scheme_code || "");

        var benchmarks = mutualBenchmarkOptions(fund);
        var benchmark = opts.benchmark || mutualState.selectedBenchmark || benchmarks[0] || "NIFTY 500";
        if (benchmarks.indexOf(benchmark) === -1) benchmark = benchmarks[0] || benchmark;
        mutualState.selectedBenchmark = benchmark;

        var ranges = mutualRangeOptions();
        var rangeKeys = ranges.map(function (item) { return item.key; });
        var rangeKey = opts.range || mutualState.selectedRange || "max";
        if (rangeKeys.indexOf(rangeKey) === -1) rangeKey = ranges[0] ? ranges[0].key : "max";
        mutualState.selectedRange = rangeKey;
        mutualState.compareError = null;
        saveMutualSelectionPrefs();
        renderMutualPage();

        var compare = mutualState.compare;
        var needsCompare = opts.forceCompare
            || !compare
            || !compare.fund
            || compare.fund.scheme_code !== mutualState.selectedSchemeCode
            || compare.benchmark !== mutualState.selectedBenchmark
            || compare.range !== mutualState.selectedRange;
        if (needsCompare) {
            loadMutualComparison({ force: true });
        }
    }

    function applyMutualWatchlist(data, opts) {
        opts = opts || {};
        mutualState.watchlist = (data && data.items) || [];
        mutualState.storage = data ? data.storage : null;
        mutualState.durable = !!(data && data.durable);

        if (!mutualState.watchlist.length) {
            mutualState.selectedSchemeCode = null;
            mutualState.selectedBenchmark = null;
            mutualState.selectedRange = "max";
            mutualState.compare = null;
            mutualState.compareLoading = false;
            mutualState.compareError = null;
            mutualChartRenderKey = "";
            renderMutualPage();
            return;
        }

        var prefs = loadMutualSelectionPrefs() || {};
        var target = (mutualState.watchlist || []).find(function (item) {
            return String(item.scheme_code || "") === String(opts.schemeCode || mutualState.selectedSchemeCode || prefs.schemeCode || "");
        }) || mutualState.watchlist[0];

        mutualState.selectedSchemeCode = String(target.scheme_code || "");
        var benchmarkOptions = target && target.benchmark_options && target.benchmark_options.length
            ? target.benchmark_options.slice()
            : ["NIFTY 500"];
        mutualState.selectedBenchmark = opts.benchmark || mutualState.selectedBenchmark || prefs.benchmark || benchmarkOptions[0] || "NIFTY 500";
        if (benchmarkOptions.indexOf(mutualState.selectedBenchmark) === -1) {
            mutualState.selectedBenchmark = benchmarkOptions[0] || mutualState.selectedBenchmark;
        }
        var rangeOptions = mutualRangeOptions();
        mutualState.selectedRange = opts.range || mutualState.selectedRange || prefs.range || "max";
        if (!rangeOptions.some(function (item) { return item.key === mutualState.selectedRange; })) {
            mutualState.selectedRange = rangeOptions[0] ? rangeOptions[0].key : "max";
        }
        mutualState.compareError = null;
        saveMutualSelectionPrefs();
        renderMutualPage();

        var compare = mutualState.compare;
        var compareMatches = compare
            && compare.fund
            && compare.fund.scheme_code === mutualState.selectedSchemeCode
            && compare.benchmark === mutualState.selectedBenchmark
            && compare.range === mutualState.selectedRange;
        if (!compareMatches || opts.forceCompare) {
            loadMutualComparison({ force: true });
        }
    }

    async function loadMutualWatchlist(opts) {
        opts = opts || {};
        if (mutualHoldingsLoadPromise && !opts.force) return mutualHoldingsLoadPromise;
        mutualHoldingsLoadPromise = (async function () {
            try {
                var data = await fetchJson("/api/mf/watchlist", { timeoutMs: 20000 });
                applyMutualWatchlist(data, opts);
                return data;
            } catch (err) {
                console.error("Mutual watchlist fetch error:", err);
                renderMutualStatus();
                return { items: mutualState.watchlist };
            } finally {
                mutualHoldingsLoadPromise = null;
            }
        })();
        return mutualHoldingsLoadPromise;
    }

    async function loadMutualComparison(opts) {
        opts = opts || {};
        var fund = currentMutualFund();
        if (!fund) return null;
        var benchmark = opts.benchmark || mutualState.selectedBenchmark || (fund.benchmark_options && fund.benchmark_options[0]) || "NIFTY 500";
        var rangeKey = opts.range || mutualState.selectedRange || "max";
        if (!opts.force && mutualState.compare && mutualState.compare.fund && mutualState.compare.fund.scheme_code === fund.scheme_code && mutualState.compare.benchmark === benchmark && mutualState.compare.range === rangeKey) {
            return mutualState.compare;
        }
        mutualState.compareLoading = true;
        mutualState.compareError = null;
        renderMutualPage();
        var requestSeq = ++mutualCompareRequestSeq;
        if (mutualCompareAbortController) {
            mutualCompareAbortController.abort();
        }
        mutualCompareAbortController = typeof AbortController !== "undefined" ? new AbortController() : null;
        var compareController = mutualCompareAbortController;
        mutualCompareLoadPromise = (async function () {
            try {
                var data = await fetchJson(
                    "/api/mf/compare/" + encodeURIComponent(String(fund.scheme_code || "")) +
                    "?benchmark=" + encodeURIComponent(benchmark) +
                    "&range=" + encodeURIComponent(rangeKey),
                    { timeoutMs: 45000, signal: compareController ? compareController.signal : null }
                );
                if (compareController && compareController.signal.aborted) return null;
                if (requestSeq !== mutualCompareRequestSeq) return data;
                data = prepareMutualComparePayload(data);
                mutualState.compare = data;
                mutualState.compareLoading = false;
                mutualState.selectedBenchmark = data.benchmark || benchmark;
                mutualState.selectedRange = data.range || rangeKey;
                saveMutualSelectionPrefs();
                renderMutualPage();
                return data;
            } catch (err) {
                if (compareController && compareController.signal.aborted) return null;
                if (requestSeq !== mutualCompareRequestSeq) return null;
                console.error("Mutual compare fetch error:", err);
                mutualState.compareLoading = false;
                mutualState.compareError = err.message || "Unable to build comparison";
                renderMutualPage();
                return null;
            } finally {
                if (mutualCompareAbortController === compareController) {
                    mutualCompareAbortController = null;
                }
                if (requestSeq === mutualCompareRequestSeq) {
                    mutualCompareLoadPromise = null;
                }
            }
        })();
        return mutualCompareLoadPromise;
    }

    async function addMutualFund(schemeCode) {
        if (!schemeCode) return null;
        try {
            var data = await fetchJson("/api/mf/watchlist/" + encodeURIComponent(String(schemeCode)), {
                method: "PUT",
                timeoutMs: 20000,
            });
            applyMutualWatchlist(data, { schemeCode: String(schemeCode), forceCompare: true });
            return data;
        } catch (err) {
            console.error("Mutual add error:", err);
            mutualState.compareError = err.message || "Unable to add mutual fund";
            renderMutualPage();
            return null;
        } finally {
            if ($mfInput) {
                $mfInput.value = "";
            }
            if (mutualSearchAbortController) {
                mutualSearchAbortController.abort();
                mutualSearchAbortController = null;
            }
            clearMutualSuggestions();
        }
    }

    async function removeMutualFund(schemeCode) {
        if (!schemeCode) return null;
        try {
            var selectedCode = mutualState.selectedSchemeCode;
            var data = await fetchJson("/api/mf/watchlist/" + encodeURIComponent(String(schemeCode)), {
                method: "DELETE",
                timeoutMs: 20000,
            });
            applyMutualWatchlist(data, {
                schemeCode: selectedCode === schemeCode ? null : selectedCode,
                forceCompare: selectedCode !== schemeCode,
            });
            return data;
        } catch (err) {
            console.error("Mutual remove error:", err);
            mutualState.compareError = err.message || "Unable to remove mutual fund";
            renderMutualPage();
            return null;
        }
    }

    function renderMutualSuggestions(items) {
        if (!$mfSuggest) return;
        clearChildren($mfSuggest);
        mutualSearchResults = (items || []).slice(0, 12);
        if (!mutualSearchResults.length) {
            $mfSuggest.classList.remove("visible");
            return;
        }
        var frag = document.createDocumentFragment();
        mutualSearchResults.forEach(function (item) {
            var row = el("button", "mf-suggest-item" + (item.tracked ? " already" : ""));
            row.type = "button";
            var head = el("div", "mf-suggest-head");
            head.appendChild(el("span", "mf-suggest-name", item.scheme_name || item.scheme_code));
            if (item.latest_nav != null) {
                head.appendChild(el("span", "mf-suggest-nav", fmtPrice(item.latest_nav)));
            }
            row.appendChild(head);
            var meta = el("div", "mf-suggest-meta");
            meta.appendChild(el("span", "mf-holding-chip", item.category ? titleCase(item.category) : "Unclassified"));
            if (item.benchmark_options && item.benchmark_options.length) {
                meta.appendChild(el("span", "mf-holding-chip muted", item.benchmark_options[0]));
            }
            if (item.latest_nav_date) {
                meta.appendChild(el("span", "mf-holding-chip muted", fmtDateLabel(item.latest_nav_date)));
            }
            row.appendChild(meta);
            if (item.tracked) {
                row.disabled = true;
            } else {
                row.addEventListener("click", function () {
                    addMutualFund(item.scheme_code);
                });
            }
            frag.appendChild(row);
        });
        $mfSuggest.appendChild(frag);
        $mfSuggest.classList.add("visible");
    }

    async function showMutualSuggestions(query) {
        if (!$mfSuggest) return;
        var q = String(query || "").trim();
        if (q.length < 2) {
            clearMutualSuggestions();
            return;
        }
        if (mutualSearchAbortController) {
            mutualSearchAbortController.abort();
        }
        mutualSearchAbortController = typeof AbortController !== "undefined" ? new AbortController() : null;
        var searchController = mutualSearchAbortController;
        try {
            var items = await fetchJson("/api/mf/search?q=" + encodeURIComponent(q), {
                timeoutMs: 12000,
                signal: searchController ? searchController.signal : null,
            });
            if (searchController && searchController.signal.aborted) return;
            renderMutualSuggestions(items || []);
        } catch (err) {
            if (searchController && searchController.signal.aborted) return;
            console.error("Mutual search error:", err);
            clearMutualSuggestions();
        } finally {
            if (mutualSearchAbortController === searchController) {
                mutualSearchAbortController = null;
            }
        }
    }

    var mutualSuggestTimeout = null;
    if ($mfInput) {
        $mfInput.addEventListener("input", function () {
            clearTimeout(mutualSuggestTimeout);
            var query = $mfInput.value.trim();
            if (!query) {
                if (mutualSearchAbortController) {
                    mutualSearchAbortController.abort();
                    mutualSearchAbortController = null;
                }
                clearMutualSuggestions();
                return;
            }
            mutualSuggestTimeout = setTimeout(function () {
                showMutualSuggestions(query);
            }, 180);
        });

        $mfInput.addEventListener("focus", function () {
            if ($mfInput.value.trim()) showMutualSuggestions($mfInput.value.trim());
        });

        $mfInput.addEventListener("blur", function () {
            setTimeout(function () {
                if ($mfSuggest) $mfSuggest.classList.remove("visible");
                if (mutualSearchAbortController) {
                    mutualSearchAbortController.abort();
                    mutualSearchAbortController = null;
                }
            }, 180);
        });

        $mfInput.addEventListener("keydown", function (e) {
            if (e.key === "Escape") {
                $mfInput.blur();
                if ($mfSuggest) $mfSuggest.classList.remove("visible");
                return;
            }
            if (e.key === "Enter" && mutualSearchResults.length) {
                e.preventDefault();
                var first = mutualSearchResults.find(function (item) { return !item.tracked; });
                if (first) addMutualFund(first.scheme_code);
            }
        });
    }

    // ── Render: Stock Detail ───────────────────────────────────────────

    function renderStockDetail(stock) {
        if (!stock) return;
        var c = cls(stock.change);

        clearChildren($stockTitle);
        $stockTitle.appendChild(el("span", "", "WATCHLIST"));
        var sep = el("span", "", " \u2014 ");
        sep.style.color = "var(--text-dim)";
        $stockTitle.appendChild(sep);
        var symSpan = el("span", "", stock.symbol);
        symSpan.style.color = "var(--amber)";
        $stockTitle.appendChild(symSpan);

        clearChildren($stockHero);
        var top = el("div", "stock-top");
        top.appendChild(el("span", "stock-company", stock.name));
        top.appendChild(el("span", "stock-price-big " + c, fmtPrice(stock.price)));
        top.appendChild(el("span", "stock-change-big " + c,
            arrow(stock.change) + " " + fmtChange(stock.change) + " (" + fmtPct(stock.change_pct) + ")"));
        $stockHero.appendChild(top);

        var ohlv = el("div", "stock-ohlv");
        var pairs = [
            ["O", stock.open], ["H", stock.high], ["L", stock.low],
            ["PREV", stock.prev_close], ["VOL", null],
        ];
        pairs.forEach(function (p) {
            var span = el("span");
            span.appendChild(el("span", "label", p[0] + " "));
            span.appendChild(document.createTextNode(
                p[0] === "VOL" ? fmtVol(stock.volume) : fmtPrice(p[1])
            ));
            ohlv.appendChild(span);
        });
        $stockHero.appendChild(ohlv);
    }

    // ── Chart ──────────────────────────────────────────────────────────

    function initChart() {
        if (chart) { chart.remove(); chart = null; }
        chart = LightweightCharts.createChart($chartBox, {
            layout: {
                background: { color: "transparent" },
                textColor: "#8899aa",
                fontFamily: "'IBM Plex Mono', monospace",
                fontSize: 10,
            },
            grid: {
                vertLines: { color: "rgba(30,41,59,.4)" },
                horzLines: { color: "rgba(30,41,59,.4)" },
            },
            crosshair: {
                mode: LightweightCharts.CrosshairMode.Normal,
                vertLine: { color: "rgba(255,140,0,.3)", labelBackgroundColor: "#ff8c00" },
                horzLine: { color: "rgba(255,140,0,.3)", labelBackgroundColor: "#ff8c00" },
            },
            timeScale: { borderColor: "#1e293b", timeVisible: true },
            rightPriceScale: { borderColor: "#1e293b" },
            handleScroll: true,
            handleScale: true,
        });

        chartSeries = chart.addCandlestickSeries({
            upColor: "#00e676", downColor: "#ff3d3d",
            borderUpColor: "#00c853", borderDownColor: "#d32f2f",
            wickUpColor: "#00c853", wickDownColor: "#d32f2f",
        });

        volumeSeries = chart.addHistogramSeries({
            priceFormat: { type: "volume" },
            priceScaleId: "",
        });
        volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.85, bottom: 0 } });
    }

    async function loadChart(symbol, period, interval) {
        if (!symbol) return;
        try {
            var res = await fetch("/api/chart/" + symbol + "?period=" + period + "&interval=" + interval);
            var data = await res.json();
            if (!data.length) {
                clearChildren($chartBox);
                var msg = el("div", "stock-prompt", "Chart data unavailable \u2014 try again shortly");
                $chartBox.appendChild(msg);
                return;
            }
            clearChildren($chartBox);
            initChart();

            chartSeries.setData(data.map(function (d) {
                return { time: d.time, open: d.open, high: d.high, low: d.low, close: d.close };
            }));

            volumeSeries.setData(data.map(function (d) {
                return {
                    time: d.time, value: d.volume,
                    color: d.close >= d.open ? "rgba(0,230,118,.2)" : "rgba(255,61,61,.2)",
                };
            }));

            chart.timeScale().fitContent();
        } catch (err) {
            console.error("Chart load error:", err);
        }
    }

    function handleResize() {
        if (chart) {
            chart.applyOptions({ width: $chartBox.clientWidth, height: $chartBox.clientHeight });
        }
        if (mfChart && $mfChartBox) {
            mfChart.applyOptions({ width: $mfChartBox.clientWidth, height: $mfChartBox.clientHeight });
        }
        var globalRows = (panelState.global && panelState.global.global_futures)
            || (dashboardData && dashboardData.global_futures)
            || [];
        if (globalRows.length) {
            renderGlobalHeatmap(globalRows);
        }
    }
    window.addEventListener("resize", handleResize);

    function rememberStock(stock) {
        if (!stock || !stock.symbol) return;
        var sym = String(stock.symbol).toUpperCase();
        stockCache[sym] = Object.assign({}, stockCache[sym] || {}, stock, {
            symbol: sym,
            _fetchedAt: Date.now(),
        });
    }

    function rememberStocks(stocks) {
        (stocks || []).forEach(rememberStock);
    }

    function isDashboardStock(symbol) {
        var sym = String(symbol || "").toUpperCase();
        return !!stockCache[sym];
    }

    function findKnownStock(symbol) {
        var sym = String(symbol || "").toUpperCase();
        return stockCache[sym] || null;
    }

    async function fetchStockDetail(symbol) {
        var sym = String(symbol || "").toUpperCase();
        if (!sym) return null;
        if (stockFetchInflight[sym]) return stockFetchInflight[sym];
        stockFetchInflight[sym] = (async function () {
            try {
                var res = await fetch("/api/stock/" + encodeURIComponent(sym));
                if (!res.ok) return null;
                var stock = await res.json();
                rememberStock(stock);
                return stock;
            } catch (err) {
                console.error("Stock detail fetch error:", sym, err);
                return null;
            } finally {
                delete stockFetchInflight[sym];
            }
        })();
        return stockFetchInflight[sym];
    }

    async function hydrateWatchlistStocks(opts) {
        opts = opts || {};
        if (!watchlist.length) {
            panelState.watchlistQuotes = { symbols: [], rows: [] };
            renderWatchlistTable();
            return panelState.watchlistQuotes;
        }
        if (!opts.force && !isWatchlistVisible()) {
            return panelState.watchlistQuotes;
        }
        if (watchlistQuotesLoadPromise) return watchlistQuotesLoadPromise;
        var now = Date.now();
        var staleMs = opts.staleMs == null ? 90000 : opts.staleMs;
        var panelFresh = panelState.watchlistQuotes && panelState.watchlistQuotes.as_of && !opts.force;
        if (panelFresh) {
            var allFresh = watchlist.every(function (sym) {
                var cached = stockCache[String(sym).toUpperCase()];
                return cached && cached._fetchedAt && (now - cached._fetchedAt <= staleMs);
            });
            if (allFresh) return panelState.watchlistQuotes;
        }
        if (!opts.force && now - lastWatchlistQuoteRefreshAt < 5000) return;
        lastWatchlistQuoteRefreshAt = now;
        watchlistQuotesLoadPromise = (async function () {
            try {
                var data = await fetchJson("/api/watchlist/quotes");
                panelState.watchlistQuotes = data;
                rememberStocks(data.rows || []);
                savePanelCache("watchlist:quotes", data);
                renderWatchlistTable();
                return data;
            } catch (err) {
                console.error("Watchlist quotes fetch error:", err);
                return panelState.watchlistQuotes;
            } finally {
                watchlistQuotesLoadPromise = null;
            }
        })();
        return watchlistQuotesLoadPromise;
    }

    // ── Stock Selection ────────────────────────────────────────────────

    async function selectStock(symbol) {
        selectedStock = symbol;
        var stock = findKnownStock(symbol);
        if (!stock) stock = await fetchStockDetail(symbol);
        if (stock) {
            renderStockDetail(stock);
            $stockHero.style.display = "";
            $chartCtrl.style.display = "";
        }
        renderWatchlistTable();

        var activeBtn = document.querySelector(".chart-btn.active");
        var period = activeBtn ? activeBtn.dataset.period : "1d";
        var interval = activeBtn ? activeBtn.dataset.interval : "5m";
        loadChart(symbol, period, interval);
    }

    // ── Tabs ───────────────────────────────────────────────────────────

    // ── News Tabs ──────────────────────────────────────────────────────

    document.querySelectorAll(".news-tab").forEach(function (tab) {
        tab.addEventListener("click", function () {
            document.querySelectorAll(".news-tab").forEach(function (t) { t.classList.remove("active"); });
            tab.classList.add("active");
            currentNewsTab = tab.dataset.news;
            if (currentNewsTab === "watchlist") {
                if (shouldRefreshNewsPanel("watchlist", 120000)) requestWatchlistBackfill();
                else renderCurrentNews();
                return;
            }
            if (shouldRefreshNewsPanel(activeNewsRequestTab(), 60000)) loadNewsPanel(activeNewsRequestTab());
            else renderCurrentNews();
        });
    });

    // ── Chart period buttons ───────────────────────────────────────────

    document.querySelectorAll(".chart-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            document.querySelectorAll(".chart-btn").forEach(function (b) { b.classList.remove("active"); });
            btn.classList.add("active");
            if (selectedStock) loadChart(selectedStock, btn.dataset.period, btn.dataset.interval);
        });
    });

    // ── Search ─────────────────────────────────────────────────────────

    var searchTimeout = null;
    $search.addEventListener("input", function () {
        clearTimeout(searchTimeout);
        var q = $search.value.trim();
        if (q.length < 1) { $results.classList.remove("visible"); return; }

        searchTimeout = setTimeout(async function () {
            try {
                var res = await fetch("/api/search?q=" + encodeURIComponent(q));
                var items = await res.json();
                rememberStocks(items);
                clearChildren($results);
                if (!items.length) { $results.classList.remove("visible"); return; }

                items.forEach(function (s) {
                    var row = el("div", "search-item");
                    row.dataset.sym = s.symbol;
                    var left = el("div");
                    left.appendChild(el("span", "sym", s.symbol));
                    left.appendChild(el("span", "name", " " + (s.name || "")));
                    row.appendChild(left);
                    if (s.price) {
                        row.appendChild(el("span", cls(s.change_pct || 0), fmtPrice(s.price)));
                    }
                    row.addEventListener("click", function () {
                        selectStock(s.symbol);
                        $search.value = "";
                        $results.classList.remove("visible");
                    });
                    $results.appendChild(row);
                });
                $results.classList.add("visible");
            } catch (err) { console.error("Search error:", err); }
        }, 200);
    });

    $search.addEventListener("blur", function () {
        setTimeout(function () { $results.classList.remove("visible"); }, 200);
    });

    document.addEventListener("keydown", function (e) {
        if ((e.ctrlKey || e.metaKey) && e.key === "k") {
            e.preventDefault();
            $search.focus();
        }
        if (e.key === "Escape") {
            $search.blur();
            $results.classList.remove("visible");
        }
    });

    // ── Watchlist ──────────────────────────────────────────────────────

    function normalizeWatchlist(list) {
        var seen = {};
        return (list || []).map(function (sym) {
            return String(sym || "").trim().toUpperCase();
        }).filter(function (sym) {
            if (!sym || seen[sym]) return false;
            seen[sym] = true;
            return true;
        });
    }

    function sameWatchlist(a, b) {
        a = normalizeWatchlist(a);
        b = normalizeWatchlist(b);
        if (a.length !== b.length) return false;
        for (var i = 0; i < a.length; i++) {
            if (a[i] !== b[i]) return false;
        }
        return true;
    }

    function loadWatchlistLocal() {
        try {
            var saved = localStorage.getItem("imt_watchlist");
            return normalizeWatchlist(saved ? JSON.parse(saved) : []);
        } catch (e) { return []; }
    }

    function saveWatchlist() {
        try { localStorage.setItem("imt_watchlist", JSON.stringify(watchlist)); } catch (e) {}
    }

    function requestWatchlistBackfill() {
        if (!watchlist.length) {
            renderCurrentNews();
            return;
        }
        loadNewsPanel("watchlist");
    }

    function applyWatchlist(symbols, opts) {
        opts = opts || {};
        var prevHash = watchlistHash(watchlist);
        watchlist = normalizeWatchlist(symbols);
        var nextHash = watchlistHash(watchlist);
        saveWatchlist();
        if (prevHash !== nextHash) {
            panelState.news.watchlist = null;
            panelState.watchlistQuotes = null;
            removePanelCache("news:watchlist", prevHash);
            removePanelCache("news:watchlist", nextHash);
            removePanelCache("watchlist:quotes");
        }
        renderWatchlistTable();
        if (!opts.deferHydrate) {
            hydrateWatchlistStocks();
        }
        if (currentNewsTab === "watchlist") requestWatchlistBackfill();
    }

    async function fetchWatchlistRemote() {
        var res = await fetch("/api/watchlist");
        if (!res.ok) throw new Error("watchlist fetch failed");
        return await res.json();
    }

    async function syncWatchlistRemote(symbols) {
        var res = await fetch("/api/watchlist/sync", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ symbols: normalizeWatchlist(symbols) }),
        });
        if (!res.ok) throw new Error("watchlist sync failed");
        var data = await res.json();
        return normalizeWatchlist(data.symbols || []);
    }

    async function loadWatchlist(opts) {
        opts = opts || {};
        if (watchlistLoadPromise) return watchlistLoadPromise;
        watchlistLoadPromise = (async function () {
            var local = loadWatchlistLocal();
            try {
                var remoteState = await fetchWatchlistRemote();
                var remote = normalizeWatchlist(remoteState.symbols || []);
                var initialized = !!remoteState.initialized;

                // Seed a brand-new shared watchlist once from the first browser's cache,
                // then switch to the server list as the only source of truth.
                if (!initialized && local.length) {
                    remote = await syncWatchlistRemote(local);
                }

                if (!sameWatchlist(remote, watchlist) || opts.forceRender) {
                    applyWatchlist(remote, { deferHydrate: true });
                }
                return remote;
            } catch (err) {
                console.warn("[Watchlist] Remote fetch failed:", err);
                if (!watchlist.length && local.length) {
                    applyWatchlist(local, { deferHydrate: true });
                }
                return watchlist;
            } finally {
                watchlistLoadPromise = null;
            }
        })();
        return watchlistLoadPromise;
    }

    async function addToWatchlist(sym) {
        if (watchlist.indexOf(sym) !== -1) return;
        var prev = watchlist.slice();
        applyWatchlist(watchlist.concat([sym]));
        try {
            var res = await fetch("/api/watchlist/" + encodeURIComponent(sym), { method: "PUT" });
            if (!res.ok) throw new Error("watchlist add failed");
            var data = await res.json();
            applyWatchlist(data.symbols || prev.concat([sym]));
        } catch (err) {
            console.error("[Watchlist] Add failed:", err);
            applyWatchlist(prev);
        }
    }

    async function removeFromWatchlist(sym) {
        var prev = watchlist.slice();
        applyWatchlist(watchlist.filter(function (s) { return s !== sym; }));
        try {
            var res = await fetch("/api/watchlist/" + encodeURIComponent(sym), { method: "DELETE" });
            if (!res.ok) throw new Error("watchlist remove failed");
            var data = await res.json();
            applyWatchlist(data.symbols || watchlist);
        } catch (err) {
            console.error("[Watchlist] Remove failed:", err);
            applyWatchlist(prev);
        }
    }

    function renderWatchlistTable() {
        clearChildren($wlTable);
        if (!watchlist.length) {
            $wlTable.appendChild(el("div", "wl-table-empty",
                "Type in the search box above to add stocks to your watchlist"));
            return;
        }
        watchlist.forEach(function (sym) {
            var stock = findKnownStock(sym);
            var row = el("div", "wl-row" + (selectedStock === sym ? " wl-row-active" : ""));
            row.appendChild(el("span", "wl-row-sym", sym));
            row.appendChild(el("span", "wl-row-name", stock ? (stock.name || "") : ""));
            var priceEl = el("span", "wl-row-price", stock ? fmtPrice(stock.price) : "\u2014");
            row.appendChild(priceEl);
            var chgEl = el("span", "wl-row-chg " + (stock ? cls(stock.change_pct) : "flat"),
                stock ? fmtPct(stock.change_pct) : "");
            row.appendChild(chgEl);
            var rm = el("span", "wl-row-rm", "\u00d7");
            rm.addEventListener("click", function (e) {
                e.stopPropagation();
                removeFromWatchlist(sym);
            });
            row.appendChild(rm);
            row.addEventListener("click", function () { selectStock(sym); });
            $wlTable.appendChild(row);

            if (stock) {
                var dir = flashDir("wl:" + sym, stock.price);
                applyFlash(row, dir);
                applyTextFlash(priceEl, dir);
            }
        });
    }

    async function showWatchlistSuggestions(query) {
        clearChildren($wlSuggest);
        if (!query) {
            $wlSuggest.classList.remove("visible");
            return;
        }
        var matches = [];
        try {
            var res = await fetch("/api/search?q=" + encodeURIComponent(query));
            if (!res.ok) throw new Error("watchlist search failed");
            matches = await res.json();
            rememberStocks(matches);
        } catch (err) {
            console.error("Watchlist search error:", err);
        }

        if (!matches.length) {
            $wlSuggest.classList.remove("visible");
            return;
        }

        matches.forEach(function (s) {
            var inList = watchlist.indexOf(s.symbol) !== -1;
            var row = el("div", "wl-suggest-item" + (inList ? " already" : ""));
            row.appendChild(el("span", "wl-s-sym", s.symbol));
            row.appendChild(el("span", "wl-s-name", s.name || ""));
            var priceSpan = el("span", "wl-s-price " + cls(s.change_pct || 0));
            priceSpan.textContent = fmtPrice(s.price);
            row.appendChild(priceSpan);
            if (!inList) {
                row.addEventListener("click", function () {
                    addToWatchlist(s.symbol);
                    $wlInput.value = "";
                    $wlSuggest.classList.remove("visible");
                    $wlInput.focus();
                });
            }
            $wlSuggest.appendChild(row);
        });
        $wlSuggest.classList.add("visible");
    }

    var wlSuggestTimeout = null;
    $wlInput.addEventListener("input", function () {
        clearTimeout(wlSuggestTimeout);
        var query = $wlInput.value.trim();
        if (!query) {
            $wlSuggest.classList.remove("visible");
            return;
        }
        wlSuggestTimeout = setTimeout(function () {
            showWatchlistSuggestions(query);
        }, 200);
    });

    $wlInput.addEventListener("focus", function () {
        if ($wlInput.value.trim()) showWatchlistSuggestions($wlInput.value.trim());
    });

    $wlInput.addEventListener("blur", function () {
        setTimeout(function () { $wlSuggest.classList.remove("visible"); }, 180);
    });

    $wlInput.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            $wlInput.blur();
            $wlSuggest.classList.remove("visible");
        }
    });

    // ── Full Render ────────────────────────────────────────────────────

    function renderDashboard(data, isLive) {
        if (!data) return;
        rememberStocks(data.stocks || []);
        applyOverviewPanel(data, { skipCache: true });
        applyGlobalPanel({
            global_futures: data.global_futures || [],
            last_global_update: data.last_global_update,
            global_streaming: data.global_streaming,
        }, { skipCache: true });
        applyNewsPanel("all", {
            items: data.news || [],
            news_llm_pending: data.news_llm_pending,
            news_llm_enabled: data.news_llm_enabled,
        }, { skipCache: true, isLiveUpdate: isLive });
        handleResize();
    }

    function ensureWSConnection() {
        if (document.hidden) return;
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
        connectWS();
    }

    function disconnectWS() {
        if (!ws) return;
        try {
            ws.onclose = null;
            ws.close();
        } catch (err) {}
        ws = null;
    }

    // ── WebSocket ──────────────────────────────────────────────────────

    function connectWS() {
        if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
        var proto = location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(proto + "://" + location.host + "/ws");

        ws.onopen = function () {
            console.log("[WS] Connected");
            requestWatchlistBackfill();
        };

        ws.onmessage = function (evt) {
            try {
                var msg = JSON.parse(evt.data);
                if (msg.type === "update" && msg.data) {
                    renderDashboard(msg.data, true);
                } else if (msg.type === "global_tick" && msg.global_futures) {
                    if (dashboardData) {
                        dashboardData.global_futures = msg.global_futures;
                        if (msg.gift_nifty) dashboardData.gift_nifty = msg.gift_nifty;
                        if (msg.last_global_update) dashboardData.last_global_update = msg.last_global_update;
                    }
                    if (msg.gift_nifty) renderGiftNifty(msg.gift_nifty);
                    updateGlobalStatus(msg.global_streaming, msg.last_global_update, msg.global_futures || []);
                    if (currentView === "global") renderGlobalMarkets(msg.global_futures);
                } else if (msg.type === "news" && msg.news) {
                    var liveNews = {
                        items: msg.news || [],
                        as_of: new Date().toISOString(),
                        stale: false,
                        refreshing: false,
                        news_llm_pending: msg.news_llm_pending,
                        news_llm_enabled: msg.news_llm_enabled,
                    };
                    applyNewsPanel("all", liveNews, { skipCache: true, isLiveUpdate: true });
                    applyNewsPanel("breaking", {
                        items: (msg.news || []).filter(function (n) {
                            return n.breaking && !n.stock_event && !n.company_specific && n.india_market_impact;
                        }),
                        as_of: liveNews.as_of,
                        stale: false,
                        refreshing: false,
                        news_llm_pending: msg.news_llm_pending,
                        news_llm_enabled: msg.news_llm_enabled,
                    }, { skipCache: true, isLiveUpdate: true });
                } else if (msg.type === "llm_queue") {
                    if (dashboardData) {
                        if (msg.news_llm_pending !== undefined) {
                            dashboardData.news_llm_pending = msg.news_llm_pending;
                        }
                        if (msg.news_llm_enabled !== undefined) {
                            dashboardData.news_llm_enabled = msg.news_llm_enabled;
                        }
                    }
                    updateNewsLlmState(msg);
                }
            } catch (err) { console.error("[WS] Parse error:", err); }
        };

        ws.onclose = function () {
            ws = null;
            if (document.hidden) return;
            console.log("[WS] Disconnected \u2014 reconnecting in 3s");
            setTimeout(ensureWSConnection, 3000);
        };

        ws.onerror = function () {
            try { ws.close(); } catch (err) {}
        };
    }

    // ── Initial Load ───────────────────────────────────────────────────

    async function initialLoad() {
        try {
            await loadWatchlist();
            loadLocalPanelCaches();
            await loadBootstrap();
            await loadOverviewPanel();
            ensureWSConnection();
            if (isNewsVisible()) await loadNewsPanel(activeNewsRequestTab());
            if (isWatchlistVisible()) await hydrateWatchlistStocks({ force: true });
            if (currentView === "global") await loadGlobalPanel();
            if (currentView === "mutual") await loadMutualWatchlist();
        } catch (err) { console.error("Initial load error:", err); }
    }

    // ── View Switching (INVESTING / OPTIONS / GLOBAL / MUTUAL) ────────

    var $terminal = document.querySelector(".terminal");
    var navTabs = document.querySelectorAll(".nav-tab");

    navTabs.forEach(function (btn) {
        btn.addEventListener("click", function () {
            var view = btn.dataset.view;
            if (view === currentView) return;
            currentView = view;
            navTabs.forEach(function (b) { b.classList.toggle("active", b.dataset.view === view); });
            $terminal.setAttribute("data-view", view);
            if (view === "options") {
                fetchOptionChain();
                ocRefreshTimer = setInterval(fetchOptionChain, 30000);
            } else {
                if (ocRefreshTimer) { clearInterval(ocRefreshTimer); ocRefreshTimer = null; }
            }
            if (view === "investing") {
                loadOverviewPanel();
                if (isNewsVisible()) loadNewsPanel(activeNewsRequestTab());
                if (isWatchlistVisible()) hydrateWatchlistStocks({ force: true });
            }
            if (view === "global") {
                if (panelState.global && panelState.global.global_futures) renderGlobalMarkets(panelState.global.global_futures);
                else loadGlobalPanel();
            }
            if (view === "mutual") {
                loadMutualWatchlist();
                setTimeout(handleResize, 60);
            }
        });
    });

    // ── Option Chain ─────────────────────────────────────────────────────

    var ocSymbol = "NIFTY";
    var ocExpiry = "";
    var ocRefreshTimer = null;
    var $ocTbody = $("oc-tbody");
    var $ocExpiry = $("oc-expiry");
    var $ocSpot = document.querySelector("#oc-spot .oc-badge-val");
    var $ocPCR = document.querySelector("#oc-pcr .oc-badge-val");
    var $ocMaxPain = document.querySelector("#oc-maxpain .oc-badge-val");
    var $ocTotalOI = document.querySelector("#oc-total-oi .oc-badge-val");
    var $ocTimestamp = $("oc-timestamp");
    var $ocStockInput = $("oc-stock-input");

    document.querySelectorAll(".oc-sym-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            document.querySelectorAll(".oc-sym-btn").forEach(function (b) { b.classList.remove("active"); });
            btn.classList.add("active");
            $ocStockInput.value = "";
            ocSymbol = btn.dataset.ocSym;
            ocExpiry = "";
            fetchOptionChain();
        });
    });

    $ocStockInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && $ocStockInput.value.trim()) {
            document.querySelectorAll(".oc-sym-btn").forEach(function (b) { b.classList.remove("active"); });
            ocSymbol = $ocStockInput.value.trim().toUpperCase();
            ocExpiry = "";
            fetchOptionChain();
        }
    });

    $ocExpiry.addEventListener("change", function () {
        ocExpiry = $ocExpiry.value;
        fetchOptionChain();
    });

    function fmtOI(n) {
        if (!n) return "\u2014";
        if (n >= 10000000) return (n / 10000000).toFixed(1) + "Cr";
        if (n >= 100000) return (n / 100000).toFixed(1) + "L";
        if (n >= 1000) return (n / 1000).toFixed(1) + "K";
        return String(n);
    }

    async function fetchOptionChain() {
        try {
            var url = "/api/options/" + encodeURIComponent(ocSymbol);
            if (ocExpiry) url += "?expiry=" + encodeURIComponent(ocExpiry);
            var res = await fetch(url);
            var data = await res.json();
            renderOptionChain(data);
        } catch (err) {
            console.error("[OC] Fetch error:", err);
        }
    }

    function renderOptionChain(data) {
        if (data.error && !data.strikes.length) {
            $ocTbody.innerHTML = '<tr><td colspan="11" class="oc-empty">' + (data.error || "No data") + '</td></tr>';
            return;
        }

        $ocSpot.textContent = data.spot ? Number(data.spot).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "--";
        $ocPCR.textContent = data.pcr || "--";
        $ocMaxPain.textContent = data.max_pain ? Number(data.max_pain).toLocaleString("en-IN") : "--";
        $ocTotalOI.textContent = fmtOI((data.total_ce_oi || 0) + (data.total_pe_oi || 0));
        $ocTimestamp.textContent = data.timestamp || "--";

        var prevExpiry = $ocExpiry.value;
        if (data.expiries && data.expiries.length) {
            $ocExpiry.innerHTML = "";
            data.expiries.forEach(function (exp) {
                var opt = document.createElement("option");
                opt.value = exp;
                opt.textContent = exp;
                if (exp === data.selected_expiry) opt.selected = true;
                $ocExpiry.appendChild(opt);
            });
            ocExpiry = data.selected_expiry || data.expiries[0];
        }

        var spot = data.spot || 0;
        var atm = data.atm_strike || 0;
        var maxOI = data.max_oi || 1;
        var html = "";

        data.strikes.forEach(function (row) {
            var isATM = row.strike === atm;
            var ceITM = spot > row.strike;
            var peITM = spot < row.strike;
            var cls = "oc-row";
            if (isATM) cls += " oc-atm";

            var ce = row.ce || {};
            var pe = row.pe || {};
            var ceOIpct = Math.round(100 * (ce.oi || 0) / maxOI);
            var peOIpct = Math.round(100 * (pe.oi || 0) / maxOI);
            var ceChgCls = (ce.chgOI || 0) > 0 ? "up" : (ce.chgOI || 0) < 0 ? "down" : "";
            var peChgCls = (pe.chgOI || 0) > 0 ? "up" : (pe.chgOI || 0) < 0 ? "down" : "";
            var ceLtpCls = (ce.chg || 0) >= 0 ? "up" : "down";
            var peLtpCls = (pe.chg || 0) >= 0 ? "up" : "down";

            html += '<tr class="' + cls + '">';
            html += '<td class="oc-ce' + (ceITM ? " oc-itm" : "") + '"><div class="oc-oi-bar" style="width:' + ceOIpct + '%"></div><span>' + fmtOI(ce.oi) + '</span></td>';
            html += '<td class="oc-ce' + (ceITM ? " oc-itm" : "") + ' ' + ceChgCls + '">' + fmtOI(ce.chgOI) + '</td>';
            html += '<td class="oc-ce' + (ceITM ? " oc-itm" : "") + '">' + fmtOI(ce.vol) + '</td>';
            html += '<td class="oc-ce' + (ceITM ? " oc-itm" : "") + '">' + (ce.iv ? ce.iv.toFixed(1) : "\u2014") + '</td>';
            html += '<td class="oc-ce oc-ltp' + (ceITM ? " oc-itm" : "") + ' ' + ceLtpCls + '">' + (ce.ltp ? ce.ltp.toFixed(2) : "\u2014") + '</td>';

            html += '<td class="oc-strike">' + Number(row.strike).toLocaleString("en-IN") + '</td>';

            html += '<td class="oc-pe oc-ltp' + (peITM ? " oc-itm" : "") + ' ' + peLtpCls + '">' + (pe.ltp ? pe.ltp.toFixed(2) : "\u2014") + '</td>';
            html += '<td class="oc-pe' + (peITM ? " oc-itm" : "") + '">' + (pe.iv ? pe.iv.toFixed(1) : "\u2014") + '</td>';
            html += '<td class="oc-pe' + (peITM ? " oc-itm" : "") + '">' + fmtOI(pe.vol) + '</td>';
            html += '<td class="oc-pe' + (peITM ? " oc-itm" : "") + ' ' + peChgCls + '">' + fmtOI(pe.chgOI) + '</td>';
            html += '<td class="oc-pe' + (peITM ? " oc-itm" : "") + '"><div class="oc-oi-bar oc-oi-bar-pe" style="width:' + peOIpct + '%"></div><span>' + fmtOI(pe.oi) + '</span></td>';
            html += '</tr>';
        });

        $ocTbody.innerHTML = html;

        var atmRow = document.querySelector(".oc-atm");
        if (atmRow) {
            atmRow.scrollIntoView({ block: "center", behavior: "smooth" });
        }
    }

    // ── Mobile Panel Switching ────────────────────────────────────────

    var isMobile = window.matchMedia("(max-width: 700px)");
    var mobileNavBtns = document.querySelectorAll(".mobile-nav-btn");
    var allPanels = document.querySelectorAll(".panels .panel");

    function activateMobilePanel(panelId) {
        allPanels.forEach(function (p) { p.classList.remove("mobile-active"); });
        var target = document.getElementById(panelId);
        if (target) target.classList.add("mobile-active");
        mobileNavBtns.forEach(function (b) {
            b.classList.toggle("active", b.dataset.panel === panelId);
        });
        if (panelId === "panel-stock" && chart) {
            setTimeout(function () { chart.timeScale().fitContent(); }, 50);
        }
    }

    function handleMobileInit() {
        if (isMobile.matches) {
            var active = document.querySelector(".mobile-nav-btn.active");
            activateMobilePanel(active ? active.dataset.panel : "panel-news");
        } else {
            allPanels.forEach(function (p) { p.classList.remove("mobile-active"); });
        }
    }

    mobileNavBtns.forEach(function (btn) {
        btn.addEventListener("click", function () {
            activateMobilePanel(btn.dataset.panel);
            if (btn.dataset.panel === "panel-news") {
                loadNewsPanel(activeNewsRequestTab());
            } else if (btn.dataset.panel === "panel-stock") {
                hydrateWatchlistStocks({ force: true });
            }
        });
    });

    isMobile.addEventListener("change", handleMobileInit);
    handleMobileInit();

    // Pull the shared watchlist again when the tab becomes active so other
    // browsers/devices stay in sync without a full reload.
    window.addEventListener("focus", function () {
        ensureWSConnection();
        loadWatchlist();
        if (currentView === "investing") loadOverviewPanel();
        if (isWatchlistVisible()) hydrateWatchlistStocks({ staleMs: 30000 });
        if (isNewsVisible()) loadNewsPanel(activeNewsRequestTab());
        if (currentView === "global") loadGlobalPanel();
        if (currentView === "mutual") loadMutualWatchlist();
    });
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) {
            ensureWSConnection();
            loadWatchlist();
            if (currentView === "investing") loadOverviewPanel();
            if (isWatchlistVisible()) hydrateWatchlistStocks({ staleMs: 30000 });
            if (isNewsVisible()) loadNewsPanel(activeNewsRequestTab());
            if (currentView === "global") loadGlobalPanel();
            if (currentView === "mutual") loadMutualWatchlist();
        } else {
            disconnectWS();
        }
    });
    setInterval(function () {
        if (!document.hidden && currentView === "investing") {
            loadOverviewPanel();
        }
    }, 60000);
    setInterval(function () {
        if (!document.hidden && isNewsVisible() && currentNewsTab !== "watchlist") {
            loadNewsPanel(activeNewsRequestTab());
        }
    }, 60000);
    setInterval(function () {
        if (!document.hidden && isWatchlistVisible()) {
            hydrateWatchlistStocks({ staleMs: 60000 });
        }
    }, 60000);
    setInterval(function () {
        if (!document.hidden && isNewsVisible() && currentNewsTab === "watchlist") {
            requestWatchlistBackfill();
        }
    }, 120000);
    setInterval(function () {
        if (!document.hidden && currentView === "global") {
            loadGlobalPanel();
        }
    }, 60000);
    setInterval(function () {
        if (!document.hidden && currentView === "mutual") {
            loadMutualWatchlist();
        }
    }, 60000);

    // ── Boot ───────────────────────────────────────────────────────────

    initialLoad();

})();

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
    let mfChartSeries = [];
    let mutualChartRenderKey = "";
    let fiiChartRenderKey = "";
    let fiiChartRows = [];
    let fiiChartResizeTimer = null;
    let fiiSeriesVisibility = { fii: true, dii: true, nifty: true };
    let selectedFiiRange = "1m";
    const MULTI_SERIES_COLORS = [
        "#ff9d3f",
        "#4fd1c5",
        "#7dd3fc",
        "#c084fc",
        "#f472b6",
        "#a3e635",
        "#fb7185",
        "#facc15",
        "#38bdf8",
        "#34d399",
        "#f97316",
        "#818cf8",
    ];
    let newsLlmState = {
        news_llm_pending: undefined,
        news_llm_enabled: undefined,
    };
    let panelState = {
        bootstrap: null,
        overview: null,
        global: null,
        fii: null,
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
        customStartDate: null,
        customEndDate: null,
        customRangeError: null,
        selectedChartSchemeCodes: [],
        compare: null,
        compareLoading: false,
        compareError: null,
        multiCompare: null,
        multiCompareLoading: false,
        multiCompareError: null,
    };
    let stockCache = {};
    const stockFetchInflight = {};
    let watchlistLoadPromise = null;
    let lastWatchlistQuoteRefreshAt = 0;
    let watchlistQuotesLoadPromise = null;
    let mutualHoldingsLoadPromise = null;
    let mutualCompareLoadPromise = null;
    let mutualPerformanceLoadPromise = null;
    let mutualCompareRequestSeq = 0;
    let mutualPerformanceRequestSeq = 0;
    let mutualCompareAbortController = null;
    let mutualPerformanceAbortController = null;
    let mutualSearchAbortController = null;
    let mutualSearchResults = [];
    let mutualDeferredChartTimer = null;
    let mutualCompareBaseCache = new Map();
    let mutualPerformanceBaseCache = new Map();
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
    const $fiiUpdated  = $("fii-updated");
    const $fiiCards    = $("fii-cards");
    const $fiiChartBox = $("fii-chart");
    const $fiiTable    = $("fii-table");
    const $fiiChartTitle = $("fii-chart-title");
    const $fiiChartNote = $("fii-chart-note");
    const $fiiChartInsights = $("fii-chart-insights");
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
    const $mfCustomRange = $("mf-custom-range");
    const $mfDateFrom  = $("mf-date-from");
    const $mfDateTo    = $("mf-date-to");
    const $mfApplyDates = $("mf-apply-dates");
    const $mfClearDates = $("mf-clear-dates");
    const $mfDateError = $("mf-date-error");
    const $mfStats     = $("mf-stats");
    const $mfChartShell = $("mf-chart-shell");
    const $mfChartTitle = $("mf-chart-title");
    const $mfChartNote = $("mf-chart-note");
    const $mfChartBox  = $("mf-chart");
    const $mfSelectAll = $("mf-select-all");
    const $mfClearAll  = $("mf-clear-all");
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

    const GM_HEATMAP_SPECS = [
        {
            key: "usa",
            label: "USA",
            shortLabel: "US",
            source: "US composite",
            names: ["S&P 500", "NASDAQ", "DOW JONES", "RUSSELL 2000"],
            mode: "average",
            countryIds: ["us"],
            anchorX: 0.64,
            anchorY: 0.48,
        },
        {
            key: "uk",
            label: "UK",
            source: "FTSE 100",
            names: ["FTSE 100"],
            countryIds: ["gb"],
            dx: -6,
            dy: -10,
        },
        {
            key: "france",
            label: "FRANCE",
            shortLabel: "FR",
            source: "CAC 40",
            names: ["CAC 40"],
            countryIds: ["frx"],
            dx: -18,
            dy: 22,
        },
        {
            key: "germany",
            label: "GERMANY",
            shortLabel: "DE",
            source: "DAX",
            names: ["DAX"],
            countryIds: ["de"],
            dx: 18,
            dy: -2,
        },
        {
            key: "eurozone",
            label: "EU STOXX",
            shortLabel: "EU",
            source: "EURO STOXX 50",
            names: ["EURO STOXX 50"],
            countryIds: [],
            badgeX: 1438,
            badgeY: 356,
        },
        {
            key: "india",
            label: "INDIA",
            shortLabel: "IN",
            source: "GIFT NIFTY",
            names: ["GIFT NIFTY"],
            countryIds: ["in"],
            anchorX: 0.58,
            anchorY: 0.54,
            dx: 24,
            dy: 8,
        },
        {
            key: "china",
            label: "CHINA",
            shortLabel: "CN",
            source: "SHANGHAI",
            names: ["SHANGHAI"],
            countryIds: ["cnx"],
            anchorX: 0.58,
            anchorY: 0.52,
            dx: 52,
            dy: 8,
        },
        {
            key: "hongkong",
            label: "HK",
            source: "HANG SENG",
            names: ["HANG SENG"],
            countryIds: ["hk"],
            dx: -18,
            dy: 26,
        },
        {
            key: "korea",
            label: "KOREA",
            shortLabel: "KR",
            source: "KOSPI",
            names: ["KOSPI"],
            countryIds: ["kr"],
            dx: 14,
            dy: -2,
        },
        {
            key: "taiwan",
            label: "TAIWAN",
            shortLabel: "TW",
            source: "TAIWAN",
            names: ["TAIWAN"],
            countryIds: ["tw"],
            dx: 16,
            dy: 14,
        },
        {
            key: "japan",
            label: "JAPAN",
            shortLabel: "JP",
            source: "NIKKEI 225",
            names: ["NIKKEI 225"],
            countryIds: ["jp"],
            anchorX: 0.44,
            anchorY: 0.42,
            dx: 22,
            dy: -18,
        },
        {
            key: "singapore",
            label: "SG",
            source: "STRAITS TIMES",
            names: ["STRAITS TIMES"],
            countryIds: ["sg"],
            dx: 12,
            dy: 18,
        },
        {
            key: "thailand",
            label: "THAILAND",
            shortLabel: "TH",
            source: "SET COMPOSITE",
            names: ["SET COMPOSITE"],
            countryIds: ["th"],
            dx: 26,
            dy: -10,
        },
        {
            key: "indonesia",
            label: "INDONESIA",
            shortLabel: "ID",
            source: "JAKARTA",
            names: ["JAKARTA"],
            countryIds: ["id"],
            anchorX: 0.52,
            anchorY: 0.50,
            dy: 12,
        },
    ];

    var gmWorldSvgTemplate = null;
    var gmWorldSvgPromise = null;

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

    function fmtCr(n) {
        if (n == null || isNaN(Number(n))) return "\u2014";
        return "\u20b9" + Number(n).toLocaleString("en-IN", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        }) + " Cr";
    }

    function fmtSignedCr(n) {
        if (n == null || isNaN(Number(n))) return "\u2014";
        var value = Number(n);
        return (value >= 0 ? "+" : "-") + fmtCr(Math.abs(value));
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
                return n.breaking && !n.stock_event && !n.company_specific;
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
            var sentiment = String(n.sentiment || "").toLowerCase();
            var sentimentClass = "";
            if (sentiment === "bullish") sentimentClass = " wire-row-bullish";
            else if (sentiment === "bearish") sentimentClass = " wire-row-bearish";

            var row = el("div", "wire-row" + sentimentClass + (isNew ? " wire-new" : ""));
            if (n.link) {
                row.addEventListener("click", function () { window.open(n.link, "_blank"); });
            }

            var tag = el("span", "wire-tag");
            if (n.breaking) {
                tag.className = "wire-tag wire-tag-breaking";
                tag.textContent = n.breaking_cluster_count ? ("BREAKING " + n.breaking_cluster_count + "x") : "BREAKING";
                if (n.breaking_sources && n.breaking_sources.length) {
                    tag.title = "Confirmed by " + n.breaking_sources.join(", ");
                }
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
            if (sentiment && sentiment !== "neutral") {
                titleCls += " wire-sent-" + sentiment;
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
            if (sentiment === "bullish" || sentiment === "bearish") {
                row.appendChild(el(
                    "span",
                    "wire-sent-chip wire-sent-chip-" + sentiment,
                    sentiment === "bullish" ? "BULL" : "BEAR"
                ));
            }
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

    function ensureGlobalWorldSvgTemplate() {
        if (gmWorldSvgTemplate) return Promise.resolve(gmWorldSvgTemplate);
        if (gmWorldSvgPromise) return gmWorldSvgPromise;
        gmWorldSvgPromise = fetch("/static/world-map-compact.svg")
            .then(function (res) {
                if (!res.ok) throw new Error("world map asset unavailable");
                return res.text();
            })
            .then(function (text) {
                var doc = new DOMParser().parseFromString(text, "image/svg+xml");
                var root = document.importNode(doc.documentElement, true);
                root.classList.add("gm-world-svg");
                root.setAttribute("preserveAspectRatio", "xMidYMid meet");
                root.removeAttribute("width");
                root.removeAttribute("height");
                root.setAttribute("role", "img");
                root.setAttribute("aria-label", "Live global markets world map");
                gmWorldSvgTemplate = root;
                return gmWorldSvgTemplate;
            })
            .catch(function (err) {
                gmWorldSvgPromise = null;
                throw err;
            });
        return gmWorldSvgPromise;
    }

    function globalHeatNodes(svgRoot, ids) {
        return (ids || []).map(function (id) {
            return svgRoot.querySelector("#" + id);
        }).filter(Boolean);
    }

    function unionBBox(nodes) {
        var box = null;
        (nodes || []).forEach(function (node) {
            var b = node.getBBox();
            if (!b || !isFinite(b.x) || !isFinite(b.y) || !isFinite(b.width) || !isFinite(b.height)) return;
            if (!box) {
                box = { x: b.x, y: b.y, width: b.width, height: b.height };
                return;
            }
            var minX = Math.min(box.x, b.x);
            var minY = Math.min(box.y, b.y);
            var maxX = Math.max(box.x + box.width, b.x + b.width);
            var maxY = Math.max(box.y + box.height, b.y + b.height);
            box = { x: minX, y: minY, width: maxX - minX, height: maxY - minY };
        });
        return box;
    }

    function tintGlobalCountry(nodes, tone, status) {
        (nodes || []).forEach(function (node) {
            node.setAttribute("data-market", status === "OPEN" ? "open" : "closed");
            node.style.setProperty("--gm-fill", tone.fill);
            node.style.setProperty("--gm-stroke", tone.stroke);
            node.style.setProperty("--gm-stroke-width", status === "OPEN" ? "1.7" : "1.3");
        });
    }

    function createSvgNode(tag) {
        return document.createElementNS("http://www.w3.org/2000/svg", tag);
    }

    function appendGlobalBadge(layer, entry, anchorX, anchorY) {
        var spec = entry.spec;
        var tone = globalHeatTone(entry.pct);
        var pctText = entry.pct == null ? "\u2014" : fmtPct(entry.pct);
        var label = globalHeatDisplayLabel(spec);
        var width = Math.max(48, 34 + label.length * 9);
        var height = window.innerWidth < 920 ? 34 : 40;
        var x = anchorX - width / 2;
        var y = anchorY - height / 2;
        var group = createSvgNode("g");
        group.setAttribute("class", "gm-market-badge" + (entry.status === "OPEN" ? " is-open" : ""));
        group.setAttribute("tabindex", "0");

        var title = createSvgNode("title");
        title.textContent = spec.label + " · " + spec.source + " · " + pctText + (entry.status === "OPEN" ? " · open" : "");
        group.appendChild(title);

        if (spec.countryIds && spec.countryIds.length && (spec.dx || spec.dy)) {
            var line = createSvgNode("path");
            line.setAttribute("class", "gm-market-link");
            line.setAttribute("d", "M" + (anchorX - (spec.dx || 0)) + " " + (anchorY - (spec.dy || 0)) + " L" + anchorX + " " + anchorY);
            group.appendChild(line);
        }

        var rect = createSvgNode("rect");
        rect.setAttribute("class", "gm-market-badge-bg");
        rect.setAttribute("x", x.toFixed(1));
        rect.setAttribute("y", y.toFixed(1));
        rect.setAttribute("width", width.toFixed(1));
        rect.setAttribute("height", height);
        rect.setAttribute("rx", "15");
        rect.setAttribute("ry", "15");
        rect.setAttribute("style", "--gm-badge-stroke:" + tone.stroke + "; --gm-badge-fill:" + tone.fill + ";");
        group.appendChild(rect);

        var code = createSvgNode("text");
        code.setAttribute("class", "gm-market-badge-code");
        code.setAttribute("x", anchorX);
        code.setAttribute("y", anchorY - 4);
        code.setAttribute("text-anchor", "middle");
        code.textContent = label;
        group.appendChild(code);

        var pct = createSvgNode("text");
        pct.setAttribute("class", "gm-market-badge-pct");
        pct.setAttribute("x", anchorX);
        pct.setAttribute("y", anchorY + 14);
        pct.setAttribute("text-anchor", "middle");
        pct.setAttribute("fill", tone.text);
        pct.textContent = pctText;
        group.appendChild(pct);

        layer.appendChild(group);
    }

    function renderGlobalHeatmap(futures) {
        if (!$gmHeatmap) return;
        clearChildren($gmHeatmap);
        if (!futures || !futures.length) {
            $gmHeatmap.appendChild(el("div", "gm-loading", "Building world market map…"));
            return;
        }

        ensureGlobalWorldSvgTemplate()
            .then(function (template) {
                if (!$gmHeatmap) return;
                clearChildren($gmHeatmap);
                var svgRoot = template.cloneNode(true);
                $gmHeatmap.appendChild(svgRoot);

                var overlay = createSvgNode("g");
                overlay.setAttribute("class", "gm-market-overlay");
                svgRoot.appendChild(overlay);

                buildGlobalHeatmapEntries(futures).forEach(function (entry) {
                    var spec = entry.spec;
                    var nodes = globalHeatNodes(svgRoot, spec.countryIds);
                    var tone = globalHeatTone(entry.pct);
                    if (nodes.length) {
                        tintGlobalCountry(nodes, tone, entry.status);
                    }

                    var box = unionBBox(nodes);
                    var anchorX = spec.badgeX != null
                        ? spec.badgeX
                        : box
                            ? box.x + box.width * (spec.anchorX == null ? 0.5 : spec.anchorX)
                            : 0;
                    var anchorY = spec.badgeY != null
                        ? spec.badgeY
                        : box
                            ? box.y + box.height * (spec.anchorY == null ? 0.5 : spec.anchorY)
                            : 0;
                    anchorX += spec.dx || 0;
                    anchorY += spec.dy || 0;

                    if (anchorX && anchorY) {
                        appendGlobalBadge(overlay, entry, anchorX, anchorY);
                    }
                });
            })
            .catch(function () {
                clearChildren($gmHeatmap);
                $gmHeatmap.appendChild(el("div", "gm-loading", "World market map unavailable"));
            });
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

    // ── Render: FII / DII Flows ───────────────────────────────────────

    function flowToneClass(value) {
        var n = Number(value || 0);
        if (n > 0) return "is-positive";
        if (n < 0) return "is-negative";
        return "is-neutral";
    }

    function renderFiiCards(items) {
        if (!$fiiCards) return;
        clearChildren($fiiCards);
        if (!items || !items.length) {
            $fiiCards.appendChild(el("div", "fii-loading", "No official FII/DII flow loaded yet."));
            return;
        }
        items.forEach(function (item) {
            var card = el("div", "fii-card " + flowToneClass(item.net));
            var top = el("div", "fii-card-top");
            top.appendChild(el("span", "fii-card-label", item.label || item.category || "FLOW"));
            top.appendChild(el("span", "fii-card-date", item.date_label || item.date || "\u2014"));
            card.appendChild(top);
            card.appendChild(el("div", "fii-card-net", fmtSignedCr(item.net)));
            var grid = el("div", "fii-card-grid");
            [["BUY", item.buy], ["SELL", item.sell]].forEach(function (pair) {
                var cell = el("div", "fii-mini-stat");
                cell.appendChild(el("span", "fii-mini-label", pair[0]));
                cell.appendChild(el("span", "fii-mini-value", fmtCr(pair[1])));
                grid.appendChild(cell);
            });
            card.appendChild(grid);
            $fiiCards.appendChild(card);
        });
    }

    function createFiiSvg(tag, attrs, text) {
        var node = document.createElementNS("http://www.w3.org/2000/svg", tag);
        Object.keys(attrs || {}).forEach(function (key) {
            node.setAttribute(key, attrs[key]);
        });
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function fmtFlowAxis(value) {
        var amount = Math.abs(Number(value || 0));
        var sign = Number(value || 0) < 0 ? "-" : "";
        if (amount >= 1000) return sign + (amount / 1000).toFixed(1).replace(/\.0$/, "") + "k";
        return sign + amount.toFixed(0);
    }

    function shortFiiDate(value, includeYear) {
        var dt = new Date(value);
        if (isNaN(dt.getTime())) return value || "\u2014";
        return dt.toLocaleDateString("en-IN", {
            day: "2-digit",
            month: "short",
            year: includeYear ? "2-digit" : undefined,
        });
    }

    function destroyFiiChart() {
        if (!$fiiChartBox) return;
        $fiiChartBox.onmousemove = null;
        $fiiChartBox.onmouseleave = null;
        $fiiChartBox.ontouchmove = null;
        $fiiChartBox.ontouchend = null;
        clearChildren($fiiChartBox);
        fiiChartRenderKey = "";
    }

    function renderFiiInsights(rows) {
        if (!$fiiChartInsights) return;
        clearChildren($fiiChartInsights);
        if (!rows.length) return;
        var fiiNet = rows.reduce(function (sum, row) { return sum + Number(row.fii_net || 0); }, 0);
        var diiNet = rows.reduce(function (sum, row) { return sum + Number(row.dii_net || 0); }, 0);
        var fiiSellDays = rows.filter(function (row) { return Number(row.fii_net || 0) < 0; }).length;
        var biggest = rows.reduce(function (current, row) {
            return !current || Math.abs(Number(row.fii_net || 0)) > Math.abs(Number(current.fii_net || 0))
                ? row
                : current;
        }, null);
        var absorption = fiiNet < 0 ? Math.round(Math.abs(diiNet) / Math.abs(fiiNet) * 100) : null;
        [
            ["RANGE NET", selectedFiiRange.toUpperCase(), "FII " + fmtSignedCr(fiiNet) + " \u00b7 DII " + fmtSignedCr(diiNet), flowToneClass(fiiNet + diiNet)],
            ["DII ABSORPTION", absorption == null ? "\u2014" : absorption + "%", absorption == null ? "FII cash flow is net positive" : "of FII net selling offset", absorption != null && absorption >= 70 ? "is-positive" : "is-neutral"],
            ["FII SELL DAYS", fiiSellDays + " / " + rows.length, "daily cash-market sessions", fiiSellDays > rows.length / 2 ? "is-negative" : "is-neutral"],
            ["BIGGEST FII DAY", fmtSignedCr(biggest && biggest.fii_net), shortFiiDate(biggest && biggest.date, true), flowToneClass(biggest && biggest.fii_net)],
        ].forEach(function (item) {
            var card = el("div", "fii-insight " + item[3]);
            card.appendChild(el("span", "fii-insight-label", item[0]));
            card.appendChild(el("strong", "fii-insight-value", item[1]));
            card.appendChild(el("span", "fii-insight-note", item[2]));
            $fiiChartInsights.appendChild(card);
        });
    }

    function renderFiiChart(history) {
        if (!$fiiChartBox) return;
        var rows = (history || []).filter(function (row) {
            return row && row.date && !isNaN(Number(row.fii_net)) && !isNaN(Number(row.dii_net));
        });
        fiiChartRows = rows;
        var width = Math.max(640, Math.round($fiiChartBox.clientWidth || 960));
        var height = Math.max(300, Math.round($fiiChartBox.clientHeight || 340));
        var first = rows[0] || {};
        var last = rows[rows.length - 1] || {};
        var renderKey = [
            width, height, rows.length, first.date, last.date, last.fii_net, last.dii_net, last.nifty_close,
            fiiSeriesVisibility.fii, fiiSeriesVisibility.dii, fiiSeriesVisibility.nifty,
        ].join(":");
        if (rows.length && fiiChartRenderKey === renderKey) return;
        destroyFiiChart();
        if (!rows.length) {
            $fiiChartBox.appendChild(el("div", "fii-chart-placeholder", "Daily FII/DII flow will appear after the archive is loaded."));
            renderFiiInsights([]);
            return;
        }

        var pad = { left: 58, right: 66, top: 18, bottom: 32 };
        var plotWidth = width - pad.left - pad.right;
        var plotHeight = height - pad.top - pad.bottom;
        var zeroY = pad.top + plotHeight / 2;
        var slot = plotWidth / Math.max(rows.length, 1);
        var barWidth = Math.max(0.22, Math.min(7, slot * 0.34));
        var gap = Math.max(0.08, Math.min(1.4, slot * 0.08));
        var flowMax = Math.max(1, rows.reduce(function (max, row) {
            return Math.max(max, Math.abs(Number(row.fii_net || 0)), Math.abs(Number(row.dii_net || 0)));
        }, 0));
        var niftyRows = rows.map(function (row, index) {
            return { index: index, value: Number(row.nifty_close) };
        }).filter(function (row) { return !isNaN(row.value) && row.value > 0; });
        var niftyMin = niftyRows.reduce(function (min, row) { return Math.min(min, row.value); }, Infinity);
        var niftyMax = niftyRows.reduce(function (max, row) { return Math.max(max, row.value); }, -Infinity);
        if (!isFinite(niftyMin) || !isFinite(niftyMax)) {
            niftyMin = 0;
            niftyMax = 1;
        }
        var niftyPad = Math.max(1, (niftyMax - niftyMin) * 0.12);
        niftyMin -= niftyPad;
        niftyMax += niftyPad;

        function xFor(index) {
            return pad.left + slot * index + slot / 2;
        }

        function flowY(value) {
            return zeroY - (Number(value || 0) / flowMax) * (plotHeight / 2);
        }

        function niftyY(value) {
            return pad.top + (niftyMax - Number(value || 0)) / (niftyMax - niftyMin) * plotHeight;
        }

        function barsPath(field, position, positive) {
            var parts = [];
            rows.forEach(function (row, index) {
                var value = Number(row[field] || 0);
                if ((positive && value <= 0) || (!positive && value >= 0)) return;
                var x = xFor(index) + (position === "left" ? -gap / 2 - barWidth : gap / 2);
                var y = flowY(value);
                var top = Math.min(zeroY, y);
                var barHeight = Math.max(0.45, Math.abs(y - zeroY));
                parts.push("M" + x.toFixed(2) + "," + top.toFixed(2) + "h" + barWidth.toFixed(2) + "v" + barHeight.toFixed(2) + "h-" + barWidth.toFixed(2) + "Z");
            });
            return parts.join("");
        }

        var svg = createFiiSvg("svg", {
            class: "fii-flow-svg",
            viewBox: "0 0 " + width + " " + height,
            "data-flow-max": flowMax.toFixed(2),
            preserveAspectRatio: "none",
            role: "img",
            "aria-label": "Daily FII and DII cash-market net flows with Nifty 50 overlay",
        });
        [-1, -0.5, 0, 0.5, 1].forEach(function (step) {
            var value = step * flowMax;
            var y = flowY(value);
            svg.appendChild(createFiiSvg("line", {
                x1: pad.left, y1: y, x2: width - pad.right, y2: y,
                class: step === 0 ? "fii-flow-zero" : "fii-flow-grid",
            }));
            svg.appendChild(createFiiSvg("text", {
                x: pad.left - 8, y: y + 3, class: "fii-flow-axis is-left", "text-anchor": "end",
            }, fmtFlowAxis(value)));
        });
        [0, 0.25, 0.5, 0.75, 1].forEach(function (step) {
            var value = niftyMin + (niftyMax - niftyMin) * (1 - step);
            svg.appendChild(createFiiSvg("text", {
                x: width - pad.right + 8, y: pad.top + plotHeight * step + 3,
                class: "fii-flow-axis is-right", "text-anchor": "start",
            }, Number(value).toLocaleString("en-IN", { maximumFractionDigits: 0 })));
        });
        [0, 0.25, 0.5, 0.75, 1].forEach(function (step) {
            var index = Math.min(rows.length - 1, Math.round((rows.length - 1) * step));
            svg.appendChild(createFiiSvg("text", {
                x: xFor(index), y: height - 10, class: "fii-flow-axis is-date", "text-anchor": "middle",
            }, shortFiiDate(rows[index].date, rows.length > 120)));
        });
        if (fiiSeriesVisibility.fii) {
            svg.appendChild(createFiiSvg("path", { d: barsPath("fii_net", "left", true), class: "fii-flow-bars is-fii is-positive" }));
            svg.appendChild(createFiiSvg("path", { d: barsPath("fii_net", "left", false), class: "fii-flow-bars is-fii is-negative" }));
        }
        if (fiiSeriesVisibility.dii) {
            svg.appendChild(createFiiSvg("path", { d: barsPath("dii_net", "right", true), class: "fii-flow-bars is-dii is-positive" }));
            svg.appendChild(createFiiSvg("path", { d: barsPath("dii_net", "right", false), class: "fii-flow-bars is-dii is-negative" }));
        }
        if (fiiSeriesVisibility.nifty && niftyRows.length) {
            svg.appendChild(createFiiSvg("path", {
                d: niftyRows.map(function (row, index) {
                    return (index ? "L" : "M") + xFor(row.index).toFixed(2) + "," + niftyY(row.value).toFixed(2);
                }).join(""),
                class: "fii-nifty-line",
            }));
        }
        var hoverLine = createFiiSvg("line", {
            x1: 0, y1: pad.top, x2: 0, y2: height - pad.bottom, class: "fii-flow-crosshair",
        });
        hoverLine.style.display = "none";
        svg.appendChild(hoverLine);
        $fiiChartBox.appendChild(svg);

        var tooltip = el("div", "fii-flow-tooltip");
        tooltip.hidden = true;
        $fiiChartBox.appendChild(tooltip);
        function appendTipRow(label, value, tone) {
            var row = el("div", "fii-flow-tooltip-row");
            row.appendChild(el("span", "fii-flow-tooltip-label", label));
            row.appendChild(el("strong", "fii-flow-tooltip-value " + (tone || ""), value));
            tooltip.appendChild(row);
        }
        function updateTooltip(event) {
            var rect = svg.getBoundingClientRect();
            var chartX = (event.clientX - rect.left) * width / rect.width;
            var index = Math.max(0, Math.min(rows.length - 1, Math.round((chartX - pad.left - slot / 2) / slot)));
            var row = rows[index];
            var x = xFor(index);
            hoverLine.setAttribute("x1", x);
            hoverLine.setAttribute("x2", x);
            hoverLine.style.display = "";
            clearChildren(tooltip);
            tooltip.appendChild(el("div", "fii-flow-tooltip-date", fmtDateLabel(row.date)));
            appendTipRow("FII Cash", fmtSignedCr(row.fii_net), flowToneClass(row.fii_net));
            appendTipRow("DII Cash", fmtSignedCr(row.dii_net), flowToneClass(row.dii_net));
            if (row.nifty_close != null) {
                var niftyPct = row.nifty_prev_close
                    ? (Number(row.nifty_close) - Number(row.nifty_prev_close)) / Number(row.nifty_prev_close) * 100
                    : null;
                appendTipRow("Nifty 50", Number(row.nifty_close).toLocaleString("en-IN", { maximumFractionDigits: 2 }) + (niftyPct == null ? "" : "  " + fmtPct(niftyPct)), flowToneClass(niftyPct));
            }
            tooltip.hidden = false;
            var boxRect = $fiiChartBox.getBoundingClientRect();
            var tooltipLeft = event.clientX - boxRect.left + 14;
            if (tooltipLeft + tooltip.offsetWidth > boxRect.width - 10) {
                tooltipLeft = event.clientX - boxRect.left - tooltip.offsetWidth - 14;
            }
            tooltip.style.left = Math.max(10, tooltipLeft) + "px";
            tooltip.style.top = "12px";
        }
        $fiiChartBox.onmousemove = updateTooltip;
        $fiiChartBox.onmouseleave = function () {
            tooltip.hidden = true;
            hoverLine.style.display = "none";
        };
        $fiiChartBox.ontouchmove = function (event) {
            if (event.touches && event.touches[0]) updateTooltip(event.touches[0]);
        };
        $fiiChartBox.ontouchend = $fiiChartBox.onmouseleave;
        fiiChartRenderKey = renderKey;
        renderFiiInsights(rows);
    }

    function renderFiiTable(history) {
        if (!$fiiTable) return;
        clearChildren($fiiTable);
        var rows = (history || []).slice(-12).reverse();
        if (!rows.length) {
            var empty = el("tr");
            empty.appendChild(el("td", "", "No FII flow history yet."));
            empty.firstChild.colSpan = 4;
            $fiiTable.appendChild(empty);
            return;
        }
        rows.forEach(function (row) {
            var tr = el("tr");
            tr.appendChild(el("td", "fii-date-cell", row.date_label || row.date || "\u2014"));
            tr.appendChild(el("td", "fii-net-cell " + flowToneClass(row.fii_net), fmtSignedCr(row.fii_net)));
            tr.appendChild(el("td", "fii-net-cell " + flowToneClass(row.dii_net), fmtSignedCr(row.dii_net)));
            tr.appendChild(el("td", "fii-net-cell " + flowToneClass(row.combined_net), fmtSignedCr(row.combined_net)));
            $fiiTable.appendChild(tr);
        });
    }

    function renderFiiFlows(data) {
        if (!data) return;
        selectedFiiRange = data.chart_range || selectedFiiRange;
        document.querySelectorAll(".fii-range").forEach(function (btn) {
            btn.classList.toggle("is-active", btn.dataset.range === selectedFiiRange);
        });
        if ($fiiUpdated) {
            var parts = [];
            if (data.latest_date_label) parts.push("NSE " + data.latest_date_label);
            if (data.history_count) parts.push(data.history_count + " history rows");
            if (data.history_source) parts.push("history via " + data.history_source);
            if (data.updated_at) parts.push("Updated " + data.updated_at);
            if (data.stale) parts.push("stale cache");
            if (data.refreshing) parts.push("refreshing");
            $fiiUpdated.textContent = parts.length ? parts.join(" \u00b7 ") : "Official NSE daily report";
        }
        if ($fiiChartTitle) {
            $fiiChartTitle.textContent = "FII / DII Flow Over Time";
        }
        if ($fiiChartNote) {
            var sessions = data.chart_count ? data.chart_count + " sessions" : "daily values";
            $fiiChartNote.textContent = sessions + " \u00b7 Nifty overlay \u00b7 Values in \u20b9 crore";
        }
        renderFiiCards(data.items || []);
        renderFiiChart(data.chart || data.history || []);
        renderFiiTable(data.history || []);
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

    function applyFiiPanel(data, opts) {
        opts = opts || {};
        if (!data) return;
        panelState.fii = data;
        if (currentView === "fii") renderFiiFlows(data);
        if (!opts.skipCache) savePanelCache("fii", data);
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
        var fii = loadPanelCache("fii");
        var newsAll = loadPanelCache("news:all");
        var newsBreaking = loadPanelCache("news:breaking");
        var watchlistNews = loadPanelCache("news:watchlist");
        var watchlistQuotes = loadPanelCache("watchlist:quotes");
        if (overview) applyOverviewPanel(overview, { skipCache: true });
        if (global) applyGlobalPanel(global, { skipCache: true });
        if (fii) applyFiiPanel(fii, { skipCache: true });
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

    async function loadFiiPanel(chartRange) {
        var normalizedRange = chartRange || selectedFiiRange || "1m";
        try {
            var data = await fetchJson("/api/panel/fii?range=" + encodeURIComponent(normalizedRange), { timeoutMs: 12000 });
            if (normalizedRange !== selectedFiiRange) return data;
            applyFiiPanel(data);
            return data;
        } catch (err) {
            console.error("FII flow fetch error:", err);
            if (panelState.fii && currentView === "fii") renderFiiFlows(panelState.fii);
            return panelState.fii;
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
                customStartDate: mutualState.customStartDate,
                customEndDate: mutualState.customEndDate,
                chartSchemeCodes: mutualState.selectedChartSchemeCodes || [],
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
        var source = mutualState.multiCompare || mutualState.compare;
        if (source && source.range_options && source.range_options.length) {
            return source.range_options;
        }
        return [
            { key: "1d", label: "1D" },
            { key: "3d", label: "3D" },
            { key: "1w", label: "1W" },
            { key: "1m", label: "1M" },
            { key: "3m", label: "3M" },
            { key: "6m", label: "6M" },
            { key: "1y", label: "1Y" },
            { key: "2y", label: "2Y" },
            { key: "5y", label: "5Y" },
            { key: "10y", label: "10Y" },
            { key: "max", label: "FULL" },
        ];
    }

    function currentChartCodes() {
        var valid = new Set((mutualState.watchlist || []).map(function (item) {
            return String(item.scheme_code || "").trim();
        }));
        var seen = new Set();
        var result = [];
        (mutualState.selectedChartSchemeCodes || []).forEach(function (code) {
            var key = String(code || "").trim();
            if (!key || seen.has(key) || !valid.has(key)) return;
            seen.add(key);
            result.push(key);
        });
        return result;
    }

    function positiveTone(value) {
        return value > 0 ? "up" : (value < 0 ? "down" : "neutral");
    }

    function compareBaseCacheKey(schemeCode, benchmark, rangeKey, customStart, customEnd) {
        return [
            String(schemeCode || "").trim(),
            String(benchmark || "").trim(),
            String(rangeKey || "max").trim(),
            rangeKey === "custom" ? String(customStart || "") : "",
            rangeKey === "custom" ? String(customEnd || "") : "",
        ].join("|");
    }

    function performanceBaseCacheKey(schemeCodes, rangeKey, customStart, customEnd) {
        return [
            (schemeCodes || []).map(function (code) {
                return String(code || "").trim();
            }).filter(Boolean).sort().join(","),
            String(rangeKey || "max").trim(),
            rangeKey === "custom" ? String(customStart || "") : "",
            rangeKey === "custom" ? String(customEnd || "") : "",
        ].join("|");
    }

    function localTodayDateValue() {
        var now = new Date();
        var year = String(now.getFullYear());
        var month = String(now.getMonth() + 1).padStart(2, "0");
        var day = String(now.getDate()).padStart(2, "0");
        return [year, month, day].join("-");
    }

    function mutualCustomRangeMatches(payload, rangeKey, customStart, customEnd) {
        if (!payload || payload.range !== rangeKey) return false;
        if (rangeKey !== "custom") return true;
        return payload.requested_from_date === customStart && payload.requested_to_date === customEnd;
    }

    function mutualRangeQuery(rangeKey, customStart, customEnd) {
        var query = "&range=" + encodeURIComponent(rangeKey);
        if (rangeKey === "custom") {
            query += "&start_date=" + encodeURIComponent(customStart || "");
            query += "&end_date=" + encodeURIComponent(customEnd || "");
        }
        return query;
    }

    function cancelSingleCompare() {
        if (mutualCompareAbortController) {
            mutualCompareAbortController.abort();
            mutualCompareAbortController = null;
        }
        mutualCompareLoadPromise = null;
    }

    function cancelMultiCompare() {
        if (mutualPerformanceAbortController) {
            mutualPerformanceAbortController.abort();
            mutualPerformanceAbortController = null;
        }
        mutualPerformanceLoadPromise = null;
    }

    function scheduleDeferredMutualChartLoad(force) {
        clearTimeout(mutualDeferredChartTimer);
        mutualDeferredChartTimer = setTimeout(function () {
            mutualDeferredChartTimer = null;
            loadActiveMutualChart({ force: !!force });
        }, 220);
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
        var chartCount = currentChartCodes().length;
        var parts = [
            "Shared",
            count + " fund" + (count === 1 ? "" : "s"),
        ];
        if (count) parts.push(chartCount + " charted");
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
        if ($mfSelectAll) {
            $mfSelectAll.checked = !!count && chartCount === count;
            $mfSelectAll.indeterminate = chartCount > 0 && chartCount < count;
            $mfSelectAll.disabled = !count;
        }
        if ($mfClearAll) {
            $mfClearAll.disabled = !chartCount;
        }
    }

    function renderMutualWatchlist() {
        if (!$mfHoldings) return;
        clearChildren($mfHoldings);
        var funds = mutualState.watchlist || [];
        var selectedSet = new Set(currentChartCodes());
        if (!funds.length) {
            $mfHoldings.appendChild(el("div", "mf-list-empty", "No funds tracked yet."));
            return;
        }
        var frag = document.createDocumentFragment();
        funds.forEach(function (fund) {
            var code = String(fund.scheme_code || "");
            var active = code === String(mutualState.selectedSchemeCode || "");
            var chartSelected = selectedSet.has(code);
            var card = el("div", "mf-holding-card" + (active ? " active" : "") + (chartSelected ? " chart-selected" : ""));
            card.tabIndex = 0;
            card.setAttribute("role", "button");

            var top = el("div", "mf-holding-top");
            top.appendChild(el("span", "mf-holding-name", fund.scheme_name || code || "Mutual Fund"));
            var tools = el("div", "mf-holding-tools");

            var toggleLabel = el("label", "mf-select-toggle");
            var checkbox = document.createElement("input");
            checkbox.type = "checkbox";
            checkbox.checked = chartSelected;
            checkbox.addEventListener("click", function (e) {
                e.stopPropagation();
            });
            checkbox.addEventListener("change", function (e) {
                e.stopPropagation();
                toggleMutualChartSelection(code, checkbox.checked);
            });
            toggleLabel.appendChild(checkbox);
            toggleLabel.appendChild(el("span", "", "Chart"));
            tools.appendChild(toggleLabel);

            var removeBtn = el("button", "mf-remove", "×");
            removeBtn.type = "button";
            removeBtn.addEventListener("click", function (e) {
                e.stopPropagation();
                removeMutualFund(code);
            });
            tools.appendChild(removeBtn);
            top.appendChild(tools);
            card.appendChild(top);

            var meta = el("div", "mf-holding-meta");
            var categoryLabel = fund.category ? titleCase(fund.category) : "Unclassified";
            meta.appendChild(el("span", "mf-holding-chip", categoryLabel));
            if (fund.benchmark_options && fund.benchmark_options.length) {
                meta.appendChild(el("span", "mf-holding-chip muted", fund.benchmark_options[0]));
            }
            meta.appendChild(el("span", "mf-holding-chip muted", "Code " + (code || "—")));
            card.appendChild(meta);

            var foot = el("div", "mf-holding-foot");
            foot.appendChild(el("span", "mf-holding-value", fund.latest_nav != null ? fmtPrice(fund.latest_nav) : "NAV —"));
            foot.appendChild(el("span", "mf-holding-date", fund.latest_nav_date ? "NAV " + fmtDateLabel(fund.latest_nav_date) : "NAV date unavailable"));
            card.appendChild(foot);

            card.addEventListener("click", function () {
                setMutualSelection(code, { loadChart: true });
            });
            card.addEventListener("keydown", function (e) {
                if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    setMutualSelection(code, { loadChart: true });
                }
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
                mode: LightweightCharts.CrosshairMode.Magnet,
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
        return mfChart;
    }

    function resetMutualChartSeries() {
        if (!mfChart) return;
        mfChartSeries.forEach(function (series) {
            try { mfChart.removeSeries(series); } catch (err) {}
        });
        mfChartSeries = [];
        mfFundSeries = null;
        mfBenchmarkSeries = null;
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

    function cumulativeReturnSeries(points) {
        var source = (points || []).filter(function (point) {
            return point && point.time != null && isFinite(Number(point.value));
        });
        if (!source.length) return [];
        var base = Number(source[0].value);
        if (!isFinite(base) || base === 0) return [];
        return source.map(function (point) {
            return {
                time: point.time,
                value: Math.round(((Number(point.value) / base) - 1) * 10000) / 100,
            };
        });
    }

    function mutualReturnPriceFormat() {
        return {
            type: "custom",
            minMove: 0.01,
            formatter: function (value) {
                return fmtPct(Number(value));
            },
        };
    }

    function renderSingleMutualChart(compare) {
        if (!$mfChartBox) return;
        if (!compare || !compare.fund_chart_data || !compare.benchmark_chart_data || !compare.fund_chart_data.length) {
            resetMutualChartSeries();
            mutualChartRenderKey = "";
            showMutualChartPlaceholder(
                mutualState.compareLoading
                    ? "Loading official NAV and benchmark history…"
                    : (mutualState.compareError || "NAV summary loaded. Click a fund or benchmark to build the chart.")
            );
            return;
        }
        var renderKey = [
            "single",
            compare.fund && compare.fund.scheme_code,
            compare.benchmark,
            compare.range,
            compare.from_date || "",
            compare.render_points || 0,
            compare.to_date || "",
        ].join("|");
        if (mutualChartRenderKey === renderKey) return;

        var chartRef = ensureMutualChart();
        if (!chartRef) return;
        resetMutualChartSeries();
        hideMutualChartPlaceholder();
        var fundReturns = cumulativeReturnSeries(compare.fund_chart_data);
        var benchmarkReturns = cumulativeReturnSeries(compare.benchmark_chart_data);
        mfFundSeries = chartRef.addLineSeries({
            color: "#ff9d3f",
            lineWidth: 2,
            priceFormat: mutualReturnPriceFormat(),
            priceLineVisible: false,
            lastValueVisible: true,
            title: "Fund",
        });
        mfBenchmarkSeries = chartRef.addLineSeries({
            color: "#4fd1c5",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            priceFormat: mutualReturnPriceFormat(),
            priceLineVisible: false,
            lastValueVisible: true,
            title: compare.benchmark || "Index",
        });
        mfChartSeries = [mfFundSeries, mfBenchmarkSeries];
        mfFundSeries.setData(fundReturns);
        mfBenchmarkSeries.setData(benchmarkReturns);
        chartRef.priceScale("right").applyOptions({ autoScale: true });
        mfFundSeries.createPriceLine({
            price: 0,
            color: "rgba(142,160,184,.42)",
            lineWidth: 1,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            axisLabelVisible: true,
            title: "START",
        });
        chartRef.timeScale().fitContent();
        mutualChartRenderKey = renderKey;
    }

    function renderMultiMutualChart(payload) {
        if (!$mfChartBox) return;
        if (!payload || !payload.items || !payload.items.length) {
            resetMutualChartSeries();
            mutualChartRenderKey = "";
            showMutualChartPlaceholder(
                mutualState.multiCompareLoading
                    ? "Loading normalized NAV performance for selected funds…"
                    : (mutualState.multiCompareError || "Tick funds, or use Select all to chart them with NIFTY 50.")
            );
            return;
        }
        var renderKey = [
            "multi",
            payload.range,
            payload.from_date || "",
            payload.to_date || "",
            payload.items.map(function (item) {
                return [item.scheme_code, item.render_points || 0, item.kind || "fund"].join(":");
            }).join("|"),
        ].join("|");
        if (mutualChartRenderKey === renderKey) return;

        var chartRef = ensureMutualChart();
        if (!chartRef) return;
        resetMutualChartSeries();
        hideMutualChartPlaceholder();
        payload.items.forEach(function (item, idx) {
            var isBenchmark = item.kind === "benchmark";
            var series = chartRef.addLineSeries({
                color: isBenchmark ? "#4fd1c5" : MULTI_SERIES_COLORS[idx % MULTI_SERIES_COLORS.length],
                lineWidth: isBenchmark ? 2.5 : (idx === 0 ? 2.4 : 2),
                lineStyle: isBenchmark ? LightweightCharts.LineStyle.Dashed : LightweightCharts.LineStyle.Solid,
                priceLineVisible: false,
                lastValueVisible: payload.items.length <= 7,
                title: item.scheme_name || item.scheme_code || ("Fund " + (idx + 1)),
            });
            series.setData(item.chart_data || []);
            mfChartSeries.push(series);
        });
        chartRef.timeScale().fitContent();
        mutualChartRenderKey = renderKey;
    }

    function renderActiveMutualChart() {
        var selectedCodes = currentChartCodes();
        if (!selectedCodes.length) {
            resetMutualChartSeries();
            mutualChartRenderKey = "";
            showMutualChartPlaceholder("Tick one or more funds to chart them. Select all overlays every tracked fund with NIFTY 50.");
            return;
        }
        if (selectedCodes.length > 1) {
            renderMultiMutualChart(mutualState.multiCompare);
            return;
        }
        renderSingleMutualChart(mutualState.compare);
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
        var selectedCodes = currentChartCodes();
        var multiMode = selectedCodes.length > 1;
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
            renderActiveMutualChart();
            return;
        }
        $mfDetail.classList.remove("is-empty");
        $mfHero.hidden = false;
        $mfBenchmarkBlock.hidden = true;
        $mfRangeBlock.hidden = true;
        $mfStats.hidden = false;
        $mfChartShell.classList.remove("is-empty");

        clearChildren($mfHero);
        clearChildren($mfBenchmarks);
        clearChildren($mfRanges);
        clearChildren($mfStats);

        var compare = mutualState.compare && mutualState.compare.fund && mutualState.compare.fund.scheme_code === fund.scheme_code
            ? mutualState.compare
            : null;

        var heroTop = el("div", "mf-hero-top");
        var titleWrap = el("div", "mf-hero-copy");
        titleWrap.appendChild(el("div", "mf-hero-kicker", multiMode ? "NAV PERFORMANCE STACK" : "MF COMPARISON"));
        titleWrap.appendChild(el("h3", "mf-hero-name", fund.scheme_name || fund.scheme_code || "Mutual Fund"));
        var meta = [];
        if (fund.category) meta.push(titleCase(fund.category));
        if (fund.scheme_code) meta.push("Code " + fund.scheme_code);
        if (fund.latest_nav_date) meta.push("NAV " + fmtDateLabel(fund.latest_nav_date));
        if (multiMode) meta.push(selectedCodes.length + " funds selected");
        titleWrap.appendChild(el("div", "mf-hero-meta", meta.join(" · ") || "Comparison against official NSE benchmarks"));
        heroTop.appendChild(titleWrap);

        var heroControls = el("div", "mf-hero-controls");
        if (!multiMode) {
            var benchmarkStrip = el("div", "mf-inline-chip-row benchmark");
            mutualBenchmarkOptions(fund).forEach(function (benchmark) {
                var chip = el("button", "mf-chip" + (benchmark === mutualState.selectedBenchmark ? " active" : ""), benchmark);
                chip.type = "button";
                chip.addEventListener("click", function () {
                    setMutualSelection(String(fund.scheme_code || ""), {
                        benchmark: benchmark,
                        range: mutualState.selectedRange || "max",
                        loadChart: true,
                        forceChart: true,
                    });
                });
                benchmarkStrip.appendChild(chip);
            });
            heroControls.appendChild(benchmarkStrip);
        }

        var rangeStrip = el("div", "mf-inline-chip-row range");
        mutualRangeOptions().forEach(function (rangeOpt) {
            var chip = el("button", "mf-chip" + (rangeOpt.key === mutualState.selectedRange ? " active" : ""), rangeOpt.label);
            chip.type = "button";
            chip.addEventListener("click", function () {
                setMutualSelection(String(fund.scheme_code || ""), {
                    benchmark: mutualState.selectedBenchmark,
                    range: rangeOpt.key,
                    loadChart: true,
                    forceChart: true,
                    autoInclude: false,
                });
            });
            rangeStrip.appendChild(chip);
        });
        if ($mfCustomRange && $mfDateFrom) {
            var dateChip = el("button", "mf-chip mf-date-chip" + (mutualState.selectedRange === "custom" ? " active" : ""), "DATES");
            dateChip.type = "button";
            dateChip.addEventListener("click", function () {
                $mfCustomRange.classList.add("is-active");
                $mfDateFrom.focus();
            });
            rangeStrip.appendChild(dateChip);
        }
        heroControls.appendChild(rangeStrip);
        heroTop.appendChild(heroControls);
        $mfHero.appendChild(heroTop);

        if ($mfCustomRange && $mfDateFrom && $mfDateTo && $mfDateError) {
            var todayValue = localTodayDateValue();
            $mfDateFrom.max = todayValue;
            $mfDateTo.max = todayValue;
            $mfDateFrom.value = mutualState.customStartDate || "";
            $mfDateTo.value = mutualState.customEndDate || "";
            $mfCustomRange.classList.toggle("is-active", mutualState.selectedRange === "custom" || !!mutualState.customStartDate || !!mutualState.customEndDate || !!mutualState.customRangeError);
            $mfDateError.textContent = mutualState.customRangeError || "";
            $mfHero.appendChild($mfCustomRange);
        }

        var statCards = [];
        if (multiMode) {
            var multiItems = ((mutualState.multiCompare && mutualState.multiCompare.items) || []).filter(function (item) {
                return item.kind !== "benchmark";
            });
            var sortedItems = multiItems.slice().sort(function (a, b) {
                return (b.return_pct || 0) - (a.return_pct || 0);
            });
            statCards = [
                ["SELECTED FUNDS", String(selectedCodes.length)],
                ["BENCHMARK", "NIFTY 50"],
                ["LEAD RETURN", sortedItems[0] ? fmtPct(sortedItems[0].return_pct || 0) : "\u2014"],
            ];
        } else {
            statCards = [
                ["LATEST NAV", fund.latest_nav != null ? fmtPrice(fund.latest_nav) : "—"],
                ["NAV DATE", fmtDateLabel(fund.latest_nav_date)],
                ["BENCHMARK", mutualState.selectedBenchmark || "\u2014"],
            ];
            if (compare) {
                statCards.push(["FUND RETURN", fmtPct(compare.fund_return_pct || 0)]);
                statCards.push(["BENCHMARK RETURN", fmtPct(compare.benchmark_return_pct || 0)]);
                statCards.push(["ALPHA", fmtPct(compare.alpha_pct || 0)]);
            }
        }
        statCards.forEach(function (pair) {
            var card = el("div", "mf-stat-card");
            card.appendChild(el("span", "mf-stat-label", pair[0]));
            var tone = "neutral";
            var raw = pair[1];
            if (pair[0] === "FUND RETURN" || pair[0] === "ALPHA" || pair[0] === "LEAD RETURN") {
                var numeric = Number(String(raw).replace(/[^0-9.-]/g, ""));
                tone = numeric > 0 ? "up" : (numeric < 0 ? "down" : "neutral");
            }
            card.appendChild(el("span", "mf-stat-value " + tone, raw));
            $mfStats.appendChild(card);
        });

        if (multiMode) {
            $mfChartTitle.textContent = selectedCodes.length + " Selected Funds vs NIFTY 50";
            if (mutualState.multiCompare && mutualState.multiCompare.items && mutualState.multiCompare.items.length) {
                $mfChartNote.textContent =
                    "Normalized to 100 from " + fmtDateLabel(mutualState.multiCompare.from_date) +
                    " · " + selectedCodes.length + " funds" +
                    " · AMFI NAV vs NSE NIFTY 50";
            } else if (mutualState.multiCompareLoading) {
                $mfChartNote.textContent = "Loading selected funds with NIFTY 50…";
            } else if (mutualState.multiCompareError) {
                $mfChartNote.textContent = mutualState.multiCompareError;
            } else {
                $mfChartNote.textContent = "Use Select all or the chart checkboxes to overlay funds with NIFTY 50.";
            }
        } else {
            $mfChartTitle.textContent = (fund.scheme_name || "Mutual Fund") + " vs " + (mutualState.selectedBenchmark || "Benchmark");
            if (compare) {
            $mfChartNote.textContent =
                "Cumulative return from " + fmtDateLabel(compare.from_date) + " (0% baseline)" +
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
                $mfChartNote.textContent = "Pick a benchmark and range to compare cumulative NAV return.";
            }
        }

        renderActiveMutualChart();
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
        var chartCodes = currentChartCodes();
        if (opts.autoInclude !== false && chartCodes.indexOf(mutualState.selectedSchemeCode) === -1) {
            chartCodes.push(mutualState.selectedSchemeCode);
        }
        mutualState.selectedChartSchemeCodes = chartCodes;

        var benchmarks = mutualBenchmarkOptions(fund);
        var benchmark = opts.benchmark || mutualState.selectedBenchmark || benchmarks[0] || "NIFTY 500";
        if (benchmarks.indexOf(benchmark) === -1) benchmark = benchmarks[0] || benchmark;
        mutualState.selectedBenchmark = benchmark;

        var ranges = mutualRangeOptions();
        var rangeKeys = ranges.map(function (item) { return item.key; });
        var rangeKey = opts.range || mutualState.selectedRange || "max";
        if (rangeKey !== "custom" && rangeKeys.indexOf(rangeKey) === -1) rangeKey = "max";
        mutualState.selectedRange = rangeKey;
        mutualState.compareError = null;
        mutualState.multiCompareError = null;
        saveMutualSelectionPrefs();
        renderMutualPage();

        if (opts.loadChart === false) return;
        if (opts.deferChart) scheduleDeferredMutualChartLoad(!!opts.forceChart);
        else loadActiveMutualChart({ force: !!opts.forceChart });
    }

    function applyMutualWatchlist(data, opts) {
        opts = opts || {};
        var previousFunds = Array.isArray(mutualState.watchlist) ? mutualState.watchlist.slice() : [];
        mutualState.watchlist = (data && data.items) || [];
        mutualState.storage = data ? data.storage : null;
        mutualState.durable = !!(data && data.durable);
        var previousMeta = new Map(previousFunds.map(function (item) {
            return [
                String(item.scheme_code || "").trim(),
                [item.latest_nav_date || "", item.latest_nav == null ? "" : String(item.latest_nav)].join("|"),
            ];
        }));
        var cacheDirty = previousFunds.length !== mutualState.watchlist.length;
        if (!cacheDirty) {
            cacheDirty = mutualState.watchlist.some(function (item) {
                var code = String(item.scheme_code || "").trim();
                var nextMeta = [item.latest_nav_date || "", item.latest_nav == null ? "" : String(item.latest_nav)].join("|");
                return previousMeta.get(code) !== nextMeta;
            });
        }
        if (cacheDirty) {
            mutualCompareBaseCache.clear();
            mutualPerformanceBaseCache.clear();
        }

        if (!mutualState.watchlist.length) {
            mutualState.selectedSchemeCode = null;
            mutualState.selectedBenchmark = null;
            mutualState.selectedRange = "max";
            mutualState.customStartDate = null;
            mutualState.customEndDate = null;
            mutualState.customRangeError = null;
            mutualState.selectedChartSchemeCodes = [];
            mutualState.compare = null;
            mutualState.compareLoading = false;
            mutualState.compareError = null;
            mutualState.multiCompare = null;
            mutualState.multiCompareLoading = false;
            mutualState.multiCompareError = null;
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
        mutualState.customStartDate = opts.customStartDate || mutualState.customStartDate || prefs.customStartDate || null;
        mutualState.customEndDate = opts.customEndDate || mutualState.customEndDate || prefs.customEndDate || null;
        if (mutualState.selectedRange === "custom" && (!mutualState.customStartDate || !mutualState.customEndDate)) {
            mutualState.selectedRange = "max";
        } else if (mutualState.selectedRange !== "custom" && !rangeOptions.some(function (item) { return item.key === mutualState.selectedRange; })) {
            mutualState.selectedRange = "max";
        }
        var requestedChartCodes = opts.chartSchemeCodes || mutualState.selectedChartSchemeCodes || prefs.chartSchemeCodes || [];
        if (!Array.isArray(requestedChartCodes)) requestedChartCodes = [];
        var validCodes = new Set((mutualState.watchlist || []).map(function (item) {
            return String(item.scheme_code || "").trim();
        }));
        var seenChartCodes = new Set();
        mutualState.selectedChartSchemeCodes = requestedChartCodes.filter(function (code) {
            var key = String(code || "").trim();
            if (!key || !validCodes.has(key) || seenChartCodes.has(key)) return false;
            seenChartCodes.add(key);
            return true;
        });
        mutualState.compareError = null;
        saveMutualSelectionPrefs();
        renderMutualPage();

        if (opts.loadChart !== false && currentChartCodes().length) {
            scheduleDeferredMutualChartLoad(!!opts.forceChart);
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
        var selectedCodes = currentChartCodes();
        if (!fund || selectedCodes.length !== 1 || selectedCodes[0] !== String(fund.scheme_code || "")) return null;
        var benchmark = opts.benchmark || mutualState.selectedBenchmark || (fund.benchmark_options && fund.benchmark_options[0]) || "NIFTY 500";
        var rangeKey = opts.range || mutualState.selectedRange || "max";
        var customStart = rangeKey === "custom" ? mutualState.customStartDate : "";
        var customEnd = rangeKey === "custom" ? mutualState.customEndDate : "";
        if (rangeKey === "custom" && (!customStart || !customEnd)) {
            mutualState.customRangeError = "Select both dates";
            renderMutualPage();
            return null;
        }
        if (!opts.force && mutualState.compare && mutualState.compare.fund && mutualState.compare.fund.scheme_code === fund.scheme_code && mutualState.compare.benchmark === benchmark && mutualCustomRangeMatches(mutualState.compare, rangeKey, customStart, customEnd)) {
            return mutualState.compare;
        }
        var cacheKey = compareBaseCacheKey(fund.scheme_code, benchmark, rangeKey, customStart, customEnd);
        var cachedCompare = mutualCompareBaseCache.get(cacheKey);
        if (cachedCompare) {
            cancelMultiCompare();
            mutualState.multiCompare = null;
            mutualState.multiCompareLoading = false;
            mutualState.multiCompareError = null;
            mutualState.compare = cachedCompare;
            mutualState.compareLoading = false;
            mutualState.compareError = null;
            mutualState.selectedBenchmark = cachedCompare.benchmark || benchmark;
            mutualState.selectedRange = cachedCompare.range || rangeKey;
            saveMutualSelectionPrefs();
            renderMutualPage();
            return cachedCompare;
        }
        cancelMultiCompare();
        mutualState.multiCompare = null;
        mutualState.multiCompareLoading = false;
        mutualState.multiCompareError = null;
        mutualState.compareLoading = true;
        mutualState.compareError = null;
        renderMutualPage();
        var requestSeq = ++mutualCompareRequestSeq;
        cancelSingleCompare();
        mutualCompareAbortController = typeof AbortController !== "undefined" ? new AbortController() : null;
        var compareController = mutualCompareAbortController;
        mutualCompareLoadPromise = (async function () {
            try {
                var data = await fetchJson(
                    "/api/mf/compare/" + encodeURIComponent(String(fund.scheme_code || "")) +
                    "?benchmark=" + encodeURIComponent(benchmark) +
                    mutualRangeQuery(rangeKey, customStart, customEnd),
                    { timeoutMs: 45000, signal: compareController ? compareController.signal : null }
                );
                if (compareController && compareController.signal.aborted) return null;
                if (requestSeq !== mutualCompareRequestSeq) return data;
                data = prepareMutualComparePayload(data);
                mutualCompareBaseCache.set(cacheKey, data);
                mutualState.compare = data;
                mutualState.compareLoading = false;
                mutualState.selectedBenchmark = data.benchmark || benchmark;
                mutualState.selectedRange = data.range || rangeKey;
                if (data.range === "custom") {
                    mutualState.customStartDate = data.requested_from_date || customStart;
                    mutualState.customEndDate = data.requested_to_date || customEnd;
                    mutualState.customRangeError = null;
                }
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

    async function loadMutualPerformance(opts) {
        opts = opts || {};
        var selectedCodes = currentChartCodes();
        if (selectedCodes.length < 2) return null;
        var rangeKey = opts.range || mutualState.selectedRange || "max";
        var customStart = rangeKey === "custom" ? mutualState.customStartDate : "";
        var customEnd = rangeKey === "custom" ? mutualState.customEndDate : "";
        if (rangeKey === "custom" && (!customStart || !customEnd)) {
            mutualState.customRangeError = "Select both dates";
            renderMutualPage();
            return null;
        }
        if (!opts.force && mutualState.multiCompare && mutualCustomRangeMatches(mutualState.multiCompare, rangeKey, customStart, customEnd)) {
            var existingCodes = (mutualState.multiCompare.items || [])
                .filter(function (item) { return item.kind !== "benchmark"; })
                .map(function (item) { return item.scheme_code; })
                .sort()
                .join(",");
            var requestedCodes = selectedCodes.slice().sort().join(",");
            if (existingCodes === requestedCodes) return mutualState.multiCompare;
        }
        var cacheKey = performanceBaseCacheKey(selectedCodes, rangeKey, customStart, customEnd);
        var cachedPayload = mutualPerformanceBaseCache.get(cacheKey);
        if (cachedPayload) {
            cancelSingleCompare();
            mutualState.compareLoading = false;
            mutualState.compareError = null;
            mutualState.multiCompare = cachedPayload;
            mutualState.multiCompareLoading = false;
            mutualState.multiCompareError = null;
            mutualState.selectedRange = cachedPayload.range || rangeKey;
            saveMutualSelectionPrefs();
            renderMutualPage();
            return cachedPayload;
        }

        cancelSingleCompare();
        mutualState.compareLoading = false;
        mutualState.compareError = null;
        mutualState.multiCompareLoading = true;
        mutualState.multiCompareError = null;
        renderMutualPage();

        var requestSeq = ++mutualPerformanceRequestSeq;
        cancelMultiCompare();
        mutualPerformanceAbortController = typeof AbortController !== "undefined" ? new AbortController() : null;
        var perfController = mutualPerformanceAbortController;
        mutualPerformanceLoadPromise = (async function () {
            try {
                var data = await fetchJson(
                    "/api/mf/performance?scheme_codes=" + encodeURIComponent(selectedCodes.join(",")) +
                    mutualRangeQuery(rangeKey, customStart, customEnd),
                    { timeoutMs: 45000, signal: perfController ? perfController.signal : null }
                );
                if (perfController && perfController.signal.aborted) return null;
                if (requestSeq !== mutualPerformanceRequestSeq) return data;
                mutualPerformanceBaseCache.set(cacheKey, data);
                mutualState.multiCompare = data;
                mutualState.multiCompareLoading = false;
                mutualState.selectedRange = data.range || rangeKey;
                if (data.range === "custom") {
                    mutualState.customStartDate = data.requested_from_date || customStart;
                    mutualState.customEndDate = data.requested_to_date || customEnd;
                    mutualState.customRangeError = null;
                }
                saveMutualSelectionPrefs();
                renderMutualPage();
                return data;
            } catch (err) {
                if (perfController && perfController.signal.aborted) return null;
                if (requestSeq !== mutualPerformanceRequestSeq) return null;
                console.error("Mutual performance fetch error:", err);
                mutualState.multiCompareLoading = false;
                mutualState.multiCompareError = err.message || "Unable to build multi-fund chart";
                renderMutualPage();
                return null;
            } finally {
                if (mutualPerformanceAbortController === perfController) {
                    mutualPerformanceAbortController = null;
                }
                if (requestSeq === mutualPerformanceRequestSeq) {
                    mutualPerformanceLoadPromise = null;
                }
            }
        })();
        return mutualPerformanceLoadPromise;
    }

    function loadActiveMutualChart(opts) {
        opts = opts || {};
        var selectedCodes = currentChartCodes();
        if (!selectedCodes.length) {
            renderMutualPage();
            return null;
        }
        if (selectedCodes.length > 1) {
            return loadMutualPerformance(opts);
        }
        return loadMutualComparison(opts);
    }

    function toggleMutualChartSelection(schemeCode, checked) {
        var code = String(schemeCode || "").trim();
        var codes = currentChartCodes();
        if (checked) {
            if (codes.indexOf(code) === -1) codes.push(code);
        } else {
            codes = codes.filter(function (item) { return item !== code; });
        }
        mutualState.selectedChartSchemeCodes = codes;
        if (codes.length === 1) {
            mutualState.selectedSchemeCode = codes[0];
        } else if (codes.length > 1 && codes.indexOf(String(mutualState.selectedSchemeCode || "")) === -1) {
            mutualState.selectedSchemeCode = codes[0];
        }
        saveMutualSelectionPrefs();
        renderMutualPage();
        if (!codes.length) {
            cancelSingleCompare();
            cancelMultiCompare();
            return;
        }
        loadActiveMutualChart({ force: true });
    }

    function selectAllMutualChartFunds() {
        mutualState.selectedChartSchemeCodes = (mutualState.watchlist || []).map(function (item) {
            return String(item.scheme_code || "").trim();
        }).filter(Boolean);
        if (mutualState.selectedChartSchemeCodes.length) {
            mutualState.selectedSchemeCode = mutualState.selectedChartSchemeCodes[0];
        }
        saveMutualSelectionPrefs();
        renderMutualPage();
        loadActiveMutualChart({ force: true });
    }

    function clearAllMutualChartFunds() {
        mutualState.selectedChartSchemeCodes = [];
        saveMutualSelectionPrefs();
        cancelSingleCompare();
        cancelMultiCompare();
        renderMutualPage();
    }

    async function addMutualFund(schemeCode) {
        if (!schemeCode) return null;
        try {
            var data = await fetchJson("/api/mf/watchlist/" + encodeURIComponent(String(schemeCode)), {
                method: "PUT",
                timeoutMs: 20000,
            });
            applyMutualWatchlist(data, {
                schemeCode: String(schemeCode),
                chartSchemeCodes: [],
                forceChart: false,
                loadChart: false,
            });
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
            var chartCodes = currentChartCodes().filter(function (code) {
                return code !== String(schemeCode || "");
            });
            var data = await fetchJson("/api/mf/watchlist/" + encodeURIComponent(String(schemeCode)), {
                method: "DELETE",
                timeoutMs: 20000,
            });
            applyMutualWatchlist(data, {
                schemeCode: selectedCode === schemeCode ? null : selectedCode,
                chartSchemeCodes: chartCodes,
                loadChart: false,
            });
            if (chartCodes.length) scheduleDeferredMutualChartLoad(false);
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

    if ($mfSelectAll) {
        $mfSelectAll.addEventListener("change", function () {
            if ($mfSelectAll.checked) {
                selectAllMutualChartFunds();
            } else {
                clearAllMutualChartFunds();
            }
        });
    }

    if ($mfClearAll) {
        $mfClearAll.addEventListener("click", function () {
            clearAllMutualChartFunds();
        });
    }

    function applyMutualCalendarRange() {
        if (!$mfDateFrom || !$mfDateTo) return;
        var startDate = $mfDateFrom.value;
        var endDate = $mfDateTo.value;
        if (!startDate || !endDate) {
            mutualState.customRangeError = "Select both dates";
            renderMutualPage();
            return;
        }
        if (startDate >= endDate) {
            mutualState.customRangeError = "Start must be before end";
            renderMutualPage();
            return;
        }
        if (endDate > localTodayDateValue()) {
            mutualState.customRangeError = "End date cannot be in the future";
            renderMutualPage();
            return;
        }
        mutualState.customStartDate = startDate;
        mutualState.customEndDate = endDate;
        mutualState.customRangeError = null;
        setMutualSelection(mutualState.selectedSchemeCode, {
            benchmark: mutualState.selectedBenchmark,
            range: "custom",
            loadChart: true,
            forceChart: true,
            autoInclude: false,
        });
    }

    if ($mfApplyDates) {
        $mfApplyDates.addEventListener("click", applyMutualCalendarRange);
    }

    [$mfDateFrom, $mfDateTo].forEach(function (input) {
        if (!input) return;
        input.addEventListener("change", function () {
            mutualState.customRangeError = null;
            if ($mfDateError) $mfDateError.textContent = "";
        });
        input.addEventListener("keydown", function (event) {
            if (event.key === "Enter") applyMutualCalendarRange();
        });
    });

    if ($mfClearDates) {
        $mfClearDates.addEventListener("click", function () {
            mutualState.customStartDate = null;
            mutualState.customEndDate = null;
            mutualState.customRangeError = null;
            setMutualSelection(mutualState.selectedSchemeCode, {
                benchmark: mutualState.selectedBenchmark,
                range: "max",
                loadChart: true,
                forceChart: true,
                autoInclude: false,
            });
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
                mode: LightweightCharts.CrosshairMode.Magnet,
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
        if ($fiiChartBox && currentView === "fii" && fiiChartRows.length) {
            clearTimeout(fiiChartResizeTimer);
            fiiChartResizeTimer = setTimeout(function () {
                fiiChartRenderKey = "";
                renderFiiChart(fiiChartRows);
            }, 90);
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
                            return n.breaking && !n.stock_event && !n.company_specific;
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
            if (currentView === "fii") await loadFiiPanel();
            if (currentView === "options") await fetchOptionChain();
        } catch (err) { console.error("Initial load error:", err); }
    }

    // ── View Switching (INVESTING / STRATEGY / GLOBAL / MUTUAL / FII) ──

    var $terminal = document.querySelector(".terminal");
    var navTabs = document.querySelectorAll(".nav-tab[data-view]");

    navTabs.forEach(function (b) {
        b.classList.toggle("active", b.dataset.view === currentView);
    });
    $terminal.setAttribute("data-view", currentView);

    navTabs.forEach(function (btn) {
        btn.addEventListener("click", function () {
            var view = btn.dataset.view;
            if (view === currentView) return;
            currentView = view;
            navTabs.forEach(function (b) { b.classList.toggle("active", b.dataset.view === view); });
            $terminal.setAttribute("data-view", view);
            if (window.history && window.history.replaceState && window.location.pathname !== "/") {
                window.history.replaceState({ view: view }, "", "/");
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
            if (view === "fii") {
                if (panelState.fii) renderFiiFlows(panelState.fii);
                loadFiiPanel();
                setTimeout(handleResize, 60);
            }
            if (view === "options") {
                fetchOptionChain();
                setTimeout(renderStrategyBuilder, 80);
            }
        });
    });

    document.querySelectorAll(".fii-range").forEach(function (btn) {
        btn.addEventListener("click", function () {
            selectedFiiRange = btn.dataset.range || "1m";
            document.querySelectorAll(".fii-range").forEach(function (item) {
                item.classList.toggle("is-active", item === btn);
            });
            loadFiiPanel(selectedFiiRange);
        });
    });
    document.querySelectorAll("[data-fii-series]").forEach(function (input) {
        input.addEventListener("change", function () {
            fiiSeriesVisibility[input.dataset.fiiSeries] = input.checked;
            fiiChartRenderKey = "";
            renderFiiChart(fiiChartRows);
        });
    });

    // ── Option Strategy Builder ─────────────────────────────────────────

    var ocSymbol = "NIFTY";
    var ocExpiry = "";
    var ocChainData = null;
    var strategyLegs = [];
    var strategyLegSeq = 1;
    var ocDefaultLotSizes = { NIFTY: 65, BANKNIFTY: 30, FINNIFTY: 60, MIDCPNIFTY: 120, NIFTYNXT50: 25 };
    var $ocTbody = $("oc-tbody");
    var $ocExpiry = $("oc-expiry");
    var $ocSpot = document.querySelector("#oc-spot .oc-badge-val");
    var $ocPCR = document.querySelector("#oc-pcr .oc-badge-val");
    var $ocMaxPain = document.querySelector("#oc-maxpain .oc-badge-val");
    var $ocTotalOI = document.querySelector("#oc-total-oi .oc-badge-val");
    var $ocTimestamp = $("oc-timestamp");
    var $ocStockInput = $("oc-stock-input");
    var $osLegType = $("os-leg-type");
    var $osDistance = $("os-distance");
    var $osDistanceMode = $("os-distance-mode");
    var $osLots = $("os-lots");
    var $osAddLeg = $("os-add-leg");
    var $osResetStrategy = $("os-reset-strategy");
    var $osPickedStrike = $("os-picked-strike");
    var $osLegsBody = $("os-legs-body");
    var $osPremium = $("os-premium");
    var $osPremiumLabel = $("os-premium-label");
    var $osMargin = $("os-margin");
    var $osMaxProfit = $("os-max-profit");
    var $osMaxLoss = $("os-max-loss");
    var $osRR = $("os-rr");
    var $osBreakeven = $("os-breakeven");
    var $osPayoffTitle = $("os-payoff-title");
    var $osPayoffChart = $("os-payoff-chart");
    var $osScanNow = $("os-scan-now");
    var $osOpportunityTitle = $("os-opportunity-title");
    var $osOpportunityAlert = $("os-opportunity-alert");
    var $osOpportunityList = $("os-opportunity-list");
    var $osOpportunityUpdated = $("os-opportunity-updated");
    var opportunitySymbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "NIFTYNXT50"];
    var opportunityScanTimer = null;
    var opportunityScanInFlight = false;
    var opportunityLastScanAt = 0;
    var opportunityLastRows = [];

    function fmtOI(n) {
        if (!n) return "\u2014";
        if (n >= 10000000) return (n / 10000000).toFixed(1) + "Cr";
        if (n >= 100000) return (n / 100000).toFixed(1) + "L";
        if (n >= 1000) return (n / 1000).toFixed(1) + "K";
        return String(n);
    }

    function fmtMoney(n) {
        if (n == null || !isFinite(Number(n))) return "\u2014";
        var abs = Math.abs(Number(n));
        var sign = Number(n) < 0 ? "-" : "";
        if (abs >= 10000000) return sign + "\u20b9" + (abs / 10000000).toFixed(2) + "Cr";
        if (abs >= 100000) return sign + "\u20b9" + (abs / 100000).toFixed(2) + "L";
        return sign + "\u20b9" + abs.toLocaleString("en-IN", { maximumFractionDigits: 0 });
    }

    function optionLotSize() {
        return (ocChainData && Number(ocChainData.lot_size)) || ocDefaultLotSizes[ocSymbol] || 1;
    }

    function updateOptionLotSize() {
        return optionLotSize();
    }

    function optionStrikeRows(chainData) {
        var data = chainData || ocChainData;
        return (data && Array.isArray(data.strikes)) ? data.strikes : [];
    }

    function nearestOptionStrike(target, chainData) {
        var rows = optionStrikeRows(chainData);
        if (!rows.length) return null;
        return rows.reduce(function (best, row) {
            if (!best) return row;
            return Math.abs(Number(row.strike) - target) < Math.abs(Number(best.strike) - target) ? row : best;
        }, null);
    }

    function selectedTargetStrikeRow() {
        var spot = Number(ocChainData && ocChainData.spot) || 0;
        if (!spot) return null;
        var rawDistance = Math.max(0, Number($osDistance && $osDistance.value) || 0);
        var distance = ($osDistanceMode && $osDistanceMode.value) === "percent" ? spot * rawDistance / 100 : rawDistance;
        var type = ($osLegType && $osLegType.value) || "CE";
        var target = type === "PE" ? spot - distance : spot + distance;
        return nearestOptionStrike(target);
    }

    function optionQuoteFor(strike, type, chainData) {
        var row = optionStrikeRows(chainData).find(function (item) { return Number(item.strike) === Number(strike); });
        if (!row) return null;
        return type === "PE" ? row.pe : row.ce;
    }

    function currentLegPrice(leg, chainData) {
        var quote = optionQuoteFor(leg.strike, leg.type, chainData);
        return quote && Number(quote.ltp) ? Number(quote.ltp) : Number(leg.price || 0);
    }

    function optionLotSizeFor(symbol, chainData) {
        return (chainData && Number(chainData.lot_size)) || ocDefaultLotSizes[symbol] || 1;
    }

    function updatePickedStrikePreview() {
        if (!$osPickedStrike) return;
        var row = selectedTargetStrikeRow();
        if (!row) {
            $osPickedStrike.textContent = "Waiting for live option chain...";
            return;
        }
        var type = ($osLegType && $osLegType.value) || "CE";
        var quote = type === "PE" ? row.pe : row.ce;
        $osPickedStrike.textContent =
            "Nearest " + type + " strike: " + Number(row.strike).toLocaleString("en-IN") +
            " | LTP " + (quote && quote.ltp ? fmtPrice(quote.ltp) : "\u2014") +
            " | Expiry " + (ocExpiry || (ocChainData && ocChainData.selected_expiry) || "\u2014");
    }

    function addStrategyLeg(type, strike, lots) {
        var quote = optionQuoteFor(strike, type);
        var price = quote && Number(quote.ltp) ? Number(quote.ltp) : 0;
        var baseSpot = Number(ocChainData && ocChainData.spot) || 0;
        var signedPct = baseSpot ? (Number(strike) - baseSpot) / baseSpot : 0;
        strategyLegs.push({
            id: strategyLegSeq++,
            side: "S",
            type: type || "CE",
            strike: Number(strike),
            lots: Math.max(1, Math.round(Number(lots) || 1)),
            price: price,
            expiry: ocExpiry || (ocChainData && ocChainData.selected_expiry) || "",
            baseSpot: baseSpot,
            targetPct: signedPct,
            distanceMode: ($osDistanceMode && $osDistanceMode.value) || "percent",
            distanceValue: Math.abs(signedPct * 100),
        });
        renderStrategyBuilder();
        scheduleOpportunityScan(500);
    }

    function legPayoffAt(leg, underlying, lotSize, chainData) {
        var lots = Math.max(1, Number(leg.lots) || 1);
        var multiplier = lots * lotSize;
        var price = currentLegPrice(leg, chainData);
        var intrinsic = leg.type === "CE"
            ? Math.max(0, underlying - Number(leg.strike))
            : Math.max(0, Number(leg.strike) - underlying);
        var unit = leg.side === "B" ? intrinsic - price : price - intrinsic;
        return unit * multiplier;
    }

    function optionPayoffMetricsFor(legs, chainData, lotSize) {
        legs = legs || [];
        var spot = Number(chainData && chainData.spot) || 0;
        if (!legs.length || !spot) {
            return {
                lotSize: lotSize, premium: 0, points: [], maxProfit: null, maxLoss: null,
                maxProfitUnlimited: false, maxLossUnlimited: false, margin: 0, breakevens: [],
            };
        }
        var strikes = legs.map(function (leg) { return Number(leg.strike); }).filter(isFinite);
        var chainStrikes = optionStrikeRows(chainData).map(function (row) { return Number(row.strike); }).filter(isFinite);
        var minStrike = Math.min.apply(null, strikes.concat([spot]));
        var maxStrike = Math.max.apply(null, strikes.concat([spot]));
        var step = Number(chainData && chainData.strike_step) || 50;
        var span = Math.max(step * 12, Math.abs(maxStrike - minStrike) * 1.8, spot * 0.08);
        var low = Math.max(0, Math.min(minStrike, spot) - span);
        var high = Math.max(maxStrike, spot) + span;
        if (chainStrikes.length) {
            low = Math.max(0, Math.min(low, Math.min.apply(null, chainStrikes)));
            high = Math.max(high, Math.max.apply(null, chainStrikes));
        }
        var points = [];
        var count = 90;
        for (var i = 0; i <= count; i++) {
            var s = low + (high - low) * i / count;
            var pnl = legs.reduce(function (sum, leg) {
                return sum + legPayoffAt(leg, s, lotSize, chainData);
            }, 0);
            points.push({ underlying: s, pnl: pnl });
        }
        var premium = legs.reduce(function (sum, leg) {
            var signed = leg.side === "B" ? 1 : -1;
            return sum + signed * currentLegPrice(leg, chainData) * Math.max(1, Number(leg.lots) || 1) * lotSize;
        }, 0);
        var riskPoints = points.slice();
        if (legs.some(function (leg) { return leg.type === "PE"; })) {
            riskPoints.push({
                underlying: 0,
                pnl: legs.reduce(function (sum, leg) {
                    return sum + legPayoffAt(leg, 0, lotSize, chainData);
                }, 0),
            });
        }
        var maxP = Math.max.apply(null, riskPoints.map(function (p) { return p.pnl; }));
        var minP = Math.min.apply(null, riskPoints.map(function (p) { return p.pnl; }));
        var highSlopeLots = legs.reduce(function (sum, leg) {
            if (leg.type !== "CE") return sum;
            return sum + (leg.side === "B" ? 1 : -1) * Math.max(1, Number(leg.lots) || 1) * lotSize;
        }, 0);
        var maxProfitUnlimited = highSlopeLots > 0;
        var maxLossUnlimited = highSlopeLots < 0;
        var breakevens = [];
        for (var j = 1; j < points.length; j++) {
            var prev = points[j - 1];
            var cur = points[j];
            if ((prev.pnl <= 0 && cur.pnl >= 0) || (prev.pnl >= 0 && cur.pnl <= 0)) {
                var denom = cur.pnl - prev.pnl;
                var ratio = denom === 0 ? 0 : (0 - prev.pnl) / denom;
                var be = prev.underlying + (cur.underlying - prev.underlying) * ratio;
                if (breakevens.every(function (existing) { return Math.abs(existing - be) > step / 2; })) {
                    breakevens.push(be);
                }
            }
        }
        var finiteRisk = !maxLossUnlimited ? Math.max(0, -minP) : 0;
        var hasShort = legs.some(function (leg) { return leg.side === "S"; });
        var nakedShortEstimate = legs.reduce(function (sum, leg) {
            if (leg.side !== "S") return sum;
            var lots = Math.max(1, Number(leg.lots) || 1);
            return sum + (spot * lotSize * lots * 0.12) + (currentLegPrice(leg, chainData) * lotSize * lots);
        }, 0);
        var margin = hasShort
            ? (maxLossUnlimited ? nakedShortEstimate + Math.max(0, premium) : finiteRisk)
            : Math.max(0, premium);
        return {
            lotSize: lotSize,
            premium: premium,
            points: points,
            maxProfit: maxP,
            maxLoss: minP,
            maxProfitUnlimited: maxProfitUnlimited,
            maxLossUnlimited: maxLossUnlimited,
            margin: margin,
            breakevens: breakevens,
            spot: spot,
        };
    }

    function optionPayoffMetrics() {
        return optionPayoffMetricsFor(strategyLegs, ocChainData, optionLotSize());
    }

    function drawPayoffChart(metrics) {
        if (!$osPayoffChart) return;
        var canvas = $osPayoffChart;
        var rect = canvas.getBoundingClientRect();
        var dpr = window.devicePixelRatio || 1;
        var width = Math.max(320, Math.floor(rect.width || 640));
        var height = Math.max(220, Math.floor(rect.height || 260));
        canvas.width = Math.floor(width * dpr);
        canvas.height = Math.floor(height * dpr);
        var ctx = canvas.getContext("2d");
        ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
        ctx.clearRect(0, 0, width, height);
        ctx.fillStyle = "rgba(5,9,16,.42)";
        ctx.fillRect(0, 0, width, height);
        var pad = { left: 54, right: 18, top: 22, bottom: 34 };
        var pts = metrics.points || [];
        if (!pts.length) {
            ctx.fillStyle = "#4a5568";
            ctx.font = "12px IBM Plex Mono, monospace";
            ctx.textAlign = "center";
            ctx.fillText("Add strategy legs to draw payoff", width / 2, height / 2);
            return;
        }
        var minX = Math.min.apply(null, pts.map(function (p) { return p.underlying; }));
        var maxX = Math.max.apply(null, pts.map(function (p) { return p.underlying; }));
        var maxAbsY = Math.max(1, Math.max.apply(null, pts.map(function (p) { return Math.abs(p.pnl); })));
        var plotW = width - pad.left - pad.right;
        var plotH = height - pad.top - pad.bottom;
        function x(v) { return pad.left + ((v - minX) / Math.max(1, maxX - minX)) * plotW; }
        function y(v) { return pad.top + (maxAbsY - v) / (maxAbsY * 2) * plotH; }
        var zeroY = y(0);
        ctx.strokeStyle = "rgba(148,163,184,.16)";
        ctx.lineWidth = 1;
        for (var g = -2; g <= 2; g++) {
            var gy = y(maxAbsY * g / 2);
            ctx.beginPath(); ctx.moveTo(pad.left, gy); ctx.lineTo(width - pad.right, gy); ctx.stroke();
        }
        ctx.strokeStyle = "rgba(148,163,184,.36)";
        ctx.beginPath(); ctx.moveTo(pad.left, zeroY); ctx.lineTo(width - pad.right, zeroY); ctx.stroke();
        if (metrics.spot) {
            var sx = x(metrics.spot);
            ctx.strokeStyle = "rgba(255,140,0,.7)";
            ctx.beginPath(); ctx.moveTo(sx, pad.top); ctx.lineTo(sx, height - pad.bottom); ctx.stroke();
        }
        ctx.beginPath();
        pts.forEach(function (p, idx) {
            var px = x(p.underlying);
            var py = y(p.pnl);
            if (idx === 0) ctx.moveTo(px, py);
            else ctx.lineTo(px, py);
        });
        ctx.strokeStyle = "#4fd1c5";
        ctx.lineWidth = 2.4;
        ctx.stroke();
        ctx.fillStyle = "#8ea0b8";
        ctx.font = "10px IBM Plex Mono, monospace";
        ctx.textAlign = "right";
        ctx.fillText(fmtMoney(maxAbsY), pad.left - 8, pad.top + 4);
        ctx.fillText("0", pad.left - 8, zeroY + 3);
        ctx.fillText("-" + fmtMoney(maxAbsY).replace(/^-/, ""), pad.left - 8, height - pad.bottom);
        ctx.textAlign = "center";
        ctx.fillText(Math.round(minX).toLocaleString("en-IN"), pad.left, height - 10);
        ctx.fillText(Math.round(maxX).toLocaleString("en-IN"), width - pad.right, height - 10);
    }

    function fmtOpportunityPct(n) {
        if (n == null || !isFinite(Number(n))) return "--";
        return (Number(n) * 100).toFixed(1) + "%";
    }

    function opportunityLegRecipes() {
        var baseSpot = Number(ocChainData && ocChainData.spot) || 0;
        if (!baseSpot || !strategyLegs.length) return [];
        return strategyLegs.map(function (leg) {
            var pct = isFinite(Number(leg.targetPct)) ? Number(leg.targetPct) : ((Number(leg.strike) - baseSpot) / baseSpot);
            return {
                id: leg.id,
                side: leg.side,
                type: leg.type,
                lots: Math.max(1, Number(leg.lots) || 1),
                targetPct: pct,
            };
        });
    }

    function evaluateOpportunity(symbol, chainData, recipes, baseExpiry) {
        if (!chainData || chainData.error || !Array.isArray(chainData.strikes) || !chainData.strikes.length) {
            return { symbol: symbol, ok: false, reason: "No live chain" };
        }
        var expiry = chainData.selected_expiry || "";
        if (baseExpiry && expiry !== baseExpiry) {
            return { symbol: symbol, ok: false, reason: "No same-day expiry", expiry: expiry };
        }
        var spot = Number(chainData.spot) || 0;
        if (!spot) return { symbol: symbol, ok: false, reason: "No spot" };
        var legs = [];
        for (var i = 0; i < recipes.length; i++) {
            var recipe = recipes[i];
            var target = spot * (1 + Number(recipe.targetPct || 0));
            var row = nearestOptionStrike(target, chainData);
            if (!row) return { symbol: symbol, ok: false, reason: "No strike" };
            var quote = recipe.type === "PE" ? row.pe : row.ce;
            if (!quote) return { symbol: symbol, ok: false, reason: "No " + recipe.type };
            legs.push({
                id: recipe.id,
                side: recipe.side,
                type: recipe.type,
                strike: Number(row.strike),
                lots: recipe.lots,
                price: Number(quote.ltp) || 0,
                expiry: expiry,
                baseSpot: spot,
                targetPct: recipe.targetPct,
            });
        }
        var lotSize = optionLotSizeFor(symbol, chainData);
        var metrics = optionPayoffMetricsFor(legs, chainData, lotSize);
        var netCredit = Math.max(0, -(metrics.premium || 0));
        var netDebit = Math.max(0, metrics.premium || 0);
        return {
            symbol: symbol,
            ok: true,
            chainData: chainData,
            expiry: expiry,
            spot: spot,
            lotSize: lotSize,
            legs: legs,
            premium: metrics.premium || 0,
            netCredit: netCredit,
            netDebit: netDebit,
            margin: metrics.margin || 0,
            creditYield: metrics.margin ? netCredit / metrics.margin : 0,
            strikesLabel: legs.map(function (leg) {
                return leg.side + " " + leg.strike.toLocaleString("en-IN") + " " + leg.type + " @" + (leg.price ? leg.price.toFixed(2) : "--");
            }).join(" / "),
        };
    }

    function renderOpportunityScanner(rows, isScanning, errorText) {
        if (!$osOpportunityTitle || !$osOpportunityList || !$osOpportunityAlert) return;
        if (!strategyLegs.length) {
            $osOpportunityTitle.textContent = "Add sell legs to scan alternatives";
            $osOpportunityAlert.className = "os-opportunity-alert";
            $osOpportunityAlert.textContent = "No active strategy yet.";
            $osOpportunityList.innerHTML = '<div class="os-opportunity-empty">Add your morning sell legs. I will compare the same percentage distance across same-day index expiries.</div>';
            if ($osOpportunityUpdated) $osOpportunityUpdated.textContent = "Auto scans while Strategy is visible.";
            return;
        }
        if (isScanning) {
            $osOpportunityTitle.textContent = "Scanning same-day expiries...";
            $osOpportunityAlert.className = "os-opportunity-alert";
            $osOpportunityAlert.textContent = "Checking NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY and NIFTYNXT50.";
            return;
        }
        if (errorText) {
            $osOpportunityTitle.textContent = "Scanner paused";
            $osOpportunityAlert.className = "os-opportunity-alert";
            $osOpportunityAlert.textContent = errorText;
            return;
        }
        var validRows = (rows || []).filter(function (row) { return row.ok; });
        var current = validRows.find(function (row) { return row.symbol === ocSymbol; }) || null;
        if (!validRows.length || !current) {
            $osOpportunityTitle.textContent = "No same-day alternatives found";
            $osOpportunityAlert.className = "os-opportunity-alert";
            $osOpportunityAlert.textContent = "No matching same-day index expiry has a usable chain right now.";
            $osOpportunityList.innerHTML = '<div class="os-opportunity-empty">Try again after the chains refresh.</div>';
            return;
        }
        validRows.sort(function (a, b) { return b.netCredit - a.netCredit; });
        var best = validRows[0];
        var extra = best.netCredit - current.netCredit;
        var alertThreshold = Math.max(100, current.netCredit * 0.05);
        var hot = best.symbol !== current.symbol && extra > alertThreshold;
        $osOpportunityTitle.textContent = hot ? "Better expiry premium found" : "Same-day expiry check";
        $osOpportunityAlert.className = "os-opportunity-alert" + (hot ? " hot" : "");
        $osOpportunityAlert.textContent = hot
            ? "ALERT: " + best.symbol + " gives " + fmtMoney(extra) + " more net credit than " + current.symbol + " for the same % distance."
            : "No better same-day expiry by net credit. Current setup remains competitive.";
        $osOpportunityList.innerHTML = validRows.map(function (row, idx) {
            var diff = row.netCredit - current.netCredit;
            var cls = "os-opportunity-row" + (idx === 0 ? " best" : "") + (row.symbol === current.symbol ? " current" : "");
            var diffCls = diff > 0 ? "positive" : (diff < 0 ? "negative" : "");
            var action = row.symbol === current.symbol
                ? '<span class="os-op-cell"><small>Current</small></span>'
                : '<button class="os-op-load" type="button" data-op-load="' + row.symbol + '">Load</button>';
            return '<div class="' + cls + '">' +
                '<div class="os-op-symbol">' + row.symbol + '<small>' + row.expiry + '</small></div>' +
                '<div class="os-op-strikes">' + row.strikesLabel + '</div>' +
                '<div class="os-op-cell positive">' + fmtMoney(row.netCredit) + '<small>Net credit</small></div>' +
                '<div class="os-op-cell">' + (row.margin ? fmtMoney(row.margin) : "—") + '<small>Est. margin</small></div>' +
                '<div class="os-op-cell ' + diffCls + '">' + (diff === 0 ? "—" : fmtMoney(diff)) + '<small>Vs current</small></div>' +
                action +
                '</div>';
        }).join("");
        if ($osOpportunityUpdated) {
            $osOpportunityUpdated.textContent = "Same % distance scan · updated " + new Date().toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
        }
    }

    function scheduleOpportunityScan(delayMs) {
        if (opportunityScanTimer) clearTimeout(opportunityScanTimer);
        opportunityScanTimer = setTimeout(function () {
            scanSameDayOpportunities(false);
        }, delayMs == null ? 800 : delayMs);
    }

    async function scanSameDayOpportunities(force) {
        if (opportunityScanInFlight) return;
        if (document.hidden || currentView !== "options") return;
        var hasShort = strategyLegs.some(function (leg) { return leg.side === "S"; });
        if (!strategyLegs.length || !hasShort || !ocChainData || !ocChainData.spot) {
            renderOpportunityScanner([], false);
            return;
        }
        if (!force && Date.now() - opportunityLastScanAt < 45000) return;
        var baseExpiry = (ocChainData && ocChainData.selected_expiry) || ocExpiry || "";
        var recipes = opportunityLegRecipes();
        if (!baseExpiry || !recipes.length) {
            renderOpportunityScanner([], false, "Waiting for current expiry and live chain.");
            return;
        }
        opportunityScanInFlight = true;
        renderOpportunityScanner(opportunityLastRows, true);
        try {
            var chains = {};
            chains[ocSymbol] = ocChainData;
            await Promise.all(opportunitySymbols.filter(function (symbol) {
                return symbol !== ocSymbol;
            }).map(async function (symbol) {
                try {
                    chains[symbol] = await fetchJson("/api/options/" + encodeURIComponent(symbol) + "?expiry=" + encodeURIComponent(baseExpiry), { timeoutMs: 18000 });
                } catch (err) {
                    chains[symbol] = { error: err && err.message ? err.message : "Fetch failed", strikes: [] };
                }
            }));
            opportunityLastRows = opportunitySymbols.map(function (symbol) {
                return evaluateOpportunity(symbol, chains[symbol], recipes, baseExpiry);
            });
            opportunityLastScanAt = Date.now();
            renderOpportunityScanner(opportunityLastRows, false);
        } catch (err) {
            renderOpportunityScanner(opportunityLastRows, false, "Scanner error: " + (err && err.message ? err.message : err));
        } finally {
            opportunityScanInFlight = false;
        }
    }

    function loadOpportunityCandidate(symbol) {
        var row = opportunityLastRows.find(function (item) { return item.ok && item.symbol === symbol; });
        if (!row) return;
        ocSymbol = row.symbol;
        ocExpiry = row.expiry;
        ocChainData = row.chainData;
        strategyLegs = row.legs.map(function (leg) {
            return Object.assign({}, leg, {
                id: strategyLegSeq++,
                baseSpot: row.spot,
                expiry: row.expiry,
            });
        });
        document.querySelectorAll(".oc-sym-btn").forEach(function (btn) {
            btn.classList.toggle("active", btn.dataset.ocSym === row.symbol);
        });
        if ($ocStockInput) $ocStockInput.value = opportunitySymbols.indexOf(row.symbol) >= 0 ? "" : row.symbol;
        renderOptionChain(row.chainData);
        renderStrategyBuilder();
        renderOpportunityScanner(opportunityLastRows, false);
        scheduleOpportunityScan(1500);
    }

    function renderStrategyBuilder() {
        updatePickedStrikePreview();
        if (!$osLegsBody) return;
        strategyLegs.forEach(function (leg) {
            leg.price = currentLegPrice(leg);
            leg.expiry = ocExpiry || leg.expiry;
        });
        if (!strategyLegs.length) {
            $osLegsBody.innerHTML = '<tr><td colspan="5" class="os-empty">Add sell legs using distance, or click LTP in the live chain.</td></tr>';
        } else {
            $osLegsBody.innerHTML = strategyLegs.map(function (leg) {
                return '<tr>' +
                    '<td>' + (leg.expiry || "\u2014") + '</td>' +
                    '<td>' + Number(leg.strike).toLocaleString("en-IN") + '</td>' +
                    '<td>' + leg.type + '</td>' +
                    '<td>' + (leg.price ? leg.price.toFixed(2) : "\u2014") + '</td>' +
                    '<td><button class="os-remove-leg" data-remove-leg="' + leg.id + '" type="button">\u00d7</button></td>' +
                    '</tr>';
            }).join("");
        }
        var metrics = optionPayoffMetrics();
        var premium = metrics.premium || 0;
        var premiumLabel = premium > 0 ? "Premium Pay" : "Premium Receive";
        $osPremium.textContent = fmtMoney(Math.abs(premium));
        $osPremium.className = premium > 0 ? "down" : (premium < 0 ? "up" : "");
        $osPremiumLabel.textContent = premiumLabel;
        $osMargin.textContent = metrics.margin ? fmtMoney(metrics.margin) : "\u2014";
        $osMargin.className = metrics.margin ? "warn" : "";
        $osMaxProfit.textContent = metrics.maxProfitUnlimited ? "Unlimited" : (metrics.maxProfit == null ? "\u2014" : fmtMoney(metrics.maxProfit));
        $osMaxProfit.className = metrics.maxProfitUnlimited || metrics.maxProfit > 0 ? "up" : "";
        $osMaxLoss.textContent = metrics.maxLossUnlimited ? "Unlimited" : (metrics.maxLoss == null ? "\u2014" : fmtMoney(metrics.maxLoss));
        $osMaxLoss.className = metrics.maxLossUnlimited || metrics.maxLoss < 0 ? "down" : "";
        var risk = !metrics.maxLossUnlimited && metrics.maxLoss < 0 ? Math.abs(metrics.maxLoss) : null;
        var reward = !metrics.maxProfitUnlimited && metrics.maxProfit > 0 ? metrics.maxProfit : null;
        $osRR.textContent = metrics.maxProfitUnlimited && risk ? "Reward / risk \u221e" : (risk && reward ? "Reward / risk " + (reward / risk).toFixed(2) + "x" : "Reward / risk --");
        $osBreakeven.textContent = metrics.breakevens && metrics.breakevens.length
            ? "Breakeven " + metrics.breakevens.map(function (be) { return Math.round(be).toLocaleString("en-IN"); }).join(", ")
            : "Breakeven --";
        if ($osPayoffTitle) {
            $osPayoffTitle.textContent = strategyLegs.length
                ? strategyLegs.length + " leg strategy on " + ocSymbol
                : "No strategy selected";
        }
        drawPayoffChart(metrics);
        if (!strategyLegs.length || !strategyLegs.some(function (leg) { return leg.side === "S"; })) {
            renderOpportunityScanner([], false);
        }
    }

    async function fetchOptionChain() {
        try {
            var url = "/api/options/" + encodeURIComponent(ocSymbol);
            if (ocExpiry) url += "?expiry=" + encodeURIComponent(ocExpiry);
            var res = await fetch(url);
            var data = await res.json();
            ocChainData = data;
            updateOptionLotSize(!strategyLegs.length);
            renderOptionChain(data);
            renderStrategyBuilder();
            if (strategyLegs.length) scheduleOpportunityScan(700);
        } catch (err) {
            console.error("[OC] Fetch error:", err);
        }
    }

    function renderOptionChain(data) {
        var rows = (data && data.strikes) || [];
        if (data.error && !rows.length) {
            $ocTbody.innerHTML = '<tr><td colspan="3" class="oc-empty">' + (data.error || "No data") + '</td></tr>';
            renderStrategyBuilder();
            return;
        }

        $ocSpot.textContent = data.spot ? Number(data.spot).toLocaleString("en-IN", { maximumFractionDigits: 2 }) : "--";
        $ocPCR.textContent = data.pcr || "--";
        $ocMaxPain.textContent = data.max_pain ? Number(data.max_pain).toLocaleString("en-IN") : "--";
        $ocTotalOI.textContent = fmtOI((data.total_ce_oi || 0) + (data.total_pe_oi || 0));
        $ocTimestamp.textContent = data.timestamp || "--";

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
        var html = "";

        rows.forEach(function (row) {
            var isATM = row.strike === atm;
            var ceITM = spot > row.strike;
            var peITM = spot < row.strike;
            var cls = "oc-row";
            if (isATM) cls += " oc-atm";

            var ce = row.ce || {};
            var pe = row.pe || {};
            var ceLtpCls = (ce.chg || 0) >= 0 ? "up" : "down";
            var peLtpCls = (pe.chg || 0) >= 0 ? "up" : "down";

            html += '<tr class="' + cls + '">';
            html += '<td class="oc-ce oc-ltp' + (ceITM ? " oc-itm" : "") + ' ' + ceLtpCls + '" data-leg-type="CE" data-strike="' + row.strike + '">' + (ce.ltp ? ce.ltp.toFixed(2) : "\u2014") + '</td>';
            html += '<td class="oc-strike">' + Number(row.strike).toLocaleString("en-IN") + '</td>';
            html += '<td class="oc-pe oc-ltp' + (peITM ? " oc-itm" : "") + ' ' + peLtpCls + '" data-leg-type="PE" data-strike="' + row.strike + '">' + (pe.ltp ? pe.ltp.toFixed(2) : "\u2014") + '</td>';
            html += '</tr>';
        });

        $ocTbody.innerHTML = html;

        var atmRow = document.querySelector(".oc-atm");
        if (atmRow && currentView === "options") {
            atmRow.scrollIntoView({ block: "center", behavior: "smooth" });
        }
    }

    document.querySelectorAll(".oc-sym-btn").forEach(function (btn) {
        btn.addEventListener("click", function () {
            document.querySelectorAll(".oc-sym-btn").forEach(function (b) { b.classList.remove("active"); });
            btn.classList.add("active");
            $ocStockInput.value = "";
            ocSymbol = btn.dataset.ocSym;
            ocExpiry = "";
            strategyLegs = [];
            opportunityLastRows = [];
            opportunityLastScanAt = 0;
            fetchOptionChain();
        });
    });

    $ocStockInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && $ocStockInput.value.trim()) {
            document.querySelectorAll(".oc-sym-btn").forEach(function (b) { b.classList.remove("active"); });
            ocSymbol = $ocStockInput.value.trim().toUpperCase();
            ocExpiry = "";
            strategyLegs = [];
            opportunityLastRows = [];
            opportunityLastScanAt = 0;
            fetchOptionChain();
        }
    });

    $ocExpiry.addEventListener("change", function () {
        ocExpiry = $ocExpiry.value;
        strategyLegs = strategyLegs.filter(function (leg) { return leg.expiry === ocExpiry; });
        opportunityLastRows = [];
        opportunityLastScanAt = 0;
        fetchOptionChain();
    });

    [$osLegType, $osDistance, $osDistanceMode, $osLots].forEach(function (node) {
        if (!node) return;
        node.addEventListener("input", renderStrategyBuilder);
        node.addEventListener("change", renderStrategyBuilder);
    });

    if ($osAddLeg) {
        $osAddLeg.addEventListener("click", function () {
            var row = selectedTargetStrikeRow();
            if (!row) return;
            addStrategyLeg(
                ($osLegType && $osLegType.value) || "CE",
                row.strike,
                Number($osLots && $osLots.value) || 1
            );
        });
    }

    if ($osResetStrategy) {
        $osResetStrategy.addEventListener("click", function () {
            strategyLegs = [];
            opportunityLastRows = [];
            opportunityLastScanAt = 0;
            renderStrategyBuilder();
        });
    }

    if ($osLegsBody) {
        $osLegsBody.addEventListener("click", function (event) {
            var btn = event.target.closest("[data-remove-leg]");
            if (!btn) return;
            var id = Number(btn.getAttribute("data-remove-leg"));
            strategyLegs = strategyLegs.filter(function (leg) { return leg.id !== id; });
            renderStrategyBuilder();
            scheduleOpportunityScan(500);
        });
    }

    if ($ocTbody) {
        $ocTbody.addEventListener("click", function (event) {
            var cell = event.target.closest("[data-leg-type][data-strike]");
            if (!cell) return;
            addStrategyLeg(
                cell.getAttribute("data-leg-type"),
                Number(cell.getAttribute("data-strike")),
                Number($osLots && $osLots.value) || 1
            );
        });
    }

    if ($osScanNow) {
        $osScanNow.addEventListener("click", function () {
            opportunityLastScanAt = 0;
            scanSameDayOpportunities(true);
        });
    }

    if ($osOpportunityList) {
        $osOpportunityList.addEventListener("click", function (event) {
            var btn = event.target.closest("[data-op-load]");
            if (!btn) return;
            loadOpportunityCandidate(btn.getAttribute("data-op-load"));
        });
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
        if (currentView === "fii") loadFiiPanel();
        if (currentView === "options") fetchOptionChain();
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
            if (currentView === "fii") loadFiiPanel();
            if (currentView === "options") fetchOptionChain();
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
    }, 30000);
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
    setInterval(function () {
        if (!document.hidden && currentView === "fii") {
            loadFiiPanel();
        }
    }, 15 * 60000);
    setInterval(function () {
        if (!document.hidden && currentView === "options") {
            fetchOptionChain();
        }
    }, 60000);

    // ── Boot ───────────────────────────────────────────────────────────

    initialLoad();

})();

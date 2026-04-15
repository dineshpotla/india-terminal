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
    let stockCache = {};
    const stockFetchInflight = {};
    let watchlistLoadPromise = null;
    let lastWatchlistQuoteRefreshAt = 0;
    let watchlistQuotesLoadPromise = null;
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
        return currentNewsTab === "watchlist" ? "watchlist" : "all";
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
        var controller = null;
        var timer = null;
        if (timeoutMs > 0 && typeof AbortController !== "undefined") {
            controller = new AbortController();
            timer = setTimeout(function () {
                controller.abort();
            }, timeoutMs);
        }
        var res;
        try {
            res = await fetch(url, controller ? { signal: controller.signal } : undefined);
        } finally {
            if (timer) clearTimeout(timer);
        }
        if (!res.ok) throw new Error("request failed: " + url);
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
            if (shouldRefreshNewsPanel("all", 60000)) loadNewsPanel("all");
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

    // ── WebSocket ──────────────────────────────────────────────────────

    function connectWS() {
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
                    if (dashboardData) {
                        dashboardData.news = msg.news;
                        if (msg.news_llm_pending !== undefined) {
                            dashboardData.news_llm_pending = msg.news_llm_pending;
                        }
                        if (msg.news_llm_enabled !== undefined) {
                            dashboardData.news_llm_enabled = msg.news_llm_enabled;
                        }
                    }
                    updateNewsLlmState(msg);
                    renderNews(msg.news, true);
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
            console.log("[WS] Disconnected \u2014 reconnecting in 3s");
            setTimeout(connectWS, 3000);
        };

        ws.onerror = function () { ws.close(); };
    }

    // ── Initial Load ───────────────────────────────────────────────────

    async function initialLoad() {
        try {
            await loadWatchlist();
            loadLocalPanelCaches();
            await loadBootstrap();
            await loadOverviewPanel();
            if (isNewsVisible()) await loadNewsPanel(activeNewsRequestTab());
            if (isWatchlistVisible()) await hydrateWatchlistStocks({ force: true });
            if (currentView === "global") await loadGlobalPanel();
        } catch (err) { console.error("Initial load error:", err); }
    }

    // ── View Switching (INVESTING / OPTIONS) ───────────────────────────

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
        loadWatchlist();
        if (currentView === "investing") loadOverviewPanel();
        if (isWatchlistVisible()) hydrateWatchlistStocks({ staleMs: 30000 });
        if (isNewsVisible()) loadNewsPanel(activeNewsRequestTab());
        if (currentView === "global") loadGlobalPanel();
    });
    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) {
            loadWatchlist();
            if (currentView === "investing") loadOverviewPanel();
            if (isWatchlistVisible()) hydrateWatchlistStocks({ staleMs: 30000 });
            if (isNewsVisible()) loadNewsPanel(activeNewsRequestTab());
            if (currentView === "global") loadGlobalPanel();
        }
    });
    setInterval(function () {
        if (!document.hidden && currentView === "investing") {
            loadOverviewPanel();
        }
    }, 60000);
    setInterval(function () {
        if (!document.hidden && isNewsVisible() && currentNewsTab !== "watchlist") {
            loadNewsPanel("all");
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

    // ── Boot ───────────────────────────────────────────────────────────

    initialLoad();

})();

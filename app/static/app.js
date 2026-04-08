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
    let stockCache = {};
    const stockFetchInflight = {};

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
        var breadthData = dashboardData && dashboardData.breadth;
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
                if (n.watchlist_stocks && n.watchlist_stocks.length) {
                    if (n.watchlist_stocks.some(function (s) { return wlSet[s]; })) return true;
                }
                return false;
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

    function updateGlobalStatus(streaming, lastUpdate) {
        if ($gmStatus) {
            if (streaming) {
                $gmStatus.textContent = "LIVE STREAMING";
                $gmStatus.className = "gm-status streaming";
            } else {
                $gmStatus.textContent = "POLLING";
                $gmStatus.className = "gm-status polling";
            }
        }
        if ($gmUpdated && lastUpdate) {
            $gmUpdated.textContent = "Updated " + lastUpdate;
        }
    }

    function renderGlobalMarkets(futures) {
        if (!futures || !futures.length) return;
        clearChildren($gmGrid);

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
            ["NAME", "LTP", "CHANGE", "CHG%"].forEach(function (h) {
                headerRow.appendChild(el("th", "", h));
            });
            thead.appendChild(headerRow);
            table.appendChild(thead);

            var tbody = el("tbody");
            regionMap[region].forEach(function (f) {
                var tr = el("tr");
                tr.appendChild(el("td", "", f.name));
                var priceTd = el("td", "gm-price", fmtGlobalPrice(f.price));
                tr.appendChild(priceTd);

                var chgSign = f.change >= 0 ? "+" : "";
                var chgTd = el("td", "gm-chg " + cls(f.change_pct),
                    chgSign + f.change.toFixed(2));
                tr.appendChild(chgTd);

                var pctTd = el("td", "gm-pct " + cls(f.change_pct), fmtPct(f.change_pct));
                tr.appendChild(pctTd);

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
        stockCache[sym] = Object.assign({}, stockCache[sym] || {}, stock, { symbol: sym });
    }

    function rememberStocks(stocks) {
        (stocks || []).forEach(rememberStock);
    }

    function findKnownStock(symbol) {
        var sym = String(symbol || "").toUpperCase();
        if (dashboardData && dashboardData.stocks) {
            var live = dashboardData.stocks.find(function (s) { return s.symbol === sym; });
            if (live) return live;
        }
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
            } finally {
                delete stockFetchInflight[sym];
            }
        })();
        return stockFetchInflight[sym];
    }

    async function hydrateWatchlistStocks() {
        var missing = watchlist.filter(function (sym) { return !findKnownStock(sym); });
        if (!missing.length) return;
        await Promise.all(missing.slice(0, 6).map(fetchStockDetail));
        renderWatchlistTable();
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
            if (dashboardData) renderNews(dashboardData.news);
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

    function loadWatchlistLocal() {
        try {
            var saved = localStorage.getItem("imt_watchlist");
            return normalizeWatchlist(saved ? JSON.parse(saved) : []);
        } catch (e) { return []; }
    }

    function saveWatchlist() {
        try { localStorage.setItem("imt_watchlist", JSON.stringify(watchlist)); } catch (e) {}
    }

    function applyWatchlist(symbols) {
        watchlist = normalizeWatchlist(symbols);
        saveWatchlist();
        renderWatchlistTable();
        hydrateWatchlistStocks();
        if (currentNewsTab === "watchlist" && dashboardData) renderNews(dashboardData.news);
        if (ws && ws.readyState === WebSocket.OPEN && watchlist.length) {
            watchlist.forEach(function (sym) { ws.send("watchlist:" + sym); });
        }
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

    async function loadWatchlist() {
        var local = loadWatchlistLocal();
        try {
            var remoteState = await fetchWatchlistRemote();
            var remote = normalizeWatchlist(remoteState.symbols || []);
            var initialized = !!remoteState.initialized;
            if (!initialized && local.length) {
                remote = await syncWatchlistRemote(local);
            }
            applyWatchlist(initialized ? remote : (remote.length ? remote : local));
        } catch (err) {
            console.warn("[Watchlist] Falling back to local storage:", err);
            applyWatchlist(local);
        }
    }

    async function addToWatchlist(sym) {
        if (watchlist.indexOf(sym) !== -1) return;
        var prev = watchlist.slice();
        applyWatchlist(watchlist.concat([sym]));
        if (ws && ws.readyState === WebSocket.OPEN) {
            ws.send("watchlist:" + sym);
        }
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
        dashboardData = data;
        rememberStocks(data.stocks);

        var st = data.market_status || "CLOSED";
        $status.textContent = st;
        $status.className = "market-badge" + (st === "LIVE" ? " live" : "");

        if (data.last_update) $lastUpdate.textContent = "Updated " + data.last_update;

        renderGiftNifty(data.gift_nifty);
        renderIndices(data.indices);
        renderMovers(data.movers);
        renderNews(data.news, isLive);
        renderSectors(data.sectors);
        renderGlobalMarkets(data.global_futures);
        updateGlobalStatus(data.global_streaming, data.last_global_update);
        renderWatchlistTable();

        if (selectedStock && data.stocks) {
            var stock = data.stocks.find(function (s) { return s.symbol === selectedStock; });
            if (stock) renderStockDetail(stock);
        }

        handleResize();
    }

    // ── WebSocket ──────────────────────────────────────────────────────

    function connectWS() {
        var proto = location.protocol === "https:" ? "wss" : "ws";
        ws = new WebSocket(proto + "://" + location.host + "/ws");

        ws.onopen = function () {
            console.log("[WS] Connected");
            if (watchlist.length) {
                watchlist.forEach(function (sym) { ws.send("watchlist:" + sym); });
            }
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
                    updateGlobalStatus(msg.global_streaming, msg.last_global_update);
                    if (currentView === "global") renderGlobalMarkets(msg.global_futures);
                } else if (msg.type === "news" && msg.news) {
                    if (dashboardData) dashboardData.news = msg.news;
                    renderNews(msg.news, true);
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
            var res = await fetch("/api/dashboard");
            var data = await res.json();
            renderDashboard(data);
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
            if (view === "global" && dashboardData) {
                renderGlobalMarkets(dashboardData.global_futures);
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
        });
    });

    isMobile.addEventListener("change", handleMobileInit);
    handleMobileInit();

    // ── Boot ───────────────────────────────────────────────────────────

    initialLoad();
    connectWS();

})();

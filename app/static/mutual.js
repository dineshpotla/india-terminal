(function () {
    "use strict";

    let mfChart = null;
    let mfFundSeries = null;
    let mfBenchmarkSeries = null;
    let mutualChartRenderKey = "";
    let mutualHoldingsLoadPromise = null;
    let mutualCompareLoadPromise = null;
    let mutualCompareRequestSeq = 0;
    let mutualCompareAbortController = null;
    let mutualSearchAbortController = null;
    let mutualSearchResults = [];
    let mutualPollTimer = null;
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

    const $ = (id) => document.getElementById(id);
    const $mfStatus = $("mf-status");
    const $mfInput = $("mf-input");
    const $mfSuggest = $("mf-suggest");
    const $mfHoldings = $("mf-holdings");
    const $mfDetail = $("mf-detail");
    const $mfHero = $("mf-hero");
    const $mfBenchmarkBlock = $("mf-benchmark-block");
    const $mfBenchmarks = $("mf-benchmarks");
    const $mfRangeBlock = $("mf-range-block");
    const $mfRanges = $("mf-ranges");
    const $mfStats = $("mf-stats");
    const $mfChartShell = $("mf-chart-shell");
    const $mfChartTitle = $("mf-chart-title");
    const $mfChartNote = $("mf-chart-note");
    const $mfChartBox = $("mf-chart");
    const $mfPageUpdated = $("mf-page-updated");

    function el(tag, cls, text) {
        const e = document.createElement(tag);
        if (cls) e.className = cls;
        if (text !== undefined) e.textContent = text;
        return e;
    }

    function clearChildren(node) {
        while (node && node.firstChild) node.removeChild(node.firstChild);
    }

    function fmtPrice(n) {
        if (n == null) return "\u2014";
        return "\u20b9" + Number(n).toLocaleString("en-IN", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    function fmtPct(n) {
        if (n == null) return "\u2014";
        return (n >= 0 ? "+" : "") + Number(n).toFixed(2) + "%";
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
            var res = await fetch(url, fetchOpts);
            if (!res.ok) {
                var message = "request failed: " + url;
                try {
                    var errPayload = await res.json();
                    message = errPayload.detail || errPayload.error || errPayload.message || message;
                } catch (err) {}
                throw new Error(message);
            }
            return await res.json();
        } catch (err) {
            if (err && err.name === "AbortError") throw new Error("Request cancelled");
            throw err;
        } finally {
            if (abortListener && externalSignal) externalSignal.removeEventListener("abort", abortListener);
            if (timer) clearTimeout(timer);
        }
    }

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
        return (fund && fund.benchmark_options && fund.benchmark_options.length) ? fund.benchmark_options : ["NIFTY 500"];
    }

    function mutualRangeOptions() {
        var compare = mutualState.compare;
        if (compare && compare.range_options && compare.range_options.length) return compare.range_options;
        return [
            { key: "1y", label: "1Y" },
            { key: "3y", label: "3Y" },
            { key: "5y", label: "5Y" },
            { key: "max", label: "Since Inception" },
        ];
    }

    function clearMutualSuggestions() {
        mutualSearchResults = [];
        clearChildren($mfSuggest);
        $mfSuggest.classList.remove("visible");
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

    function markUpdated(label) {
        if (!$mfPageUpdated) return;
        $mfPageUpdated.textContent = label || ("Updated " + new Date().toLocaleTimeString("en-US", {
            hour: "2-digit",
            minute: "2-digit",
        }));
    }

    function renderMutualStatus() {
        var count = (mutualState.watchlist || []).length;
        var parts = ["Shared", count + " fund" + (count === 1 ? "" : "s")];
        if (mutualState.storage) parts.push(mutualState.durable ? "saved" : "session");
        var clsName = "mf-status" + (count ? " is-ready" : " is-muted");
        $mfStatus.className = clsName;
        $mfStatus.textContent = parts.join(" · ");
    }

    function renderMutualWatchlist() {
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
            meta.appendChild(el("span", "mf-holding-chip", fund.category ? titleCase(fund.category) : "Unclassified"));
            if (fund.benchmark_options && fund.benchmark_options.length) {
                meta.appendChild(el("span", "mf-holding-chip muted", fund.benchmark_options[0]));
            }
            meta.appendChild(el("span", "mf-holding-chip muted", "Code " + (fund.scheme_code || "\u2014")));
            card.appendChild(meta);

            var foot = el("div", "mf-holding-foot");
            foot.appendChild(el("span", "mf-holding-value", fund.latest_nav != null ? fmtPrice(fund.latest_nav) : "NAV \u2014"));
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
        $mfChartBox.classList.remove("is-empty");
        var placeholder = $mfChartBox.querySelector(".mf-chart-placeholder");
        if (placeholder) placeholder.hidden = true;
    }

    function renderMutualChart(compare) {
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
        heroValue.appendChild(el("span", "mf-hero-value-number", fund.latest_nav != null ? fmtPrice(fund.latest_nav) : "\u2014"));
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
            ["LATEST NAV", fund.latest_nav != null ? fmtPrice(fund.latest_nav) : "\u2014"],
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
            if (pair[0] === "FUND RETURN" || pair[0] === "ALPHA") {
                var numeric = Number(String(pair[1]).replace(/[^0-9.-]/g, ""));
                tone = numeric > 0 ? "up" : (numeric < 0 ? "down" : "neutral");
            }
            card.appendChild(el("span", "mf-stat-value " + tone, pair[1]));
            $mfStats.appendChild(card);
        });

        $mfChartTitle.textContent = (fund.scheme_name || "Mutual Fund") + " vs " + (mutualState.selectedBenchmark || "Benchmark");
        if (compare) {
            $mfChartNote.textContent =
                "Normalized to 100 from " + fmtDateLabel(compare.from_date) + " · " +
                ((compare.render_points && compare.render_points < compare.points)
                    ? (compare.render_points + "/" + compare.points + " points rendered")
                    : (compare.points + " NAV points")) +
                " · " + (compare.source && compare.source.fund ? compare.source.fund : "AMFI") +
                " vs " + (compare.source && compare.source.benchmark ? compare.source.benchmark : "NSE");
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
        if (needsCompare) loadMutualComparison({ force: true });
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
            markUpdated("Shared watchlist");
            return;
        }

        var prefs = loadMutualSelectionPrefs() || {};
        var target = (mutualState.watchlist || []).find(function (item) {
            return String(item.scheme_code || "") === String(opts.schemeCode || mutualState.selectedSchemeCode || prefs.schemeCode || "");
        }) || mutualState.watchlist[0];

        mutualState.selectedSchemeCode = String(target.scheme_code || "");
        var benchmarkOptions = target.benchmark_options && target.benchmark_options.length ? target.benchmark_options.slice() : ["NIFTY 500"];
        mutualState.selectedBenchmark = opts.benchmark || mutualState.selectedBenchmark || prefs.benchmark || benchmarkOptions[0] || "NIFTY 500";
        if (benchmarkOptions.indexOf(mutualState.selectedBenchmark) === -1) {
            mutualState.selectedBenchmark = benchmarkOptions[0] || mutualState.selectedBenchmark;
        }
        mutualState.selectedRange = opts.range || mutualState.selectedRange || prefs.range || "max";
        if (!mutualRangeOptions().some(function (item) { return item.key === mutualState.selectedRange; })) {
            mutualState.selectedRange = "max";
        }
        mutualState.compareError = null;
        saveMutualSelectionPrefs();
        renderMutualPage();
        markUpdated("Updated " + new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }));

        var compare = mutualState.compare;
        var compareMatches = compare
            && compare.fund
            && compare.fund.scheme_code === mutualState.selectedSchemeCode
            && compare.benchmark === mutualState.selectedBenchmark
            && compare.range === mutualState.selectedRange;
        if (!compareMatches || opts.forceCompare) loadMutualComparison({ force: true });
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
                markUpdated(err.message || "Watchlist unavailable");
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
        if (mutualCompareAbortController) mutualCompareAbortController.abort();
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
                markUpdated("Compared " + fmtDateLabel(data.to_date));
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
                if (mutualCompareAbortController === compareController) mutualCompareAbortController = null;
                if (requestSeq === mutualCompareRequestSeq) mutualCompareLoadPromise = null;
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
            $mfInput.value = "";
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
            if (item.latest_nav != null) head.appendChild(el("span", "mf-suggest-nav", fmtPrice(item.latest_nav)));
            row.appendChild(head);
            var meta = el("div", "mf-suggest-meta");
            meta.appendChild(el("span", "mf-holding-chip", item.category ? titleCase(item.category) : "Unclassified"));
            if (item.benchmark_options && item.benchmark_options.length) {
                meta.appendChild(el("span", "mf-holding-chip muted", item.benchmark_options[0]));
            }
            if (item.latest_nav_date) meta.appendChild(el("span", "mf-holding-chip muted", fmtDateLabel(item.latest_nav_date)));
            row.appendChild(meta);
            if (item.tracked) row.disabled = true;
            else row.addEventListener("click", function () { addMutualFund(item.scheme_code); });
            frag.appendChild(row);
        });
        $mfSuggest.appendChild(frag);
        $mfSuggest.classList.add("visible");
    }

    async function showMutualSuggestions(query) {
        var q = String(query || "").trim();
        if (q.length < 2) {
            clearMutualSuggestions();
            return;
        }
        if (mutualSearchAbortController) mutualSearchAbortController.abort();
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
            if (mutualSearchAbortController === searchController) mutualSearchAbortController = null;
        }
    }

    var mutualSuggestTimeout = null;
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
            $mfSuggest.classList.remove("visible");
            if (mutualSearchAbortController) {
                mutualSearchAbortController.abort();
                mutualSearchAbortController = null;
            }
        }, 180);
    });

    $mfInput.addEventListener("keydown", function (e) {
        if (e.key === "Escape") {
            $mfInput.blur();
            $mfSuggest.classList.remove("visible");
            return;
        }
        if (e.key === "Enter" && mutualSearchResults.length) {
            e.preventDefault();
            var first = mutualSearchResults.find(function (item) { return !item.tracked; });
            if (first) addMutualFund(first.scheme_code);
        }
    });

    function handleResize() {
        if (mfChart && $mfChartBox) {
            mfChart.applyOptions({ width: $mfChartBox.clientWidth, height: $mfChartBox.clientHeight });
        }
    }

    function startPolling() {
        if (mutualPollTimer) clearInterval(mutualPollTimer);
        mutualPollTimer = setInterval(function () {
            if (!document.hidden) loadMutualWatchlist({ force: true });
        }, 120000);
    }

    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) loadMutualWatchlist({ force: true });
    });
    window.addEventListener("resize", handleResize);

    async function initialLoad() {
        renderMutualPage();
        markUpdated("Loading shared watchlist");
        await loadMutualWatchlist({ force: true });
        startPolling();
        setTimeout(handleResize, 60);
    }

    initialLoad();
})();

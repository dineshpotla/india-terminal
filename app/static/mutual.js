(function () {
    "use strict";

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

    let mfChart = null;
    let mfChartSeries = [];
    let mutualChartRenderKey = "";
    let mutualHoldingsLoadPromise = null;
    let mutualCompareLoadPromise = null;
    let mutualPerformanceLoadPromise = null;
    let mutualCompareAbortController = null;
    let mutualPerformanceAbortController = null;
    let mutualSearchAbortController = null;
    let mutualCompareRequestSeq = 0;
    let mutualPerformanceRequestSeq = 0;
    let mutualSearchResults = [];
    let mutualPollTimer = null;
    let deferredChartTimer = null;

    let mutualState = {
        watchlist: [],
        storage: null,
        durable: false,
        selectedSchemeCode: null,
        selectedBenchmark: null,
        selectedRange: "max",
        selectedChartSchemeCodes: [],
        compare: null,
        compareLoading: false,
        compareError: null,
        multiCompare: null,
        multiCompareLoading: false,
        multiCompareError: null,
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
    const $mfSelectAll = $("mf-select-all");
    const $mfClearAll = $("mf-clear-all");

    function el(tag, cls, text) {
        const node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text !== undefined) node.textContent = text;
        return node;
    }

    function clearChildren(node) {
        while (node && node.firstChild) node.removeChild(node.firstChild);
    }

    function fmtPrice(value) {
        if (value == null) return "\u2014";
        return "\u20b9" + Number(value).toLocaleString("en-IN", {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2,
        });
    }

    function fmtPct(value) {
        if (value == null) return "\u2014";
        return (value >= 0 ? "+" : "") + Number(value).toFixed(2) + "%";
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

    function positiveTone(value) {
        return value > 0 ? "up" : (value < 0 ? "down" : "neutral");
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
                chartSchemeCodes: mutualState.selectedChartSchemeCodes,
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
        var source = mutualState.multiCompare || mutualState.compare;
        if (source && source.range_options && source.range_options.length) return source.range_options;
        return [
            { key: "1y", label: "1Y" },
            { key: "3y", label: "3Y" },
            { key: "5y", label: "5Y" },
            { key: "max", label: "Since Inception" },
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

    function isMultiChartMode() {
        return currentChartCodes().length > 1;
    }

    function clearMutualSuggestions() {
        mutualSearchResults = [];
        clearChildren($mfSuggest);
        $mfSuggest.classList.remove("visible");
    }

    function markUpdated(label) {
        if (!$mfPageUpdated) return;
        $mfPageUpdated.textContent = label || ("Updated " + new Date().toLocaleTimeString("en-US", {
            hour: "2-digit",
            minute: "2-digit",
        }));
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

    function scheduleDeferredChartLoad(force) {
        clearTimeout(deferredChartTimer);
        deferredChartTimer = setTimeout(function () {
            deferredChartTimer = null;
            loadActiveChart({ force: !!force });
        }, 220);
    }

    function renderMutualStatus() {
        var funds = mutualState.watchlist || [];
        var chartCount = currentChartCodes().length;
        var parts = [
            "Shared",
            funds.length + " fund" + (funds.length === 1 ? "" : "s"),
            chartCount + " charted",
        ];
        if (mutualState.storage) parts.push(mutualState.durable ? "saved" : "session");
        var clsName = "mf-status" + (funds.length ? " is-ready" : " is-muted");
        $mfStatus.className = clsName;
        $mfStatus.textContent = parts.join(" · ");
    }

    function renderMutualWatchlist() {
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
                toggleChartSelection(code, checkbox.checked);
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
            meta.appendChild(el("span", "mf-holding-chip", fund.category ? titleCase(fund.category) : "Unclassified"));
            if (fund.benchmark_options && fund.benchmark_options.length) {
                meta.appendChild(el("span", "mf-holding-chip muted", fund.benchmark_options[0]));
            }
            meta.appendChild(el("span", "mf-holding-chip muted", "Code " + (code || "\u2014")));
            card.appendChild(meta);

            var foot = el("div", "mf-holding-foot");
            foot.appendChild(el("span", "mf-holding-value", fund.latest_nav != null ? fmtPrice(fund.latest_nav) : "NAV \u2014"));
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
        return mfChart;
    }

    function resetMutualChartSeries() {
        if (!mfChart) return;
        mfChartSeries.forEach(function (series) {
            try { mfChart.removeSeries(series); } catch (err) {}
        });
        mfChartSeries = [];
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

    function renderSingleChart(compare) {
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
            compare.render_points || 0,
            compare.to_date || "",
        ].join("|");
        if (mutualChartRenderKey === renderKey) return;

        var chartRef = ensureMutualChart();
        resetMutualChartSeries();
        hideMutualChartPlaceholder();

        var fundSeries = chartRef.addLineSeries({
            color: "#ff9d3f",
            lineWidth: 2,
            priceLineVisible: false,
            lastValueVisible: true,
            title: "Fund",
        });
        var benchmarkSeries = chartRef.addLineSeries({
            color: "#4fd1c5",
            lineWidth: 2,
            lineStyle: LightweightCharts.LineStyle.Dashed,
            priceLineVisible: false,
            lastValueVisible: true,
            title: "Benchmark",
        });
        mfChartSeries = [fundSeries, benchmarkSeries];
        fundSeries.setData(compare.fund_chart_data || []);
        benchmarkSeries.setData(compare.benchmark_chart_data || []);
        chartRef.timeScale().fitContent();
        mutualChartRenderKey = renderKey;
    }

    function renderMultiChart(payload) {
        if (!payload || !payload.items || !payload.items.length) {
            resetMutualChartSeries();
            mutualChartRenderKey = "";
            showMutualChartPlaceholder(
                mutualState.multiCompareLoading
                    ? "Loading normalized NAV performance for selected funds…"
                    : (mutualState.multiCompareError || "Tick one or more funds to chart them together.")
            );
            return;
        }

        var renderKey = [
            "multi",
            payload.range,
            payload.to_date || "",
            payload.items.map(function (item) {
                return [item.scheme_code, item.render_points || 0].join(":");
            }).join("|"),
        ].join("|");
        if (mutualChartRenderKey === renderKey) return;

        var chartRef = ensureMutualChart();
        resetMutualChartSeries();
        hideMutualChartPlaceholder();

        payload.items.forEach(function (item, idx) {
            var color = MULTI_SERIES_COLORS[idx % MULTI_SERIES_COLORS.length];
            var series = chartRef.addLineSeries({
                color: color,
                lineWidth: idx === 0 ? 2.4 : 2,
                priceLineVisible: false,
                lastValueVisible: payload.items.length <= 6,
                title: item.scheme_name || item.scheme_code || ("Fund " + (idx + 1)),
            });
            series.setData(item.chart_data || []);
            mfChartSeries.push(series);
        });
        chartRef.timeScale().fitContent();
        mutualChartRenderKey = renderKey;
    }

    function renderActiveChart() {
        var selectedCodes = currentChartCodes();
        if (!selectedCodes.length) {
            resetMutualChartSeries();
            mutualChartRenderKey = "";
            showMutualChartPlaceholder("Use the checkboxes to chart one or more funds. The page loads NAV summaries first, then charts on demand.");
            return;
        }
        if (selectedCodes.length > 1) {
            renderMultiChart(mutualState.multiCompare);
            return;
        }
        renderSingleChart(mutualState.compare);
    }

    function renderSingleStats(fund, compare) {
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
            var numeric = Number(String(pair[1]).replace(/[^0-9.-]/g, ""));
            var tone = (pair[0] === "FUND RETURN" || pair[0] === "ALPHA") && !isNaN(numeric)
                ? positiveTone(numeric)
                : "neutral";
            card.appendChild(el("span", "mf-stat-value " + tone, pair[1]));
            $mfStats.appendChild(card);
        });
    }

    function renderMultiStats(payload) {
        var items = (payload && payload.items) || [];
        var sorted = items.slice().sort(function (a, b) {
            return (b.return_pct || 0) - (a.return_pct || 0);
        });
        var leader = sorted[0];
        var laggard = sorted[sorted.length - 1];
        [
            ["SELECTED FUNDS", String(items.length)],
            ["LEAD RETURN", leader ? fmtPct(leader.return_pct || 0) : "\u2014"],
            ["LAG RETURN", laggard ? fmtPct(laggard.return_pct || 0) : "\u2014"],
        ].forEach(function (pair) {
            var card = el("div", "mf-stat-card");
            card.appendChild(el("span", "mf-stat-label", pair[0]));
            var numeric = Number(String(pair[1]).replace(/[^0-9.-]/g, ""));
            var tone = !isNaN(numeric) ? positiveTone(numeric) : "neutral";
            card.appendChild(el("span", "mf-stat-value " + tone, pair[1]));
            $mfStats.appendChild(card);
        });
    }

    function renderMutualDetail() {
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
            renderActiveChart();
            return;
        }

        $mfDetail.classList.remove("is-empty");
        $mfHero.hidden = false;
        $mfRangeBlock.hidden = false;
        $mfStats.hidden = false;
        $mfChartShell.classList.remove("is-empty");
        $mfBenchmarkBlock.hidden = multiMode;
        clearChildren($mfHero);
        clearChildren($mfBenchmarks);
        clearChildren($mfRanges);
        clearChildren($mfStats);

        var heroTop = el("div", "mf-hero-top");
        var titleWrap = el("div", "mf-hero-copy");
        titleWrap.appendChild(el("div", "mf-hero-kicker", multiMode ? "NAV PERFORMANCE STACK" : "OFFICIAL NAV TRACKER"));
        titleWrap.appendChild(el("h3", "mf-hero-name", fund.scheme_name || fund.scheme_code || "Mutual Fund"));
        var meta = [];
        if (fund.category) meta.push(titleCase(fund.category));
        if (fund.scheme_code) meta.push("Code " + fund.scheme_code);
        if (fund.latest_nav_date) meta.push("NAV " + fmtDateLabel(fund.latest_nav_date));
        if (multiMode) meta.push(selectedCodes.length + " funds selected");
        titleWrap.appendChild(el("div", "mf-hero-meta", meta.join(" · ")));
        heroTop.appendChild(titleWrap);

        var heroValue = el("div", "mf-hero-value");
        heroValue.appendChild(el("span", "mf-hero-value-label", "Latest NAV"));
        heroValue.appendChild(el("span", "mf-hero-value-number", fund.latest_nav != null ? fmtPrice(fund.latest_nav) : "\u2014"));
        heroValue.appendChild(el("span", "mf-hero-value-change flat", fund.latest_nav_date ? "Updated " + fmtDateLabel(fund.latest_nav_date) : "Official NAV date unavailable"));
        heroTop.appendChild(heroValue);
        $mfHero.appendChild(heroTop);

        if (!multiMode) {
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
                $mfBenchmarks.appendChild(chip);
            });
        }

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
            $mfRanges.appendChild(chip);
        });

        if (multiMode) {
            renderMultiStats(mutualState.multiCompare);
            $mfChartTitle.textContent = selectedCodes.length + " Selected Funds";
            if (mutualState.multiCompare && mutualState.multiCompare.items && mutualState.multiCompare.items.length) {
                $mfChartNote.textContent =
                    "Normalized NAV lines from " + fmtDateLabel(mutualState.multiCompare.from_date) +
                    " · " + selectedCodes.length + " funds" +
                    " · " + (mutualState.multiCompare.source && mutualState.multiCompare.source.fund ? mutualState.multiCompare.source.fund : "AMFI");
            } else if (mutualState.multiCompareLoading) {
                $mfChartNote.textContent = "Loading normalized NAV performance for selected funds…";
            } else if (mutualState.multiCompareError) {
                $mfChartNote.textContent = mutualState.multiCompareError;
            } else {
                $mfChartNote.textContent = "NAV summaries are loaded first. Use All or the chart checkboxes to overlay selected funds.";
            }
        } else {
            renderSingleStats(fund, mutualState.compare && mutualState.compare.fund && mutualState.compare.fund.scheme_code === fund.scheme_code ? mutualState.compare : null);
            $mfChartTitle.textContent = (fund.scheme_name || "Mutual Fund") + " vs " + (mutualState.selectedBenchmark || "Benchmark");
            if (mutualState.compare && mutualState.compare.fund && mutualState.compare.fund.scheme_code === fund.scheme_code) {
                $mfChartNote.textContent =
                    "Normalized to 100 from " + fmtDateLabel(mutualState.compare.from_date) +
                    " · " + (mutualState.compare.render_points || 0) + " points" +
                    " · " + (mutualState.compare.source && mutualState.compare.source.fund ? mutualState.compare.source.fund : "AMFI") +
                    " vs " + (mutualState.compare.source && mutualState.compare.source.benchmark ? mutualState.compare.source.benchmark : "NSE");
            } else if (mutualState.compareLoading) {
                $mfChartNote.textContent = "Loading official NAV and benchmark history…";
            } else if (mutualState.compareError) {
                $mfChartNote.textContent = mutualState.compareError;
            } else {
                $mfChartNote.textContent = "NAV summary loaded. Click the selected card, benchmark, or range to build the benchmark chart.";
            }
        }

        renderActiveChart();
    }

    function renderMutualPage() {
        renderMutualStatus();
        renderMutualWatchlist();
        renderMutualDetail();
    }

    function syncSelectionToChartCodes() {
        var chartCodes = currentChartCodes();
        if (chartCodes.length === 1) {
            mutualState.selectedSchemeCode = chartCodes[0];
        } else if (chartCodes.length > 1 && chartCodes.indexOf(String(mutualState.selectedSchemeCode || "")) === -1) {
            mutualState.selectedSchemeCode = chartCodes[0];
        }
        mutualState.selectedChartSchemeCodes = chartCodes;
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
        syncSelectionToChartCodes();

        var benchmarks = mutualBenchmarkOptions(fund);
        var benchmark = opts.benchmark || mutualState.selectedBenchmark || benchmarks[0] || "NIFTY 500";
        if (benchmarks.indexOf(benchmark) === -1) benchmark = benchmarks[0] || benchmark;
        mutualState.selectedBenchmark = benchmark;

        var rangeOptions = mutualRangeOptions();
        var rangeKey = opts.range || mutualState.selectedRange || "max";
        if (!rangeOptions.some(function (item) { return item.key === rangeKey; })) rangeKey = "max";
        mutualState.selectedRange = rangeKey;

        mutualState.compareError = null;
        mutualState.multiCompareError = null;
        saveMutualSelectionPrefs();
        renderMutualPage();

        if (opts.loadChart === false) return;
        if (opts.deferChart) scheduleDeferredChartLoad(!!opts.forceChart);
        else loadActiveChart({ force: !!opts.forceChart });
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
            mutualState.selectedChartSchemeCodes = [];
            mutualState.compare = null;
            mutualState.compareLoading = false;
            mutualState.compareError = null;
            mutualState.multiCompare = null;
            mutualState.multiCompareLoading = false;
            mutualState.multiCompareError = null;
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

        var rangeOptions = mutualRangeOptions();
        mutualState.selectedRange = opts.range || mutualState.selectedRange || prefs.range || "max";
        if (!rangeOptions.some(function (item) { return item.key === mutualState.selectedRange; })) {
            mutualState.selectedRange = "max";
        }

        var requestedChartCodes = opts.chartSchemeCodes || mutualState.selectedChartSchemeCodes || prefs.chartSchemeCodes || [];
        mutualState.selectedChartSchemeCodes = requestedChartCodes.slice();
        syncSelectionToChartCodes();
        if (!mutualState.selectedChartSchemeCodes.length) {
            mutualState.selectedChartSchemeCodes = [mutualState.selectedSchemeCode];
        }

        saveMutualSelectionPrefs();
        renderMutualPage();
        markUpdated("Updated " + new Date().toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }));
        if (opts.loadChart !== false) scheduleDeferredChartLoad(!!opts.forceChart);
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
        var selectedCodes = currentChartCodes();
        if (!fund || selectedCodes.length !== 1 || selectedCodes[0] !== String(fund.scheme_code || "")) return null;

        var benchmark = opts.benchmark || mutualState.selectedBenchmark || (fund.benchmark_options && fund.benchmark_options[0]) || "NIFTY 500";
        var rangeKey = opts.range || mutualState.selectedRange || "max";
        if (!opts.force && mutualState.compare && mutualState.compare.fund && mutualState.compare.fund.scheme_code === fund.scheme_code && mutualState.compare.benchmark === benchmark && mutualState.compare.range === rangeKey) {
            return mutualState.compare;
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
                    "&range=" + encodeURIComponent(rangeKey),
                    { timeoutMs: 32000, signal: compareController ? compareController.signal : null }
                );
                if (compareController && compareController.signal.aborted) return null;
                if (requestSeq !== mutualCompareRequestSeq) return data;
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

    async function loadMutualPerformance(opts) {
        opts = opts || {};
        var selectedCodes = currentChartCodes();
        if (selectedCodes.length < 2) return null;
        var rangeKey = opts.range || mutualState.selectedRange || "max";
        if (!opts.force && mutualState.multiCompare && mutualState.multiCompare.range === rangeKey) {
            var existingCodes = (mutualState.multiCompare.items || []).map(function (item) { return item.scheme_code; }).sort().join(",");
            var requestedCodes = selectedCodes.slice().sort().join(",");
            if (existingCodes === requestedCodes) return mutualState.multiCompare;
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
                    "&range=" + encodeURIComponent(rangeKey),
                    { timeoutMs: 32000, signal: perfController ? perfController.signal : null }
                );
                if (perfController && perfController.signal.aborted) return null;
                if (requestSeq !== mutualPerformanceRequestSeq) return data;
                mutualState.multiCompare = data;
                mutualState.multiCompareLoading = false;
                mutualState.selectedRange = data.range || rangeKey;
                saveMutualSelectionPrefs();
                renderMutualPage();
                markUpdated("Stacked " + String((data.items || []).length) + " funds");
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
                if (mutualPerformanceAbortController === perfController) mutualPerformanceAbortController = null;
                if (requestSeq === mutualPerformanceRequestSeq) mutualPerformanceLoadPromise = null;
            }
        })();
        return mutualPerformanceLoadPromise;
    }

    function loadActiveChart(opts) {
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

    function toggleChartSelection(schemeCode, checked) {
        var codes = currentChartCodes();
        if (checked) {
            if (codes.indexOf(schemeCode) === -1) codes.push(schemeCode);
        } else {
            codes = codes.filter(function (code) { return code !== schemeCode; });
        }
        mutualState.selectedChartSchemeCodes = codes;
        syncSelectionToChartCodes();
        saveMutualSelectionPrefs();
        renderMutualPage();
        if (!codes.length) {
            cancelSingleCompare();
            cancelMultiCompare();
            return;
        }
        loadActiveChart({ force: true });
    }

    function selectAllChartFunds() {
        mutualState.selectedChartSchemeCodes = (mutualState.watchlist || []).map(function (item) {
            return String(item.scheme_code || "").trim();
        }).filter(Boolean);
        syncSelectionToChartCodes();
        saveMutualSelectionPrefs();
        renderMutualPage();
        loadActiveChart({ force: true });
    }

    function clearAllChartFunds() {
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
                chartSchemeCodes: [String(schemeCode)],
                forceChart: false,
                loadChart: false,
            });
            scheduleDeferredChartLoad(false);
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
            var chartCodes = currentChartCodes().filter(function (code) { return code !== schemeCode; });
            var data = await fetchJson("/api/mf/watchlist/" + encodeURIComponent(String(schemeCode)), {
                method: "DELETE",
                timeoutMs: 20000,
            });
            applyMutualWatchlist(data, {
                schemeCode: selectedCode === schemeCode ? null : selectedCode,
                chartSchemeCodes: chartCodes,
                loadChart: false,
            });
            if (chartCodes.length) scheduleDeferredChartLoad(false);
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
            if (item.latest_nav_date) {
                meta.appendChild(el("span", "mf-holding-chip muted", fmtDateLabel(item.latest_nav_date)));
            }
            row.appendChild(meta);

            if (item.tracked) row.disabled = true;
            else {
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

    if ($mfSelectAll) {
        $mfSelectAll.addEventListener("click", function () {
            selectAllChartFunds();
        });
    }

    if ($mfClearAll) {
        $mfClearAll.addEventListener("click", function () {
            clearAllChartFunds();
        });
    }

    function handleResize() {
        if (mfChart && $mfChartBox) {
            mfChart.applyOptions({ width: $mfChartBox.clientWidth, height: $mfChartBox.clientHeight });
        }
    }

    function startPolling() {
        if (mutualPollTimer) clearInterval(mutualPollTimer);
        mutualPollTimer = setInterval(function () {
            if (!document.hidden) loadMutualWatchlist({ force: true, loadChart: false });
        }, 120000);
    }

    document.addEventListener("visibilitychange", function () {
        if (!document.hidden) loadMutualWatchlist({ force: true, loadChart: false });
    });
    window.addEventListener("resize", handleResize);

    async function initialLoad() {
        renderMutualPage();
        markUpdated("Loading shared watchlist");
        await loadMutualWatchlist({ force: true, loadChart: false });
        scheduleDeferredChartLoad(false);
        startPolling();
        setTimeout(handleResize, 60);
    }

    initialLoad();
})();

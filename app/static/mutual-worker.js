"use strict";

const SINGLE_MAX_POINTS = 240;
const MULTI_MAX_POINTS = 180;

function roundChartValue(value) {
    return Math.round(Number(value || 0) * 100) / 100;
}

function isoFromUnixTime(seconds) {
    if (!Number.isFinite(Number(seconds))) return null;
    return new Date(Number(seconds) * 1000).toISOString().slice(0, 10);
}

function rangeCutoffUnix(rangeKey, lastTime) {
    if (!lastTime || rangeKey === "max") return null;
    const dt = new Date(Number(lastTime) * 1000);
    if (rangeKey === "1y") dt.setFullYear(dt.getFullYear() - 1);
    else if (rangeKey === "3y") dt.setFullYear(dt.getFullYear() - 3);
    else if (rangeKey === "5y") dt.setFullYear(dt.getFullYear() - 5);
    else return null;
    return Math.floor(dt.getTime() / 1000);
}

function downsampleSeries(points, maxPoints) {
    const source = Array.isArray(points) ? points : [];
    if (source.length <= maxPoints) return source.slice();
    if (maxPoints <= 2) return [source[0], source[source.length - 1]];
    const sampled = [];
    const seen = new Set();
    const step = (source.length - 1) / (maxPoints - 1);
    for (let idx = 0; idx < maxPoints; idx += 1) {
        const point = source[Math.round(idx * step)];
        const key = point && point.time;
        if (seen.has(key)) continue;
        seen.add(key);
        sampled.push(point);
    }
    if (sampled.length && sampled[sampled.length - 1].time !== source[source.length - 1].time) {
        sampled[sampled.length - 1] = source[source.length - 1];
    }
    return sampled;
}

function rebaseRawSeries(rawSeries, rangeKey) {
    const source = Array.isArray(rawSeries) ? rawSeries : [];
    if (!source.length) return [];
    const lastTime = source[source.length - 1] && Number(source[source.length - 1][0]);
    const cutoff = rangeCutoffUnix(rangeKey, lastTime);
    let subset = source.filter((point) => !cutoff || Number(point[0]) >= cutoff);
    if (!subset.length) subset = source.slice();
    const base = Number(subset[0] && subset[0][1]) || 0;
    if (!base) return [];
    return subset.map((point) => ({
        time: Number(point[0]),
        value: roundChartValue((Number(point[1]) / base) * 100),
    }));
}

function multiPointBudget(selectedCount) {
    const count = Math.max(1, Number(selectedCount || 0));
    if (count <= 2) return MULTI_MAX_POINTS;
    if (count <= 4) return 144;
    if (count <= 6) return 120;
    return 96;
}

function buildSingleCompare(payload) {
    const fundHistory = Array.isArray(payload && payload.fund_history) ? payload.fund_history : [];
    const benchmarkHistory = Array.isArray(payload && payload.benchmark_history) ? payload.benchmark_history : [];
    if (fundHistory.length < 2 || benchmarkHistory.length < 2) {
        throw new Error("Not enough stored history to compare");
    }
    const rangeKey = (payload && payload.range) || "max";
    const fundChart = downsampleSeries(rebaseRawSeries(fundHistory, rangeKey), SINGLE_MAX_POINTS);
    const benchmarkChart = downsampleSeries(rebaseRawSeries(benchmarkHistory, rangeKey), SINGLE_MAX_POINTS);
    if (!fundChart.length || !benchmarkChart.length) {
        throw new Error("Unable to build comparison series");
    }
    const fundReturn = roundChartValue((fundChart[fundChart.length - 1] && fundChart[fundChart.length - 1].value) - 100);
    const benchmarkReturn = roundChartValue((benchmarkChart[benchmarkChart.length - 1] && benchmarkChart[benchmarkChart.length - 1].value) - 100);
    return {
        fund: payload.fund,
        benchmark: payload.benchmark,
        range: rangeKey,
        range_options: payload.range_options || [],
        benchmark_options: payload.benchmark_options || [],
        from_date: isoFromUnixTime(fundChart[0] && fundChart[0].time),
        to_date: isoFromUnixTime(fundChart[fundChart.length - 1] && fundChart[fundChart.length - 1].time),
        points: fundChart.length,
        render_points: fundChart.length,
        fund_chart_data: fundChart,
        benchmark_chart_data: benchmarkChart,
        fund_return_pct: fundReturn,
        benchmark_return_pct: benchmarkReturn,
        alpha_pct: roundChartValue(fundReturn - benchmarkReturn),
        source: payload.source || {},
    };
}

function buildMultiCompare(payload) {
    const items = Array.isArray(payload && payload.items) ? payload.items : [];
    const rangeKey = (payload && payload.range) || "max";
    const pointBudget = multiPointBudget(items.length);
    let earliest = null;
    let latest = null;
    const nextItems = items.map((item) => {
        const chartData = downsampleSeries(rebaseRawSeries(item && item.nav_history, rangeKey), pointBudget);
        if (!chartData.length) return null;
        const fromDate = isoFromUnixTime(chartData[0] && chartData[0].time);
        const toDate = isoFromUnixTime(chartData[chartData.length - 1] && chartData[chartData.length - 1].time);
        if (fromDate && (!earliest || fromDate < earliest)) earliest = fromDate;
        if (toDate && (!latest || toDate > latest)) latest = toDate;
        return {
            scheme_code: item.scheme_code,
            scheme_name: item.scheme_name,
            category: item.category,
            latest_nav: item.latest_nav,
            latest_nav_date: item.latest_nav_date,
            points: chartData.length,
            render_points: chartData.length,
            return_pct: roundChartValue((chartData[chartData.length - 1] && chartData[chartData.length - 1].value) - 100),
            chart_data: chartData,
        };
    }).filter(Boolean);
    if (!nextItems.length) {
        throw new Error("Not enough NAV history to chart the selected funds");
    }
    return {
        range: rangeKey,
        range_options: payload.range_options || [],
        selected_count: nextItems.length,
        from_date: earliest,
        to_date: latest,
        items: nextItems,
        source: payload.source || {},
    };
}

self.onmessage = function (event) {
    const data = event && event.data ? event.data : {};
    const id = data.id;
    const type = data.type;
    try {
        let payload;
        if (type === "single") payload = buildSingleCompare(data.payload || {});
        else if (type === "multi") payload = buildMultiCompare(data.payload || {});
        else throw new Error("Unknown mutual worker task");
        self.postMessage({ id, ok: true, payload });
    } catch (err) {
        self.postMessage({ id, ok: false, error: err && err.message ? err.message : "Worker failed" });
    }
};

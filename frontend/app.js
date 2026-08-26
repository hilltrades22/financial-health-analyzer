(function () {
  "use strict";

  const NA = "Not reported / unavailable from standardized SEC data";

  // ---------- Theme ----------
  const root = document.documentElement;
  const themeToggle = document.getElementById("theme-toggle");
  const themeIcon = document.getElementById("theme-icon");
  function applyTheme(mode) {
    if (mode === "dark") { root.setAttribute("data-theme", "dark"); themeIcon.textContent = "☀"; }
    else { root.setAttribute("data-theme", "light"); themeIcon.textContent = "☽"; }
  }
  let storedTheme = null;
  try { storedTheme = localStorage.getItem("forge-theme"); } catch (e) { storedTheme = null; }
  applyTheme(storedTheme || (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  themeToggle.addEventListener("click", () => {
    const current = root.getAttribute("data-theme") === "dark" ? "dark" : "light";
    const next = current === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem("forge-theme", next); } catch (e) {}
    if (window.__forgeCharts) redrawAllCharts();
  });

  // ---------- Chart.js theme colors ----------
  function cssVar(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function chartColors() {
    return {
      text: cssVar("--text"), muted: cssVar("--muted"), border: cssVar("--border"),
      brand: cssVar("--brand"), pass: cssVar("--pass"), watch: cssVar("--watch"),
      fail: cssVar("--fail"), na: cssVar("--na"), card: cssVar("--card"),
    };
  }
  if (window.Chart) {
    Chart.defaults.font.family = "Segoe UI, -apple-system, sans-serif";
  }

  // Chart.js throws "Canvas is already in use" if a new Chart is created on a
  // canvas that still has a live instance attached (e.g. clicking a
  // timeframe/metric control that redraws in place without re-rendering the
  // whole tab). This was the root cause of controls appearing to do nothing
  // after the first redraw - destroy any existing instance on that canvas
  // first, via Chart.js's own registry, so every control genuinely redraws.
  function getOrCreateChart(canvas, config) {
    if (!canvas) return null;
    const existing = window.Chart && Chart.getChart ? Chart.getChart(canvas) : null;
    if (existing) existing.destroy();
    const chart = new Chart(canvas, config);
    window.__forgeCharts.push(chart);
    return chart;
  }

  // ---------- Elements ----------
  const homePanel = document.getElementById("home-panel");
  const loadingPanel = document.getElementById("loading-panel");
  const errorPanel = document.getElementById("error-panel");
  const resultsPanel = document.getElementById("results");
  const searchForm = document.getElementById("search-form");
  const tickerInput = document.getElementById("ticker-input");
  const brandHome = document.getElementById("brand-home");
  const backBtn = document.getElementById("back-btn");
  const printBtn = document.getElementById("print-btn");

  let currentData = null;
  let currentTicker = null;
  let timelineCache = {}; // { annual: [...], quarterly: [...] } for currentTicker
  window.__forgeCharts = [];

  function showPanel(panel) {
    [homePanel, loadingPanel, errorPanel, resultsPanel].forEach((p) => (p.hidden = p !== panel));
  }

  searchForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const t = tickerInput.value.trim().toUpperCase();
    if (t) analyze(t);
  });
  brandHome.addEventListener("click", () => showPanel(homePanel));
  backBtn.addEventListener("click", () => showPanel(homePanel));
  printBtn.addEventListener("click", () => window.print());

  // ---------- Loading experience ----------
  const LOADING_STEPS = [
    "Finding company", "Retrieving SEC filings", "Retrieving financial facts",
    "Normalizing financial data", "Calculating financial health", "Building visual analysis",
  ];
  let loadingTimer = null;

  function startLoading(ticker) {
    showPanel(loadingPanel);
    document.getElementById("loading-ticker").textContent = `Analyzing ${ticker}...`;
    const list = document.getElementById("loading-steps");
    list.innerHTML = LOADING_STEPS.map((s) => `<li>${s}</li>`).join("");
    let i = 0;
    const items = list.querySelectorAll("li");
    if (loadingTimer) clearInterval(loadingTimer);
    loadingTimer = setInterval(() => {
      items.forEach((el, idx) => {
        el.classList.toggle("active", idx === i);
        el.classList.toggle("done", idx < i);
      });
      i = Math.min(i + 1, items.length);
    }, 550);
  }
  function stopLoading() {
    if (loadingTimer) clearInterval(loadingTimer);
    loadingTimer = null;
  }

  // ---------- Fetch & orchestrate ----------
  async function analyze(ticker) {
    startLoading(ticker);
    try {
      const resp = await fetch(`/api/analyze/${encodeURIComponent(ticker)}`);
      const body = await safeJson(resp);
      stopLoading();
      if (!resp.ok) {
        showError(ticker, resp.status, body);
        return;
      }
      currentData = body;
      currentTicker = ticker;
      timelineCache = { annual: body.timeline, quarterly: null };
      render(body);
      showPanel(resultsPanel);
      window.scrollTo({ top: 0, behavior: "instant" });
    } catch (err) {
      stopLoading();
      showError(ticker, 0, { detail: err.message });
    }
  }

  async function safeJson(resp) { try { return await resp.json(); } catch { return null; } }

  function showError(ticker, status, body) {
    const detail = (body && body.detail) || "Unknown error";
    let msg;
    if (status === 404) msg = `We couldn't find "${ticker}" in SEC EDGAR. Double-check the ticker symbol and try again.`;
    else if (status === 503) msg = `SEC EDGAR appears to be unavailable right now. Please try again in a few minutes. (${detail})`;
    else msg = `Something went wrong analyzing "${ticker}": ${detail}`;
    errorPanel.textContent = msg;
    showPanel(errorPanel);
  }

  // ---------- Formatting helpers ----------
  function fmtUsd(v) {
    if (v === null || v === undefined) return NA;
    const sign = v < 0 ? "-" : "";
    const a = Math.abs(v);
    if (a >= 1e9) return `${sign}$${(a / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${sign}$${(a / 1e6).toFixed(2)}M`;
    return `${sign}$${a.toLocaleString()}`;
  }
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function slug(s) { return String(s).replace(/[^a-zA-Z0-9]+/g, "-"); }

  // ---------- Render orchestration ----------
  function render(data) {
    renderDashboard(data);
    document.getElementById("financial-story").textContent = data.financial_story;
    document.getElementById("trend-story").textContent = data.trend_story || "";
    renderHealthTab(data);
    renderQualityTab(data);
    renderValuationTab(data);
    renderMarketTab(data);
    renderRiskTab(data);
    renderTimelineTab(data);
    renderModel3D(data);
    renderCompareTab(data);
    renderDataSources(data);
    initTabs();
  }

  function redrawAllCharts() {
    if (currentData) render(currentData);
  }

  // ---------- Dashboard ----------
  function gradeClass(letter) {
    if (!letter || letter === "N/A") return "grade-N";
    return "grade-" + letter[0];
  }

  function renderDashboard(data) {
    const el = document.getElementById("dashboard-card");
    const forge = data.forge || {};
    const grading = data.grading || {};
    const badgeClass = "badge-" + slug(forge.label || "Insufficient Data");
    const meta = [];
    if (data.industry) meta.push(`Industry: ${esc(data.industry)}`);
    meta.push(`Latest Quarter: ${esc(data.latest_quarter.period_end || NA)}`);
    meta.push(`Latest Annual: FY${esc(data.latest_annual.fiscal_year || "?")} (${esc(data.latest_annual.period_end || NA)})`);
    if (data.valuation && data.valuation.price && data.valuation.price.value) {
      meta.push(`Price: $${data.valuation.price.value.toFixed(2)} (${esc(data.valuation.price.as_of || "")})`);
    }

    const score = data.score || {};
    const kpiStripHtml = `
      <div class="kpi-strip">
        <div class="kpi-mini k-pass"><div class="n">${(score.passed_rules || []).length}</div><div class="l">Pass</div></div>
        <div class="kpi-mini k-watch"><div class="n">${(score.watch_rules || []).length}</div><div class="l">Watch</div></div>
        <div class="kpi-mini k-fail"><div class="n">${(score.failed_rules || []).length}</div><div class="l">Fail</div></div>
        <div class="kpi-mini k-na"><div class="n">${(score.unavailable_rules || []).length}</div><div class="l">Unavailable</div></div>
      </div>`;

    const pillars = grading.pillars || forge.pillars || {};
    const pillarOrder = [
      ["financial_health", "Financial Health"], ["financial_quality", "Financial Quality"],
      ["valuation", "Valuation"], ["risk", "Risk"],
    ];
    const pillarHtml = pillarOrder.map(([key, label]) => {
      const p = pillars[key] || {};
      const val = p.score === null || p.score === undefined ? "N/A" : Math.round(p.score);
      const pct = p.score === null || p.score === undefined ? 0 : p.score;
      const color = p.score === null ? "var(--na)" : p.score >= 80 ? "var(--pass)" : p.score >= 60 ? "var(--pass)" : p.score >= 40 ? "var(--watch)" : "var(--fail)";
      const reasons = (p.key_reasons || []).map((r) => `<li>${esc(r)}</li>`).join("");
      return `<div class="pillar-card">
        <div class="pillar-name">${label}</div>
        <div class="pillar-value" style="color:${color}">${val}</div>
        <div class="pillar-bar-track"><div class="pillar-bar-fill" style="width:${pct}%;background:${color}"></div></div>
        <div class="pillar-grade-line"><span class="pg-letter">${esc(p.letter_grade || "N/A")}</span><span>${p.contribution_pct ? p.contribution_pct + "% of score" : "excluded"}</span></div>
        ${reasons ? `<details class="pillar-detail"><summary>Why this score</summary><ul>${reasons}</ul></details>` : ""}
      </div>`;
    }).join("");

    el.innerHTML = `
      <div class="dash-head">
        <div class="dash-identity">
          <h2>${esc(data.company_name)} <span class="dash-ticker">${esc(data.ticker)}</span></h2>
          <div class="dash-meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div>
          ${kpiStripHtml}
        </div>
        <div class="forge-score-box">
          <div class="forge-score-label">FORGE Score</div>
          <div class="forge-score-value">${forge.forge_score === null || forge.forge_score === undefined ? "N/A" : Math.round(forge.forge_score)}<span style="font-size:1.2rem;color:var(--muted)"> / 100</span></div>
          <div class="grade-row">
            <span class="letter-grade ${gradeClass(grading.letter_grade)}">${esc(grading.letter_grade || "N/A")}</span>
          </div>
          <div class="health-classification">${esc(grading.health_classification || "Insufficient Data")}</div>
          <div class="forge-score-badge ${badgeClass}">${esc(forge.label || "Insufficient Data")}</div>
        </div>
      </div>
      <div class="pillar-grid">${pillarHtml}</div>
      <p class="kpi-formula" style="margin-top:14px">${esc(grading.grading_methodology || "")}</p>
    `;
  }

  // ---------- Health tab ----------
  function ruleCard(r) {
    return `<div class="kpi-card">
      <div class="kpi-label">${esc(r.name)}</div>
      <div class="kpi-value">${esc(r.value)}</div>
      <span class="kpi-status status-${r.status}">${r.status}</span>
      <p class="kpi-explain">${esc(r.explanation)}</p>
      <p class="kpi-formula"><code>${esc(r.formula)}</code></p>
    </div>`;
  }

  function gaugeCanvas(id) { return `<div class="gauge-item"><canvas id="${id}" width="160" height="100"></canvas></div>`; }

  function drawGauge(canvasId, pct, label, colorFn) {
    const c = document.getElementById(canvasId);
    if (!c || !window.Chart) return;
    const clamped = Math.max(0, Math.min(100, pct === null ? 0 : pct));
    const color = colorFn(pct);
    const colors = chartColors();
    const chart = getOrCreateChart(c, {
      type: "doughnut",
      data: { datasets: [{ data: [clamped, 100 - clamped], backgroundColor: [color, colors.border], borderWidth: 0 }] },
      options: {
        circumference: 180, rotation: 270, cutout: "72%",
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        animation: { duration: 400 },
      },
    });
  }

  function renderHealthTab(data) {
    const el = document.getElementById("tab-health");
    const rules = data.score.rules;
    const byId = {};
    rules.forEach((r) => (byId[r.id] = r));
    const cats = ["Liquidity", "Debt & Leverage", "Retained Earnings", "Capital Structure", "Treasury Stock", "Lease Obligations", "Cash Generation", "Debt Service"];
    const byCat = {};
    rules.forEach((r) => (byCat[r.category] = byCat[r.category] || []).push(r));

    const liq = byId.liquidity, de = byId.debt_to_equity;
    const cashDebtChart = `<div class="chart-box"><h3>Cash vs. Debt</h3><p class="chart-sub">Latest quarter balance-sheet snapshot</p>
      <div class="chart-canvas-wrap short"><canvas id="chart-cash-debt"></canvas></div></div>`;

    const leaseSummary = data.lease_summary || {};
    const leaseChart = leaseSummary.available ? `<div class="chart-box"><h3>Lease Obligations</h3>
      <p class="chart-sub">Already included in Total Liabilities above — shown separately, not double-counted.</p>
      <div class="chart-canvas-wrap short"><canvas id="chart-lease"></canvas></div></div>` : "";

    let catHtml = "";
    cats.forEach((cat) => {
      const items = byCat[cat];
      if (!items) return;
      catHtml += `<h3 style="margin-top:24px">${cat}</h3><div class="metric-grid">${items.map(ruleCard).join("")}</div>`;
    });

    el.innerHTML = `
      <div class="chart-box">
        <h3>Liquidity &amp; Leverage Gauges</h3>
        <div class="gauge-row">
          <div class="gauge-item">${gaugeCanvas("gauge-liquidity")}<div class="gauge-title">Liquidity</div><div class="gauge-sub">${esc(liq ? liq.status : NA)}</div></div>
          <div class="gauge-item">${gaugeCanvas("gauge-de")}<div class="gauge-title">Debt / Equity</div><div class="gauge-sub">${esc(de ? de.value : NA)} (target &lt; 0.80x)</div></div>
        </div>
      </div>
      ${cashDebtChart}
      ${leaseChart}
      ${catHtml}
    `;

    // Gauges: liquidity pass=100/fail=20 visual proxy; D/E inverse-scaled
    if (liq) drawGauge("gauge-liquidity", liq.status === "PASS" ? 85 : liq.status === "UNAVAILABLE" ? 0 : 20, "", () =>
      liq.status === "PASS" ? cssVar("--pass") : liq.status === "UNAVAILABLE" ? cssVar("--na") : cssVar("--fail"));
    if (de) {
      const ratio = parseFloat(de.value);
      const pct = isNaN(ratio) ? 0 : Math.max(4, Math.min(100, 100 - ratio * 60));
      drawGauge("gauge-de", de.status === "UNAVAILABLE" ? 0 : pct, "", () =>
        de.status === "PASS" ? cssVar("--pass") : de.status === "WATCH" ? cssVar("--watch") : de.status === "UNAVAILABLE" ? cssVar("--na") : cssVar("--fail"));
    }

    const qf = data.quarterly_facts;
    const colors = chartColors();
    const cashDebtEl = document.getElementById("chart-cash-debt");
    if (cashDebtEl && window.Chart) {
      const cash = (qf.cash_and_equivalents.available ? qf.cash_and_equivalents.value : 0) +
        (qf.short_term_investments.available ? qf.short_term_investments.value : 0) +
        (qf.long_term_investments.available ? qf.long_term_investments.value : 0);
      const debt = (qf.short_term_debt.available ? qf.short_term_debt.value : 0) + (qf.long_term_debt.available ? qf.long_term_debt.value : 0);
      const chart = getOrCreateChart(cashDebtEl, {
        type: "bar",
        data: {
          labels: ["Cash + Securities", "Total Debt"],
          datasets: [{ data: [cash, debt], backgroundColor: [colors.pass, colors.fail], borderRadius: 6 }],
        },
        options: {
          plugins: { legend: { display: false } },
          scales: {
            y: { ticks: { color: colors.muted, callback: (v) => fmtUsd(v) }, grid: { color: colors.border } },
            x: { ticks: { color: colors.text }, grid: { display: false } },
          },
        },
      });
    }

    const leaseEl = document.getElementById("chart-lease");
    if (leaseEl && window.Chart && leaseSummary.available) {
      const chart = getOrCreateChart(leaseEl, {
        type: "doughnut",
        data: {
          labels: ["Current Lease Liabilities", "Long-Term Lease Liabilities"],
          datasets: [{ data: [leaseSummary.current_total, leaseSummary.noncurrent_total], backgroundColor: [colors.brand, colors.watch] }],
        },
        options: { plugins: { legend: { position: "bottom", labels: { color: colors.text } } } },
      });
    }
  }

  // ---------- Quality tab ----------
  function renderQualityTab(data) {
    const el = document.getElementById("tab-quality");
    const q = data.quality_metrics;
    const kpis = [
      ["Revenue", q.revenue], ["Revenue Growth", q.revenue_growth], ["Operating Income", q.operating_income],
      ["Net Income", q.net_income], ["Operating Margin", q.operating_margin], ["Net Margin", q.net_margin],
      ["Return on Equity (ROE)", q.roe], ["Return on Assets (ROA)", q.roa], ["Operating Cash Flow", q.operating_cash_flow],
      ["Free Cash Flow", q.free_cash_flow], ["FCF Margin", q.fcf_margin],
    ];
    const kpiHtml = kpis.map(([label, m]) => `<div class="kpi-card"><div class="kpi-label">${label}</div><div class="kpi-value">${esc(m.display)}</div></div>`).join("");

    const piotroski = data.piotroski;
    const heatCells = piotroski.criteria.map((c) => `<div class="heat-cell ${c.status}"><div class="heat-cell-title">${esc(c.label)}</div><div>${esc(c.detail)}</div></div>`).join("");

    el.innerHTML = `
      <div class="metric-grid">${kpiHtml}</div>
      <div class="chart-box">
        <h3>Piotroski F-Score: ${piotroski.score} / ${piotroski.scored_out_of}${piotroski.scored_out_of < 9 ? ` <span style="color:var(--muted);font-size:0.7em">(${piotroski.unavailable_count} of 9 criteria unavailable from SEC data)</span>` : ""}</h3>
        <p class="chart-sub">A 9-point checklist of profitability, leverage/liquidity, and operating-efficiency trends, each scored PASS/FAIL from two consecutive fiscal years of SEC data.</p>
        <div class="heatmap-grid">${heatCells}</div>
      </div>
      <div class="chart-box">
        <h3>Revenue &amp; Net Income Trend</h3>
        <div class="timeframe-controls" id="quality-tf"></div>
        <div class="chart-canvas-wrap"><canvas id="chart-quality-trend"></canvas></div>
      </div>
      <div class="chart-box">
        <h3>Free Cash Flow Trend</h3>
        <div class="chart-canvas-wrap short"><canvas id="chart-fcf-trend"></canvas></div>
      </div>
    `;

    buildTimeframeButtons("quality-tf", data.timeline, (filtered) => drawQualityCharts(filtered));
    drawQualityCharts(filterTimeline(data.timeline, "MAX"));
  }

  function drawQualityCharts(timeline) {
    const colors = chartColors();
    const labels = timeline.map((t) => "FY" + (t.fiscal_year || t.period_end));
    const trendEl = document.getElementById("chart-quality-trend");
    if (trendEl && window.Chart) {
      const chart = getOrCreateChart(trendEl, {
        type: "bar",
        data: {
          labels,
          datasets: [
            { label: "Revenue", data: timeline.map((t) => t.revenue), backgroundColor: colors.brand },
            { label: "Net Income", data: timeline.map((t) => t.net_income), backgroundColor: colors.pass, type: "line", borderColor: colors.pass, tension: 0.3 },
          ],
        },
        options: {
          plugins: { legend: { labels: { color: colors.text } } },
          scales: {
            y: { ticks: { color: colors.muted, callback: (v) => fmtUsd(v) }, grid: { color: colors.border } },
            x: { ticks: { color: colors.text }, grid: { display: false } },
          },
        },
      });
    }
    const fcfEl = document.getElementById("chart-fcf-trend");
    if (fcfEl && window.Chart) {
      const chart = getOrCreateChart(fcfEl, {
        type: "line",
        data: { labels, datasets: [{ label: "Free Cash Flow", data: timeline.map((t) => t.free_cash_flow), borderColor: colors.accent, backgroundColor: colors.accent + "33", fill: true, tension: 0.3 }] },
        options: {
          plugins: { legend: { display: false } },
          scales: {
            y: { ticks: { color: colors.muted, callback: (v) => fmtUsd(v) }, grid: { color: colors.border } },
            x: { ticks: { color: colors.text }, grid: { display: false } },
          },
        },
      });
    }
  }

  // ---------- Valuation tab ----------
  let valuationHistoryMetric = "pe";
  const VALUATION_METRIC_LABELS = { pe: "P/E", pb: "P/B", ps: "P/S", ev_ebitda: "EV/EBITDA" };

  function renderValuationTab(data) {
    const el = document.getElementById("tab-valuation");
    const v = data.valuation;
    const vh = data.valuation_history || { available: false };
    const bbb = data.bull_base_bear || { available: false };
    const unavailable = { available: false, display: NA };
    const rows = [
      ["Current Price", v.price ? { available: true, display: "$" + Number(v.price.value).toFixed(2) } : unavailable],
      ["Market Cap", v.market_cap || unavailable], ["Shares Outstanding", v.shares_outstanding ? { available: true, display: Number(v.shares_outstanding.value).toLocaleString() } : unavailable],
      ["EPS (TTM)", v.eps || unavailable], ["Dividend Yield", v.dividend_yield || unavailable],
      ["P/E Ratio", v.pe_ratio || unavailable], ["Forward P/E", v.forward_pe_ratio || unavailable],
      ["P/B Ratio", v.pb_ratio || unavailable], ["P/S Ratio", v.ps_ratio || unavailable],
      ["Enterprise Value", v.enterprise_value || unavailable], ["EV / EBITDA", v.ev_ebitda || unavailable], ["EV / Sales", v.ev_sales || unavailable],
    ];
    const kpiHtml = rows.map(([label, m]) => `<div class="kpi-card"><div class="kpi-label">${label}</div><div class="kpi-value">${esc(m.display)}</div>${m.source ? `<div class="kpi-formula">${esc(m.source)}</div>` : ""}</div>`).join("");
    const sourceLine = v.price_source
      ? `<p class="source-line">Market data source: <strong>${esc(v.price_source_short || "market feed")}</strong> — last updated ${esc(v.last_updated || "unknown time")}. SEC-derived fundamentals (shares, equity, revenue, debt, cash) are combined with this live price; SEC data and market data are never blended into a single unlabeled number.</p>`
      : "";

    let barsHtml = "";
    if (v.pe_ratio && v.pe_ratio.available) {
      barsHtml = `<div class="chart-box"><h3>Current Valuation Multiples</h3>
        <div class="chart-canvas-wrap short"><canvas id="chart-valuation-bars"></canvas></div></div>`;
    }

    let historyHtml = "";
    if (vh.available) {
      const metricOptions = Object.keys(VALUATION_METRIC_LABELS).filter((k) => vh[k] && vh[k].available);
      historyHtml = `<div class="chart-box">
        <h3>Historical Valuation</h3>
        <p class="chart-sub">${esc(vh.note || "")} (${vh.years_of_data} fiscal years of real data)</p>
        <div class="chart-controls">
          <div class="control-group">
            <label>Metric</label>
            <select class="chart-type-select" id="valuation-metric-select">
              ${metricOptions.map((k) => `<option value="${k}"${k === valuationHistoryMetric ? " selected" : ""}>${VALUATION_METRIC_LABELS[k]}</option>`).join("")}
            </select>
          </div>
        </div>
        <div class="chart-canvas-wrap short"><canvas id="chart-valuation-history"></canvas></div>
        <div class="metric-grid" id="valuation-history-stats"></div>
      </div>`;
    } else {
      historyHtml = `<div class="chart-box"><h3>Historical Valuation</h3><p style="color:var(--muted)">Data unavailable — ${esc(vh.reason || "insufficient historical price/fundamental overlap")}.</p></div>`;
    }

    let bbbHtml = "";
    if (bbb.available) {
      bbbHtml = `<div class="chart-box">
        <h3>Bull / Base / Bear Valuation</h3>
        <p class="chart-sub">${esc(bbb.methodology)}</p>
        <div class="metric-grid">
          <div class="kpi-card"><div class="kpi-label">Bear</div><div class="kpi-value">$${bbb.bear.price_target}</div><div class="kpi-formula">${esc(bbb.bear.assumption)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Base</div><div class="kpi-value">$${bbb.base.price_target}</div><div class="kpi-formula">${esc(bbb.base.assumption)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Bull</div><div class="kpi-value">$${bbb.bull.price_target}</div><div class="kpi-formula">${esc(bbb.bull.assumption)}</div></div>
          <div class="kpi-card"><div class="kpi-label">Current Price</div><div class="kpi-value">$${bbb.current_price}</div></div>
        </div>
      </div>`;
    } else {
      bbbHtml = `<div class="chart-box"><h3>Bull / Base / Bear Valuation</h3><p style="color:var(--muted)">Data unavailable — ${esc(bbb.reason || "requires current EPS and a historical P/E series")}.</p></div>`;
    }

    el.innerHTML = `
      <p style="color:var(--muted)">${v.note ? esc(v.note) : "Live price via " + esc(v.price_source || "market feed") + ". Combined with SEC-reported fundamentals (never estimated)."}</p>
      <div class="metric-grid">${kpiHtml}</div>
      ${sourceLine}
      ${barsHtml}
      ${historyHtml}
      ${bbbHtml}
    `;

    if (v.pe_ratio && v.pe_ratio.available && window.Chart) {
      const colors = chartColors();
      const labels = [], values = [];
      if (v.pe_ratio.available) { labels.push("P/E"); values.push(v.pe_ratio.value); }
      if (v.pb_ratio && v.pb_ratio.available) { labels.push("P/B"); values.push(v.pb_ratio.value); }
      if (v.ps_ratio && v.ps_ratio.available) { labels.push("P/S"); values.push(v.ps_ratio.value); }
      if (v.ev_ebitda && v.ev_ebitda.available) { labels.push("EV/EBITDA"); values.push(v.ev_ebitda.value); }
      getOrCreateChart(document.getElementById("chart-valuation-bars"), {
        type: "bar",
        data: { labels, datasets: [{ data: values, backgroundColor: colors.brand, borderRadius: 6 }] },
        options: {
          indexAxis: "y",
          plugins: { legend: { display: false } },
          scales: { x: { ticks: { color: colors.muted }, grid: { color: colors.border } }, y: { ticks: { color: colors.text }, grid: { display: false } } },
        },
      });
    }

    if (vh.available) {
      const select = document.getElementById("valuation-metric-select");
      if (select) {
        select.addEventListener("change", (e) => {
          valuationHistoryMetric = e.target.value;
          drawValuationHistoryChart(vh);
        });
      }
      drawValuationHistoryChart(vh);
    }
  }

  function drawValuationHistoryChart(vh) {
    const canvas = document.getElementById("chart-valuation-history");
    const statsEl = document.getElementById("valuation-history-stats");
    if (!canvas || !window.Chart) return;
    const metric = vh[valuationHistoryMetric] && vh[valuationHistoryMetric].available ? valuationHistoryMetric : Object.keys(VALUATION_METRIC_LABELS).find((k) => vh[k] && vh[k].available);
    if (!metric) return;
    const rows = vh.series.filter((r) => metric in r).slice().reverse();
    const labels = rows.map((r) => r.period_end);
    const values = rows.map((r) => r[metric]);
    const stats = vh[metric];
    const colors = chartColors();
    getOrCreateChart(canvas, {
      type: "line",
      data: {
        labels,
        datasets: [
          { label: VALUATION_METRIC_LABELS[metric] + " (actual, per fiscal year)", data: values, borderColor: colors.brand, backgroundColor: colors.brand, tension: 0.25, pointRadius: 4 },
          ...(stats.avg_5y ? [{ label: "5Y Average", data: labels.map(() => stats.avg_5y), borderColor: colors.watch, borderDash: [6, 4], pointRadius: 0 }] : []),
          ...(stats.avg_10y ? [{ label: "10Y Average", data: labels.map(() => stats.avg_10y), borderColor: colors.muted, borderDash: [2, 3], pointRadius: 0 }] : []),
        ],
      },
      options: {
        plugins: { legend: { labels: { color: colors.text } } },
        scales: { x: { ticks: { color: colors.muted }, grid: { color: colors.border } }, y: { ticks: { color: colors.muted }, grid: { color: colors.border } } },
      },
    });
    if (statsEl) {
      const premium5 = stats.premium_discount_vs_5y_pct;
      const premium10 = stats.premium_discount_vs_10y_pct;
      statsEl.innerHTML = `
        <div class="kpi-card"><div class="kpi-label">Current ${VALUATION_METRIC_LABELS[metric]}</div><div class="kpi-value">${stats.current}x</div></div>
        <div class="kpi-card"><div class="kpi-label">5Y Average</div><div class="kpi-value">${stats.avg_5y ?? NA}x</div></div>
        <div class="kpi-card"><div class="kpi-label">10Y Average</div><div class="kpi-value">${stats.avg_10y ?? NA}x</div></div>
        <div class="kpi-card"><div class="kpi-label">vs 5Y Avg</div><div class="kpi-value">${premium5 != null ? (premium5 > 0 ? "+" : "") + premium5 + "%" : NA}</div><div class="kpi-formula">${premium5 != null ? (premium5 > 0 ? "Premium" : "Discount") + " to historical average" : ""}</div></div>
      `;
    }
  }

  // ---------- Market Chart tab (real historical price data) ----------
  const HISTORY_RANGES = ["3M", "6M", "YTD", "1Y", "3Y", "5Y", "10Y", "MAX"];
  const CHART_TYPES = ["Line", "Area", "Bar", "Scatter", "Histogram", "Candlestick"];
  const PANEL_SIZES = ["Small", "Medium", "Large", "Full"];
  let marketState = { range: "1Y", type: "Line", size: "Medium" };
  let marketChartInstance = null;

  function renderMarketTab(data) {
    const el = document.getElementById("tab-market");
    el.innerHTML = `
      <div class="chart-box panel-size-${marketState.size.toLowerCase()}" id="market-panel">
        <h3>Market Price History</h3>
        <p class="chart-sub">Real historical closing prices for ${esc(data.ticker)} — never demo or static values. Choose a timeframe, chart type, and panel size below; each change re-fetches or redraws immediately.</p>
        <div class="chart-controls">
          <div class="control-group">
            <label>Timeframe</label>
            ${HISTORY_RANGES.map((r) => `<button type="button" class="tf-btn${r === marketState.range ? " active" : ""}" data-range="${r}">${r}</button>`).join("")}
          </div>
          <div class="control-group">
            <label>Chart Type</label>
            <select class="chart-type-select" id="market-type-select">
              ${CHART_TYPES.map((t) => `<option value="${t}"${t === marketState.type ? " selected" : ""}>${t}</option>`).join("")}
            </select>
          </div>
          <div class="control-group">
            <label>Panel Size</label>
            ${PANEL_SIZES.map((s) => `<button type="button" class="size-btn${s === marketState.size ? " active" : ""}" data-size="${s}">${s}</button>`).join("")}
          </div>
        </div>
        <div class="chart-canvas-wrap" id="market-canvas-wrap"><canvas id="chart-market-price"></canvas></div>
        <p class="source-line" id="market-source-line">Loading price history…</p>
      </div>
    `;

    const panel = document.getElementById("market-panel");
    panel.querySelectorAll(".tf-btn").forEach((btn) => btn.addEventListener("click", () => {
      marketState.range = btn.dataset.range;
      panel.querySelectorAll(".tf-btn").forEach((b) => b.classList.toggle("active", b === btn));
      loadAndDrawMarket(data.ticker);
    }));
    document.getElementById("market-type-select").addEventListener("change", (e) => {
      marketState.type = e.target.value;
      loadAndDrawMarket(data.ticker, true);
    });
    panel.querySelectorAll(".size-btn").forEach((btn) => btn.addEventListener("click", () => {
      marketState.size = btn.dataset.size;
      panel.className = "chart-box panel-size-" + marketState.size.toLowerCase();
      panel.querySelectorAll(".size-btn").forEach((b) => b.classList.toggle("active", b === btn));
      if (marketChartInstance) marketChartInstance.resize();
    }));

    loadAndDrawMarket(data.ticker);
  }

  let marketFetchCache = {};
  async function loadAndDrawMarket(ticker, skipFetch) {
    const sourceLine = document.getElementById("market-source-line");
    const cacheKey = ticker + ":" + marketState.range;
    let payload = marketFetchCache[cacheKey];
    if (!payload || !skipFetch) {
      if (!payload) {
        sourceLine.textContent = "Loading price history…";
        try {
          const resp = await fetch(`/api/price-history/${encodeURIComponent(ticker)}?range=${encodeURIComponent(marketState.range)}`);
          payload = await resp.json();
          marketFetchCache[cacheKey] = payload;
        } catch (err) {
          payload = { available: false, reason: err.message };
        }
      }
    }
    drawMarketChart(payload, sourceLine);
  }

  function drawMarketChart(payload, sourceLine) {
    const canvasWrap = document.getElementById("market-canvas-wrap");
    if (!canvasWrap) return;
    if (marketChartInstance) { marketChartInstance.destroy(); marketChartInstance = null; }
    canvasWrap.innerHTML = '<canvas id="chart-market-price"></canvas>';
    const canvas = document.getElementById("chart-market-price");

    if (!payload || !payload.available) {
      canvasWrap.innerHTML = `<p style="padding:20px;color:var(--muted)">Data unavailable — ${esc((payload && payload.reason) || "the market-data provider could not be reached")}.</p>`;
      sourceLine.textContent = "Market data source: unavailable for this request.";
      return;
    }
    if (!window.Chart) {
      canvasWrap.innerHTML = `<p style="padding:20px;color:var(--muted)">Charting library failed to load.</p>`;
      return;
    }

    const colors = chartColors();
    const points = payload.points;
    const labels = points.map((p) => p.date);
    const closes = points.map((p) => p.close);
    let chart;

    let effectiveType = marketState.type;
    let candlestickUnavailableNote = "";
    if (effectiveType === "Candlestick" && !Chart.registry.controllers.get("candlestick")) {
      effectiveType = "Line";
      candlestickUnavailableNote = " (Candlestick plugin failed to load — showing Line instead.)";
    }

    if (effectiveType === "Candlestick") {
      chart = new Chart(canvas, {
        type: "candlestick",
        data: { datasets: [{ label: payload.range, data: points.map((p) => ({ x: new Date(p.date).getTime(), o: p.open, h: p.high, l: p.low, c: p.close })) }] },
        options: {
          plugins: { legend: { display: false } },
          scales: { x: { type: "time", ticks: { color: colors.muted }, grid: { color: colors.border } }, y: { ticks: { color: colors.muted }, grid: { color: colors.border } } },
        },
      });
    }
    if (!chart && effectiveType === "Histogram") {
      const returns = [];
      for (let i = 1; i < closes.length; i++) {
        if (closes[i - 1]) returns.push(((closes[i] - closes[i - 1]) / closes[i - 1]) * 100);
      }
      const bins = 16;
      const min = Math.min(...returns), max = Math.max(...returns);
      const width = (max - min) / bins || 1;
      const counts = new Array(bins).fill(0);
      returns.forEach((r) => { let idx = Math.floor((r - min) / width); if (idx >= bins) idx = bins - 1; if (idx < 0) idx = 0; counts[idx]++; });
      const binLabels = counts.map((_, i) => `${(min + i * width).toFixed(1)}%`);
      chart = new Chart(canvas, {
        type: "bar",
        data: { labels: binLabels, datasets: [{ label: "Daily return frequency", data: counts, backgroundColor: colors.brand, borderRadius: 4 }] },
        options: {
          plugins: { legend: { display: false } },
          scales: { x: { ticks: { color: colors.muted, maxRotation: 60 }, grid: { display: false } }, y: { ticks: { color: colors.muted }, grid: { color: colors.border } } },
        },
      });
    }
    if (!chart && effectiveType === "Scatter") {
      chart = new Chart(canvas, {
        type: "scatter",
        data: { datasets: [{ label: "Close", data: points.map((p, i) => ({ x: i, y: p.close })), backgroundColor: colors.brand }] },
        options: {
          plugins: { legend: { display: false } },
          scales: {
            x: { ticks: { color: colors.muted, callback: (v) => labels[v] || "" }, grid: { display: false } },
            y: { ticks: { color: colors.muted }, grid: { color: colors.border } },
          },
        },
      });
    }
    if (!chart && effectiveType === "Bar") {
      chart = new Chart(canvas, {
        type: "bar",
        data: { labels, datasets: [{ label: "Close", data: closes, backgroundColor: colors.brand }] },
        options: {
          plugins: { legend: { display: false } },
          scales: { x: { ticks: { color: colors.muted, maxTicksLimit: 12 }, grid: { display: false } }, y: { ticks: { color: colors.muted }, grid: { color: colors.border } } },
        },
      });
    }
    if (!chart) {
      // Line or Area (default fallback too)
      const isArea = effectiveType === "Area";
      chart = new Chart(canvas, {
        type: "line",
        data: { labels, datasets: [{ label: "Close", data: closes, borderColor: colors.brand, backgroundColor: isArea ? colors.brand + "33" : "transparent", fill: isArea, tension: 0.15, pointRadius: 0 }] },
        options: {
          plugins: { legend: { display: false } },
          scales: { x: { ticks: { color: colors.muted, maxTicksLimit: 12 }, grid: { display: false } }, y: { ticks: { color: colors.muted }, grid: { color: colors.border } } },
        },
      });
    }
    marketChartInstance = chart;
    window.__forgeCharts.push(chart);
    sourceLine.textContent = `Market data source: ${payload.source || "Yahoo Finance"} — fetched ${payload.fetched_at || "just now"} — ${points.length} data points (${payload.interval || "daily"} interval).${candlestickUnavailableNote}`;
  }

  // ---------- Risk tab ----------
  function renderRiskTab(data) {
    const el = document.getElementById("tab-risk");
    const altman = data.altman;
    const de = (data.score.rules.find((r) => r.id === "debt_to_equity")) || {};
    const ic = (data.score.rules.find((r) => r.id === "interest_coverage")) || {};

    let altmanHtml;
    if (altman.available) {
      const zoneColor = altman.zone === "SAFE" ? "var(--pass)" : altman.zone === "GREY" ? "var(--watch)" : "var(--fail)";
      altmanHtml = `<div class="chart-box">
        <h3>Altman Z-Score: <span style="color:${zoneColor}">${altman.score} — ${esc(altman.zone_label)}</span></h3>
        <p class="chart-sub"><strong>${esc(altman.model)}.</strong> ${esc(altman.model_note)}</p>
        <div class="metric-grid">
          <div class="kpi-card"><div class="kpi-label">Working Capital / Assets</div><div class="kpi-value">${altman.components.x1_working_capital_to_assets}</div></div>
          <div class="kpi-card"><div class="kpi-label">Retained Earnings / Assets</div><div class="kpi-value">${altman.components.x2_retained_earnings_to_assets}</div></div>
          <div class="kpi-card"><div class="kpi-label">EBIT / Assets</div><div class="kpi-value">${altman.components.x3_ebit_to_assets}</div></div>
          <div class="kpi-card"><div class="kpi-label">Equity / Liabilities</div><div class="kpi-value">${altman.components.x4_equity_to_liabilities}</div></div>
        </div>
        ${altman.missing_components && altman.missing_components.length ? `<p class="kpi-formula">Computed with $0 assumed for unavailable components: ${esc(altman.missing_components.join(", "))}.</p>` : ""}
        ${altman.classic_model ? `<p class="kpi-explain"><strong>${esc(altman.classic_model.model)}:</strong> ${altman.classic_model.score} (${esc(altman.classic_model.zone_label)}) — ${esc(altman.classic_model.model_note)}</p>` : ""}
      </div>`;
    } else {
      altmanHtml = `<div class="chart-box"><h3>Altman Z-Score</h3><p>${NA}. ${esc(altman.reason || "")}</p></div>`;
    }

    el.innerHTML = `
      ${altmanHtml}
      <div class="metric-grid">
        <div class="kpi-card"><div class="kpi-label">Piotroski F-Score</div><div class="kpi-value">${data.piotroski.score} / ${data.piotroski.scored_out_of}</div></div>
        <div class="kpi-card"><div class="kpi-label">Debt / Equity</div><div class="kpi-value">${esc(de.value || NA)}</div><span class="kpi-status status-${de.status}">${de.status || ""}</span></div>
        <div class="kpi-card"><div class="kpi-label">Interest Coverage</div><div class="kpi-value">${esc(ic.value || NA)}</div><span class="kpi-status status-${ic.status}">${ic.status || ""}</span></div>
        <div class="kpi-card"><div class="kpi-label">Lease Exposure</div><div class="kpi-value">${data.lease_summary.available ? fmtUsd(data.lease_summary.grand_total) : NA}</div></div>
      </div>
    `;
  }

  // ---------- Timeline tab ----------
  const TIMELINE_METRICS = [
    { key: "revenue", label: "Revenue", color: "brand" }, { key: "net_income", label: "Net Income", color: "pass" },
    { key: "cash", label: "Cash", color: "accent" }, { key: "total_debt", label: "Total Debt", color: "fail" },
    { key: "equity", label: "Equity", color: "watch" }, { key: "free_cash_flow", label: "Free Cash Flow", color: "brand" },
    { key: "retained_earnings", label: "Retained Earnings", color: "pass" }, { key: "buybacks", label: "Buybacks", color: "fail" },
    { key: "operating_cash_flow", label: "Operating Cash Flow", color: "brand" }, { key: "capital_expenditures", label: "CapEx", color: "fail" },
    { key: "eps", label: "EPS", color: "watch" }, { key: "net_margin_pct", label: "Net Margin %", color: "pass" },
    { key: "gross_margin_pct", label: "Gross Margin %", color: "accent" },
  ];
  let timelineActive = new Set(["revenue", "net_income", "cash", "total_debt"]);
  let timelineFrequency = "annual";

  function filterTimeline(timeline, tf) {
    if (!timeline || !timeline.length) return [];
    if (tf === "MAX" || !tf) return timeline;
    const n = parseInt(tf, 10);
    return timeline.slice(-n);
  }

  function buildTimeframeButtons(containerId, timeline, onChange, options) {
    const opts = options || ["3Y", "5Y", "10Y", "MAX"];
    const container = document.getElementById(containerId);
    if (!container) return;
    container.innerHTML = opts.map((o, i) => `<button class="tf-btn${i === opts.length - 1 ? " active" : ""}" data-tf="${o}">${o}</button>`).join("");
    container.querySelectorAll(".tf-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        container.querySelectorAll(".tf-btn").forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const tf = btn.dataset.tf.replace("Y", "");
        onChange(filterTimeline(timeline, tf === "MAX" ? "MAX" : tf));
      });
    });
  }

  async function fetchTimelineFrequency(freq) {
    if (timelineCache[freq]) return timelineCache[freq];
    if (!currentTicker) return [];
    try {
      const resp = await fetch(`/api/analyze/${encodeURIComponent(currentTicker)}?frequency=${freq}`);
      const body = await safeJson(resp);
      const tl = (resp.ok && body && body.timeline) || [];
      timelineCache[freq] = tl;
      return tl;
    } catch (e) {
      return [];
    }
  }

  function renderTimelineTab(data) {
    const el = document.getElementById("tab-timeline");
    if (!data.timeline || !data.timeline.length) {
      el.innerHTML = `<div class="chart-box"><p>${NA} — not enough consecutive annual filings were found to build a multi-year timeline.</p></div>`;
      return;
    }
    const toggles = TIMELINE_METRICS.map((m) => `<label style="margin-right:14px;font-size:0.88rem;cursor:pointer">
      <input type="checkbox" data-metric="${m.key}" ${timelineActive.has(m.key) ? "checked" : ""}> ${m.label}</label>`).join("");

    el.innerHTML = `
      <div class="chart-box">
        <h3>Financial Timeline</h3>
        <div style="display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin-bottom:8px">
          <div class="timeframe-controls" id="timeline-tf"></div>
          <div class="freq-toggle" id="timeline-freq">
            <button class="tf-btn${timelineFrequency === "annual" ? " active" : ""}" data-freq="annual">Annual</button>
            <button class="tf-btn${timelineFrequency === "quarterly" ? " active" : ""}" data-freq="quarterly">Quarterly</button>
          </div>
        </div>
        <div style="margin-bottom:10px">${toggles}</div>
        <p class="chart-sub" id="timeline-loading" hidden>Loading quarterly data...</p>
        <div class="chart-canvas-wrap"><canvas id="chart-timeline"></canvas></div>
      </div>
      <div class="chart-box">
        <h3>FORGE Health Score Over Time</h3>
        <p class="chart-sub">${data.trend_story ? "" : "Not enough historical SEC data to compute a score trend."}</p>
        <div class="chart-canvas-wrap short"><canvas id="chart-score-history"></canvas></div>
      </div>
    `;

    el.querySelectorAll('input[type="checkbox"][data-metric]').forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) timelineActive.add(cb.dataset.metric); else timelineActive.delete(cb.dataset.metric);
        drawTimelineChart(getCurrentTimelineFilter(timelineCache[timelineFrequency] || data.timeline));
      });
    });

    function activeTimelineFull() { return timelineCache[timelineFrequency] || data.timeline; }

    buildTimeframeButtons("timeline-tf", activeTimelineFull(), (filtered) => drawTimelineChart(filtered));
    drawTimelineChart(filterTimeline(activeTimelineFull(), "MAX"));

    el.querySelectorAll('#timeline-freq button[data-freq]').forEach((btn) => {
      btn.addEventListener("click", async () => {
        const freq = btn.dataset.freq;
        if (freq === timelineFrequency) return;
        el.querySelectorAll('#timeline-freq button').forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const loadingEl = document.getElementById("timeline-loading");
        if (freq === "quarterly" && !timelineCache.quarterly) { loadingEl.hidden = false; }
        const tl = await fetchTimelineFrequency(freq);
        loadingEl.hidden = true;
        timelineFrequency = freq;
        buildTimeframeButtons("timeline-tf", tl, (filtered) => drawTimelineChart(filtered));
        drawTimelineChart(filterTimeline(tl, "MAX"));
      });
    });

    drawScoreHistoryChart(data.historical_scores);
  }

  let lastTimelineFilter = null;
  function getCurrentTimelineFilter(full) { return lastTimelineFilter || full; }

  const TIMELINE_PCT_OR_PER_SHARE = new Set(["eps", "net_margin_pct", "gross_margin_pct"]);

  function drawTimelineChart(timeline) {
    lastTimelineFilter = timeline;
    const el = document.getElementById("chart-timeline");
    if (!el || !window.Chart) return;
    const colors = chartColors();
    const colorMap = { brand: colors.brand, pass: colors.pass, accent: cssVar("--accent"), fail: colors.fail, watch: colors.watch };
    const labels = timeline.map((t) => t.fiscal_period && t.fiscal_period !== "FY" ? `${t.fiscal_period} ${t.fiscal_year || ""}` : "FY" + (t.fiscal_year || t.period_end));
    const activeMetrics = TIMELINE_METRICS.filter((m) => timelineActive.has(m.key));
    const usesSecondAxis = activeMetrics.some((m) => TIMELINE_PCT_OR_PER_SHARE.has(m.key));
    const datasets = activeMetrics.map((m) => ({
      label: m.label, data: timeline.map((t) => t[m.key]), borderColor: colorMap[m.color], backgroundColor: colorMap[m.color], tension: 0.3, fill: false,
      yAxisID: TIMELINE_PCT_OR_PER_SHARE.has(m.key) ? "y1" : "y",
    }));
    const scales = {
      y: { ticks: { color: colors.muted, callback: (v) => fmtUsd(v) }, grid: { color: colors.border } },
      x: { ticks: { color: colors.text }, grid: { display: false } },
    };
    if (usesSecondAxis) {
      scales.y1 = { position: "right", ticks: { color: colors.muted }, grid: { display: false } };
    }
    const chart = getOrCreateChart(el, {
      type: "line",
      data: { labels, datasets },
      options: {
        plugins: { legend: { labels: { color: colors.text } } },
        scales,
      },
    });
  }

  function drawScoreHistoryChart(history) {
    const el = document.getElementById("chart-score-history");
    if (!el || !window.Chart || !history || !history.length) return;
    const colors = chartColors();
    const chart = getOrCreateChart(el, {
      type: "line",
      data: {
        labels: history.map((h) => "FY" + h.fiscal_year),
        datasets: [{ label: "FORGE Financial Health Score", data: history.map((h) => h.overall_score), borderColor: colors.brand, backgroundColor: colors.brand + "33", fill: true, tension: 0.3 }],
      },
      options: {
        plugins: { legend: { display: false } },
        scales: { y: { min: 0, max: 100, ticks: { color: colors.muted }, grid: { color: colors.border } }, x: { ticks: { color: colors.text }, grid: { display: false } } },
      },
    });
  }

  // ---------- 3D Model ----------
  const DIMENSION_DEFS = [
    { key: "liquidity", label: "Liquidity", metrics: (d) => [
      ["Cash", fmtUsd(d.quarterly_facts.cash_and_equivalents.value)],
      ["Short-Term Investments", d.quarterly_facts.short_term_investments.available ? fmtUsd(d.quarterly_facts.short_term_investments.value) : NA],
      ["Net Liquidity Rule", (d.score.rules.find((r) => r.id === "liquidity") || {}).status || NA],
    ]},
    { key: "leverage", label: "Leverage", metrics: (d) => [
      ["Debt / Equity", (d.score.rules.find((r) => r.id === "debt_to_equity") || {}).value || NA],
      ["Total Liabilities", d.quarterly_facts.total_liabilities.available ? fmtUsd(d.quarterly_facts.total_liabilities.value) : NA],
    ]},
    { key: "profitability", label: "Profitability", metrics: (d) => [
      ["Net Margin", d.quality_metrics.net_margin.display], ["ROE", d.quality_metrics.roe.display], ["ROA", d.quality_metrics.roa.display],
    ]},
    { key: "growth", label: "Growth", metrics: (d) => [
      ["Revenue Growth", d.quality_metrics.revenue_growth.display], ["Retained Earnings Rule", (d.score.rules.find((r) => r.id === "retained_earnings_growth") || {}).status || NA],
    ]},
    { key: "cash_generation", label: "Cash Generation", metrics: (d) => [
      ["Free Cash Flow", d.quality_metrics.free_cash_flow.display], ["FCF Margin", d.quality_metrics.fcf_margin.display],
    ]},
    { key: "risk", label: "Risk", metrics: (d) => [
      ["Altman Z-Score", d.altman.available ? `${d.altman.score} (${d.altman.zone_label})` : NA],
      ["Piotroski F-Score", `${d.piotroski.score} / ${d.piotroski.scored_out_of}`],
    ]},
    { key: "valuation", label: "Valuation", metrics: (d) => [
      ["P/E", (d.valuation.pe_ratio && d.valuation.pe_ratio.display) || NA],
      ["P/B", (d.valuation.pb_ratio && d.valuation.pb_ratio.display) || NA],
      ["EV/EBITDA", (d.valuation.ev_ebitda && d.valuation.ev_ebitda.display) || NA],
    ]},
  ];

  function dimensionScore(key, data) {
    const forge = data.forge.pillars;
    if (key === "leverage" || key === "liquidity") return forge.financial_health.score;
    if (key === "profitability" || key === "growth" || key === "cash_generation") return forge.financial_quality.score;
    if (key === "risk") return forge.risk.score;
    if (key === "valuation") return forge.valuation.score;
    return null;
  }

  let model3dState = null;
  function renderModel3D(data) {
    const el = document.getElementById("tab-model3d");
    el.innerHTML = `
      <div class="chart-box">
        <h3>FORGE 3D Financial Model</h3>
        <p class="model3d-hint">Drag to rotate, scroll to zoom, click a bar to see the metrics behind that dimension.</p>
        <div class="model3d-wrap">
          <div id="model3d-canvas-holder"></div>
          <div id="model3d-detail"><p style="color:var(--muted)">Click a bar to inspect that dimension.</p></div>
        </div>
      </div>
    `;
    if (!window.THREE) {
      document.getElementById("model3d-canvas-holder").innerHTML = "<p style='padding:16px;color:var(--muted)'>3D library failed to load.</p>";
      return;
    }
    setup3D(data);
  }

  function setup3D(data) {
    const holder = document.getElementById("model3d-canvas-holder");
    const detail = document.getElementById("model3d-detail");
    const width = holder.clientWidth, height = holder.clientHeight;

    const scene = new THREE.Scene();
    const isDark = root.getAttribute("data-theme") === "dark";
    scene.background = new THREE.Color(isDark ? 0x171e2b : 0xf5f6f8);

    const camera = new THREE.PerspectiveCamera(45, width / height, 0.1, 100);
    camera.position.set(6, 6, 9);

    const renderer = new THREE.WebGLRenderer({ antialias: true });
    renderer.setSize(width, height);
    holder.innerHTML = "";
    holder.appendChild(renderer.domElement);

    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.minDistance = 4; controls.maxDistance = 20;

    scene.add(new THREE.AmbientLight(0xffffff, 0.7));
    const dirLight = new THREE.DirectionalLight(0xffffff, 0.8);
    dirLight.position.set(5, 10, 7);
    scene.add(dirLight);

    const grid = new THREE.GridHelper(8, 8, 0x888888, isDark ? 0x2a3446 : 0xdde1e7);
    scene.add(grid);

    const n = DIMENSION_DEFS.length;
    const radius = 3;
    const bars = [];
    const barColor = (score) => {
      if (score === null || score === undefined) return 0x93a0b8;
      if (score >= 80) return 0x2ecc71;
      if (score >= 60) return 0x2ecc71;
      if (score >= 40) return 0xf5b642;
      return 0xe05656;
    };

    DIMENSION_DEFS.forEach((dim, i) => {
      const angle = (i / n) * Math.PI * 2;
      const x = Math.cos(angle) * radius;
      const z = Math.sin(angle) * radius;
      const score = dimensionScore(dim.key, currentData);
      const h = Math.max(0.3, ((score === null ? 10 : score) / 100) * 4);
      const geo = new THREE.BoxGeometry(0.7, h, 0.7);
      const mat = new THREE.MeshStandardMaterial({ color: barColor(score) });
      const bar = new THREE.Mesh(geo, mat);
      bar.position.set(x, h / 2, z);
      bar.userData = { dim };
      scene.add(bar);
      bars.push(bar);

      const canvas = document.createElement("canvas");
      canvas.width = 256; canvas.height = 64;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = isDark ? "#e7ebf2" : "#1b2430";
      ctx.font = "bold 34px Segoe UI";
      ctx.textAlign = "center";
      ctx.fillText(dim.label, 128, 44);
      const tex = new THREE.CanvasTexture(canvas);
      const labelMat = new THREE.SpriteMaterial({ map: tex, transparent: true });
      const sprite = new THREE.Sprite(labelMat);
      sprite.scale.set(2, 0.5, 1);
      sprite.position.set(x, h + 0.5, z);
      scene.add(sprite);
    });

    const raycaster = new THREE.Raycaster();
    const mouse = new THREE.Vector2();
    function showDetail(dim) {
      const rows = dim.metrics(currentData).map(([k, v]) => `<div style="display:flex;justify-content:space-between;padding:4px 0;border-top:1px solid var(--border)"><span style="color:var(--muted)">${esc(k)}</span><strong>${esc(v)}</strong></div>`).join("");
      detail.innerHTML = `<h4 style="margin-top:0">${esc(dim.label.toUpperCase())}</h4>${rows}`;
    }
    renderer.domElement.addEventListener("click", (ev) => {
      const rect = renderer.domElement.getBoundingClientRect();
      mouse.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
      mouse.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(mouse, camera);
      const hits = raycaster.intersectObjects(bars);
      if (hits.length) showDetail(hits[0].object.userData.dim);
    });

    let frameId;
    function animate() {
      frameId = requestAnimationFrame(animate);
      controls.update();
      renderer.render(scene, camera);
    }
    animate();

    if (model3dState && model3dState.frameId) cancelAnimationFrame(model3dState.frameId);
    model3dState = { frameId };
    window.addEventListener("resize", () => {
      if (!holder.isConnected) return;
      const w = holder.clientWidth, h = holder.clientHeight;
      if (w === 0 || h === 0) return;
      camera.aspect = w / h; camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    });
  }

  // ---------- Compare tab ----------
  function renderCompareTab(data) {
    const el = document.getElementById("tab-compare");
    el.innerHTML = `
      <div class="chart-box">
        <h3>Peer Comparison</h3>
        <p class="chart-sub">Compare up to 5 tickers side by side. Add the current company plus peers.</p>
        <div class="compare-input-row">
          <input id="compare-input" type="text" placeholder="e.g. MSFT" maxlength="10">
          <button id="compare-add-btn" type="button">Add</button>
          <button id="compare-run-btn" type="button">Compare</button>
        </div>
        <div id="compare-chips" style="margin-bottom:10px"></div>
        <div id="compare-output"></div>
      </div>
    `;
    const chipsEl = document.getElementById("compare-chips");
    const tickers = new Set([data.ticker]);
    function renderChips() {
      chipsEl.innerHTML = Array.from(tickers).map((t) => `<span class="kpi-status status-PASS" style="margin-right:6px">${esc(t)}</span>`).join("");
    }
    renderChips();
    document.getElementById("compare-add-btn").addEventListener("click", () => {
      const v = document.getElementById("compare-input").value.trim().toUpperCase();
      if (v && tickers.size < 5) { tickers.add(v); renderChips(); document.getElementById("compare-input").value = ""; }
    });
    document.getElementById("compare-run-btn").addEventListener("click", async () => {
      const out = document.getElementById("compare-output");
      out.innerHTML = "<p>Loading comparison...</p>";
      try {
        const resp = await fetch(`/api/compare?tickers=${Array.from(tickers).join(",")}`);
        const body = await resp.json();
        renderCompareResults(body.companies, out);
      } catch (e) {
        out.innerHTML = `<p>Comparison failed: ${esc(e.message)}</p>`;
      }
    });
  }

  function renderCompareResults(companies, out) {
    const valid = companies.filter((c) => !c.error);
    if (!valid.length) { out.innerHTML = "<p>No valid companies to compare.</p>"; return; }
    const rows = [
      ["FORGE Score", (c) => (c.forge.forge_score === null ? "N/A" : Math.round(c.forge.forge_score))],
      ["Financial Health", (c) => (c.forge.pillars.financial_health.score === null ? "N/A" : Math.round(c.forge.pillars.financial_health.score))],
      ["Financial Quality", (c) => (c.forge.pillars.financial_quality.score === null ? "N/A" : Math.round(c.forge.pillars.financial_quality.score))],
      ["Valuation", (c) => (c.forge.pillars.valuation.score === null ? "N/A" : Math.round(c.forge.pillars.valuation.score))],
      ["Risk", (c) => (c.forge.pillars.risk.score === null ? "N/A" : Math.round(c.forge.pillars.risk.score))],
      ["P/E", (c) => (c.valuation.pe_ratio && c.valuation.pe_ratio.display) || NA],
      ["P/B", (c) => (c.valuation.pb_ratio && c.valuation.pb_ratio.display) || NA],
      ["P/S", (c) => (c.valuation.ps_ratio && c.valuation.ps_ratio.display) || NA],
      ["ROE", (c) => c.quality_metrics.roe.display],
      ["FCF Margin", (c) => c.quality_metrics.fcf_margin.display],
      ["Debt/Equity", (c) => (c.score.rules.find((r) => r.id === "debt_to_equity") || {}).value || NA],
      ["Piotroski", (c) => `${c.piotroski.score}/${c.piotroski.scored_out_of}`],
      ["Altman Z", (c) => (c.altman.available ? c.altman.score : NA)],
    ];
    let html = `<table class="compare-table"><thead><tr><th>Metric</th>${valid.map((c) => `<th>${esc(c.ticker)}</th>`).join("")}</tr></thead><tbody>`;
    rows.forEach(([label, fn]) => {
      html += `<tr><td>${label}</td>${valid.map((c) => `<td>${esc(fn(c))}</td>`).join("")}</tr>`;
    });
    html += "</tbody></table>";

    html += `<div class="chart-canvas-wrap" style="margin-top:20px"><canvas id="chart-compare-radar"></canvas></div>`;
    out.innerHTML = html;

    if (window.Chart) {
      const colors = chartColors();
      const palette = [colors.brand, colors.pass, colors.watch, colors.fail, cssVar("--accent")];
      const chart = getOrCreateChart(document.getElementById("chart-compare-radar"), {
        type: "radar",
        data: {
          labels: ["Financial Health", "Financial Quality", "Valuation", "Risk"],
          datasets: valid.map((c, i) => ({
            label: c.ticker,
            data: [c.forge.pillars.financial_health.score, c.forge.pillars.financial_quality.score, c.forge.pillars.valuation.score, c.forge.pillars.risk.score].map((v) => v || 0),
            borderColor: palette[i % palette.length], backgroundColor: palette[i % palette.length] + "22",
          })),
        },
        options: {
          plugins: { legend: { labels: { color: colors.text } } },
          scales: { r: { min: 0, max: 100, ticks: { color: colors.muted, backdropColor: "transparent" }, grid: { color: colors.border }, angleLines: { color: colors.border }, pointLabels: { color: colors.text } } },
        },
      });
    }
  }

  // ---------- Data sources ----------
  function renderDataSources(data) {
    const el = document.getElementById("data-sources-content");
    let html = `<p><strong>SEC data:</strong> ${esc(data.data_source)}</p>`;
    html += `<p><strong>Market data:</strong> ${esc(data.market_data_source)}</p>`;
    html += `<p><strong>Scoring formula:</strong> ${esc(data.score.scoring_formula)}</p>`;
    html += `<p><strong>FORGE Score methodology:</strong> ${esc(data.forge.methodology_note)}</p>`;
    html += `<p><strong>SEC EDGAR filing index:</strong> <a href="${esc(data.sec_edgar_url)}" target="_blank" rel="noopener">${esc(data.sec_edgar_url)}</a></p>`;
    el.innerHTML = html;
  }

  // ---------- Tabs ----------
  function initTabs() {
    document.querySelectorAll(".tab-btn").forEach((btn) => {
      btn.onclick = () => {
        document.querySelectorAll(".tab-btn").forEach((b) => b.classList.remove("active"));
        document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
        btn.classList.add("active");
        document.getElementById("tab-" + btn.dataset.tab).classList.add("active");
        if (btn.dataset.tab === "model3d") {
          window.dispatchEvent(new Event("resize"));
        }
      };
    });
  }
})();

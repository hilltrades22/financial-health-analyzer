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
  function renderDashboard(data) {
    const el = document.getElementById("dashboard-card");
    const forge = data.forge || {};
    const badgeClass = "badge-" + slug(forge.label || "Insufficient Data");
    const meta = [];
    if (data.industry) meta.push(`Industry: ${esc(data.industry)}`);
    meta.push(`Latest Quarter: ${esc(data.latest_quarter.period_end || NA)}`);
    meta.push(`Latest Annual: FY${esc(data.latest_annual.fiscal_year || "?")} (${esc(data.latest_annual.period_end || NA)})`);
    if (data.valuation && data.valuation.price && data.valuation.price.value) {
      meta.push(`Price: $${data.valuation.price.value.toFixed(2)} (${esc(data.valuation.price.as_of || "")})`);
    }

    const pillars = forge.pillars || {};
    const pillarOrder = [
      ["financial_health", "Financial Health"], ["financial_quality", "Financial Quality"],
      ["valuation", "Valuation"], ["risk", "Risk"],
    ];
    const pillarHtml = pillarOrder.map(([key, label]) => {
      const p = pillars[key] || {};
      const val = p.score === null || p.score === undefined ? "N/A" : Math.round(p.score);
      const pct = p.score === null || p.score === undefined ? 0 : p.score;
      const color = p.score === null ? "var(--na)" : p.score >= 80 ? "var(--pass)" : p.score >= 60 ? "var(--pass)" : p.score >= 40 ? "var(--watch)" : "var(--fail)";
      return `<div class="pillar-card">
        <div class="pillar-name">${label}</div>
        <div class="pillar-value" style="color:${color}">${val}${p.score !== null ? "" : ""}</div>
        <div class="pillar-bar-track"><div class="pillar-bar-fill" style="width:${pct}%;background:${color}"></div></div>
      </div>`;
    }).join("");

    el.innerHTML = `
      <div class="dash-head">
        <div class="dash-identity">
          <h2>${esc(data.company_name)} <span class="dash-ticker">${esc(data.ticker)}</span></h2>
          <div class="dash-meta">${meta.map((m) => `<span>${m}</span>`).join("")}</div>
        </div>
        <div class="forge-score-box">
          <div class="forge-score-label">FORGE Score</div>
          <div class="forge-score-value">${forge.forge_score === null || forge.forge_score === undefined ? "N/A" : Math.round(forge.forge_score)}<span style="font-size:1.2rem;color:var(--muted)"> / 100</span></div>
          <div class="forge-score-badge ${badgeClass}">${esc(forge.label || "Insufficient Data")}</div>
        </div>
      </div>
      <div class="pillar-grid">${pillarHtml}</div>
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
    const chart = new Chart(c, {
      type: "doughnut",
      data: { datasets: [{ data: [clamped, 100 - clamped], backgroundColor: [color, colors.border], borderWidth: 0 }] },
      options: {
        circumference: 180, rotation: 270, cutout: "72%",
        plugins: { legend: { display: false }, tooltip: { enabled: false } },
        animation: { duration: 400 },
      },
    });
    window.__forgeCharts.push(chart);
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
      const chart = new Chart(cashDebtEl, {
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
      window.__forgeCharts.push(chart);
    }

    const leaseEl = document.getElementById("chart-lease");
    if (leaseEl && window.Chart && leaseSummary.available) {
      const chart = new Chart(leaseEl, {
        type: "doughnut",
        data: {
          labels: ["Current Lease Liabilities", "Long-Term Lease Liabilities"],
          datasets: [{ data: [leaseSummary.current_total, leaseSummary.noncurrent_total], backgroundColor: [colors.brand, colors.watch] }],
        },
        options: { plugins: { legend: { position: "bottom", labels: { color: colors.text } } } },
      });
      window.__forgeCharts.push(chart);
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
      const chart = new Chart(trendEl, {
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
      window.__forgeCharts.push(chart);
    }
    const fcfEl = document.getElementById("chart-fcf-trend");
    if (fcfEl && window.Chart) {
      const chart = new Chart(fcfEl, {
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
      window.__forgeCharts.push(chart);
    }
  }

  // ---------- Valuation tab ----------
  function renderValuationTab(data) {
    const el = document.getElementById("tab-valuation");
    const v = data.valuation;
    const rows = [
      ["Market Cap", v.market_cap], ["P/E Ratio", v.pe_ratio], ["P/B Ratio", v.pb_ratio], ["P/S Ratio", v.ps_ratio],
      ["Enterprise Value", v.enterprise_value], ["EV / EBITDA", v.ev_ebitda], ["EV / Sales", v.ev_sales],
    ];
    const kpiHtml = rows.map(([label, m]) => `<div class="kpi-card"><div class="kpi-label">${label}</div><div class="kpi-value">${esc(m.display)}</div></div>`).join("");

    let barsHtml = "";
    if (v.pe_ratio && v.pe_ratio.available) {
      barsHtml = `<div class="chart-box"><h3>Valuation Multiples</h3>
        <div class="chart-canvas-wrap short"><canvas id="chart-valuation-bars"></canvas></div></div>`;
    }

    el.innerHTML = `
      <p style="color:var(--muted)">${v.note ? esc(v.note) : "Live price via " + esc(v.price_source || "market feed") + ". Combined with SEC-reported fundamentals (never estimated)."}</p>
      <div class="metric-grid">${kpiHtml}</div>
      ${barsHtml}
    `;

    if (v.pe_ratio && v.pe_ratio.available && window.Chart) {
      const colors = chartColors();
      const labels = [], values = [];
      if (v.pe_ratio.available) { labels.push("P/E"); values.push(v.pe_ratio.value); }
      if (v.pb_ratio && v.pb_ratio.available) { labels.push("P/B"); values.push(v.pb_ratio.value); }
      if (v.ps_ratio && v.ps_ratio.available) { labels.push("P/S"); values.push(v.ps_ratio.value); }
      if (v.ev_ebitda && v.ev_ebitda.available) { labels.push("EV/EBITDA"); values.push(v.ev_ebitda.value); }
      const chart = new Chart(document.getElementById("chart-valuation-bars"), {
        type: "bar",
        data: { labels, datasets: [{ data: values, backgroundColor: colors.brand, borderRadius: 6 }] },
        options: {
          indexAxis: "y",
          plugins: { legend: { display: false } },
          scales: { x: { ticks: { color: colors.muted }, grid: { color: colors.border } }, y: { ticks: { color: colors.text }, grid: { display: false } } },
        },
      });
      window.__forgeCharts.push(chart);
    }
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
  ];
  let timelineActive = new Set(["revenue", "net_income", "cash", "total_debt"]);

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
        <div class="timeframe-controls" id="timeline-tf"></div>
        <div style="margin-bottom:10px">${toggles}</div>
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
        drawTimelineChart(getCurrentTimelineFilter(data.timeline));
      });
    });

    buildTimeframeButtons("timeline-tf", data.timeline, (filtered) => drawTimelineChart(filtered));
    drawTimelineChart(filterTimeline(data.timeline, "MAX"));
    drawScoreHistoryChart(data.historical_scores);
  }

  let lastTimelineFilter = null;
  function getCurrentTimelineFilter(full) { return lastTimelineFilter || full; }

  function drawTimelineChart(timeline) {
    lastTimelineFilter = timeline;
    const el = document.getElementById("chart-timeline");
    if (!el || !window.Chart) return;
    const colors = chartColors();
    const colorMap = { brand: colors.brand, pass: colors.pass, accent: cssVar("--accent"), fail: colors.fail, watch: colors.watch };
    const labels = timeline.map((t) => "FY" + (t.fiscal_year || t.period_end));
    const datasets = TIMELINE_METRICS.filter((m) => timelineActive.has(m.key)).map((m) => ({
      label: m.label, data: timeline.map((t) => t[m.key]), borderColor: colorMap[m.color], backgroundColor: colorMap[m.color], tension: 0.3, fill: false,
    }));
    const chart = new Chart(el, {
      type: "line",
      data: { labels, datasets },
      options: {
        plugins: { legend: { labels: { color: colors.text } } },
        scales: { y: { ticks: { color: colors.muted, callback: (v) => fmtUsd(v) }, grid: { color: colors.border } }, x: { ticks: { color: colors.text }, grid: { display: false } } },
      },
    });
    window.__forgeCharts.push(chart);
  }

  function drawScoreHistoryChart(history) {
    const el = document.getElementById("chart-score-history");
    if (!el || !window.Chart || !history || !history.length) return;
    const colors = chartColors();
    const chart = new Chart(el, {
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
    window.__forgeCharts.push(chart);
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
      ["P/E", d.valuation.pe_ratio.display], ["P/B", d.valuation.pb_ratio.display], ["EV/EBITDA", d.valuation.ev_ebitda.display],
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
      ["P/E", (c) => c.valuation.pe_ratio.display],
      ["P/B", (c) => c.valuation.pb_ratio.display],
      ["P/S", (c) => c.valuation.ps_ratio.display],
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
      const chart = new Chart(document.getElementById("chart-compare-radar"), {
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
      window.__forgeCharts.push(chart);
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
      };
    });
  }
})();

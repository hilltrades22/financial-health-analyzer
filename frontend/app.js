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

  // Every failure the user can actually hit gets a plain-English explanation
  // of what happened and what to do about it. The raw server detail is kept
  // available but tucked away - it is diagnostic, not an error message.
  function showError(ticker, status, body) {
    const detail = (body && body.detail) || "";
    const t = esc(ticker);
    let kind = "generic", title, message, hint = "";

    if (status === 404) {
      kind = "notfound";
      title = `No SEC filings found for "${t}"`;
      message = `SEC EDGAR has no registered company under that ticker. FORGE analyses companies that file with the SEC, `
        + `which includes US companies and foreign issuers listed here — but not funds, indices or crypto.`;
      hint = "Check the spelling, or try a ticker like AAPL, MSFT, JPM or TSM.";
    } else if (status === 429 || /rate.?limit/i.test(detail)) {
      kind = "throttle";
      title = "SEC is limiting requests right now";
      message = "SEC EDGAR asks automated tools to stay within a modest request rate, and we have hit that limit. "
        + "No data is missing — the request simply needs to wait.";
      hint = "Give it a minute and try again.";
    } else if (status === 504 || /did not complete|timed out/i.test(detail)) {
      kind = "timeout";
      title = `${t} took too long to analyse`;
      message = detail || "This company's SEC dataset is unusually large and could not be processed within the time limit.";
      hint = "Some long-established filers publish enormous datasets. Trying again may succeed once data is cached.";
    } else if (status === 503) {
      kind = "throttle";
      title = "SEC EDGAR is unavailable";
      message = "We could not reach SEC EDGAR, so there is nothing to analyse from. Rather than show partial or "
        + "estimated figures, FORGE shows nothing at all.";
      hint = "This is usually brief — please try again shortly.";
    } else {
      title = `Could not analyse "${t}"`;
      message = "Something went wrong while building this analysis. No partial or estimated figures are shown.";
      hint = "Try again, or try a different ticker.";
    }

    errorPanel.className = `error-panel is-${kind}`;
    errorPanel.innerHTML = `
      <p class="error-title">${title}</p>
      <p class="error-body">${esc(message)}</p>
      ${hint ? `<p class="error-hint">${esc(hint)}</p>` : ""}
      ${detail && kind === "generic" ? `<details class="error-hint"><summary>Technical detail</summary><p>${esc(detail)}</p></details>` : ""}
    `;
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
    // Structured story when the API provides it, plain text as a fallback.
    const storyEl = document.getElementById("financial-story");
    const sectionsHtml = storySectionsHtml(data);
    if (sectionsHtml) storyEl.innerHTML = sectionsHtml;
    else storyEl.textContent = data.financial_story;
    document.getElementById("trend-story").textContent = data.trend_story || "";
    renderHealthTab(data);
    renderQualityTab(data);
    renderValuationTab(data);
    renderMarketTab(data);
    renderRiskTab(data);
    renderTimelineTab(data);
    renderBusinessMixTab(data);
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

  // ---------- Company header + score hero ----------
  // The page has to answer, in order: what company is this, how healthy is
  // it, and why. The header carries identity, the dial carries the verdict,
  // and the readout beside it carries the reasoning - so the answer and its
  // justification are never more than one glance apart.

  const STATUS_ORDER = ["FAIL", "WATCH", "PASS", "NOT_APPLICABLE", "UNAVAILABLE"];

  function verdictTone(score, label) {
    if (score === null || score === undefined) return "na";
    if (score >= 80) return "pass";
    if (score >= 60) return "pass";
    if (score >= 40) return "watch";
    return "fail";
  }

  function plainVerdict(data) {
    const sc = data.score || {};
    const score = sc.overall_score;
    const name = data.company_name || data.ticker;
    if (score === null || score === undefined) {
      return `There isn't enough standardised SEC data to judge ${name}'s financial health.`;
    }
    const fails = (sc.failed_rules || []).length;
    const watches = (sc.watch_rules || []).length;
    if (score >= 80 && fails === 0) return `${name} looks financially strong on the measures that apply to it.`;
    if (score >= 80) return `${name} scores strongly overall, with ${fails} measure${fails === 1 ? "" : "s"} still falling short.`;
    if (score >= 60) return `${name} looks broadly healthy, with ${watches + fails} area${watches + fails === 1 ? "" : "s"} worth watching.`;
    if (score >= 40) return `${name} shows real weaknesses alongside its strengths — several measures need attention.`;
    return `${name} is failing most of the financial-health measures that apply to it.`;
  }

  // Radial dial. The arc is drawn with a stroke-dashoffset transition so the
  // score sweeps in on load rather than snapping - motion that says "this was
  // measured", not decoration.
  function heroDialHtml(score, tone) {
    const R = 78, C = 2 * Math.PI * R;
    const pct = score === null || score === undefined ? 0 : Math.max(0, Math.min(100, score));
    const color = tone === "pass" ? "var(--pass)" : tone === "watch" ? "var(--watch)"
      : tone === "fail" ? "var(--fail)" : "var(--na)";
    // Ticks give the dial the feel of a calibrated instrument.
    let ticks = "";
    for (let i = 0; i <= 20; i++) {
      const a = (-90 + (i / 20) * 360) * Math.PI / 180;
      const long = i % 5 === 0;
      const r1 = R + 10, r2 = R + (long ? 18 : 14);
      ticks += `<line x1="${100 + Math.cos(a) * r1}" y1="${100 + Math.sin(a) * r1}"
        x2="${100 + Math.cos(a) * r2}" y2="${100 + Math.sin(a) * r2}"
        stroke="var(--border)" stroke-width="${long ? 1.6 : 1}" opacity="${long ? 0.9 : 0.5}"/>`;
    }
    return `<div class="hero-dial">
      <svg viewBox="0 0 200 200" role="img" aria-label="Financial health score ${score === null ? "unavailable" : score + " out of 100"}">
        ${ticks}
        <circle cx="100" cy="100" r="${R}" fill="none" stroke="var(--border)" stroke-width="10" opacity="0.5"/>
        <circle class="hero-dial-arc" cx="100" cy="100" r="${R}" fill="none"
          stroke="${color}" stroke-width="10" stroke-linecap="round"
          transform="rotate(-90 100 100)"
          stroke-dasharray="${C}" stroke-dashoffset="${C}"
          data-target-offset="${C - (pct / 100) * C}"/>
      </svg>
      <div class="hero-dial-center">
        <div class="hero-score-value" data-count-to="${pct}">${score === null || score === undefined ? "—" : 0}</div>
        <div class="hero-score-max">${score === null || score === undefined ? "no score" : "out of 100"}</div>
        <div class="hero-score-label">Financial Health</div>
      </div>
    </div>`;
  }

  function tallyHtml(sc) {
    const items = [
      ["t-pass", (sc.passed_rules || []).length, "Pass"],
      ["t-watch", (sc.watch_rules || []).length, "Watch"],
      ["t-fail", (sc.failed_rules || []).length, "Fail"],
      ["t-na", (sc.not_applicable_rules || []).length, "N/A"],
      ["t-unav", (sc.unavailable_rules || []).length, "No data"],
    ].filter(([, n]) => n > 0);
    return `<div class="hero-tally">${items.map(([cls, n, label]) =>
      `<div class="tally-chip ${cls}"><span class="tally-n">${n}</span><span class="tally-l">${label}</span></div>`).join("")}</div>`;
  }

  // The measures actually moving the score, worst first - this is the "why".
  function driversHtml(sc) {
    const rules = (sc.rules || []).filter((r) => (r.points_available || 0) > 0);
    const rank = (r) => STATUS_ORDER.indexOf(r.status);
    const top = rules.slice().sort((a, b) =>
      rank(a) - rank(b) || (b.points_available || 0) - (a.points_available || 0)).slice(0, 5);
    if (!top.length) return "";
    return `<div class="hero-drivers">${top.map((r) => `
      <div class="driver-row">
        <span class="driver-name"><strong>${esc(r.name)}</strong> &middot; ${esc(r.value || NA)}</span>
        <span class="kpi-status status-${r.status}">${r.status === "NOT_APPLICABLE" ? "N/A" : r.status}</span>
      </div>`).join("")}</div>`;
  }

  function companyHeaderHtml(data) {
    const c = data.classification || {};
    const val = data.valuation || {};
    const price = val.price && val.price.value;
    const mc = c.market_cap || {};
    // Only the identifying facts belong up here; the full metadata grid lives
    // in the profile strip so the header keeps a clear hierarchy.
    const facts = [
      ["Exchange", data.exchange],
      ["Sector", data.sector],
      ["Industry", data.industry],
      ["Analysed as", (data.score || {}).peer_group],
      ["Reports in", data.reporting_currency],
    ].filter(([, v]) => v);
    return `<div class="co-head">
      <div>
        <h2 class="co-name">${esc(data.company_name || data.ticker)}<span class="co-ticker">${esc(data.ticker)}</span></h2>
        <div class="co-facts">${facts.map(([k, v]) =>
          `<span class="co-fact">${esc(k)} <b>${esc(String(v))}</b></span>`).join("")}</div>
      </div>
      <div class="co-price">
        ${price ? `<div class="co-price-value">${(val.price_currency === "USD" || !val.price_currency) ? "$" : ""}${price.toFixed(2)}</div>
          <div class="co-price-meta">${esc(val.price_currency || "")} &middot; ${esc((val.price && val.price.as_of) || "")}</div>`
          : `<div class="co-price-meta">Live price unavailable</div>`}
        ${mc.available ? `<div class="co-price-meta" style="margin-top:6px">Market cap <b>${esc(fmtUsd(mc.value))}</b></div>` : ""}
      </div>
    </div>`;
  }

  function renderDashboard(data) {
    const el = document.getElementById("dashboard-card");
    const sc = data.score || {};
    const forge = data.forge || {};
    const grading = data.grading || {};
    const score = sc.overall_score;
    const tone = verdictTone(score, sc.label);
    const toneColor = tone === "pass" ? "var(--pass-bg);color:var(--pass)"
      : tone === "watch" ? "var(--watch-bg);color:var(--watch)"
      : tone === "fail" ? "var(--fail-bg);color:var(--fail)" : "var(--na-bg);color:var(--na)";

    const pillars = grading.pillars || forge.pillars || {};
    const pillarOrder = [
      ["financial_health", "Financial Health"], ["financial_quality", "Financial Quality"],
      ["valuation", "Valuation"], ["risk", "Risk"],
    ];
    const pillarHtml = pillarOrder.map(([key, label]) => {
      const p = pillars[key] || {};
      const has = p.score !== null && p.score !== undefined;
      const v = has ? Math.round(p.score) : "N/A";
      const pct = has ? p.score : 0;
      const color = !has ? "var(--na)" : p.score >= 60 ? "var(--pass)" : p.score >= 40 ? "var(--watch)" : "var(--fail)";
      const reasons = (p.key_reasons || []).map((r) => `<li>${esc(r)}</li>`).join("");
      return `<div class="pillar-card">
        <div class="pillar-name">${label}</div>
        <div class="pillar-value" style="color:${color}">${v}</div>
        <div class="pillar-bar-track"><div class="pillar-bar-fill" style="width:${pct}%;background:${color}"></div></div>
        <div class="pillar-grade-line"><span class="pg-letter">${esc(p.letter_grade || "N/A")}</span><span>${p.contribution_pct ? p.contribution_pct + "% of score" : "excluded"}</span></div>
        ${reasons ? `<details class="pillar-detail"><summary>Why this score</summary><ul>${reasons}</ul></details>` : ""}
      </div>`;
    }).join("");

    const conf = sc.data_confidence_pct;
    el.innerHTML = `
      ${companyHeaderHtml(data)}
      <div class="score-hero">
        ${heroDialHtml(score, tone)}
        <div class="hero-readout">
          <p class="hero-question">Is this company financially healthy?</p>
          <p class="hero-answer">${esc(plainVerdict(data))}</p>
          <span class="hero-verdict" style="background:${toneColor}">${esc(sc.label || "Insufficient Data")}</span>
          ${tallyHtml(sc)}
          <p class="hero-question" style="margin-top:18px">What is driving it?</p>
          ${driversHtml(sc)}
          ${conf !== null && conf !== undefined && conf < 100
            ? `<p class="hero-confidence">${conf}% of the measures considered could be scored from this company's SEC data.
               Measures marked not-applicable or unavailable are excluded from the score rather than counted as failures.</p>`
            : ""}
        </div>
      </div>
      <div class="pillar-grid" style="margin-top:26px">${pillarHtml}</div>
      ${profileStripHtml(data)}
    `;
    animateHero(el);
  }

  // Sweep the arc and count the number up once, after paint. Skipped entirely
  // when the reader has asked for reduced motion.
  function animateHero(scope) {
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const arc = scope.querySelector(".hero-dial-arc");
    const num = scope.querySelector(".hero-score-value");
    const target = num ? parseFloat(num.getAttribute("data-count-to")) : 0;
    const isNumeric = num && num.textContent.trim() !== "—";
    if (arc) {
      const off = arc.getAttribute("data-target-offset");
      if (reduce) arc.style.strokeDashoffset = off;
      else requestAnimationFrame(() => requestAnimationFrame(() => { arc.style.strokeDashoffset = off; }));
    }
    if (!num || !isNumeric) return;
    if (reduce) { num.textContent = Math.round(target); return; }
    const t0 = performance.now(), dur = 900;
    (function step(now) {
      const k = Math.min(1, (now - t0) / dur);
      num.textContent = Math.round(target * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(step);
    })(t0);
  }

  function profileStripHtml(data) {
    const c = data.classification;
    if (!c) return "";
    const cell = (label, field) => {
      const val = field && field.available ? field.value : null;
      return `<div class="profile-cell">
        <div class="profile-label">${esc(label)}</div>
        <div class="profile-value${val ? "" : " profile-na"}">${val ? esc(String(val)) : "Unavailable"}</div>
      </div>`;
    };
    const mc = c.market_cap || {};
    const mcField = { available: mc.available, value: mc.available ? fmtUsd(mc.value) : null };
    const currency = data.reporting_currency
      ? { available: true, value: data.reporting_currency }
      : { available: false };
    const note = (data.valuation && data.valuation.currency_note) || "";
    return `
      <div class="profile-strip">
        ${cell("Sector", c.sector)}
        ${cell("Industry", c.industry)}
        ${cell("Sub-Industry", c.sub_industry)}
        ${cell("Exchange", c.exchange)}
        ${cell("Country", c.country)}
        ${cell("Market Cap", mcField)}
        ${cell("Reports In", currency)}
      </div>
      ${c.peer_group_note ? `<p class="profile-note"><strong>How this sector reads:</strong> ${esc(c.peer_group_note)}</p>` : ""}
      ${note ? `<p class="profile-note profile-warn">${esc(note)}</p>` : ""}
    `;
  }

  // ---------- Health tab ----------
  function ruleCard(r) {
    const label = r.status === "NOT_APPLICABLE" ? "NOT APPLICABLE" : r.status;
    const meta = [];
    if (r.threshold) meta.push(`Threshold: ${esc(r.threshold)}`);
    if (r.period) meta.push(`Period: ${esc(r.period)}`);
    if (r.currency) meta.push(`Currency: ${esc(r.currency)}`);
    const tag = r.rule_type === "sector"
      ? `<span class="rule-tag rule-tag-sector">Sector rule</span>`
      : `<span class="rule-tag">Universal rule</span>`;
    const weight = r.points_available > 0
      ? `<span class="rule-weight">${r.points_available} pts</span>`
      : `<span class="rule-weight rule-weight-off">not scored</span>`;
    return `<div class="kpi-card rule-${r.status}">
      <div class="rule-card-head">${tag}${weight}</div>
      <div class="kpi-label">${esc(r.name)}</div>
      <div class="kpi-value">${esc(r.value)}</div>
      <span class="kpi-status status-${r.status}">${label}</span>
      <p class="kpi-explain">${esc(r.explanation)}</p>
      ${meta.length ? `<p class="rule-meta">${meta.join(" &middot; ")}</p>` : ""}
      <p class="kpi-formula"><code>${esc(r.formula)}</code></p>
      ${r.source ? `<details class="rule-source"><summary>Data source</summary><p>${esc(r.source)}</p></details>` : ""}
    </div>`;
  }

  // Plain-English story, rendered from the structured sections the API
  // returns (strengths / concerns / sector context / what is missing).
  function storySectionsHtml(data) {
    const s = data.financial_story_sections;
    if (!s) return "";
    const list = (items, cls) => items.length
      ? `<ul class="story-list ${cls}">${items.map((x) => `<li><strong>${esc(x.rule)}</strong>${x.value ? ` <span class="story-val">${esc(x.value)}</span>` : ""} &mdash; ${esc(x.detail)}</li>`).join("")}</ul>`
      : "";
    const blocks = [];
    blocks.push(`<p class="story-overview">${esc(s.overview)}</p>`);
    if (s.strengths && s.strengths.length)
      blocks.push(`<h4 class="story-h">What is working</h4>${list(s.strengths, "story-good")}`);
    if (s.concerns && s.concerns.length)
      blocks.push(`<h4 class="story-h">What needs attention</h4>${list(s.concerns, "story-warn")}`);
    if (s.sector_context)
      blocks.push(`<h4 class="story-h">Why this sector is read differently</h4><p class="story-p">${esc(s.sector_context)}</p>`);
    if (s.data_gaps && s.data_gaps.length)
      blocks.push(`<h4 class="story-h">What could not be assessed</h4><ul class="story-list story-gap">${s.data_gaps.map((g) => `<li>${esc(g)}</li>`).join("")}</ul>`);
    return `<div class="story-sections">${blocks.join("")}</div>`;
  }

  // Market/investor sections have no configured provider. They are shown as
  // explicitly unavailable rather than hidden, so the absence is visible.
  function providerSectionHtml(data) {
    const p = data.market_providers || {};
    const sections = [
      ["Analyst Consensus", "analyst", "Buy/Hold/Sell consensus, number of analysts"],
      ["Price Target", "analyst", "Average price target and contributing analysts"],
      ["EPS & Revenue Estimates", "estimates", "Forward EPS and revenue consensus"],
      ["Institutional Ownership", "ownership", "Major holders and position changes"],
    ];
    const cards = sections.map(([title, key, desc]) => {
      const prov = p[key] || {};
      const status = prov.available
        ? `<span class="kpi-status status-PASS">${esc(prov.provider || "Available")}</span>`
        : `<span class="kpi-status status-UNAVAILABLE">Unavailable</span>`;
      const reason = prov.available ? "" : `<p class="kpi-explain">${esc(prov.reason || "Unavailable — no provider configured.")}</p>`;
      return `<div class="kpi-card">
        <div class="kpi-label">${esc(title)}</div>
        <div class="kpi-value">${prov.available ? "" : "&mdash;"}</div>
        ${status}
        <p class="kpi-formula">${esc(desc)}</p>
        ${reason}
      </div>`;
    }).join("");
    return `<div class="chart-box">
      <h3>Analyst &amp; Investor Information</h3>
      <p class="section-note">These figures come from market-data providers, not from SEC filings, and are kept
      separate from the SEC-based financial-health analysis above.</p>
      <div class="kpi-grid">${cards}</div>
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
    // Preferred display order, then ANY other category the engine produced.
    // Sector rules introduce categories (Profitability, Financial Risk,
    // Business Quality, ...) that a fixed list would silently hide.
    const preferredCats = ["Liquidity", "Debt & Leverage", "Capital Structure", "Retained Earnings",
      "Profitability", "Cash Generation", "Debt Service", "Financial Risk", "Business Quality",
      "Treasury Stock", "Lease Obligations"];
    const byCat = {};
    rules.forEach((r) => (byCat[r.category] = byCat[r.category] || []).push(r));
    const cats = preferredCats.filter((c) => byCat[c])
      .concat(Object.keys(byCat).filter((c) => !preferredCats.includes(c)).sort());

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

    const sc = data.score || {};
    const naCount = (sc.not_applicable_rules || []).length;
    const sectorBanner = sc.peer_group && sc.peer_group !== "general" ? `
      <div class="chart-box sector-banner">
        <h3>Analysed as: ${esc(sc.peer_group)}</h3>
        <p class="section-note">${esc(sc.peer_group_note || "")}</p>
        <div class="sector-stats">
          <span><strong>${sc.universal_rule_count || 0}</strong> universal rules</span>
          <span><strong>${sc.sector_rule_count || 0}</strong> sector rules</span>
          <span><strong>${naCount}</strong> set aside as not applicable</span>
          <span><strong>${sc.data_confidence_pct != null ? sc.data_confidence_pct + "%" : "—"}</strong> data confidence</span>
        </div>
        ${sc.data_confidence_note ? `<p class="section-note">${esc(sc.data_confidence_note)}</p>` : ""}
      </div>` : "";

    el.innerHTML = sectorBanner + `
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
      ${providerSectionHtml(data)}
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
      <div class="chart-box">
        <h3>Revenue, Net Income &amp; Free Cash Flow</h3>
        <p class="chart-sub">Revenue and Net Income as bars, Free Cash Flow as a line - all from SEC-reported and FORGE-calculated (OCF - CapEx) figures.</p>
        <div class="chart-canvas-wrap"><canvas id="chart-rev-earn-fcf"></canvas></div>
      </div>
      <div class="chart-box">
        <h3>Cash vs. Total Debt</h3>
        <p class="chart-sub">SEC-reported cash &amp; equivalents vs. total debt (short-term + long-term) at each period end.</p>
        <div class="chart-canvas-wrap"><canvas id="chart-cash-debt"></canvas></div>
      </div>
      <div class="chart-box">
        <h3>Margin Trends</h3>
        <p class="chart-sub">Gross margin and net margin over time, FORGE-calculated from SEC-reported revenue, gross profit and net income.</p>
        <div class="chart-canvas-wrap"><canvas id="chart-margins"></canvas></div>
      </div>
      <div class="chart-box">
        <h3>Debt &amp; Lease Composition</h3>
        <p class="chart-sub">Latest reported period - short-term debt, long-term debt, and operating/finance leases (leases already included in Total Liabilities, shown separately for composition only).</p>
        <div class="chart-canvas-wrap short"><canvas id="chart-debt-composition"></canvas></div>
      </div>
    `;

    el.querySelectorAll('input[type="checkbox"][data-metric]').forEach((cb) => {
      cb.addEventListener("change", () => {
        if (cb.checked) timelineActive.add(cb.dataset.metric); else timelineActive.delete(cb.dataset.metric);
        drawTimelineChart(getCurrentTimelineFilter(timelineCache[timelineFrequency] || data.timeline));
      });
    });

    function activeTimelineFull() { return timelineCache[timelineFrequency] || data.timeline; }

    function drawSecondaryVisuals(tl) {
      drawRevEarnFcfChart(tl);
      drawCashDebtChart(tl);
      drawMarginsChart(tl);
    }

    buildTimeframeButtons("timeline-tf", activeTimelineFull(), (filtered) => { drawTimelineChart(filtered); drawSecondaryVisuals(filtered); });
    drawTimelineChart(filterTimeline(activeTimelineFull(), "MAX"));
    drawSecondaryVisuals(filterTimeline(activeTimelineFull(), "MAX"));
    drawDebtCompositionChart(data);

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
        buildTimeframeButtons("timeline-tf", tl, (filtered) => { drawTimelineChart(filtered); drawSecondaryVisuals(filtered); });
        drawTimelineChart(filterTimeline(tl, "MAX"));
        drawSecondaryVisuals(filterTimeline(tl, "MAX"));
      });
    });

    drawScoreHistoryChart(data.historical_scores);
  }

  function timelineChartLabels(timeline) {
    return timeline.map((t) => t.fiscal_period && t.fiscal_period !== "FY" ? `${t.fiscal_period} ${t.fiscal_year || ""}` : "FY" + (t.fiscal_year || t.period_end));
  }

  function drawRevEarnFcfChart(timeline) {
    const el = document.getElementById("chart-rev-earn-fcf");
    if (!el || !window.Chart || !timeline || !timeline.length) return;
    const colors = chartColors();
    const chart = getOrCreateChart(el, {
      data: {
        labels: timelineChartLabels(timeline),
        datasets: [
          { type: "bar", label: "Revenue", data: timeline.map((t) => t.revenue), backgroundColor: colors.brand + "cc" },
          { type: "bar", label: "Net Income", data: timeline.map((t) => t.net_income), backgroundColor: colors.pass + "cc" },
          { type: "line", label: "Free Cash Flow", data: timeline.map((t) => t.free_cash_flow), borderColor: cssVar("--accent"), backgroundColor: cssVar("--accent"), tension: 0.3, fill: false },
        ],
      },
      options: {
        plugins: { legend: { labels: { color: colors.text } }, tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${fmtUsd(ctx.parsed.y)}` } } },
        scales: { y: { ticks: { color: colors.muted, callback: (v) => fmtUsd(v) }, grid: { color: colors.border } }, x: { ticks: { color: colors.text }, grid: { display: false } } },
      },
    });
  }

  function drawCashDebtChart(timeline) {
    const el = document.getElementById("chart-cash-debt");
    if (!el || !window.Chart || !timeline || !timeline.length) return;
    const colors = chartColors();
    const chart = getOrCreateChart(el, {
      type: "bar",
      data: {
        labels: timelineChartLabels(timeline),
        datasets: [
          { label: "Cash", data: timeline.map((t) => t.cash), backgroundColor: colors.pass + "cc" },
          { label: "Total Debt", data: timeline.map((t) => t.total_debt), backgroundColor: colors.fail + "cc" },
        ],
      },
      options: {
        plugins: { legend: { labels: { color: colors.text } }, tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${fmtUsd(ctx.parsed.y)}` } } },
        scales: { y: { ticks: { color: colors.muted, callback: (v) => fmtUsd(v) }, grid: { color: colors.border } }, x: { ticks: { color: colors.text }, grid: { display: false } } },
      },
    });
  }

  function drawMarginsChart(timeline) {
    const el = document.getElementById("chart-margins");
    if (!el || !window.Chart || !timeline || !timeline.length) return;
    const colors = chartColors();
    const chart = getOrCreateChart(el, {
      type: "line",
      data: {
        labels: timelineChartLabels(timeline),
        datasets: [
          { label: "Gross Margin %", data: timeline.map((t) => t.gross_margin_pct), borderColor: cssVar("--accent"), backgroundColor: cssVar("--accent"), tension: 0.3, fill: false },
          { label: "Net Margin %", data: timeline.map((t) => t.net_margin_pct), borderColor: colors.brand, backgroundColor: colors.brand, tension: 0.3, fill: false },
        ],
      },
      options: {
        plugins: { legend: { labels: { color: colors.text } }, tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${ctx.parsed.y == null ? NA : ctx.parsed.y.toFixed(1) + "%"}` } } },
        scales: { y: { ticks: { color: colors.muted, callback: (v) => v + "%" }, grid: { color: colors.border } }, x: { ticks: { color: colors.text }, grid: { display: false } } },
      },
    });
  }

  function drawDebtCompositionChart(data) {
    const el = document.getElementById("chart-debt-composition");
    if (!el || !window.Chart) return;
    const qf = data.quarterly_facts;
    const lease = data.lease_summary;
    const parts = [
      { label: "Short-Term Debt", value: qf.short_term_debt.available ? qf.short_term_debt.value : 0 },
      { label: "Long-Term Debt", value: qf.long_term_debt.available ? qf.long_term_debt.value : 0 },
      { label: "Operating Leases", value: (qf.operating_lease_current.available ? qf.operating_lease_current.value : 0) + (qf.operating_lease_noncurrent.available ? qf.operating_lease_noncurrent.value : 0) },
      { label: "Finance Leases", value: (qf.finance_lease_current.available ? qf.finance_lease_current.value : 0) + (qf.finance_lease_noncurrent.available ? qf.finance_lease_noncurrent.value : 0) },
    ].filter((p) => p.value > 0);
    if (!parts.length) {
      el.parentElement.innerHTML = `<p>${NA} — no short/long-term debt or lease figures were found in this company's SEC filings.</p>`;
      return;
    }
    const colors = chartColors();
    const palette = [colors.fail, colors.watch, cssVar("--accent"), colors.brand, colors.pass];
    const chart = getOrCreateChart(el, {
      type: "doughnut",
      data: {
        labels: parts.map((p) => p.label),
        datasets: [{ data: parts.map((p) => p.value), backgroundColor: parts.map((_, i) => palette[i % palette.length]) }],
      },
      options: {
        plugins: {
          legend: { position: "right", labels: { color: colors.text } },
          tooltip: { callbacks: { label: (ctx) => { const total = parts.reduce((s, p) => s + p.value, 0); const pct = total ? (ctx.parsed / total * 100).toFixed(1) : "0.0"; return `${ctx.label}: ${fmtUsd(ctx.parsed)} (${pct}%)`; } } },
        },
      },
    });
  }

  // ---------- Business Mix / Revenue Map tab ----------
  function renderBusinessMixTab(data) {
    const el = document.getElementById("tab-businessmix");
    const bm = data.business_mix;
    if (!bm || !bm.available) {
      const reason = (bm && bm.reason) || NA;
      el.innerHTML = `<div class="chart-box"><h3>Business Mix / Revenue Map</h3><p>${esc(reason)}</p></div>`;
      return;
    }
    const filingNote = bm.filing ? `Source: ${esc(bm.source)} — 10-K filed ${esc(bm.filing.filing_date || "unknown date")}, fiscal year ended ${esc(bm.period_end || "unknown")}.` : bm.source;

    const sections = [];
    if (bm.business_segments && bm.business_segments.length) {
      sections.push(`
        <div class="chart-box">
          <h3>Revenue by Business Segment / Product</h3>
          <p class="chart-sub">${esc(filingNote)}</p>
          <div style="display:flex;gap:24px;flex-wrap:wrap;align-items:center">
            <div class="chart-canvas-wrap short" style="flex:1;min-width:260px"><canvas id="chart-mix-business"></canvas></div>
            <div style="flex:1;min-width:260px">${businessMixTable(bm.business_segments)}</div>
          </div>
        </div>`);
    }
    if (bm.geographic && bm.geographic.length) {
      sections.push(`
        <div class="chart-box">
          <h3>Revenue by Geography</h3>
          <p class="chart-sub">${esc(filingNote)}</p>
          <div style="display:flex;gap:24px;flex-wrap:wrap;align-items:center">
            <div class="chart-canvas-wrap short" style="flex:1;min-width:260px"><canvas id="chart-mix-geo"></canvas></div>
            <div style="flex:1;min-width:260px">${businessMixTable(bm.geographic)}</div>
          </div>
        </div>`);
    }
    el.innerHTML = sections.join("");
    if (bm.business_segments && bm.business_segments.length) drawMixDonut("chart-mix-business", bm.business_segments);
    if (bm.geographic && bm.geographic.length) drawMixDonut("chart-mix-geo", bm.geographic);
  }

  function businessMixTable(rows) {
    const body = rows.map((r) => `<tr><td>${esc(r.label)}</td><td>${fmtUsd(r.value)}</td><td>${r.pct_of_total.toFixed(1)}%</td></tr>`).join("");
    return `<table class="data-table"><thead><tr><th>Segment</th><th>Revenue</th><th>% of Total</th></tr></thead><tbody>${body}</tbody></table>`;
  }

  function drawMixDonut(canvasId, rows) {
    const el = document.getElementById(canvasId);
    if (!el || !window.Chart) return;
    const colors = chartColors();
    const palette = [colors.brand, colors.pass, colors.watch, colors.fail, cssVar("--accent"), colors.na];
    const chart = getOrCreateChart(el, {
      type: "doughnut",
      data: {
        labels: rows.map((r) => r.label),
        datasets: [{ data: rows.map((r) => r.value), backgroundColor: rows.map((_, i) => palette[i % palette.length]) }],
      },
      options: {
        plugins: {
          legend: { position: "right", labels: { color: colors.text } },
          tooltip: { callbacks: { label: (ctx) => `${ctx.label}: ${fmtUsd(ctx.parsed)} (${rows[ctx.dataIndex].pct_of_total.toFixed(1)}%)` } },
        },
      },
    });
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

  // ---------- 3D: the Financial Core ----------
  // The object is a reading of the company, not decoration. A central core
  // carries overall health; each orbiting column is one financial dimension,
  // its height the score and its colour the status. Dimensions that cannot be
  // scored are drawn as hollow wireframe columns rather than short solid ones,
  // so "we cannot measure this" never looks like "this is weak" - the same
  // distinction the rest of the product makes in text.

  let model3dState = null;

  const M3D_COLORS = {
    pass: 0x35c98a, watch: 0xf2b134, fail: 0xf05a52, na: 0x6b7896, core: 0x4d97f5,
  };

  function dimensionState(key, data) {
    const score = dimensionScore(key, data);
    if (score === null || score === undefined) return { score: null, tone: "na", measured: false };
    const tone = score >= 60 ? "pass" : score >= 40 ? "watch" : "fail";
    return { score, tone, measured: true };
  }

  function renderModel3D(data) {
    const el = document.getElementById("tab-model3d");
    const sc = data.score || {};
    const overall = sc.overall_score;
    el.innerHTML = `
      <div class="chart-box">
        <h3>Financial Core</h3>
        <p class="section-note">Each column is one dimension of this company's finances: its height is that
          dimension's score and its colour is the verdict. The core at the centre reflects overall financial
          health — it holds steady when the company is strong and destabilises when it is not. Hollow columns
          are dimensions that could not be scored, which is not the same as scoring badly.</p>
        <div class="model3d-wrap">
          <div id="model3d-canvas-holder">
            <div class="m3d-hud">Drag to orbit &middot; scroll to zoom &middot; click a column</div>
          </div>
          <div id="model3d-detail">
            <p class="m3d-title">Overall</p>
            <div class="m3d-value">${overall === null || overall === undefined ? "—" : Math.round(overall)}<span style="font-size:0.8rem;color:var(--faint)"> / 100</span></div>
            <p style="color:var(--muted);font-size:0.85rem;margin:10px 0 0">Hover or click a column to inspect that dimension.</p>
          </div>
        </div>
        <div class="m3d-legend">
          <span><i class="m3d-swatch" style="background:#35c98a"></i>Healthy</span>
          <span><i class="m3d-swatch" style="background:#f2b134"></i>Watch</span>
          <span><i class="m3d-swatch" style="background:#f05a52"></i>Weak</span>
          <span><i class="m3d-swatch" style="background:transparent;box-shadow:inset 0 0 0 1px #6b7896"></i>Not scored</span>
        </div>
      </div>
    `;
    if (!window.THREE) {
      document.getElementById("model3d-canvas-holder").innerHTML =
        `<div class="empty-note" style="margin:18px">The 3D view needs WebGL and the Three.js library, which did not load.
         Every figure it shows is also available in the other tabs.</div>`;
      return;
    }
    try { setup3D(data); } catch (err) {
      document.getElementById("model3d-canvas-holder").innerHTML =
        `<div class="empty-note" style="margin:18px">The 3D view could not start in this browser.
         Every figure it shows is also available in the other tabs.</div>`;
    }
  }

  function setup3D(data) {
    const holder = document.getElementById("model3d-canvas-holder");
    const detail = document.getElementById("model3d-detail");
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const isDark = root.getAttribute("data-theme") === "dark" ||
      (!root.getAttribute("data-theme") && window.matchMedia("(prefers-color-scheme: dark)").matches);

    const width = holder.clientWidth || 640;
    const height = holder.clientHeight || 480;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(isDark ? 0x060910 : 0xe4e7ee);
    scene.fog = new THREE.Fog(scene.background.getHex(), 14, 30);

    const camera = new THREE.PerspectiveCamera(42, width / height, 0.1, 100);
    camera.position.set(7.5, 5.5, 9);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: false });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height);
    const hud = holder.querySelector(".m3d-hud");
    holder.innerHTML = "";
    if (hud) holder.appendChild(hud);
    holder.appendChild(renderer.domElement);

    const controls = new THREE.OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.07;
    controls.minDistance = 6;
    controls.maxDistance = 22;
    controls.maxPolarAngle = Math.PI * 0.52;
    controls.enablePan = false;
    controls.autoRotate = !reduce;
    controls.autoRotateSpeed = 0.45;

    scene.add(new THREE.AmbientLight(0xffffff, isDark ? 0.55 : 0.8));
    const key = new THREE.DirectionalLight(0xffffff, isDark ? 0.9 : 0.75);
    key.position.set(6, 12, 8);
    scene.add(key);
    const rim = new THREE.DirectionalLight(M3D_COLORS.core, isDark ? 0.5 : 0.25);
    rim.position.set(-8, 4, -6);
    scene.add(rim);

    // --- Base plate: a calibrated disc rather than a generic grid ---------
    const baseGroup = new THREE.Group();
    const discMat = new THREE.MeshBasicMaterial({
      color: isDark ? 0x121824 : 0xffffff, transparent: true, opacity: isDark ? 0.55 : 0.75,
    });
    const disc = new THREE.Mesh(new THREE.CircleGeometry(4.6, 64), discMat);
    disc.rotation.x = -Math.PI / 2;
    disc.position.y = -0.01;
    baseGroup.add(disc);
    [2.4, 3.5, 4.6].forEach((r) => {
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(r - 0.012, r + 0.012, 96),
        new THREE.MeshBasicMaterial({ color: isDark ? 0x263047 : 0xd8dde6, side: THREE.DoubleSide })
      );
      ring.rotation.x = -Math.PI / 2;
      baseGroup.add(ring);
    });
    scene.add(baseGroup);

    // --- The core: overall financial health -------------------------------
    const sc = data.score || {};
    const overall = sc.overall_score;
    const hasOverall = overall !== null && overall !== undefined;
    const coreTone = !hasOverall ? "na" : overall >= 60 ? "pass" : overall >= 40 ? "watch" : "fail";
    const coreColor = M3D_COLORS[coreTone];
    // A healthy core is tight and bright; a weak one is smaller and dimmer.
    const coreScale = hasOverall ? 0.62 + (overall / 100) * 0.5 : 0.55;

    const coreGroup = new THREE.Group();
    const coreMesh = new THREE.Mesh(
      new THREE.IcosahedronGeometry(coreScale, 1),
      new THREE.MeshStandardMaterial({
        color: coreColor, roughness: 0.32, metalness: 0.45,
        emissive: coreColor, emissiveIntensity: hasOverall ? 0.16 + (overall / 100) * 0.3 : 0.08,
        flatShading: true,
      })
    );
    coreGroup.add(coreMesh);
    const coreShell = new THREE.Mesh(
      new THREE.IcosahedronGeometry(coreScale * 1.45, 1),
      new THREE.MeshBasicMaterial({ color: coreColor, wireframe: true, transparent: true, opacity: 0.22 })
    );
    coreGroup.add(coreShell);
    coreGroup.position.y = 1.9;
    scene.add(coreGroup);

    // --- One column per financial dimension -------------------------------
    const dims = DIMENSION_DEFS;
    const n = dims.length;
    const radius = 3.2;
    const columns = [];

    dims.forEach((dim, i) => {
      const angle = (i / n) * Math.PI * 2;
      const x = Math.cos(angle) * radius;
      const z = Math.sin(angle) * radius;
      const st = dimensionState(dim.key, data);
      const h = st.measured ? Math.max(0.35, (st.score / 100) * 3.6) : 1.2;
      const color = M3D_COLORS[st.tone];

      const geo = new THREE.CylinderGeometry(0.34, 0.38, h, 6);
      // Unmeasured dimensions are hollow: "not scored" must never read as "weak".
      const mat = st.measured
        ? new THREE.MeshStandardMaterial({ color, roughness: 0.4, metalness: 0.3,
            emissive: color, emissiveIntensity: 0.1 })
        : new THREE.MeshBasicMaterial({ color, wireframe: true, transparent: true, opacity: 0.5 });
      const col = new THREE.Mesh(geo, mat);
      col.position.set(x, h / 2, z);
      col.userData = { dim, st, baseY: h / 2, height: h };
      scene.add(col);
      columns.push(col);

      // A strut tying each dimension back to the core.
      const from = new THREE.Vector3(x, h, z);
      const to = new THREE.Vector3(0, coreGroup.position.y, 0);
      const strut = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints([from, to]),
        new THREE.LineBasicMaterial({ color, transparent: true, opacity: st.measured ? 0.3 : 0.12 })
      );
      scene.add(strut);
      col.userData.strut = strut;

      const canvas = document.createElement("canvas");
      canvas.width = 320; canvas.height = 72;
      const ctx = canvas.getContext("2d");
      ctx.fillStyle = isDark ? "#dfe5f0" : "#131a26";
      ctx.font = "600 30px ui-monospace, SFMono-Regular, Menlo, monospace";
      ctx.textAlign = "center";
      ctx.fillText(dim.label.toUpperCase(), 160, 44);
      const sprite = new THREE.Sprite(new THREE.SpriteMaterial({
        map: new THREE.CanvasTexture(canvas), transparent: true, depthTest: false,
      }));
      sprite.scale.set(2.3, 0.52, 1);
      sprite.position.set(x, h + 0.55, z);
      scene.add(sprite);
      col.userData.sprite = sprite;
    });

    // --- Interaction ------------------------------------------------------
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let hovered = null, pinned = null;

    function renderDetail(col) {
      if (!col) {
        detail.innerHTML = `<p class="m3d-title">Overall</p>
          <div class="m3d-value">${hasOverall ? Math.round(overall) : "—"}<span style="font-size:0.8rem;color:var(--faint)"> / 100</span></div>
          <p style="color:var(--muted);font-size:0.85rem;margin:10px 0 0">Hover or click a column to inspect that dimension.</p>`;
        return;
      }
      const { dim, st } = col.userData;
      let rows = "";
      try {
        rows = dim.metrics(currentData).map(([k, v]) =>
          `<div class="m3d-row"><span>${esc(k)}</span><strong>${esc(v == null ? NA : String(v))}</strong></div>`).join("");
      } catch (e) { rows = `<div class="m3d-row"><span>Detail unavailable</span></div>`; }
      const chip = st.measured
        ? `<span class="kpi-status status-${st.tone === "pass" ? "PASS" : st.tone === "watch" ? "WATCH" : "FAIL"}">${st.tone === "pass" ? "HEALTHY" : st.tone === "watch" ? "WATCH" : "WEAK"}</span>`
        : `<span class="kpi-status status-UNAVAILABLE">NOT SCORED</span>`;
      detail.innerHTML = `<p class="m3d-title">${esc(dim.label)}</p>
        <div class="m3d-value">${st.measured ? Math.round(st.score) : "—"}${st.measured ? '<span style="font-size:0.8rem;color:var(--faint)"> / 100</span>' : ""}</div>
        <div style="margin:8px 0 4px">${chip}</div>
        ${rows}`;
    }

    function setHover(col) {
      if (hovered === col) return;
      if (hovered && hovered !== pinned) {
        hovered.material.emissiveIntensity !== undefined && (hovered.material.emissiveIntensity = 0.1);
        hovered.userData.strut.material.opacity = hovered.userData.st.measured ? 0.3 : 0.12;
      }
      hovered = col;
      if (col) {
        if (col.material.emissiveIntensity !== undefined) col.material.emissiveIntensity = 0.45;
        col.userData.strut.material.opacity = 0.8;
        renderDetail(col);
        renderer.domElement.style.cursor = "pointer";
      } else {
        renderer.domElement.style.cursor = "grab";
        if (!pinned) renderDetail(null);
      }
    }

    function pick(ev) {
      const rect = renderer.domElement.getBoundingClientRect();
      const src = ev.touches ? ev.touches[0] : ev;
      pointer.x = ((src.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((src.clientY - rect.top) / rect.height) * 2 + 1;
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(columns);
      return hits.length ? hits[0].object : null;
    }

    const onMove = (ev) => { if (!pinned) setHover(pick(ev)); };
    const onClick = (ev) => {
      const col = pick(ev);
      pinned = (pinned === col) ? null : col;
      if (pinned) { setHover(null); hovered = pinned; renderDetail(pinned); }
      else renderDetail(null);
      controls.autoRotate = !reduce && !pinned;
    };
    renderer.domElement.addEventListener("pointermove", onMove);
    renderer.domElement.addEventListener("click", onClick);
    renderer.domElement.addEventListener("pointerleave", () => { if (!pinned) setHover(null); });

    // --- Animation --------------------------------------------------------
    // Instability is proportional to poor health: a strong company's core sits
    // almost still, a failing one visibly wavers. Subtle by design.
    const instability = hasOverall ? Math.max(0, (60 - overall) / 60) : 0.5;
    const clock = new THREE.Clock();
    let frameId;

    function animate() {
      frameId = requestAnimationFrame(animate);
      const t = clock.getElapsedTime();
      if (!reduce) {
        coreGroup.rotation.y = t * 0.22;
        coreShell.rotation.y = -t * 0.34;
        coreShell.rotation.x = t * 0.12;
        coreGroup.position.y = 1.9 + Math.sin(t * 1.4) * 0.05 * (0.4 + instability);
        coreGroup.rotation.z = Math.sin(t * 2.1) * 0.05 * instability;
        coreGroup.rotation.x = Math.cos(t * 1.7) * 0.05 * instability;
      }
      controls.update();
      renderer.render(scene, camera);
    }
    animate();

    if (model3dState) {
      if (model3dState.frameId) cancelAnimationFrame(model3dState.frameId);
      if (model3dState.dispose) model3dState.dispose();
    }
    const onResize = () => {
      if (!holder.isConnected) return;
      const w = holder.clientWidth, h = holder.clientHeight;
      if (!w || !h) return;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener("resize", onResize);
    model3dState = {
      frameId,
      dispose() {
        window.removeEventListener("resize", onResize);
        renderer.domElement.removeEventListener("pointermove", onMove);
        renderer.domElement.removeEventListener("click", onClick);
        try { renderer.dispose(); } catch (e) { /* nothing useful to do */ }
      },
    };
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

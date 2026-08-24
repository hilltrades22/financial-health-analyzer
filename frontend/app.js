(function () {
  const form = document.getElementById("search-form");
  const input = document.getElementById("ticker-input");
  const btn = document.getElementById("analyze-btn");
  const statusPanel = document.getElementById("status-panel");
  const results = document.getElementById("results");

  const CATEGORY_ORDER = [
    "Liquidity",
    "Debt & Leverage",
    "Retained Earnings",
    "Capital Structure",
    "Treasury Stock",
    "Lease Obligations",
    "Cash Generation",
    "Debt Service",
  ];

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const ticker = input.value.trim().toUpperCase();
    if (!ticker) return;
    await analyze(ticker);
  });

  function setStatus(message, isError) {
    statusPanel.hidden = false;
    statusPanel.textContent = message;
    statusPanel.className = "status-panel" + (isError ? " error" : "");
  }

  function clearStatus() {
    statusPanel.hidden = true;
    statusPanel.textContent = "";
  }

  async function analyze(ticker) {
    results.hidden = true;
    btn.disabled = true;
    btn.textContent = "Analyzing...";
    setStatus(`Looking up ${ticker} in SEC EDGAR...`, false);

    try {
      const resp = await fetch(`/api/analyze/${encodeURIComponent(ticker)}`);
      if (!resp.ok) {
        const body = await safeJson(resp);
        const detail = (body && body.detail) || `Request failed with status ${resp.status}`;
        if (resp.status === 404) {
          setStatus(`We couldn't find "${ticker}" in SEC EDGAR. Double-check the ticker symbol and try again.`, true);
        } else if (resp.status === 503) {
          setStatus(`SEC EDGAR appears to be unavailable right now. Please try again in a few minutes. (${detail})`, true);
        } else {
          setStatus(`Something went wrong: ${detail}`, true);
        }
        return;
      }
      const data = await resp.json();
      clearStatus();
      renderResults(data);
    } catch (err) {
      setStatus(`Network error: could not reach the analysis service. (${err.message})`, true);
    } finally {
      btn.disabled = false;
      btn.textContent = "Analyze Company";
    }
  }

  async function safeJson(resp) {
    try { return await resp.json(); } catch { return null; }
  }

  function fmtDate(d) {
    return d || "Not reported / unavailable from standardized SEC data";
  }

  function renderResults(data) {
    document.getElementById("company-name").textContent = data.company_name || data.ticker;
    document.getElementById("company-ticker").textContent = data.ticker;

    const score = data.score;
    const scoreValEl = document.getElementById("score-value");
    scoreValEl.textContent = score.overall_score === null || score.overall_score === undefined
      ? "N/A"
      : `${score.overall_score} / 100`;

    const badge = document.getElementById("score-badge");
    badge.textContent = score.label;
    badge.className = "score-badge " + score.label.replace(/\s+/g, "-");

    document.getElementById("period-quarter").textContent =
      `${fmtDate(data.latest_quarter.period_end)}` + (data.latest_quarter.form ? ` (${data.latest_quarter.form}, filed ${data.latest_quarter.filed || "n/a"})` : "");
    document.getElementById("period-annual").textContent =
      (data.latest_annual.fiscal_year ? `FY${data.latest_annual.fiscal_year} - ` : "") +
      `${fmtDate(data.latest_annual.period_end)}` + (data.latest_annual.filed ? ` (filed ${data.latest_annual.filed})` : "");

    document.getElementById("financial-story").textContent = data.financial_story;

    renderRuleSections(score.rules);
    renderDataSources(data);

    results.hidden = false;
  }

  function renderRuleSections(rules) {
    const container = document.getElementById("rule-sections");
    container.innerHTML = "";

    const byCategory = {};
    rules.forEach((r) => {
      (byCategory[r.category] = byCategory[r.category] || []).push(r);
    });

    CATEGORY_ORDER.forEach((cat) => {
      const items = byCategory[cat];
      if (!items || !items.length) return;
      const section = document.createElement("section");
      section.className = "rule-section";
      const h3 = document.createElement("h3");
      h3.textContent = cat;
      section.appendChild(h3);

      items.forEach((r) => {
        const row = document.createElement("div");
        row.className = "rule-row";
        row.innerHTML = `
          <div class="rule-row-head">
            <span class="rule-metric">${escapeHtml(r.name)}</span>
            <span>
              <span class="rule-value">${escapeHtml(r.value)}</span>
              <span class="status-chip ${r.status}">${r.status}</span>
            </span>
          </div>
          <p class="rule-explanation">${escapeHtml(r.explanation)}</p>
          <p class="rule-meta"><strong>Formula:</strong> <code>${escapeHtml(r.formula)}</code></p>
          <p class="rule-meta"><strong>Source:</strong> ${escapeHtml(r.source)}</p>
        `;
        section.appendChild(row);
      });
      container.appendChild(section);
    });
  }

  function renderDataSources(data) {
    const el = document.getElementById("data-sources-content");
    const score = data.score;
    let html = `<p><strong>Scoring formula:</strong> ${escapeHtml(score.scoring_formula)}</p>`;
    html += `<p><strong>Points earned:</strong> ${score.points_earned} &nbsp; <strong>Points available (rules with data):</strong> ${score.points_available_scored} / 100</p>`;
    html += `<p><strong>Passed rules:</strong> ${score.passed_rules.join(", ") || "none"}<br>`;
    html += `<strong>Watch rules:</strong> ${score.watch_rules.join(", ") || "none"}<br>`;
    html += `<strong>Failed rules:</strong> ${score.failed_rules.join(", ") || "none"}<br>`;
    html += `<strong>Unavailable rules:</strong> ${score.unavailable_rules.join(", ") || "none"}</p>`;
    html += `<p><strong>SEC EDGAR filing index:</strong> <a href="${escapeAttr(data.sec_edgar_url)}" target="_blank" rel="noopener">${escapeHtml(data.sec_edgar_url)}</a></p>`;
    html += `<p>${escapeHtml(data.data_source)}</p>`;
    el.innerHTML = html;
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }
  function escapeAttr(str) { return escapeHtml(str); }
})();

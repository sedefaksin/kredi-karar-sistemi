const state = { meta: null, customers: [], current: null, portfolio: null, audit: [], auditIntegrity: null, user: null, csrfToken: "" };
const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[char]);
const money = (value) => `${new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 }).format(Number(value) || 0)} ₺`;
const decimal = (value, digits = 1) => new Intl.NumberFormat("tr-TR", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(Number(value) || 0);
const pct = (value) => `%${new Intl.NumberFormat("tr-TR", { maximumFractionDigits: 0 }).format((Number(value) || 0) * 100)}`;

async function api(path, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  if (method !== "GET" && path !== "/api/login" && state.csrfToken) headers["X-CSRF-Token"] = state.csrfToken;
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers,
  });
  let data;
  try { data = await response.json(); } catch { throw new Error("Sunucudan geçerli bir yanıt alınamadı."); }
  if (response.status === 401 && path !== "/api/login" && path !== "/api/session") {
    state.csrfToken = "";
    $("#app-shell").classList.add("hidden");
    $("#landing-page").classList.add("hidden");
    $("#login-gate").classList.remove("hidden");
    $("#login-error").textContent = "Oturumunuz sona erdi. Lütfen yeniden giriş yapın.";
    $("#login-error").classList.remove("hidden");
  }
  if (!response.ok) throw new Error(data.error || `İstek başarısız (${response.status}).`);
  return data;
}

function showToast(message, isError = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.toggle("error", isError);
  toast.classList.add("visible");
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove("visible"), 3600);
}

function showError(target, message) {
  target.textContent = message;
  target.classList.remove("hidden");
}

function switchView(viewName) {
  const labels = { review: "Başvuru değerlendirme", portfolio: "Portföy analizi", audit: "Karar geçmişi" };
  $$(".view").forEach((view) => view.classList.toggle("active", view.id === `view-${viewName}`));
  $$(".nav-item").forEach((button) => button.classList.toggle("active", button.dataset.view === viewName));
  $("#crumb-current").textContent = labels[viewName] || labels.review;
  history.replaceState(null, "", `#${viewName}`);
  if (viewName === "audit") loadAudit();
}

function parseReport(report) {
  const fields = {};
  const labels = new Set(["MUSTERI", "RISK SKORU", "OTOMATIK ONAYA UYGUN MU", "GEREKCE", "GEMINI ACIKLAMASI", "DAYANILAN BELGE", "NIHAI ONERI", "SON KARAR"]);
  const fold = (value) => value.normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toLocaleUpperCase("tr-TR").replaceAll("İ", "I");
  let current = "";
  for (const line of String(report || "").split(/\r?\n/)) {
    const match = line.match(/^\s*([\p{L} ]+):\s*(.*)$/u);
    if (match && labels.has(fold(match[1].trim()))) {
      current = fold(match[1].trim());
      fields[current] = match[2].trim();
    } else if (current && line.trim()) fields[current] += ` ${line.trim()}`;
  }
  return fields;
}

function fieldValue(customer, label, value) {
  return `<div><div class="fact-label">${escapeHTML(label)}</div><div class="fact-value">${escapeHTML(value)}</div></div>`;
}

function renderDecision(evidence, report, issues = [], mode = "demo") {
  const customer = evidence.get_customer_data;
  const risk = evidence.calculate_risk_score;
  const policy = evidence.check_policy_compliance;
  const fields = parseReport(report);
  const decision = String(policy.recommended_decision || fields["NIHAI ONERI"] || "UZMAN İNCELEMESİ").toLocaleUpperCase("tr-TR");
  const key = decision.includes("ONAY") ? "approve" : decision.includes("RET") ? "reject" : "human";
  const title = { approve: "Onay önerisi", reject: "Ret önerisi", human: "Uzman incelemesi gerekli" }[key];
  const reason = fields.GEREKCE || (policy.reasons_against || []).join("; ") || "Tüm otomatik onay kontrolleri sağlandı.";
  const banner = $("#decision-banner");
  banner.className = `decision-banner ${key}`;
  banner.innerHTML = `<span class="decision-icon">${key === "approve" ? "✓" : key === "reject" ? "!" : "↗"}</span><span class="decision-copy"><h2>${title}</h2><p>${escapeHTML((policy.reasons_against || []).join(" · ") || "Kontrol edilen ölçütler uygun; nihai karar uzmana aittir.")}</p></span><span class="decision-label">SİSTEM ÖNERİSİ</span>`;

  const metrics = [
    { label: "RİSK SKORU", value: `${decimal(risk.risk_score)}/100`, hint: `${risk.risk_level} risk`, accent: true },
    { label: "BORÇ / GELİR", value: pct(risk.debt_to_income_ratio), hint: risk.debt_to_income_complete ? "Yeni taksit dahil" : "Yeni taksit bilinmiyor · eksik oran" },
    { label: "KREDİ / AYLIK GELİR", value: `${decimal(policy.income_multiple || 0)}×`, hint: `Politika tavanı ${policy.income_cap_multiple}×` },
    { label: "GEREKLİ TEMİNAT", value: money(policy.required_collateral), hint: policy.collateral_required ? "Talep tutarının en az %20'si" : "Politika eşiği aşılmadı" },
  ];
  $("#risk-metrics").innerHTML = metrics.map((m) => `<article class="metric-card"><div class="metric-label">${m.label}</div><div class="metric-value ${m.accent ? "metric-accent" : ""}">${escapeHTML(m.value)}</div><div class="metric-hint">${escapeHTML(m.hint)}</div></article>`).join("");

  $("#branch-pill").textContent = customer.branch;
  $("#customer-facts").innerHTML = [
    fieldValue(customer, "Müşteri", customer.name), fieldValue(customer, "Müşteri kimliği", customer.customer_id),
    fieldValue(customer, "Aylık net gelir", money(customer.monthly_income)), fieldValue(customer, "Mevcut aylık borç", money(customer.existing_monthly_debt)),
    fieldValue(customer, "Talep edilen tutar", money(customer.requested_amount)), fieldValue(customer, "Vade", `${customer.requested_term_months} ay`),
    fieldValue(customer, "Kredi geçmişi", `${customer.credit_history_months} ay`), fieldValue(customer, "Geçmiş temerrüt", `${customer.past_defaults} kayıt`),
  ].join("");

  $("#policy-checks").innerHTML = policy.checks.map((check) => {
    const mark = { pass: "✓", fail: "!", unknown: "?" }[check.status] || "?";
    return `<div class="check-item"><span class="check-mark ${escapeHTML(check.status)}">${mark}</span><span>${escapeHTML(check.label)}</span><span class="check-value">${escapeHTML(check.detail)}</span></div>`;
  }).join("");
  $("#policy-source").textContent = `${policy.policy_references.length} kaynak · ${policy.cited_source_file || "Politika belgeleri"}`;
  $("#report-rationale").textContent = reason;
  $("#gemini-explanation").textContent = fields["GEMINI ACIKLAMASI"] || "";
  $("#gemini-explanation-wrap").classList.toggle("hidden", mode !== "gemini");
  $("#full-report").textContent = report || "Rapor bulunamadı.";
  $("#policy-text").textContent = policy.policy_references.map((ref) => `[kaynak: ${ref.source_file} — ${ref.section}]\n${ref.text}`).join("\n\n");
  const auditWarning = $("#audit-warning");
  if (issues?.length) { auditWarning.textContent = `İkinci kontrol raporda tutarsızlık buldu: ${issues.join(" · ")}`; auditWarning.classList.remove("hidden"); }
  else auditWarning.classList.add("hidden");

  state.current = { customer, risk, policy, report, decision: policy.recommended_decision, decisionId: evidence.decision_id };
  $("#human-review").classList.toggle("hidden", !["admin", "reviewer"].includes(state.user?.role));
  if (state.user && ["admin", "reviewer"].includes(state.user.role)) {
    $("#reviewer-identity").textContent = `Karar kaydı: ${state.user.display_name} · ${state.user.role}`;
  }
  $("#saved-banner").classList.add("hidden");
  $("#review-result").classList.remove("hidden");
}

async function handleReview(event) {
  event.preventDefault();
  const customerInput = $("#customer-search").value.trim();
  const selected = state.customers.find((customer) => customerInput === `${customer.name} · ${customer.customer_id}` || customerInput === customer.customer_id);
  if (!selected) return showToast("Listeden geçerli bir müşteri seçin.", true);
  const collateral = Number($("#collateral-amount").value || 0);
  const button = $(".evaluate-button");
  const loading = $("#review-loading");
  const error = $("#review-error");
  const fallback = $("#fallback-alert");
  error.classList.add("hidden"); fallback.classList.add("hidden");
  $("#review-result").classList.add("hidden"); loading.classList.remove("hidden"); button.disabled = true;
  try {
    const response = await api("/api/evaluate", { method: "POST", body: JSON.stringify({ customer_id: selected.customer_id, demo: !$("#live-toggle").checked, collateral_amount: collateral }) });
    if (response.error) throw new Error(response.error);
    response.evidence.decision_id = response.decision_id;
    if (response.warning) { fallback.textContent = response.warning; fallback.classList.remove("hidden"); }
    renderDecision(response.evidence, response.report, response.audit_issues, response.mode);
    $("#review-result").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (err) { showError(error, err.message); }
  finally { loading.classList.add("hidden"); button.disabled = false; }
}

function formatFilters(filters) {
  const items = [];
  if (filters.branch) items.push(`Şube: ${filters.branch}`);
  if (filters.has_defaults === true) items.push("Temerrütlü");
  if (filters.has_defaults === false) items.push("Temerrütsüz");
  if (filters.min_income != null) items.push(`Gelir ≥ ${money(filters.min_income)}`);
  if (filters.max_income != null) items.push(`Gelir ≤ ${money(filters.max_income)}`);
  return items.length ? items : ["Tüm portföy"];
}

async function handlePortfolio(event) {
  event.preventDefault();
  const question = $("#portfolio-question").value.trim();
  const loading = $("#portfolio-loading"), error = $("#portfolio-error"), result = $("#portfolio-result");
  error.classList.add("hidden"); result.classList.add("hidden"); loading.classList.remove("hidden");
  try {
    const data = await api("/api/portfolio", { method: "POST", body: JSON.stringify({ question }) });
    state.portfolio = data;
    if (!data.count) { $("#portfolio-interpretation").textContent = formatFilters(data.filters).join(" · "); $("#portfolio-result").classList.remove("hidden"); $("#portfolio-metrics").innerHTML = ""; $("#portfolio-rows").innerHTML = `<tr><td colspan="5" class="results-empty">Bu filtrelerle eşleşen müşteri bulunamadı.</td></tr>`; $("#result-description").textContent = "Sonuç bulunamadı"; }
    else renderPortfolio(data);
    result.classList.remove("hidden");
  } catch (err) { showError(error, err.message); }
  finally { loading.classList.add("hidden"); }
}

function renderPortfolio(data) {
  const filters = formatFilters(data.filters);
  $("#portfolio-interpretation").innerHTML = `Soru ölçütleri: ${filters.map((item) => `<span class="pill neutral">${escapeHTML(item)}</span>`).join(" ")}`;
  $("#result-count-pill").textContent = `${data.count.toLocaleString("tr-TR")} müşteri`;
  $("#result-description").textContent = `${data.count.toLocaleString("tr-TR")} kayıt · ${filters.join(" · ")}`;
  const m = data.metrics;
  const items = [
    ["EŞLEŞEN MÜŞTERİ", data.count.toLocaleString("tr-TR"), "Arama ölçütlerine göre"],
    ["ORTALAMA ÖN RİSK", `${decimal(m.average_risk)}/100`, `Yeni taksit doğrulanmış kayıt: ${m.risk_data_complete_count}/${data.count}`],
    ["TEMERRÜTLÜ MÜŞTERİ", `${m.defaulted_customers.toLocaleString("tr-TR")}`, `Portföyün %${decimal(m.default_rate)}’i`],
    ["ORTALAMA AYLIK GELİR", money(m.average_income), "Eşleşen grubun ortalaması"],
  ];
  $("#portfolio-metrics").innerHTML = items.map(([label, value, hint]) => `<article class="metric-card"><div class="metric-label">${label}</div><div class="metric-value">${escapeHTML(value)}</div><div class="metric-hint">${escapeHTML(hint)}</div></article>`).join("");
  $("#portfolio-rows").innerHTML = data.customers.map((customer) => {
    const level = customer.risk_score < 25 ? "low" : customer.risk_score >= 50 ? "high" : "";
    return `<tr><td><div class="customer-cell">${escapeHTML(customer.name)}</div><div class="customer-sub">${escapeHTML(customer.customer_id)}</div></td><td>${escapeHTML(customer.branch)}</td><td>${escapeHTML(money(customer.monthly_income))}</td><td>${customer.past_defaults ? `<span class="risk-chip">${customer.past_defaults} kayıt</span>` : `<span class="pill green-pill">Yok</span>`}</td><td><span class="risk-chip ${level}">${decimal(customer.risk_score)}</span></td></tr>`;
  }).join("");
}

function downloadFile(filename, contents, type) {
  const blob = new Blob([contents], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a"); anchor.href = url; anchor.download = filename; anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function loadAudit() {
  try {
    [state.audit, state.auditIntegrity] = await Promise.all([api("/api/audit"), api("/api/audit/integrity")]);
    renderAudit();
  }
  catch (err) { showToast(err.message, true); }
}

function renderAudit() {
  const entries = state.audit;
  const integrity = $("#audit-integrity");
  integrity.className = `pill ${state.auditIntegrity?.valid ? "valid" : "invalid"}`;
  integrity.textContent = state.auditIntegrity?.valid
    ? `Doğrulandı · ${state.auditIntegrity.event_count} olay`
    : "Doğrulama başarısız";
  const approved = entries.filter((entry) => entry.final_decision === "ONAYLANDI (insan)").length;
  const rejected = entries.filter((entry) => entry.final_decision === "REDDEDİLDİ (insan)").length;
  const pending = entries.length - approved - rejected;
  $("#audit-summary").innerHTML = [
    ["TOPLAM DEĞERLENDİRME", entries.length, "Denetim kaydındaki tüm başvurular"],
    ["UZMAN ONAYI", approved, "İnsan tarafından onaylanan"],
    ["UZMAN REDDİ", rejected, "İnsan tarafından reddedilen"],
    ["UZMAN KARARI BEKLİYOR", pending, "Henüz sonuçlandırılmayan"],
  ].map(([label, value, hint]) => `<article class="metric-card"><div class="metric-label">${label}</div><div class="metric-value">${value}</div><div class="metric-hint">${hint}</div></article>`).join("");
  $("#audit-count-pill").textContent = `${entries.length.toLocaleString("tr-TR")} kayıt`;
  $("#audit-empty").classList.toggle("hidden", entries.length > 0);
  $("#audit-table-wrap").classList.toggle("hidden", entries.length === 0);
  $("#audit-rows").innerHTML = entries.map((entry) => {
    const approvedDecision = entry.final_decision === "ONAYLANDI (insan)";
    const date = new Date(entry.created_at).toLocaleString("tr-TR", { dateStyle: "medium", timeStyle: "short" });
    const reviewer = entry.reviewer ? `${entry.reviewer} · ${entry.reviewer_username || ""}` : "—";
    return `<tr><td>${escapeHTML(date)}</td><td><div class="customer-cell">${escapeHTML(entry.customer_id)}</div></td><td>${escapeHTML(entry.recommendation || "—")}</td><td><span class="pill ${approvedDecision ? "green-pill" : "neutral"}">${escapeHTML(entry.final_decision || "UZMAN KARARI BEKLİYOR")}</span></td><td>${escapeHTML(reviewer)}</td><td>${decimal(entry.risk_score)}</td></tr>`;
  }).join("");
}

async function saveHumanDecision(decision) {
  const current = state.current;
  if (!current) return;
  const buttons = $$('[data-human-decision]'); buttons.forEach((button) => { button.disabled = true; });
  try {
    await api("/api/audit", { method: "POST", body: JSON.stringify({
      decision_id: current.decisionId, decision,
      note: $("#reviewer-note").value.trim(),
    }) });
    $("#saved-banner").classList.remove("hidden");
    $("#human-review").classList.add("hidden");
    await loadAudit();
    showToast("Uzman kararı denetim günlüğüne kaydedildi.");
  } catch (err) { showToast(err.message, true); }
  finally { buttons.forEach((button) => { button.disabled = false; }); }
}

async function activateSession(session) {
  state.user = session.user;
  state.csrfToken = session.csrf_token;
  $("#landing-page").classList.add("hidden");
  $("#login-gate").classList.add("hidden");
  $("#app-shell").classList.remove("hidden");
  $("#login-error").classList.add("hidden");
  $("#login-password").value = "";
  $("#today-label").textContent = new Intl.DateTimeFormat("tr-TR", { dateStyle: "medium" }).format(new Date());
  $("#user-avatar").textContent = state.user.display_name.split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toLocaleUpperCase("tr-TR");
  $("#user-avatar").setAttribute("aria-label", `${state.user.display_name} · ${state.user.role}`);
  $(".user-card b").textContent = state.user.display_name;
  $(".user-card small").textContent = state.user.role;
  $(".nav-item[data-view=\"audit\"]").classList.toggle("hidden", !["admin", "reviewer"].includes(state.user.role));
  $("#side-mode").textContent = `Hesap: ${state.user.role}`;
  try {
    const [meta, customers] = await Promise.all([api("/api/meta"), api("/api/customers")]);
    state.meta = meta; state.customers = customers;
    $("#customer-total").textContent = meta.customer_count.toLocaleString("tr-TR");
    $("#side-mode").textContent = meta.llm_configured ? "Canlı rapor modu kullanılabilir" : "Örnek veriyle çalışma modu";
    const branches = Array.isArray(meta.branches) ? meta.branches : [];
    $("#city-branch-count").textContent = `${branches.length} şube`;
    $("#workspace-branches").innerHTML = branches.length
      ? branches.map((branch) => `<li>${escapeHTML(branch)}</li>`).join("")
      : "<li>Şube bilgisi bulunamadı</li>";
    const toggle = $("#live-toggle");
    toggle.disabled = !meta.llm_configured;
    toggle.checked = false;
    if (!toggle.dataset.listenerBound) {
      toggle.dataset.listenerBound = "true";
      toggle.addEventListener("change", () => {
        $("#mode-text").textContent = toggle.checked ? "Gemini açıklaması" : "Örnek veri modu";
        $(".mode-label .status-pulse").classList.toggle("gemini-active", toggle.checked);
      });
    }
    $("#customer-options").innerHTML = customers.map((c) => `<option value="${escapeHTML(`${c.name} · ${c.customer_id}`)}"></option>`).join("");
    if (!$("#customer-search").dataset.listenerBound) {
      $("#customer-search").dataset.listenerBound = "true";
      $("#customer-search").addEventListener("input", (event) => {
        const selected = state.customers.find((customer) => event.target.value === `${customer.name} · ${customer.customer_id}`);
        $("#selected-customer-hint").textContent = selected ? `${selected.customer_id} · Müşteri kaydı seçildi.` : `${state.customers.length.toLocaleString("tr-TR")} müşteri arasından seçim yapın.`;
      });
    }
    const initialView = location.hash.slice(1);
    switchView(initialView === "audit" && !["admin", "reviewer"].includes(state.user.role)
      ? "review" : (["review", "portfolio", "audit"].includes(initialView) ? initialView : "review"));
  } catch (err) { showError($("#review-error"), err.message); }
}

async function initialise() {
  const openLogin = () => {
    $("#landing-page").classList.add("hidden");
    $("#login-gate").classList.remove("hidden");
    $("#login-error").classList.add("hidden");
    history.replaceState(null, "", `${location.pathname}${location.search}#login`);
    $("#login-username").focus();
  };
  const showLanding = () => {
    $("#app-shell").classList.add("hidden");
    $("#login-gate").classList.add("hidden");
    $("#landing-page").classList.remove("hidden");
    history.replaceState(null, "", `${location.pathname}${location.search}`);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };
  $$('[data-open-login]').forEach((button) => button.addEventListener("click", openLogin));
  $("#back-to-landing").addEventListener("click", showLanding);
  const revealTargets = $$("[data-reveal]");
  if ("IntersectionObserver" in window && !window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const revealObserver = new IntersectionObserver((entries, observer) => entries.forEach((entry) => {
      if (entry.isIntersecting) { entry.target.classList.add("revealed"); observer.unobserve(entry.target); }
    }), { threshold: 0.12 });
    revealTargets.forEach((target) => revealObserver.observe(target));
  } else revealTargets.forEach((target) => target.classList.add("revealed"));
  $("#review-form").addEventListener("submit", handleReview);
  $("#portfolio-form").addEventListener("submit", handlePortfolio);
  $$(".nav-item").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.view)));
  const workspaceToggle = $("#workspace-toggle"), workspaceMenu = $("#workspace-menu");
  workspaceToggle.addEventListener("click", () => {
    const isOpen = workspaceToggle.getAttribute("aria-expanded") !== "true";
    workspaceToggle.setAttribute("aria-expanded", String(isOpen));
    workspaceMenu.classList.toggle("hidden", !isOpen);
  });
  const cityToggle = $("#city-toggle"), cityBranches = $("#city-branches");
  cityToggle.addEventListener("click", () => {
    const isOpen = cityToggle.getAttribute("aria-expanded") !== "true";
    cityToggle.setAttribute("aria-expanded", String(isOpen));
    cityBranches.classList.toggle("hidden", !isOpen);
  });
  document.addEventListener("click", (event) => {
    if (!event.target.closest(".workspace-picker")) {
      workspaceToggle.setAttribute("aria-expanded", "false");
      workspaceMenu.classList.add("hidden");
    }
  });
  $$(".suggestions button").forEach((button) => button.addEventListener("click", () => { $("#portfolio-question").value = button.dataset.question; $("#portfolio-form").requestSubmit(); }));
  $$('[data-human-decision]').forEach((button) => button.addEventListener("click", () => saveHumanDecision(button.dataset.humanDecision)));
  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const error = $("#login-error"), button = $("#login-form button[type=submit]");
    error.classList.add("hidden"); button.disabled = true;
    try {
      const session = await api("/api/login", { method: "POST", body: JSON.stringify({
        username: $("#login-username").value.trim(), password: $("#login-password").value,
      }) });
      await activateSession(session);
    } catch (err) { showError(error, err.message); }
    finally { button.disabled = false; }
  });
  $("#logout-button").addEventListener("click", async () => {
    try { await api("/api/logout", { method: "POST", body: JSON.stringify({}) }); }
    catch (err) { showToast(err.message, true); return; }
    state.user = null; state.csrfToken = ""; state.current = null;
    $("#app-shell").classList.add("hidden"); showLanding();
  });
  $("#export-portfolio").addEventListener("click", () => {
    if (!state.portfolio?.customers?.length) return showToast("Dışa aktarılacak sonuç yok.", true);
    const headers = ["Müşteri", "Kimlik", "Şube", "Aylık gelir", "Temerrüt", "Risk skoru"];
    const rows = state.portfolio.customers.map((c) => [c.name, c.customer_id, c.branch, c.monthly_income, c.past_defaults, c.risk_score]);
    const csv = [headers, ...rows].map((row) => row.map((item) => `"${String(item).replaceAll('"', '""')}"`).join(";")).join("\r\n");
    downloadFile("kredi-portfoy-analizi.csv", `\uFEFF${csv}`, "text/csv;charset=utf-8");
  });
  $("#export-audit").addEventListener("click", async () => {
    if (!state.audit.length) await loadAudit();
    downloadFile("kredi-denetim-gunlugu.json", JSON.stringify(state.audit, null, 2), "application/json;charset=utf-8");
  });
  $("#help-button").addEventListener("click", () => showToast("Müşteri seçip Değerlendir'e basın. Toplu sorgular için Portföy analizi sayfasına geçin."));
  try {
    const session = await api("/api/session");
    if (session.authenticated) await activateSession(session);
    else {
      if (location.hash === "#login") openLogin();
      else $("#landing-page").classList.remove("hidden");
    }
  } catch (err) {
    $("#landing-page").classList.remove("hidden");
    showToast(`Oturum durumu doğrulanamadı: ${err.message}`, true);
  }
}

initialise();

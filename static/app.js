"use strict";

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const inr = n => "Rs " + Number(n).toLocaleString("en-IN");
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

let airports = {};
let pollTimer = null;
let sseSource = null;
let currentUser = null;
let isRegisterMode = false;

// ---------------------------------------------------------------------------
// API Client
// ---------------------------------------------------------------------------
async function api(path, opts = {}) {
  const headers = { "Content-Type": "application/json", ...(opts.headers || {}) };
  const token = localStorage.getItem("fht_token");
  if (token) headers["Authorization"] = `Bearer ${token}`

  const res = await fetch(path, {
    method: opts.method || "GET",
    headers,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });

  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = data.detail || data.error || `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 3200);
}

// ---------------------------------------------------------------------------
// Navigation & Views
// ---------------------------------------------------------------------------
document.querySelectorAll(".tab").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));

function showView(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("active", t.dataset.view === name));
  document.querySelectorAll(".view").forEach(v => v.classList.toggle("hidden", v.id !== "view-" + name));
  if (name === "flights") loadFlightDepartures();
  if (name === "desk") loadDeskData();
  refresh();
}

// ---------------------------------------------------------------------------
// Authentication & Single Login
// ---------------------------------------------------------------------------
function updateUserUI() {
  const openLoginBtn = $("#btn-open-login");
  const userPill = $("#user-pill");
  const logoutBtn = $("#btn-logout");
  const adminSecretBtn = $("#btn-admin-secret");
  const deskTab = $("#tab-desk");
  const nameSpan = $("#user-display-name");
  const roleBadge = $("#user-role-badge");

  // Hide all navigation tabs before customizing view
  document.querySelectorAll('.tab').forEach(t => t.classList.add('hidden'));

  if (!currentUser) {
    openLoginBtn.classList.remove('hidden');
    userPill.classList.add('hidden');
    logoutBtn.classList.add('hidden');
    adminSecretBtn.classList.add('hidden');
    deskTab.classList.add('hidden');
    return;
  }

  if (!$("#f-name").value) $("#f-name").value = currentUser.name || "";
  if (!$("#f-email").value) $("#f-email").value = currentUser.email || "";
  openLoginBtn.classList.add('hidden');
  userPill.classList.remove('hidden');
  logoutBtn.classList.remove('hidden');

  nameSpan.textContent = currentUser.name || currentUser.email;
  const dept = currentUser.department ? ` – ${currentUser.department}` : "";
  roleBadge.textContent = `${currentUser.role}${dept}`;
  roleBadge.className = `role-badge role-${currentUser.role}`;

  // Determine which tabs should be visible for this role
  if (currentUser.role === "DESK_STAFF" || currentUser.role === "PLATFORM_ADMIN") {
    deskTab.classList.remove('hidden');
  } else {
    deskTab.classList.add('hidden');
  }

  if (currentUser.role === "PLATFORM_ADMIN") {
    adminSecretBtn.classList.remove('hidden');
  } else {
    adminSecretBtn.classList.add('hidden');
  }

  // Show appropriate tabs based on role
  if (currentUser.role === "DESK_STAFF") {
    // Desk staff sees only the desk view (plus common utilities like logout)
    document.querySelectorAll('.tab[data-view="desk"]').forEach(t => t.classList.remove('hidden'));
    // set desk as the active view
    showView('desk');
  } else {
    // All other users see the standard tabs (flights, pools, etc.)
    document.querySelectorAll('.tab').forEach(t => {
      if (t.dataset.view !== 'desk') t.classList.remove('hidden');
    });
    // If no view is active, default to "flights"
    const active = document.querySelector('.tab.active');
    if (!active) showView('flights');
  }
}

async function checkAuth() {
  const token = localStorage.getItem("fht_token");
  if (!token) { currentUser = null; updateUserUI(); return; }
  try {
    currentUser = await api("/auth/me");
    updateUserUI();
  } catch (err) {
    localStorage.removeItem("fht_token");
    currentUser = null;
    updateUserUI();
  }
}

// Login Modal
$("#btn-open-login").onclick = () => {
  $("#modal-login").classList.remove("hidden");
  $("#auth-msg").textContent = "";
};
$("#btn-close-login").onclick = () => $("#modal-login").classList.add("hidden");

$("#btn-toggle-auth-mode").onclick = () => {
  isRegisterMode = !isRegisterMode;
  $("#register-fields").classList.toggle("hidden", !isRegisterMode);
  $("#login-modal-title").textContent = isRegisterMode ? "Create Account" : "Sign In";
  $("#auth-submit-btn").textContent = isRegisterMode ? "Register & Enter" : "Sign In";
  $("#btn-toggle-auth-mode").textContent = isRegisterMode ? "Already have an account? Sign In" : "Need an account? Register";
  $("#auth-msg").textContent = "";
};

// 1-Click Demo Login buttons
$$(".quick-btn").forEach(btn => {
  btn.onclick = async () => {
    const email = btn.dataset.email;
    const password = btn.dataset.pw;
    $("#auth-email").value = email;
    $("#auth-password").value = password;
    await doLogin(email, password);
  };
});

async function doLogin(email, password) {
  const msg = $("#auth-msg");
  msg.className = "msg"; msg.textContent = "Authenticating...";
  try {
    const res = await api("/auth/login", { method: "POST", body: { email, password } });
    localStorage.setItem("fht_token", res.access_token);
    currentUser = res.user;
    updateUserUI();
    msg.className = "msg ok"; msg.textContent = `Welcome, ${currentUser.name}! (Role: ${currentUser.role})`;
    setTimeout(() => {
      $("#modal-login").classList.add("hidden");
      toast(`Signed in as ${currentUser.email} (${currentUser.role})`);
      if (currentUser.role === "DESK_STAFF") showView("desk");
    }, 600);
  } catch (e) {
    msg.className = "msg err"; msg.textContent = e.message;
  }
}

$("#auth-form").onsubmit = async (ev) => {
  ev.preventDefault();
  const email = $("#auth-email").value;
  const password = $("#auth-password").value;
  const msg = $("#auth-msg");

  if (isRegisterMode) {
    const name = $("#auth-name").value;
    const phone = $("#auth-phone").value;
    try {
      msg.className = "msg"; msg.textContent = "Registering...";
      const reg = await api("/auth/register", { method: "POST", body: { email, password, name, phone } });
      if (reg.access_token) localStorage.setItem("fht_token", reg.access_token);
      currentUser = reg.user;
      updateUserUI();
      msg.className = "msg ok"; msg.textContent = "Registered! " + (reg.message || "");
      setTimeout(() => $("#modal-login").classList.add("hidden"), 800);
    } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
  } else {
    await doLogin(email, password);
  }
};

$("#btn-logout").onclick = () => {
  localStorage.removeItem("fht_token");
  currentUser = null;
  updateUserUI();
  toast("Logged out");
  showView("pools");
};

// Secret shortcut for Admin Console (Ctrl+Shift+A)
window.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === "a") {
    e.preventDefault();
    openAdminModal();
  }
});
$("#btn-admin-secret").onclick = openAdminModal;
$("#btn-close-admin").onclick = () => $("#modal-admin").classList.add("hidden");

async function openAdminModal() {
  if (!currentUser || currentUser.role !== "PLATFORM_ADMIN") {
    toast("Admin access required");
    return;
  }
  $("#modal-admin").classList.remove("hidden");
  await loadAdminDashboard();
}

// ---------------------------------------------------------------------------
// Hidden Admin Console Operations
// ---------------------------------------------------------------------------
async function loadAdminDashboard() {
  try {
    const stats = await api("/admin/stats");
    $("#admin-stats-box").innerHTML = [
      [stats.total_users, "Total Registered Users"],
      [inr(stats.total_saved), "Passenger Savings"],
      [inr(stats.platform_revenue), "Platform Revenue"],
      [stats.total_notifications, "Total Notifications Sent"],
    ].map(([n, l]) => `<div class="stat"><div class="n">${esc(n)}</div><div class="l">${l}</div></div>`).join("");

    const cohorts = await api("/admin/cohorts");
    $("#admin-cohort-select").innerHTML = cohorts.map(c =>
      `<option value="${esc(c.id)}">${esc(c.origin)}-${esc(c.dest)} on ${esc(c.travel_date)} [${esc(c.status)} - ${c.committed}/${c.threshold}]</option>`
    ).join("");
    $("#admin-cohort-select").onchange = async () => {
      await loadAdminCohortMembers();
      await loadAdminCohortEditor();
    };
    await loadAdminCohortMembers();
    await loadAdminCohortEditor();

    const users = await api("/admin/users");
    $("#admin-users-tbody").innerHTML = users.map(u => `
      <tr>
        <td><b>${esc(u.email)}</b></td>
        <td>${esc(u.name)}</td>
        <td><span class="role-badge role-${esc(u.role)}">${esc(u.role)}</span></td>
        <td>${esc(u.department || "—")}</td>
        <td>
          <button class="sm" onclick="promoteUser('${esc(u.id)}', 'DESK_STAFF')">Set Desk</button>
          <button class="sm" onclick="promoteUser('${esc(u.id)}', 'PLATFORM_ADMIN')">Set Admin</button>
        </td>
      </tr>
    `).join("");

    const notifs = await api("/admin/notifications");
    $("#admin-notifs-log").innerHTML = notifs.slice(0, 15).map(n => `
      <div class="notif-item">
        <div class="notif-meta"><span>[${esc(n.channel)}] <b>${esc(n.type)}</b></span><span>${esc(n.created_at)}</span></div>
        <div>Status: <b>${esc(n.status)}</b> &middot; Payload: ${esc(n.payload_json)}</div>
      </div>
    `).join("") || "No notifications recorded yet.";
  } catch (err) {
    toast(err.message);
  }
}

async function loadAdminCohortEditor() {
  const cid = $("#admin-cohort-select").value;
  if (!cid) return;
  const cohorts = await api("/admin/cohorts");
  const c = cohorts.find(item => item.id === cid);
  if (!c) return;
  $("#admin-origin").innerHTML = Object.entries(airports).map(([code, name]) =>
    `<option value="${esc(code)}">${esc(code)} — ${esc(name)}</option>`).join("");
  $("#admin-dest").innerHTML = $("#admin-origin").innerHTML;
  $("#admin-origin").value = c.origin;
  $("#admin-dest").value = c.dest;
  $("#admin-date").value = c.travel_date;
  $("#admin-threshold").value = c.threshold;
  $("#admin-capacity").value = c.capacity;
}

$("#btn-admin-save-pool").onclick = async () => {
  const cid = $("#admin-cohort-select").value;
  const msg = $("#admin-pool-msg");
  try {
    await api(`/admin/cohorts/${cid}/edit`, {
      method: "POST",
      body: {
        origin: $("#admin-origin").value,
        dest: $("#admin-dest").value,
        travel_date: $("#admin-date").value,
        threshold: Number($("#admin-threshold").value),
        capacity: Number($("#admin-capacity").value),
      },
    });
    msg.className = "msg ok";
    msg.textContent = "Pool updated.";
    await loadAdminDashboard();
  } catch (e) {
    msg.className = "msg err";
    msg.textContent = e.message;
  }
};

async function loadAdminCohortMembers() {
  const cid = $("#admin-cohort-select").value;
  const box = $("#admin-cohort-members");
  if (!cid) { box.textContent = ""; return; }
  try {
    const members = await api(`/admin/cohorts/${cid}/members`);
    const waitlist = members.filter(m => m.status === "WAITLIST");
    const quoted = members.find(m => m.status === "JOINED");
    box.innerHTML = `
      <div class="fine">${members.length} total tickets · ${waitlist.length} waitlisted</div>
      ${waitlist.length
        ? `<h4>Waitlist</h4><ul>${waitlist.map(m =>
            `<li>${esc(m.name)} · ${esc(m.email)} · joined ${esc(m.joined_at)}</li>`).join("")}</ul>`
        : `<div class="fine">No waitlisted tickets.</div>`}
      ${quoted ? `<div class="fine">Joined tickets are shown above in the cohort count. The quote applies to committed seats; waitlisted tickets are not included until promoted.</div>` : ""}
    `;
  } catch (err) {
    box.textContent = err.message;
  }
}

window.promoteUser = async function(userId, newRole) {
  try {
    const dept = newRole === "DESK_STAFF" ? "FLIGHT_OPERATIONS" : (newRole === "PLATFORM_ADMIN" ? "ADMIN_HQ" : null);
    await api(`/admin/users/${userId}/role`, { method: "POST", body: { role: newRole, department: dept } });
    toast(`User updated to ${newRole}`);
    await loadAdminDashboard();
  } catch (e) { toast(e.message); }
};

$("#btn-admin-rfq").onclick = async () => {
  const cid = $("#admin-cohort-select").value;
  if (!cid) return;
  try {
    await api(`/admin/cohorts/${cid}/rfq`, { method: "POST" });
    toast("RFQ forced successfully");
    await loadAdminDashboard();
  } catch (e) { toast(e.message); }
};

$("#btn-admin-cancel").onclick = async () => {
  const cid = $("#admin-cohort-select").value;
  if (!cid) return;
  try {
    await api(`/admin/cohorts/${cid}/cancel`, { method: "POST" });
    toast("Cohort cancelled");
    await loadAdminDashboard();
  } catch (e) { toast(e.message); }
};

$("#btn-admin-reopen").onclick = async () => {
  const cid = $("#admin-cohort-select").value;
  if (!cid) return;
  try {
    await api(`/admin/cohorts/${cid}/reopen`, { method: "POST" });
    toast("Cancelled pool reopened");
    await loadAdminDashboard();
  } catch (e) { toast(e.message); }
};

// ---------------------------------------------------------------------------
// Notifications Drawer
// ---------------------------------------------------------------------------
$("#btn-notifs").onclick = async () => {
  $("#modal-notifs").classList.remove("hidden");
  try {
    const list = await api("/admin/notifications");
    $("#notifs-list").innerHTML = list.length ? list.map(n => `
      <div class="notif-item">
        <div class="notif-meta">
          <span>${n.channel === "TELEGRAM" ? "📱 Telegram" : "✉ Email"} &middot; <b>${esc(n.type)}</b></span>
          <span>${esc(new Date(n.created_at).toLocaleTimeString())}</span>
        </div>
        <div>${esc(n.payload_json)}</div>
        <div class="fine">Delivery: <span class="badge ${n.status === 'SENT' ? 'OPEN' : 'fest'}">${esc(n.status)}</span></div>
      </div>
    `).join("") : `<div style="text-align:center;color:var(--muted);padding:1.5rem">No notifications recorded yet.</div>`;
  } catch {
    $("#notifs-list").innerHTML = `<div class="notif-item">Sign in to check system notifications.</div>`;
  }
};
$("#btn-close-notifs").onclick = () => $("#modal-notifs").classList.add("hidden");

// ---------------------------------------------------------------------------
// Real-Time Domestic Flights 🇮🇳
// ---------------------------------------------------------------------------
function fillFlightAirports() {
  const select = $("#flight-airport-select");
  const opts = Object.entries(airports).map(([c, n]) =>
    `<option value="${esc(c)}">${esc(n)} (${esc(c)})</option>`
  ).join("");
  select.innerHTML = opts;
  select.value = "BLR";
  $("#flight-date-select").value = new Date().toISOString().slice(0, 10);
}

async function loadFlightDepartures() {
  const iata = $("#flight-airport-select").value || "BLR";
  const dateStr = $("#flight-date-select").value || new Date().toISOString().slice(0, 10);
  $("#departures-title").textContent = `Live Departures from ${iata} (${airports[iata] || iata})`;

  const tbody = $("#departures-tbody");
  tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:1.5rem;color:var(--muted)">Fetching live board...</td></tr>`;

  try {
    const flights = await api(`/api/flights/departures/${iata}?date=${dateStr}`);
    if (!flights.length) {
      tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;padding:1.5rem">No scheduled domestic departures found for date.</td></tr>`;
      return;
    }
    tbody.innerHTML = flights.map(f => {
      const statusClass = "status-" + (f.status || "OnTime").replace(/\s+/g, "");
      const delayInfo = f.delay_minutes > 0 ? ` <small style="color:var(--bad)">(+${f.delay_minutes}m)</small>` : "";
      return `
        <tr>
          <td class="flight-num">${esc(f.flight_number)}</td>
          <td><b>${esc(f.airline)}</b></td>
          <td>${esc(f.destination_name || f.destination)} (${esc(f.destination)})</td>
          <td>${esc(f.scheduled_departure ? f.scheduled_departure.slice(11, 16) : "—")}</td>
          <td>${esc(f.estimated_departure ? f.estimated_departure.slice(11, 16) : "—")}${delayInfo}</td>
          <td>${esc(f.terminal || "—")} / Gate ${esc(f.gate || "—")}</td>
          <td><span class="status-pill ${statusClass}">${esc(f.status || "On Time")}</span></td>
        </tr>
      `;
    }).join("");

    // Load routes
    const routesData = await api(`/api/flights/routes/${iata}`);
    const chips = $("#routes-chips");
    chips.innerHTML = (routesData.destinations || []).slice(0, 30).map(d =>
      `<span class="route-chip" title="${esc(d.name)}">${esc(d.name)} (${esc(d.iata)})</span>`
    ).join("");
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="7" style="color:var(--bad);text-align:center;padding:1.5rem">${esc(err.message)}</td></tr>`;
  }
}

$("#flight-airport-select").onchange = loadFlightDepartures;
$("#flight-date-select").onchange = loadFlightDepartures;
$("#btn-refresh-flights").onclick = loadFlightDepartures;

$("#btn-check-flight").onclick = async () => {
  const fnum = $("#flight-num-input").value.trim();
  const dateStr = $("#flight-date-select").value || new Date().toISOString().slice(0, 10);
  const box = $("#flight-status-result");
  if (!fnum) { toast("Enter a flight number, e.g. 6E-204"); return; }
  box.classList.remove("hidden");
  box.innerHTML = "Looking up flight...";
  try {
    const s = await api(`/api/flights/status/${encodeURIComponent(fnum)}?date=${dateStr}`);
    box.innerHTML = `
      <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem">
        <div>
          <span class="flight-num" style="font-size:1.1rem">${esc(s.flight_number)}</span> &middot; Status: <b>${esc(s.status)}</b>
          ${s.delay_minutes ? `<span style="color:var(--bad);margin-left:.5rem">+${s.delay_minutes} min delay</span>` : ""}
        </div>
        <div>Terminal ${esc(s.terminal)} &middot; Gate <b>${esc(s.gate)}</b> &middot; <small>Source: ${esc(s.source)}</small></div>
      </div>
    `;
  } catch (e) {
    box.innerHTML = `<span style="color:var(--bad)">${esc(e.message)}</span>`;
  }
};

// ---------------------------------------------------------------------------
// Airline Desk Portal (Flight Dept)
// ---------------------------------------------------------------------------
async function loadDeskData() {
  if (!currentUser || (currentUser.role !== "DESK_STAFF" && currentUser.role !== "PLATFORM_ADMIN")) {
    return;
  }
  try {
    const rfqs = await api("/desk/rfqs");
    const rfqsList = $("#desk-rfqs-list");
    const cohortSel = $("#desk-cohort-select");

    if (!rfqs.length) {
      rfqsList.innerHTML = `<div class="fine">No open RFQs waiting for quotation right now. When a pool reaches threshold, it appears here.</div>`;
      cohortSel.innerHTML = `<option value="">No open RFQs available</option>`;
    } else {
      rfqsList.innerHTML = rfqs.map(c => `
        <div class="card" style="margin-bottom:.5rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:.5rem">
          <div>
            <b>${esc(c.origin_city)} to ${esc(c.dest_city)}</b> (${esc(c.travel_date)})
            <div class="fine">${c.committed} passengers pooled &middot; Market Fare: ${inr(c.market_fare)}</div>
          </div>
          <button class="primary sm" onclick="selectDeskRfq('${esc(c.id)}')">Quote This Pool</button>
        </div>
      `).join("");

      cohortSel.innerHTML = rfqs.map(c =>
        `<option value="${esc(c.id)}">${esc(c.origin)}->${esc(c.dest)} (${esc(c.travel_date)}) - ${c.committed} pax</option>`
      ).join("");
    }

    // Manifests for ticketed cohorts
    const allCohorts = await api("/desk/cohorts");
    const passengerCohortSel = $("#desk-passenger-cohort");
    passengerCohortSel.innerHTML = allCohorts.length
      ? allCohorts.map(c =>
        `<option value="${esc(c.id)}">${esc(c.origin)}-${esc(c.dest)} · ${esc(c.travel_date)} · ${esc(c.status)}</option>`
      ).join("")
      : `<option value="">No active pools</option>`;
    await loadDeskPassengers();
    const ticketed = allCohorts.filter(c => c.status === "TICKETED");
    $("#desk-manifest-list").innerHTML = ticketed.length ? ticketed.map(c => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:.5rem 0;border-bottom:1px solid var(--line)">
        <div><b>${esc(c.origin)}-${esc(c.dest)}</b> (${esc(c.travel_date)}) &middot; ${c.ticketed || c.committed} passengers</div>
        <a href="/desk/pnr-export/${esc(c.id)}" download="manifest_${esc(c.id)}.csv" class="primary sm" style="text-decoration:none;display:inline-block">⬇ Download PNR CSV</a>
      </div>
    `).join("") : `<div class="fine">No ticketed cohorts with manifests yet.</div>`;

    // Submissions
    const subs = await api("/desk/my-submissions");
    $("#desk-submissions-list").innerHTML = subs.length ? subs.map(s => `
      <div style="display:flex;justify-content:space-between;align-items:center;padding:.5rem 0;border-bottom:1px solid var(--line);font-size:.88rem">
        <div>Cohort <code>${esc(s.cohort_id)}</code> &middot; <b>${inr(s.per_seat)}</b>/seat (${s.seats_avail} seats)</div>
        <div>
          <span class="badge ${s.status === 'COMPLETED' ? 'OPEN' : 'QUOTED'}">${esc(s.status)}</span>
          ${currentUser.role === "PLATFORM_ADMIN" && s.status === "PENDING"
            ? `<button type="button" class="sm" onclick="completeRfq(event, '${esc(s.id)}')">Mark Completed</button>` : ""}
        </div>
      </div>
    `).join("") : `<div class="fine">No quotes submitted by your account yet.</div>`;
  } catch (err) {
    toast(err.message);
  }
}

async function loadDeskPassengers() {
  const cohortId = $("#desk-passenger-cohort").value;
  const box = $("#desk-passenger-review");
  if (!cohortId) {
    box.innerHTML = `<div class="fine">No pool selected.</div>`;
    return;
  }
  try {
    const data = await api(`/desk/cohorts/${encodeURIComponent(cohortId)}/passengers`);
    const waitlist = data.passengers.filter(p => p.status === "WAITLIST");
    box.innerHTML = `
      <div class="fine">${esc(data.cohort.origin)} → ${esc(data.cohort.dest)} ·
        ${esc(data.cohort.travel_date)} · ${data.passengers.length} total passengers ·
        ${waitlist.length} waitlisted</div>
      <div style="overflow-x:auto;margin-top:.5rem">
        <table class="flight-table" style="font-size:.82rem">
          <thead><tr><th>#</th><th>Name</th><th>Email</th><th>Status</th><th>PNR</th><th>Joined</th></tr></thead>
          <tbody>${data.passengers.map((p, index) => `
            <tr>
              <td>${p.waitlist_position ? `WL #${p.waitlist_position}` : index + 1}</td>
              <td><b>${esc(p.name)}</b></td><td>${esc(p.email)}</td>
              <td><span class="badge ${esc(p.status)}">${esc(p.status)}</span></td>
              <td>${esc(p.pnr || "—")}</td><td>${esc(p.joined_at)}</td>
            </tr>`).join("")}</tbody>
        </table>
      </div>`;
  } catch (err) {
    box.innerHTML = `<div class="msg err">${esc(err.message)}</div>`;
  }
}

$("#btn-load-passengers").onclick = loadDeskPassengers;
$("#desk-passenger-cohort").onchange = loadDeskPassengers;

window.completeRfq = async function(event, submissionId) {
  event?.preventDefault();
  try {
    await api(`/admin/rfq-submissions/${encodeURIComponent(submissionId)}/complete`, { method: "POST" });
    toast("RFQ marked completed");
    await loadDeskData();
  } catch (err) {
    toast(err.message);
  }
};

window.selectDeskRfq = function(cohortId) {
  $("#desk-cohort-select").value = cohortId;
  $("#desk-per-seat").focus();
};

$("#btn-refresh-rfqs").onclick = loadDeskData;

$("#desk-quote-form").onsubmit = async (e) => {
  e.preventDefault();
  const cohortId = $("#desk-cohort-select").value;
  const perSeat = Number($("#desk-per-seat").value);
  const seats = Number($("#desk-seats").value);
  const deposit = Number($("#desk-deposit").value);
  const notes = $("#desk-notes").value;
  const msg = $("#desk-quote-msg");

  if (!cohortId) { toast("Select a cohort RFQ first"); return; }
  try {
    msg.className = "msg"; msg.textContent = "Submitting...";
    await api(`/desk/rfqs/${cohortId}/quote`, {
      method: "POST",
      body: { per_seat: perSeat, seats_available: seats, deposit_pct: deposit, notes },
    });
    msg.className = "msg ok"; msg.textContent = "Quote submitted to pool!";
    toast("Quote submitted for cohort " + cohortId);
    await loadDeskData();
  } catch (err) {
    msg.className = "msg err"; msg.textContent = err.message;
  }
};

// ---------------------------------------------------------------------------
// Pools Form & Estimates
// ---------------------------------------------------------------------------
function fillAirportSelects() {
  const opts = Object.entries(airports).map(([c, n]) => `<option value="${esc(c)}">${esc(n)} (${esc(c)})</option>`).join("");
  $("#f-origin").innerHTML = opts;
  $("#f-dest").innerHTML = opts;
  $("#f-origin").value = "BLR";
  $("#f-dest").value = "DEL";
}

function defaultDate() {
  const d = new Date(); d.setDate(d.getDate() + 10);
  return d.toISOString().slice(0, 10);
}

async function updateEstimate() {
  const o = $("#f-origin").value, d = $("#f-dest").value, dt = $("#f-date").value;
  const box = $("#estimate");
  if (!o || !d || !dt) { box.textContent = ""; return; }
  try {
    const e = await api(`/api/estimate?origin=${o}&dest=${d}&date=${dt}`);
    const fest = e.festival ? ` <span class="badge fest">${esc(e.festival)} surge</span>` : "";
    box.innerHTML = `Typical solo fare <b>${inr(e.market_fare)}</b>${fest}. ` +
      `Expected group fare <b>${inr(e.expected_group_fare_low)} to ${inr(e.expected_group_fare_high)}</b> ` +
      `+ ${inr(e.service_fee)} service fee. <span class="fine">Domestic airline group inventory rates.</span>`;
  } catch (err) { box.textContent = err.message; }
}
["f-origin", "f-dest", "f-date"].forEach(id => $("#" + id).addEventListener("change", updateEstimate));

$("#join-form").addEventListener("submit", async ev => {
  ev.preventDefault();
  const msg = $("#join-msg");
  msg.textContent = ""; msg.className = "msg";
  try {
    const m = await api("/api/join", {
      method: "POST",
      body: {
        name: $("#f-name").value, email: $("#f-email").value,
        origin: $("#f-origin").value, dest: $("#f-dest").value, date: $("#f-date").value,
        flex_days: $("#f-flex").value, price_cap: $("#f-cap").value || null,
        tickets: Number($("#f-tickets").value || 1),
      },
    });
    const savedMembers = JSON.parse(localStorage.getItem("fht_members") || "[]");
    const createdMembers = (m.members || [m]).map(member => member.id);
    for (const memberId of createdMembers) {
      if (!savedMembers.includes(memberId)) savedMembers.push(memberId);
    }
    localStorage.setItem("fht_members", JSON.stringify(savedMembers));
    localStorage.setItem("fht_member", createdMembers[0]);
    msg.className = "msg ok";
    msg.textContent = m.already_joined
      ? "You already joined this route and date. Opening your existing pool entry..."
      : m.status === "WAITLIST"
      ? "Pool is full: you are on the waitlist."
      : `${m.ticket_count || 1} ticket${(m.ticket_count || 1) === 1 ? "" : "s"} added. Redirecting to your trip...`;
    setTimeout(() => showView("trip"), 600);
  } catch (err) {
    msg.className = "msg err"; msg.textContent = err.message;
  }
});

// ---------------------------------------------------------------------------
// Pools List
// ---------------------------------------------------------------------------
function poolCard(c) {
  const pct = Math.min(100, Math.round(100 * c.committed / c.threshold));
  const full = c.committed >= c.threshold;
  const fest = c.festival ? `<span class="badge fest">${esc(c.festival)}</span>` : "";
  let price = `<div class="price"><span>Solo fare <s>${inr(c.market_fare)}</s></span></div>`;
  if (c.quote) {
    price = `<div class="price"><s>${inr(c.market_fare)}</s> <strong>${inr(c.quote.per_seat + c.quote.service_fee)}</strong>
      <span class="save">save ${inr(c.quote.saving_per_seat - c.quote.service_fee)} + fee</span></div>`;
  }
  const left = c.status === "OPEN"
    ? (c.spots_to_threshold > 0 ? `${c.spots_to_threshold} more to unlock group fare` : "Threshold reached")
    : c.status === "QUOTED" ? "Quote ready, members deciding"
    : c.status === "TICKETED" ? "Tickets issued" : "Cancelled";
  return `<div class="card pool">
    <div class="route">${esc(c.origin_city)} to ${esc(c.dest_city)}
      <span class="badge ${esc(c.status)}">${esc(c.status)}</span>${fest}</div>
    <div class="meta">${esc(c.travel_date)} &middot; ${c.waitlist ? c.waitlist + " on waitlist" : "seats open"}</div>
    <div class="count"><span><b>${c.committed}</b> / ${c.threshold} joined</span><span>${esc(left)}</span></div>
    <div class="progress ${full ? "full" : ""}"><i style="width:${pct}%"></i></div>
    ${price}
  </div>`;
}

async function loadPools() {
  const cohorts = await api("/api/cohorts");
  const routes = [...new Set(cohorts.map(c => `${c.origin}-${c.dest}`))].sort();
  const sel = $("#filter-route"), cur = sel.value;
  sel.innerHTML = `<option value="">All routes</option>` + routes.map(r => `<option>${esc(r)}</option>`).join("");
  sel.value = routes.includes(cur) ? cur : "";
  const shown = cohorts.filter(c => !sel.value || `${c.origin}-${c.dest}` === sel.value);
  $("#pool-list").innerHTML = shown.length ? shown.map(poolCard).join("") :
    `<div class="card">No pools yet. Be the first to start one.</div>`;
}

// ---------------------------------------------------------------------------
// My Trip & SSE Live Updates
// ---------------------------------------------------------------------------
async function loadTrip() {
  const editingPreferences = $("#quote-notification-preferences");
  if (editingPreferences && editingPreferences.contains(document.activeElement)) return;
  await loadMyPools();
  await loadMyTickets();
  const id = localStorage.getItem("fht_member");
  const empty = $("#trip-empty"), box = $("#trip");
  if (!id) { empty.classList.remove("hidden"); box.classList.add("hidden"); return; }
  let m;
  try { m = await api("/api/members/" + id); }
  catch { localStorage.removeItem("fht_member"); empty.classList.remove("hidden"); box.classList.add("hidden"); return; }
  empty.classList.add("hidden"); box.classList.remove("hidden");
  const c = m.cohort;
  const pct = Math.min(100, Math.round(100 * c.committed / c.threshold));

  // Connect SSE for live updates if not connected to this cohort
  connectSSE(c.id);

  let action = "";
  if (m.status === "TICKETED") {
    action = `<div><div class="fine">Official Airline Booking Reference</div><div class="pnr">${esc(m.pnr)}</div>
      <p class="msg ok" style="margin-top:.5rem">Ticket confirmed! Real-time flight tracking is available in Live Flights.</p></div>`;
  } else if (m.status === "JOINED" && c.status === "QUOTED" && c.quote) {
    const total = c.quote.per_seat + c.quote.service_fee;
    const over = c.quote.per_seat > m.price_cap;
    action = `<div>
      <table class="quote-table">
        ${(c.quote.all_quotes || []).map((q, i) => `<tr class="${i === 0 ? "best" : ""}"><td>${esc(q.airline)}</td><td>${inr(q.per_seat)}</td></tr>`).join("")}
      </table>
      <p class="fine">Best fare ${inr(c.quote.per_seat)} + ${inr(c.quote.service_fee)} fee = <b>${inr(total)}</b> per seat.
        Valid until ${esc(new Date(c.quote_expires).toLocaleString())}.</p>
      ${over ? `<p class="msg err">This quote exceeds your price cap of ${inr(m.price_cap)}.</p>` : ""}
      <div class="row"><button class="primary" id="btn-accept" ${over ? "disabled" : ""}>Accept and lock seat</button>
      <button class="danger" id="btn-decline">Decline</button></div></div>`;
  } else if (m.status === "ACCEPTED") {
    action = `<p class="msg ok">You accepted the quote! Waiting for final ticketing confirmation.</p>`;
  } else if (m.status === "JOINED" || m.status === "WAITLIST") {
    const wl = m.status === "WAITLIST" ? `<p>You are #${m.waitlist_position} on the waitlist.</p>` : "";
    action = `${wl}<div class="row"><button class="danger" id="btn-decline">Leave pool and release hold</button></div>`;
  } else {
    action = `<p>Status: <b>${esc(m.status)}</b>. Your hold has been released.</p>`;
  }

  const quoteNotificationPanel = c.quote && ["QUOTED", "TICKETED"].includes(c.status)
    ? (currentUser ? `
      <form id="quote-notification-preferences" class="notification-prefs">
        <h3>Quote and booking alerts</h3>
        <p class="fine">Choose where we send updates for your tickets. Email goes to ${esc(currentUser.email)}.</p>
        <div class="row">
          <label class="inline"><input id="pref-email-enabled" type="checkbox"> Email (Gmail)</label>
          <label class="inline"><input id="pref-telegram-enabled" type="checkbox"> Telegram</label>
        </div>
        <label class="telegram-chat-label">Telegram chat ID
          <input id="pref-telegram-chat-id" autocomplete="off" placeholder="Paste the chat ID from your bot">
        </label>
        <div id="pref-notification-status" class="fine"></div>
        <button type="submit" class="sm">Save settings & send quote</button>
      </form>`
      : `<div class="notification-prefs"><h3>Quote and booking alerts</h3>
          <p class="fine">Sign in to choose email and Telegram alerts for this quote.</p>
          <button type="button" class="sm" onclick="document.querySelector('#btn-open-login').click()">Sign in</button>
        </div>`)
    : "";

  box.innerHTML = `<div class="card">
    <div class="trip-head">
      <div>
        <div class="route" style="font-size:1.3rem;font-weight:700">${esc(c.origin_city)} to ${esc(c.dest_city)}</div>
        <div class="meta">${esc(c.travel_date)} &middot; ${esc(m.name)} <span class="badge ${esc(m.status)}">${esc(m.status)}</span></div>
      </div>
      ${m.hold_amount ? `<div class="fine">Refundable hold placed: <b>${inr(m.hold_amount)}</b></div>` : ""}
    </div>
    <div class="count" style="margin-top:1rem"><span><b>${c.committed}</b> / ${c.threshold} joined</span><span>${esc(c.status)}</span></div>
    <div class="progress ${c.committed >= c.threshold ? "full" : ""}"><i style="width:${pct}%"></i></div>
    <div style="margin-top:1rem">${action}${quoteNotificationPanel}</div>
  </div>
  <div class="card"><h3>Pool activity</h3>
    <ul class="timeline">${c.events.map(e => `<li><time>${esc(new Date(e.at).toLocaleTimeString())}</time>${esc(e.message)}</li>`).join("")}</ul>
  </div>`;

  const acc = $("#btn-accept"), dec = $("#btn-decline");
  if (acc) acc.onclick = () => act(id, "accept");
  if (dec) dec.onclick = () => act(id, "decline");

  const preferencesForm = $("#quote-notification-preferences");
  if (preferencesForm) {
    const status = $("#pref-notification-status");
    try {
      const preferences = await api("/auth/notification-preferences");
      $("#pref-email-enabled").checked = preferences.email_enabled;
      $("#pref-telegram-enabled").checked = preferences.telegram_enabled;
      status.textContent = [
        preferences.telegram_linked ? "Telegram account linked." : "To use Telegram, enter your bot chat ID.",
        preferences.gmail_configured ? "Gmail delivery is configured." : "Gmail SMTP is not configured by the app owner yet.",
        preferences.telegram_bot_configured ? "Telegram bot is configured." : "Telegram bot is not configured by the app owner yet.",
      ].join(" ");
    } catch (err) {
      status.textContent = err.message;
    }
    preferencesForm.onsubmit = async event => {
      event.preventDefault();
      status.textContent = "Saving alert settings...";
      try {
        const saved = await api("/auth/notification-preferences", {
          method: "PUT",
          body: {
            email_enabled: $("#pref-email-enabled").checked,
            telegram_enabled: $("#pref-telegram-enabled").checked,
            telegram_chat_id: $("#pref-telegram-chat-id").value.trim() || null,
          },
        });
        const alert = await api(`/api/members/${encodeURIComponent(id)}/quote-alert`, {
          method: "POST",
          body: {},
        });
        const simulated = (saved.email_enabled && !saved.gmail_configured) ||
          (saved.telegram_enabled && !saved.telegram_bot_configured);
        status.textContent = alert.sent
          ? `Settings saved. Quote sent to your selected channel${simulated ? " (simulated until the app owner configures delivery)" : "s"}.`
          : alert.message;
        toast(alert.sent ? "Quote sent to selected channels" : "Alert settings saved");
      } catch (err) {
        status.textContent = err.message;
      }
    };
  }
}

async function loadMyPools() {
  const box = $("#my-pools-list");
  try {
    let pools;
    if (currentUser) {
      const data = await api("/api/my-pools");
      pools = data.pools;
    } else {
      const memberIds = JSON.parse(localStorage.getItem("fht_members") || "[]");
      const members = await Promise.all(memberIds.map(memberId =>
        api(`/api/members/${encodeURIComponent(memberId)}`).catch(() => null)
      ));
      pools = members.filter(Boolean).map(member => ({
        id: member.id,
        name: member.name,
        status: member.status,
        cohort_id: member.cohort.id,
        origin: member.cohort.origin,
        dest: member.cohort.dest,
        travel_date: member.cohort.travel_date,
        cohort_status: member.cohort.status,
      }));
    }

    const activeStatuses = new Set(["JOINED", "WAITLIST", "ACCEPTED", "TICKETED"]);
    const activePools = pools.filter(pool => activeStatuses.has(pool.status));
    const removedPools = pools.filter(pool => !activeStatuses.has(pool.status));
    const selectedId = localStorage.getItem("fht_member");
    if (!activePools.some(pool => pool.id === selectedId)) {
      if (activePools.length) localStorage.setItem("fht_member", activePools[0].id);
      else localStorage.removeItem("fht_member");
    }

    if (!pools.length) {
      box.textContent = currentUser
        ? "No pool tickets found for this account."
        : "Join a pool to see each ticket here.";
      return;
    }

    const entry = (pool, removed = false) => `
      <div style="display:flex;justify-content:space-between;align-items:center;gap:.75rem;flex-wrap:wrap;padding:.65rem 0;border-bottom:1px solid var(--line)">
        <div>
          <b>${esc(pool.origin)} to ${esc(pool.dest)}</b> - ${esc(pool.travel_date)}
          <div class="fine">${esc(pool.name)} - <span class="badge ${esc(pool.status)}">${removed ? "REMOVED" : esc(pool.status)}</span>${removed ? ` - ${esc(pool.status)}` : ` - pool ${esc(pool.cohort_status)}`}</div>
        </div>
        ${removed ? "" : `<button type="button" class="sm" onclick="selectMyPool('${esc(pool.id)}')">View ticket</button>`}
      </div>`;
    box.innerHTML = `<div><b>Active tickets (${activePools.length})</b>${activePools.map(pool => entry(pool)).join("") || `<p class="fine">No active tickets.</p>`}</div>` +
      (removedPools.length
        ? `<details class="removed-pools"><summary>Removed or completed tickets (${removedPools.length})</summary>${removedPools.map(pool => entry(pool, true)).join("")}</details>`
        : "");
  } catch (err) {
    box.textContent = err.message;
  }
}

window.selectMyPool = function(memberId) {
  localStorage.setItem("fht_member", memberId);
  loadTrip();
};

async function loadMyTickets() {
  const box = $("#my-tickets-list");
  if (!currentUser) {
    box.textContent = "Sign in to see all tickets booked under your account.";
    return;
  }
  try {
    const data = await api("/api/my-tickets");
    if (!data.tickets.length) {
      box.textContent = "No completed bookings are stored for this account yet.";
      return;
    }
    box.innerHTML = `
      <div class="fine">${data.count} ticket${data.count === 1 ? "" : "s"} stored in the booking database.</div>
      <div style="overflow-x:auto;margin-top:.5rem">
        <table class="flight-table" style="font-size:.84rem">
          <thead><tr><th>PNR</th><th>Passenger</th><th>Route</th><th>Date</th><th>Airline</th><th>Total</th></tr></thead>
          <tbody>${data.tickets.map(t => `
            <tr>
              <td><b>${esc(t.pnr)}</b></td><td>${esc(t.name)}</td>
              <td>${esc(t.origin)} → ${esc(t.dest)}</td><td>${esc(t.travel_date)}</td>
              <td>${esc(t.airline || "—")}</td><td>${t.total == null ? "—" : inr(t.total)}</td>
            </tr>`).join("")}</tbody>
        </table>
      </div>`;
  } catch (err) {
    box.textContent = err.message;
  }
}

function connectSSE(cohortId) {
  if (sseSource && sseSource._cohortId === cohortId) return;
  if (sseSource) sseSource.close();
  try {
    sseSource = new EventSource(`/api/live/cohorts/${cohortId}`);
    sseSource._cohortId = cohortId;
    sseSource.onmessage = (e) => {
      // Live event received from backend
      loadTrip();
    };
  } catch {}
}

async function act(id, what) {
  try {
    await api(`/api/members/${id}/${what}`, { method: "POST", body: {} });
    toast(what === "accept" ? "Seat locked!" : "Hold released");
  } catch (e) { toast(e.message); }
  refresh();
}

// ---------------------------------------------------------------------------
// Impact View
// ---------------------------------------------------------------------------
async function loadImpact() {
  const s = await api("/api/summary");
  $("#impact").innerHTML = [
    [s.passengers, "Passengers Ticketed"],
    [inr(s.total_saved), "Saved vs Solo Fares"],
    [s.ticketed_cohorts, "Cohorts Completed"],
    [inr(s.platform_revenue), "Platform Fees Earned"],
  ].map(([n, l]) => `<div class="stat"><div class="n">${esc(n)}</div><div class="l">${l}</div></div>`).join("");

  const cohorts = await api("/api/cohorts");
  const sel = $("#demo-cohort"), cur = sel.value;
  sel.innerHTML = cohorts.map(c =>
    `<option value="${esc(c.id)}">${esc(c.origin)}-${esc(c.dest)} ${esc(c.travel_date)} (${esc(c.status)}, ${c.committed}/${c.threshold})</option>`).join("");
  if (cur) sel.value = cur;
}

async function demo(action, body = {}) {
  const cid = $("#demo-cohort").value;
  const msg = $("#demo-msg");
  try {
    if (action === "advance") await api("/admin/demo/advance", { method: "POST", body: { hours: 25 } });
    else if (action === "fill") await api(`/admin/demo/fill/${cid}`, { method: "POST" });
    else if (action === "accept_all") await api(`/admin/demo/accept-all/${cid}`, { method: "POST" });
    msg.className = "msg ok"; msg.textContent = "Done";
  } catch (e) { msg.className = "msg err"; msg.textContent = e.message; }
  refresh();
}
$("#demo-fill").onclick = () => demo("fill");
$("#demo-accept").onclick = () => demo("accept_all");
$("#demo-advance").onclick = () => demo("advance");

// ---------------------------------------------------------------------------
// Periodic Refresh
// ---------------------------------------------------------------------------
async function refresh() {
  const activeTab = document.querySelector(".tab.active");
  const active = activeTab ? activeTab.dataset.view : "pools";
  try {
    if (active === "pools") await loadPools();
    else if (active === "trip") await loadTrip();
    else if (active === "impact") await loadImpact();
  } catch (e) { /* ignore periodic poll errors */ }
}

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------
(async function init() {
  try {
    airports = await api("/api/airports");
  } catch {
    airports = { "BLR": "Bengaluru", "DEL": "Delhi", "BOM": "Mumbai", "HYD": "Hyderabad" };
  }
  fillAirportSelects();
  fillFlightAirports();

  let cfg = { default_date: defaultDate(), min_date: new Date(Date.now() + 864e5).toISOString().slice(0, 10) };
  try { cfg = await api("/api/config"); } catch {}
  $("#f-date").value = cfg.default_date;
  $("#f-date").min = cfg.min_date;

  await checkAuth();
  await updateEstimate();
  await refresh();

  pollTimer = setInterval(refresh, 5000);
})();

/* Whodunit card: why did that change? */
const WD_VERSION = "0.2.0";

const ICONS = {
  automation: "mdi:robot-outline",
  script: "mdi:script-text-outline",
  trigger: "mdi:flash-outline",
  user: "mdi:account",
  device: "mdi:chip",
  source: "mdi:clock-outline",
  loop: "mdi:sync-alert",
  probable: "mdi:timer-sand",
  unknown: "mdi:help-circle-outline",
};
const LABELS = {
  automation: "Automation",
  script: "Script",
  trigger: "Triggered by",
  user: "Person",
  device: "Device / integration",
  source: "Trigger",
  loop: "Loop back to",
  probable: "Probably",
  unknown: "Unknown",
};
const TABS = [
  ["live", "Live", "mdi:pulse"],
  ["why", "Why?", "mdi:magnify"],
  ["fights", "Fights", "mdi:boxing-glove"],
  ["busy", "Busy", "mdi:run-fast"],
];

const esc = (s) =>
  String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const fmtTime = (at) => {
  if (!at) return "";
  const d = new Date(at * 1000);
  const today = new Date().toDateString() === d.toDateString();
  return today
    ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })
    : d.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
};

const ago = (at) => {
  const s = Math.max(0, Date.now() / 1000 - at);
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  return `${Math.round(s / 3600)}h ago`;
};

const changeText = (c) => (c.attr_only ? `updated (${c.new})` : c.old == null ? c.new : `${c.old} → ${c.new}`);

class WhodunitCard extends HTMLElement {
  static getStubConfig() {
    return {};
  }

  setConfig(config) {
    this._config = { title: "Whodunit", ...config };
    const tabs = this._config.tabs || TABS.map((t) => t[0]);
    this._tabs = TABS.filter((t) => tabs.includes(t[0]));
    this._tab = this._config.default_tab || (this._config.entity ? "why" : this._tabs[0]?.[0] || "live");
    this._whyEntity = this._config.entity || "";
    this._built = false;
    if (this.isConnected) this._build();
  }

  set hass(hass) {
    const first = !this._hass;
    this._hass = hass;
    if (!this._built) this._build();
    if (first || this._conn !== hass.connection) this._connect();
    this._updateDatalist();
  }

  connectedCallback() {
    if (this._hass && !this._unsub) this._connect();
  }

  disconnectedCallback() {
    this._disconnect();
  }

  getCardSize() {
    return 7;
  }

  getGridOptions() {
    return { columns: 12, min_columns: 6, rows: 7, min_rows: 4 };
  }

  // ------------------------------------------------------------ data

  async _connect() {
    this._disconnect();
    const hass = this._hass;
    if (!hass?.connection) return;
    this._conn = hass.connection;
    this._feed = [];
    this._error = null;
    try {
      const recent = await hass.callWS({ type: "whodunit/recent", limit: 50 });
      this._feed = recent.changes;
      this._unsub = await hass.connection.subscribeMessage((ev) => this._onEvent(ev), { type: "whodunit/subscribe" });
    } catch (err) {
      this._error = err?.message || String(err);
    }
    this._renderPane();
    this._loadTab();
  }

  _disconnect() {
    if (this._unsub) {
      Promise.resolve(this._unsub).then((u) => typeof u === "function" && u()).catch(() => {});
      this._unsub = null;
    }
  }

  _onEvent(ev) {
    if (ev.kind === "change") {
      this._feed.unshift(ev.data);
      this._feed.length = Math.min(this._feed.length, 100);
      if (this._tab === "live") this._renderPane();
      if (this._tab === "why" && ev.data.entity_id === this._whyEntity) this._loadWhy();
    } else {
      this._fightsDirty = true;
      this._updateBadge(ev);
      if (this._tab === "fights") this._loadFights();
    }
  }

  async _loadTab() {
    if (this._tab === "why") return this._loadWhy();
    if (this._tab === "fights") return this._loadFights();
    if (this._tab === "busy") return this._loadBusy();
    return this._renderPane();
  }

  async _loadWhy() {
    const id = this._whyEntity;
    if (!id) {
      this._why = null;
      return this._renderPane();
    }
    try {
      this._why = await this._hass.callWS({ type: "whodunit/explain", entity_id: id, limit: 10 });
    } catch (err) {
      this._why = { error: err?.message || String(err) };
    }
    this._renderPane();
  }

  async _loadFights() {
    try {
      this._fights = await this._hass.callWS({ type: "whodunit/fights" });
    } catch (err) {
      this._fights = { error: err?.message || String(err) };
    }
    this._renderPane();
  }

  async _loadBusy() {
    try {
      this._busy = await this._hass.callWS({ type: "whodunit/busy", limit: 15 });
    } catch (err) {
      this._busy = { error: err?.message || String(err) };
    }
    this._renderPane();
  }

  // ------------------------------------------------------------- dom

  _build() {
    if (!this._config) return;
    if (!this.shadowRoot) this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>${STYLE}</style>
      <ha-card>
        <div class="head">
          <div class="title"><ha-icon icon="mdi:magnify-scan"></ha-icon>${esc(this._config.title)}</div>
          <div class="tabs" role="tablist">
            ${this._tabs
              .map(
                ([k, label, icon]) =>
                  `<button role="tab" data-tab="${k}" class="${k === this._tab ? "on" : ""}">
                     <ha-icon icon="${icon}"></ha-icon><span>${label}</span>${k === "fights" ? '<i class="badge" hidden></i>' : ""}
                   </button>`
              )
              .join("")}
          </div>
        </div>
        <div class="why-bar" ${this._tab === "why" ? "" : "hidden"}>
          <input list="wd-entities" placeholder="Pick or type an entity, e.g. light.hallway" value="${esc(this._whyEntity)}" />
          <datalist id="wd-entities"></datalist>
        </div>
        <div class="pane"></div>
      </ha-card>`;
    const root = this.shadowRoot;
    root.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => this._setTab(b.dataset.tab)));
    const input = root.querySelector(".why-bar input");
    const go = () => {
      const v = input.value.trim();
      if (v && v !== this._whyEntity) {
        this._whyEntity = v;
        this._loadWhy();
      }
    };
    input.addEventListener("change", go);
    input.addEventListener("keydown", (e) => e.key === "Enter" && go());
    root.querySelector(".pane").addEventListener("click", (e) => this._onPaneClick(e));
    this._built = true;
    this._datalistFor = null;
    this._renderPane();
  }

  _setTab(tab) {
    this._tab = tab;
    const root = this.shadowRoot;
    root.querySelectorAll("[data-tab]").forEach((b) => b.classList.toggle("on", b.dataset.tab === tab));
    root.querySelector(".why-bar").hidden = tab !== "why";
    this._loadTab();
  }

  _explain(entityId) {
    this._whyEntity = entityId;
    this.shadowRoot.querySelector(".why-bar input").value = entityId;
    this._setTab("why");
  }

  _onPaneClick(e) {
    const el = e.target.closest("[data-why],[data-info],[data-trace]");
    if (!el) return;
    e.preventDefault();
    e.stopPropagation();
    if (el.dataset.why) return this._explain(el.dataset.why);
    if (el.dataset.trace) {
      history.pushState(null, "", el.dataset.trace);
      window.dispatchEvent(new CustomEvent("location-changed", { detail: { replace: false } }));
      return;
    }
    if (el.dataset.info) {
      this.dispatchEvent(
        new CustomEvent("hass-more-info", { bubbles: true, composed: true, detail: { entityId: el.dataset.info } })
      );
    }
  }

  _updateDatalist() {
    const list = this.shadowRoot?.querySelector("#wd-entities");
    if (!list || this._datalistFor === this._hass.states) return;
    // Only rebuild when the set of entities changes, not on every state update
    const ids = Object.keys(this._hass.states);
    if (this._datalistCount === ids.length && list.childElementCount) return;
    this._datalistFor = this._hass.states;
    this._datalistCount = ids.length;
    const skip = /^(sensor|binary_sensor|update|button|event|device_tracker|zone|image|camera|weather|sun|conversation|tts|stt)\./;
    list.innerHTML = ids
      .filter((id) => !skip.test(id))
      .sort()
      .map((id) => `<option value="${esc(id)}">${esc(this._hass.states[id].attributes.friendly_name || "")}</option>`)
      .join("");
  }

  _updateBadge(ev) {
    const badge = this.shadowRoot?.querySelector(".badge");
    if (!badge) return;
    if (ev?.kind === "fight_started") this._activeFights = (this._activeFights || 0) + 1;
    if (ev?.kind === "fight_ended") this._activeFights = Math.max(0, (this._activeFights || 1) - 1);
    badge.hidden = !this._activeFights;
    badge.textContent = this._activeFights || "";
  }

  _renderPane() {
    const pane = this.shadowRoot?.querySelector(".pane");
    if (!pane) return;
    if (this._error) {
      pane.innerHTML = `<div class="empty err"><ha-icon icon="mdi:alert"></ha-icon>${esc(this._error)}<br><small>Is the Whodunit integration set up?</small></div>`;
      return;
    }
    const render = { live: this._renderLive, why: this._renderWhy, fights: this._renderFights, busy: this._renderBusy }[this._tab];
    pane.innerHTML = render.call(this);
  }

  // ------------------------------------------------------------ views

  _linkHtml(link, opts = {}) {
    const icon = ICONS[link.kind] || ICONS.unknown;
    let label = LABELS[link.kind] || link.kind;
    let name = esc(link.name);
    if (link.kind === "probable") {
      const conf = link.detail === "possibly" ? "Possibly" : "Probably";
      const delay = opts.changeAt && link.at ? ` · ${Math.max(0, Math.round(opts.changeAt - link.at))}s before` : "";
      return `<li class="k-probable" title="The device reported this on its own. This command was sent ${
        link.detail === "possibly" ? "to something in the same area" : "to it"
      } shortly before.">
        <ha-icon icon="${ICONS.probable}"></ha-icon>
        <div><div class="lbl">${conf}${delay}</div><div class="nm">${name} <span class="muted">command</span></div></div>
      </li>`;
    }
    if (link.id && link.kind !== "user") name = `<a href="#" data-info="${esc(link.id)}">${name}</a>`;
    let extra = "";
    if (link.kind === "trigger" && link.detail) extra = `<span class="chg">${esc(link.detail)}</span>`;
    if (link.kind === "trigger") extra += ` <a href="#" class="mini" data-why="${esc(link.id)}">why?</a>`;
    if (link.kind === "automation") {
      const cfgId = this._hass?.states[link.id]?.attributes?.id;
      if (cfgId) extra += ` <a href="#" class="mini" data-trace="/config/automation/trace/${encodeURIComponent(cfgId)}">trace</a>`;
      if (link.detail && opts.showSource) extra += ` <span class="muted">(${esc(link.detail)})</span>`;
    }
    if (link.kind === "device") name = `${name} <span class="muted">reported it itself</span>`;
    return `<li class="k-${esc(link.kind)}">
      <ha-icon icon="${icon}"></ha-icon>
      <div><div class="lbl">${label}${link.at ? ` · ${fmtTime(link.at)}` : ""}</div><div class="nm">${name} ${extra}</div></div>
    </li>`;
  }

  _renderLive() {
    if (!this._feed?.length) {
      return `<div class="empty"><ha-icon icon="mdi:timer-sand"></ha-icon>Waiting for something to change…<br><small>Whodunit only sees changes made since Home Assistant started.</small></div>`;
    }
    return `<ul class="feed">${this._feed
      .slice(0, 50)
      .map((c) => {
        const guessed = c.links[0]?.kind === "probable";
        const a = (guessed ? c.links[1] || c.links[0] : c.links[0]) || { kind: "unknown", name: "?" };
        const who = (guessed ? "≈ " : "") + (a.kind === "device" ? "device" : a.kind === "probable" ? a.name : a.name);
        return `<li data-why="${esc(c.entity_id)}" title="${esc(c.sentence)}">
          <span class="t">${fmtTime(c.at)}</span>
          <span class="e"><b>${esc(c.name)}</b> <span class="chg">${esc(changeText(c))}</span></span>
          <span class="who k-${esc(a.kind)}"><ha-icon icon="${ICONS[a.kind] || ICONS.unknown}"></ha-icon>${esc(who)}${
            c.links.length > 1 ? `<span class="hops">+${c.links.length - 1}</span>` : ""
          }</span>
        </li>`;
      })
      .join("")}</ul>`;
  }

  _renderWhy() {
    const w = this._why;
    if (!this._whyEntity) return `<div class="empty"><ha-icon icon="mdi:magnify"></ha-icon>Pick an entity to find out why it is the way it is.</div>`;
    if (!w) return `<div class="empty">Looking…</div>`;
    if (w.error) return `<div class="empty err">${esc(w.error)}</div>`;
    const head = `<div class="sentence"><ha-icon icon="mdi:comment-quote-outline"></ha-icon><span>${esc(w.sentence)}</span></div>`;
    const changes = w.changes || [];
    if (!changes.length) {
      const note = w.current
        ? `<p class="muted">Nothing recorded since Whodunit started${w.tracked ? "" : " (this domain is not tracked)"}; this is the best guess from the entity's current context.</p>
           <ol class="chain">${w.current.links.map((l) => this._linkHtml(l, { showSource: true })).join("")}</ol>`
        : "";
      return head + note;
    }
    return (
      head +
      changes
        .map(
          (c, i) => `<details ${i === 0 ? "open" : ""}>
            <summary><span class="t">${fmtTime(c.at)}</span> <span class="chg">${esc(changeText(c))}</span>
              <span class="muted">· ${esc(c.links[0]?.name || "")}</span></summary>
            <ol class="chain">${c.links.map((l) => this._linkHtml(l, { showSource: true, changeAt: c.at })).join("")}</ol>
          </details>`
        )
        .join("")
    );
  }

  _renderFights() {
    const f = this._fights;
    if (!f) return `<div class="empty">Loading…</div>`;
    if (f.error) return `<div class="empty err">${esc(f.error)}</div>`;
    this._activeFights = f.active.length;
    this._updateBadge();
    const card = (x) => `<div class="fight ${x.active ? "active" : ""}">
        <div class="fh"><ha-icon icon="${x.kind === "loop" ? "mdi:sync-alert" : "mdi:boxing-glove"}"></ha-icon>
          <a href="#" data-why="${esc(x.entity_id)}"><b>${esc(x.name)}</b></a>
          <span class="muted">${x.flips} flips · ${x.active ? "since " + fmtTime(x.started) : "ended " + ago(x.last)}</span></div>
        <ul class="actors">${x.actors
          .map(
            (a) => `<li class="k-${esc(a.kind)}"><ha-icon icon="${ICONS[a.kind] || ICONS.unknown}"></ha-icon>
              ${a.id && a.kind !== "user" ? `<a href="#" data-info="${esc(a.id)}">${esc(a.name)}</a>` : esc(a.name)}
              <span class="bar"><i style="width:${Math.round((a.count / x.flips) * 100)}%"></i></span><span class="n">${a.count}×</span></li>`
          )
          .join("")}</ul>
      </div>`;
    const intro = `<p class="muted">A fight is ${f.flips}+ flips within ${f.window}s caused by two or more different actors. A loop is a chain that leads back to an automation already in it.</p>`;
    if (!f.active.length && !f.recent.length)
      return `<div class="empty ok"><ha-icon icon="mdi:handshake-outline"></ha-icon>No fights. Your automations are getting along.</div>${intro}`;
    return (
      (f.active.length ? `<h4>Happening now</h4>${f.active.map(card).join("")}` : "") +
      (f.recent.length ? `<h4>Recent</h4>${f.recent.map(card).join("")}` : "") +
      intro
    );
  }

  _renderBusy() {
    const b = this._busy;
    if (!b) return `<div class="empty">Loading…</div>`;
    if (b.error) return `<div class="empty err">${esc(b.error)}</div>`;
    if (!b.automations.length) return `<div class="empty">No automations have run in the last hour.</div>`;
    const max = b.automations[0].runs_last_hour;
    return `<table class="busy"><thead><tr><th>Automation</th><th>Runs / hr</th><th title="Most runs within one second">Burst</th></tr></thead><tbody>${b.automations
      .map(
        (a) => `<tr><td><a href="#" data-info="${esc(a.entity_id)}">${esc(a.name)}</a></td>
          <td><span class="bar"><i style="width:${Math.round((a.runs_last_hour / max) * 100)}%"></i></span>${a.runs_last_hour}</td>
          <td class="${a.peak_per_second >= 3 ? "hot" : ""}">${a.peak_per_second}/s</td></tr>`
      )
      .join("")}</tbody></table>
      <p class="muted">A burst of 3 or more runs in one second usually means several triggers fire for the same event. Consider <code>mode: single</code> or a short <code>for:</code>.</p>`;
  }
}

const STYLE = `
  :host { display:block; }
  ha-icon { display:inline-flex; flex:none; width:var(--mdc-icon-size,24px); height:var(--mdc-icon-size,24px); }
  ha-card { overflow:hidden; display:flex; flex-direction:column; height:100%; }
  a { color: var(--primary-color); text-decoration:none; cursor:pointer; }
  a:hover { text-decoration:underline; }
  .head { display:flex; align-items:center; justify-content:space-between; gap:8px; padding:12px 16px 8px; flex-wrap:wrap; }
  .title { display:flex; align-items:center; gap:8px; font-size:1.15em; font-weight:500; color:var(--primary-text-color); }
  .title ha-icon { color: var(--primary-color); }
  .tabs { display:flex; gap:2px; background: var(--secondary-background-color, rgba(127,127,127,.12)); border-radius:10px; padding:3px; }
  .tabs button { all:unset; cursor:pointer; display:flex; align-items:center; gap:4px; padding:4px 10px; border-radius:8px;
    color: var(--secondary-text-color); font-size:.9em; position:relative; }
  .tabs button ha-icon { --mdc-icon-size:16px; }
  .tabs button.on { background: var(--card-background-color, #fff); color: var(--primary-text-color); box-shadow: 0 1px 2px rgba(0,0,0,.15); }
  .tabs button:focus-visible { outline: 2px solid var(--primary-color); }
  .badge { font-style:normal; background: var(--error-color, #db4437); color:#fff; border-radius:9px; font-size:.7em; padding:0 5px; min-width:8px; text-align:center; }
  @media (max-width: 420px) { .tabs button span { display:none; } }
  .why-bar { padding: 0 16px 8px; }
  .why-bar[hidden] { display:none; }
  .why-bar input { width:100%; box-sizing:border-box; padding:8px 10px; border-radius:8px; border:1px solid var(--divider-color);
    background: var(--card-background-color); color: var(--primary-text-color); font: inherit; }
  .pane { padding: 0 16px 14px; overflow:auto; flex:1; max-height: var(--whodunit-max-height, 480px); color: var(--primary-text-color); }
  .muted { color: var(--secondary-text-color); font-size:.88em; }
  .chg { font-family: var(--code-font-family, monospace); font-size:.85em; background: var(--secondary-background-color, rgba(127,127,127,.12)); padding:1px 5px; border-radius:4px; white-space:nowrap; }
  .empty { text-align:center; padding:28px 8px; color: var(--secondary-text-color); }
  .empty ha-icon { display:block; margin:0 auto 6px; --mdc-icon-size:32px; opacity:.7; }
  .empty.err { color: var(--error-color); }
  .empty.ok ha-icon { color: var(--success-color, #43a047); opacity:1; }
  ul.feed { list-style:none; margin:0; padding:0; }
  ul.feed li { display:grid; grid-template-columns: auto minmax(0,1.4fr) minmax(0,1fr); gap:8px; align-items:center; padding:7px 4px; border-bottom:1px solid var(--divider-color); cursor:pointer; }
  ul.feed li:hover { background: var(--secondary-background-color, rgba(127,127,127,.08)); }
  ul.feed .t { color: var(--secondary-text-color); font-size:.8em; font-variant-numeric: tabular-nums; }
  ul.feed .e { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .who { display:block; text-align:right; font-size:.85em; color: var(--secondary-text-color); overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }
  .who ha-icon { --mdc-icon-size:16px; vertical-align:-3px; margin-right:3px; }
  .hops { margin-left:3px; font-size:.8em; opacity:.8; }
  .k-automation ha-icon, .k-script ha-icon { color: var(--primary-color); }
  .k-user ha-icon { color: var(--accent-color, #ff9800); }
  .k-trigger ha-icon { color: var(--warning-color, #ffa600); }
  .k-loop ha-icon { color: var(--error-color, #db4437); }
  .k-probable ha-icon { color: var(--secondary-text-color); }
  .k-probable .lbl { font-style: italic; }
  .sentence { display:flex; gap:8px; align-items:flex-start; background: var(--secondary-background-color, rgba(127,127,127,.1)); padding:10px 12px; border-radius:10px; margin:2px 0 10px; line-height:1.4; }
  .sentence ha-icon { color: var(--primary-color); flex:none; }
  details { border-bottom: 1px solid var(--divider-color); padding: 6px 0; }
  summary { cursor:pointer; list-style-position: outside; }
  summary .t { font-variant-numeric: tabular-nums; }
  ol.chain { list-style:none; margin:8px 0 4px; padding:0 0 0 4px; }
  ol.chain li { display:flex; gap:10px; position:relative; padding: 0 0 12px 0; }
  ol.chain li:not(:last-child)::before { content:""; position:absolute; left:11px; top:26px; bottom:2px; width:2px; background: var(--divider-color); }
  ol.chain li ha-icon { flex:none; width:24px; height:24px; }
  ol.chain .lbl { font-size:.75em; text-transform:uppercase; letter-spacing:.04em; color: var(--secondary-text-color); }
  ol.chain .nm { line-height:1.35; }
  .mini { font-size:.78em; border:1px solid var(--divider-color); border-radius:8px; padding:0 6px; margin-left:2px; }
  h4 { margin: 10px 0 6px; font-weight:500; }
  .fight { border:1px solid var(--divider-color); border-radius:10px; padding:8px 10px; margin-bottom:8px; }
  .fight.active { border-color: var(--error-color, #db4437); box-shadow: inset 3px 0 0 var(--error-color, #db4437); }
  .fh { display:flex; gap:8px; align-items:center; flex-wrap:wrap; }
  .fight.active .fh ha-icon { color: var(--error-color, #db4437); }
  ul.actors { list-style:none; padding:0; margin:6px 0 0; }
  ul.actors li { display:grid; grid-template-columns: 22px minmax(0,1fr) 30% 34px; gap:6px; align-items:center; padding:2px 0; font-size:.92em; }
  ul.actors ha-icon { --mdc-icon-size:18px; }
  .bar { display:inline-block; height:6px; background: var(--divider-color); border-radius:3px; overflow:hidden; vertical-align:middle; width:100%; }
  .bar i { display:block; height:100%; background: var(--primary-color); border-radius:3px; }
  .n { text-align:right; font-variant-numeric: tabular-nums; }
  table.busy { width:100%; border-collapse:collapse; font-size:.92em; }
  table.busy th { text-align:left; font-weight:500; color: var(--secondary-text-color); font-size:.85em; padding:4px; }
  table.busy td { padding:5px 4px; border-top:1px solid var(--divider-color); font-variant-numeric: tabular-nums; }
  table.busy td:nth-child(2) { width:34%; white-space:nowrap; }
  table.busy td:nth-child(2) .bar { width: calc(100% - 40px); margin-right:6px; }
  table.busy td.hot { color: var(--error-color, #db4437); font-weight:500; }
  code { font-size:.9em; }
`;

if (!customElements.get("whodunit-card")) {
  customElements.define("whodunit-card", WhodunitCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: "whodunit-card",
    name: "Whodunit",
    description: "Why did that change? Live cause chains, automation fights and busy automations.",
    preview: false,
    documentationURL: "https://github.com/s2n2/Whodunit",
  });
  console.info(`%c WHODUNIT %c ${WD_VERSION} `, "background:#5c6bc0;color:#fff;border-radius:3px 0 0 3px", "background:#333;color:#fff;border-radius:0 3px 3px 0");
}

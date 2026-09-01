/* Bay Area Smoke Trends -- chart layer.
 *
 * Plain ESM, no build step. Observable Plot from esm.sh.
 *
 * Design notes worth keeping in view while editing:
 *
 * - Every magnitude encoding on this page uses ONE sequential ramp (blue,
 *   light to dark) on ONE shared domain. That is what lets you compare the
 *   headline calendar against the ten-location strip, and one location against
 *   another, without doing arithmetic in your head.
 * - "No data" is painted as a diagonal hatch, not a tone. Any flat grey lands
 *   somewhere on a light-to-dark ramp and therefore reads as a value; a hatch
 *   is off the scale entirely. The whole point of this project is that a gap in
 *   the record must never read as clean air — or, in dark mode, as danger.
 * - Colour never carries meaning alone: the day card states the numbers, and
 *   every calendar has a table view underneath it.
 */

import * as Plot from "https://esm.sh/@observablehq/plot@0.6.17";

/* ------------------------------------------------------------- constants */

// The sequential ramp, light -> dark. Mirrors --seq-* in style.css.
const RAMP = [
  "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5",
  "#256abf", "#184f95", "#0d366b",
];

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

// Fire seasons that shaped the record. Annotated on the time series because
// without them the spikes are anonymous.
// Labels are kept to ~15 characters: they render rotated in the top margin,
// and anything longer is sheared off rather than wrapped.
const FIRE_EVENTS = [
  { date: "2008-06-21", label: "June Lightning" },
  { date: "2017-10-09", label: "Tubbs / Atlas" },
  { date: "2018-11-09", label: "Camp Fire" },
  { date: "2020-09-09", label: "Aug Complex/CZU" },
  { date: "2021-08-05", label: "Dixie" },
];

const METRICS = {
  smoke: {
    key: "p_smoke",
    hitKey: "years_hit_smoke",
    series: "smoke_pm",
    srcKey: "smoke_src",
    label: "Chance of a smoky day",
    unit: "µg/m³",
    seriesLabel: "Smoke PM2.5 (µg/m³)",
    note:
      "Wildfire smoke isolated from everything else in the air: PM2.5 above a " +
      "local, seasonal baseline on days a satellite saw a plume overhead.",
    thresholdText: (t) => `smoke PM2.5 at or above ${t} µg/m³`,
  },
  aqi: {
    key: "p_aqi",
    hitKey: "years_hit_aqi",
    series: "aqi",
    srcKey: "pm25_src",
    label: "Chance of an unhealthy day",
    unit: "AQI",
    seriesLabel: "AQI (from measured PM2.5)",
    note:
      "Total measured air quality, smoke or not — recomputed from raw " +
      "concentrations on the current (2024) AQI scale so the whole record is " +
      "on one ruler.",
    thresholdText: (t) => `AQI at or above ${t} (Unhealthy for Sensitive Groups)`,
  },
};

/* ----------------------------------------------------------------- state */

const state = {
  meta: null,
  clim: null,
  daily: new Map(),
  location: "napa",
  metric: "smoke",
  selectedSlot: null,
};

const $ = (sel) => document.querySelector(sel);

/* ------------------------------------------------------------- utilities */

async function getJSON(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
  return r.json();
}

/** "09-14" -> "14 September" */
function prettyDate(label) {
  const [m, d] = label.split("-").map(Number);
  const full = ["January", "February", "March", "April", "May", "June", "July",
                "August", "September", "October", "November", "December"];
  return `${d} ${full[m - 1]}`;
}

const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)}%`);

/** Read the CSS custom properties so the plots follow the active theme. */
function tokens() {
  const cs = getComputedStyle(document.documentElement);
  const get = (n) => cs.getPropertyValue(n).trim();
  return {
    surface: get("--surface-1"),
    text: get("--text-primary"),
    secondary: get("--text-secondary"),
    muted: get("--text-muted"),
    grid: get("--grid"),
    baseline: get("--baseline"),
    series: get("--series-1"),
    noData: get("--no-data"),
    noDataInk: get("--no-data-ink"),
    critical: get("--critical"),
  };
}

/**
 * One shared colour domain across the calendar and the small multiples, so a
 * dark cell means the same thing everywhere on the page.
 */
function sharedMax(metricKey) {
  let max = 0;
  for (const rows of Object.values(state.clim.by_location)) {
    for (const v of rows[metricKey]) if (v != null && v > max) max = v;
  }
  // Round up to a clean tick so the legend reads sensibly.
  return Math.max(0.05, Math.ceil(max * 20) / 20);
}

/**
 * Paint "no data" as a diagonal hatch rather than a flat tone.
 *
 * A flat grey has to sit *somewhere* on a light-to-dark ramp, and wherever you
 * put it, it reads as a value. In dark mode this is actively dangerous: a dark
 * neutral lands next to the ramp's dark end, so Point Reyes' missing AQI record
 * looked like the highest-risk row on the chart. Hatching is off the scale
 * entirely — it cannot be misread as a magnitude, and it survives greyscale,
 * print and colour-vision deficiency.
 *
 * Each plot gets its own `<defs>` with a unique id, because `url(#id)` resolves
 * document-wide and same-named patterns in sibling SVGs would collide.
 */
function addHatch(node, id, { bg, stroke }) {
  const svg = node.tagName === "svg" ? node : node.querySelector("svg");
  if (!svg) return;
  const NS = "http://www.w3.org/2000/svg";
  const defs = document.createElementNS(NS, "defs");
  defs.innerHTML =
    `<pattern id="${id}" width="5" height="5" patternUnits="userSpaceOnUse" ` +
    `patternTransform="rotate(45)">` +
    `<rect width="5" height="5" fill="${bg}"/>` +
    `<line x1="0" y1="0" x2="0" y2="5" stroke="${stroke}" stroke-width="1.6"/>` +
    `</pattern>`;
  svg.prepend(defs);
}

/** Build the per-slot records the calendar marks consume. */
function calendarRows(slug, metricKey) {
  const rows = state.clim.by_location[slug];
  const labels = state.meta.slot_labels;
  const out = [];
  for (let s = 0; s < labels.length; s++) {
    const [m, d] = labels[s].split("-").map(Number);
    out.push({
      slot: s,
      month: m,
      day: d,
      monthName: MONTHS[m - 1],
      label: labels[s],
      p: rows[metricKey][s],
      nYears: rows.n_years[s],
      hit: rows[METRICS[state.metric].hitKey][s],
      worstYear: rows.worst_year[s],
      worstPM: rows.worst_pm25[s],
      pm25Median: rows.pm25_median[s],
    });
  }
  return out;
}

/* --------------------------------------------------------- the calendar */

function renderCalendar() {
  const el = $("#calendar");
  const t = tokens();
  const M = METRICS[state.metric];
  const rows = calendarRows(state.location, M.key);
  const domainMax = sharedMax(M.key);

  const withData = rows.filter((r) => r.p != null);
  const noData = rows.filter((r) => r.p == null);

  const plot = Plot.plot({
    width: 940,
    height: 340,
    marginLeft: 46,
    marginTop: 28,
    marginBottom: 34,
    marginRight: 14,
    style: { background: "transparent", color: t.secondary, fontSize: "12px" },
    x: {
      domain: Array.from({ length: 31 }, (_, i) => i + 1),
      label: "Day of month →",
      labelAnchor: "left",
      tickSize: 0,
      ticks: [1, 5, 10, 15, 20, 25, 31],
    },
    y: {
      domain: MONTHS,
      label: null,
      tickSize: 0,
    },
    color: {
      type: "linear",
      domain: [0, domainMax],
      range: RAMP,
      interpolate: "rgb",
      clamp: true,
      legend: true,
      label: `${M.label} (${pct(0)}–${pct(domainMax)})`,
      tickFormat: (v) => `${Math.round(v * 100)}%`,
      width: 260,
      height: 44,
      marginLeft: 16,   // otherwise the "0%" tick is clipped at the left edge
    },
    marks: [
      // Days with no usable record: a flat neutral, never the pale end of the
      // ramp. A gap must not look like a good day.
      Plot.cell(noData, {
        x: "day",
        y: "monthName",
        fill: "url(#hatch-cal)",
        inset: 1,          // 2px total gap between fills
        rx: 2,
      }),
      Plot.cell(withData, {
        x: "day",
        y: "monthName",
        fill: "p",
        inset: 1,
        rx: 2,
        stroke: t.surface,
        strokeWidth: 0.5,
      }),
      // Highlight ring on the selected day.
      Plot.cell(
        rows.filter((r) => r.slot === state.selectedSlot),
        {
          x: "day",
          y: "monthName",
          fill: "none",
          stroke: t.text,
          strokeWidth: 2,
          inset: 0,
          rx: 3,
        }
      ),
      Plot.tip(
        rows,
        Plot.pointer({
          x: "day",
          y: "monthName",
          title: (d) =>
            d.p == null
              ? `${prettyDate(d.label)}\nNo usable record`
              : [
                  prettyDate(d.label),
                  `${pct(d.p)} chance`,
                  `${d.hit} of ${d.nYears} years`,
                  d.worstYear ? `worst ${d.worstYear}: ${d.worstPM} µg/m³` : "",
                ].filter(Boolean).join("\n"),
          fontSize: 12,
        })
      ),
    ],
  });

  addHatch(plot, "hatch-cal", { bg: t.surface, stroke: t.noDataInk });
  el.replaceChildren(plot);

  // Clicking a cell pins the detail card.
  plot.addEventListener("click", () => {
    if (plot.value) selectSlot(plot.value.slot);
  });
  plot.addEventListener("input", () => {
    if (plot.value && state.selectedSlot == null) renderDayCard(plot.value);
  });

  const loc = state.meta.locations.find((l) => l.slug === state.location);
  $("#cal-lede").textContent =
    `How often ${loc.name} has ${M.thresholdText(
      state.metric === "smoke"
        ? state.meta.thresholds.smoke_pm
        : state.meta.thresholds.aqi
    )}, on each calendar date. Each cell pools a ±${state.meta.window_days}-day ` +
    `window across every year with adequate data, so one freak afternoon does ` +
    `not define a date. Click a day for the detail.`;

  $("#cal-caption").textContent =
    `Darker means smokier. Hatched means no usable record at this location — not a clean day. ` +
    `Scale is shared with the ten-location strip below (0–${pct(domainMax)}).`;

  renderCalTable(rows);
}

function selectSlot(slot) {
  state.selectedSlot = slot;
  const rows = calendarRows(state.location, METRICS[state.metric].key);
  renderDayCard(rows[slot]);
  renderCalendar();
}

function renderDayCard(d) {
  const el = $("#daycard");
  if (!d) {
    el.className = "daycard is-empty";
    el.textContent = "Hover or click a day for the detail.";
    return;
  }
  const loc = state.meta.locations.find((l) => l.slug === state.location);
  const M = METRICS[state.metric];

  if (d.p == null) {
    el.className = "daycard is-empty";
    el.innerHTML =
      `<span class="dc-date">${prettyDate(d.label)} in ${loc.name}</span>` +
      `No year has enough data at this location to estimate this date.`;
    return;
  }

  el.className = "daycard";
  const worst = d.worstYear
    ? ` Worst on record nearby: <strong>${d.worstYear}</strong> at ${d.worstPM} µg/m³.`
    : "";
  el.innerHTML =
    `<span class="dc-date">${prettyDate(d.label)} in ${loc.name}</span>` +
    `<span class="dc-big">${pct(d.p)}</span> ` +
    `<span class="dc-muted">${M.label.toLowerCase()} — ` +
    `${d.hit} of ${d.nYears} years with adequate data had one in the ` +
    `±${state.meta.window_days}-day window.${worst}</span>`;
}

/** The table view, so identity and magnitude are never colour-only. */
function renderCalTable(rows) {
  const byMonth = MONTHS.map((name, i) => {
    const m = rows.filter((r) => r.month === i + 1 && r.p != null);
    if (!m.length) return { name, mean: null, peak: null, peakDay: null };
    const mean = m.reduce((a, r) => a + r.p, 0) / m.length;
    const peak = m.reduce((a, r) => (r.p > a.p ? r : a), m[0]);
    return { name, mean, peak: peak.p, peakDay: peak.day };
  });

  const head = `<thead><tr><th>Month</th><th>Average chance</th>
    <th>Worst date</th><th>Chance that date</th></tr></thead>`;
  const body = byMonth
    .map(
      (r) =>
        `<tr><td>${r.name}</td><td>${pct(r.mean)}</td>` +
        `<td>${r.peakDay ? `${r.name} ${r.peakDay}` : "—"}</td>` +
        `<td>${pct(r.peak)}</td></tr>`
    )
    .join("");
  $("#cal-table").innerHTML = `<table>${head}<tbody>${body}</tbody></table>`;
}

/* -------------------------------------------------- ten-location strip */

function renderSmallMultiples() {
  const el = $("#smallmult");
  const t = tokens();
  const M = METRICS[state.metric];
  const domainMax = sharedMax(M.key);
  const labels = state.meta.slot_labels;

  // Ordered coast -> inland so the gradient is legible as a shape, not a list.
  const order = [
    "point-reyes", "half-moon-bay", "santa-cruz", "san-francisco",
    "redwood-city", "oakland", "san-jose", "sebastopol", "livermore", "napa",
  ].filter((s) => state.clim.by_location[s]);

  const rows = [];
  for (const slug of order) {
    const name = state.meta.locations.find((l) => l.slug === slug).name;
    const series = state.clim.by_location[slug][M.key];
    for (let s = 0; s < labels.length; s++) {
      rows.push({ slug, name, slot: s, label: labels[s], p: series[s] });
    }
  }

  const monthStarts = [];
  for (let s = 0; s < labels.length; s++) {
    if (labels[s].endsWith("-01")) {
      monthStarts.push({ slot: s, name: MONTHS[Number(labels[s].split("-")[0]) - 1] });
    }
  }

  const plot = Plot.plot({
    width: 940,
    height: 330,
    marginLeft: 142,   // "Oakland / Berkeley" is the longest label
    marginTop: 24,
    marginBottom: 34,
    marginRight: 14,
    style: { background: "transparent", color: t.secondary, fontSize: "12px" },
    x: {
      label: null,
      ticks: monthStarts.map((m) => m.slot),
      tickFormat: (s) => monthStarts.find((m) => m.slot === s)?.name ?? "",
      tickSize: 0,
    },
    y: { domain: order.map((s) => state.meta.locations.find((l) => l.slug === s).name),
         label: null, tickSize: 0 },
    color: {
      type: "linear",
      domain: [0, domainMax],
      range: RAMP,
      interpolate: "rgb",
      clamp: true,
      legend: true,
      label: `${M.label}`,
      tickFormat: (v) => `${Math.round(v * 100)}%`,
      width: 260,
      height: 44,
      marginLeft: 16,   // otherwise the "0%" tick is clipped at the left edge
    },
    marks: [
      // A negative inset overlaps neighbouring cells by half a pixel. At 366
      // cells per row they otherwise antialias into a moire of pale vertical
      // seams that reads as structure in the data. (`shapeRendering:
      // crispEdges` is the obvious fix and makes it worse: snapping each ~4px
      // cell to whole pixels opens real 1px gaps.)
      Plot.cell(rows.filter((r) => r.p == null), {
        x: "slot", y: "name", fill: "url(#hatch-sm)", inset: -0.5,
      }),
      Plot.cell(rows.filter((r) => r.p != null), {
        x: "slot", y: "name", fill: "p", inset: -0.5,
      }),
      Plot.tip(
        rows,
        Plot.pointer({
          x: "slot",
          y: "name",
          title: (d) =>
            `${d.name}\n${prettyDate(d.label)}\n` +
            (d.p == null ? "No usable record" : `${pct(d.p)} chance`),
          fontSize: 12,
        })
      ),
    ],
  });

  addHatch(plot, "hatch-sm", { bg: t.surface, stroke: t.noDataInk });
  el.replaceChildren(plot);
  $("#sm-caption").textContent =
    `Ordered coast to inland. The fire-season band is the same shape everywhere ` +
    `but not the same depth — that difference is the reason this site has ten ` +
    `locations instead of one. Hatched rows have no usable record for this ` +
    `metric.`;
}

/* ---------------------------------------------------------- time series */

async function loadDaily(slug) {
  if (!state.daily.has(slug)) {
    state.daily.set(slug, await getJSON(`data/daily/${slug}.json`));
  }
  return state.daily.get(slug);
}

async function renderTimeSeries() {
  const el = $("#timeseries");
  el.innerHTML = '<p class="loading">Loading the daily record…</p>';

  const t = tokens();
  const M = METRICS[state.metric];
  const doc = await loadDaily(state.location);
  const loc = state.meta.locations.find((l) => l.slug === state.location);

  const start = new Date(`${doc.start}T00:00:00Z`);
  const values = doc[M.series];
  const src = doc[M.srcKey];

  const data = [];
  for (let i = 0; i < doc.n; i++) {
    if (values[i] == null) continue;
    const d = new Date(start.getTime() + i * 86400000);
    data.push({ date: d, value: values[i], src: src[i] });
  }

  if (!data.length) {
    el.innerHTML =
      `<p class="error">No ${M.seriesLabel.toLowerCase()} exists for ` +
      `${loc.name}. ${loc.coverage_note ?? ""}</p>`;
    $("#ts-caption").textContent = "";
    return;
  }

  const yMax = Math.max(...data.map((d) => d.value));
  const events = FIRE_EVENTS.map((e) => ({ ...e, d: new Date(`${e.date}T00:00:00Z`) }))
    .filter((e) => e.d >= data[0].date && e.d <= data[data.length - 1].date);

  const plot = Plot.plot({
    width: 940,
    height: 360,
    marginLeft: 52,
    // Room for the rotated fire-season labels above the frame. At 26px they
    // were sheared off mid-word.
    marginTop: 108,
    marginBottom: 30,
    marginRight: 16,
    style: { background: "transparent", color: t.secondary, fontSize: "12px" },
    x: { label: null, grid: false },
    y: {
      label: M.seriesLabel,
      labelAnchor: "top",
      grid: true,
      nice: true,
      zero: true,
    },
    marks: [
      Plot.ruleY([0], { stroke: t.baseline, strokeWidth: 1 }),

      // Fire-season markers, behind the data.
      Plot.ruleX(events, {
        x: "d",
        stroke: t.muted,
        strokeWidth: 1,
        strokeDasharray: "2,3",
      }),
      // Anchored to the frame, not to yMax, so the labels sit in the top
      // margin regardless of how tall this location's worst day happens to be.
      Plot.text(events, {
        x: "d",
        frameAnchor: "top",
        text: "label",
        dy: -6,
        fontSize: 10,
        fill: t.muted,
        textAnchor: "start",
        rotate: -90,
      }),

      Plot.areaY(data, {
        x: "date",
        y: "value",
        fill: t.series,
        fillOpacity: 0.16,
        curve: "step",
      }),
      Plot.lineY(data, {
        x: "date",
        y: "value",
        stroke: t.series,
        strokeWidth: 1,
        curve: "step",
      }),

      Plot.tip(
        data,
        Plot.pointerX({
          x: "date",
          y: "value",
          title: (d) =>
            `${d.date.toISOString().slice(0, 10)}\n` +
            `${d.value} ${M.unit}\n` +
            `${state.meta.source_legend[state.metric === "smoke" ? "smoke" : "pm25"][String(d.src)]}`,
          fontSize: 12,
        })
      ),
    ],
  });

  el.replaceChildren(plot);

  const last = data[data.length - 1].date.toISOString().slice(0, 10);
  $("#ts-lede").textContent =
    `Every day on record for ${loc.name}. The big years are marked; between ` +
    `them the baseline is what ordinary air looks like there.`;
  $("#ts-caption").textContent =
    `${data.length.toLocaleString()} days with data, ` +
    `${data[0].date.toISOString().slice(0, 10)} to ${last}. ` +
    `Gaps are days with no measurement, not clean days.`;

  renderCoverage(loc, doc);
}

/** Provenance chips: what standing does this location's record actually have? */
function renderCoverage(loc, doc) {
  const el = $("#coverage");
  const legend = state.meta.source_legend;
  const M = METRICS[state.metric];
  const src = doc[M.srcKey];
  const key = state.metric === "smoke" ? "smoke" : "pm25";

  // Last date for each provenance code, so we can say where each layer ends.
  const start = new Date(`${doc.start}T00:00:00Z`);
  const lastByCode = new Map();
  for (let i = 0; i < doc.n; i++) {
    if (src[i] === 0) continue;
    lastByCode.set(src[i], i);
  }

  const chips = [];
  for (const [code, idx] of [...lastByCode.entries()].sort((a, b) => a[0] - b[0])) {
    const d = new Date(start.getTime() + idx * 86400000).toISOString().slice(0, 10);
    chips.push(
      `<span class="chip"><strong>${legend[key][String(code)]}</strong> — through ${d}</span>`
    );
  }
  if (loc.coverage_note) {
    chips.push(`<span class="chip warn">${loc.coverage_note}</span>`);
  }
  el.innerHTML = chips.join("");
}

/* --------------------------------------------------------------- static */

function renderProvenanceTable() {
  const rows = state.meta.locations.map((l) => {
    return `<tr>
      <td>${l.name}</td>
      <td>${l.monitors.length ? l.monitors.map((m) => m.site_id).join(", ") : "—"}</td>
      <td>${l.pm25_first ?? "—"} → ${l.pm25_last ?? "—"}</td>
      <td>${l.smoke_first ?? "—"} → ${l.smoke_last ?? "—"}</td>
      <td>${l.grid_id_10km ?? "—"}</td>
    </tr>`;
  }).join("");

  $("#provenance-table").innerHTML = `
    <div class="table-scroll"><table>
      <thead><tr>
        <th>Location</th><th>EPA monitor(s)</th>
        <th>Measured PM2.5</th><th>Smoke PM2.5</th><th>10 km cell</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table></div>`;
}

function renderCrossval() {
  const xv = state.meta.crossval || {};
  const el = $("#crossval-note");
  if (xv.pearson_r_all == null) {
    el.textContent =
      "Cross-validation against the published product has not been run for " +
      "this build.";
    return;
  }
  el.innerHTML =
    `<strong>Does our reimplementation agree with the published one?</strong> ` +
    `Over the ${xv.n_overlap_days.toLocaleString()} location-days where both ` +
    `exist, our estimates correlate with ECHOLab's at ` +
    `<strong>r = ${xv.pearson_r_all}</strong> ` +
    `(mean ${xv.mean_ours} vs ${xv.mean_published} µg/m³). ` +
    `We publish theirs wherever they published it and ours only afterwards, ` +
    `but this is the check that our "afterwards" is the same kind of number.`;
}

/* ------------------------------------------------------------------ wire */

function setMetric(metric) {
  state.metric = metric;
  for (const b of document.querySelectorAll(".segmented button")) {
    const on = b.dataset.metric === metric;
    b.classList.toggle("is-on", on);
    b.setAttribute("aria-checked", String(on));
  }
  $("#metric-note").textContent = METRICS[metric].note;
  redraw();
}

function redraw() {
  renderCalendar();
  renderSmallMultiples();
  renderTimeSeries();
  renderDayCard(
    state.selectedSlot == null
      ? null
      : calendarRows(state.location, METRICS[state.metric].key)[state.selectedSlot]
  );
}

async function main() {
  try {
    const [meta, clim] = await Promise.all([
      getJSON("data/locations.json"),
      getJSON("data/climatology.json"),
    ]);
    state.meta = meta;
    state.clim = clim;

    const sel = $("#location");
    sel.innerHTML = meta.locations
      .map((l) => `<option value="${l.slug}">${l.name}</option>`)
      .join("");
    if (!meta.locations.some((l) => l.slug === state.location)) {
      state.location = meta.locations[0].slug;
    }
    sel.value = state.location;
    sel.addEventListener("change", () => {
      state.location = sel.value;
      state.selectedSlot = null;
      redraw();
    });

    for (const b of document.querySelectorAll(".segmented button")) {
      b.addEventListener("click", () => setMetric(b.dataset.metric));
    }

    $("#theme-toggle").addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme");
      const next = cur === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      redraw();
    });

    $("#generated").textContent =
      `Data covers ${meta.start} to ${meta.end}. Built ${meta.generated_utc}.`;
    $("#trailing-year").textContent = new Date(meta.end).getUTCFullYear();

    renderProvenanceTable();
    renderCrossval();
    setMetric(state.metric);
    renderDayCard(null);
  } catch (err) {
    document.querySelector("main").insertAdjacentHTML(
      "afterbegin",
      `<p class="error">Could not load the data: ${err.message}. ` +
      `If you are running this locally, serve the folder over HTTP ` +
      `(<code>python -m http.server</code>) — <code>file://</code> blocks fetch.</p>`
    );
    console.error(err);
  }
}

main();

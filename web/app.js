'use strict';

const METRICS = [
  { key: 'temperature', title: 'Température', unit: '°C', decimals: 1, minSpan: 4 },
  { key: 'humidity', title: 'Humidité', unit: '%', decimals: 0, minSpan: 10 },
  { key: 'pressure', title: 'Pression', unit: 'hPa', decimals: 1, minSpan: 6 },
  { key: 'rain_60min', title: 'Pluie sur 1 h', short: 'Pluie', unit: 'mm', decimals: 1, minSpan: 1, zero: true },
  { key: 'wind_strength', title: 'Vent moyen', short: 'Vent', estimatedTitle: 'Vent moyen estimé', unit: 'km/h', decimals: 0, minSpan: 10, zero: true },
  { key: 'gust_strength', title: 'Rafales', estimatedTitle: 'Rafales estimées', unit: 'km/h', decimals: 0, minSpan: 10, zero: true },
];
// WMO weather codes used by Open-Meteo: code -> [picture, label].
const WEATHER = {
  0: ['clear', 'Ciel dégagé'],
  1: ['clear', 'Plutôt dégagé'],
  2: ['partly', 'Partiellement nuageux'],
  3: ['cloud', 'Couvert'],
  45: ['fog', 'Brouillard'],
  48: ['fog', 'Brouillard givrant'],
  51: ['drizzle', 'Bruine légère'],
  53: ['drizzle', 'Bruine'],
  55: ['drizzle', 'Forte bruine'],
  56: ['drizzle', 'Bruine verglaçante'],
  57: ['drizzle', 'Bruine verglaçante'],
  61: ['rain', 'Pluie faible'],
  63: ['rain', 'Pluie'],
  65: ['rain', 'Forte pluie'],
  66: ['rain', 'Pluie verglaçante'],
  67: ['rain', 'Pluie verglaçante'],
  71: ['snow', 'Neige faible'],
  73: ['snow', 'Neige'],
  75: ['snow', 'Forte neige'],
  77: ['snow', 'Grains de neige'],
  80: ['rain', 'Averses faibles'],
  81: ['rain', 'Averses'],
  82: ['rain', 'Fortes averses'],
  85: ['snow', 'Averses de neige'],
  86: ['snow', 'Fortes averses de neige'],
  95: ['storm', 'Orage'],
  96: ['storm', 'Orage avec grêle'],
  99: ['storm', 'Orage avec grêle'],
};
const BAROMETER_ARROWS = {
  fall_fast: '⇊', fall: '↓', fall_slow: '↘', stable: '→', rise_slow: '↗', rise: '↑', rise_fast: '⇈',
};
const SHORT_NAME_MAX = 14; // longer station names keep their first word in tabs and legends
const HOURS_ON_CARD = 24;
const HOURS_IN_PANEL = 24;
const ALERT_NAMES = { yellow: 'jaune', orange: 'orange', red: 'rouge' };
const MAX_RADIUS = 10;
const DEFAULT_RADIUS = 5;
const CLOUD_PATH = 'M13 33a7.5 7.5 0 0 1-.6-15A10.5 10.5 0 0 1 32.6 16 8.5 8.5 0 0 1 34 33z';
const PERIODS = [[24, '24 h'], [72, '3 j'], [168, '7 j'], [360, '15 j'], [720, '30 j'], [8760, '1 an'], [0, 'Tout']];
const FORECAST_DAYS = { 72: 3, 168: 7 }; // the longer periods show every day there is
const MAX_FORECAST_DAYS = 15;
const RELIABLE_DAYS = 7;
const PRESSURE_OFFSET = 5; // hPa from the neighbours: more than the weather explains
const FORECAST_CHOICES = [[0, '24 h'], [3, '3 j'], [7, '7 j'], [15, '15 j']];
const COMPASS = ['N', 'NE', 'E', 'SE', 'S', 'SO', 'O', 'NO'];
const HOUR = 3600;
const DAY = 86400;
const HEIGHT = 230;
const HEIGHT_LARGE = 460;
const MARGIN = { top: 12, right: 64, bottom: 26, left: 44 };
const SVG_NS = 'http://www.w3.org/2000/svg';
const MAX_TABLE_ROWS = 400;
const MAX_END_LABELS = 4;
const HISTORY_GAP = 3600; // recovered history has one point every 30 minutes
const REFRESH_MS = 15000;
const MIN_ZOOM = HOUR; // shortest period a zoom can show, in seconds
const MIN_DRAG = 10; // pixels: a shorter drag is a click, not a selection
const PAN_SHARE = 0.5; // arrows move the view by half of its length
const DAILY_MIN_SPAN = 7 * DAY; // same limit as the server: shorter periods stay detailed
const RAIN_BARS_FROM = 7 * DAY; // below this, rain is shown as a curve
const RAIN_MONTHS_FROM = 92 * DAY; // above this, rain bars are monthly
const MAX_COMPARE_SPAN = 366 * DAY;
const RAIN_MIN_SPAN = 5; // mm: keeps a dry period from looking dramatic
const BAR_GAP = 2;
const GHOST_SLOTS = [2, 4, 0, 6, 1, 7, 3, 5]; // last year's colour for each station's slot
const TOUCH_SCREEN = '(pointer: coarse)';
const MOUSE_HINT = 'Glissez sur une courbe pour zoomer, cliquez pour fixer un moment, double-cliquez pour revenir en arrière.';
const TOUCH_HINT = 'Glissez un doigt sur une courbe pour lire les valeurs, touchez-la pour fixer un moment, écartez deux doigts pour zoomer et pincez pour revenir.';
const PINCH_STEP = 1.15; // fingers moved apart or together by less than this do nothing
const PINCH_MAX = 8; // one pinch zooms in eight times at most
const TAP_MEMORY = 1000; // ms: a double click this soon after a touch comes from a double tap
let lastTouch = 0;

const view = {
  hours: readPeriod(),
  range: null, // {from, to} while zoomed or using dates, null for a preset period
  zoomStack: [],
  stationId: null, // the station shown alone, null for all of them
  hiddenMetrics: readSet('meteo-hidden-metrics'),
  expanded: new Set(), // every chart starts reduced
  dailyOn: readFlag('meteo-daily', true),
  compareOn: readFlag('meteo-compare', false),
  compare: null, // the same period one year earlier
  pinned: null, // timestamp fixed by a click in a chart
  datesDirty: false, // the date fields hold a choice that is not validated yet
  forecastDays: null, // chosen in the forecast panel; null follows the period
  hoursOn: readFlag('meteo-hours', false),
  forecastOn: readFlag('meteo-forecast', true),
  forecastOpen: readFlag('meteo-forecast-open', false),
  forecastFor: undefined, // the tab the forecast panel was last opened or closed for
  alertsOn: readFlag('meteo-alerts', false),
  barometerOn: readFlag('meteo-barometer', true),
  statsOn: readFlag('meteo-stats', false),
  statsOpen: readFlag('meteo-stats-open', false),
  statsRequest: 0,
  nearbyOn: readFlag('meteo-nearby', false),
  skyOn: readFlag('meteo-sky', true),
  fullscreenId: null, // the station shown on its own over the whole screen
  radius: readRadius(),
  request: 0,
  data: null,
  charts: [],
  renderedKey: '',
};

function readRadius() {
  try {
    const saved = Number(localStorage.getItem('meteo-radius'));
    return saved >= 1 && saved <= MAX_RADIUS ? saved : DEFAULT_RADIUS;
  } catch {
    return DEFAULT_RADIUS;
  }
}

function saveRadius(radius) {
  try {
    localStorage.setItem('meteo-radius', String(radius));
  } catch {
    /* the choice just won't be remembered */
  }
  syncSettings();
}

function readPeriod() {
  try {
    const saved = Number(localStorage.getItem('meteo-period'));
    return PERIODS.some(([hours]) => hours === saved) && localStorage.getItem('meteo-period') !== null ? saved : 24;
  } catch {
    return 24;
  }
}

function savePeriod(hours) {
  try {
    localStorage.setItem('meteo-period', String(hours));
  } catch {
    /* the choice just won't be remembered */
  }
  syncSettings();
}

function readSet(key) {
  try {
    return new Set(JSON.parse(localStorage.getItem(key)) || []);
  } catch {
    return new Set();
  }
}

function saveSet(key, set) {
  try {
    localStorage.setItem(key, JSON.stringify([...set]));
  } catch {
    /* the choice just won't be remembered */
  }
  syncSettings();
}

function readFlag(key, fallback) {
  try {
    const saved = localStorage.getItem(key);
    return saved === null ? fallback : saved === '1';
  } catch {
    return fallback;
  }
}

function saveFlag(key, value) {
  try {
    localStorage.setItem(key, value ? '1' : '0');
  } catch {
    /* the choice just won't be remembered */
  }
  syncSettings();
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function svg(tag, attributes, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attributes || {})) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function number(value, decimals) {
  return value.toLocaleString('fr-FR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function withUnit(value, metric) {
  return value === null || value === undefined ? '–' : `${number(value, metric.decimals)} ${metric.unit}`;
}

function dateTime(ts) {
  return new Date(ts * 1000).toLocaleString('fr-FR', {
    weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  });
}

function dayLabel(ts) {
  return new Date(ts * 1000).toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' });
}

function monthLabel(ts) {
  return new Date(ts * 1000).toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' });
}

function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

function ago(seconds) {
  const minutes = Math.round(seconds / 60);
  if (minutes < 1) return 'à l’instant';
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `il y a ${hours} h` : `il y a ${Math.round(hours / 24)} j`;
}

function compass(angle) {
  return COMPASS[Math.round(angle / 45) % 8];
}

/* ---------- Sun and moon ---------- */

const SYNODIC_MONTH = 29.530588853; // days from one new moon to the next
const KNOWN_NEW_MOON = Date.UTC(2000, 0, 6, 18, 14) / 1000;
// Each phase covers an eighth of the cycle, centred on its exact moment.
const MOON_PHASES = [
  'Nouvelle lune', 'Premier croissant', 'Premier quartier', 'Gibbeuse croissante',
  'Pleine lune', 'Gibbeuse décroissante', 'Dernier quartier', 'Dernier croissant',
];

// Mean motion only: right to within about half a day, enough for a phase and a date.
function moonInfo(ts) {
  const days = (ts - KNOWN_NEW_MOON) / DAY;
  const age = ((days % SYNODIC_MONTH) + SYNODIC_MONTH) % SYNODIC_MONTH;
  const angle = (2 * Math.PI * age) / SYNODIC_MONTH;
  const half = SYNODIC_MONTH / 2;
  const toFull = age < half ? half - age : SYNODIC_MONTH - age + half;
  const toNew = SYNODIC_MONTH - age;
  return {
    age,
    lit: (1 - Math.cos(angle)) / 2,
    name: MOON_PHASES[Math.round((age / SYNODIC_MONTH) * 8) % 8],
    isWaxing: age < half,
    next: toFull < toNew ? ['Pleine lune', toFull] : ['Nouvelle lune', toNew],
  };
}

// The lit part as seen from France: growing on the right, shrinking on the left.
function moonIcon(info) {
  const r = 10;
  const top = '12,2';
  const bottom = '12,22';
  const shadow = Math.cos((2 * Math.PI * info.age) / SYNODIC_MONTH); // 1 new, -1 full
  const rx = (r * Math.abs(shadow)).toFixed(2);
  const isCrescent = shadow > 0;
  const d = info.isWaxing
    ? `M${top}A${r} ${r} 0 0 1 ${bottom}A${rx} ${r} 0 0 ${isCrescent ? 0 : 1} ${top}Z`
    : `M${top}A${r} ${r} 0 0 0 ${bottom}A${rx} ${r} 0 0 ${isCrescent ? 1 : 0} ${top}Z`;
  const icon = svg('svg', { class: 'moon', viewBox: '0 0 24 24', 'aria-hidden': 'true' });
  icon.append(svg('circle', { class: 'moon-dark', cx: 12, cy: 12, r }), svg('path', { class: 'moon-lit', d }));
  return icon;
}

function hourMinute(text) {
  const [hours, minutes] = text.slice(11, 16).split(':');
  return `${Number(hours)} h ${minutes}`;
}

function duration(seconds) {
  const minutes = Math.round(seconds / 60);
  return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, '0')}`;
}

function todayIso() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
}

// Today's sunrise and sunset at the first station shown that has a forecast.
function sunToday(data) {
  const station = data && data.stations.find((item) => item.forecast && item.forecast.days.length);
  if (!station) return null;
  const days = station.forecast.days;
  const index = Math.max(0, days.findIndex((day) => day.date === todayIso()));
  const today = days[index];
  if (!today.sunrise || !today.sunset) return null;
  const tomorrow = days[index + 1];
  const change = tomorrow && tomorrow.daylight !== undefined ? Math.round((tomorrow.daylight - today.daylight) / 60) : 0;
  return { ...today, change };
}

function sunLine(sun) {
  const line = el('span', 'sun');
  line.append(el('span', 'sun-icon', '☀'), ` ${hourMinute(sun.sunrise)} → ${hourMinute(sun.sunset)}`);
  if (sun.daylight !== undefined) {
    const change = sun.change ? `, ${Math.abs(sun.change)} min de ${sun.change > 0 ? 'plus' : 'moins'} demain` : '';
    line.title = `${duration(sun.daylight)} de jour${change}`;
    line.append(el('span', 'sky-detail', ` · ${duration(sun.daylight)} de jour${change}`));
  }
  return line;
}

function moonLine(ts) {
  const info = moonInfo(ts);
  const line = el('span', 'moon-line');
  const [event, days] = info.next;
  const when = new Date((ts + days * DAY) * 1000).toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
  line.title = `${info.name}, éclairée à ${Math.round(info.lit * 100)} %. ${event} vers le ${when}.`;
  line.append(moonIcon(info), ` ${info.name}`, el('span', 'sky-detail', ` · ${event.toLowerCase()} vers le ${when}`));
  return line;
}

/* ---------- Header icon that follows the time of day ---------- */

const DAY_PARTS = {
  morning: { label: 'matin', top: '#7db7f4', bottom: '#ffd09c', sun: [14, 19], sunColor: '#ffd35a', cloud: '#ffffff' },
  afternoon: { label: 'après-midi', top: '#2f80e8', bottom: '#74b8f7', sun: [31, 16], sunColor: '#ffc93c', cloud: '#ffffff' },
  evening: { label: 'soirée', top: '#4a3d92', bottom: '#f2885a', sun: [35, 22], sunColor: '#ffa743', cloud: '#f8e3ec' },
  night: { label: 'nuit', top: '#0a1230', bottom: '#2a3672', moon: [30, 15], cloud: '#7d89b3' },
};
const STARS = [[11, 10, 1.2], [20, 6, 0.9], [41, 27, 1], [8, 21, 0.8]];
// Hour at which each part of the day starts, on this device's clock.
const DAY_PART_HOURS = [[22, 'night'], [18, 'evening'], [12, 'afternoon'], [6, 'morning']];
let brandPart = null;

function dayPart(date) {
  const found = DAY_PART_HOURS.find(([hour]) => date.getHours() >= hour);
  return found ? found[1] : 'night'; // before 6 h
}

function brandIcon(part) {
  const sky = DAY_PARTS[part];
  const id = `brand-${part}`;
  const icon = svg('svg', { class: 'brand-icon', viewBox: '0 0 48 48', width: 48, height: 48, 'aria-hidden': 'true' });
  const defs = svg('defs');
  const gradient = svg('linearGradient', { id: `${id}-sky`, x1: 0, y1: 0, x2: 0, y2: 1 });
  gradient.append(svg('stop', { offset: 0, 'stop-color': sky.top }), svg('stop', { offset: 1, 'stop-color': sky.bottom }));
  defs.append(gradient);
  icon.append(defs, svg('rect', { width: 48, height: 48, rx: 11, fill: `url(#${id}-sky)` }));
  if (sky.moon) {
    const [x, y] = sky.moon;
    const mask = svg('mask', { id: `${id}-moon` });
    mask.append(svg('rect', { width: 48, height: 48, fill: '#fff' }), svg('circle', { cx: x + 4, cy: y - 3, r: 6.5, fill: '#000' }));
    defs.append(mask);
    icon.append(...STARS.map(([cx, cy, r]) => svg('circle', { cx, cy, r, fill: '#fff', opacity: 0.85 })));
    icon.append(svg('circle', { cx: x, cy: y, r: 7.5, fill: '#f3efcf', mask: `url(#${id}-moon)` }));
  } else {
    const [x, y] = sky.sun;
    icon.append(
      svg('circle', { cx: x, cy: y, r: 12.5, fill: sky.sunColor, opacity: 0.3 }),
      svg('circle', { cx: x, cy: y, r: 8.5, fill: sky.sunColor }),
    );
  }
  icon.append(svg('path', { d: CLOUD_PATH, fill: sky.cloud, transform: 'translate(-1 7)' }));
  return icon;
}

function renderBrand() {
  const part = dayPart(new Date());
  if (part === brandPart) return;
  brandPart = part;
  const brand = document.querySelector('.brand');
  const icon = brandIcon(part);
  brand.querySelector('img, .brand-icon').replaceWith(icon);
  brand.title = `Météo · ${DAY_PARTS[part].label}`;
  // The browser tab shows the same sky; an installed app keeps its own icon.
  const tab = document.querySelector('link[rel="icon"]');
  if (tab) tab.href = `data:image/svg+xml,${encodeURIComponent(new XMLSerializer().serializeToString(icon))}`;
}

function renderSky() {
  const box = document.getElementById('sky-line');
  box.hidden = !view.skyOn;
  if (!view.skyOn) return;
  const sun = sunToday(view.data && shownData());
  box.replaceChildren(...[sun && sunLine(sun), moonLine(Date.now() / 1000)].filter(Boolean));
}

/* ---------- Header, notices and station cards ---------- */

function renderPeriods() {
  const box = document.getElementById('periods');
  box.replaceChildren(...PERIODS.map(([hours, label]) => {
    const button = el('button', '', label);
    button.type = 'button';
    button.setAttribute('aria-pressed', String(!view.range && hours === view.hours));
    button.addEventListener('click', () => {
      view.hours = hours;
      view.forecastDays = null;
      view.range = null;
      view.zoomStack = [];
      view.datesDirty = false;
      savePeriod(hours);
      renderPeriods();
      refresh(true);
    });
    return button;
  }));
}

/* ---------- Zoom and time navigation ---------- */

function currentWindow() {
  return view.range || { from: view.data.since, to: view.data.until };
}

function zoomTo(from, to) {
  if (to - from < MIN_ZOOM) {
    const middle = (from + to) / 2;
    from = middle - MIN_ZOOM / 2;
    to = middle + MIN_ZOOM / 2;
  }
  view.zoomStack.push(view.range);
  view.range = { from: Math.round(from), to: Math.round(to) };
  view.datesDirty = false;
  refresh(true);
}

function zoomOut() {
  if (!view.range) return;
  view.range = view.zoomStack.length ? view.zoomStack.pop() : null;
  view.datesDirty = false;
  refresh(true);
}

// Back to the period shown before any zoom or move, in one go.
function zoomReset() {
  if (!view.range) return;
  view.range = view.zoomStack.length ? view.zoomStack[0] : null;
  view.zoomStack = [];
  view.datesDirty = false;
  refresh(true);
}

function pan(direction) {
  if (!view.data) return;
  const { from, to } = currentWindow();
  const shift = Math.min((to - from) * PAN_SHARE * direction, view.data.now - to);
  if (!view.range) view.zoomStack.push(null);
  view.range = { from: Math.round(from + shift), to: Math.round(to + shift) };
  view.datesDirty = false;
  refresh(true);
}

function dateValue(ts) {
  const date = new Date(ts * 1000);
  const pad = (value) => String(value).padStart(2, '0');
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

function markDatesChanged() {
  view.datesDirty = true;
  document.getElementById('date-apply').disabled = false;
  document.getElementById('date-message').textContent = '';
}

function applyDates(event) {
  event.preventDefault();
  const start = document.getElementById('date-from').value;
  const end = document.getElementById('date-to').value;
  const message = document.getElementById('date-message');
  if (!start || !end) {
    message.textContent = 'Indiquez les deux dates.';
    return;
  }
  const from = new Date(`${start}T00:00:00`).getTime() / 1000;
  const to = new Date(`${end}T23:59:59`).getTime() / 1000;
  if (from >= to) {
    message.textContent = 'La première date doit précéder la seconde.';
    return;
  }
  view.range = { from, to };
  view.zoomStack = [];
  view.datesDirty = false;
  refresh(true);
}

function renderRange(data) {
  const start = document.getElementById('date-from');
  const end = document.getElementById('date-to');
  if (!view.datesDirty) {
    start.value = dateValue(data.since);
    end.value = dateValue(data.until);
    document.getElementById('date-apply').disabled = true;
    document.getElementById('date-message').textContent = '';
  }
  start.min = end.min = dateValue(data.first);
  start.max = end.max = dateValue(data.now);
  document.getElementById('zoom-out').disabled = !view.range;
  document.getElementById('pan-right').disabled = data.until >= data.now - 60;
  const parts = view.range ? [`Affiché : du ${dateTime(data.since)} au ${dateTime(data.until)}.`] : [];
  if (view.compare && !view.compare.stations.some((station) => station.series.ts.length)) {
    parts.push('Pas encore de mesures de l’an dernier sur cette période.');
  }
  const label = document.getElementById('range-label');
  label.textContent = parts.join(' ');
  label.hidden = !parts.length;
}

/* ---------- Moment fixed by a click ---------- */

function pinLabel(ts) {
  return view.data.daily ? dayLabel(ts) : dateTime(ts);
}

function renderPin() {
  document.getElementById('pin-bar').hidden = view.pinned === null;
  if (view.pinned === null) return;
  const values = shownStations(view.data).flatMap((station) => {
    const reading = readingAt(station, view.data);
    return reading && reading.temperature !== null
      ? [`${shortName(station, view.data.stations)} ${withUnit(reading.temperature, METRICS[0])}`]
      : [];
  });
  document.getElementById('pin-label').textContent = [pinLabel(view.pinned), ...values].join(' · ');
}

function setPin(ts) {
  view.pinned = ts;
  renderPin();
  renderStations(shownData());
  setHover(null);
}

function togglePin(ts) {
  setPin(ts === view.pinned ? null : ts);
}

function pointTolerance(data) {
  return Math.max(data.poll_seconds * 3, data.bucket * 3, HISTORY_GAP) / 3;
}

function estimateAt(station, ts, data) {
  const estimate = station.wind_estimate;
  if (!estimate.ts.length) return null;
  const index = nearestIndex(estimate.ts, ts, (time) => time);
  if (Math.abs(estimate.ts[index] - ts) > Math.max(pointTolerance(data), HOUR / 2)) return null;
  return {
    wind_strength: estimate.wind_strength[index],
    wind_angle: estimate.wind_angle[index],
    gust_strength: estimate.gust_strength[index],
  };
}

function withEstimate(reading, estimate) {
  if (reading.wind_strength !== null || !estimate) return reading;
  const { wind_strength, wind_angle, gust_strength } = estimate;
  return { ...reading, wind_strength, wind_angle, gust_strength, windEstimated: true };
}

function readingAt(station, data) {
  const stamps = station.series.ts;
  if (!stamps.length) return null;
  const index = nearestIndex(stamps, view.pinned, (time) => time);
  if (Math.abs(stamps[index] - view.pinned) > pointTolerance(data)) return null;
  const reading = { ts: stamps[index] };
  for (const [name, values] of Object.entries(station.series)) reading[name] = values[index];
  return withEstimate(reading, estimateAt(station, stamps[index], data));
}

/* ---------- Options of all the charts ---------- */

function compareAllowed(data) {
  return (view.range !== null || view.hours !== 0) && data.until - data.since <= MAX_COMPARE_SPAN;
}

function optionButton(label, isOn, isAvailable, reason, onClick) {
  const button = toggleButton(label, isOn && isAvailable, onClick);
  button.disabled = !isAvailable;
  if (!isAvailable) button.title = reason;
  return button;
}

function displayOption(label, flag, key) {
  return optionButton(label, view[flag], true, '', () => {
    view[flag] = !view[flag];
    saveFlag(key, view[flag]);
    refresh(true);
  });
}

function renderOptions(data) {
  document.getElementById('view-options').replaceChildren(
    optionButton('Min/max par jour', view.dailyOn, data.until - data.since >= DAILY_MIN_SPAN,
      'Disponible à partir de 7 jours affichés', () => {
        view.dailyOn = !view.dailyOn;
        saveFlag('meteo-daily', view.dailyOn);
        refresh(true);
      }),
    optionButton('Comparer à l’an dernier', view.compareOn, compareAllowed(data),
      'Disponible jusqu’à un an affiché', () => {
        view.compareOn = !view.compareOn;
        saveFlag('meteo-compare', view.compareOn);
        refresh(true);
      }),
    displayOption('Météo sur les cartes', 'hoursOn', 'meteo-hours'),
    displayOption('Prévisions', 'forecastOn', 'meteo-forecast'),
    displayOption('Alertes météo', 'alertsOn', 'meteo-alerts'),
    displayOption('Baromètre', 'barometerOn', 'meteo-barometer'),
    displayOption('Stats', 'statsOn', 'meteo-stats'),
    displayOption('Soleil et lune', 'skyOn', 'meteo-sky'),
    optionButton('Stations voisines', view.nearbyOn, data.neighbours_allowed !== false, 'Désactivé par l’administrateur', () => {
      view.nearbyOn = !view.nearbyOn;
      saveFlag('meteo-nearby', view.nearbyOn);
      refresh(true);
    }),
    ...(view.nearbyOn ? [radiusControl()] : []),
  );
}

function radiusControl() {
  const label = el('label', 'radius');
  const value = el('span', '', `${view.radius} km`);
  const input = el('input');
  input.type = 'range';
  input.min = '1';
  input.max = String(MAX_RADIUS);
  input.value = String(view.radius);
  input.setAttribute('aria-label', 'Rayon de recherche des stations voisines');
  input.addEventListener('input', () => { value.textContent = `${input.value} km`; });
  input.addEventListener('change', () => {
    view.radius = Number(input.value);
    saveRadius(view.radius);
    refresh(true);
  });
  label.append(input, value);
  return label;
}

/* ---------- Choice of stations and curves ---------- */

function toggleButton(label, isOn, onClick, slot) {
  const button = el('button', slot === undefined ? '' : `series-${slot}`);
  button.type = 'button';
  button.setAttribute('aria-pressed', String(isOn));
  if (slot !== undefined) button.append(el('i', 'key'));
  button.append(label);
  button.addEventListener('click', onClick);
  return button;
}

function toggleHidden(set, storageKey, key, shownCount) {
  if (set.has(key)) set.delete(key);
  else if (shownCount > 1) set.add(key); // at least one always stays visible
  saveSet(storageKey, set);
  renderContent();
}

function firstWord(name) {
  return name.split(/[\s-]/)[0];
}

function shortName(station, stations) {
  const { name } = station;
  if (name.length <= SHORT_NAME_MAX) return name;
  const word = firstWord(name);
  const isAmbiguous = stations.some((other) => other !== station && firstWord(other.name) === word);
  return isAmbiguous ? name : word;
}

function shownStations(data) {
  const alone = data.stations.find((station) => station.id === view.stationId);
  return alone ? [alone] : data.stations;
}

function showStation(id) {
  view.stationId = id;
  view.pinned = null;
  renderContent();
}

function renderStationTabs(data) {
  const box = document.getElementById('station-tabs');
  if (data.stations.length < 2) {
    box.replaceChildren();
    return;
  }
  const shown = shownStations(data);
  const all = toggleButton('Toutes les stations', shown.length > 1, () => showStation(null));
  box.replaceChildren(all, ...data.stations.map((station) => {
    const tab = toggleButton(
      shortName(station, data.stations),
      shown.length === 1 && shown[0] === station,
      () => showStation(station.id),
      station.slot,
    );
    tab.title = station.name;
    return tab;
  }));
}

function renderFilters(data) {
  document.getElementById('credits').hidden = !data.stations.some(
    (station) => station.forecast || station.wind_estimate.ts.length,
  );
  renderStationTabs(data);
  const shownOnly = { ...data, stations: shownStations(data) };
  const metrics = METRICS.filter((metric) => chartSeries(shownOnly, displayMetric(shownOnly, metric)).length);
  const shownMetrics = metrics.filter((metric) => !view.hiddenMetrics.has(metric.key));
  document.getElementById('metric-filter').replaceChildren(...metrics.map((metric) => toggleButton(
    metric.short || metric.title,
    shownMetrics.includes(metric),
    () => toggleHidden(view.hiddenMetrics, 'meteo-hidden-metrics', metric.key, shownMetrics.length),
  )));
}

function notice(title, lines, isError) {
  const box = el('div', isError ? 'notice error' : 'notice');
  box.append(el('strong', '', title));
  for (const line of lines) box.append(line);
  return box;
}

function setupSteps() {
  const list = el('ol');
  for (const step of [
    'Créer une application sur dev.netatmo.com (menu « My Apps »).',
    'Copier son client ID et son client secret dans config.json.',
    'Redémarrer le dashboard, puis cliquer sur « Connecter mon compte ».',
  ]) list.append(el('li', '', step));
  return list;
}

// On the PC, the person running the program connects Netatmo; on the site, the administrator does.
function setupNotices(data) {
  if (data.demo) {
    return [notice('Mode démonstration : ces valeurs sont simulées', [
      el('span', '', 'Aucune donnée ne vient de Netatmo. Lancez le dashboard sans --demo pour les vraies mesures.'),
    ])];
  }
  if (data.setup === 'ok') return [];
  if (data.mode !== 'local') {
    return [notice('La collecte Netatmo est arrêtée', [
      el('span', '', 'Le serveur n’est plus connecté à Netatmo : prévenez son administrateur.'),
    ])];
  }
  if (data.setup === 'config') return [notice('Accès Netatmo à configurer', [setupSteps()])];
  const link = el('a', '', 'Connecter mon compte Netatmo');
  link.href = '/connecter';
  return [notice('Dernière étape', [link])];
}

function renderNotices(data) {
  const items = setupNotices(data);
  if (!data.stations.length) {
    items.push(notice('Bienvenue', [
      el('span', '', 'Ajoutez votre première station ci-dessous. Le guide explique comment trouver son lien.'),
    ]));
  }
  if (data.announcement) {
    items.push(notice('Message de l’administrateur', [el('span', '', data.announcement)]));
  }
  if (data.status.error) {
    items.push(notice('Problème de collecte', [el('span', '', data.status.error)], true));
  }
  if (data.status.notify_error) {
    items.push(notice('Notification non envoyée', [el('span', '', data.status.notify_error)], true));
  }
  if (view.alertsOn && data.status.alerts_error) {
    items.push(notice('Alertes météo indisponibles pour l’instant', [
      el('span', '', 'Vérifiez les vigilances officielles sur meteofrance.com. Nouvel essai automatique.'),
    ]));
  }
  for (const station of data.stations) {
    const error = data.status.stations[station.id];
    if (error) {
      items.push(notice(`${station.name} : dernière collecte en échec`, [el('span', '', error)], true));
    }
    const historyError = data.status.history[station.id];
    if (historyError) {
      items.push(notice(`${station.name} : historique indisponible pour l’instant`, [
        el('span', '', `${historyError}. Les mesures en direct continuent et un nouvel essai aura lieu automatiquement.`),
      ]));
    }
    const forecastError = data.status.forecast[station.id];
    if (forecastError && !station.forecast) {
      items.push(notice(`${station.name} : prévisions indisponibles pour l’instant`, [
        el('span', '', `${forecastError}. Nouvel essai dans l’heure.`),
      ]));
    }
    const windError = data.status.wind[station.id];
    if (windError) {
      items.push(notice(`${station.name} : vent estimé indisponible pour l’instant`, [
        el('span', '', `${windError}. Nouvel essai dans l’heure.`),
      ]));
    }
  }
  document.getElementById('notices').replaceChildren(...items);
}

function tile(label, value, detail) {
  const box = el('div', 'tile');
  box.append(el('span', '', label), el('b', '', value));
  if (detail) box.append(el('small', '', detail));
  return box;
}

function sunShapes(x, y, radius) {
  const rays = [0, 45, 90, 135, 180, 225, 270, 315].map((angle) => {
    const [cos, sin] = [Math.cos(angle * Math.PI / 180), Math.sin(angle * Math.PI / 180)];
    return svg('line', {
      x1: x + cos * (radius + 3), y1: y + sin * (radius + 3),
      x2: x + cos * (radius + 6), y2: y + sin * (radius + 6),
    });
  });
  const group = svg('g', { class: 'wx-sun' });
  group.append(svg('circle', { cx: x, cy: y, r: radius }), ...rays);
  return group;
}

function moonShape(x, y, scale) {
  const path = 'M0-10a10 10 0 1 0 10 13 8 8 0 0 1-10-13z';
  return svg('path', { class: 'wx-moon', d: path, transform: `translate(${x} ${y}) scale(${scale})` });
}

function fallingLines(xs, length) {
  const group = svg('g', { class: 'wx-rain' });
  group.append(...xs.map((x) => svg('line', { x1: x, y1: 37, x2: x - 2, y2: 37 + length })));
  return group;
}

function weatherIcon(code, isDay) {
  const [picture, label] = WEATHER[code] || ['cloud', 'Temps inconnu'];
  const icon = svg('svg', { class: 'wx', viewBox: '0 0 48 48', role: 'img', 'aria-label': label });
  if (picture === 'clear') {
    icon.append(isDay ? sunShapes(24, 24, 9) : moonShape(24, 24, 1.5));
    return icon;
  }
  if (picture === 'partly') icon.append(isDay ? sunShapes(15, 15, 6) : moonShape(14, 15, 0.9));
  icon.append(svg('path', { class: 'wx-cloud', d: CLOUD_PATH }));
  if (picture === 'drizzle') icon.append(fallingLines([18, 30], 4));
  if (picture === 'rain') icon.append(fallingLines([15, 24, 33], 7));
  if (picture === 'snow') {
    icon.append(...[15, 24, 33].map((x, i) => svg('circle', { class: 'wx-snow', cx: x, cy: 39 + (i % 2) * 4, r: 2.2 })));
  }
  if (picture === 'fog') {
    const lines = svg('g', { class: 'wx-fog' });
    lines.append(...[38, 43].map((y) => svg('line', { x1: 9, y1: y, x2: 39, y2: y })));
    icon.append(lines);
  }
  if (picture === 'storm') icon.append(svg('path', { class: 'wx-bolt', d: 'M26 30l-8 10h6l-3 8 10-12h-6l3-6z' }));
  return icon;
}

function roundedDegrees(value) {
  return value === null || value === undefined ? '–' : `${number(Math.round(value), 0)}°`;
}

function hourTile(label, code, isDay, degrees, isNow) {
  const tile = el('div', isNow ? 'hour now' : 'hour');
  tile.title = (WEATHER[code] || [null, ''])[1];
  tile.append(el('span', '', label), weatherIcon(code, isDay), el('b', '', degrees));
  return tile;
}

// 0 means hour by hour.
function periodForecastDays() {
  return view.hours === 24 ? 0 : FORECAST_DAYS[view.hours] || MAX_FORECAST_DAYS;
}

// The first day is left out: the "Maintenant" tile stands for today.
function comingTiles(station) {
  const dayCount = periodForecastDays();
  if (!dayCount) {
    return comingHours(station, HOURS_ON_CARD).map(
      (hour) => hourTile(hourName(hour), hour.code, Boolean(hour.is_day), roundedDegrees(hour.temperature), false),
    );
  }
  return station.forecast.days.slice(1, dayCount).map((day) => hourTile(
    forecastDayName(day.date), day.code, true, `${roundedDegrees(day.max)}/${roundedDegrees(day.min)}`, false,
  ));
}

function comingHours(station, count) {
  const now = Date.now();
  return (station.forecast.hours || []).filter((hour) => new Date(hour.time).getTime() > now).slice(0, count);
}

function hourName(hour) {
  return `${new Date(hour.time).getHours()}h`;
}

function hoursStrip(station, latest) {
  const { code, is_day: isDay } = station.forecast;
  const row = el('div', 'hours-row');
  row.append(hourTile('Maintenant', code, isDay, roundedDegrees(latest.temperature), true), ...comingTiles(station));
  const previous = hoursArrow(row, 'hours-previous', '‹', 'Revenir en arrière', -1);
  const next = hoursArrow(row, 'hours-next', '›', 'Voir la suite', 1);
  const showArrows = () => {
    previous.hidden = row.scrollLeft <= 4;
    next.hidden = row.scrollLeft + row.clientWidth >= row.scrollWidth - 4;
  };
  previous.hidden = true;
  row.addEventListener('scroll', showArrows);
  requestAnimationFrame(showArrows); // the row has no size until it is on the page
  dragToScroll(row);
  const box = el('div', 'hours');
  box.append(row, previous, next);
  return box;
}

function hoursArrow(row, className, symbol, label, direction) {
  const arrow = el('button', `hours-arrow ${className}`, symbol);
  arrow.type = 'button';
  arrow.setAttribute('aria-label', label);
  arrow.addEventListener('click', () => {
    row.scrollTo({ left: row.scrollLeft + direction * row.clientWidth * 0.7 });
  });
  return arrow;
}

// A finger scrolls the row by itself: this gives the mouse the same gesture.
function dragToScroll(row) {
  let start = null;
  const stop = () => {
    start = null;
    row.classList.remove('dragging');
  };
  row.addEventListener('pointerdown', (event) => {
    if (event.pointerType !== 'mouse') return;
    start = { x: event.clientX, left: row.scrollLeft };
    row.setPointerCapture(event.pointerId);
    row.classList.add('dragging');
  });
  row.addEventListener('pointermove', (event) => {
    if (start) row.scrollLeft = start.left + start.x - event.clientX;
  });
  row.addEventListener('pointerup', stop);
  row.addEventListener('pointercancel', stop);
}

function alertText(alert) {
  const when = alert.start > Date.now() / 1000
    ? `à partir de ${dateTime(alert.start)}, jusqu'à ${dateTime(alert.end)}`
    : `jusqu'à ${dateTime(alert.end)}`;
  return `Vigilance ${ALERT_NAMES[alert.level]} ${alert.hazard}, ${when}`;
}

function alertBadges(station) {
  const badges = el('div', 'alert-badges');
  const detail = el('div', 'alert-detail');
  detail.hidden = true;
  detail.append(...station.alerts.map((alert) => el('span', '', alertText(alert))));
  for (const alert of station.alerts) {
    const badge = el('button', `alert-badge level-${alert.level}`, alert.icon);
    badge.type = 'button';
    badge.title = alertText(alert);
    badge.setAttribute('aria-label', alertText(alert));
    badge.addEventListener('click', () => { detail.hidden = !detail.hidden; });
    badges.append(badge);
  }
  return { badges, detail };
}

function nearbyLine(summary) {
  const area = `${summary.radius} km`;
  if (summary.pending) return el('p', 'nearby', `Stations voisines (${area}) : calcul en cours…`);
  if (summary.temperature === undefined) return el('p', 'nearby', `Pas assez de stations voisines dans ${area}`);
  const values = [
    summary.temperature === null ? null : withUnit(summary.temperature, METRICS[0]),
    summary.humidity === null ? null : `${number(summary.humidity, 0)} %`,
    summary.pressure === null ? null : `${number(summary.pressure, 0)} hPa`,
  ].filter(Boolean);
  const line = el('p', 'nearby', `Médiane de ${summary.count} voisines (${area}) : ${values.join(' · ')}`);
  line.title = 'Valeur du milieu des stations publiques voisines, sans cette station';
  return line;
}

function barometerLine({ level, text, change, fog }) {
  const box = el('div', 'baro');
  const arrow = el('span', 'baro-arrow', BAROMETER_ARROWS[level]);
  arrow.setAttribute('aria-hidden', 'true');
  const sign = change > 0 ? '+' : '';
  const body = el('div');
  body.append(
    el('p', '', `Baromètre : ${text}`),
    el('small', '', `${sign}${number(change, 1)} hPa en 3 h, selon cette station`),
  );
  if (fog) body.append(el('small', '', 'Air très humide : brouillard possible'));
  box.append(arrow, body);
  return box;
}

function forecastDayName(date, today) {
  if (date === today) return 'Aujourd’hui';
  const [year, month, day] = date.split('-').map(Number);
  return new Date(year, month - 1, day).toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric' });
}

function forecastDay(day, today, isDetailed) {
  const label = (WEATHER[day.code] || [null, ''])[1];
  const box = el('div', 'forecast-day');
  box.title = `${label}, pluie ${number(day.rain, 1)} mm (${day.rain_chance} %), vent ${number(day.wind, 0)} km/h`;
  box.append(
    el('span', '', forecastDayName(day.date, today)),
    weatherIcon(day.code, true),
    el('b', '', `${number(day.max, 0)}° / ${number(day.min, 0)}°`),
  );
  if (day.rain > 0 || isDetailed) box.append(el('small', '', `${number(day.rain, 1)} mm · ${day.rain_chance} %`));
  if (isDetailed) {
    box.append(el('small', '', label), el('small', '', `Vent ${number(day.wind, 0)}, rafales ${number(day.gust, 0)} km/h`));
  }
  return box;
}

function forecastHour(hour, isDetailed) {
  const label = (WEATHER[hour.code] || [null, ''])[1];
  const box = el('div', 'forecast-day');
  box.title = `${label}, pluie ${hour.rain_chance} %, vent ${number(hour.wind, 0)} km/h`;
  box.append(el('span', '', hourName(hour)), weatherIcon(hour.code, Boolean(hour.is_day)), el('b', '', roundedDegrees(hour.temperature)));
  if (hour.rain_chance > 0 || isDetailed) box.append(el('small', '', `Pluie ${hour.rain_chance} %`));
  if (isDetailed) box.append(el('small', '', label), el('small', '', `Vent ${number(hour.wind, 0)} km/h`));
  return box;
}

function renderForecastChoice(dayCount) {
  document.getElementById('forecast-modes').replaceChildren(...FORECAST_CHOICES.map(([days, label]) => {
    const button = el('button', '', label);
    button.type = 'button';
    button.setAttribute('aria-pressed', String(days === dayCount));
    button.addEventListener('click', () => {
      view.forecastDays = days;
      renderForecast(shownData());
    });
    return button;
  }));
}

function renderForecast(data) {
  const dayCount = view.forecastDays === null ? periodForecastDays() : view.forecastDays; // 0: hour by hour
  renderForecastChoice(dayCount);
  document.getElementById('forecast-title').textContent = dayCount ? `Prévisions sur ${dayCount} jours` : 'Prévisions sur 24 h';
  const stations = data.stations.filter((station) => station.forecast);
  const isDetailed = stations.length === 1;
  const today = dateValue(data.now);
  const panel = document.getElementById('forecast-panel');
  panel.hidden = !stations.length || !view.forecastOn;
  if (view.forecastFor !== view.stationId) { // a single station always opens it
    view.forecastFor = view.stationId;
    panel.open = isDetailed || view.forecastOpen;
  }
  const cards = stations.map((station) => {
    const card = el('article', 'forecast-card');
    const head = el('div', 'forecast-head');
    head.append(
      el('h2', '', `Prévisions : ${station.name}`),
      el('span', 'muted', `Mises à jour à ${clock(station.forecast.fetched)}`),
    );
    const days = el('div', 'forecast-days');
    days.append(...(dayCount
      ? station.forecast.days.slice(0, dayCount).map((day) => forecastDay(day, today, isDetailed))
      : comingHours(station, HOURS_IN_PANEL).map((hour) => forecastHour(hour, isDetailed))));
    card.append(head, days);
    return card;
  });
  if (dayCount > RELIABLE_DAYS) cards.push(el('p', 'muted', `Au-delà de ${RELIABLE_DAYS} jours, ce sont des tendances, bien moins fiables.`));
  document.getElementById('forecast').replaceChildren(...cards);
}

function statTile(label, value, note) {
  const tile = el('div', 'stat');
  tile.append(el('span', '', label), el('b', '', value));
  if (note) tile.append(el('small', '', note));
  return tile;
}

function statGrid(title, tiles) {
  const grid = el('div', 'stats-grid');
  grid.append(...tiles.filter(Boolean));
  const box = el('div');
  box.append(el('h3', 'stats-title', title), grid);
  return box;
}

function signed(value, decimals, unit) {
  return `${value > 0 ? '+' : ''}${number(value, decimals)} ${unit}`;
}

function rainDayLabel(day) {
  const [year, month, date] = day.split('-').map(Number);
  return dayLabel(new Date(year, month - 1, date).getTime() / 1000);
}

function extremeTile(label, extreme, metric) {
  return extreme && statTile(label, withUnit(extreme.value, metric), dayLabel(extreme.ts));
}

function wettestTile(label, rain) {
  return rain && statTile(label, `${number(rain.wettest.value, 1)} mm`, rainDayLabel(rain.wettest.day));
}

function periodTiles({ period, last_year: past }) {
  const temperature = METRICS[0];
  const pastMean = past && past.mean !== null ? `${signed(period.mean - past.mean, 1, '°C')} sur un an` : '';
  const pastRain = past && past.rain !== null && period.rain ? `${signed(period.rain.total - past.rain, 1, 'mm')} sur un an` : '';
  return [
    period.mean !== null && statTile('Température moyenne', withUnit(period.mean, temperature), pastMean),
    extremeTile('Plus chaud', period.hottest, temperature),
    extremeTile('Plus froid', period.coldest, temperature),
    statTile('Jours de gel', number(period.frost_days, 0), `sur ${number(period.days, 0)} jour${period.days > 1 ? 's' : ''}`),
    period.hot_days > 0 && statTile('Jours à 30 °C ou plus', number(period.hot_days, 0)),
    period.rain && statTile('Pluie', `${number(period.rain.total, 1)} mm`, pastRain || `${number(period.rain.rainy_days, 0)} jour${period.rain.rainy_days > 1 ? 's' : ''} de pluie`),
    wettestTile('Jour le plus arrosé', period.rain),
    period.gust && statTile('Plus forte rafale', `${number(period.gust.value, 0)} km/h`, dayLabel(period.gust.ts)),
  ];
}

function recordTiles({ records }) {
  const temperature = METRICS[0];
  return [
    extremeTile('Record de chaleur', records.hottest, temperature),
    extremeTile('Record de froid', records.coldest, temperature),
    wettestTile('Record de pluie en un jour', records.rain),
    records.gust && statTile('Record de rafale', `${number(records.gust.value, 0)} km/h`, dayLabel(records.gust.ts)),
  ];
}

// Rain of the month, season and year so far, compared with the same dates a year earlier.
function rainCalendarTiles(calendar) {
  return ['month', 'season', 'year'].map((key) => {
    const span = calendar[key];
    const [year, month, date] = span.since.split('-').map(Number);
    const since = new Date(year, month - 1, date).toLocaleDateString('fr-FR', { day: 'numeric', month: 'long', year: 'numeric' });
    const note = span.last_year === null
      ? `depuis le ${since}`
      : `${number(span.last_year, 1)} mm l’an dernier à la même date`;
    return statTile(`Pluie ${span.label}`, `${number(span.total, 1)} mm`, note);
  });
}

function neighbourTiles({ gaps, count, low, high, warmer_than: warmerThan }) {
  const pressureOff = gaps.pressure !== null && Math.abs(gaps.pressure) >= PRESSURE_OFFSET;
  return [
    gaps.temperature !== null && statTile('Écart de température', signed(gaps.temperature, 1, '°C'), 'avec la médiane'),
    gaps.humidity !== null && statTile('Écart d’humidité', signed(gaps.humidity, 0, '%'), 'avec la médiane'),
    gaps.pressure !== null && statTile('Écart de pression', signed(gaps.pressure, 1, 'hPa'), pressureOff ? 'Baromètre sans doute décalé' : 'avec la médiane'),
    warmerThan !== null && statTile('Plus chaud que', `${warmerThan} %`, `des ${count} voisines`),
    low !== null && statTile('Voisines', `${number(low, 1)} à ${number(high, 1)} °C`, 'de la plus froide à la plus chaude'),
  ];
}

function neighbourStats(item) {
  if (item.neighbours) return statGrid(`Par rapport aux voisines (${item.neighbours.radius} km), en ce moment`, neighbourTiles(item.neighbours));
  return el('p', 'muted', view.nearbyOn
    ? 'Comparaison aux voisines indisponible pour l’instant : calcul en cours ou trop peu de voisines.'
    : 'Activez « Stations voisines » pour comparer cette station à son secteur.');
}

function stationStats(item, station) {
  const card = el('article', 'forecast-card');
  const head = el('div', 'forecast-head');
  head.append(el('h2', '', station.name));
  card.append(
    head,
    statGrid('Sur la période affichée', periodTiles(item)),
  );
  if (item.rain_calendar) card.append(statGrid('Cumuls de pluie à ce jour', rainCalendarTiles(item.rain_calendar)));
  card.append(
    statGrid(`Records depuis le ${dayLabel(item.oldest)}`, recordTiles(item)),
    neighbourStats(item),
  );
  return card;
}

async function loadStats() {
  const request = ++view.statsRequest;
  try {
    const response = await fetch(`/api/stats?${dataQuery()}`);
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const { stations } = await response.json();
    if (request !== view.statsRequest) return; // a newer request has replaced this one
    const shown = shownData().stations;
    document.getElementById('stats').replaceChildren(...shown.map((station) => {
      const item = stations.find((candidate) => candidate.id === station.id);
      return item && item.oldest ? stationStats(item, station) : el('p', 'muted', `${station.name} : pas encore de mesures.`);
    }));
  } catch {
    document.getElementById('stats').replaceChildren(el('p', 'muted', 'Stats indisponibles pour l’instant.'));
  }
}

function renderStats(data) {
  const panel = document.getElementById('stats-panel');
  panel.hidden = !view.statsOn || !data.stations.length;
  panel.open = view.statsOpen;
  if (view.statsOn && view.statsOpen) loadStats();
}

function stationFooter(station, data) {
  const foot = el('div', 'station-foot');
  if (station.place && station.place !== station.name) foot.append(el('span', '', station.place));
  if (station.url) {
    const link = el('a', '', 'Voir sur Netatmo ↗');
    link.href = station.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    foot.append(link);
  }
  if (!data.demo) {
    const tools = el('span', 'station-tools');
    if (data.stations.length > 1) {
      const index = data.stations.indexOf(station);
      for (const [symbol, label, step] of [['←', 'Avancer', -1], ['→', 'Reculer', 1]]) {
        const target = index + step;
        if (target < 0 || target >= data.stations.length) continue;
        const move = el('button', 'move', symbol);
        move.type = 'button';
        move.title = `${label} ${station.name} dans la liste`;
        move.setAttribute('aria-label', move.title);
        move.addEventListener('click', () => moveStation(data.stations, index, target));
        tools.append(move);
      }
    }
    for (const [label, action] of [['Renommer', renameStation], ['Retirer', removeStation]]) {
      const button = el('button', '', label);
      button.type = 'button';
      button.setAttribute('aria-label', `${label} ${station.name}`);
      button.addEventListener('click', () => action(station));
      tools.append(button);
    }
    foot.append(tools);
  }
  return foot;
}

function stationCard(station, data) {
  const card = el('article', `card station series-${station.slot}`);
  const head = el('div', 'station-head');
  head.append(el('i', 'key'), el('h2', '', station.name));
  card.append(head);
  if (view.alertsOn && station.alerts.length) {
    const { badges, detail } = alertBadges(station);
    head.append(badges);
    card.append(detail);
  }
  head.append(screenButton(station));

  const isPinned = view.pinned !== null;
  const latest = isPinned
    ? readingAt(station, data)
    : station.latest && withEstimate(station.latest, station.wind_latest);
  if (!latest) {
    const message = isPinned ? 'Pas de mesure à ce moment.' : 'Aucune mesure pour l’instant.';
    card.append(el('p', 'muted', message), stationFooter(station, data));
    return card;
  }

  const age = data.now - latest.ts;
  const isStale = !isPinned && age > data.poll_seconds * 3;
  if (isStale) card.append(el('p', 'stale-banner', `⚠ Plus de nouvelle mesure, dernière ${ago(age)}`));
  const hero = el('div', 'hero');
  hero.append(el('strong', '', withUnit(latest.temperature, METRICS[0])));
  if (!isPinned && station.forecast) {
    const sky = el('span', 'sky', (WEATHER[station.forecast.code] || [null, ''])[1]);
    if (!view.hoursOn) sky.prepend(weatherIcon(station.forecast.code, station.forecast.is_day)); // no strip to show it
    hero.append(sky);
  }
  const low = isPinned ? latest.temperature_min : station.temperature_min;
  const high = isPinned ? latest.temperature_max : station.temperature_max;
  if (low !== null && high !== null && (!isPinned || data.daily)) {
    const scope = isPinned ? 'ce jour-là' : 'sur la période';
    hero.append(el('span', 'muted', `min ${number(low, 1)} · max ${number(high, 1)} ${scope}`));
  }
  card.append(hero);
  if (!isPinned && station.forecast && view.hoursOn) card.append(hoursStrip(station, latest));
  if (!isPinned && station.barometer && view.barometerOn) card.append(barometerLine(station.barometer));
  if (!isPinned && station.neighbours) card.append(nearbyLine(station.neighbours));

  const tiles = el('div', 'tiles');
  tiles.append(
    tile('Humidité', withUnit(latest.humidity, METRICS[1])),
    tile('Pression', withUnit(latest.pressure, METRICS[2])),
  );
  const dayTotal = isPinned && data.daily
    ? station.rain_days.find(([day]) => day === dateValue(latest.ts))
    : null;
  if (dayTotal) {
    tiles.append(tile('Pluie du jour', withUnit(dayTotal[1], METRICS[3])));
  } else if (!(isPinned && data.daily)) {
    if (latest.rain_60min !== null) tiles.append(tile('Pluie 1 h', withUnit(latest.rain_60min, METRICS[3])));
    if (latest.rain_24h !== null) tiles.append(tile('Pluie 24 h', withUnit(latest.rain_24h, METRICS[3])));
  }
  if (latest.wind_strength !== null) {
    // An average of angles means nothing, so grouped points show no direction.
    const isGrouped = isPinned && data.bucket > HOUR;
    const direction = latest.wind_angle === null || isGrouped ? '' : compass(latest.wind_angle);
    const gust = latest.gust_strength === null ? '' : `rafales ${number(latest.gust_strength, 0)}`;
    const label = latest.windEstimated ? 'Vent estimé' : 'Vent';
    tiles.append(tile(label, withUnit(latest.wind_strength, METRICS[4]), [direction, gust].filter(Boolean).join(' · ')));
  }
  card.append(tiles);

  const label = `Mesure de ${clock(latest.ts)}, ${ago(age)}`;
  const caption = isPinned ? `Valeurs du ${pinLabel(latest.ts)}` : label;
  card.append(el('p', 'muted stale', caption));
  card.append(stationFooter(station, data));
  return card;
}

function renderStations(data) {
  document.getElementById('stations').replaceChildren(
    ...data.stations.map((station) => stationCard(station, data)),
  );
  const panel = document.getElementById('add-station');
  panel.hidden = data.demo;
  if (!data.stations.length) panel.open = true;
}

/* ---------- A station over the whole screen ---------- */

const SCREEN_HOURS = 6;
const SCREEN_PARAM = 'ecran'; // ?ecran=<station id> opens it straight away, for a dedicated screen
let wakeLock = null;

async function keepScreenOn(isOn) {
  try {
    if (isOn && !wakeLock && 'wakeLock' in navigator) {
      wakeLock = await navigator.wakeLock.request('screen');
      wakeLock.addEventListener('release', () => { wakeLock = null; });
    } else if (!isOn && wakeLock) {
      await wakeLock.release();
    }
  } catch {
    /* the screen may still go to sleep on its own */
  }
}

function screenAddress(id) {
  const url = new URL(location.href);
  if (id) url.searchParams.set(SCREEN_PARAM, id);
  else url.searchParams.delete(SCREEN_PARAM);
  history.replaceState(null, '', url);
}

function openScreen(station) {
  view.fullscreenId = station.id;
  screenAddress(station.id);
  renderScreen();
  const box = document.getElementById('screen');
  // Phones without a full screen mode (iPhone) still get the page covered.
  if (box.requestFullscreen) box.requestFullscreen().catch(() => {});
  keepScreenOn(true);
}

function closeScreen() {
  view.fullscreenId = null;
  screenAddress(null);
  if (document.fullscreenElement) document.exitFullscreen().catch(() => {});
  keepScreenOn(false);
  renderScreen();
}

function screenButton(station) {
  const button = el('button', 'screen-open', '⛶');
  button.type = 'button';
  button.title = `Afficher ${station.name} en plein écran`;
  button.setAttribute('aria-label', button.title);
  button.addEventListener('click', () => openScreen(station));
  return button;
}

function screenTiles(latest) {
  const tiles = [
    tile('Humidité', withUnit(latest.humidity, METRICS[1])),
    tile('Pression', withUnit(latest.pressure, METRICS[2])),
  ];
  if (latest.rain_60min !== null) tiles.push(tile('Pluie 1 h', withUnit(latest.rain_60min, METRICS[3])));
  if (latest.rain_24h !== null) tiles.push(tile('Pluie 24 h', withUnit(latest.rain_24h, METRICS[3])));
  if (latest.wind_strength !== null) {
    const direction = latest.wind_angle === null ? '' : compass(latest.wind_angle);
    const gust = latest.gust_strength === null ? '' : `rafales ${number(latest.gust_strength, 0)}`;
    tiles.push(tile(latest.windEstimated ? 'Vent estimé' : 'Vent', withUnit(latest.wind_strength, METRICS[4]),
      [direction, gust].filter(Boolean).join(' · ')));
  }
  const box = el('div', 'tiles screen-tiles');
  box.append(...tiles);
  return box;
}

function renderScreen() {
  const box = document.getElementById('screen');
  const station = view.fullscreenId && view.data
    ? view.data.stations.find((item) => item.id === view.fullscreenId)
    : null;
  box.hidden = !station;
  document.body.classList.toggle('screen-shown', Boolean(station));
  if (!station) {
    box.replaceChildren();
    return;
  }
  const head = el('div', 'screen-head');
  const close = el('button', 'screen-close', '✕');
  close.type = 'button';
  close.title = 'Fermer le plein écran';
  close.setAttribute('aria-label', close.title);
  close.addEventListener('click', closeScreen);
  const title = el('div');
  title.append(el('h2', '', station.name), el('p', 'screen-clock', new Date().toLocaleString('fr-FR', {
    weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit',
  })));
  head.append(title, close);

  const latest = station.latest && withEstimate(station.latest, station.wind_latest);
  if (!latest) {
    box.replaceChildren(head, el('p', 'screen-empty', 'Aucune mesure pour l’instant.'));
    return;
  }
  const now = el('div', 'screen-now');
  if (station.forecast) now.append(weatherIcon(station.forecast.code, station.forecast.is_day));
  const reading = el('div');
  reading.append(el('strong', 'screen-temp', withUnit(latest.temperature, METRICS[0])));
  if (station.forecast) reading.append(el('p', 'screen-sky', (WEATHER[station.forecast.code] || [null, ''])[1]));
  now.append(reading);

  const parts = [head, now];
  if (view.alertsOn && station.alerts.length) {
    const alerts = el('div', 'screen-alerts');
    alerts.append(...station.alerts.map((alert) => el('p', `level-${alert.level}`, `${alert.icon} ${alertText(alert)}`)));
    parts.push(alerts);
  }
  parts.push(screenTiles(latest));
  if (station.barometer) parts.push(barometerLine(station.barometer));
  if (station.forecast) {
    const hours = comingHours(station, SCREEN_HOURS);
    if (hours.length) {
      const row = el('div', 'screen-hours');
      row.append(...hours.map((hour) => hourTile(hourName(hour), hour.code, Boolean(hour.is_day), roundedDegrees(hour.temperature), false)));
      parts.push(row);
    }
  }
  const foot = el('div', 'screen-foot');
  const sun = sunToday({ stations: [station] });
  const sky = el('p', 'sky-line');
  sky.append(...[sun && sunLine(sun), moonLine(Date.now() / 1000)].filter(Boolean));
  foot.append(sky, el('p', 'muted', `Mesure de ${clock(latest.ts)}, ${ago(view.data.now - latest.ts)}`));
  parts.push(foot);
  box.replaceChildren(...parts);
}

async function moveStation(stations, from, to) {
  const ids = stations.map((station) => station.id);
  [ids[from], ids[to]] = [ids[to], ids[from]];
  const response = await fetch('/api/stations/order', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ids }),
  });
  if (response.ok) refresh(true);
}

async function renameStation(station) {
  const name = prompt('Nouveau nom de la station', station.name);
  if (name === null || !name.trim() || name.trim() === station.name) return;
  const response = await fetch('/api/stations', {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: station.id, name }),
  });
  if (response.ok) refresh(true);
  else alert((await response.json()).error);
}

async function removeStation(station) {
  if (!confirm(`Retirer ${station.name} du dashboard ? Son historique reste conservé.`)) return;
  const response = await fetch(`/api/stations?id=${encodeURIComponent(station.id)}`, { method: 'DELETE' });
  if (response.ok) refresh(true);
}

async function addStation(event) {
  event.preventDefault();
  const form = event.target;
  const button = form.querySelector('button');
  const message = document.getElementById('add-message');
  button.disabled = true;
  message.textContent = 'Recherche de la station…';
  try {
    const response = await fetch('/api/stations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    const answer = await response.json();
    if (!response.ok) {
      message.textContent = answer.error;
      return;
    }
    form.reset();
    message.textContent = `${answer.name} ajoutée (${answer.place}).`;
    refresh(true);
  } catch {
    message.textContent = 'Serveur injoignable, réessayez.';
  } finally {
    button.disabled = false;
  }
}

function renderStatus() {
  const data = view.data;
  if (!data) return;
  if (data.status.history_running) {
    document.getElementById('status').textContent = `Récupération de l’historique de ${data.status.history_running}…`;
    return;
  }
  const now = Date.now() / 1000;
  const parts = [];
  if (data.status.last_poll) parts.push(`Collecte ${ago(now - data.status.last_poll)}`);
  if (data.status.next_poll) {
    const minutes = Math.max(0, Math.ceil((data.status.next_poll - now) / 60));
    parts.push(minutes ? `prochaine dans ${minutes} min` : 'prochaine imminente');
  }
  document.getElementById('status').textContent = parts.join(' · ') || 'En attente de la première collecte';
}

/* ---------- Charts ---------- */

function niceStep(rough) {
  const power = 10 ** Math.floor(Math.log10(rough));
  const ratio = rough / power;
  return (ratio <= 1 ? 1 : ratio <= 2 ? 2 : ratio <= 2.5 ? 2.5 : ratio <= 5 ? 5 : 10) * power;
}

function yDomain(values, metric) {
  let low = Math.min(...values);
  let high = Math.max(...values);
  if (metric.zero) low = 0;
  if (high - low < metric.minSpan) {
    const middle = (high + low) / 2;
    low = metric.zero ? 0 : middle - metric.minSpan / 2;
    high = metric.zero ? metric.minSpan : middle + metric.minSpan / 2;
  }
  const step = niceStep((high - low) / 4);
  const start = Math.floor(low / step) * step;
  const end = Math.ceil(high / step) * step;
  const ticks = [];
  for (let value = start; value <= end + step / 2; value += step) ticks.push(Number(value.toFixed(6)));
  return { low: start, high: end, ticks };
}

function xTicks(start, end, width) {
  const steps = [600, 1800, HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, DAY, 2 * DAY, 7 * DAY, 14 * DAY, 30 * DAY, 90 * DAY, 365 * DAY];
  const maxTicks = Math.max(2, Math.floor(width / 76));
  const step = steps.find((candidate) => (end - start) / candidate <= maxTicks) || steps[steps.length - 1];
  const midnight = new Date(start * 1000);
  midnight.setHours(0, 0, 0, 0);
  const ticks = [];
  for (let ts = midnight.getTime() / 1000; ts <= end; ts += step) {
    if (ts >= start) ticks.push({ ts, label: xLabel(ts, step) });
  }
  return ticks;
}

function xLabel(ts, step) {
  const date = new Date(ts * 1000);
  if (step >= DAY) return date.toLocaleDateString('fr-FR', { day: 'numeric', month: 'short' });
  if (date.getHours() === 0 && date.getMinutes() === 0) {
    return date.toLocaleDateString('fr-FR', { weekday: 'short', day: 'numeric' });
  }
  return step < HOUR ? clock(ts) : `${date.getHours()} h`;
}

function nearestIndex(points, ts, timeOf = (point) => point[0]) {
  let low = 0;
  let high = points.length - 1;
  while (low < high) {
    const middle = (low + high) >> 1;
    if (timeOf(points[middle]) < ts) low = middle + 1;
    else high = middle;
  }
  if (low > 0 && Math.abs(timeOf(points[low - 1]) - ts) <= Math.abs(timeOf(points[low]) - ts)) return low - 1;
  return low;
}

function splitOnGaps(points, gap) {
  const segments = [];
  let current = [];
  for (const point of points) {
    if (current.length && point[0] - current[current.length - 1][0] > gap) {
      segments.push(current);
      current = [];
    }
    current.push(point);
  }
  if (current.length) segments.push(current);
  return segments;
}

function hasMeasures(station, metric) {
  return station.series[metric.key].some((value) => value !== null);
}

function isEstimated(station, metric) {
  return Boolean(metric.estimatedTitle) && !hasMeasures(station, metric) && station.wind_estimate.ts.length > 0;
}

function displayMetric(data, metric) {
  if (metric.estimatedTitle) {
    const isMeasured = data.stations.some((station) => hasMeasures(station, metric));
    return isMeasured ? metric : { ...metric, title: metric.estimatedTitle };
  }
  if (metric.key !== 'rain_60min') return metric;
  const span = data.until - data.since;
  if (span < RAIN_BARS_FROM || !data.stations.some((station) => station.rain_days.length)) return metric;
  const bars = span > RAIN_MONTHS_FROM ? 'month' : 'day';
  const title = bars === 'month' ? 'Pluie par mois' : 'Pluie par jour';
  return { ...metric, title, bars, minSpan: RAIN_MIN_SPAN };
}

// A point is [time, value, low, high, start, end]; only bars have a start and an end.
function rainPoints(station, bars) {
  const totals = new Map();
  for (const [day, mm] of station.rain_days) {
    const key = bars === 'month' ? day.slice(0, 7) : day;
    totals.set(key, (totals.get(key) || 0) + mm);
  }
  return [...totals].map(([key, total]) => {
    const [year, month, day] = key.split('-').map(Number);
    const start = new Date(year, month - 1, day || 1);
    const end = bars === 'month' ? new Date(year, month, 1) : new Date(year, month - 1, day + 1);
    const [from, to] = [start.getTime() / 1000, end.getTime() / 1000];
    return [(from + to) / 2, total, null, null, from, to];
  });
}

function linePoints(station, metric, withRange) {
  const series = isEstimated(station, metric) ? station.wind_estimate : station.series;
  const values = series[metric.key];
  const lows = series[`${metric.key}_min`];
  const highs = series[`${metric.key}_max`];
  return series.ts
    .map((ts, i) => [ts, values[i], withRange && lows ? lows[i] : null, withRange && highs ? highs[i] : null])
    .filter((point) => point[1] !== null);
}

function chartSeries(data, metric) {
  const list = [];
  for (const station of data.stations) {
    const points = metric.bars ? rainPoints(station, metric.bars) : linePoints(station, metric, data.daily);
    const name = shortName(station, view.data.stations);
    if (points.length) list.push({ id: station.id, name, index: station.slot, points });
  }
  const estimated = data.stations.filter((station) => isEstimated(station, metric)).map((station) => station.id);
  if (estimated.length && estimated.length < list.length) {
    for (const item of list) {
      if (estimated.includes(item.id)) item.name = `${item.name} (estimé)`;
    }
  }
  return list;
}

function lastYearSeries(item, metric) {
  const { compare } = view;
  const station = compare && compare.stations.find((candidate) => candidate.id === item.id);
  if (!station) return null;
  const [series] = chartSeries({ ...compare, stations: [station] }, metric);
  if (!series) return null;
  const move = (value) => (value === undefined ? value : value + compare.shift);
  const points = series.points.map(([ts, value, low, high, start, end]) => (
    [move(ts), value, null, null, move(start), move(end)]
  ));
  return { ...series, name: `${item.name} (an dernier)`, index: GHOST_SLOTS[item.index], ghost: true, points };
}

function withLastYear(series, metric) {
  return series.flatMap((item) => {
    const ghost = lastYearSeries(item, metric);
    return ghost ? [item, ghost] : [item];
  });
}

function seriesTotal(item) {
  return item.points.reduce((sum, point) => sum + point[1], 0);
}

function placeEndLabels(labels) {
  labels.sort((a, b) => a.y - b.y);
  for (let i = 1; i < labels.length; i += 1) {
    labels[i].y = Math.max(labels[i].y, labels[i - 1].y + 15);
  }
}

class Chart {
  constructor(metric, series, data) {
    this.metric = metric;
    this.series = series;
    this.daily = data.daily;
    this.height = view.expanded.has(metric.key) ? HEIGHT_LARGE : HEIGHT;
    const barLength = metric.bars === 'month' ? 31 * DAY : DAY;
    this.gap = metric.bars ? barLength * 3 : Math.max(data.poll_seconds * 3, data.bucket * 3, HISTORY_GAP);
    this.start = data.since;
    this.end = data.until;
    this.times = [...new Set(series.flatMap((item) => item.points.map((point) => point[0])))].sort((a, b) => a - b);
    this.timePoints = this.times.map((time) => [time]);

    const isLarge = view.expanded.has(metric.key);
    this.card = el('section', isLarge ? 'card chart wide' : 'card chart');
    const head = el('div', 'chart-head');
    const title = el('h3', '', `${metric.title} `);
    title.append(el('span', '', metric.unit));
    head.append(title);
    const legend = el('div', 'legend');
    for (const item of series) {
      const entry = el('span', `series-${item.index}`);
      const total = metric.bars ? ` : ${withUnit(seriesTotal(item), metric)}` : '';
      entry.append(el('i', item.ghost ? 'key ghost' : 'key'), `${item.name}${total}`);
      legend.append(entry);
    }
    const expand = el('button', 'expand', isLarge ? 'Réduire' : 'Agrandir');
    expand.type = 'button';
    expand.setAttribute('aria-label', `${isLarge ? 'Réduire' : 'Agrandir'} le graphique ${metric.title}`);
    expand.addEventListener('click', () => {
      if (isLarge) view.expanded.delete(metric.key);
      else view.expanded.add(metric.key);
      renderCharts(shownData());
    });
    const tools = el('div', 'chart-tools');
    tools.append(legend);
    if (view.range) { // right on the chart, so that nobody has to scroll back up to the toolbar
      const back = el('button', 'expand', '↩ Dézoomer');
      back.type = 'button';
      back.title = 'Revenir à la période choisie';
      back.addEventListener('click', zoomReset);
      tools.append(back);
    }
    tools.append(expand);
    head.append(tools);
    this.plot = el('div', 'plot');
    this.card.append(head, this.plot);
  }

  x(ts) {
    return MARGIN.left + ((ts - this.start) / (this.end - this.start)) * this.innerWidth;
  }

  y(value) {
    const { low, high } = this.domain;
    return MARGIN.top + (1 - (value - low) / (high - low)) * this.innerHeight;
  }

  draw() {
    const width = Math.max(280, this.plot.clientWidth);
    this.innerWidth = width - MARGIN.left - MARGIN.right;
    this.innerHeight = this.height - MARGIN.top - MARGIN.bottom;
    const values = this.series.flatMap((item) => item.points.flatMap((point) => point.slice(1, 4)));
    this.domain = yDomain(values.filter((value) => value !== null), this.metric);

    const root = svg('svg', {
      viewBox: `0 0 ${width} ${this.height}`,
      height: this.height,
      role: 'img',
      tabindex: '0',
      'aria-label': `${this.metric.title} en ${this.metric.unit}. Flèches gauche et droite pour parcourir les mesures, Entrée pour fixer un moment.`,
    });

    for (const value of this.domain.ticks) {
      const y = this.y(value);
      root.append(
        svg('line', { class: value === this.domain.low ? 'base-line' : 'grid-line', x1: MARGIN.left, x2: MARGIN.left + this.innerWidth, y1: y, y2: y }),
        svg('text', { class: 'tick', x: MARGIN.left - 8, y: y + 4, 'text-anchor': 'end' }, number(value, Number.isInteger(value) ? 0 : 1)),
      );
    }
    for (const tick of xTicks(this.start, this.end, this.innerWidth)) {
      root.append(svg('text', { class: 'tick', x: this.x(tick.ts), y: this.height - 7, 'text-anchor': 'middle' }, tick.label));
    }

    if (this.metric.bars) this.drawBars(root);
    else this.drawLines(root);

    this.cross = svg('line', { class: 'cross', y1: MARGIN.top, y2: MARGIN.top + this.innerHeight, visibility: 'hidden' });
    root.append(this.cross);
    this.hoverDots = this.series.map((item) => {
      const dot = svg('circle', { class: `dot series-${item.index}`, r: 5, visibility: 'hidden' });
      root.append(dot);
      return dot;
    });

    this.band = svg('rect', { class: 'band', y: MARGIN.top, height: this.innerHeight, visibility: 'hidden' });
    root.append(this.band);
    this.dragStart = null;
    const hit = svg('rect', { class: 'hit', x: MARGIN.left, y: 0, width: this.innerWidth, height: this.height - MARGIN.bottom });
    // The mouse uses the area between the axes; fingers can land anywhere on the drawing.
    const isMouse = (event) => event.pointerType !== 'touch';
    hit.addEventListener('pointermove', (event) => {
      if (isMouse(event)) this.pointer(event);
    });
    hit.addEventListener('pointerdown', (event) => {
      if (!isMouse(event)) return;
      this.pointer(event);
      if (event.button !== 0) return;
      hit.setPointerCapture(event.pointerId);
      this.dragStart = this.plotX(event);
    });
    hit.addEventListener('mousedown', (event) => event.preventDefault()); // no focus frame on click
    hit.addEventListener('pointerup', (event) => {
      if (isMouse(event)) this.endDrag(event);
    });
    hit.addEventListener('pointercancel', (event) => {
      if (isMouse(event)) this.cancelDrag();
    });
    hit.addEventListener('pointerleave', (event) => {
      if (isMouse(event)) setHover(null);
    });
    hit.addEventListener('dblclick', () => {
      if (Date.now() - lastTouch > TAP_MEMORY) zoomOut(); // a double tap is two taps, not a zoom out
    });
    root.append(hit);
    this.touches = new Map(); // finger id -> x in the chart, for reading values and pinching
    this.pinch = null;
    this.tap = null;
    root.addEventListener('pointerdown', (event) => {
      if (isMouse(event)) return;
      lastTouch = Date.now();
      this.touchStart(event, root);
    });
    root.addEventListener('pointermove', (event) => {
      if (!isMouse(event)) this.touchMove(event, root);
    });
    root.addEventListener('pointerup', (event) => {
      if (!isMouse(event)) this.touchEnd(event, root);
    });
    root.addEventListener('pointercancel', (event) => {
      if (!isMouse(event)) this.touchCancel(root);
    });
    root.addEventListener('keydown', (event) => this.key(event));
    root.addEventListener('blur', () => setHover(null));

    this.tip = el('div', 'tip');
    this.tip.hidden = true;
    this.plot.replaceChildren(root, this.tip);
  }

  drawLines(root) {
    const labels = [];
    for (const item of this.series) {
      const group = svg('g', { class: `series-${item.index}` });
      for (const segment of splitOnGaps(item.points, this.gap)) {
        const ranged = segment.filter((point) => point[2] !== null && point[3] !== null);
        if (!item.ghost && ranged.length > 1) {
          const upper = ranged.map((point) => `${this.x(point[0]).toFixed(1)},${this.y(point[3]).toFixed(1)}`);
          const lower = ranged.map((point) => `${this.x(point[0]).toFixed(1)},${this.y(point[2]).toFixed(1)}`);
          group.append(svg('polygon', { class: 'range', points: [...upper, ...lower.reverse()].join(' ') }));
        }
        if (segment.length === 1) {
          group.append(svg('circle', { class: 'dot', r: 3, cx: this.x(segment[0][0]), cy: this.y(segment[0][1]) }));
        } else {
          const points = segment.map(([ts, value]) => `${this.x(ts).toFixed(1)},${this.y(value).toFixed(1)}`).join(' ');
          group.append(svg('polyline', { class: item.ghost ? 'line ghost' : 'line', points }));
        }
      }
      root.append(group);
      if (item.ghost) continue;
      const last = item.points[item.points.length - 1];
      group.append(svg('circle', { class: 'dot', r: 4, cx: this.x(last[0]), cy: this.y(last[1]) }));
      labels.push({ y: this.y(last[1]) + 4, text: number(last[1], this.metric.decimals) });
    }
    placeEndLabels(labels);
    for (const label of labels.length <= MAX_END_LABELS ? labels : []) {
      root.append(svg('text', { class: 'end-label', x: MARGIN.left + this.innerWidth + 9, y: label.y }, label.text));
    }
  }

  drawBars(root) {
    const left = MARGIN.left;
    const right = MARGIN.left + this.innerWidth;
    const base = this.y(this.domain.low);
    this.series.forEach((item, position) => {
      const group = svg('g', { class: `series-${item.index}` });
      for (const [, value, , , start, end] of item.points) {
        if (value <= 0) continue;
        const from = Math.max(left, this.x(start));
        const to = Math.min(right, this.x(end));
        const width = Math.max(1, (to - from - BAR_GAP) / this.series.length);
        const y = this.y(value);
        group.append(svg('rect', {
          class: item.ghost ? 'bar ghost' : 'bar',
          x: (from + BAR_GAP / 2 + position * width).toFixed(1),
          y: y.toFixed(1),
          width: Math.max(1, width - 1).toFixed(1),
          height: Math.max(1, base - y).toFixed(1),
          rx: Math.min(2, width / 2).toFixed(1),
        }));
      }
      root.append(group);
    });
  }

  timeLabel(ts) {
    if (this.metric.bars === 'month') return monthLabel(ts);
    return this.metric.bars || this.daily ? dayLabel(ts) : dateTime(ts);
  }

  // Measured on the chart's box, which the pinch preview leaves as it is.
  chartX(event) {
    return event.clientX - this.plot.getBoundingClientRect().left;
  }

  clamp(x) {
    return Math.min(MARGIN.left + this.innerWidth, Math.max(MARGIN.left, x));
  }

  plotX(event) {
    return this.clamp(this.chartX(event));
  }

  timeAt(x) {
    return this.start + ((x - MARGIN.left) / this.innerWidth) * (this.end - this.start);
  }

  pointer(event) {
    const x = this.plotX(event);
    if (this.dragStart !== null) {
      this.band.setAttribute('x', Math.min(x, this.dragStart));
      this.band.setAttribute('width', Math.abs(x - this.dragStart));
      this.band.setAttribute('visibility', 'visible');
    }
    setHover(this.times[nearestIndex(this.timePoints, this.timeAt(x))], this);
  }

  cancelDrag() {
    this.dragStart = null;
    this.band.setAttribute('visibility', 'hidden');
  }

  endDrag(event) {
    const start = this.dragStart;
    this.cancelDrag();
    if (start === null) return;
    const end = this.plotX(event);
    if (Math.abs(end - start) < MIN_DRAG) {
      togglePin(this.times[nearestIndex(this.timePoints, this.timeAt(end))]);
      return;
    }
    zoomTo(this.timeAt(Math.min(start, end)), this.timeAt(Math.max(start, end)));
  }

  // On a touch screen, one finger reads the values and a tap fixes a moment;
  // two fingers pinch to zoom in or out, like on a photo.
  touchStart(event, root) { // a touch screen already keeps each finger on the chart it started on
    if (this.touches.size >= 2) return; // a third finger changes nothing
    this.touches.set(event.pointerId, this.chartX(event));
    if (this.touches.size === 2) {
      const [a, b] = [...this.touches.values()];
      const gap = Math.max(MIN_DRAG, Math.abs(a - b));
      this.pinch = { from: gap, to: gap, center: this.clamp((a + b) / 2) };
      this.tap = null;
      setHover(null);
      root.style.transformOrigin = `${this.pinch.center}px 50%`;
    } else {
      this.tap = { x: this.plotX(event), moved: false };
      this.pointer(event);
    }
  }

  touchMove(event, root) {
    if (!this.touches.has(event.pointerId)) return;
    this.touches.set(event.pointerId, this.chartX(event));
    if (this.pinch) {
      const [a, b] = [...this.touches.values()];
      this.pinch.to = Math.max(1, Math.abs(a - b));
      const scale = Math.min(PINCH_MAX, Math.max(1 / PINCH_MAX, this.pinch.to / this.pinch.from));
      root.style.transform = `scaleX(${scale})`; // a preview until the fingers lift
      return;
    }
    if (this.tap && Math.abs(this.plotX(event) - this.tap.x) >= MIN_DRAG) this.tap.moved = true;
    this.pointer(event);
  }

  touchEnd(event, root) {
    if (!this.touches.has(event.pointerId)) return;
    this.touches.delete(event.pointerId);
    if (this.pinch) {
      this.endPinch(root); // the first finger up ends it; the other one is then ignored
      return;
    }
    const tap = this.tap;
    this.tap = null;
    if (tap && !tap.moved) togglePin(this.times[nearestIndex(this.timePoints, this.timeAt(tap.x))]);
    else setHover(null);
  }

  endPinch(root) {
    const { from, to, center } = this.pinch;
    this.touchCancel(root);
    const ratio = to / from;
    if (ratio >= PINCH_STEP) {
      const middle = this.timeAt(center);
      const span = (this.end - this.start) / Math.min(ratio, PINCH_MAX);
      zoomTo(middle - span / 2, middle + span / 2);
    } else if (ratio <= 1 / PINCH_STEP) {
      zoomOut();
    }
  }

  touchCancel(root) {
    this.touches.clear();
    this.pinch = null;
    this.tap = null;
    root.style.transform = '';
    setHover(null);
  }

  key(event) {
    if (event.key === 'Enter' && this.hovered !== null && this.hovered !== undefined) {
      togglePin(this.hovered);
      return;
    }
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    event.preventDefault();
    const current = this.hovered === null || this.hovered === undefined
      ? this.times.length
      : this.times.indexOf(this.hovered);
    const next = current + (event.key === 'ArrowLeft' ? -1 : 1);
    setHover(this.times[Math.min(this.times.length - 1, Math.max(0, next))], this);
  }

  showHover(ts, active) {
    this.hovered = ts;
    const visible = ts !== null;
    this.cross.setAttribute('visibility', visible ? 'visible' : 'hidden');
    this.tip.hidden = !(visible && active);
    if (!visible) {
      this.hoverDots.forEach((dot) => dot.setAttribute('visibility', 'hidden'));
      return;
    }
    const x = this.x(ts);
    this.cross.setAttribute('x1', x);
    this.cross.setAttribute('x2', x);

    const rows = [];
    this.series.forEach((item, i) => {
      const point = item.points[nearestIndex(item.points, ts)];
      const near = Math.abs(point[0] - ts) <= this.gap / 3;
      const dot = this.hoverDots[i];
      dot.setAttribute('visibility', near ? 'visible' : 'hidden');
      if (near) {
        dot.setAttribute('cx', this.x(point[0]));
        dot.setAttribute('cy', this.y(point[1]));
      }
      const row = el('div', `series-${item.index}`);
      const hasRange = near && point[2] !== null && point[2] !== undefined && point[3] !== null;
      const detail = hasRange
        ? `${item.name} · ${number(point[2], this.metric.decimals)} à ${number(point[3], this.metric.decimals)}`
        : item.name;
      row.append(el('i', item.ghost ? 'key ghost' : 'key'), el('b', '', near ? withUnit(point[1], this.metric) : '–'), el('small', '', detail));
      rows.push(row);
    });
    if (active) {
      const time = el('time', '', this.timeLabel(ts));
      this.tip.replaceChildren(time, ...rows);
      const width = this.tip.offsetWidth;
      const left = x + 14 + width > this.plot.clientWidth ? x - width - 14 : x + 14;
      this.tip.style.left = `${Math.max(0, left)}px`;
    }
  }
}

// Without a pointer on a chart, every chart shows the moment fixed by a click, if any.
function setHover(ts, active) {
  const isHovering = ts !== null && ts !== undefined;
  const shown = isHovering ? ts : view.pinned;
  for (const chart of view.charts) chart.showHover(shown, isHovering ? chart === active : shown !== null);
}

function renderCharts(data) {
  const box = document.getElementById('charts');
  const charts = METRICS
    .filter((metric) => !view.hiddenMetrics.has(metric.key))
    .map((metric) => displayMetric(data, metric))
    .map((metric) => ({ metric, series: withLastYear(chartSeries(data, metric), metric) }))
    .filter(({ series }) => series.length)
    .map(({ metric, series }) => new Chart(metric, series, data));
  if (charts.length) {
    box.replaceChildren(...charts.map((chart) => chart.card));
    charts.forEach((chart) => chart.draw());
  } else {
    box.replaceChildren(el('p', 'card empty', view.range
      ? 'Aucune mesure sur cette période.'
      : 'Les courbes apparaîtront après les premières collectes.'));
  }
  view.charts = charts; // only now: removing a focused chart above triggers setHover
  setHover(null);
}

function renderTable(data) {
  const rows = [];
  for (const station of data.stations) {
    station.series.ts.forEach((ts, i) => rows.push({ ts, station, i }));
  }
  rows.sort((a, b) => b.ts - a.ts);
  for (const header of document.querySelectorAll('th[data-daily]')) header.hidden = !data.daily;
  const isAlone = data.stations.length === 1;
  document.getElementById('station-column').hidden = isAlone;
  document.getElementById('table-title').textContent = isAlone
    ? `Voir les valeurs de ${data.stations[0].name} en tableau`
    : 'Voir les valeurs de toutes les stations en tableau';
  const cell = (value, decimals) => el('td', '', value === null ? '–' : number(value, decimals));
  document.getElementById('rows').replaceChildren(...rows.slice(0, MAX_TABLE_ROWS).map(({ ts, station, i }) => {
    const row = el('tr');
    const series = station.series;
    const extremes = data.daily ? [cell(series.temperature_min[i], 1), cell(series.temperature_max[i], 1)] : [];
    const estimate = series.wind_strength[i] === null ? estimateAt(station, ts, data) : null;
    row.append(
      el('td', 'left', data.daily ? dayLabel(ts) : dateTime(ts)),
      ...(isAlone ? [] : [el('td', 'left', station.name)]),
      cell(series.temperature[i], 1),
      ...extremes,
      cell(series.humidity[i], 0),
      cell(series.pressure[i], 1),
      cell(series.rain_60min[i], 1),
      cell(estimate ? estimate.wind_strength : series.wind_strength[i], 0),
      cell(estimate ? estimate.gust_strength : series.gust_strength[i], 0),
    );
    return row;
  }));
}

/* ---------- Refresh loop ---------- */

function contentKey(data) {
  return JSON.stringify([
    view.hours,
    view.range,
    view.dailyOn,
    view.compareOn,
    data.setup,
    data.status.error,
    data.status.stations,
    data.status.history,
    data.status.history_rows,
    data.status.wind,
    data.status.forecast,
    data.status.alerts_error,
    data.status.notify_error,
    data.announcement,
    data.neighbours_allowed,
    view.hoursOn,
    view.forecastOn,
    view.alertsOn,
    view.barometerOn,
    view.statsOn,
    view.nearbyOn,
    view.skyOn,
    view.radius,
    data.stations.map((station) => [
      station.id,
      station.name,
      station.alerts,
      station.neighbours,
      station.barometer,
      station.forecast && station.forecast.fetched,
      station.latest && station.latest.ts,
      station.wind_latest && station.wind_latest.ts,
    ]),
  ]);
}

function shownData() {
  return { ...view.data, stations: shownStations(view.data) };
}

async function changePassword(event) {
  event.preventDefault();
  const form = event.target;
  const message = document.getElementById('password-message');
  const button = form.querySelector('button');
  button.disabled = true;
  message.textContent = '';
  try {
    const response = await fetch('/api/password', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    if (response.ok) {
      form.reset();
      message.textContent = 'Mot de passe changé.';
    } else {
      message.textContent = (await response.json()).error;
    }
  } catch {
    message.textContent = 'Serveur injoignable, réessayez.';
  } finally {
    button.disabled = false;
  }
}

/* ---------- Notifications ---------- */

let notifySettings = null;
const IPHONE = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);

function notifyRow(event, settings) {
  const row = el('div', 'notify-event');
  const check = el('label', 'check');
  const box = el('input');
  box.type = 'checkbox';
  box.name = 'event';
  box.value = event.key;
  box.checked = settings.events.includes(event.key);
  check.append(box, ` ${event.label}`);
  row.append(check);
  if (event.limits) {
    const threshold = el('label', 'threshold');
    const input = el('input');
    input.type = 'number';
    input.name = `threshold-${event.key}`;
    [input.min, input.max] = event.limits;
    input.step = event.key === 'offline' ? '1' : '0.5';
    input.value = settings.thresholds[event.key];
    const word = { frost: 'à', heat: 'dès', rain: 'dès', wind: 'dès', offline: 'après' }[event.key];
    threshold.append(word, input, event.unit);
    row.append(threshold);
  }
  return row;
}

function pushSupported() {
  return 'serviceWorker' in navigator && 'PushManager' in window && 'Notification' in window;
}

function keyBytes(text) {
  const raw = atob(text.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - (text.length % 4)) % 4));
  return Uint8Array.from(raw, (c) => c.charCodeAt(0));
}

async function currentSubscription() {
  if (!pushSupported()) return null;
  const registration = await navigator.serviceWorker.ready;
  return registration.pushManager.getSubscription();
}

async function renderPushState() {
  const state = document.getElementById('push-state');
  const button = document.getElementById('push-toggle');
  const devices = notifySettings ? notifySettings.devices : 0;
  const others = devices ? ` Activées sur ${devices} appareil${devices > 1 ? 's' : ''} en tout.` : '';
  document.getElementById('push-iphone').hidden = !IPHONE;
  if (!pushSupported()) {
    state.textContent = IPHONE
      ? 'Ouvrez le site depuis son icône sur l’écran d’accueil pour activer les notifications.'
      : 'Ce navigateur ne reçoit pas les notifications.';
    button.hidden = true;
    return;
  }
  button.hidden = false;
  let subscription = null;
  try {
    subscription = await currentSubscription();
  } catch { /* the state stays unknown: the button still works */ }
  if (Notification.permission === 'denied') {
    state.textContent = `Notifications bloquées pour ce site dans les réglages du navigateur.${others}`;
    button.hidden = true;
  } else if (subscription) {
    state.textContent = `Activées sur cet appareil.${others}`;
    button.textContent = 'Désactiver sur cet appareil';
  } else {
    state.textContent = `Pas encore activées sur cet appareil.${others}`;
    button.textContent = 'Activer sur cet appareil';
  }
}

async function postJson(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const answer = await response.json();
  if (!response.ok) throw new Error(answer.error);
  return answer;
}

async function togglePush() {
  const message = document.getElementById('notify-message');
  const button = document.getElementById('push-toggle');
  button.disabled = true;
  message.textContent = '';
  try {
    const existing = await currentSubscription();
    if (existing) {
      await postJson('/api/push/unsubscribe', { endpoint: existing.endpoint });
      await existing.unsubscribe();
      notifySettings.devices = Math.max(0, notifySettings.devices - 1);
      message.textContent = 'Notifications désactivées sur cet appareil.';
    } else {
      if (await Notification.requestPermission() !== 'granted') throw new Error('Autorisation refusée : rien n’a été activé.');
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true, applicationServerKey: keyBytes(notifySettings.push_key),
      });
      try {
        notifySettings.devices = (await postJson('/api/push/subscribe', { endpoint: subscription.endpoint })).devices;
      } catch (error) {
        await subscription.unsubscribe();
        throw error;
      }
      message.textContent = 'Notifications activées sur cet appareil. « Envoyer un test » pour vérifier.';
    }
  } catch (error) {
    message.textContent = error.message || 'Activation impossible, réessayez.';
  } finally {
    button.disabled = false;
    renderPushState();
  }
}

function renderMailbox(settings) {
  const form = document.getElementById('mailbox-form');
  const box = settings.mailbox;
  form.hidden = !box.may_change;
  if (!box.may_change) return;
  document.getElementById('mailbox-state').textContent = box.address
    ? `Les e-mails partent de ${box.address}.`
    : 'Aucune boîte d’envoi pour l’instant : suivez ces étapes une fois.';
  document.getElementById('mailbox-remove').hidden = !box.from_page;
}

function showNotifySettings(settings) {
  notifySettings = settings;
  const form = document.getElementById('notify-form');
  form.elements.email.checked = settings.email;
  form.elements.email.disabled = !settings.email_available;
  form.elements.address.value = settings.address;
  form.querySelector('.notify-address').hidden = !settings.email_available;
  document.getElementById('email-off').hidden = settings.email_available || settings.mailbox.may_change;
  document.getElementById('notify-events').replaceChildren(...settings.events_known.map((event) => notifyRow(event, settings)));
  renderMailbox(settings);
  renderPushState();
}

async function loadNotifications() {
  const message = document.getElementById('notify-message');
  try {
    const response = await fetch('/api/notifications');
    if (!response.ok) throw new Error();
    showNotifySettings(await response.json());
    message.textContent = '';
  } catch {
    message.textContent = 'Serveur injoignable, réessayez.';
  }
}

async function saveNotifications(event) {
  event.preventDefault();
  const form = event.target;
  const message = document.getElementById('notify-message');
  const thresholds = {};
  for (const input of form.querySelectorAll('input[type="number"]')) {
    thresholds[input.name.replace('threshold-', '')] = Number(input.value);
  }
  const body = {
    email: form.elements.email.checked,
    address: form.elements.address.value.trim(),
    events: [...form.querySelectorAll('input[name="event"]:checked')].map((box) => box.value),
    thresholds,
  };
  try {
    const response = await fetch('/api/notifications', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const answer = await response.json();
    if (!response.ok) throw new Error(answer.error);
    showNotifySettings({ ...notifySettings, ...answer });
    message.textContent = 'Enregistré.';
  } catch (error) {
    message.textContent = error.message || 'Serveur injoignable, réessayez.';
  }
}

async function testNotifications() {
  const message = document.getElementById('notify-message');
  const button = document.getElementById('notify-test');
  button.disabled = true;
  message.textContent = 'Envoi…';
  try {
    const answer = await postJson('/api/notifications/test', {});
    const names = { push: 'appareils', email: 'e-mail' };
    message.textContent = Object.entries(answer.results)
      .map(([channel, error]) => `${names[channel]} : ${error || 'envoyé'}`).join(' · ');
  } catch (error) {
    message.textContent = error.message || 'Serveur injoignable, réessayez.';
  } finally {
    button.disabled = false;
  }
}

async function saveMailbox(event) {
  event.preventDefault();
  const form = event.target;
  const message = document.getElementById('mailbox-message');
  const button = form.querySelector('button[type="submit"]');
  button.disabled = true;
  message.textContent = 'Connexion à Gmail…';
  try {
    await postJson('/api/mailbox', { user: form.elements.user.value.trim(), password: form.elements.password.value });
    form.reset();
    message.textContent = 'Boîte enregistrée : cochez « Aussi par e-mail » plus haut, puis faites un test.';
    await loadNotifications();
  } catch (error) {
    message.textContent = error.message || 'Serveur injoignable, réessayez.';
  } finally {
    button.disabled = false;
  }
}

async function removeMailbox() {
  if (!confirm('Retirer cette boîte d’envoi ? Plus aucun e-mail ne partira.')) return;
  try {
    await postJson('/api/mailbox/remove', {});
    document.getElementById('mailbox-message').textContent = 'Boîte retirée.';
    await loadNotifications();
  } catch (error) {
    document.getElementById('mailbox-message').textContent = error.message || 'Serveur injoignable, réessayez.';
  }
}

async function copyInviteLink() {
  const code = document.getElementById('my-code').textContent;
  const link = `${location.origin}/login?code=${encodeURIComponent(code)}`;
  const message = document.getElementById('invite-message');
  try {
    await navigator.clipboard.writeText(link);
    message.textContent = 'Lien copié : il ne servira qu’à une seule personne.';
  } catch {
    message.replaceChildren('Copiez ce lien : ', el('code', 'admin-password', link));
  }
}

function showInvite(code) {
  document.getElementById('my-code').textContent = code || '';
  document.getElementById('invite-body').hidden = !code;
  document.getElementById('invite-message').textContent = code ? '' : 'Les invitations sont fermées pour le moment.';
}

async function loadInvite() {
  try {
    const response = await fetch('/api/invite');
    showInvite((await response.json()).code);
  } catch {
    document.getElementById('invite-message').textContent = 'Serveur injoignable, réessayez.';
  }
}

async function renewInvite() {
  if (!confirm('Créer un nouveau code ? L’ancien ne marchera plus.')) return;
  try {
    const response = await fetch('/api/invite', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const answer = await response.json();
    if (!response.ok) throw new Error(answer.error);
    showInvite(answer.code);
    document.getElementById('invite-message').textContent = 'Nouveau code créé : l’ancien ne marche plus.';
  } catch (error) {
    document.getElementById('invite-message').textContent = error.message || 'Serveur injoignable, réessayez.';
  }
}

async function logout() {
  await fetch('/api/logout', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: '{}',
  });
  forgetSettings();
  location.href = '/login';
}

function toggleUserList(force) {
  const list = document.getElementById('user-list');
  list.hidden = force === undefined ? !list.hidden : !force;
  document.getElementById('user-open').setAttribute('aria-expanded', String(!list.hidden));
}

function openPanel(id) {
  const panel = document.getElementById(id);
  panel.open = true;
  panel.scrollIntoView({ block: 'center' });
  toggleUserList(false);
}

function renderContent() {
  const data = view.data;
  const shown = shownData();
  document.getElementById('user-open').textContent = data.mode === 'local' ? 'Menu ▾' : `${data.user} ▾`;
  document.getElementById('admin-open').hidden = !data.is_admin;
  document.getElementById('invite-open').hidden = !(data.invites_open || data.is_admin);
  if (view.pinned !== null && (view.pinned < data.since || view.pinned > data.until)) view.pinned = null;
  renderPeriods();
  renderNotices(data);
  renderRange(data);
  renderOptions(data);
  renderFilters(data);
  renderPin();
  renderSky();
  renderStations(shown);
  renderForecast(shown);
  renderStats(shown);
  renderCharts(shown);
  renderTable(shown);
}

function renderClock() {
  document.getElementById('clock').textContent = new Date().toLocaleString('fr-FR', {
    weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit',
  });
  if (view.data) renderSky(); // the moon moves on, and the day changes at midnight
  renderBrand();
}

function renderThemeList() {
  const current = savedTheme();
  document.getElementById('theme-list').replaceChildren(...THEMES.map(([name, label, swatch]) => {
    const button = el('button');
    button.type = 'button';
    button.setAttribute('aria-pressed', String(name === current));
    const dot = el('i');
    dot.style.background = swatch;
    button.append(dot, label);
    button.addEventListener('click', () => {
      applyTheme(name);
      saveTheme(name);
      renderThemeList();
    });
    return button;
  }));
}

function toggleThemeList(force) {
  const list = document.getElementById('theme-list');
  list.hidden = force === undefined ? !list.hidden : !force;
  document.getElementById('theme-open').setAttribute('aria-expanded', String(!list.hidden));
}

async function fetchData(query) {
  const daily = view.dailyOn ? '&daily=1' : '';
  const response = await fetch(`/api/data?${query}${daily}`);
  if (response.status === 401) location.href = '/login';
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

async function fetchLastYear(data) {
  if (!view.compareOn || !compareAllowed(data)) return null;
  const date = new Date(data.until * 1000);
  date.setFullYear(date.getFullYear() - 1);
  const shift = data.until - Math.round(date.getTime() / 1000);
  const compare = await fetchData(`from=${data.since - shift}&to=${data.until - shift}`);
  return { ...compare, shift };
}

function dataQuery() {
  const period = view.range ? `from=${view.range.from}&to=${view.range.to}` : `hours=${view.hours}`;
  return view.nearbyOn ? `${period}&radius=${view.radius}` : period;
}

async function refresh(showBusy) {
  const request = ++view.request;
  const query = dataQuery();
  if (showBusy) document.body.classList.add('refreshing');
  try {
    const data = await fetchData(query);
    if (request !== view.request) return; // a newer request has replaced this one
    const key = contentKey(data);
    if (key !== view.renderedKey) {
      const compare = await fetchLastYear(data);
      if (request !== view.request) return;
      view.compare = compare;
      view.data = data;
      view.renderedKey = key;
      renderContent();
    } else {
      view.data = data;
    }
    renderStatus();
    renderScreen(); // every time: the screen shows how long ago the measure was taken
  } catch {
    document.getElementById('status').textContent = 'Serveur injoignable pour le moment';
  } finally {
    if (request === view.request) document.body.classList.remove('refreshing');
  }
}

let resizeTimer;
window.addEventListener('resize', () => {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    view.charts.forEach((chart) => chart.draw());
    setHover(null);
  }, 150);
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape' && view.fullscreenId) closeScreen();
  else if (event.key === 'Escape' && view.pinned !== null) setPin(null);
});
let screenWasFull = false;
document.addEventListener('fullscreenchange', () => {
  // Leaving the browser's full screen (Échap, back gesture) also closes the station view.
  if (document.fullscreenElement) screenWasFull = true;
  else if (screenWasFull && view.fullscreenId) closeScreen();
  if (!document.fullscreenElement) screenWasFull = false;
});
document.addEventListener('visibilitychange', () => {
  // The browser drops the screen lock whenever the page is hidden.
  if (document.visibilityState === 'visible' && view.fullscreenId) keepScreenOn(true);
});
view.fullscreenId = new URLSearchParams(location.search).get(SCREEN_PARAM);
if (view.fullscreenId) keepScreenOn(true);

document.getElementById('theme-open').addEventListener('click', (event) => {
  event.stopPropagation();
  toggleThemeList();
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('.theme-menu')) toggleThemeList(false);
  if (!event.target.closest('.user-menu')) toggleUserList(false);
});
renderThemeList();
renderClock();
setInterval(renderClock, 15000);
document.getElementById('user-open').addEventListener('click', (event) => {
  event.stopPropagation();
  toggleUserList();
});
document.getElementById('password-open').addEventListener('click', () => openPanel('password-panel'));
document.getElementById('notify-open').addEventListener('click', () => openPanel('notify-panel'));
document.getElementById('notify-panel').addEventListener('toggle', (event) => {
  if (event.target.open) loadNotifications();
});
document.getElementById('notify-form').addEventListener('submit', saveNotifications);
document.getElementById('notify-test').addEventListener('click', testNotifications);
document.getElementById('push-toggle').addEventListener('click', togglePush);
document.getElementById('mailbox-form').addEventListener('submit', saveMailbox);
document.getElementById('mailbox-remove').addEventListener('click', removeMailbox);
document.getElementById('admin-open').addEventListener('click', () => { location.href = '/admin'; });
document.getElementById('invite-open').addEventListener('click', () => openPanel('invite-panel'));
document.getElementById('invite-panel').addEventListener('toggle', (event) => {
  if (event.target.open) loadInvite();
});
document.getElementById('invite-renew').addEventListener('click', renewInvite);
document.getElementById('logout').addEventListener('click', logout);
document.getElementById('password-form').addEventListener('submit', changePassword);
document.getElementById('invite-copy').addEventListener('click', copyInviteLink);
document.getElementById('add-form').addEventListener('submit', addStation);
document.getElementById('stats-panel').addEventListener('toggle', (event) => {
  if (event.target.open === view.statsOpen) return;
  view.statsOpen = event.target.open;
  saveFlag('meteo-stats-open', view.statsOpen);
  if (view.statsOpen) loadStats();
});
document.getElementById('forecast-panel').addEventListener('toggle', (event) => {
  if (event.target.open === view.forecastOpen || shownData().stations.length === 1) return;
  view.forecastOpen = event.target.open;
  saveFlag('meteo-forecast-open', view.forecastOpen);
});
document.getElementById('add-open').addEventListener('click', () => {
  const panel = document.getElementById('add-station');
  panel.open = !panel.open;
  if (panel.open) {
    panel.scrollIntoView({ block: 'center' });
    document.querySelector('#add-form input').focus();
  }
});
document.getElementById('date-form').addEventListener('submit', applyDates);
document.getElementById('date-from').addEventListener('input', markDatesChanged);
document.getElementById('date-to').addEventListener('input', markDatesChanged);
document.getElementById('hint').textContent = matchMedia(TOUCH_SCREEN).matches ? TOUCH_HINT : MOUSE_HINT;
document.getElementById('zoom-out').addEventListener('click', zoomReset);
document.getElementById('pan-left').addEventListener('click', () => pan(-1));
document.getElementById('pan-right').addEventListener('click', () => pan(1));
document.getElementById('pin-clear').addEventListener('click', () => setPin(null));
renderPeriods();
refresh(false);
setInterval(() => refresh(false), REFRESH_MS);
setInterval(renderStatus, 30000);

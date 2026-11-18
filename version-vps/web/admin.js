'use strict';

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function shortDate(ts) {
  return ts ? new Date(ts * 1000).toLocaleDateString('fr-FR') : 'Jamais';
}

async function adminAction(url, options, onDone) {
  const message = document.getElementById('admin-message');
  message.textContent = '';
  try {
    const response = await fetch(url, options);
    const answer = await response.json();
    if (response.ok) onDone(answer);
    else message.textContent = answer.error;
  } catch {
    message.textContent = 'Serveur injoignable, réessayez.';
  }
}

function resetUser(user) {
  if (!confirm(`Créer un mot de passe temporaire pour ${user.name} ?`)) return;
  adminAction('/api/admin/reset', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: user.id }),
  }, ({ password }) => {
    const message = document.getElementById('admin-message');
    message.replaceChildren(
      `Mot de passe temporaire de ${user.name}, affiché une seule fois : `,
      el('code', 'admin-password', password),
    );
  });
}

function deleteUser(user) {
  const stations = user.stations.length ? ` et sa liste de ${user.stations.length} station(s)` : '';
  if (!confirm(`Supprimer définitivement le compte de ${user.name}${stations} ?`)) return;
  adminAction(`/api/admin/users?id=${user.id}`, { method: 'DELETE' }, () => {
    loadUsers();
    document.getElementById('admin-message').textContent = `Compte de ${user.name} supprimé.`;
  });
}

function stationLinks(stations) {
  const cell = el('td', 'left');
  if (!stations.length) cell.append('Aucune');
  stations.forEach((station, index) => {
    const link = el('a', '', `${station.name} ↗`);
    link.href = station.url;
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    if (index) cell.append(', ');
    cell.append(link);
  });
  return cell;
}

function renewCode(user) {
  adminAction('/api/admin/code', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: user.id }),
  }, () => {
    loadUsers();
    document.getElementById('admin-message').textContent = `Nouveau code créé pour ${user.name}.`;
  });
}

function codeCell(code) {
  const cell = el('td', 'left');
  cell.append(code ? el('code', 'admin-password', code) : '–');
  return cell;
}

function setRole(user, isAdmin) {
  const question = isAdmin
    ? `Nommer ${user.name} administrateur ? Il aura accès à cette page.`
    : `Retirer à ${user.name} l’accès à l’administration ?`;
  if (!confirm(question)) return;
  post('/api/admin/role', { id: user.id, admin: isAdmin }, () => {
    loadUsers();
    say(isAdmin ? `${user.name} est maintenant administrateur.` : `${user.name} n’est plus administrateur.`);
  });
}

const ROLES = { owner: 'propriétaire', admin: 'admin', user: '' };

function userRow(user, people) {
  const card = el('article', 'user-card');
  const isMe = user.id === people.me;
  const amOwner = people.me === people.owner;
  const isProtected = user.role === 'owner' || (user.role === 'admin' && !amOwner);
  const head = el('div', 'user-head');
  head.append(el('strong', '', isMe ? `${user.name} (vous)` : user.name));
  if (ROLES[user.role]) head.append(el('span', 'role', ROLES[user.role]));
  if (user.online) head.append(el('span', 'user-online', 'en ligne'));
  const seen = el('span', 'user-seen', visitText(user.last_seen));
  seen.dataset.seen = user.last_seen || '';
  const dates = el('p', 'user-meta', `Inscrit le ${shortDate(user.created)} · dernière visite `);
  dates.append(seen);
  const code = el('p', 'user-meta');
  code.append('Code d’invitation : ', user.code ? el('code', 'admin-password', user.code) : 'pas encore créé');
  const stations = el('p', 'user-meta');
  stations.append('Stations : ', ...stationLinks(user.stations).childNodes);
  card.append(head, dates);
  if (user.invited_by) card.append(el('p', 'user-meta', `Invité par ${user.invited_by}`));
  card.append(code, stations);
  const choices = [['Nouveau code', renewCode]];
  if (!isMe && !isProtected) choices.push(['Nouveau mot de passe', resetUser], ['Supprimer', deleteUser]);
  if (amOwner && user.role === 'user') choices.push(['Nommer admin', (person) => setRole(person, true)]);
  if (amOwner && user.role === 'admin') choices.push(['Retirer admin', (person) => setRole(person, false)]);
  const actions = el('div', 'user-actions');
  for (const [label, action] of choices) {
    const button = el('button', '', label);
    button.type = 'button';
    button.addEventListener('click', () => action(user));
    actions.append(button);
  }
  card.append(actions);
  return card;
}

let clockGap = 0; // server time minus this computer's time, in seconds

function serverNow() {
  return Date.now() / 1000 + clockGap;
}

// Down to the second, then minutes, hours and days.
function visitText(ts) {
  if (!ts) return 'jamais';
  const seconds = Math.max(0, Math.round(serverNow() - ts));
  if (seconds < 60) return `il y a ${seconds} s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `il y a ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `il y a ${hours} h${minutes % 60 ? ` ${String(minutes % 60).padStart(2, '0')}` : ''}`;
  return `il y a ${Math.floor(hours / 24)} jours (le ${shortDate(ts)})`;
}

function tickVisits() {
  document.querySelectorAll('.user-seen').forEach((node) => {
    node.textContent = visitText(Number(node.dataset.seen));
  });
}

async function loadUsers() {
  await adminAction('/api/admin/users', {}, ({ users, invites_open: isOpen, me, owner, now }) => {
    clockGap = now - Date.now() / 1000;
    document.getElementById('invites-open').checked = isOpen;
    const list = document.getElementById('admin-users');
    if (list.contains(document.activeElement)) return; // a button is being used: keep the cards still
    list.replaceChildren(...users.map((user) => userRow(user, { me, owner })));
  });
}

function setInvitesOpen(event) {
  const isOpen = event.target.checked;
  adminAction('/api/admin/invites', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ open: isOpen }),
  }, () => {
    document.getElementById('admin-message').textContent = isOpen
      ? 'Tout le monde peut inviter.'
      : 'Seul votre code marche désormais.';
  });
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };
const BUSY_SHARE = 70; // % of the estimated Netatmo limit worth a warning
let overview = null;

function number(value, decimals = 0) {
  return value.toLocaleString('fr-FR', { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

function ago(seconds) {
  if (seconds < 90) return 'à l’instant';
  if (seconds < 90 * 60) return `il y a ${Math.round(seconds / 60)} min`;
  if (seconds < 48 * 3600) return `il y a ${Math.round(seconds / 3600)} h`;
  return `il y a ${Math.round(seconds / 86400)} jours`;
}

function clock(ts) {
  return new Date(ts * 1000).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

function size(bytes) {
  return bytes >= 1e9 ? `${number(bytes / 1e9, 1)} Go` : `${number(bytes / 1e6, 1)} Mo`;
}

function say(text) {
  document.getElementById('admin-message').textContent = text;
}

function post(url, body, onDone) {
  return adminAction(url, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }, onDone);
}

function tile(label, value, note) {
  const box = el('div', 'stat');
  box.append(el('span', '', label), el('b', '', value));
  if (note) box.append(el('small', '', note));
  return box;
}

function serverTiles(info) {
  const share = Math.round((100 * info.netatmo_calls) / info.netatmo_limit);
  const busy = share >= BUSY_SHARE ? 'Attention : proche de la limite. ' : '';
  return [
    tile('Version', info.version, `Démarré ${ago(info.now - info.started)}`),
    tile('Netatmo', info.setup === 'ok' ? 'Connecté' : 'Déconnecté', info.error || ''),
    tile(
      'Dernière collecte',
      info.last_poll ? ago(info.now - info.last_poll) : 'Pas encore',
      info.next_poll ? `Prochaine à ${clock(info.next_poll)}` : '',
    ),
    tile(
      'Requêtes Netatmo',
      `${number(info.netatmo_calls)} en 1 h`,
      `${busy}${share} % d’une limite estimée à ${number(info.netatmo_limit)} par heure`,
    ),
    tile('Stations suivies', `${info.stations.length} / ${info.limits.total_stations}`, 'sur tout le serveur'),
    tile('Utilisateurs', number(info.users), `${info.online} en ligne`),
    tile('Base de données', size(info.database_bytes)),
    tile('Disque libre', size(info.disk_free), `sur ${size(info.disk_total)}`),
    tile('Alertes météo', info.alerts_error ? 'Indisponibles' : 'OK', info.alerts_error || ''),
    tile('Erreurs sur 24 h', number(recentProblems(info)), 'Détail dans l’onglet Journal'),
  ];
}

function usageBar(label, count, people) {
  const row = el('div', 'usage-row');
  const bar = el('span', 'usage-bar');
  const fill = el('i');
  fill.style.width = `${people ? Math.round((100 * count) / people) : 0}%`;
  bar.append(fill);
  row.append(el('span', 'usage-label', label), bar, el('b', '', `${count} / ${people}`));
  return row;
}

function usageGroup(title, items, people) {
  const group = el('div', 'usage-group');
  group.append(el('h3', '', title), ...items.map((item) => usageBar(item.label, item.count, people)));
  return group;
}

function renderUsage(usage) {
  const options = [...usage.options].sort((a, b) => b.count - a.count);
  document.getElementById('usage').replaceChildren(
    usageGroup('Options affichées', options, usage.people),
    usageGroup('Thème', usage.themes, usage.people),
    usageGroup('Période au démarrage', usage.periods, usage.people),
  );
}

function limitsHelp() {
  const form = document.getElementById('limits-form');
  const stations = overview ? overview.stations.length : 0;
  const minutes = Number(form.elements.poll_minutes.value) || 10;
  const live = Math.round((stations * 60) / minutes);
  const full = Math.round((Number(form.elements.total_stations.value) * 60) / minutes);
  document.getElementById('limits-help').textContent = `Estimation pour les mesures en direct : environ ${live} requêtes par heure `
    + `aujourd’hui, ${full} au maximum de stations. L’historique des nouvelles stations et les voisines en ajoutent.`;
}

function recentProblems(info) {
  return info.log.filter((entry) => entry.message !== 'Rétabli' && info.now - entry.ts < 86400).length;
}

function renderLimits(info) {
  const form = document.getElementById('limits-form');
  if (form.contains(document.activeElement)) return; // do not overwrite what is being typed
  for (const name of ['total_stations', 'user_stations', 'poll_minutes']) form.elements[name].value = info.limits[name];
  form.elements.neighbours_allowed.checked = info.limits.neighbours_allowed;
  limitsHelp();
}

function stationRow(station, now) {
  const row = el('tr');
  const name = el('td', 'left');
  const link = el('a', '', `${station.name} ↗`);
  link.href = station.url;
  link.target = '_blank';
  link.rel = 'noopener noreferrer';
  name.append(link);
  if (station.place && station.place !== station.name) name.append(el('small', 'station-place', station.place));
  row.append(
    name,
    el('td', '', `${station.followers} pers.`),
    el('td', 'left', station.last ? ago(now - station.last) : 'Aucune'),
    el('td', 'left', station.error || 'OK'),
  );
  return row;
}

async function loadOverview() {
  try {
    const response = await fetch('/api/admin/overview');
    if (response.status === 401) location.href = '/login';
    if (response.status === 403) location.href = '/';
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    overview = await response.json();
  } catch {
    say('Serveur injoignable pour le moment.');
    return;
  }
  document.getElementById('server').replaceChildren(...serverTiles(overview));
  renderUsage(overview.usage);
  renderLimits(overview);
  renderAnnouncement(overview);
  document.getElementById('admin-log').replaceChildren(...logRows(overview.log));
  document.getElementById('log-empty').hidden = overview.log.length > 0;
  document.getElementById('admin-abandoned').replaceChildren(...overview.abandoned.map(abandonedRow));
  document.getElementById('abandoned-section').hidden = !overview.abandoned.length;
  document.getElementById('admin-stations').replaceChildren(
    ...overview.stations.map((station) => stationRow(station, overview.now)),
  );
  document.getElementById('log-table').hidden = !overview.log.length;
  const problems = recentProblems(overview);
  document.getElementById('journal-tab').textContent = problems ? `Journal (${problems})` : 'Journal';
}

const TABS = ['apercu', 'utilisateurs', 'stations', 'reglages', 'journal'];

function showTab(name) {
  const tab = TABS.includes(name) ? name : TABS[0];
  document.querySelectorAll('[data-group]').forEach((group) => { group.hidden = group.dataset.group !== tab; });
  document.querySelectorAll('#admin-tabs button').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.tab === tab));
  });
}

document.getElementById('admin-tabs').addEventListener('click', (event) => {
  const button = event.target.closest('button');
  if (!button) return;
  history.replaceState(null, '', `#${button.dataset.tab}`); // a reload stays on the same part
  say('');
  showTab(button.dataset.tab);
});
showTab(location.hash.slice(1));

function dateTime(ts) {
  return new Date(ts * 1000).toLocaleString('fr-FR', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

function logRows(log) {
  return log.map((entry) => {
    const row = el('tr', entry.message === 'Rétabli' ? 'log-ok' : '');
    row.append(
      el('td', 'left', dateTime(entry.ts)),
      el('td', 'left', entry.kind),
      el('td', 'left', entry.station || '–'),
      el('td', 'left', entry.message),
    );
    return row;
  });
}

function forgetStation(station) {
  const label = station.place || station.id;
  if (!confirm(`Supprimer définitivement les ${number(station.measures)} mesures de ${label} ? Cela ne peut pas être annulé.`)) return;
  adminAction(`/api/admin/stations?id=${encodeURIComponent(station.id)}`, { method: 'DELETE' }, () => {
    say(`Données de ${label} supprimées.`);
    loadOverview();
  });
}

function abandonedRow(station) {
  const row = el('tr');
  const action = el('td');
  const button = el('button', '', 'Supprimer ses données');
  button.type = 'button';
  button.addEventListener('click', () => forgetStation(station));
  action.append(button);
  row.append(
    el('td', 'left', station.place || station.id),
    el('td', '', number(station.measures)),
    el('td', 'left', station.last ? dateTime(station.last) : 'Aucune'),
    action,
  );
  return row;
}

function saveLimits(event) {
  event.preventDefault();
  const form = event.target;
  post('/api/admin/limits', {
    total_stations: Number(form.elements.total_stations.value),
    user_stations: Number(form.elements.user_stations.value),
    poll_minutes: Number(form.elements.poll_minutes.value),
    neighbours_allowed: form.elements.neighbours_allowed.checked,
  }, () => {
    say('Limites enregistrées.');
    document.activeElement.blur();
    loadOverview();
  });
}

function untilText(ts) {
  return new Date(ts * 1000).toLocaleString('fr-FR', {
    weekday: 'long', day: 'numeric', month: 'long', hour: '2-digit', minute: '2-digit',
  });
}

function renderAnnouncement(info) {
  const form = document.getElementById('announcement-form');
  if (!form.contains(document.activeElement)) form.elements.text.value = info.announcement;
  document.getElementById('announcement-remove').hidden = !info.announcement;
  let state = 'Aucun message en ce moment.';
  if (info.announcement) {
    state = info.announcement_until
      ? `Affiché jusqu’au ${untilText(info.announcement_until)}.`
      : 'Affiché jusqu’à ce que vous l’enleviez.';
  }
  document.getElementById('announcement-state').textContent = state;
}

function sendAnnouncement(text, hours) {
  post('/api/admin/announcement', { text, hours }, () => {
    say(text ? 'Message publié : chacun le verra au prochain rafraîchissement.' : 'Message retiré.');
    document.activeElement.blur();
    loadOverview();
  });
}

function publishAnnouncement(event) {
  event.preventDefault();
  const form = event.target;
  sendAnnouncement(form.elements.text.value.trim(), Number(form.elements.hours.value));
}

document.getElementById('announcement-remove').addEventListener('click', () => {
  document.getElementById('announcement-form').elements.text.value = '';
  sendAnnouncement('', 0);
});

document.getElementById('limits-form').addEventListener('submit', saveLimits);
document.getElementById('limits-form').addEventListener('input', limitsHelp);
document.getElementById('announcement-form').addEventListener('submit', publishAnnouncement);
document.getElementById('invites-open').addEventListener('change', setInvitesOpen);
loadOverview();
loadUsers();
setInterval(loadOverview, 60000);
setInterval(loadUsers, 30000); // who is online changes often
setInterval(tickVisits, 1000);

'use strict';

// Loaded first on every page, so that the saved theme applies before anything is drawn.
const THEMES = [
  ['auto', 'Automatique', 'linear-gradient(135deg, #f9f9f7 50%, #1a1a19 50%)'],
  ['clair', 'Clair', '#fcfcfb'],
  ['sombre', 'Sombre', '#1a1a19'],
  // Au choix seulement : l'ambiance de la page de connexion, sans animation
  ['ciel', 'Ciel', 'linear-gradient(180deg, #0a1028, #2e3468 60%, #5b4a7e)'],
];

function applyTheme(name) {
  if (name === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.dataset.theme = name;
}

function savedTheme() {
  try {
    const saved = localStorage.getItem('meteo-theme');
    return THEMES.some(([name]) => name === saved) ? saved : 'auto';
  } catch {
    return 'auto';
  }
}

function saveTheme(name) {
  try {
    localStorage.setItem('meteo-theme', name);
  } catch {
    /* the choice just won't be remembered */
  }
  syncSettings();
}

function savedSettings() {
  return Object.keys(localStorage).filter((key) => key.startsWith('meteo-'));
}

// The account keeps the choices, so that they follow the person on every device.
function syncSettings() {
  try {
    const settings = Object.fromEntries(savedSettings().map((key) => [key, localStorage.getItem(key)]));
    fetch('/api/settings', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ settings }),
      keepalive: true,
    }).catch(() => { /* this browser still has them */ });
  } catch {
    /* nothing to send */
  }
}

// After a logout, the next person on this browser must not inherit these choices.
function forgetSettings() {
  try {
    savedSettings().forEach((key) => localStorage.removeItem(key));
  } catch {
    /* nothing to forget */
  }
}

applyTheme(savedTheme());

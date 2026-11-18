/* Météo : bouton « Installer l'appli » (PC et téléphone).
   Chrome et Edge préviennent (beforeinstallprompt) seulement quand l'appli n'est PAS installée : le bouton
   n'apparaît que dans ce cas. Un onglet ne sait pas tout seul que l'appli existe : on retient sur l'appareil
   qu'elle a été ouverte dans sa fenêtre, et on demande au navigateur s'il la connaît.
   iPhone / iPad : impossible à savoir ; le bouton (des explications) disparaît une fois lu. */
(() => {
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  const bouton = document.getElementById('install-app');
  const CLE = 'meteo-appli-installee';
  const notee = () => { try { return localStorage.getItem(CLE) === '1'; } catch { return false; } };
  const noter = oui => { try { oui ? localStorage.setItem(CLE, '1') : localStorage.removeItem(CLE); } catch { /* rien */ } };
  const enFenetre = () => matchMedia('(display-mode: standalone)').matches || !!navigator.standalone;
  if (enFenetre()) noter(true);
  if (!bouton) return;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  let invite = null;
  const maj = () => { bouton.hidden = enFenetre() || notee() || !(invite || ios); };
  addEventListener('beforeinstallprompt', e => { e.preventDefault(); invite = e; noter(false); maj(); });
  addEventListener('appinstalled', () => { noter(true); invite = null; maj(); });
  maj();
  if (!notee() && navigator.getInstalledRelatedApps) {
    navigator.getInstalledRelatedApps().then(l => { if (l.length) { noter(true); maj(); } }).catch(() => {});
  }
  bouton.addEventListener('click', async () => {
    if (invite) {
      invite.prompt();
      if ((await invite.userChoice).outcome === 'accepted') noter(true);
      invite = null;
    } else {
      alert('Sur iPhone : touche le bouton Partager (le carré avec une flèche) en bas de Safari, puis « Sur l\'écran d\'accueil ».');
      noter(true);   // on ne peut pas vérifier sur iPhone : expliqué une fois suffit
    }
    maj();
  });
})();

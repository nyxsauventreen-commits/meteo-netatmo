'use strict';

const form = document.getElementById('login-form');
const message = document.getElementById('login-message');
const submit = document.getElementById('login-submit');
let isCreating = false;

function showMode() {
  document.getElementById('login-title').textContent = isCreating ? 'Créer un compte' : 'Connexion';
  document.getElementById('code-field').hidden = !isCreating;
  form.elements.code.required = isCreating;
  form.elements.password.autocomplete = isCreating ? 'new-password' : 'current-password';
  submit.textContent = isCreating ? 'Créer mon compte' : 'Se connecter';
  document.getElementById('login-switch').textContent = isCreating ? 'J’ai déjà un compte' : 'Créer un compte';
  message.textContent = isCreating ? 'Mot de passe : 10 caractères minimum.' : '';
}

const invitation = new URLSearchParams(location.search).get('code');
if (invitation) {
  isCreating = true;
  form.elements.code.value = invitation;
  showMode();
  history.replaceState(null, '', '/login'); // keeps the code out of the address bar
}

document.getElementById('login-switch').addEventListener('click', () => {
  isCreating = !isCreating;
  showMode();
});

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  submit.disabled = true;
  message.textContent = '';
  try {
    const response = await fetch(isCreating ? '/api/register' : '/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.fromEntries(new FormData(form))),
    });
    if (response.ok) {
      location.href = '/';
      return;
    }
    message.textContent = (await response.json()).error;
  } catch {
    message.textContent = 'Serveur injoignable, réessayez.';
  } finally {
    submit.disabled = false;
  }
});

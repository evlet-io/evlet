// A small static portal built without the Evlet UI. It reads where the API is from the
// config block the server adds to index.html, signs in a portal user and lists the
// customer and invoices the own_customer scope lets them see.

const config = JSON.parse(document.getElementById('evlet-config')?.textContent || '{"api":"/api"}');
let csrf = '';

async function call(method, path, body) {
  const headers = { Accept: 'application/json' };
  if (method !== 'GET') Object.assign(headers, { 'X-Evlet-Request': '1', 'X-CSRF-Token': csrf, 'Content-Type': 'application/json' });
  const response = await fetch(config.api + path, { method, headers, credentials: 'same-origin', body: body && JSON.stringify(body) });
  const data = response.status === 204 ? null : await response.json();
  if (!response.ok) throw new Error(data?.error || 'Something went wrong');
  return data;
}

function row(cells) {
  const tr = document.createElement('tr');
  for (const value of cells) tr.append(Object.assign(document.createElement('td'), { textContent: value ?? '' }));
  return tr;
}

async function show(session) {
  const signedIn = Boolean(session.user);
  document.getElementById('sign-in').hidden = signedIn;
  document.getElementById('account').hidden = !signedIn;
  if (!signedIn) return;
  csrf = session.csrf;
  document.getElementById('who').textContent = session.user.username;
  const [customers, invoices] = await Promise.all([call('GET', '/customer'), call('GET', '/invoice')]);
  document.getElementById('customers').replaceChildren(...customers.items.map(item => Object.assign(document.createElement('li'), { textContent: item.name })));
  document.getElementById('invoices').replaceChildren(...invoices.items.map(item => row([item.number || item.reference, item.status, item.total])));
}

document.getElementById('sign-in').addEventListener('submit', async event => {
  event.preventDefault();
  const form = new FormData(event.target);
  const error = event.target.querySelector('.error');
  error.textContent = '';
  try {
    await show(await call('POST', '/auth/login', { username: form.get('username'), password: form.get('password') }));
  } catch (failure) {
    error.textContent = failure.message;
  }
});

document.getElementById('sign-out').addEventListener('click', async () => {
  await call('POST', '/auth/logout');
  csrf = '';
  await show({ user: null });
});

show(await call('GET', '/auth/session'));

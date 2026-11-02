export function accountIndex(url) {
  const parsed = new URL(url);
  if (parsed.origin !== 'https://gemini.google.com') throw new Error('请先打开 Gemini 网页。');
  const pathIndex = parsed.pathname.match(/\/u\/(\d+)(?:\/|$)/)?.[1];
  const queryIndices = parsed.searchParams.getAll('authuser');
  if ((pathIndex !== undefined && pathIndex !== '0') || queryIndices.some(index => index !== '0')) throw new Error('当前是非默认 Google 账号。请使用独立浏览器配置或仅登录目标账号的无痕窗口，再导出，避免 Cookie 与邮箱错配。');
  return 0;
}

export function readEmailCandidates() {
  // Only visible account controls, never chat text or arbitrary page-wide emails.
  const selectors = [
    'a[href*="accounts.google.com/SignOutOptions"]',
    'a[aria-label*="Google Account"]', 'button[aria-label*="Google Account"]',
    'a[aria-label*="Google 账号"]', 'a[aria-label*="Google 帐号"]',
    '[data-test-id="account-menu-button"]',
  ];
  const emails = new Set();
  for (const element of document.querySelectorAll(selectors.join(','))) {
    if (!element.getClientRects().length || getComputedStyle(element).visibility === 'hidden') continue;
    const label = [element.getAttribute('aria-label'), element.getAttribute('title'), element.getAttribute('data-email'), element.textContent].filter(Boolean).join(' ');
    for (const match of label.match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi) || []) emails.add(match);
  }
  return [...emails];
}

export async function readSession(api, report = () => {}) {
  let step = 'tab';
  const emit = (name, data = {}) => { step = name; report(name, data); };
  try {
  emit('tab');
  const [tab] = await api.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !tab.url) throw new Error('无法读取当前标签页，请重新点击扩展。');
  const parsed = new URL(tab.url);
  const isGemini = parsed.origin === 'https://gemini.google.com';
  emit('page', {isGemini, defaultAccount: isGemini && !(/\/u\/[1-9]\d*(?:\/|$)/.test(parsed.pathname) || parsed.searchParams.getAll('authuser').some(index => index !== '0'))});
  accountIndex(tab.url);
  if (api.permissions?.contains) {
    for (const origin of ['https://google.com/*', 'https://gemini.google.com/*']) {
      emit('permissions', {origin, granted: await api.permissions.contains({origins: [origin]})});
    }
  }
  emit('store');
  const stores = await api.cookies.getAllCookieStores();
  const store = stores.find(item => item.tabIds.includes(tab.id));
  if (!store) throw new Error('无法确定当前标签页的 Cookie 存储，未读取凭证。');
  emit('store', {found: true});
  let candidates = [];
  emit('email');
  try {
    const results = await api.scripting.executeScript({target: {tabId: tab.id}, func: readEmailCandidates});
    candidates = results[0]?.result || [];
  } catch { emit('email', {available: false}); }
  emit('email', {count: candidates.length});
  const cookies = {};
  for (const name of ['__Secure-1PSID', '__Secure-1PSIDTS']) {
    emit('cookie', {name});
    const all = await api.cookies.getAll({url:'https://gemini.google.com/', name, storeId:store.id});
    const domains = ['.google.com','google.com','gemini.google.com','.gemini.google.com'];
    const found = all.filter(cookie => cookie.path === '/' && !cookie.partitionKey && domains.includes(cookie.domain));
    emit('cookie', {name, matched: all.length, accepted: found.length,
      pathRejected: all.filter(cookie => cookie.path !== '/').length,
      partitionRejected: all.filter(cookie => Boolean(cookie.partitionKey)).length,
      domainRejected: all.filter(cookie => !domains.includes(cookie.domain)).length});
    if (found.length === 0) throw new Error(`未读取到 ${name}。请确认扩展已更新并获准访问 google.com 和 gemini.google.com，且当前浏览器配置已登录目标账号。`);
    if (found.length > 1) throw new Error(`找到多个 ${name}，无法安全选择。请使用只登录目标账号的独立浏览器配置。`);
    if (!found[0].value) throw new Error(`${name} 为空，请重新登录目标账号。`);
    cookies[name] = found[0].value;
  }
  // A tab can navigate while asynchronous reads are in progress.
  emit('navigation');
  const current = await api.tabs.get(tab.id);
  if (current.url !== tab.url) throw new Error('读取期间页面发生跳转，请重新读取。');
  emit('complete');
  return {cookies, candidates};
  } catch (error) {
    // Never record browser exceptions: messages can contain URLs or private data.
    report('failed', {step});
    throw error;
  }
}

export function makeExport(session, email, confirmed) {
  const value = email.trim();
  if (!session || !confirmed) throw new Error('请先读取并确认目标账号。');
  if (value.length > 100 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) throw new Error('请填写正确的邮箱，最多100个字符。');
  return {
    schema_version: 1,
    source: 'gemini-web-bridge-session-exporter',
    exported_at: new Date().toISOString(),
    email: value,
    display_name: value,
    email_source: session.candidates.length === 1 && session.candidates[0] === value ? 'page_account_control_confirmed' : 'user_confirmed',
    account_index: 0,
    cookies: {'__Secure-1PSID': session.cookies['__Secure-1PSID'], '__Secure-1PSIDTS':session.cookies['__Secure-1PSIDTS']},
  };
}

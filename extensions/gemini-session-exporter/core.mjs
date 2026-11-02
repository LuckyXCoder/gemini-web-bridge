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

export async function readSession(api) {
  const [tab] = await api.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !tab.url) throw new Error('无法读取当前标签页，请重新点击扩展。');
  accountIndex(tab.url);
  const stores = await api.cookies.getAllCookieStores();
  const store = stores.find(item => item.tabIds.includes(tab.id));
  if (!store) throw new Error('无法确定当前标签页的 Cookie 存储，未读取凭证。');
  let candidates = [];
  try {
    const results = await api.scripting.executeScript({target: {tabId: tab.id}, func: readEmailCandidates});
    candidates = results[0]?.result || [];
  } catch { /* Account controls can be inside an inaccessible frame; manual email confirmation remains available. */ }
  const cookies = {};
  for (const name of ['__Secure-1PSID', '__Secure-1PSIDTS']) {
    const found = (await api.cookies.getAll({url:'https://gemini.google.com/', name, storeId:store.id}))
      .filter(cookie => cookie.path === '/' && !cookie.partitionKey && ['.google.com','google.com','gemini.google.com','.gemini.google.com'].includes(cookie.domain));
    if (found.length !== 1 || !found[0].value) throw new Error(`未找到唯一的 ${name}，请确认已登录目标账号。`);
    cookies[name] = found[0].value;
  }
  // A tab can navigate while asynchronous reads are in progress.
  const current = await api.tabs.get(tab.id);
  if (current.url !== tab.url) throw new Error('读取期间页面发生跳转，请重新读取。');
  return {cookies, candidates};
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

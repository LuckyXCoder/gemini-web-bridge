/** Parse only allowed form fields. Never apply IDs, paths, API keys or enabled flags. */
export function parseCredentialImport(text) {
  if (typeof text !== 'string' || new TextEncoder().encode(text).length > 65536) throw new Error('JSON 文件或内容不能超过 64KB');
  let data;
  try { data = JSON.parse(text); } catch { throw new Error('JSON 格式不正确'); }
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('请导入单个账号的 JSON 对象');
  if (data.schema_version !== undefined && data.schema_version !== 1) throw new Error('不支持此 JSON 版本');
  if (data.account_index !== undefined && data.account_index !== 0) throw new Error('暂不支持非默认 Google 会话，请在独立浏览器会话中导出目标账号');
  const cookies = data.cookies ?? data;
  if (!cookies || typeof cookies !== 'object' || Array.isArray(cookies)) throw new Error('cookies 必须是名称和值组成的对象');
  const result = {};
  for (const [source, target] of [['__Secure-1PSID','secure_1psid'],['__Secure-1PSIDTS','secure_1psidts']]) {
    const value = cookies[source];
    if (typeof value !== 'string' || !value.trim() || value.length > 8192 || /[\r\n]/.test(value)) throw new Error(`缺少或无效的 ${source}`);
    result[target] = value.trim();
  }
  const name = data.display_name ?? data.email;
  if (name !== undefined) {
    if (typeof name !== 'string' || !name.trim() || name.trim().length > 100 || /[\x00-\x1f\x7f]/.test(name)) throw new Error('账号名称必须为 1–100 个可显示字符');
    result.display_name = name.trim();
  }
  return result;
}

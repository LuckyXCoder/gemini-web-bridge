export type TransferAccount = { display_name: string; enabled: boolean; secure_1psid: string; secure_1psidts: string };
export const transferKind = 'gemini-web-bridge-accounts';
export function parseAccountTransfer(text: string): TransferAccount[] {
  if (new TextEncoder().encode(text).length > 2 * 1024 * 1024) throw new Error('文件不能超过 2MB');
  let data;
  try { data = JSON.parse(text); } catch { throw new Error('JSON 格式不正确'); }
  if (!data || data.schema_version !== 1 || data.kind !== transferKind || !Array.isArray(data.accounts) || data.accounts.length > 100)
    throw new Error('请选择“导出全部账号”生成的 JSON，最多 100 个账号');
  return data.accounts.map((a: unknown, i: number) => {
    if (!a || typeof a !== 'object') throw new Error(`第 ${i+1} 个账号格式不正确`);
    const row = a as Record<string, unknown>;
    const name = row.display_name;
    if (typeof name !== 'string' || !name.trim() || name.length > 100 || /[\u0000-\u001f\u007f]/.test(name) || typeof row.enabled !== 'boolean')
      throw new Error(`第 ${i+1} 个账号名称或启用状态不正确`);
    for (const key of ['secure_1psid', 'secure_1psidts']) {
      const value = row[key];
      if (typeof value !== 'string' || !value.trim() || value.length > 8192 || /[\r\n]/.test(value))
        throw new Error(`第 ${i+1} 个账号凭证格式不正确`);
    }
    // Never apply IDs, paths, service/admin keys or unrecognized fields.
    return {display_name:name.trim(), enabled:row.enabled, secure_1psid:(row.secure_1psid as string).trim(), secure_1psidts:(row.secure_1psidts as string).trim()};
  });
}

// Only predefined labels, booleans and counts enter diagnostic output.
const steps = {tab:'读取标签页',page:'检查页面',permissions:'检查域名权限',store:'选择 Cookie 存储',email:'识别邮箱',cookie:'读取 Cookie',navigation:'检查页面跳转',complete:'读取完成'};
const yes = value => value === true ? '是' : '否';
const count = value => Number.isSafeInteger(value) && value >= 0 ? String(value) : '?';
export function diagnosticLine(event, data = {}) {
  switch(event) {
    case 'page': return `Gemini 页面：${yes(data.isGemini)}；默认账号路由：${yes(data.defaultAccount)}`;
    case 'permissions': return `域名权限 ${['https://google.com/*','https://gemini.google.com/*'].includes(data.origin) ? data.origin : '未知'}：${yes(data.granted)}`;
    case 'store': return data.found ? '已找到当前标签页的 Cookie 存储' : '正在定位 Cookie 存储';
    case 'email': return data.available === false ? '邮箱控件读取失败，可手动填写' : data.count !== undefined ? `邮箱候选数量：${count(data.count)}（不记录邮箱）` : '正在识别账号控件';
    case 'cookie': {
      const name = ['__Secure-1PSID','__Secure-1PSIDTS'].includes(data.name) ? data.name : '未知 Cookie';
      return data.matched === undefined ? `正在查询 ${name}` : `${name}：API 返回 ${count(data.matched)} 条，可用 ${count(data.accepted)} 条；过滤原因：路径 ${count(data.pathRejected)}、分区 ${count(data.partitionRejected)}、域名 ${count(data.domainRejected)}（原因计数可重叠）`;
    }
    case 'failed': return `读取失败，步骤：${steps[data.step] || '未知'}；请结合弹窗提示排查`;
    case 'copy-failed': return '复制账号 JSON 失败';
    case 'download-failed': return '下载账号 JSON 失败';
    default: return steps[event] || '未知诊断事件';
  }
}
export function environmentSummary(version, userAgent) {
  const safeVersion = /^\d+(\.\d+){1,3}$/.test(version) ? version : '未知';
  const browser = String(userAgent).match(/(?:Edg|Chrome)\/\d+(?:\.\d+)*/g)?.at(-1) || '未知浏览器';
  return `扩展版本：${safeVersion}\n浏览器：${browser}`;
}

import { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Alert, Button, Card, ConfigProvider, Empty, Form, Input, Layout, Menu, Modal, Popconfirm, Space, Statistic, Table, Tag, Tooltip, Typography, message } from 'antd';
import { ApiOutlined, CheckCircleOutlined, ClockCircleOutlined, DeleteOutlined, EditOutlined, LogoutOutlined, PlusOutlined, ReloadOutlined, SafetyCertificateOutlined, TeamOutlined } from '@ant-design/icons';
import 'antd/dist/reset.css';
import './style.css';
import { parseCredentialImport } from './credentialImport.mjs';

type Outcome = { time: number; success: boolean; duration: number; error: string | null };
type Account = { id: string; display_name?: string; enabled: boolean; status: string; login_status: string; last_checked_at: number | null; checking: boolean; removing: boolean; recent: Outcome[] };
type Snapshot = { data: Account[]; queue_depth: number };
type AccountForm = { display_name: string; secure_1psid?: string; secure_1psidts?: string };
const nameOf = (account: Account) => account.display_name || account.id;
const empty: Snapshot = { data: [], queue_depth: 0 };
const runtime: Record<string, [string, string]> = { idle: ['空闲', 'green'], busy: ['生成中', 'blue'], paused: ['已暂停', 'orange'], disabled: ['已禁用', 'default'], starting: ['初始化中', 'blue'] };
const loginState: Record<string, [string, string]> = { valid: ['有效', 'green'], invalid: ['已失效', 'red'], unavailable: ['验证失败', 'orange'], unknown: ['未验证', 'default'] };
const stamp = (value: number | null) => value ? new Date(value * 1000).toLocaleString('zh-CN', { hour12: false }) : '—';
const errorNames: Record<string, string> = { timeout: '请求超时', upstream_failure: '上游请求或下载失败', authentication_failed: '认证失败', account_unavailable: '账号不可用', quota_exceeded: '额度受限', service_stopping: '服务停止' };
class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

const sessionKey = 'gemini-bridge-admin-session';
function savedKey() { try { return sessionStorage.getItem(sessionKey) || ''; } catch { return ''; } }
function saveKey(key: string) { try { if (key) sessionStorage.setItem(sessionKey, key); else sessionStorage.removeItem(sessionKey); } catch { /* Browser storage can be disabled. */ } }

function App() {
  const [token, setToken] = useState(savedKey);
  const [snapshot, setSnapshot] = useState<Snapshot>(empty);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [action, setAction] = useState<string | null>(null);
  const [updated, setUpdated] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Account | null>(null);
  const [importing, setImporting] = useState(false);
  const [importText, setImportText] = useState('');
  const [importError, setImportError] = useState('');
  const [progress, setProgress] = useState<{ done: number; total: number } | null>(null);
  const [summary, setSummary] = useState('');
  const [form] = Form.useForm();
  const [addForm] = Form.useForm();
  const [toast, contextHolder] = message.useMessage();
  const locked = loading || action !== null;

  useEffect(() => {
    const key = savedKey();
    if (!key) return;
    let active = true;
    const controller = new AbortController();
    setLoading(true);
    fetch('/admin/api/accounts', { headers: { Authorization: 'Bearer ' + key }, signal: controller.signal })
      .then(async response => {
        if (!active) return;
        if (response.status === 401) { saveKey(''); setToken(''); setSnapshot(empty); throw new Error('管理员密钥已失效，请重新输入。'); }
        if (!response.ok) throw new Error('恢复会话失败，请刷新重试。');
        const data = await response.json();
        if (active) { setSnapshot(data); setUpdated(Date.now() / 1000); }
      })
      .catch(error => { if (active) setError(error.message); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; controller.abort(); };
  }, []);

  async function request(path: string, key = token, method = 'GET', body?: unknown) {
    const response = await fetch('/admin/api' + path, {
      method,
      headers: { Authorization: 'Bearer ' + key, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
      signal: AbortSignal.timeout(80000),
    });
    if (response.status === 401) {
      saveKey(''); setToken(''); setSnapshot(empty); setAdding(false); setEditing(null); addForm.resetFields();
      throw new ApiError(401, '管理员密钥无效，请重新输入。');
    }
    const data = await response.json();
    if (!response.ok) {
      let reason = `请求失败（${response.status}），请检查服务是否正常。`;
      if (response.status === 409) reason = /already|duplicate/i.test(data.error?.message || '') ? '账号别名或凭证已存在，请勿重复添加。' : '账号正在生成、验证或等待删除，请稍后再试。';
      if (response.status === 400) reason = '输入不合法或凭证验证失败，请核对 Cookie。';
      if (response.status === 404) reason = '账号已不存在，请刷新列表。';
      throw new ApiError(response.status, reason);
    }
    return data;
  }
  async function refresh(key = token): Promise<Snapshot> {
    const data = await request('/accounts', key);
    setSnapshot(data); setUpdated(Date.now() / 1000); setError('');
    return data;
  }
  async function signIn(values: { key: string }) {
    setLoading(true);
    try { await refresh(values.key); saveKey(values.key); setToken(values.key); form.resetFields(); }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }
  async function manualRefresh() {
    setLoading(true);
    try { await refresh(); } catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }
  async function check(account: Account) {
    setAction('check:' + account.id);
    try {
      const result = await request(`/accounts/${encodeURIComponent(account.id)}/check`, token, 'POST');
      await refresh();
      if (result.login_status === 'valid') toast.success(`${nameOf(account)} 凭证有效`);
      else if (result.login_status === 'invalid') toast.warning(`${nameOf(account)} 凭证已失效`);
      else toast.warning('验证失败，请检查网络或凭证文件。');
    } catch (e) { setError((e as Error).message); }
    finally { setAction(null); }
  }
  async function batchCheck() {
    setAction('batch'); setSummary('');
    const counts = { valid: 0, invalid: 0, failed: 0, skipped: 0 };
    let interrupted = false;
    try {
      const current = await refresh();
      setProgress({ done: 0, total: current.data.length });
      for (const [index, account] of current.data.entries()) {
        if (account.status === 'busy' || account.checking || account.removing) counts.skipped++;
        else {
          try {
            const result = await request(`/accounts/${encodeURIComponent(account.id)}/check`, token, 'POST');
            if (result.login_status === 'valid') counts.valid++;
            else if (result.login_status === 'invalid') counts.invalid++;
            else counts.failed++;
            setSnapshot(previous => ({ ...previous, data: previous.data.map(a => a.id === account.id ? { ...a, ...result } : a) }));
          } catch (e) {
            if (e instanceof ApiError && e.status === 401) { interrupted = true; throw e; }
            if (e instanceof ApiError && [404, 409].includes(e.status)) counts.skipped++;
            else counts.failed++;
          }
        }
        setProgress({ done: index + 1, total: current.data.length });
      }
      await refresh();
    } catch (e) { interrupted = true; setError((e as Error).message); }
    finally {
      setSummary(`${interrupted ? '验证已中止' : '批量验证完成'}：有效 ${counts.valid} · 失效 ${counts.invalid} · 验证失败 ${counts.failed} · 跳过 ${counts.skipped}`);
      setProgress(null); setAction(null);
    }
  }
  async function saveAccount(values: AccountForm) {
    setAction('add');
    try {
      const display_name = values.display_name.trim();
      if (editing) {
        const credentialsChanged = Boolean(values.secure_1psid?.trim() || values.secure_1psidts?.trim());
        await request(`/accounts/${encodeURIComponent(editing.id)}${credentialsChanged ? '/cookies' : ''}`, token, credentialsChanged ? 'PUT' : 'PATCH', credentialsChanged ? { ...values, display_name } : { display_name });
      } else {
        await request('/accounts', token, 'POST', { ...values, display_name });
      }
      addForm.resetFields(); setAdding(false); setEditing(null); setSummary(''); await refresh();
      toast.success(editing ? '账号信息已保存' : '凭证验证通过，账号已加入列表');
    } catch (e) { toast.error(['TimeoutError', 'AbortError'].includes((e as Error).name) ? '请求超时，请先刷新列表确认结果，不要重复提交。' : (e as Error).message); }
    finally { setAction(null); }
  }
  function editAccount(account: Account) {
    addForm.resetFields(); addForm.setFieldValue('display_name', nameOf(account)); setEditing(account);
  }
  const credentialRules = [{ validator: (_: unknown, value: string) => {
    const required = !editing || addForm.getFieldValue('secure_1psid')?.trim() || addForm.getFieldValue('secure_1psidts')?.trim();
    return !required || value?.trim() ? Promise.resolve() : Promise.reject(new Error('更新凭证时请同时填写两个 Cookie'));
  }}];
  function applyImport(text: string) {
    try {
      const values = parseCredentialImport(text);
      addForm.setFieldsValue(values);
      setImportText(''); setImportError(''); setImporting(false);
      toast.success('已填入表单，请确认账号名称，再验证保存');
    } catch (e) { setImportError((e as Error).message); }
  }
  async function importFile(file?: File) {
    if (!file) return;
    if (file.size > 65536) { setImportError('文件不能超过 64KB'); return; }
    try { applyImport(await file.text()); } catch { setImportError('文件读取失败'); }
  }
  async function removeAccount(account: Account) {
    setAction('delete:' + account.id);
    try {
      const result = await request(`/accounts/${encodeURIComponent(account.id)}`, token, 'DELETE');
      setSummary(''); await refresh();
      toast.success(result.pending ? '已停止接单，当前任务完成后移出列表；可稍后刷新查看' : '账号已移出列表，凭证文件已保留');
    } catch (e) { setError((e as Error).message); }
    finally { setAction(null); }
  }
  const columns = [
    { title: '账号', key: 'id', render: (_: unknown, a: Account) => <Space size={12}><span className="account-icon"><ApiOutlined /></span><div><Typography.Text strong className="account-name">{nameOf(a)}</Typography.Text><div className="muted">{a.enabled ? '已启用' : '已禁用'} · Gemini 网页账号</div></div></Space> },
    { title: '运行状态', key: 'status', render: (_: unknown, a: Account) => { const [label, color] = a.removing ? ['待删除', 'orange'] : runtime[a.status] || [a.status, 'default']; return <Tag color={color}>{label}</Tag>; } },
    { title: '凭证状态', key: 'login', render: (_: unknown, a: Account) => { const [label, color] = loginState[a.login_status] || loginState.unknown; return <div><Tag color={color}>{label}</Tag><div className="timestamp">{stamp(a.last_checked_at)}</div></div>; } },
    { title: '最近请求', key: 'recent', render: (_: unknown, a: Account) => <div><div className="timestamp">{stamp(a.recent.at(-1)?.time ?? null)}</div><div className="results">{a.recent.length ? a.recent.map((r, i) => <Tooltip key={`${r.time}-${i}`} title={<>{r.success ? '成功' : '失败'} · {r.duration}s<br />{stamp(r.time)}{r.error && <><br />{errorNames[r.error] || '请求失败'}</>}</>}><span tabIndex={0} aria-label={`${r.success ? '成功' : '失败'}，${r.duration}秒`} className={`result ${r.success ? 'success' : 'failure'}`} /></Tooltip>) : <span className="muted">暂无请求记录</span>}</div></div> },
    { title: '操作', key: 'action', fixed: 'right' as const, width: 285, render: (_: unknown, a: Account) => <Space size={8}>
      <Tooltip title={a.status === 'busy' ? '正在生成，任务结束后再验证' : '向 Google 验证当前 Cookie，不生成图片'}><Button size="small" icon={<SafetyCertificateOutlined />} loading={action === 'check:' + a.id || a.checking} disabled={locked || a.status === 'busy' || a.removing} onClick={() => void check(a)}>验证凭证</Button></Tooltip>
      <Button size="small" icon={<EditOutlined />} disabled={locked || a.checking || a.removing} onClick={() => editAccount(a)}>编辑</Button>
      <Popconfirm title={`删除账号 ${nameOf(a)}？`} description="停止分配新任务，当前任务完成后移出列表。磁盘上的凭证文件会保留。" okText="删除" cancelText="取消" okButtonProps={{ danger: true }} onConfirm={() => removeAccount(a)} disabled={locked || a.checking || a.removing}><Button danger size="small" icon={<DeleteOutlined />} disabled={locked || a.checking || a.removing}>删除</Button></Popconfirm>
    </Space> },
  ];
  return <ConfigProvider theme={{ token: { colorPrimary: '#2563eb', borderRadius: 8, fontFamily: '-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif' } }}>{contextHolder}
    {!token ? <div className="login"><Card className="login-card"><ApiOutlined className="login-mark" /><Typography.Title level={3}>Gemini Web Bridge</Typography.Title><Typography.Paragraph type="secondary">账号状态控制台</Typography.Paragraph>{error && <Alert type="error" title={error} showIcon />}<Form form={form} layout="vertical" onFinish={signIn}><Form.Item name="key" label="管理员密钥" rules={[{ required: true, message: '请输入管理员密钥' }]}><Input.Password autoComplete="off" placeholder="输入管理员 Key" /></Form.Item><Button block type="primary" htmlType="submit" loading={loading}>查看账号状态</Button></Form><p className="muted">使用独立管理员 Key，不是 CPA 调用 Key。<br />当前标签页刷新可保持登录，退出后清除会话。</p></Card></div>
      : <Layout className="shell"><Layout.Sider width={212} className="sidebar" breakpoint="lg" collapsedWidth={0}><div className="brand"><ApiOutlined /><div>Gemini Bridge<small>账号状态控制台</small></div></div><Menu selectedKeys={['accounts']} items={[{ key: 'accounts', icon: <TeamOutlined />, label: '账号状态' }]} /><div className="sidebar-footer">Gemini Web Bridge<br /><span>独立维护 · AGPL-3.0</span></div></Layout.Sider><Layout><Layout.Header className="header"><span>账号状态</span><Button icon={<LogoutOutlined />} disabled={locked} onClick={() => { saveKey(''); setToken(''); setSnapshot(empty); setError(''); setSummary(''); setAdding(false); setEditing(null); addForm.resetFields(); }}>退出</Button></Layout.Header><Layout.Content className="content">
        <div className="heading"><div><Typography.Title level={3}>账号概览</Typography.Title><Typography.Text type="secondary">查看运行状态，按需验证账号凭证</Typography.Text></div><Space wrap>
          <Tooltip title="刷新服务记录，不重新验证 Google 登录"><Button icon={<ReloadOutlined />} loading={loading} disabled={action !== null} onClick={() => void manualRefresh()}>刷新列表</Button></Tooltip>
          <Button icon={<SafetyCertificateOutlined />} loading={action === 'batch'} disabled={locked || !snapshot.data.length} onClick={() => void batchCheck()}>{progress ? `验证中 ${progress.done}/${progress.total}` : '批量验证'}</Button>
          <Button type="primary" icon={<PlusOutlined />} disabled={locked} onClick={() => { addForm.resetFields(); setEditing(null); setAdding(true); }}>新增账号</Button>
        </Space></div>
        <div className="stats">{[{ title: '账号总数', value: snapshot.data.length, icon: <TeamOutlined /> }, { title: '空闲可用', value: snapshot.data.filter(a => a.enabled && a.status === 'idle').length, icon: <CheckCircleOutlined /> }, { title: '等待任务', value: snapshot.queue_depth, icon: <ClockCircleOutlined /> }].map(item => <Card key={item.title}><Statistic title={item.title} value={item.value} prefix={item.icon} /></Card>)}</div>
        {error && <Alert className="error" type="error" title={error} showIcon />}
        <Card className="table-card" title={<div>账号列表<div className="list-hint">凭证验证不生成图片，忙碌账号自动跳过</div></div>} extra={<span className="muted">更新于 {stamp(updated)}</span>}>
          {summary && <div className="batch-summary" role="status">{summary}</div>}
          <Table rowKey="id" dataSource={snapshot.data} columns={columns} pagination={false} scroll={{ x: 1200 }} locale={{ emptyText: <Empty description="暂无账号"><Button type="primary" onClick={() => setAdding(true)}>新增账号</Button></Empty> }} />
          <div className="note"><span className="dot success" />成功 <span className="dot failure" />失败 <span>最近 10 次请求 · 重启后清空</span></div>
        </Card>
      </Layout.Content></Layout></Layout>}
    <Modal title={editing ? '编辑账号' : '新增账号'} open={adding || editing !== null} onCancel={() => { addForm.resetFields(); setAdding(false); setEditing(null); }} onOk={() => addForm.submit()} okText={editing ? '保存' : '验证并新增'} cancelText="取消" confirmLoading={action === 'add'} closable={action !== 'add'} maskClosable={action !== 'add'} cancelButtonProps={{ disabled: action === 'add' }} destroyOnHidden>
      <p className="muted">{editing ? '只改名称时无需填写 Cookie。更新凭证请同时填写两个值，验证通过后即时生效。' : '填写邮箱或中文名称方便识别，验证通过后加入账号池。名称不参与 Google 登录。'}</p>
      <Button disabled={action === 'add' || editing?.status === 'busy'} onClick={() => { setImportText(''); setImportError(''); setImporting(true); }} style={{marginBottom:16}}>导入 JSON</Button>
      <Form form={addForm} layout="vertical" onFinish={saveAccount} preserve={false}>
        <Form.Item name="display_name" label="账号名称" rules={[{ required: true, whitespace: true, message: '请输入账号名称' }, { max: 100, message: '名称最多 100 个字符' }]}><Input placeholder="例如 name@gmail.com 或 中文账号名" autoComplete="off" maxLength={100} /></Form.Item>
        <Form.Item name="secure_1psid" label="__Secure-1PSID" rules={credentialRules}><Input.Password disabled={editing?.status === 'busy'} placeholder={editing ? '留空则保留现有凭证' : '注意不是 __Secure-1PAPISID'} autoComplete="off" /></Form.Item>
        <Form.Item name="secure_1psidts" label="__Secure-1PSIDTS" rules={credentialRules}><Input.Password disabled={editing?.status === 'busy'} placeholder={editing ? '留空则保留现有凭证' : '同一账号的 PSIDTS'} autoComplete="off" /></Form.Item>
        {editing?.status === 'busy' && <p className="muted">账号正在生成，当前仅可修改名称。</p>}
      </Form>
    </Modal>
    <Modal title="导入账号 JSON" open={importing} onCancel={() => { setImportText(''); setImportError(''); setImporting(false); }} onOk={() => applyImport(importText)} okText="填入表单" cancelText="取消" destroyOnHidden>
      <p className="muted">选择扩展导出的文件，或粘贴 JSON。仅在浏览器解析；确认表单并保存后才发送到当前管理服务。</p>
      <input aria-label="选择账号 JSON 文件" type="file" accept=".json,application/json" onChange={event => void importFile(event.target.files?.[0])} />
      <Input.TextArea aria-label="粘贴账号 JSON" placeholder="在这里粘贴 JSON（包含敏感 Cookie，请勿分享）" value={importText} onChange={event => setImportText(event.target.value)} maxLength={65536} rows={7} style={{marginTop:16}} autoComplete="off" />
      {importError && <Alert style={{marginTop:12}} type="error" title={importError} showIcon/>}
    </Modal>
  </ConfigProvider>;
}
createRoot(document.getElementById('root')!).render(<App />);

import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { Alert, Button, Card, ConfigProvider, Empty, Form, Input, Layout, Menu, Space, Statistic, Table, Tag, Tooltip, Typography, message } from 'antd';
import { ApiOutlined, CheckCircleOutlined, ClockCircleOutlined, LogoutOutlined, ReloadOutlined, SafetyCertificateOutlined, TeamOutlined } from '@ant-design/icons';
import 'antd/dist/reset.css';
import './style.css';

type Outcome = {time:number;success:boolean;duration:number;error:string|null};
type Account = {id:string;enabled:boolean;status:string;login_status:string;last_checked_at:number|null;checking:boolean;recent:Outcome[]};
type Snapshot = {data:Account[];queue_depth:number};
const empty:Snapshot={data:[],queue_depth:0};
const runtime:Record<string,[string,string]>={idle:['空闲','green'],busy:['生成中','blue'],paused:['已暂停','orange'],disabled:['已禁用','default'],starting:['初始化中','blue']};
const loginState:Record<string,[string,string]>={valid:['登录有效','green'],invalid:['未登录 / 已失效','red'],unavailable:['检查未完成','orange'],unknown:['尚未检查','default']};
const stamp=(value:number|null)=>value?new Date(value*1000).toLocaleString('zh-CN',{hour12:false}):'—';
const errorNames:Record<string,string>={timeout:'请求超时',upstream_failure:'上游请求或下载失败',authentication_failed:'认证失败',account_unavailable:'账号不可用',quota_exceeded:'额度受限',service_stopping:'服务停止'};

function App(){
 const [token,setToken]=useState('');const [snapshot,setSnapshot]=useState<Snapshot>(empty);const [error,setError]=useState('');
 const [loading,setLoading]=useState(false);const [checking,setChecking]=useState<string|null>(null);const [updated,setUpdated]=useState<number|null>(null);
 const [form]=Form.useForm();const [toast,contextHolder]=message.useMessage();
 async function request(path:string,key=token,method='GET'){
  const response=await fetch('/admin/api'+path,{method,headers:{Authorization:'Bearer '+key},signal:AbortSignal.timeout(60000)});
  if(response.status===401){setToken('');setSnapshot(empty);throw new Error('管理员密钥无效，请重新输入。');}
  if(response.status===409)throw new Error('账号正在生成或检查，请稍后再试。');
  if(!response.ok)throw new Error(`请求失败（${response.status}），请检查服务是否正常。`);
  return response.json();
 }
 async function refresh(key=token){const data=await request('/accounts',key);setSnapshot(data);setUpdated(Date.now()/1000);setError('');}
 async function signIn(values:{key:string}){setLoading(true);try{await refresh(values.key);setToken(values.key);form.resetFields();}catch(e){setError((e as Error).message);}finally{setLoading(false);}}
 async function manualRefresh(){setLoading(true);try{await refresh();}catch(e){setError((e as Error).message);}finally{setLoading(false);}}
 async function check(account:Account){
  setChecking(account.id);
  try{const result=await request(`/accounts/${encodeURIComponent(account.id)}/check`,token,'POST');await refresh();
   if(result.login_status==='valid')toast.success(`${account.id} 登录有效`);
   else if(result.login_status==='invalid')toast.warning(`${account.id} 当前未登录或凭证已失效`);
   else toast.warning('未能完成检查，请检查网络或凭证文件。');
  }catch(e){setError((e as Error).message);}finally{setChecking(null);}
 }
 const columns=[
  {title:'账号',key:'id',render:(_:unknown,a:Account)=><Space size={12}><span className="account-icon"><ApiOutlined/></span><div><Typography.Text strong>{a.id}</Typography.Text><div className="muted">{a.enabled?'已启用':'已禁用'} · Gemini 网页账号</div></div></Space>},
  {title:'运行状态',key:'status',render:(_:unknown,a:Account)=>{const [label,color]=runtime[a.status]||[a.status,'default'];return <Tag color={color}>{label}</Tag>;}},
  {title:'最近登录检查',key:'login',render:(_:unknown,a:Account)=>{const [label,color]=loginState[a.login_status]||loginState.unknown;return <div><Tag color={color}>{label}</Tag><div className="timestamp">{stamp(a.last_checked_at)}</div></div>;}},
  {title:'最近请求',key:'recent',render:(_:unknown,a:Account)=><div><div className="timestamp">{stamp(a.recent.at(-1)?.time??null)}</div><div className="results">{a.recent.length?a.recent.map((r,i)=><Tooltip key={`${r.time}-${i}`} title={<>{r.success?'成功':'失败'} · {r.duration}s<br/>{stamp(r.time)}{r.error&&<><br/>{errorNames[r.error]||'请求失败'}</>}</>}><span tabIndex={0} aria-label={`${r.success?'成功':'失败'}，${r.duration}秒`} className={`result ${r.success?'success':'failure'}`}/></Tooltip>):<span className="muted">暂无请求记录</span>}</div></div>},
  {title:'操作',key:'action',fixed:'right' as const,width:140,render:(_:unknown,a:Account)=><Tooltip title={a.status==='busy'?'正在生成，任务结束后再检查':'向 Google 验证当前登录，不生成图片，也不更改启用状态'}><Button icon={<SafetyCertificateOutlined/>} loading={checking===a.id||a.checking} disabled={a.status==='busy'||(checking!==null&&checking!==a.id)} onClick={()=>void check(a)}>检查登录</Button></Tooltip>}
 ];
 return <ConfigProvider theme={{token:{colorPrimary:'#2563eb',borderRadius:8,fontFamily:'-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif'}}}>{contextHolder}{!token?<div className="login"><Card className="login-card"><ApiOutlined className="login-mark"/><Typography.Title level={3}>Gemini Web Bridge</Typography.Title><Typography.Paragraph type="secondary">账号状态控制台</Typography.Paragraph>{error&&<Alert type="error" title={error} showIcon/>}<Form form={form} layout="vertical" onFinish={signIn}><Form.Item name="key" label="管理员密钥" rules={[{required:true,message:'请输入管理员密钥'}]}><Input.Password autoComplete="off" placeholder="输入管理员 Key"/></Form.Item><Button block type="primary" htmlType="submit" loading={loading}>查看账号状态</Button></Form><p className="muted">使用独立管理员 Key，不是 CPA 调用 Key。<br/>密钥仅存于页面内存，刷新页面后需重新输入。</p></Card></div>:<Layout className="shell"><Layout.Sider width={212} className="sidebar" breakpoint="lg" collapsedWidth={0}><div className="brand"><ApiOutlined/><div>Gemini Bridge<small>账号状态控制台</small></div></div><Menu selectedKeys={['accounts']} items={[{key:'accounts',icon:<TeamOutlined/>,label:'账号状态'}]}/><div className="sidebar-footer">Gemini Web Bridge<br/><span>独立维护 · AGPL-3.0</span></div></Layout.Sider><Layout><Layout.Header className="header"><span>账号状态</span><Button icon={<LogoutOutlined/>} disabled={loading||checking!==null} onClick={()=>{setToken('');setSnapshot(empty);setError('');}}>退出</Button></Layout.Header><Layout.Content className="content"><div className="heading"><div><Typography.Title level={3}>账号概览</Typography.Title><Typography.Text type="secondary">查看运行状态，按需验证账号登录</Typography.Text></div><Tooltip title="刷新服务记录，不重新验证 Google 登录"><Button icon={<ReloadOutlined/>} loading={loading} onClick={()=>void manualRefresh()}>刷新列表</Button></Tooltip></div><div className="stats">{[{title:'账号总数',value:snapshot.data.length,icon:<TeamOutlined/>},{title:'空闲可用',value:snapshot.data.filter(a=>a.enabled&&a.status==='idle').length,icon:<CheckCircleOutlined/>},{title:'等待任务',value:snapshot.queue_depth,icon:<ClockCircleOutlined/>}].map(item=><Card key={item.title}><Statistic title={item.title} value={item.value} prefix={item.icon}/></Card>)}</div>{error&&<Alert className="error" type="error" title={error} showIcon/>}<Card className="table-card" title={<div>账号列表<div className="list-hint">登录检查仅验证会话，不生成图片</div></div>} extra={<span className="muted">更新于 {stamp(updated)}</span>}><Table rowKey="id" dataSource={snapshot.data} columns={columns} pagination={false} scroll={{x:950}} locale={{emptyText:<Empty description="暂无账号，请先在服务端配置账号"/>}}/><div className="note"><span className="dot success"/>成功 <span className="dot failure"/>失败 <span>最近 10 次请求 · 重启后清空</span></div></Card></Layout.Content></Layout></Layout>}</ConfigProvider>;
}
createRoot(document.getElementById('root')!).render(<App/>);

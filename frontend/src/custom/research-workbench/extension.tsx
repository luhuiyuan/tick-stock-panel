import { lazy } from 'react'
import { Navigate, useLocation } from 'react-router-dom'
import { Layers } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'
import { LEGACY_TABS } from './recipes'

const WorkbenchPage = lazy(() => import('./Workbench').then(m => ({ default: m.Workbench })))

/** 旧地址收藏夹不失效: 保留原有查询参数(公网 sharecode 等), 只追加 tab。 */
function LegacyRedirect({ tab }: { tab: string }) {
  const location = useLocation()
  const params = new URLSearchParams(location.search)
  params.set('tab', tab)
  return <Navigate to={`/research-workbench?${params.toString()}`} replace />
}

const extension: FrontendExtension = {
  id: 'official.research-workbench',
  apiVersion: 1,
  routes: [
    { id: 'research-workbench', path: '/research-workbench', component: WorkbenchPage },
    // 旧地址只保留跳转, 不再挂各自的页面组件
    ...Object.entries(LEGACY_TABS).map(([legacy, tab]) => ({
      id: `legacy-${legacy}`,
      path: `/${legacy}` as const,
      component: () => <LegacyRedirect tab={tab} />,
    })),
  ],
  navigation: [
    { id: 'research-workbench', routeId: 'research-workbench', label: '研究工作台', icon: Layers },
  ],
}

export default extension

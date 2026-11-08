import { lazy } from 'react'
import { BarChart3 } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'

export const StrategyReviewPage = lazy(() => import('./Page').then(m => ({ default: m.StrategyReviewPage })))

const extension: FrontendExtension = {
  id: 'official.strategy-review',
  apiVersion: 1,
  routes: [
    { id: 'strategy-review', path: '/strategy-review', component: StrategyReviewPage },
  ],
  navigation: [
    { id: 'strategy-review', routeId: 'strategy-review', label: '策略复盘', icon: BarChart3 },
  ],
}

export default extension

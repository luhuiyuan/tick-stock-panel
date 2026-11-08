import { lazy } from 'react'
import { Layers } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'

export const StrategyComboPage = lazy(() => import('./Page').then(m => ({ default: m.StrategyComboPage })))

const extension: FrontendExtension = {
  id: 'official.strategy-combo',
  apiVersion: 1,
  routes: [
    { id: 'strategy-combo', path: '/strategy-combo', component: StrategyComboPage },
  ],
  navigation: [
    { id: 'strategy-combo', routeId: 'strategy-combo', label: '组合探索', icon: Layers },
  ],
}

export default extension

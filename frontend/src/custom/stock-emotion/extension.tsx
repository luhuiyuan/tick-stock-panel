import { lazy } from 'react'
import { Flame } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'

export const StockEmotionPage = lazy(() => import('./Page').then(m => ({ default: m.StockEmotionPage })))

const extension: FrontendExtension = {
  id: 'official.stock-emotion',
  apiVersion: 1,
  routes: [
    { id: 'stock-emotion', path: '/stock-emotion', component: StockEmotionPage },
  ],
  navigation: [
    { id: 'stock-emotion', routeId: 'stock-emotion', label: '个股情绪', icon: Flame },
  ],
}

export default extension

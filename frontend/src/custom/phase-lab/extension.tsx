import { lazy } from 'react'
import { Activity } from 'lucide-react'
import type { FrontendExtension } from '@/extensions/types'

export const PhaseLabPage = lazy(() => import('./Page').then(m => ({ default: m.PhaseLabPage })))

const extension: FrontendExtension = {
  id: 'official.phase-lab',
  apiVersion: 1,
  routes: [
    { id: 'phase-lab', path: '/phase-lab', component: PhaseLabPage },
  ],
  navigation: [
    { id: 'phase-lab', routeId: 'phase-lab', label: '价格结构', icon: Activity },
  ],
}

export default extension
